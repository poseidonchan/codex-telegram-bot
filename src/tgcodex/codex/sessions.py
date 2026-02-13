from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from tgcodex.codex.adapter import SessionMeta
from tgcodex.codex.events import TokenCount, parse_event_obj, parse_json_line
from tgcodex.machines.base import Machine


def _session_id_from_filename(path: str) -> Optional[str]:
    stem = Path(path).name
    if not stem.endswith(".jsonl"):
        return None
    stem = stem[: -len(".jsonl")]
    parts = stem.split("-")
    if len(parts) < 5:
        return None
    candidate = "-".join(parts[-5:])
    # Cheap sanity check: expect 5 groups.
    if candidate.count("-") != 4:
        return None
    return candidate


def extract_latest_token_count(jsonl_text: str) -> Optional[TokenCount]:
    """
    Best-effort scan of a Codex session JSONL file contents for the latest token_count telemetry.

    This is used as a fallback for `/status` when the bot didn't persist token/rate-limit info
    during streaming (e.g. after a restart or older versions).
    """
    latest: Optional[TokenCount] = None
    for line in jsonl_text.splitlines():
        obj, _non_json = parse_json_line(line)
        if obj is None:
            continue
        for ev in parse_event_obj(obj):
            if isinstance(ev, TokenCount):
                latest = ev
    return latest


async def find_session_path(machine: Machine, *, session_id: str) -> Optional[str]:
    sessions_dir = await machine.realpath("~/.codex/sessions")
    # Codex session files typically end with the session id.
    pattern = os.path.join(sessions_dir, "**", f"*{session_id}.jsonl")
    paths = await machine.list_glob(pattern)
    return paths[0] if paths else None


async def read_latest_token_count(machine: Machine, *, session_id: str) -> Optional[TokenCount]:
    path = await find_session_path(machine, session_id=session_id)
    if not path:
        return None
    # Fast path: session JSONLs can get large. Try tailing the end first.
    try:
        res = await machine.exec_capture(["tail", "-n", "2000", path], cwd=None)
        if res.exit_code == 0 and (res.stdout or "").strip():
            ev = extract_latest_token_count(res.stdout)
            if ev is not None:
                return ev
    except Exception:
        pass
    try:
        text = await machine.read_text(path)
    except Exception:
        return None
    return extract_latest_token_count(text)


async def list_sessions(machine: Machine, *, limit: int = 50) -> list[SessionMeta]:
    sessions_dir = await machine.realpath("~/.codex/sessions")
    paths = await machine.list_glob(os.path.join(sessions_dir, "**", "*.jsonl"))
    out: list[SessionMeta] = []
    for p in paths:
        sid = _session_id_from_filename(p)
        if not sid:
            continue
        updated_at: Optional[int] = None
        if getattr(machine, "type", None) == "local":
            try:
                updated_at = int(Path(p).stat().st_mtime)
            except Exception:
                updated_at = None
        else:
            try:
                res = await machine.exec_capture(["stat", "-c", "%Y", p], cwd=None)
                if res.exit_code == 0:
                    updated_at = int(res.stdout.strip() or "0") or None
            except Exception:
                updated_at = None
        out.append(SessionMeta(session_id=sid, path=p, updated_at=updated_at))

    out.sort(key=lambda x: x.updated_at or 0, reverse=True)
    return out[:limit]
