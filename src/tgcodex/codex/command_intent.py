from __future__ import annotations

import shlex


_WRITE_COMMANDS = {
    "rm",
    "rmdir",
    "mkdir",
    "mv",
    "cp",
    "touch",
    "chmod",
    "chown",
    "ln",
    "tee",
    "truncate",
    "dd",
}

_GIT_WRITE_SUBCOMMANDS = {
    "add",
    "am",
    "apply",
    "checkout",
    "cherry-pick",
    "clean",
    "clone",
    "commit",
    "init",
    "merge",
    "pull",
    "push",
    "rebase",
    "reset",
    "restore",
    "rm",
    "stash",
    "switch",
}


def needs_write_approval(command: str) -> bool:
    """
    Heuristic: returns True when a shell command likely mutates the filesystem or repo.

    This is used to implement "Always ask" defense-in-depth in tgcodex when Codex exec-mode
    does not emit approval request events.
    """
    s = (command or "").strip()
    if not s:
        return False

    try:
        tokens = shlex.split(s, posix=True)
    except Exception:
        # If we can't parse, fail closed.
        return True

    if not tokens:
        return False

    # Common wrappers: the actual command is inside the -lc string.
    # Example: /bin/bash -lc "cd /home/ubuntu && mkdir -p foo"
    if (
        len(tokens) >= 3
        and tokens[0] in ("bash", "/bin/bash", "sh", "/bin/sh")
        and tokens[1] == "-lc"
        and isinstance(tokens[2], str)
        and tokens[2].strip()
    ):
        # Recurse on the inner command string.
        return needs_write_approval(tokens[2])

    for i, tok in enumerate(tokens):
        # Shell redirections can write even if the command itself is "read-only".
        if ">" in tok and tok != "2>&1":
            return True
        if tok in (">", ">>"):
            return True

        # Common write-ish primitives.
        if tok in _WRITE_COMMANDS:
            return True

        # In-place edits.
        if tok == "sed":
            for t in tokens[i + 1 :]:
                if t == "-i" or t.startswith("-i"):
                    return True

        # Git: treat many subcommands as mutating.
        if tok == "git" and i + 1 < len(tokens):
            sub = tokens[i + 1]
            if sub in _GIT_WRITE_SUBCOMMANDS:
                return True

    return False
