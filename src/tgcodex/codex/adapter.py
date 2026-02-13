from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from tgcodex.codex.cli_runner import CodexRun, start_codex_process
from tgcodex.machines.base import Machine


@dataclass(frozen=True)
class RunSettings:
    codex_bin: str
    codex_args: tuple[str, ...]
    model: Optional[str]
    thinking_level: Optional[str]
    sandbox: Optional[str]
    approval_policy: str
    skip_git_repo_check: bool


@dataclass(frozen=True)
class SessionMeta:
    session_id: str
    path: Optional[str]
    updated_at: Optional[int]
    title: Optional[str] = None


class CodexAdapter(Protocol):
    async def start_run(
        self,
        *,
        machine: Machine,
        session_id: Optional[str],
        workdir: str,
        prompt: str,
        settings: RunSettings,
    ) -> CodexRun: ...


class CodexCLIAdapter:
    @staticmethod
    def build_argv(
        *,
        settings: RunSettings,
        session_id: Optional[str],
        workdir: str,
        prompt: str,
    ) -> list[str]:
        """
        Build a `codex` CLI argv list.

        NOTE: `codex exec resume` (codex-cli 0.98.0) does not accept `--color` after the `resume`
        subcommand; keep the argv layout compatible with both `exec` and `exec resume`.
        """
        argv: list[str] = [settings.codex_bin]
        if settings.sandbox:
            argv += ["-s", settings.sandbox]
        argv += ["-a", settings.approval_policy]
        argv += ["-C", workdir]
        if settings.approval_policy == "untrusted":
            # "Always ask" semantics: force the current project to be treated as untrusted even if
            # the user's global Codex config has marked it as trusted.
            argv += ["-c", f'projects."{workdir}".trust_level="untrusted"']
        if settings.thinking_level:
            # Codex CLI expects TOML-parsed values; keep it explicit.
            argv += ["-c", f'model_reasoning_effort="{settings.thinking_level}"']

        argv += ["exec"]

        if session_id is not None:
            argv += ["resume", session_id]

        # JSON is required so tgcodex can parse events from stdout.
        argv += ["--json"]
        if settings.skip_git_repo_check:
            argv += ["--skip-git-repo-check"]
        if settings.model:
            argv += ["-m", settings.model]
        argv += list(settings.codex_args)

        argv += [prompt]
        return argv

    async def start_run(
        self,
        *,
        machine: Machine,
        session_id: Optional[str],
        workdir: str,
        prompt: str,
        settings: RunSettings,
    ) -> CodexRun:
        argv = self.build_argv(
            settings=settings,
            session_id=session_id,
            workdir=workdir,
            prompt=prompt,
        )

        return await start_codex_process(
            machine=machine,
            argv=argv,
            cwd=workdir,
            env=None,
            pty=False,
        )
