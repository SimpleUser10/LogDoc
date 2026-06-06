"""Run wrapped commands and stream stdout/stderr."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass

from rich.console import Console

from .config import WatchSettings
from .watcher import LogStreamWatcher


@dataclass
class RunConfig:
    command: Sequence[str]
    settings: WatchSettings
    shell: bool = False


class LogDocRunner:
    def __init__(self, config: RunConfig, console: Console | None = None) -> None:
        self.config = config
        self.console = console or Console(stderr=True)
        cmd = " ".join(config.command)
        self.watcher = LogStreamWatcher(
            config.settings,
            console=self.console,
            source_label=f"run: {cmd}",
        )

    async def run(self) -> int:
        import shutil
        cmd = list(self.config.command)
        if not cmd:
            self.console.print("[red]Empty command[/red]")
            return 2

        self.console.print(f"[dim]logdoc >>[/dim] {' '.join(cmd)}\n")

        if not self.config.shell:
            resolved = shutil.which(cmd[0])
            if resolved:
                cmd[0] = resolved

        if self.config.shell:
            proc = await asyncio.create_subprocess_shell(
                cmd[0] if len(cmd) == 1 else " ".join(cmd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )

        assert proc.stdout and proc.stderr
        try:
            await asyncio.gather(
                self._read_stream(proc.stdout, "stdout"),
                self._read_stream(proc.stderr, "stderr"),
            )
            return await proc.wait()
        finally:
            await self.watcher.close()

    async def _read_stream(self, stream: asyncio.StreamReader, label: str) -> None:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            try:
                line = raw.decode(errors="replace")
            except Exception:
                line = str(raw)
            await self.watcher.process_line(line, stream=label)
