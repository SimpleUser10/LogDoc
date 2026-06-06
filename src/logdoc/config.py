"""Load ~/.logdoc.toml and merge with CLI overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

from .advisor import DEFAULT_MODEL, DEFAULT_OLLAMA
from .detector import CustomPattern, ErrorKind

USER_CONFIG = Path.home() / ".logdoc.toml"
LOCAL_CONFIG = Path("logdoc.toml")

_KIND_MAP = {k.value: k for k in ErrorKind}


@dataclass
class WatchSettings:
    use_ollama: bool = False
    ollama_url: str = DEFAULT_OLLAMA
    model: str = DEFAULT_MODEL
    context_before: int = 8
    context_after: int = 4
    debounce_seconds: float = 0.6
    buffer_maxlen: int = 200
    passthrough: bool = True
    custom_patterns: list[CustomPattern] = field(default_factory=list)
    cooldown_seconds: float = 30.0
    ai_provider: str = "ollama"
    ai_api_key: str = ""
    editor_cmd: str = ""
    auto_open: bool = False
    ci_mode: bool = False


@dataclass
class LogDocConfig:
    watch: WatchSettings = field(default_factory=WatchSettings)

    @classmethod
    def load(cls) -> LogDocConfig:
        data: dict = {}
        if USER_CONFIG.is_file():
            data = _deep_merge(data, _read_toml(USER_CONFIG))
        if LOCAL_CONFIG.is_file():
            data = _deep_merge(data, _read_toml(LOCAL_CONFIG))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> LogDocConfig:
        watch_raw = data.get("watch", {}) or {}
        ollama_raw = data.get("ollama", {}) or {}
        ai_raw = data.get("ai", {}) or {}
        patterns_raw = data.get("patterns", []) or []

        patterns: list[CustomPattern] = []
        for i, entry in enumerate(patterns_raw):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", f"custom-{i}"))
            regex = entry.get("regex")
            if not regex:
                continue
            kind_str = str(entry.get("kind", "unknown")).lower()
            kind = _KIND_MAP.get(kind_str, ErrorKind.UNKNOWN)
            try:
                patterns.append(
                    CustomPattern(
                        name=name,
                        kind=kind,
                        regex=re.compile(regex, re.I),
                    )
                )
            except re.error:
                continue

        ai_provider = str(ai_raw.get("provider", ollama_raw.get("provider", "ollama"))).lower()
        ai_api_key = str(ai_raw.get("api_key", ""))
        editor_raw = data.get("editor", {}) or {}
        editor_cmd = str(editor_raw.get("cmd", ""))
        auto_open = bool(editor_raw.get("auto_open", False))
        ci_mode = bool(watch_raw.get("ci", watch_raw.get("ci_mode", False)))

        watch = WatchSettings(
            use_ollama=bool(ollama_raw.get("default_ai", watch_raw.get("default_ai", ai_raw.get("default_ai", False)))),
            ollama_url=str(ai_raw.get("url", ollama_raw.get("url", DEFAULT_OLLAMA))),
            model=str(ai_raw.get("model", ollama_raw.get("model", DEFAULT_MODEL))),
            context_before=int(watch_raw.get("context_before", 8)),
            context_after=int(watch_raw.get("context_after", 4)),
            debounce_seconds=float(watch_raw.get("debounce_seconds", 0.6)),
            buffer_maxlen=int(watch_raw.get("buffer_maxlen", 200)),
            passthrough=bool(watch_raw.get("passthrough", True)),
            custom_patterns=patterns,
            cooldown_seconds=float(watch_raw.get("cooldown_seconds", watch_raw.get("cooldown", 30.0))),
            ai_provider=ai_provider,
            ai_api_key=ai_api_key,
            editor_cmd=editor_cmd,
            auto_open=auto_open,
            ci_mode=ci_mode,
        )
        return cls(watch=watch)

    def merge_cli(
        self,
        *,
        ai: bool | None = None,
        ollama_url: str | None = None,
        model: str | None = None,
        context_before: int | None = None,
        context_after: int | None = None,
        debounce: float | None = None,
        passthrough: bool | None = None,
        cooldown: float | None = None,
        ai_provider: str | None = None,
        ai_api_key: str | None = None,
        editor_cmd: str | None = None,
        auto_open: bool | None = None,
        ci: bool | None = None,
    ) -> WatchSettings:
        w = self.watch
        return WatchSettings(
            use_ollama=ai if ai is not None else w.use_ollama,
            ollama_url=ollama_url or w.ollama_url,
            model=model or w.model,
            context_before=context_before if context_before is not None else w.context_before,
            context_after=context_after if context_after is not None else w.context_after,
            debounce_seconds=debounce if debounce is not None else w.debounce_seconds,
            buffer_maxlen=w.buffer_maxlen,
            passthrough=passthrough if passthrough is not None else w.passthrough,
            custom_patterns=w.custom_patterns,
            cooldown_seconds=cooldown if cooldown is not None else w.cooldown_seconds,
            ai_provider=ai_provider or w.ai_provider,
            ai_api_key=ai_api_key or w.ai_api_key,
            editor_cmd=editor_cmd or w.editor_cmd,
            auto_open=auto_open if auto_open is not None else w.auto_open,
            ci_mode=ci if ci is not None else w.ci_mode,
        )


def _read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, val in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def default_config_toml() -> str:
    return """# LogDoc configuration
# Copy to ~/.logdoc.toml or ./logdoc.toml

[ai]
provider = "ollama"  # ollama | openai | anthropic | gemini
url = "http://127.0.0.1:11434"
model = "llama3.2"
api_key = ""  # required for openai/anthropic/gemini if not set in environment

[watch]
context_before = 8
context_after = 4
debounce_seconds = 0.6
buffer_maxlen = 200
passthrough = true
cooldown_seconds = 30.0

[editor]
cmd = "code -g"  # command to open your editor at file:line, e.g. "code -g" or "cursor -g"
auto_open = false  # automatically open the editor on error detection

# Custom error patterns (checked before built-in rules)
[[patterns]]
name = "my-service-down"
kind = "network"
regex = "MyServiceUnavailable|SERVICE_UNAVAILABLE"

# [[patterns]]
# name = "internal-api"
# kind = "runtime"
# regex = "InternalApiError\\\\d+"
"""


def write_user_config(force: bool = False) -> Path:
    if USER_CONFIG.exists() and not force:
        return USER_CONFIG
    USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG.write_text(default_config_toml(), encoding="utf-8")
    return USER_CONFIG
