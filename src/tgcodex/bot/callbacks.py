from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tgcodex.bot.auth import is_allowed_user


def _rt(context: Any):
    return context.application.bot_data["runtime"]


def _log(msg: str) -> None:
    # Keep logs local to stdout so detached mode captures them in .tgcodex-bot/<config>.log.
    try:
        print(f"[tgcodex-bot] {msg}", flush=True)
    except Exception:
        pass


_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Telegram bot tokens often appear in request URLs/exceptions as "<digits>:<opaque>".
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"), "<BOT_TOKEN>"),
    # Best-effort OpenAI API key redaction (not exhaustive).
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "<OPENAI_KEY>"),
)


def _redact(s: str) -> str:
    for pat, repl in _REDACT_PATTERNS:
        s = pat.sub(repl, s)
    return s


def _cmd_tag(cmd: str) -> str:
    return hashlib.sha256(cmd.encode("utf-8", "replace")).hexdigest()[:12]


def _cmd_preview(cmd: str, *, max_chars: int = 80) -> str:
    one = " ".join((cmd or "").split())
    one = _redact(one)
    if len(one) > max_chars:
        one = one[:max_chars].rstrip() + "..."
    return one


def _truncate_text(s: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "\n...[truncated]...\n"


async def on_callback_query(update: Any, context: Any) -> None:
    runtime = _rt(context)
    user_id = getattr(update.effective_user, "id", None)
    if not is_allowed_user(user_id, allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        try:
            await update.callback_query.answer("Unauthorized", show_alert=True)
        except Exception:
            pass
        return

    q = update.callback_query
    data = q.data or ""
    chat = getattr(getattr(q, "message", None), "chat", None)
    chat_id = getattr(chat, "id", None) or getattr(getattr(update, "effective_chat", None), "id", None)
    if chat_id is None:
        _log(f"callback missing chat_id data={data!r}")
        try:
            await q.answer("No chat for this button.", show_alert=True)
        except Exception:
            pass
        return

    # Provide a visible toast for model selection flows so users can tell the click was received.
    answer_text: str | None = None
    if data.startswith("model_select:"):
        answer_text = "Loading thinking levels..."
    elif data.startswith("thinking_select:"):
        answer_text = "Saved."
    elif data.startswith("approval_mode_select:"):
        answer_text = "Loading..."
    elif data.startswith("approval_mode_confirm:"):
        answer_text = "Saved."
    elif data.startswith("sandbox_select:"):
        answer_text = "Loading..."
    elif data.startswith("sandbox_confirm:"):
        answer_text = "Saved."
    elif data == "cbtest_ping":
        answer_text = "pong"

    try:
        if answer_text:
            await q.answer(text=answer_text)
        else:
            await q.answer()
    except Exception:
        pass

    # Helps diagnose cases where inline button clicks appear to do nothing.
    _log(f"callback data={data!r}")

    if data == "cbtest_ping":
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(chat_id=chat_id, text="Callback OK.")
        return

    if data == "resume_cancel":
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if data.startswith("resume:"):
        session_id = data.split(":", 1)[1]
        state = runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        idx = runtime.store.get_session_index(machine_name=state.machine_name, session_id=session_id)
        title = idx.title if idx and idx.title else None
        runtime.store.set_session(chat_id=chat_id, session_id=session_id, title=title)
        runtime.store.upsert_session_index(
            chat_id=chat_id,
            machine_name=state.machine_name,
            session_id=session_id,
            title=title,
        )
        if title:
            await context.bot.send_message(chat_id=chat_id, text=f"Active session set to: {title}")
        else:
            await context.bot.send_message(chat_id=chat_id, text="Active session set.")
        return

    if data == "approval_mode_cancel":
        active = runtime.store.get_active_run(chat_id)
        if active and active.status == "waiting_approval":
            await context.bot.send_message(chat_id=chat_id, text="Approval pending — approve/reject first.")
            return

        state = runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        current = state.approval_mode
        if current == "always":
            # Backward compat: "always" mode was removed; treat as on-request.
            current = "on-request"
        modes = [
            ("on-request", "🤔 Ask on request"),
            ("yolo", "☠️ YOLO"),
        ]
        buttons = [
            InlineKeyboardButton(
                f"{'✅ ' if m == current else ''}{label}",
                callback_data=f"approval_mode_select:{m}",
            )
            for m, label in modes
        ]
        kb = InlineKeyboardMarkup([[b] for b in buttons])
        try:
            await q.edit_message_text(text="Choose approval mode:", reply_markup=kb)
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Choose approval mode:", reply_markup=kb)
        return

    if data.startswith("approval_mode_select:"):
        mode = data.split(":", 1)[1]
        # Backward compat: old inline UIs may still contain "always".
        if mode == "always":
            mode = "on-request"
        if mode not in ("on-request", "yolo"):
            await context.bot.send_message(chat_id=chat_id, text="Invalid mode.")
            return
        active = runtime.store.get_active_run(chat_id)
        if active and active.status == "waiting_approval":
            await context.bot.send_message(chat_id=chat_id, text="Approval pending — approve/reject first.")
            return
        runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )

        if mode == "yolo":
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Confirm YOLO", callback_data="approval_mode_confirm:yolo"),
                        InlineKeyboardButton("Cancel", callback_data="approval_mode_cancel"),
                    ]
                ]
            )
            try:
                await q.edit_message_text(
                    text="Confirm YOLO? This disables approvals.",
                    reply_markup=kb,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Confirm YOLO? This disables approvals.",
                    reply_markup=kb,
                )
            return

        runtime.store.update_chat_state(chat_id, approval_mode=mode)
        # Apply on the next run: restart the Codex thread to avoid stale per-thread policies.
        runtime.store.clear_session(chat_id=chat_id)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Approval mode set to: {mode}\nSession cleared. Next message starts a new session.",
        )
        return

    if data.startswith("approval_mode_confirm:"):
        mode = data.split(":", 1)[1]
        if mode != "yolo":
            await context.bot.send_message(chat_id=chat_id, text="Invalid confirmation.")
            return
        active = runtime.store.get_active_run(chat_id)
        if active and active.status == "waiting_approval":
            await context.bot.send_message(chat_id=chat_id, text="Approval pending — approve/reject first.")
            return
        runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        runtime.store.update_chat_state(chat_id=chat_id, approval_mode="yolo")
        runtime.store.clear_session(chat_id=chat_id)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text="Approval mode set to: yolo\nSession cleared. Next message starts a new session.",
        )
        return

    if data == "sandbox_cancel":
        active = runtime.store.get_active_run(chat_id)
        if active and active.status == "waiting_approval":
            await context.bot.send_message(chat_id=chat_id, text="Approval pending — approve/reject first.")
            return

        state = runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        current = state.sandbox_mode or (runtime.cfg.codex.sandbox or "workspace-write")
        modes = [
            ("read-only", "🔒 Read-only"),
            ("workspace-write", "✍️ Workspace-write"),
            ("danger-full-access", "☠️ Full access"),
        ]
        buttons = [
            InlineKeyboardButton(
                f"{'✅ ' if m == current else ''}{label}",
                callback_data=f"sandbox_select:{m}",
            )
            for m, label in modes
        ]
        kb = InlineKeyboardMarkup([[b] for b in buttons])
        try:
            await q.edit_message_text(text="Choose sandbox mode:", reply_markup=kb)
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Choose sandbox mode:", reply_markup=kb)
        return

    if data.startswith("sandbox_select:"):
        mode = data.split(":", 1)[1]
        if mode not in ("read-only", "workspace-write", "danger-full-access"):
            await context.bot.send_message(chat_id=chat_id, text="Invalid sandbox mode.")
            return
        active = runtime.store.get_active_run(chat_id)
        if active and active.status == "waiting_approval":
            await context.bot.send_message(chat_id=chat_id, text="Approval pending — approve/reject first.")
            return

        runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )

        if mode == "danger-full-access":
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Confirm full access",
                            callback_data="sandbox_confirm:danger-full-access",
                        ),
                        InlineKeyboardButton("Cancel", callback_data="sandbox_cancel"),
                    ]
                ]
            )
            try:
                await q.edit_message_text(
                    text="Confirm danger-full-access? This weakens sandbox containment.",
                    reply_markup=kb,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Confirm danger-full-access? This weakens sandbox containment.",
                    reply_markup=kb,
                )
            return

        runtime.store.update_chat_state(chat_id=chat_id, sandbox_mode=mode)
        runtime.store.clear_session(chat_id=chat_id)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Sandbox mode set to: {mode}\nSession cleared. Next message starts a new session.",
        )
        return

    if data.startswith("sandbox_confirm:"):
        mode = data.split(":", 1)[1]
        if mode != "danger-full-access":
            await context.bot.send_message(chat_id=chat_id, text="Invalid sandbox confirmation.")
            return
        active = runtime.store.get_active_run(chat_id)
        if active and active.status == "waiting_approval":
            await context.bot.send_message(chat_id=chat_id, text="Approval pending — approve/reject first.")
            return
        runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        runtime.store.update_chat_state(chat_id=chat_id, sandbox_mode="danger-full-access")
        runtime.store.clear_session(chat_id=chat_id)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text="Sandbox mode set to: danger-full-access\nSession cleared. Next message starts a new session.",
        )
        return

    if data.startswith("model_select:"):
        from tgcodex.codex.models_cache import read_models_cache
        slug = data.split(":", 1)[1]
        state = runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        machine_rt = runtime.machines[state.machine_name]
        try:
            cache = await read_models_cache(machine_rt.machine)
        except Exception as exc:
            _log(f"model_select read_models_cache failed: {exc!r}")
            await context.bot.send_message(chat_id=chat_id, text=f"Failed to read models cache: {exc}")
            return
        target = next((m for m in cache.models if m.slug == slug), None)
        if target is None:
            await context.bot.send_message(chat_id=chat_id, text=f"Unknown model: {slug}")
            return
        # If model has thinking levels, show them; otherwise save immediately.
        levels = target.supported_reasoning_levels or []
        if levels:
            _log(f"model_select slug={slug!r} levels={[lv.effort for lv in levels]!r}")
            current_effort = state.thinking_level or ""
            buttons = []
            for lv in levels:
                label = f"{'✅ ' if lv.effort == current_effort else ''}{lv.effort}"
                buttons.append(InlineKeyboardButton(label, callback_data=f"thinking_select:{slug}:{lv.effort}"))
            # Add "no thinking" option
            buttons.append(InlineKeyboardButton(
                f"{'✅ ' if not current_effort else ''}default",
                callback_data=f"thinking_select:{slug}:default",
            ))
            rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
            kb = InlineKeyboardMarkup(rows)
            try:
                await q.edit_message_text(
                    text=f"Model: {slug}\nChoose thinking level:",
                    reply_markup=kb,
                )
            except Exception:
                _log("model_select edit_message_text failed; sending a new message instead")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Model: {slug}\nChoose thinking level:",
                    reply_markup=kb,
                )
        else:
            runtime.store.update_chat_state(chat_id, model=slug, thinking_level=None)
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await context.bot.send_message(chat_id=chat_id, text=f"Model set to: {slug}")
        return

    if data.startswith("thinking_select:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        _, slug, effort = parts
        effort = effort if effort != "default" else None
        _log(f"thinking_select slug={slug!r} effort={effort!r}")
        runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        runtime.store.update_chat_state(chat_id, model=slug, thinking_level=effort)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        effort_str = effort or "default"
        await context.bot.send_message(chat_id=chat_id, text=f"Model set to: {slug}, thinking: {effort_str}")
        return

    if ":" not in data:
        return
    action, run_id = data.split(":", 1)
    if action not in ("approve_once", "approve_similar", "reject"):
        return

    active = runtime.store.get_active_run(chat_id)
    if active is None or active.run_id != run_id:
        await context.bot.send_message(chat_id=chat_id, text="Stale approval; no active run.")
        return
    if not active.pending_action_json:
        await context.bot.send_message(chat_id=chat_id, text="No pending approval.")
        return
    pending = json.loads(active.pending_action_json)
    if not isinstance(pending, dict):
        await context.bot.send_message(chat_id=chat_id, text="Pending action mismatch.")
        return
    ptype = pending.get("type")
    if ptype != "approval_request":
        await context.bot.send_message(chat_id=chat_id, text="Pending action mismatch.")
        return

    run = runtime.active_runs.get(chat_id)
    if run is None:
        await context.bot.send_message(chat_id=chat_id, text="Run handle missing (restart?).")
        runtime.store.clear_active_run(chat_id=chat_id)
        return

    request_kind = pending.get("request_kind")
    rpc_id = pending.get("rpc_id")
    if request_kind not in ("commandExecution", "fileChange"):
        await context.bot.send_message(chat_id=chat_id, text="Pending approval missing request kind.")
        return
    if not isinstance(rpc_id, (int, str)):
        await context.bot.send_message(chat_id=chat_id, text="Pending approval missing rpc id.")
        return

    if action == "reject":
        decision = "decline"
        execpolicy_amendment = None
    elif action == "approve_similar":
        proposed = pending.get("proposed_execpolicy_amendment")
        if request_kind == "commandExecution" and isinstance(proposed, list) and all(isinstance(x, str) for x in proposed):
            decision = "acceptWithExecpolicyAmendment"
            execpolicy_amendment = proposed
        else:
            decision = "acceptForSession"
            execpolicy_amendment = None
    else:
        decision = "accept"
        execpolicy_amendment = None

    cmd = pending.get("command")
    cmd_s = cmd if isinstance(cmd, str) else ""
    _log(
        "approval_decision "
        f"type=approval_request action={action} decision={decision} run_id={run_id} "
        f"rpc_id={rpc_id!r} cmd_tag={_cmd_tag(cmd_s) if cmd_s else '-'} cmd={_cmd_preview(cmd_s)}"
    )
    try:
        await run.respond_approval(
            rpc_id=rpc_id,
            request_kind=request_kind,
            decision=decision,
            execpolicy_amendment=execpolicy_amendment,
        )
    except Exception as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"Failed to send approval: {exc}")
        return

    runtime.store.set_active_run(chat_id=chat_id, run_id=run_id, status="running", pending_action=None)

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await context.bot.send_message(chat_id=chat_id, text=f"Decision sent: {decision}.")
    return
