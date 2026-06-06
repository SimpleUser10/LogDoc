"""LogDoc CLI entrypoint."""

from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path

import click
from rich.console import Console

from . import __version__
from .advisor import DEFAULT_MODEL, DEFAULT_OLLAMA
from .config import LogDocConfig, USER_CONFIG, write_user_config
from .docker_logs import DockerLogWatcher
from .runner import LogDocRunner, RunConfig
from .tail_file import FileTailWatcher


def _bootstrap_stdio() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _console() -> Console:
    _bootstrap_stdio()
    return Console(legacy_windows=False)


def _watch_options(f):
    opts = [
        click.option(
            "--ai/--no-ai",
            "ai",
            default=None,
            help="Use local Ollama (overrides config default_ai).",
        ),
        click.option("--ollama-url", default=None, help=f"Ollama base URL (default: {DEFAULT_OLLAMA})."),
        click.option("--model", default=None, help=f"Ollama model (default: {DEFAULT_MODEL})."),
        click.option("--context-before", type=int, default=None, help="Context lines before error."),
        click.option("--context-after", type=int, default=None, help="Context lines after error."),
        click.option("--debounce", type=float, default=None, help="Seconds to wait before diagnosing."),
        click.option("--no-passthrough/--passthrough", "passthrough", default=None, help="Echo log lines live."),
        click.option("--ci", is_flag=True, default=None, help="CI/CD mode: write report to GITHUB_STEP_SUMMARY on failure."),
    ]
    for opt in reversed(opts):
        f = opt(f)
    return f


def _resolve_settings(ctx: click.Context, passthrough: bool | None) -> None:
    cfg: LogDocConfig = ctx.ensure_object(dict)["config"]
    p = ctx.params
    ctx.obj["settings"] = cfg.merge_cli(
        ai=p.get("ai"),
        ollama_url=p.get("ollama_url"),
        model=p.get("model"),
        context_before=p.get("context_before"),
        context_after=p.get("context_after"),
        debounce=p.get("debounce"),
        passthrough=passthrough if passthrough is not None else p.get("passthrough"),
        ci=p.get("ci"),
    )


@click.group()
@click.version_option(__version__, prog_name="logdoc")
@click.pass_context
def main(ctx: click.Context) -> None:
    """LogDoc — intercept logs, detect errors, suggest fixes locally."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = LogDocConfig.load()


@main.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing ~/.logdoc.toml")
def init_cmd(force: bool) -> None:
    """Create ~/.logdoc.toml with defaults."""
    console = _console()
    if USER_CONFIG.exists() and not force:
        console.print(f"[yellow]Already exists:[/yellow] {USER_CONFIG} (use --force to overwrite)")
        return
    path = write_user_config(force=True)
    console.print(f"[green]Created[/green] {path}")


@main.command("config-path")
def config_path_cmd() -> None:
    """Print active config file locations."""
    console = _console()
    console.print(f"User:  {USER_CONFIG} ({'found' if USER_CONFIG.is_file() else 'missing'})")
    local = Path("logdoc.toml")
    console.print(f"Local: {local.resolve()} ({'found' if local.is_file() else 'missing'})")


@main.command("run")
@_watch_options
@click.option("-s", "--shell", "shell_cmd", default=None, help='Run via shell, e.g. --shell "npm run dev"')
@click.argument("cmd", nargs=-1, required=False)
@click.pass_context
def run_cmd(ctx: click.Context, cmd: tuple[str, ...], shell_cmd: str | None, **_: object) -> None:
    """Wrap a command and watch stdout/stderr for failures."""
    _resolve_settings(ctx, None)
    console = _console()

    if shell_cmd:
        argv = [shell_cmd] if sys.platform == "win32" else shlex.split(shell_cmd, posix=True)
        use_shell = True
    else:
        argv = list(cmd)
        use_shell = False

    if not argv:
        console.print("[red]Provide a command after 'run' or use --shell.[/red]")
        raise SystemExit(2)

    config = RunConfig(command=argv, settings=ctx.obj["settings"], shell=use_shell)
    code = asyncio.run(LogDocRunner(config, console=console).run())
    raise SystemExit(code)


@main.command("docker")
@_watch_options
@click.argument("container")
@click.option("--tail", default=100, show_default=True, help="Lines of history from docker logs.")
@click.pass_context
def docker_cmd(ctx: click.Context, container: str, tail: int, **_: object) -> None:
    """Follow logs from a Docker container (docker logs -f)."""
    _resolve_settings(ctx, True)
    console = _console()
    try:
        code = asyncio.run(
            DockerLogWatcher(container, ctx.obj["settings"], tail=tail, console=console).run()
        )
    except KeyboardInterrupt:
        code = 0
    raise SystemExit(code)


@main.command("tail")
@_watch_options
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--from-start", is_flag=True, help="Read from beginning instead of end of file.")
@click.pass_context
def tail_cmd(ctx: click.Context, file: Path, from_start: bool, **_: object) -> None:
    """Follow a log file and diagnose new errors as they appear."""
    _resolve_settings(ctx, True)
    console = _console()
    try:
        code = asyncio.run(
            FileTailWatcher(file, ctx.obj["settings"], from_start=from_start, console=console).run()
        )
    except KeyboardInterrupt:
        code = 0
    raise SystemExit(code)


@main.command("check-ollama")
@click.option("--ollama-url", default=None, help="Override config ollama URL.")
@click.pass_context
def check_ollama(ctx: click.Context, ollama_url: str | None) -> None:
    """Verify Ollama is reachable."""
    import httpx

    cfg: LogDocConfig = ctx.obj["config"]
    base = ollama_url or cfg.watch.ollama_url
    console = _console()
    try:
        r = httpx.get(base.rstrip("/") + "/api/tags", timeout=5.0)
        r.raise_for_status()
        models = [m.get("name") for m in r.json().get("models", [])]
        console.print(f"[green]Ollama OK[/green] ({base})")
        for name in models[:10]:
            console.print(f"  - {name}")
        if len(models) > 10:
            console.print(f"  ... and {len(models) - 10} more")
    except Exception as e:
        console.print(f"[red]Ollama unreachable:[/red] {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
