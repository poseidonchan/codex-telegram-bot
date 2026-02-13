from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable


class CdNotAllowed(ValueError):
    def __init__(self, *, real: str, allowed_roots: list[str]) -> None:
        self.real = real
        self.allowed_roots = allowed_roots
        roots = ", ".join(allowed_roots) if allowed_roots else "(none)"
        super().__init__(
            f"Refusing to cd to {real!r}; outside allowed_roots ({roots})"
        )


def _is_within(child: str, root: str) -> bool:
    # commonpath is boundary-aware (unlike startswith).
    try:
        return os.path.commonpath([child, root]) == root
    except Exception:
        return False


def _normalize_join(current_workdir: str, new_path: str, *, expand_user: bool) -> str:
    # Best-effort normalization before resolving symlinks.
    #
    # IMPORTANT: For remote machines, "~" must be expanded on the remote side (not
    # using the bot host's HOME), so callers can disable local expansion.
    if expand_user and new_path.startswith("~"):
        new_path = os.path.expanduser(new_path)

    # Treat "~" paths like absolute paths to avoid joining them under the current
    # workdir (e.g. "/cwd/~/x"), which would prevent remote expansion.
    if new_path.startswith("~") or os.path.isabs(new_path):
        joined = new_path
    else:
        joined = os.path.join(current_workdir, new_path)
    return os.path.normpath(joined)


def resolve_cd_local(
    *,
    current_workdir: str,
    new_path: str,
    allowed_roots: Iterable[str],
) -> str:
    candidate = _normalize_join(current_workdir, new_path, expand_user=True)
    real = str(Path(candidate).resolve())
    roots = [str(Path(r).expanduser().resolve()) for r in allowed_roots]
    if not any(_is_within(real, rr) for rr in roots):
        raise CdNotAllowed(real=real, allowed_roots=roots)
    return real


async def resolve_cd(
    *,
    current_workdir: str,
    new_path: str,
    allowed_roots: Iterable[str],
    realpath: Callable[[str], "os.PathLike[str] | str"],
) -> str:
    # For remote machines where resolving must be delegated.
    candidate = _normalize_join(current_workdir, new_path, expand_user=False)
    resolved = str(await _maybe_await(realpath(candidate)))
    roots = [str(await _maybe_await(realpath(r))) for r in allowed_roots]
    if not any(_is_within(resolved, rr) for rr in roots):
        raise CdNotAllowed(real=resolved, allowed_roots=roots)
    return resolved


async def _maybe_await(value):  # type: ignore[no-untyped-def]
    if hasattr(value, "__await__"):
        return await value
    return value
