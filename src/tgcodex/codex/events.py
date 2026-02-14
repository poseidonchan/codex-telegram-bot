from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ThreadStarted:
    thread_id: str


@dataclass(frozen=True)
class TurnStarted:
    pass


@dataclass(frozen=True)
class TurnCompleted:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None


@dataclass(frozen=True)
class TurnFailed:
    message: str


@dataclass(frozen=True)
class AgentMessageDelta:
    text: str


@dataclass(frozen=True)
class AgentMessage:
    text: str


@dataclass(frozen=True)
class AgentReasoningDelta:
    text: str


@dataclass(frozen=True)
class TokenCount:
    """
    Best-effort representation of Codex CLI token/rate-limit telemetry.

    Observed (codex-cli 0.98.0) inside `event_msg` payloads:
      {"type":"token_count","info":{...},"rate_limits":{...}}
    """

    model_context_window: Optional[int]
    total_tokens: Optional[int]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cached_input_tokens: Optional[int]
    reasoning_output_tokens: Optional[int]

    primary_used_percent: Optional[float]
    primary_window_minutes: Optional[int]
    primary_resets_at: Optional[int]

    secondary_used_percent: Optional[float]
    secondary_window_minutes: Optional[int]
    secondary_resets_at: Optional[int]

    raw: dict[str, Any]


@dataclass(frozen=True)
class ExecApprovalRequest:
    command: str
    cwd: Optional[str] = None
    reason: Optional[str] = None
    call_id: Optional[str] = None
    command_argv: Optional[list[str]] = None
    outer_id: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecCommandOutputDelta:
    text: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class ExecCommandEnd:
    exit_code: Optional[int]
    aggregated_output: Optional[str]
    raw: dict[str, Any]


@dataclass(frozen=True)
class ToolStarted:
    """Emitted when a command_execution item.started event is received."""
    command: str


@dataclass(frozen=True)
class LogLine:
    text: str


@dataclass(frozen=True)
class ErrorEvent:
    message: str
    raw: Optional[dict[str, Any]] = None


CodexEvent = (
    ThreadStarted
    | TurnStarted
    | TurnCompleted
    | TurnFailed
    | AgentMessageDelta
    | AgentMessage
    | AgentReasoningDelta
    | TokenCount
    | ExecApprovalRequest
    | ExecCommandOutputDelta
    | ExecCommandEnd
    | ToolStarted
    | LogLine
    | ErrorEvent
)


def parse_event_obj(obj: dict[str, Any]) -> list[CodexEvent]:
    """
    Best-effort mapping from Codex CLI JSONL events to a stable internal event set.

    Handles both the legacy event_msg/response_item format and the v0.98+ item.completed format.
    """
    # Some Codex CLI builds emit protocol-style envelopes:
    #   {"id":"...","msg":{...}}
    # Unwrap them while keeping outer metadata for approvals/debugging.
    raw_outer = obj
    outer_id: Optional[str] = None
    msg = obj.get("msg")
    if isinstance(msg, dict):
        oid = obj.get("id")
        if isinstance(oid, str) and oid:
            outer_id = oid
        obj = msg

    t = obj.get("type")

    # ── Legacy wrapper formats (older Codex versions) ─────────────────────────
    if isinstance(t, str) and t in ("event_msg", "response_item") and isinstance(obj.get("payload"), dict):
        return parse_event_obj(obj["payload"])

    # ── Session metadata (Codex exec) ─────────────────────────────────────────
    if t == "session_meta" and isinstance(obj.get("payload"), dict):
        sid = obj["payload"].get("id")
        if isinstance(sid, str) and sid:
            return [ThreadStarted(thread_id=sid)]

    # ── v0.98+ item.completed / item.started formats ──────────────────────────
    if t in ("item.completed", "item_completed"):
        item = obj.get("item")
        if isinstance(item, dict):
            return _parse_item(item, raw_outer=raw_outer, outer_id=outer_id)
        return []

    if t in ("item.started", "item_started"):
        item = obj.get("item")
        if isinstance(item, dict):
            # Approval requests can arrive on item.started (Codex pauses until a decision is sent).
            parsed = _parse_item(item, raw_outer=raw_outer, outer_id=outer_id)
            if parsed:
                return parsed
            if item.get("type") == "command_execution":
                cmd = item.get("command") or ""
                if isinstance(cmd, list):
                    cmd = " ".join(cmd)
                if cmd:
                    return [ToolStarted(command=str(cmd))]
        return []

    # ── Structured event types ─────────────────────────────────────────────────
    if t == "thread.started":
        thread_id = obj.get("thread_id")
        if isinstance(thread_id, str):
            return [ThreadStarted(thread_id=thread_id)]

    if t == "turn.started":
        return [TurnStarted()]

    if t == "turn.completed":
        usage = obj.get("usage") or {}
        return [TurnCompleted(
            input_tokens=usage.get("input_tokens") if isinstance(usage.get("input_tokens"), int) else None,
            output_tokens=usage.get("output_tokens") if isinstance(usage.get("output_tokens"), int) else None,
            cached_input_tokens=usage.get("cached_input_tokens") if isinstance(usage.get("cached_input_tokens"), int) else None,
        )]

    if t == "turn.failed":
        msg = None
        if isinstance(obj.get("error"), dict):
            msg = obj["error"].get("message")
        if not isinstance(msg, str):
            msg = obj.get("message")
        return [TurnFailed(message=str(msg) if msg is not None else "turn failed")]

    if t in ("error", "stream_error"):
        msg = obj.get("message") or obj.get("error_description") or obj.get("error")
        return [ErrorEvent(message=str(msg), raw=obj)]

    # ── Legacy payload types (inside event_msg/response_item) ─────────────────
    if t in ("agent_message_delta", "agent_message_content_delta"):
        delta = obj.get("delta") or obj.get("text") or obj.get("message")
        if isinstance(delta, str) and delta:
            return [AgentMessageDelta(text=delta)]

    if t == "agent_message":
        msg = obj.get("message") or obj.get("text")
        if isinstance(msg, str) and msg:
            return [AgentMessage(text=msg)]

    if t in ("agent_reasoning_delta", "reasoning_content_delta", "reasoning_raw_content_delta"):
        delta = obj.get("delta") or obj.get("text") or obj.get("content")
        if isinstance(delta, str) and delta:
            return [AgentReasoningDelta(text=delta)]

    if t == "token_count":
        info = obj.get("info")
        model_context_window: Optional[int] = None
        total_tokens: Optional[int] = None
        input_tokens: Optional[int] = None
        output_tokens: Optional[int] = None
        cached_input_tokens: Optional[int] = None
        reasoning_output_tokens: Optional[int] = None
        if isinstance(info, dict):
            mcw = info.get("model_context_window")
            if isinstance(mcw, int):
                model_context_window = mcw
            # `total_token_usage` is cumulative usage across the run; it's *not* the same as
            # "how much context is currently occupied". For context telemetry we want the last
            # request/turn usage when available.
            usage = info.get("last_token_usage") or info.get("total_token_usage")
            if isinstance(usage, dict):
                tt = usage.get("total_tokens")
                if isinstance(tt, int):
                    total_tokens = tt
                it = usage.get("input_tokens")
                if isinstance(it, int):
                    input_tokens = it
                ot = usage.get("output_tokens")
                if isinstance(ot, int):
                    output_tokens = ot
                cit = usage.get("cached_input_tokens")
                if isinstance(cit, int):
                    cached_input_tokens = cit
                rot = usage.get("reasoning_output_tokens")
                if isinstance(rot, int):
                    reasoning_output_tokens = rot

        rl = obj.get("rate_limits")
        p_used = p_window = p_resets = None
        s_used = s_window = s_resets = None
        if isinstance(rl, dict):
            primary = rl.get("primary")
            if isinstance(primary, dict):
                used = primary.get("used_percent")
                if isinstance(used, (int, float)) and not isinstance(used, bool):
                    p_used = float(used)
                win = primary.get("window_minutes")
                if isinstance(win, int):
                    p_window = win
                ra = primary.get("resets_at")
                if isinstance(ra, int):
                    p_resets = ra

            secondary = rl.get("secondary")
            if isinstance(secondary, dict):
                used = secondary.get("used_percent")
                if isinstance(used, (int, float)) and not isinstance(used, bool):
                    s_used = float(used)
                win = secondary.get("window_minutes")
                if isinstance(win, int):
                    s_window = win
                ra = secondary.get("resets_at")
                if isinstance(ra, int):
                    s_resets = ra

        return [TokenCount(
            model_context_window=model_context_window,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            primary_used_percent=p_used,
            primary_window_minutes=p_window,
            primary_resets_at=p_resets,
            secondary_used_percent=s_used,
            secondary_window_minutes=s_window,
            secondary_resets_at=s_resets,
            raw=obj,
        )]

    # OpenAI tool-call format: surface exec_command requests and outputs as tool events.
    if t == "function_call":
        name = obj.get("name")
        args_raw = obj.get("arguments")
        if name == "exec_command" and isinstance(args_raw, (str, dict)):
            args = None
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = None
            elif isinstance(args_raw, dict):
                args = args_raw
            if isinstance(args, dict):
                cmd = args.get("cmd")
                if isinstance(cmd, list):
                    argv = [str(c) for c in cmd]
                    try:
                        cmd = shlex.join(argv)
                    except Exception:
                        cmd = " ".join(argv)
                if isinstance(cmd, str) and cmd:
                    call_id = obj.get("call_id")
                    sandbox_perm = args.get("sandbox_permissions")
                    if isinstance(sandbox_perm, str) and sandbox_perm.startswith("require"):
                        cwd = args.get("cwd")
                        reason = args.get("justification")
                        return [ExecApprovalRequest(
                            command=cmd,
                            cwd=cwd if isinstance(cwd, str) else None,
                            reason=reason if isinstance(reason, str) else None,
                            call_id=call_id if isinstance(call_id, str) else None,
                            outer_id=outer_id,
                            raw=raw_outer,
                        )]
                    return [ToolStarted(command=cmd)]

    if t == "function_call_output":
        out = obj.get("output")
        if isinstance(out, str):
            return [ExecCommandEnd(exit_code=None, aggregated_output=out, raw=obj)]

    if t == "exec_approval_request":
        cmd = obj.get("command")
        # command can be a list or a string
        if isinstance(cmd, list):
            argv = [str(c) for c in cmd]
            try:
                cmd = shlex.join(argv)
            except Exception:
                cmd = " ".join(argv)
        if not isinstance(cmd, str):
            cmd = _first_str(obj, ("codex_command", "cmd"))
        cwd = _first_str(obj, ("cwd", "codex_cwd", "working_directory"))
        reason = _first_str(obj, ("reason", "codex_reason"))
        call_id = obj.get("call_id")
        cmd_argv = obj.get("command") if isinstance(obj.get("command"), list) else None
        if cmd:
            return [ExecApprovalRequest(
                command=cmd,
                cwd=cwd,
                reason=reason,
                call_id=call_id if isinstance(call_id, str) else None,
                command_argv=[str(c) for c in cmd_argv] if isinstance(cmd_argv, list) else None,
                outer_id=outer_id,
                raw=raw_outer,
            )]

    if t == "exec_command_output_delta":
        text = _first_str(obj, ("chunk", "text", "output", "delta", "aggregated_output"))
        if text:
            return [ExecCommandOutputDelta(text=text, raw=obj)]

    if t == "exec_command_end":
        exit_code = obj.get("exit_code")
        if isinstance(exit_code, bool):
            exit_code = None
        if not isinstance(exit_code, int):
            exit_code = None
        aggregated_output = _first_str(obj, ("aggregated_output", "formatted_output", "output"))
        return [ExecCommandEnd(exit_code=exit_code, aggregated_output=aggregated_output, raw=obj)]

    # Legacy response_item message format (role=assistant)
    if t == "message" and obj.get("role") == "assistant":
        content = obj.get("content") or []
        if isinstance(content, list):
            texts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "output_text"
            ]
            combined = "".join(texts)
            if combined:
                return [AgentMessage(text=combined)]

    return []


def _parse_item(
    item: dict[str, Any], *, raw_outer: dict[str, Any], outer_id: Optional[str]
) -> list[CodexEvent]:
    """Parse a single item object from item.completed or similar."""
    item_type = item.get("type")

    if item_type == "agent_message":
        text = item.get("text") or item.get("message") or ""
        if isinstance(text, str) and text:
            return [AgentMessage(text=text)]

    if item_type == "reasoning":
        # Reasoning text comes as .text or nested in .summary list
        text = item.get("text") or ""
        if not text:
            summary = item.get("summary") or []
            if isinstance(summary, list):
                text = " ".join(
                    s.get("text", "") for s in summary
                    if isinstance(s, dict) and s.get("type") == "summary_text"
                )
        if isinstance(text, str) and text:
            return [AgentReasoningDelta(text=text)]

    if item_type == "command_execution":
        status = item.get("status")
        if status == "completed":
            cmd = item.get("command") or ""
            if isinstance(cmd, list):
                cmd = " ".join(cmd)
            exit_code = item.get("exit_code")
            if isinstance(exit_code, bool):
                exit_code = None
            if not isinstance(exit_code, int):
                exit_code = None
            out = item.get("aggregated_output")
            return [ExecCommandEnd(
                exit_code=exit_code,
                aggregated_output=out if isinstance(out, str) else None,
                raw=raw_outer,
            )]

    if item_type == "exec_approval_request":
        cmd = item.get("command")
        if isinstance(cmd, list):
            argv = [str(c) for c in cmd]
            try:
                cmd = shlex.join(argv)
            except Exception:
                cmd = " ".join(argv)
        if not isinstance(cmd, str):
            cmd = ""
        cwd = item.get("cwd")
        reason = item.get("reason")
        call_id = item.get("call_id")
        if cmd:
            return [ExecApprovalRequest(
                command=cmd,
                cwd=cwd if isinstance(cwd, str) else None,
                reason=reason if isinstance(reason, str) else None,
                call_id=call_id if isinstance(call_id, str) else None,
                command_argv=[str(c) for c in item.get("command")] if isinstance(item.get("command"), list) else None,
                outer_id=outer_id,
                raw=raw_outer,
            )]

    return []


def parse_json_line(line: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        obj = json.loads(line)
    except Exception:
        return None, line
    if not isinstance(obj, dict):
        return None, line
    return obj, None


def _first_str(obj: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v:
            return v
    return None
