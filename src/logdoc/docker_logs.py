"""Follow Docker container logs."""

from __future__ import annotations

import asyncio
import shutil

from rich.console import Console

from .config import WatchSettings
from .watcher import LogStreamWatcher


class DockerLogWatcher:
    def __init__(
        self,
        container: str,
        settings: WatchSettings,
        *,
        tail: int = 100,
        console: Console | None = None,
    ) -> None:
        self.container = container
        self.settings = settings
        self.tail = tail
        self.console = console or Console(stderr=True)
        self.watcher = LogStreamWatcher(
            settings,
            console=self.console,
            source_label=f"docker: {container}",
        )

    async def run(self) -> int:
        docker = shutil.which("docker")
        if not docker:
            self.console.print("[red]docker not found in PATH[/red]")
            return 127

        self.console.print(
            f"[dim]logdoc >>[/dim] docker logs -f --tail {self.tail} {self.container}\n"
        )

        proc = await asyncio.create_subprocess_exec(
            docker,
            "logs",
            "-f",
            "--tail",
            str(self.tail),
            self.container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace")
                await self.watcher.process_line(line, stream="stdout")
            return await proc.wait()
        finally:
            await self.watcher.close()
