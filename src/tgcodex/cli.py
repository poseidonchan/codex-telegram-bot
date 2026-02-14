from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional


def _require(mod: str) -> None:
    try:
        __import__(mod)
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            f"Missing dependency '{mod}'. Install project deps first (see README.md)."
        ) from exc


_require("typer")

import typer  # noqa: E402

from tgcodex.config import load_config, validate_config  # noqa: E402
from tgcodex import daemon  # noqa: E402
from tgcodex.constants import DEFAULT_DB_PATH  # noqa: E402

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("validate-config")
def validate_config_cmd(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
    check_binaries: bool = typer.Option(
        True, "--check-binaries/--no-check-binaries"
    ),
) -> None:
    cfg = load_config(config)
    errors = validate_config(cfg, validate_binaries=check_binaries)
    if errors:
        for e in errors:
            typer.echo(f"ERROR: {e}")
        raise typer.Exit(2)
    typer.echo("OK")


@app.command("run")
def run_cmd(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
) -> None:
    cfg = load_config(config)
    errors = validate_config(cfg, validate_binaries=False)
    if errors:
        for e in errors:
            typer.echo(f"ERROR: {e}")
        raise typer.Exit(2)

    _require("telegram")
    _require("telegram.ext")

    from tgcodex.bot.app import run_bot

    run_bot(cfg)


def _parse_int_list(raw: str) -> list[int]:
    parts = [p.strip() for p in (raw or "").replace("\n", ",").split(",")]
    out: list[int] = []
    for p in parts:
        if not p:
            continue
        try:
            out.append(int(p))
        except Exception:
            raise typer.BadParameter(f"Expected integer user id, got: {p!r}")
    return out


def _parse_str_list(raw: str) -> list[str]:
    parts = [p.strip() for p in (raw or "").replace("\n", ",").split(",")]
    return [p for p in parts if p]


def _is_absolute_or_tilde(path: str) -> bool:
    return bool(path) and (Path(path).is_absolute() or path.startswith("~"))


def _validate_allowed_roots(roots: list[str], *, where: str) -> list[str]:
    if not roots:
        raise typer.BadParameter(f"{where} must be non-empty")
    for r in roots:
        if not _is_absolute_or_tilde(r):
            raise typer.BadParameter(f"{where} entries must be absolute (or ~): {r!r}")
    return roots


def _is_env_var_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))


def _tail_text(path: Path, *, max_lines: int = 40) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def _yaml_dump_fallback(obj: Any) -> str:
    """
    Minimal YAML writer for our config (dict/list/scalars), used when PyYAML
    isn't available at runtime.

    We always quote strings using JSON quoting, which is valid YAML.
    """

    def scalar(v: Any) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
        if isinstance(v, str):
            return json.dumps(v)
        raise TypeError(f"Unsupported scalar type: {type(v).__name__}")

    def dump(v: Any, indent: int) -> list[str]:
        pad = " " * indent
        if isinstance(v, dict):
            lines: list[str] = []
            for k, vv in v.items():
                if not isinstance(k, str):
                    raise TypeError("YAML keys must be strings")
                if isinstance(vv, (dict, list)):
                    lines.append(f"{pad}{k}:")
                    lines.extend(dump(vv, indent + 2))
                else:
                    lines.append(f"{pad}{k}: {scalar(vv)}")
            return lines
        if isinstance(v, list):
            lines = []
            for item in v:
                if isinstance(item, (dict, list)):
                    lines.append(f"{pad}-")
                    lines.extend(dump(item, indent + 2))
                else:
                    lines.append(f"{pad}- {scalar(item)}")
            return lines
        return [f"{pad}{scalar(v)}"]

    return "\n".join(dump(obj, 0)) + "\n"


@app.command("setup")
def setup_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c", dir_okay=False),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """
    Interactive setup wizard that writes a starter config.yaml.
    """

    if config.exists() and not force:
        typer.echo(f"ERROR: Refusing to overwrite existing file: {config}")
        raise typer.Exit(1)

    token_env = typer.prompt(
        "Step 1/6: Telegram bot token env var name (not the token value)",
        default="TELEGRAM_BOT_TOKEN",
    ).strip()
    if not token_env:
        typer.echo("ERROR: token_env cannot be empty")
        raise typer.Exit(2)
    if not _is_env_var_name(token_env):
        typer.echo("ERROR: token_env must be a valid env var name")
        raise typer.Exit(2)

    allowed_raw = typer.prompt(
        "Step 2/6: Allowed Telegram user IDs (comma separated)",
    )
    allowed_user_ids = _parse_int_list(allowed_raw)
    if not allowed_user_ids:
        typer.echo("ERROR: allowed_user_ids must be non-empty")
        raise typer.Exit(2)

    db_path = typer.prompt(
        "Step 3/6: SQLite state DB path",
        default=DEFAULT_DB_PATH,
    ).strip()
    if not db_path:
        typer.echo("ERROR: db_path cannot be empty")
        raise typer.Exit(2)

    codex_bin = typer.prompt(
        "Step 4/6: Codex binary",
        default="codex",
    ).strip()
    if not codex_bin:
        typer.echo("ERROR: codex.bin cannot be empty")
        raise typer.Exit(2)

    default_workdir = typer.prompt(
        "Step 5/6: Local default workdir",
        default=str(Path.cwd()),
    ).strip()
    if not default_workdir:
        typer.echo("ERROR: default_workdir cannot be empty")
        raise typer.Exit(2)
    if not _is_absolute_or_tilde(default_workdir):
        typer.echo("ERROR: default_workdir must be absolute (or start with ~)")
        raise typer.Exit(2)

    allowed_roots_default = f"{default_workdir},/tmp"
    allowed_roots_raw = typer.prompt(
        "Step 6/6: Local allowed_roots (comma separated)",
        default=allowed_roots_default,
    )
    try:
        allowed_roots = _validate_allowed_roots(
            _parse_str_list(allowed_roots_raw),
            where="allowed_roots",
        )
    except typer.BadParameter as exc:
        typer.echo(f"ERROR: {exc}")
        raise typer.Exit(2)

    cfg: dict[str, Any] = {
        "telegram": {
            "token_env": token_env,
            "allowed_user_ids": allowed_user_ids,
        },
        "state": {"db_path": db_path},
        "codex": {
            "bin": codex_bin,
            "args": [],
            "model": None,
            "sandbox": "workspace-write",
            "approval_policy": "on-request",
            "skip_git_repo_check": True,
        },
        "output": {
            "flush_interval_ms": 250,
            "min_flush_chars": 120,
            "max_flush_delay_seconds": 2.0,
            "max_chars": 3500,
            "truncate": True,
            "typing_interval_seconds": 4.0,
            "show_codex_logs": False,
            "show_tool_output": False,
            "max_tool_output_chars": 1200,
        },
        "approvals": {"prefix_tokens": 2},
        "machines": {
            "default": "local",
            "defs": {
                "local": {
                    "type": "local",
                    "default_workdir": default_workdir,
                    "allowed_roots": allowed_roots,
                }
            },
        },
    }

    while typer.confirm("Add an SSH machine definition now?", default=False):
        name = typer.prompt("SSH machine name (e.g. buildbox)").strip()
        if not name:
            raise typer.BadParameter("SSH machine name cannot be empty")
        if name in cfg["machines"]["defs"]:  # type: ignore[index]
            raise typer.BadParameter(f"SSH machine name already exists: {name!r}")
        host = typer.prompt("SSH host (IP or hostname)").strip()
        if not host:
            raise typer.BadParameter("SSH host cannot be empty")
        user = typer.prompt("SSH username").strip()
        if not user:
            raise typer.BadParameter("SSH username cannot be empty")
        port_raw = typer.prompt("SSH port", default=22)
        try:
            port = int(port_raw)
        except Exception:
            raise typer.BadParameter(f"SSH port must be an integer: {port_raw!r}")
        if port <= 0:
            raise typer.BadParameter("SSH port must be > 0")
        ssh_workdir = typer.prompt("SSH default_workdir").strip()
        if not ssh_workdir:
            raise typer.BadParameter("SSH default_workdir cannot be empty")
        if not _is_absolute_or_tilde(ssh_workdir):
            raise typer.BadParameter("SSH default_workdir must be absolute (or start with ~)")
        ssh_roots_raw = typer.prompt(
            "SSH allowed_roots (comma separated, absolute paths)"
        )
        ssh_roots = _validate_allowed_roots(
            _parse_str_list(ssh_roots_raw),
            where="SSH allowed_roots",
        )
        use_agent = typer.confirm("SSH auth: use agent?", default=True)
        key_path = typer.prompt(
            "SSH auth: key_path (blank for none)",
            default="",
            show_default=False,
        ).strip()
        known_hosts = typer.prompt(
            "SSH known_hosts",
            default="~/.ssh/known_hosts",
        ).strip()
        if not known_hosts:
            raise typer.BadParameter("SSH known_hosts cannot be empty")
        codex_bin_override = typer.prompt(
            "SSH codex_bin override (blank to use global codex.bin)",
            default="",
            show_default=False,
        ).strip()

        ssh_def: dict[str, Any] = {
            "type": "ssh",
            "host": host,
            "user": user,
            "port": port,
            "default_workdir": ssh_workdir,
            "allowed_roots": ssh_roots,
            "auth": {
                "use_agent": bool(use_agent),
                "key_path": key_path or None,
            },
            "known_hosts": known_hosts,
        }
        if codex_bin_override:
            ssh_def["codex_bin"] = codex_bin_override
        cfg["machines"]["defs"][name] = ssh_def  # type: ignore[index]

        if not typer.confirm("Add another SSH machine?", default=False):
            break

    # Best-effort YAML dump; prefer PyYAML when present for nicer formatting.
    try:
        import yaml  # type: ignore

        text = yaml.safe_dump(cfg, sort_keys=False)  # type: ignore[attr-defined]
    except Exception:
        text = _yaml_dump_fallback(cfg)

    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(text, encoding="utf-8")
    typer.echo(f"Wrote: {config}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo(f"1) Set env var: export {token_env}=...  (keep it secret)")
    typer.echo(f"2) Validate: tgcodex-bot validate-config --config {config}")
    typer.echo(f"3) Run in background: tgcodex-bot start --config {config}")


@app.command("start")
def start_cmd(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
    pid_file: Optional[Path] = typer.Option(None, "--pid-file", dir_okay=False),
    log_file: Optional[Path] = typer.Option(None, "--log-file", dir_okay=False),
    check_binaries: bool = typer.Option(
        False, "--check-binaries/--no-check-binaries"
    ),
    validate: bool = typer.Option(True, "--validate/--no-validate"),
) -> None:
    """
    Start the bot in the background (detached), writing PID and logs next to the config.
    """

    if validate:
        cfg = load_config(config)
        errors = validate_config(cfg, validate_binaries=check_binaries)
        if errors:
            for e in errors:
                typer.echo(f"ERROR: {e}")
            raise typer.Exit(2)

    pf = pid_file or daemon.pid_file_for_config(config)
    lf = log_file or daemon.log_file_for_config(config)

    existing = daemon.read_pid(pf)
    if existing is not None and daemon.pid_file_matches_running_process(pf):
        typer.echo(f"Already running (pid={existing}).")
        raise typer.Exit(1)
    if existing is not None and pf.exists():
        try:
            pf.unlink()
        except Exception:
            pass

    argv = [
        sys.executable,
        "-m",
        "tgcodex.cli",
        "run",
        "--config",
        str(Path(config).expanduser().resolve()),
    ]
    pid = daemon.start_detached(
        argv,
        pid_file=pf,
        log_file=lf,
        env={"PYTHONUNBUFFERED": "1"},
        cwd=str(Path(config).expanduser().resolve().parent),
    )
    # Catch immediate startup failures before reporting success.
    deadline = time.monotonic() + 1.0
    seen_healthy = False
    while time.monotonic() < deadline:
        if daemon.pid_file_matches_running_process(pf):
            seen_healthy = True
        elif seen_healthy:
            # Became unhealthy during startup grace period.
            seen_healthy = False
            break
        time.sleep(0.05)
    if not seen_healthy:
        try:
            pf.unlink()
        except Exception:
            pass
        typer.echo("ERROR: Start failed; process exited immediately.")
        typer.echo(f"Log file: {lf}")
        tail = _tail_text(lf)
        if tail:
            typer.echo("--- log tail ---")
            typer.echo(tail)
        raise typer.Exit(1)
    typer.echo(f"Started (pid={pid}).")
    typer.echo(f"PID file: {pf}")
    typer.echo(f"Log file: {lf}")


@app.command("stop")
def stop_cmd(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
    pid_file: Optional[Path] = typer.Option(None, "--pid-file", dir_okay=False),
    timeout_seconds: float = typer.Option(10.0, "--timeout-seconds", min=0.0),
) -> None:
    """
    Stop a previously started background bot (SIGTERM).
    """

    pf = pid_file or daemon.pid_file_for_config(config)
    ok = daemon.stop(pf, timeout_seconds=float(timeout_seconds))
    if not ok:
        pid = daemon.read_pid(pf)
        if pid is None:
            typer.echo("Not running (no pid file).")
        else:
            typer.echo(f"Failed to stop (pid={pid}). Try killing it manually.")
        raise typer.Exit(1)
    typer.echo("Stopped.")


@app.command("status")
def status_cmd(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False),
    pid_file: Optional[Path] = typer.Option(None, "--pid-file", dir_okay=False),
) -> None:
    """
    Show background bot status based on pid file.
    """

    pf = pid_file or daemon.pid_file_for_config(config)
    pid = daemon.read_pid(pf)
    if pid is None:
        typer.echo("Not running.")
        raise typer.Exit(1)
    if daemon.pid_file_matches_running_process(pf):
        typer.echo(f"Running (pid={pid}).")
        return
    typer.echo(f"Not running (stale pid file: {pf}).")
    try:
        pf.unlink()
    except Exception:
        pass
    raise typer.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    # Allows `python -m tgcodex.cli ...`, used by `tgcodex-bot start`.
    app()
