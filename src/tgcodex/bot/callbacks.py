from __future__ import annotations

import json
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

    if data.startswith("approval_select:"):
        policy = data.split(":", 1)[1]
        if policy not in ("untrusted", "on-request", "on-failure", "never"):
            await context.bot.send_message(chat_id=chat_id, text="Invalid policy.")
            return
        runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        runtime.store.update_chat_state(chat_id, approval_policy=policy)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(chat_id=chat_id, text=f"Approval policy set to: {policy}")
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
    kind = pending.get("kind")

    if kind == "exec_approval":
        run = runtime.active_runs.get(chat_id)
        if run is None:
            await context.bot.send_message(chat_id=chat_id, text="Run handle missing (restart?).")
            runtime.store.clear_active_run(chat_id=chat_id)
            return

        if action == "reject":
            decision = "denied"
        else:
            decision = "approved"

        if action == "approve_similar":
            session_id = pending.get("session_id")
            prefix = pending.get("prefix")
            if isinstance(session_id, str) and isinstance(prefix, str) and session_id:
                state = runtime.store.get_chat_state(chat_id)
                runtime.store.add_trusted_prefix(
                    machine_name=state.machine_name if state else runtime.cfg.machines.default,
                    session_id=session_id,
                    prefix=prefix,
                )

        runtime.store.set_active_run(chat_id=chat_id, run_id=run_id, status="running", pending_action=None)
        try:
            call_id = pending.get("call_id")
            await run.send_exec_approval(
                decision=decision,
                call_id=call_id if isinstance(call_id, str) else None,
            )
        except Exception as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"Failed to send approval: {exc}")
            return

        # Remove keyboard to reduce double-clicks.
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        await context.bot.send_message(chat_id=chat_id, text=f"Decision sent: {decision}.")
        return

    if kind == "proxy_exec":
        # Proxy approvals are tgcodex-enforced when Codex exec-mode bypasses approvals. We run the
        # approved command ourselves and feed the output back into the session as the next message.
        cmd = pending.get("command")
        cwd = pending.get("cwd")
        if not isinstance(cmd, str) or not cmd.strip():
            await context.bot.send_message(chat_id=chat_id, text="Pending action missing command.")
            runtime.store.clear_active_run(chat_id=chat_id)
            return
        if not isinstance(cwd, str) or not cwd.strip():
            state = runtime.store.get_chat_state(chat_id)
            cwd = state.workdir if state else None

        if action == "reject":
            runtime.store.clear_active_run(chat_id=chat_id)
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await context.bot.send_message(chat_id=chat_id, text="Rejected.")
            return

        # Approve (once/similar behave the same in Always-ask mode).
        state = runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=runtime.machines[runtime.cfg.machines.default].defn.default_workdir,
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        machine_rt = runtime.machines[state.machine_name]

        # Clear pending approval now to avoid deadlocks if execution or follow-up fails.
        runtime.store.clear_active_run(chat_id=chat_id)

        try:
            res = await machine_rt.machine.exec_capture(["bash", "-lc", cmd], cwd=cwd)
        except Exception as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"Command failed to start: {exc}")
            return

        # Keep chat clean: don't spam stdout/stderr for proxy-approved commands.
        # If something goes wrong, send only the exit code; detailed output is still fed back
        # into Codex below so it can continue the task.
        if res.exit_code != 0:
            await context.bot.send_message(chat_id=chat_id, text=f"Command failed (exit code: {res.exit_code}).")

        # Remove keyboard to reduce double-clicks.
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        # Feed the output back into Codex so it can continue the original task.
        from tgcodex.bot.commands import on_text_message

        class _SyntheticMsg:
            def __init__(self, text: str) -> None:
                self.text = text

        class _SyntheticUpdate:
            effective_chat = update.effective_chat
            effective_user = update.effective_user
            # Cap tool output fed back into Codex to avoid blowing up the prompt/context window.
            _max_feed = max(int(getattr(runtime.cfg.output, "max_tool_output_chars", 0) or 0), 20000)
            _stdout = _truncate_text(res.stdout, max_chars=_max_feed)
            _stderr = _truncate_text(res.stderr, max_chars=_max_feed)
            message = _SyntheticMsg(
                "[Tool output]\n"
                f"Command: {cmd}\n"
                f"Exit code: {res.exit_code}\n"
                f"STDOUT:\n{_stdout}\n"
                f"STDERR:\n{_stderr}\n"
                "Continue."
            )

        await on_text_message(_SyntheticUpdate(), context)
        return

    await context.bot.send_message(chat_id=chat_id, text="Pending action mismatch.")
