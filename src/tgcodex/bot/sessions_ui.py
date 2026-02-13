from __future__ import annotations

from typing import Optional


def derive_session_title(message: str, *, max_len: int = 60) -> str:
    """
    Best-effort, human-friendly default session title derived from the user's first message.

    Keeps it readable for `/resume` lists without exposing session IDs.
    """
    s = " ".join((message or "").strip().split())
    if not s:
        return "Untitled"
    if max_len > 3 and len(s) > max_len:
        s = s[: max_len - 3].rstrip() + "..."
    return s


def format_resume_label(
    *,
    title: Optional[str],
    updated_at: Optional[int],
    now_ts: int,
) -> str:
    base = " ".join((title or "").strip().split()) if title else ""
    if not base:
        base = "Untitled"

    if not updated_at:
        return base

    age_secs = max(0, int(now_ts) - int(updated_at))
    if age_secs < 60:
        age = "just now"
    elif age_secs < 3600:
        age = f"{age_secs // 60}m ago"
    elif age_secs < 86400:
        age = f"{age_secs // 3600}h ago"
    else:
        age = f"{age_secs // 86400}d ago"
    return f"{base} ({age})"

