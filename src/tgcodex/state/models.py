from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ChatState:
    chat_id: int
    machine_name: str
    workdir: str
    active_session_id: Optional[str]
    session_title: Optional[str]
    approval_policy: str
    approval_mode: str
    model: Optional[str]
    thinking_level: Optional[str]
    show_reasoning: bool
    plan_mode: bool
    last_input_tokens: Optional[int]
    last_output_tokens: Optional[int]
    last_cached_tokens: Optional[int]
    last_total_tokens: Optional[int]
    last_context_window: Optional[int]
    last_context_remaining: Optional[int]
    rate_primary_used_percent: Optional[float]
    rate_primary_window_minutes: Optional[int]
    rate_primary_resets_at: Optional[int]
    rate_secondary_used_percent: Optional[float]
    rate_secondary_window_minutes: Optional[int]
    rate_secondary_resets_at: Optional[int]
    updated_at: int


@dataclass(frozen=True)
class ActiveRun:
    chat_id: int
    run_id: str
    status: str
    pending_action_json: Optional[str]
    updated_at: int


@dataclass(frozen=True)
class SessionIndexRow:
    id: int
    chat_id: int
    machine_name: str
    session_id: str
    title: Optional[str]
    created_at: Optional[int]
    last_used_at: Optional[int]


@dataclass(frozen=True)
class TrustedPrefixRow:
    id: int
    machine_name: str
    session_id: str
    prefix: str
    created_at: int
