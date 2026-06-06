"""Heuristic and configurable error detection in streamed log lines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ErrorKind(str, Enum):
    SYNTAX = "syntax"
    RUNTIME = "runtime"
    DATABASE = "database"
    NETWORK = "network"
    BUILD = "build"
    DOCKER = "docker"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DetectedError:
    kind: ErrorKind
    line: str
    line_no: int
    pattern: str


@dataclass(frozen=True)
class CustomPattern:
    name: str
    kind: ErrorKind
    regex: re.Pattern[str]


_BUILTIN: list[tuple[re.Pattern[str], ErrorKind, str]] = [
    (re.compile(r"SyntaxError|ParseError|Unexpected token", re.I), ErrorKind.SYNTAX, "syntax"),
    (re.compile(r"ReferenceError|TypeError|Cannot find module|Module not found", re.I), ErrorKind.RUNTIME, "runtime"),
    (re.compile(r"ECONNREFUSED|ETIMEDOUT|connection refused|getaddrinfo", re.I), ErrorKind.NETWORK, "network"),
    (
        re.compile(
            r"postgres|mysql|sqlite|sequelize|prisma|mongodb|"
            r"relation does not exist|duplicate key|authentication failed",
            re.I,
        ),
        ErrorKind.DATABASE,
        "database",
    ),
    (re.compile(r"npm ERR!|error TS\d+|Failed to compile|BUILD FAILED|exit code [1-9]", re.I), ErrorKind.BUILD, "build"),
    (re.compile(r"Error response from daemon|no such container|Cannot connect to the Docker", re.I), ErrorKind.DOCKER, "docker"),
    (re.compile(r"EACCES|permission denied|Operation not permitted", re.I), ErrorKind.PERMISSION, "permission"),
    (re.compile(r"\bError:\b|\bERROR\b|\bFATAL\b|Traceback \(most recent", re.I), ErrorKind.UNKNOWN, "generic"),
]

_STACK_CONTINUATION = re.compile(
    r"^("
    r"\s+at\s+"
    r"|\s+File \""
    r"|\s+\^"
    r"|Caused by:"
    r"|During handling"
    r"|The above exception"
    r"|npm ERR! A complete log"
    r"|---\s*$"
    r"|\s+\.\.\."
    r"|\s+from\s+"
    r"|\s+in\s+"
    r")",
    re.I,
)


def is_stack_continuation(line: str) -> bool:
    """True when line is likely part of an ongoing stack trace."""
    if not line.strip():
        return True
    return bool(_STACK_CONTINUATION.match(line))


class ErrorDetector:
    def __init__(self, custom: list[CustomPattern] | None = None) -> None:
        self._custom = custom or []

    def detect(self, line: str, line_no: int) -> DetectedError | None:
        stripped = line.strip()
        if not stripped:
            return None
        for pat in self._custom:
            if pat.regex.search(line):
                return DetectedError(
                    kind=pat.kind,
                    line=line.rstrip("\n"),
                    line_no=line_no,
                    pattern=pat.name,
                )
        for pattern, kind, name in _BUILTIN:
            if pattern.search(line):
                return DetectedError(
                    kind=kind,
                    line=line.rstrip("\n"),
                    line_no=line_no,
                    pattern=name,
                )
        return None


_default = ErrorDetector()


def detect(line: str, line_no: int, detector: ErrorDetector | None = None) -> DetectedError | None:
    d = detector or _default
    return d.detect(line, line_no)


_PYTHON_FILE_LINE = re.compile(r'File "([^"]+)", line (\d+)', re.I)
_JS_FILE_LINE = re.compile(r'(?:at|[^(\s]+)\s*\(?([^:\s]+):(\d+):(\d+)\)?', re.I)
_GENERIC_FILE_LINE = re.compile(r'([^:\s"\']+):(\d+)(?::\d+)?\b')


def extract_file_line(lines: list[str]) -> tuple[str, int] | None:
    for line in reversed(lines):
        m = _PYTHON_FILE_LINE.search(line)
        if m:
            path, line_no = m.group(1), int(m.group(2))
            if _is_local_file(path):
                return path, line_no

        m = _JS_FILE_LINE.search(line)
        if m:
            path, line_no = m.group(1), int(m.group(2))
            if _is_local_file(path):
                return path, line_no

        m = _GENERIC_FILE_LINE.search(line)
        if m:
            path, line_no = m.group(1), int(m.group(2))
            if _is_local_file(path):
                return path, line_no
    return None


def _is_local_file(path: str) -> bool:
    if not path or "<" in path or ">" in path:
        return False
    low = path.lower()
    if "node_modules" in low or "site-packages" in low or "venv" in low or ".venv" in low:
        return False
    return "/" in path or "\\" in path or Path(path).exists()
