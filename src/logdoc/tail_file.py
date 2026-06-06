"""Follow a log file (like tail -f)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console

from .config import WatchSettings
from .watcher import LogStreamWatcher


class FileTailWatcher:
    def __init__(
        self,
        path: Path,
        settings: WatchSettings,
        *,
        from_start: bool = False,
        poll_interval: float = 0.15,
        console: Console | None = None,
    ) -> None:
        self.path = path.resolve()
        self.settings = settings
        self.from_start = from_start
        self.poll_interval = poll_interval
        self.console = console or Console(stderr=True)
        self.watcher = LogStreamWatcher(
            settings,
            console=self.console,
            source_label=f"tail: {self.path.name}",
        )

    async def run(self) -> int:
        if not self.path.is_file():
            self.console.print(f"[red]File not found:[/red] {self.path}")
            return 2

        self.console.print(f"[dim]logdoc >>[/dim] tail -f {self.path}\n")

        try:
            fh = self.path.open("r", encoding="utf-8", errors="replace")
            if self.from_start:
                fh.seek(0)
            else:
                fh.seek(0, 2)

            last_inode = self.path.stat().st_ino if self.path.is_file() else None
            last_size = self.path.stat().st_size if self.path.is_file() else 0

            try:
                while True:
                    if not self.path.is_file():
                        await asyncio.sleep(self.poll_interval)
                        continue

                    try:
                        stat = self.path.stat()
                        inode = stat.st_ino
                        size = stat.st_size
                    except OSError:
                        await asyncio.sleep(self.poll_interval)
                        continue

                    if inode != last_inode or size < last_size:
                        self.console.print("[yellow]Log file rotated or truncated; reopening.[/yellow]")
                        fh.close()
                        fh = self.path.open("r", encoding="utf-8", errors="replace")
                        last_inode = inode
                        last_size = size
                        fh.seek(0)

                    line = fh.readline()
                    if line:
                        last_size = fh.tell()
                        await self.watcher.process_line(line, stream="stdout")
                    else:
                        await asyncio.sleep(self.poll_interval)
            finally:
                fh.close()
        except asyncio.CancelledError:
            pass
        finally:
            await self.watcher.close()
        return 0
