"""Collect surrounding log context and safe environment hints."""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass

from .detector import DetectedError

# Env keys often relevant to local dev failures (never log secrets wholesale)
_ENV_HINT_KEYS = (
    "NODE_ENV",
    "PORT",
    "HOST",
    "DATABASE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "REDIS_URL",
    "DOCKER_HOST",
    "COMPOSE_PROJECT_NAME",
    "PYTHONPATH",
    "PATH",
    "CI",
)


@dataclass
class ErrorContext:
    error: DetectedError
    before: list[str]
    after: list[str]
    env_hints: dict[str, str]

    def to_prompt_block(self) -> str:
        parts = [
            f"Error type: {self.error.kind.value} (matched: {self.error.pattern})",
            f"Line {self.error.line_no}: {self.error.line}",
            "",
            "Context (lines before):",
            *self.before,
            ">>> ERROR <<<",
            self.error.line,
            "Context (lines after):",
            *self.after,
        ]
        if self.env_hints:
            parts.extend(["", "Environment hints:"])
            for k, v in self.env_hints.items():
                parts.append(f"  {k}={v}")
        return "\n".join(parts)


class LineBuffer:
    """Rolling window of recent stdout/stderr lines."""

    def __init__(self, maxlen: int = 40) -> None:
        self._lines: deque[tuple[int, str]] = deque(maxlen=maxlen)
        self._counter = 0

    def push(self, line: str) -> int:
        self._counter += 1
        self._lines.append((self._counter, line.rstrip("\n")))
        return self._counter

    def snapshot_around(self, line_no: int, before: int = 8, after: int = 4) -> tuple[list[str], list[str]]:
        items = list(self._lines)
        idx = next((i for i, (n, _) in enumerate(items) if n == line_no), None)
        if idx is None:
            return [], []
        b = [text for _, text in items[max(0, idx - before) : idx]]
        a = [text for _, text in items[idx + 1 : idx + 1 + after]]
        return b, a


def collect_env_hints() -> dict[str, str]:
    out: dict[str, str] = {}
    for key in _ENV_HINT_KEYS:
        val = os.environ.get(key)
        if not val:
            continue
        if key.endswith("_URL") or "PASSWORD" in key or "SECRET" in key or "TOKEN" in key:
            out[key] = _redact_url(val)
        else:
            out[key] = val if len(val) <= 120 else val[:117] + "..."
    return out


def _redact_url(url: str) -> str:
    if "@" in url:
        scheme, rest = url.split("://", 1) if "://" in url else ("", url)
        if "@" in rest:
            creds, hostpart = rest.rsplit("@", 1)
            return f"{scheme}://***:***@{hostpart}" if scheme else f"***:***@{hostpart}"
    return url[:40] + "..." if len(url) > 40 else url


def build_context(error: DetectedError, buffer: LineBuffer, after_lines: list[str]) -> ErrorContext:
    before, _ = buffer.snapshot_around(error.line_no)
    return ErrorContext(
        error=error,
        before=before,
        after=after_lines,
        env_hints=collect_env_hints(),
    )
