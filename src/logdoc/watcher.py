"""Shared log stream watcher with debounced incident reporting."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from rich import box
from rich.console import Console
from rich.panel import Panel

from .advisor import AdviceResult, advise
from .config import WatchSettings
from .context import LineBuffer, build_context
from .detector import DetectedError, ErrorDetector, is_stack_continuation, extract_file_line


@dataclass
class _Incident:
    error: DetectedError
    extra_after: list[str]


class LogStreamWatcher:
    """Process log lines, debounce stack traces, print diagnoses."""

    def __init__(
        self,
        settings: WatchSettings,
        *,
        console: Console | None = None,
        source_label: str = "stream",
    ) -> None:
        self.settings = settings
        self.console = console or Console(stderr=True)
        self.source_label = source_label
        self.buffer = LineBuffer(maxlen=settings.buffer_maxlen)
        self.detector = ErrorDetector(settings.custom_patterns)
        self._incident: _Incident | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._shown_line_nos: set[int] = set()
        self._shown_errors: dict[str, float] = {}

    async def process_line(
        self,
        line: str,
        *,
        stream: str = "stdout",
        passthrough: bool | None = None,
    ) -> None:
        do_pass = self.settings.passthrough if passthrough is None else passthrough
        line_no = self.buffer.push(line)

        if do_pass:
            target = sys.stdout if stream == "stdout" else sys.stderr
            target.write(line if line.endswith("\n") else line + "\n")
            target.flush()

        text = line.rstrip("\n")

        if self._incident and is_stack_continuation(line):
            self._incident.extra_after.append(text)
            self._schedule_flush()
            return

        err = self.detector.detect(line, line_no)
        if not err:
            return

        err_hash = f"{err.kind.value}:{err.pattern}:{err.line.strip()}"
        last_time = self._shown_errors.get(err_hash, 0.0)
        current_time = asyncio.get_event_loop().time()
        if current_time - last_time < self.settings.cooldown_seconds:
            return

        if err.line_no in self._shown_line_nos:
            return

        if self._incident:
            await self._flush_incident()

        self._incident = _Incident(error=err, extra_after=[])
        self._schedule_flush()

    async def close(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush_incident()

    def _schedule_flush(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = asyncio.create_task(self._debounced_flush())

    async def _debounced_flush(self) -> None:
        try:
            await asyncio.sleep(self.settings.debounce_seconds)
        except asyncio.CancelledError:
            return
        asyncio.create_task(self._flush_incident())

    async def _flush_incident(self) -> None:
        if not self._incident:
            return
        inc = self._incident
        self._incident = None
        self._shown_line_nos.add(inc.error.line_no)

        err_hash = f"{inc.error.kind.value}:{inc.error.pattern}:{inc.error.line.strip()}"
        self._shown_errors[err_hash] = asyncio.get_event_loop().time()

        last_line_no = inc.error.line_no + len(inc.extra_after)
        _, snapshot_after = self.buffer.snapshot_around(
            last_line_no,
            before=0,
            after=self.settings.context_after,
        )
        after = (inc.extra_after + snapshot_after)[: self.settings.context_after + 32]
        ctx = build_context(inc.error, self.buffer, after)

        all_lines = ctx.before + [inc.error.line] + ctx.after
        file_line = extract_file_line(all_lines)

        if file_line and (self.settings.auto_open or self.settings.editor_cmd):
            filepath, line_no = file_line
            if self.settings.editor_cmd:
                try:
                    import subprocess
                    import shlex
                    import shutil
                    cmd_parts = shlex.split(self.settings.editor_cmd)
                    resolved = shutil.which(cmd_parts[0])
                    if resolved:
                        cmd_parts[0] = resolved
                    cmd_parts.append(f"{filepath}:{line_no}")
                    subprocess.Popen(cmd_parts, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

        result = await asyncio.to_thread(
            advise,
            ctx,
            use_ollama=self.settings.use_ollama,
            ollama_url=self.settings.ollama_url,
            model=self.settings.model,
            provider=self.settings.ai_provider,
            api_key=self.settings.ai_api_key,
        )
        self._print_diagnosis(result, ctx.to_prompt_block(), file_line)
        if self.settings.ci_mode:
            self._write_ci_report(result, ctx.to_prompt_block())

    def _print_diagnosis(self, result: AdviceResult, excerpt: str, file_line: tuple[str, int] | None = None) -> None:
        self.console.print()
        self.console.rule("[bold red]LogDoc - error detected[/bold red]")
        self.console.print(f"[dim]source: {self.source_label}[/dim]")
        source = "local rules" if result.from_local_rules else f"ollama:{result.model}"
        if not result.from_local_rules and result.model != "rules" and self.settings.ai_provider != "ollama":
            source = f"{self.settings.ai_provider}:{result.model}"
        self.console.print(f"[dim]advisor: {source}[/dim]")
        if file_line:
            path, line_no = file_line
            # Normalize path for links
            link_path = path.replace("\\", "/")
            self.console.print(f"[dim]location: [link=file:///{link_path}]{path}:{line_no}[/link][/dim]")
        self.console.print()
        self.console.print(result.text)
        self.console.print()
        panel_box = box.ASCII if sys.platform == "win32" else box.ROUNDED
        self.console.print(Panel(excerpt, title="context", border_style="dim", box=panel_box))
        self.console.rule()
        self.console.print()

    def _write_ci_report(self, result: AdviceResult, excerpt: str) -> None:
        import os
        source = "local rules" if result.from_local_rules else f"ollama:{result.model}"
        if not result.from_local_rules and result.model != "rules" and self.settings.ai_provider != "ollama":
            source = f"{self.settings.ai_provider}:{result.model}"

        md_lines = [
            f"### ❌ LogDoc: Error Detected",
            f"- **Source**: `{self.source_label}`",
            f"- **Advisor**: `{source}`\n",
            f"#### AI Advice:\n",
            result.text,
            f"\n#### Log Excerpt:\n",
            f"```text",
            excerpt,
            f"```\n",
            "---\n"
        ]
        report_content = "\n".join(md_lines)

        github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if github_summary:
            try:
                with open(github_summary, "a", encoding="utf-8") as f:
                    f.write(report_content)
                return
            except Exception:
                pass

        try:
            with open("logdoc-report.md", "a", encoding="utf-8") as f:
                f.write(report_content)
        except Exception:
            pass
