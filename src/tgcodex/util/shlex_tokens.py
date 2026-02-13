from __future__ import annotations

import shlex
from typing import Iterable


def split_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except Exception:
        # Fallback: whitespace split (best-effort).
        return command.strip().split()


def prefix_string(tokens: Iterable[str], n: int) -> str:
    out: list[str] = []
    for i, t in enumerate(tokens):
        if i >= n:
            break
        out.append(t)
    return " ".join(out)

