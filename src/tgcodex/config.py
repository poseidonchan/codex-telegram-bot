from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Optional

from tgcodex.constants import DEFAULT_DB_PATH

ApprovalPolicy = Literal["untrusted", "on-request", "on-failure", "never"]
SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
MachineType = Literal["local", "ssh"]


class ConfigError(ValueError):
    pass


def _require_yaml() -> Any:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ConfigError(
            "PyYAML is required to load config. Install project deps (see README.md)."
        ) from exc
    return yaml


def _as_dict(value: Any, *, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ConfigError(f"Expected mapping at {where}, got {type(value).__name__}")


def _as_list(value: Any, *, where: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ConfigError(f"Expected list at {where}, got {type(value).__name__}")


def _as_str(value: Any, *, where: str) -> str:
    if isinstance(value, str):
        return value
    raise ConfigError(f"Expected string at {where}, got {type(value).__name__}")


def _as_int(value: Any, *, where: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"Expected int at {where}, got bool")
    if isinstance(value, int):
        return value
    raise ConfigError(f"Expected int at {where}, got {type(value).__name__}")


def _as_float(value: Any, *, where: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise ConfigError(f"Expected number at {where}, got {type(value).__name__}")


def _as_opt_str(value: Any, *, where: str) -> Optional[str]:
    if value is None:
        return None
    return _as_str(value, where=where)


def _as_opt_int(value: Any, *, where: str) -> Optional[int]:
    if value is None:
        return None
    return _as_int(value, where=where)


def _as_bool(value: Any, *, where: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ConfigError(f"Expected bool at {where}, got {type(value).__name__}")


@dataclass(frozen=True)
class TelegramConfig:
    token_env: str
    allowed_user_ids: tuple[int, ...]

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "TelegramConfig":
        token_env = _as_str(d.get("token_env"), where="telegram.token_env")
        user_ids_raw = _as_list(d.get("allowed_user_ids"), where="telegram.allowed_user_ids")
        allowed_user_ids = tuple(_as_int(x, where="telegram.allowed_user_ids[]") for x in user_ids_raw)
        return TelegramConfig(token_env=token_env, allowed_user_ids=allowed_user_ids)


@dataclass(frozen=True)
class StateConfig:
    db_path: str

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "StateConfig":
        db_path = _as_str(d.get("db_path", DEFAULT_DB_PATH), where="state.db_path")
        return StateConfig(db_path=db_path)


@dataclass(frozen=True)
class CodexConfig:
    bin: str
    args: tuple[str, ...]
    model: Optional[str]
    sandbox: Optional[SandboxMode]
    approval_policy: ApprovalPolicy
    skip_git_repo_check: bool

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "CodexConfig":
        bin_ = _as_str(d.get("bin", "codex"), where="codex.bin")
        args_raw = _as_list(d.get("args"), where="codex.args")
        args = tuple(_as_str(x, where="codex.args[]") for x in args_raw)
        model = _as_opt_str(d.get("model"), where="codex.model")
        sandbox = d.get("sandbox")
        if sandbox is None:
            sandbox_mode: Optional[SandboxMode] = None
        else:
            sandbox_mode = _as_str(sandbox, where="codex.sandbox")  # type: ignore[assignment]
        approval_policy = _as_str(
            d.get("approval_policy", "untrusted"), where="codex.approval_policy"
        )
        if approval_policy not in ("untrusted", "on-request", "on-failure", "never"):
            raise ConfigError(
                "codex.approval_policy must be one of: untrusted|on-request|on-failure|never"
            )
        skip_git_repo_check = _as_bool(
            d.get("skip_git_repo_check", False), where="codex.skip_git_repo_check"
        )
        return CodexConfig(
            bin=bin_,
            args=args,
            model=model,
            sandbox=sandbox_mode,
            approval_policy=approval_policy,  # type: ignore[arg-type]
            skip_git_repo_check=skip_git_repo_check,
        )


@dataclass(frozen=True)
class OutputConfig:
    flush_interval_ms: int
    min_flush_chars: int
    max_flush_delay_seconds: float
    max_chars: int
    truncate: bool
    typing_interval_seconds: float
    show_codex_logs: bool
    show_tool_output: bool
    max_tool_output_chars: int

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "OutputConfig":
        return OutputConfig(
            flush_interval_ms=_as_int(
                d.get("flush_interval_ms", 250), where="output.flush_interval_ms"
            ),
            min_flush_chars=_as_int(
                d.get("min_flush_chars", 120), where="output.min_flush_chars"
            ),
            max_flush_delay_seconds=_as_float(
                d.get("max_flush_delay_seconds", 2.0),
                where="output.max_flush_delay_seconds",
            ),
            max_chars=_as_int(d.get("max_chars", 3500), where="output.max_chars"),
            truncate=_as_bool(d.get("truncate", True), where="output.truncate"),
            typing_interval_seconds=_as_float(
                d.get("typing_interval_seconds", 4.0),
                where="output.typing_interval_seconds",
            ),
            show_codex_logs=_as_bool(
                d.get("show_codex_logs", False), where="output.show_codex_logs"
            ),
            show_tool_output=_as_bool(
                d.get("show_tool_output", False), where="output.show_tool_output"
            ),
            max_tool_output_chars=_as_int(
                d.get("max_tool_output_chars", 1200),
                where="output.max_tool_output_chars",
            ),
        )


@dataclass(frozen=True)
class ApprovalsConfig:
    prefix_tokens: int

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "ApprovalsConfig":
        prefix_tokens = _as_int(d.get("prefix_tokens", 2), where="approvals.prefix_tokens")
        return ApprovalsConfig(prefix_tokens=prefix_tokens)


@dataclass(frozen=True)
class LocalMachineDef:
    type: Literal["local"]
    default_workdir: str
    allowed_roots: tuple[str, ...]
    codex_bin: Optional[str] = None


@dataclass(frozen=True)
class SSHAuthDef:
    use_agent: bool
    key_path: Optional[str]

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "SSHAuthDef":
        return SSHAuthDef(
            use_agent=_as_bool(d.get("use_agent", True), where="machines.*.auth.use_agent"),
            key_path=_as_opt_str(d.get("key_path"), where="machines.*.auth.key_path"),
        )


@dataclass(frozen=True)
class SSHMachineDef:
    type: Literal["ssh"]
    host: str
    user: str
    port: int
    default_workdir: str
    allowed_roots: tuple[str, ...]
    auth: SSHAuthDef
    known_hosts: str
    codex_bin: Optional[str] = None


MachineDef = LocalMachineDef | SSHMachineDef


@dataclass(frozen=True)
class MachinesConfig:
    default: str
    defs: dict[str, MachineDef]

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "MachinesConfig":
        default = _as_str(d.get("default"), where="machines.default")
        defs_raw = _as_dict(d.get("defs"), where="machines.defs")
        defs: dict[str, MachineDef] = {}
        for name, md_raw in defs_raw.items():
            if not isinstance(name, str):
                raise ConfigError("machines.defs keys must be strings")
            md = _as_dict(md_raw, where=f"machines.defs.{name}")
            mtype = _as_str(md.get("type"), where=f"machines.defs.{name}.type")
            if mtype == "local":
                defs[name] = LocalMachineDef(
                    type="local",
                    default_workdir=_as_str(
                        md.get("default_workdir"), where=f"machines.defs.{name}.default_workdir"
                    ),
                    allowed_roots=tuple(
                        _as_str(x, where=f"machines.defs.{name}.allowed_roots[]")
                        for x in _as_list(md.get("allowed_roots"), where=f"machines.defs.{name}.allowed_roots")
                    ),
                    codex_bin=_as_opt_str(md.get("codex_bin"), where=f"machines.defs.{name}.codex_bin"),
                )
            elif mtype == "ssh":
                auth = SSHAuthDef.from_dict(
                    _as_dict(md.get("auth"), where=f"machines.defs.{name}.auth")
                )
                defs[name] = SSHMachineDef(
                    type="ssh",
                    host=_as_str(md.get("host"), where=f"machines.defs.{name}.host"),
                    user=_as_str(md.get("user"), where=f"machines.defs.{name}.user"),
                    port=_as_int(md.get("port", 22), where=f"machines.defs.{name}.port"),
                    default_workdir=_as_str(
                        md.get("default_workdir"), where=f"machines.defs.{name}.default_workdir"
                    ),
                    allowed_roots=tuple(
                        _as_str(x, where=f"machines.defs.{name}.allowed_roots[]")
                        for x in _as_list(md.get("allowed_roots"), where=f"machines.defs.{name}.allowed_roots")
                    ),
                    auth=auth,
                    known_hosts=_as_str(md.get("known_hosts", "~/.ssh/known_hosts"), where=f"machines.defs.{name}.known_hosts"),
                    codex_bin=_as_opt_str(md.get("codex_bin"), where=f"machines.defs.{name}.codex_bin"),
                )
            else:
                raise ConfigError(
                    f"machines.defs.{name}.type must be one of: local|ssh"
                )
        return MachinesConfig(default=default, defs=defs)


@dataclass(frozen=True)
class Config:
    telegram: TelegramConfig
    state: StateConfig
    codex: CodexConfig
    output: OutputConfig
    approvals: ApprovalsConfig
    machines: MachinesConfig


def load_config(path: Path) -> Config:
    yaml = _require_yaml()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raise ConfigError("Config file is empty")
    if not isinstance(raw, dict):
        raise ConfigError("Top-level config must be a mapping")

    telegram = TelegramConfig.from_dict(_as_dict(raw.get("telegram"), where="telegram"))
    state = StateConfig.from_dict(_as_dict(raw.get("state"), where="state"))
    codex = CodexConfig.from_dict(_as_dict(raw.get("codex"), where="codex"))
    output = OutputConfig.from_dict(_as_dict(raw.get("output"), where="output"))
    approvals = ApprovalsConfig.from_dict(_as_dict(raw.get("approvals"), where="approvals"))
    machines = MachinesConfig.from_dict(_as_dict(raw.get("machines"), where="machines"))
    return Config(
        telegram=telegram,
        state=state,
        codex=codex,
        output=output,
        approvals=approvals,
        machines=machines,
    )


def validate_config(cfg: Config, *, validate_binaries: bool) -> list[str]:
    errors: list[str] = []

    # Telegram token presence (fail closed).
    if not cfg.telegram.allowed_user_ids:
        errors.append("telegram.allowed_user_ids must be non-empty")
    if not os.getenv(cfg.telegram.token_env):
        errors.append(f"Env var {cfg.telegram.token_env} is not set (telegram.token_env)")

    # Output guard rails (Telegram hard limit is 4096 chars).
    if cfg.output.max_chars > 3900:
        errors.append("output.max_chars must be <= 3900")
    if cfg.output.flush_interval_ms < 50:
        errors.append("output.flush_interval_ms must be >= 50")

    # Approvals.
    if cfg.approvals.prefix_tokens < 1:
        errors.append("approvals.prefix_tokens must be >= 1")

    # Machines.
    if cfg.machines.default not in cfg.machines.defs:
        errors.append("machines.default must exist in machines.defs")

    for name, md in cfg.machines.defs.items():
        if not md.allowed_roots:
            errors.append(f"machines.defs.{name}.allowed_roots must be non-empty")
            continue
        for root in md.allowed_roots:
            # Accept "~" roots as home-relative (expanded later where appropriate).
            if not (Path(root).is_absolute() or root.startswith("~")):
                errors.append(f"machines.defs.{name}.allowed_roots must be absolute: {root!r}")

        # Local-only path validation; for ssh we can't resolve here.
        if md.type == "local":
            try:
                default_real = Path(md.default_workdir).expanduser().resolve()
            except Exception as exc:
                errors.append(
                    f"machines.defs.{name}.default_workdir cannot be resolved: {exc}"
                )
                continue
            root_reals = []
            for r in md.allowed_roots:
                try:
                    root_reals.append(Path(r).expanduser().resolve())
                except Exception as exc:
                    errors.append(
                        f"machines.defs.{name}.allowed_roots entry cannot be resolved: {r!r} ({exc})"
                    )
            if root_reals:
                ok = any(
                    str(default_real) == str(rr) or str(default_real).startswith(str(rr) + os.sep)
                    for rr in root_reals
                )
                if not ok:
                    errors.append(
                        f"machines.defs.{name}.default_workdir must be within allowed_roots"
                    )

    if validate_binaries:
        # Best-effort local binary validation.
        import shutil

        codex_bin = cfg.codex.bin
        if os.path.isabs(codex_bin):
            if not Path(codex_bin).exists():
                errors.append(f"codex.bin not found: {codex_bin!r}")
        else:
            if not shutil.which(codex_bin):
                errors.append(f"codex.bin not found on PATH: {codex_bin!r}")

    return errors
