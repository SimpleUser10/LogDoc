import asyncio

from logdoc.config import WatchSettings
from logdoc.watcher import LogStreamWatcher


async def test_debounce_groups_stack_lines():
    settings = WatchSettings(debounce_seconds=0.15, passthrough=False, context_after=4)
    watcher = LogStreamWatcher(settings, source_label="test")

    await watcher.process_line("Traceback (most recent call last):\n", stream="stderr")
    await watcher.process_line('  File "x.py", line 1\n', stream="stderr")
    await watcher.process_line("ValueError: bad\n", stream="stderr")
    await asyncio.sleep(0.35)
    await watcher.close()


async def test_cooldown_deduplication():
    settings = WatchSettings(debounce_seconds=0.05, cooldown_seconds=2.0, passthrough=False)
    watcher = LogStreamWatcher(settings, source_label="test")

    # Traceback A
    await watcher.process_line("Traceback (most recent call last):\n", stream="stderr")
    assert watcher._incident is not None

    # Wait for flush
    await asyncio.sleep(0.15)
    assert watcher._incident is None

    # Traceback A again (should be ignored due to cooldown)
    await watcher.process_line("Traceback (most recent call last):\n", stream="stderr")
    assert watcher._incident is None

    await watcher.close()
