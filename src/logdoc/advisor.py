"""Local fix suggestions via Ollama (optional)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx

from .context import ErrorContext

DEFAULT_MODEL = "llama3.2"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"

_SYSTEM = """You are a senior developer helping debug terminal/build logs.
Given an error and its context, respond in plain language for the terminal.
Be concise: 3-6 bullet points max. Include the most likely root cause first,
then concrete fix steps (commands or file edits). Do not invent file paths
unless they appear in the log. If unsure, say what to check next."""


@dataclass
class AdviceResult:
    text: str
    model: str
    from_local_rules: bool = False


def quick_rules(ctx: ErrorContext) -> str | None:
    """Zero-cost hints when Ollama is off or unreachable."""
    kind = ctx.error.kind.value
    hints: dict[str, list[str]] = {
        "database": [
            "Check DB is running: `docker compose up -d` or local Postgres service.",
            "Verify DATABASE_URL / host / port match your compose file.",
            "Run migrations if you see 'relation does not exist'.",
        ],
        "network": [
            "Confirm the target host/port is listening (`netstat` / `curl`).",
            "If using Docker, try `host.docker.internal` instead of `localhost` from inside containers.",
        ],
        "syntax": [
            "Open the file and line from the stack trace; fix the reported token.",
            "Run the same file through your formatter/linter locally.",
        ],
        "build": [
            "Scroll up for the first error — later messages are often cascading.",
            "Clear cache: `rm -rf node_modules/.cache` or rebuild from clean.",
        ],
        "docker": [
            "Run `docker ps` and ensure the container name matches compose.",
            "Try `docker compose logs <service>` for the failing service.",
        ],
        "permission": [
            "Avoid sudo for npm; fix directory ownership or run from a writable path.",
        ],
    }
    lines = hints.get(kind)
    if not lines:
        return None
    return "\n".join(f"- {h}" for h in lines)


def ask_ollama(
    ctx: ErrorContext,
    *,
    base_url: str = DEFAULT_OLLAMA,
    model: str = DEFAULT_MODEL,
    timeout: float = 60.0,
) -> AdviceResult:
    prompt = (
        f"{_SYSTEM}\n\n--- LOG EXCERPT ---\n{ctx.to_prompt_block()}\n\n"
        "Suggest a fix for this failure."
    )
    url = base_url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 512},
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = (data.get("response") or "").strip()
    if not text:
        raise ValueError("Empty response from Ollama")
    return AdviceResult(text=text, model=model)


def ask_openai(
    ctx: ErrorContext,
    *,
    model: str = "gpt-4o-mini",
    api_key: str = "",
    timeout: float = 30.0,
) -> AdviceResult:
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("Missing OpenAI API Key. Set OPENAI_API_KEY environment variable or in config.")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    prompt = (
        f"--- LOG EXCERPT ---\n{ctx.to_prompt_block()}\n\n"
        "Suggest a fix for this failure."
    )
    payload = {
        "model": model or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    return AdviceResult(text=text, model=model)


def ask_anthropic(
    ctx: ErrorContext,
    *,
    model: str = "claude-3-5-sonnet-20241022",
    api_key: str = "",
    timeout: float = 30.0,
) -> AdviceResult:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("Missing Anthropic API Key. Set ANTHROPIC_API_KEY environment variable or in config.")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    prompt = (
        f"--- LOG EXCERPT ---\n{ctx.to_prompt_block()}\n\n"
        "Suggest a fix for this failure."
    )
    payload = {
        "model": model or "claude-3-5-sonnet-20241022",
        "system": _SYSTEM,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 512,
        "temperature": 0.2,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = data["content"][0]["text"].strip()
    return AdviceResult(text=text, model=model)


def ask_gemini(
    ctx: ErrorContext,
    *,
    model: str = "gemini-1.5-flash",
    api_key: str = "",
    timeout: float = 30.0,
) -> AdviceResult:
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError("Missing Gemini API Key. Set GEMINI_API_KEY environment variable or in config.")

    gemini_model = model or "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={key}"
    headers = {
        "Content-Type": "application/json",
    }
    prompt = (
        f"{_SYSTEM}\n\n"
        f"--- LOG EXCERPT ---\n{ctx.to_prompt_block()}\n\n"
        "Suggest a fix for this failure."
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 512
        }
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    return AdviceResult(text=text, model=model)


def advise(
    ctx: ErrorContext,
    *,
    use_ollama: bool,
    ollama_url: str,
    model: str,
    provider: str = "ollama",
    api_key: str = "",
) -> AdviceResult:
    if not use_ollama:
        rules = quick_rules(ctx)
        body = rules or (
            "- Read the stack trace from bottom to top.\n"
            "- Re-run with verbose logging if the cause is unclear.\n"
            "- Enable Ollama/AI (`logdoc run --ai`) for deeper local suggestions."
        )
        return AdviceResult(text=body, model="rules", from_local_rules=True)

    try:
        prov = provider.lower()
        if prov == "ollama":
            return ask_ollama(ctx, base_url=ollama_url, model=model)
        elif prov == "openai":
            gpt_model = model if model != DEFAULT_MODEL else "gpt-4o-mini"
            return ask_openai(ctx, model=gpt_model, api_key=api_key)
        elif prov == "anthropic":
            claude_model = model if model != DEFAULT_MODEL else "claude-3-5-sonnet-20241022"
            return ask_anthropic(ctx, model=claude_model, api_key=api_key)
        elif prov == "gemini":
            gem_model = model if model != DEFAULT_MODEL else "gemini-1.5-flash"
            return ask_gemini(ctx, model=gem_model, api_key=api_key)
        else:
            raise ValueError(f"Unknown AI provider: {provider}")
    except Exception as e:
        rules = quick_rules(ctx)
        fallback = rules or f"- Could not reach AI provider ({provider}): {e}"
        if provider == "ollama":
            fallback += "\n- Start Ollama with: `ollama serve`"
            fallback += "\n- Install model: `ollama pull llama3.2`"
        return AdviceResult(text=fallback, model="rules+fallback", from_local_rules=True)
