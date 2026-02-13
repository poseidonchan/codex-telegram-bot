from __future__ import annotations

from dataclasses import dataclass
import os
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
    def _toml_basic_escape(s: str) -> str:
        # Escape a value for inclusion inside TOML basic-string quotes.
        # https://toml.io/ (basic string)
        return s.replace("\\", "\\\\").replace('"', '\\"')

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
            wd = CodexCLIAdapter._toml_basic_escape(workdir)
            argv += ["-c", f'projects."{wd}".trust_level="untrusted"']
        if settings.thinking_level:
            # Codex CLI expects TOML-parsed values; keep it explicit.
            effort = CodexCLIAdapter._toml_basic_escape(settings.thinking_level)
            argv += ["-c", f'model_reasoning_effort="{effort}"']

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
        # Normalize/resolve the workdir before passing it to Codex.
        #
        # Codex uses exact project-path keys in config (projects."<path>"). Trailing slashes or
        # symlink differences can cause a trust override to miss, which in turn can bypass approval
        # prompts. Use the machine's realpath to match Codex's canonicalization as closely as
        # possible.
        try:
            workdir = await machine.realpath(workdir)
        except Exception:
            workdir = os.path.normpath(workdir)

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
