from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from tgcodex.state.models import ActiveRun, ChatState
from tgcodex.util.text import escape_html


def fmt_code_inline(s: str) -> str:
    return f"<code>{escape_html(s)}</code>"


def fmt_bold(s: str) -> str:
    return f"<b>{escape_html(s)}</b>"


def fmt_status(state: ChatState, run: Optional[ActiveRun]) -> str:
    parts: list[str] = []
    parts.append(f"Machine: {state.machine_name}")
    parts.append(f"Workdir: {state.workdir}")
    parts.append(f"Session title: {state.session_title or 'None'}")
    parts.append(f"Session ID: {state.active_session_id or 'None'}")
    parts.append(f"Approval: {state.approval_policy}")
    if state.model:
        model_str = state.model
        if state.thinking_level:
            model_str += f" ({state.thinking_level})"
        parts.append(f"Model: {model_str}")
    parts.append(f"Reasoning: {'on' if state.show_reasoning else 'off'}")
    parts.append(f"Plan mode: {'on' if state.plan_mode else 'off'}")

    # Context: best-effort, from token_count telemetry (if available).
    if state.last_context_remaining is not None and state.last_context_window is not None:
        pct = 0.0
        if state.last_context_window > 0:
            pct = (state.last_context_remaining / state.last_context_window) * 100.0
        parts.append(
            "Context remaining: "
            f"{state.last_context_remaining:,} / {state.last_context_window:,} tokens ({pct:.1f}%)"
        )
    elif state.last_context_remaining is not None:
        parts.append(
            f"Context remaining: {state.last_context_remaining:,} tokens"
        )
    else:
        parts.append("Context remaining: Unknown")

    # Rate limits: best-effort, from token_count telemetry (if available).
    rl_lines: list[str] = []
    if state.rate_primary_used_percent is not None:
        rl_lines.append(_fmt_rate_line(
            label="primary",
            used_percent=state.rate_primary_used_percent,
            window_minutes=state.rate_primary_window_minutes,
            resets_at=state.rate_primary_resets_at,
        ))
    if state.rate_secondary_used_percent is not None:
        rl_lines.append(_fmt_rate_line(
            label="secondary",
            used_percent=state.rate_secondary_used_percent,
            window_minutes=state.rate_secondary_window_minutes,
            resets_at=state.rate_secondary_resets_at,
        ))
    if rl_lines:
        parts.append("Rate limit:")
        parts.extend(rl_lines)
    else:
        parts.append("Rate limit: Unknown")

    if state.last_input_tokens is not None or state.last_output_tokens is not None:
        in_tok = state.last_input_tokens or 0
        out_tok = state.last_output_tokens or 0
        cached = state.last_cached_tokens or 0
        tok_str = f"in={in_tok:,} out={out_tok:,}"
        if cached:
            tok_str += f" cached={cached:,}"
        parts.append(f"Last tokens: {tok_str}")
    if run:
        parts.append(f"Run: {run.status} ({run.run_id})")
    return "\n".join(parts)


def _fmt_rate_line(
    *,
    label: str,
    used_percent: float,
    window_minutes: Optional[int],
    resets_at: Optional[int],
) -> str:
    s = f"- {label}: {used_percent:.1f}%"
    if window_minutes:
        s += f" / {window_minutes}m"
    if resets_at:
        dt = datetime.fromtimestamp(resets_at, tz=timezone.utc)
        s += f" (resets {dt.strftime('%Y-%m-%d %H:%M:%S')} UTC)"
    return s


def fmt_approval_prompt(*, command: str, cwd: Optional[str], reason: Optional[str]) -> str:
    lines: list[str] = []
    lines.append("Command approval required")
    if cwd:
        lines.append(f"CWD: {cwd}")
    if reason:
        lines.append(f"Reason: {reason}")
    lines.append("Command:")
    lines.append(command)
    return "\n".join(lines)
