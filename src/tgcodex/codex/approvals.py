from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from tgcodex.util.shlex_tokens import prefix_string, split_command

ApprovalDecision = Literal["approved", "denied"]


@dataclass(frozen=True)
class PrefixMatch:
    prefix: str
    matched: bool


def command_prefix(command: str, *, prefix_tokens: int) -> str:
    tokens = split_command(command)
    return prefix_string(tokens, prefix_tokens)


def is_trusted_prefix(
    command: str, *, trusted_prefixes: Iterable[str], prefix_tokens: int
) -> PrefixMatch:
    prefix = command_prefix(command, prefix_tokens=prefix_tokens)
    matched = prefix in set(trusted_prefixes)
    return PrefixMatch(prefix=prefix, matched=matched)


def should_prompt_for_approval(
    *,
    approval_policy: str,
    command: str,
    trusted_prefixes: Iterable[str],
    prefix_tokens: int,
) -> tuple[bool, str]:
    """
    Returns (needs_prompt, canonical_prefix).

    The Codex CLI may already be in an approval-gated mode; this function is used to decide whether
    to show Telegram UI or auto-approve based on stored trusted prefixes.
    """
    match = is_trusted_prefix(
        command, trusted_prefixes=trusted_prefixes, prefix_tokens=prefix_tokens
    )
    if approval_policy == "never":
        return (False, match.prefix)
    # untrusted: Always ask (don't auto-approve even if user previously trusted a prefix).
    if approval_policy == "untrusted":
        return (True, match.prefix)
    # on-request: allow stored trusted prefixes to skip prompting (for reduced friction).
    if approval_policy == "on-request":
        return (not match.matched, match.prefix)
    # on-failure: treat approval requests as requiring user attention.
    if approval_policy == "on-failure":
        return (True, match.prefix)
    return (True, match.prefix)
