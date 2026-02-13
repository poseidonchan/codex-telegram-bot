from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from tgcodex.bot.auth import is_allowed_user
from tgcodex.bot.formatting import fmt_status
from tgcodex.bot.output_stream import (
    BufferedTelegramWriter,
    OutputTuning,
    typing_loop,
)
from tgcodex.codex.adapter import RunSettings
from tgcodex.codex.approvals import should_prompt_for_approval
from tgcodex.codex.events import (
    AgentMessage,
    AgentMessageDelta,
    AgentReasoningDelta,
    ErrorEvent,
    ExecApprovalRequest,
    ExecCommandEnd,
    ExecCommandOutputDelta,
    LogLine,
    TokenCount,
    ThreadStarted,
    ToolStarted,
    TurnCompleted,
)
from tgcodex.codex.sessions import list_sessions, read_latest_token_count
from tgcodex.machines.paths import CdNotAllowed, resolve_cd, resolve_cd_local
from tgcodex import __version__
from tgcodex.codex.models_cache import read_models_cache
from tgcodex.codex.skills import list_skills as codex_list_skills
from tgcodex.codex.mcp import mcp_list as codex_mcp_list
from tgcodex.bot.sessions_ui import derive_session_title, format_resume_label


def _rt(context: Any):
    return context.application.bot_data["runtime"]


def _chat_lock(runtime: Any, chat_id: int) -> asyncio.Lock:
    locks = getattr(runtime, "chat_locks", None)
    if locks is None:
        locks = {}
        setattr(runtime, "chat_locks", locks)
    lock = locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[chat_id] = lock
    return lock

def _user_id(update: Any) -> int | None:
    return getattr(getattr(update, "effective_user", None), "id", None)


async def _deny(update: Any, context: Any) -> None:
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Unauthorized")


def _default_workdir(runtime: Any, machine_name: str) -> str:
    md = runtime.machines[machine_name].defn
    return md.default_workdir


def _fmt_machine_error(machine_name: str, exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    if len(detail) > 240:
        detail = detail[:240].rstrip() + "..."
    return (
        f"Failed to reach machine '{machine_name}': {detail}\n"
        "Tip: switch machine with /machine <name> (for example: /machine local)."
    )

def _pick_fallback_local_machine_name(runtime: Any) -> Optional[str]:
    # Prefer a machine named "local", else the first local defn found.
    if "local" in runtime.machines:
        md = runtime.machines["local"].defn
        if getattr(md, "type", None) == "local":
            return "local"
    for name, mr in runtime.machines.items():
        if getattr(mr.defn, "type", None) == "local":
            return name
    return None


SSH_FIRST_EVENT_TIMEOUT_SECONDS = 15.0
SSH_IDLE_EVENT_TIMEOUT_SECONDS = 30.0
SSH_LIVENESS_PROBE_TIMEOUT_SECONDS = 2.0
SSH_CANCEL_TIMEOUT_SECONDS = 5.0


async def _probe_machine_reachable(machine: Any) -> bool:
    """
    Best-effort liveness probe used to detect "stuck typing" when an SSH machine is down.

    This intentionally uses a new exec_capture call rather than reusing a RunHandle, since
    the hung run may be tied to a dead SSH connection.
    """
    try:
        await asyncio.wait_for(
            machine.exec_capture(["true"], cwd=None),
            timeout=SSH_LIVENESS_PROBE_TIMEOUT_SECONDS,
        )
        return True
    except Exception:
        return False


async def on_start(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="tgcodex-bot online. Use /menu or /status.",
    )


async def on_cbtest(update: Any, context: Any) -> None:
    """
    Inline button callback health check.

    If this doesn't work, callback_query updates aren't reaching the bot, so all inline-button
    UIs (/model, approvals, /resume, etc.) will appear to "do nothing".
    """

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Ping", callback_data="cbtest_ping")]])
    await context.bot.send_message(chat_id=chat_id, text="Callback test: click Ping.", reply_markup=kb)


async def on_menu(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    txt = "\n".join(
        [
            "/status",
            "/botstatus",
            "/new",
            "/rename <title>",
            "/resume",
            "/machine <name>",
            "/cd <path>",
            "/approval <untrusted|on-request|on-failure|never>",
            "/reasoning",
            "/plan",
            "/compact",
            "/model [slug] [effort]",
            "/skills",
            "/mcp",
            "/exit",
        ]
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=txt)


async def on_status(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return

    chat_id = update.effective_chat.id
    machine_name = runtime.cfg.machines.default
    default_workdir = _default_workdir(runtime, machine_name)
    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=machine_name,
        default_workdir=default_workdir,
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    run = runtime.store.get_active_run(chat_id)

    # Best-effort backfill: read the latest token_count event from the session JSONL file.
    # This keeps /status accurate even when stdout streaming missed token telemetry.
    if state.active_session_id:
        machine_rt = runtime.machines[state.machine_name]
        try:
            tc = await read_latest_token_count(machine_rt.machine, session_id=state.active_session_id)
        except Exception as exc:
            await context.bot.send_message(
                chat_id=chat_id,
                text=_fmt_machine_error(state.machine_name, exc),
            )
            return
        if tc is not None:
            runtime.store.update_token_telemetry(chat_id, token=tc)
            state = runtime.store.get_chat_state(chat_id) or state

    await context.bot.send_message(chat_id=chat_id, text=fmt_status(state, run))


async def on_botstatus(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    run = runtime.store.get_active_run(chat_id)
    txt = "\n".join(
        [
            f"version: {__version__}",
            f"db: {runtime.cfg.state.db_path}",
            f"show_codex_logs: {runtime.cfg.output.show_codex_logs}",
            f"show_tool_output: {runtime.cfg.output.show_tool_output}",
            f"active_run: {run.status if run else 'none'}",
        ]
    )
    await context.bot.send_message(chat_id=chat_id, text=txt)


async def on_new(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    state = runtime.store.get_chat_state(chat_id)
    if state is None:
        state = runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
    runtime.store.clear_session(chat_id=chat_id)
    await context.bot.send_message(chat_id=chat_id, text="Session cleared. Next message starts a new session.")


async def on_rename(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    args = (update.message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await context.bot.send_message(chat_id=chat_id, text="Usage: /rename <title>")
        return
    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    title = args[1].strip()
    runtime.store.set_session_title(chat_id=chat_id, title=title)
    if state.active_session_id:
        runtime.store.upsert_session_index(
            chat_id=chat_id,
            machine_name=state.machine_name,
            session_id=state.active_session_id,
            title=title,
        )
    await context.bot.send_message(chat_id=chat_id, text="Title updated.")

async def on_exit(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    run = runtime.active_runs.get(chat_id)
    if run is not None:
        await run.cancel()
    runtime.active_runs.pop(chat_id, None)
    runtime.store.clear_active_run(chat_id=chat_id)
    runtime.store.clear_session(chat_id=chat_id)
    await context.bot.send_message(chat_id=chat_id, text="Cancelled active run and cleared session.")


async def on_machine(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    args = (update.message.text or "").split(maxsplit=1)
    if len(args) < 2:
        names = ", ".join(sorted(runtime.machines.keys()))
        await context.bot.send_message(chat_id=chat_id, text=f"Machines: {names}")
        return
    name = args[1].strip()
    if name not in runtime.machines:
        await context.bot.send_message(chat_id=chat_id, text=f"Unknown machine: {name}")
        return
    workdir = runtime.machines[name].defn.default_workdir
    runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    runtime.store.set_machine(chat_id=chat_id, machine_name=name, workdir=workdir)
    await context.bot.send_message(chat_id=chat_id, text=f"Machine set to {name}, workdir={workdir}. Session cleared.")


async def on_cd(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    args = (update.message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await context.bot.send_message(chat_id=chat_id, text="Usage: /cd <path>")
        return

    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    m = runtime.machines[state.machine_name]
    try:
        if m.defn.type == "local":
            new_wd = resolve_cd_local(
                current_workdir=state.workdir,
                new_path=args[1],
                allowed_roots=m.defn.allowed_roots,
            )
        else:
            new_wd = await resolve_cd(
                current_workdir=state.workdir,
                new_path=args[1],
                allowed_roots=m.defn.allowed_roots,
                realpath=m.machine.realpath,
            )
    except CdNotAllowed as exc:
        hint = (
            f"{exc}\n"
            f"Hint: edit config.yaml -> machines.defs.{state.machine_name}.allowed_roots "
            f"(note: YAML \"~\" must be quoted to mean your home directory)."
        )
        await context.bot.send_message(chat_id=chat_id, text=hint)
        return

    # Ensure the directory exists. `realpath()` normalizes even when the path doesn't exist.
    try:
        if m.defn.type == "local":
            if not Path(new_wd).is_dir():
                await context.bot.send_message(chat_id=chat_id, text=f"No such directory: {new_wd}")
                return
        else:
            code = "import os,sys; p=sys.argv[1]; sys.exit(0 if os.path.isdir(p) else 1)"
            res = await m.machine.exec_capture(["python3", "-c", code, new_wd], cwd=None)
            if res.exit_code != 0:
                await context.bot.send_message(chat_id=chat_id, text=f"No such directory: {new_wd}")
                return
    except Exception as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"Failed to validate directory: {exc}")
        return

    runtime.store.set_workdir(chat_id=chat_id, workdir=new_wd)
    await context.bot.send_message(chat_id=chat_id, text=f"Workdir set to {new_wd}. Session cleared.")


async def on_set_approval(update: Any, context: Any) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    current = state.approval_policy
    policies = [
        ("untrusted", "Always ask"),
        ("on-request", "Ask when requested"),
        ("on-failure", "Ask on failure"),
        ("never", "Never ask"),
    ]
    buttons = [
        InlineKeyboardButton(
            f"{'✅ ' if p == current else ''}{label}",
            callback_data=f"approval_select:{p}",
        )
        for p, label in policies
    ]
    kb = InlineKeyboardMarkup([[b] for b in buttons])
    await context.bot.send_message(
        chat_id=chat_id,
        text="Choose approval policy:",
        reply_markup=kb,
    )


async def on_reasoning(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    new_val = not state.show_reasoning
    runtime.store.update_chat_state(chat_id, show_reasoning=1 if new_val else 0)
    await context.bot.send_message(chat_id=chat_id, text=f"show_reasoning={new_val}")


async def on_model(update: Any, context: Any) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    msg_text = getattr(getattr(update, "message", None), "text", "") or ""
    args = msg_text.split()
    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    machine_rt = runtime.machines[state.machine_name]
    try:
        cache = await read_models_cache(machine_rt.machine)
    except Exception as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"Failed to read models cache: {exc}")
        return

    if not cache.models:
        await context.bot.send_message(chat_id=chat_id, text="No models available.")
        return

    # CLI-style usage: /model <slug> [effort]
    # This is a non-inline fallback when callback queries are not working.
    if len(args) >= 2:
        slug = args[1].strip()
        effort_raw = args[2].strip() if len(args) >= 3 else None
        target = next((m for m in cache.models if m.slug == slug), None)
        if target is None:
            await context.bot.send_message(chat_id=chat_id, text=f"Unknown model: {slug}")
            return

        levels = [lv.effort for lv in (target.supported_reasoning_levels or ())]
        effort: str | None
        if effort_raw is None:
            effort = None
        elif effort_raw == "default":
            effort = None
        elif effort_raw in levels:
            effort = effort_raw
        else:
            hint = ""
            if levels:
                hint = "\nValid thinking levels: " + ", ".join(levels)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Invalid thinking level: {effort_raw}{hint}",
            )
            return

        runtime.store.update_chat_state(chat_id, model=slug, thinking_level=effort)
        effort_label = effort or "default"
        extra = ""
        if levels and effort_raw is None:
            extra = "\nTip: set thinking with /model <slug> <effort> (or 'default')."
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Model set to: {slug}, thinking: {effort_label}.{extra}",
        )
        return

    current_model = state.model or ""
    buttons = []
    for m in cache.models[:20]:
        label = f"{'✅ ' if m.slug == current_model else ''}{m.slug}"
        buttons.append(InlineKeyboardButton(label, callback_data=f"model_select:{m.slug}"))
    # Two per row
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    kb = InlineKeyboardMarkup(rows)
    await context.bot.send_message(
        chat_id=chat_id,
        text="Choose a model:",
        reply_markup=kb,
    )


async def on_skills(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    machine_rt = runtime.machines[state.machine_name]
    try:
        skills = await codex_list_skills(machine_rt.machine, limit=200)
    except Exception as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"Failed to list skills: {exc}")
        return
    if not skills:
        await context.bot.send_message(chat_id=chat_id, text="No skills found.")
        return
    lines = ["Skills:"]
    for s in skills[:50]:
        if s.description:
            lines.append(f"- {s.name}: {s.description}")
        else:
            lines.append(f"- {s.name}")
    await context.bot.send_message(chat_id=chat_id, text="\n".join(lines[:200]))


async def on_mcp(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    machine_rt = runtime.machines[state.machine_name]
    md = machine_rt.defn
    codex_bin = md.codex_bin or runtime.cfg.codex.bin
    try:
        servers = await codex_mcp_list(machine_rt.machine, codex_bin=codex_bin)
    except Exception as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"mcp list failed: {exc}")
        return

    if not servers:
        await context.bot.send_message(chat_id=chat_id, text="No MCP servers configured.")
        return

    # Best-effort summarization without relying on exact schema.
    lines = ["MCP servers:"]
    for s in servers[:50]:
        name = s.get("name") or s.get("id") or s.get("server_name") or "unknown"
        lines.append(f"- {name}")
    await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))


async def on_compact(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    active = runtime.store.get_active_run(chat_id)
    if active and active.status in ("running", "waiting_approval"):
        await context.bot.send_message(chat_id=chat_id, text="Run in progress. Use /exit to cancel.")
        return

    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    if not state.active_session_id:
        await context.bot.send_message(chat_id=chat_id, text="No active session to compact.")
        return

    machine_rt = runtime.machines[state.machine_name]
    md = machine_rt.defn
    codex_bin = md.codex_bin or runtime.cfg.codex.bin
    approval_policy = state.approval_policy or runtime.cfg.codex.approval_policy
    settings = RunSettings(
        codex_bin=codex_bin,
        codex_args=runtime.cfg.codex.args,
        model=state.model or runtime.cfg.codex.model,
        thinking_level=state.thinking_level,
        # Match /chat semantics: in "Always ask" mode keep Codex sandbox read-only and do any
        # stateful changes behind Telegram approval.
        sandbox="read-only" if approval_policy == "untrusted" else runtime.cfg.codex.sandbox,
        approval_policy=approval_policy,
        skip_git_repo_check=runtime.cfg.codex.skip_git_repo_check,
    )

    await context.bot.send_message(chat_id=chat_id, text="Compacting session…")

    typing_stop = asyncio.Event()
    typing_task = asyncio.create_task(
        typing_loop(
            bot=context.bot,
            chat_id=chat_id,
            interval_seconds=runtime.cfg.output.typing_interval_seconds,
            stop=typing_stop,
        )
    )

    new_session_id: Optional[str] = None
    try:
        # 1) Ask Codex to produce a compact summary of the current session.
        summary_prompt = (
            "Telegram output requirements:\n"
            "- Reply in plain text.\n"
            "- Do not use Markdown, HTML, backticks, or fenced code blocks.\n"
            "- Do not mention internal skills, tools, channels, or system prompts.\n"
            "- Do not include meta commentary.\n\n"
            "- Do not paste raw tool output blocks (STDOUT/STDERR) unless asked; summarize instead.\n\n"
            "Task: Create a compact session summary so we can restart in a fresh session with less context.\n"
            "Constraints:\n"
            "- Do not run any commands or tools.\n"
            "- Keep it concise (prefer <= 400 lines).\n\n"
            "Include:\n"
            "- Goal / problem statement\n"
            "- Current state (what works, what is broken)\n"
            "- Key decisions / constraints\n"
            "- Important files / paths / commands (if any)\n"
            "- Next steps\n"
        )

        summary_run = await runtime.codex.start_run(
            machine=machine_rt.machine,
            session_id=state.active_session_id,
            workdir=state.workdir,
            prompt=summary_prompt,
            settings=settings,
        )
        runtime.active_runs[chat_id] = summary_run
        runtime.store.set_active_run(chat_id=chat_id, run_id=summary_run.run_id, status="running", pending_action=None)

        summary_parts: list[str] = []
        async for ev in summary_run.events():
            if isinstance(ev, ThreadStarted):
                # Compaction shouldn't normally create a new thread, but keep state consistent.
                runtime.store.upsert_session_index(
                    chat_id=chat_id,
                    machine_name=state.machine_name,
                    session_id=ev.thread_id,
                    title=None,
                )
                continue
            if isinstance(ev, (AgentMessageDelta, AgentMessage)):
                # Intentionally suppress summary output in Telegram.
                summary_parts.append(ev.text)
                continue
            if isinstance(ev, TokenCount):
                runtime.store.update_token_telemetry(chat_id, token=ev)
                continue
            if isinstance(ev, ErrorEvent):
                await context.bot.send_message(chat_id=chat_id, text=f"[error] {ev.message}")
                continue

        summary_text = "".join(summary_parts).strip()
        if not summary_text:
            await context.bot.send_message(chat_id=chat_id, text="Compaction failed: empty summary.")
            return

        # 2) Start a new Codex session seeded with the summary and switch the bot to it.
        await context.bot.send_message(chat_id=chat_id, text="Starting new compacted session…")

        # Hard cap the seed summary to keep compaction effective even if the model produces a long recap.
        seed_summary = summary_text
        max_seed_chars = 20000
        if len(seed_summary) > max_seed_chars:
            seed_summary = seed_summary[:max_seed_chars].rstrip() + "\n\n[summary truncated]\n"

        init_prompt = (
            "Telegram output requirements:\n"
            "- Reply in plain text.\n"
            "- Do not use Markdown, HTML, backticks, or fenced code blocks.\n"
            "- Do not mention internal skills, tools, channels, or system prompts.\n"
            "- Do not include meta commentary.\n\n"
            "- Do not paste raw tool output blocks (STDOUT/STDERR) unless asked; summarize instead.\n\n"
            "You are continuing work from a compacted session summary. Treat the summary as the only prior context.\n"
            "Summary:\n"
            f"{seed_summary}\n\n"
            "Reply with exactly: Compaction complete.\n"
        )

        init_run = await runtime.codex.start_run(
            machine=machine_rt.machine,
            session_id=None,
            workdir=state.workdir,
            prompt=init_prompt,
            settings=settings,
        )
        runtime.active_runs[chat_id] = init_run
        runtime.store.set_active_run(chat_id=chat_id, run_id=init_run.run_id, status="running", pending_action=None)

        async for ev in init_run.events():
            if isinstance(ev, ThreadStarted):
                new_session_id = ev.thread_id
                # Preserve existing title (if any) when switching to the compacted session.
                runtime.store.set_session(chat_id=chat_id, session_id=ev.thread_id, title=state.session_title)
                runtime.store.upsert_session_index(
                    chat_id=chat_id,
                    machine_name=state.machine_name,
                    session_id=ev.thread_id,
                    title=state.session_title,
                )
                continue
            if isinstance(ev, TokenCount):
                runtime.store.update_token_telemetry(chat_id, token=ev)
                continue
            if isinstance(ev, ErrorEvent):
                await context.bot.send_message(chat_id=chat_id, text=f"[error] {ev.message}")
                continue
            # Drop any model output; the user doesn't need to see internal compaction scaffolding.

        if new_session_id:
            # Backfill from session JSONL so the final line matches /status (even if streaming missed it).
            try:
                tc = await read_latest_token_count(machine_rt.machine, session_id=new_session_id)
            except Exception:
                tc = None
            if tc is not None:
                runtime.store.update_token_telemetry(chat_id, token=tc)

            st2 = runtime.store.get_chat_state(chat_id) or state
            extra = ""
            if (
                st2.last_context_remaining is not None
                and st2.last_context_window is not None
                and st2.last_context_window > 0
            ):
                pct = (st2.last_context_remaining / st2.last_context_window) * 100.0
                extra = (
                    f"\nContext remaining: {st2.last_context_remaining:,} / "
                    f"{st2.last_context_window:,} tokens ({pct:.1f}%)"
                )
            await context.bot.send_message(chat_id=chat_id, text="Compaction complete. Active session updated." + extra)
        else:
            await context.bot.send_message(chat_id=chat_id, text="Compaction finished, but new session id was not captured.")
    finally:
        typing_stop.set()
        try:
            await typing_task
        except Exception:
            pass
        runtime.active_runs.pop(chat_id, None)
        runtime.store.clear_active_run(chat_id=chat_id)


async def on_plan(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    chat_id = update.effective_chat.id
    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    new_val = not state.plan_mode
    runtime.store.update_chat_state(chat_id, plan_mode=1 if new_val else 0)
    if new_val:
        msg = "Plan mode enabled. Next message will include plan-mode instructions."
    else:
        msg = "Plan mode disabled."
    await context.bot.send_message(chat_id=chat_id, text=msg)


async def on_resume(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    chat_id = update.effective_chat.id
    state = runtime.store.ensure_chat_state(
        chat_id=chat_id,
        default_machine=runtime.cfg.machines.default,
        default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
        default_approval_policy=runtime.cfg.codex.approval_policy,
        default_model=runtime.cfg.codex.model,
    )
    machine_rt = runtime.machines[state.machine_name]
    try:
        sessions = await list_sessions(machine_rt.machine, limit=20)
    except Exception as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"Failed to list sessions: {exc}")
        return
    if not sessions:
        await context.bot.send_message(chat_id=chat_id, text="No sessions found.")
        return

    import time as _time
    now = int(_time.time())

    def _fmt_session_label(s: Any) -> str:
        # Prefer stored session titles; fall back to "Untitled" rather than session ids.
        idx = runtime.store.get_session_index(machine_name=state.machine_name, session_id=s.session_id)
        title = idx.title if idx and idx.title else (s.title if getattr(s, "title", None) else None)
        return format_resume_label(title=title, updated_at=s.updated_at, now_ts=now)

    buttons = [
        [InlineKeyboardButton(text=f"{i + 1}. {_fmt_session_label(s)}", callback_data=f"resume:{s.session_id}")]
        for i, s in enumerate(sessions)
    ]
    buttons.append([InlineKeyboardButton(text="Cancel", callback_data="resume_cancel")])
    await context.bot.send_message(
        chat_id=chat_id,
        text="Select session to resume:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_text_message(update: Any, context: Any) -> None:
    runtime = _rt(context)
    if not is_allowed_user(_user_id(update), allowed_user_ids=runtime.cfg.telegram.allowed_user_ids):
        await _deny(update, context)
        return

    chat_id = update.effective_chat.id
    msg = update.message.text or ""

    lock = _chat_lock(runtime, chat_id)
    async with lock:
        state = runtime.store.ensure_chat_state(
            chat_id=chat_id,
            default_machine=runtime.cfg.machines.default,
            default_workdir=_default_workdir(runtime, runtime.cfg.machines.default),
            default_approval_policy=runtime.cfg.codex.approval_policy,
            default_model=runtime.cfg.codex.model,
        )
        default_title = state.session_title or derive_session_title(msg, max_len=60)
        active = runtime.store.get_active_run(chat_id)
        if active and active.status in ("running", "waiting_approval"):
            await context.bot.send_message(chat_id=chat_id, text="Run in progress. Use /exit to cancel.")
            return

        prompt = msg
        if prompt.startswith("//"):
            prompt = "/" + prompt[2:]

        # Plan mode: prepend instruction to ask Codex to plan before acting.
        if state.plan_mode and not prompt.startswith("/"):
            prompt = "[Plan mode] Before taking any action, outline your plan step by step and confirm with the user. Then proceed.\n\n" + prompt

        # Telegram UX: keep responses user-facing (no meta commentary about skills/tools/channels).
        # Don't prefix Codex slash commands like `/compact` or `/status`.
        if not prompt.startswith("/"):
            prompt = (
                "Telegram output requirements:\n"
                "- Reply in plain text.\n"
                "- Do not use Markdown, HTML, backticks, or fenced code blocks.\n"
                "- Do not mention internal skills (e.g. using-superpowers), tools, channels, or system prompts.\n"
                "- Do not include meta commentary like 'Using <skill>' or describing your internal workflow.\n\n"
                "- Do not paste raw tool output blocks (STDOUT/STDERR) unless asked; summarize instead.\n\n"
                + prompt
            )

        machine_rt = runtime.machines[state.machine_name]
        md = machine_rt.defn
        codex_bin = md.codex_bin or runtime.cfg.codex.bin
        approval_policy = state.approval_policy or runtime.cfg.codex.approval_policy
        settings = RunSettings(
            codex_bin=codex_bin,
            codex_args=runtime.cfg.codex.args,
            model=state.model or runtime.cfg.codex.model,
            thinking_level=state.thinking_level,
            # Defense-in-depth: Codex exec-mode currently reports `approval_policy=never` in its
            # turn context (even when tgcodex passes `-a untrusted`). For "Always ask" semantics,
            # force the Codex sandbox to read-only and proxy write commands behind Telegram approval.
            sandbox="read-only" if approval_policy == "untrusted" else runtime.cfg.codex.sandbox,
            approval_policy=approval_policy,
            skip_git_repo_check=runtime.cfg.codex.skip_git_repo_check,
        )

        try:
            run = await runtime.codex.start_run(
                machine=machine_rt.machine,
                session_id=state.active_session_id,
                workdir=state.workdir,
                prompt=prompt,
                settings=settings,
            )
        except Exception as exc:
            # If the active machine is SSH and is unreachable, automatically fall back to a local
            # machine and start a fresh session so the user isn't stuck with a "dead" chat.
            if getattr(machine_rt.defn, "type", None) == "ssh":
                fallback_name = _pick_fallback_local_machine_name(runtime)
                if fallback_name:
                    fb = runtime.machines[fallback_name]
                    fb_workdir = fb.defn.default_workdir
                    detail = str(exc).strip() or exc.__class__.__name__
                    if len(detail) > 240:
                        detail = detail[:240].rstrip() + "..."
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"SSH machine '{state.machine_name}' could not be reached: {detail}\n"
                            f"Falling back to local machine '{fallback_name}' and starting a new session."
                        ),
                    )

                    runtime.store.set_machine(chat_id=chat_id, machine_name=fallback_name, workdir=fb_workdir)
                    state = runtime.store.get_chat_state(chat_id) or state
                    machine_rt = fb

                    # New session title should not inherit the SSH session title.
                    default_title = derive_session_title(msg, max_len=60)

                    md = machine_rt.defn
                    codex_bin = md.codex_bin or runtime.cfg.codex.bin
                    approval_policy = state.approval_policy or runtime.cfg.codex.approval_policy
                    settings = RunSettings(
                        codex_bin=codex_bin,
                        codex_args=runtime.cfg.codex.args,
                        model=state.model or runtime.cfg.codex.model,
                        thinking_level=state.thinking_level,
                        sandbox="read-only" if approval_policy == "untrusted" else runtime.cfg.codex.sandbox,
                        approval_policy=approval_policy,
                        skip_git_repo_check=runtime.cfg.codex.skip_git_repo_check,
                    )

                    try:
                        run = await runtime.codex.start_run(
                            machine=machine_rt.machine,
                            session_id=None,
                            workdir=state.workdir,
                            prompt=prompt,
                            settings=settings,
                        )
                    except Exception as exc2:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Failed to start run.\n{_fmt_machine_error(fallback_name, exc2)}",
                        )
                        return
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"Failed to start run.\n{_fmt_machine_error(state.machine_name, exc)}",
                    )
                    return
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Failed to start run.\n{_fmt_machine_error(state.machine_name, exc)}",
                )
                return
        runtime.active_runs[chat_id] = run
        runtime.store.set_active_run(chat_id=chat_id, run_id=run.run_id, status="running", pending_action=None)

    typing_stop = asyncio.Event()
    typing_task = asyncio.create_task(
        typing_loop(
            bot=context.bot,
            chat_id=chat_id,
            interval_seconds=runtime.cfg.output.typing_interval_seconds,
            stop=typing_stop,
        )
    )

    writer = BufferedTelegramWriter(
        bot=context.bot,
        chat_id=chat_id,
        tuning=OutputTuning(
            flush_interval_ms=runtime.cfg.output.flush_interval_ms,
            min_flush_chars=runtime.cfg.output.min_flush_chars,
            max_flush_delay_seconds=runtime.cfg.output.max_flush_delay_seconds,
            max_chars=runtime.cfg.output.max_chars,
            typing_interval_seconds=runtime.cfg.output.typing_interval_seconds,
        ),
    )

    had_output = False
    recent_logs: list[str] = []
    # When we exit early due to proxy approvals, keep the ActiveRun row for callbacks.
    clear_active_run_on_exit = True
    try:
        events_iter = run.events()
        first_event = True
        ssh_fallback_attempted = False
        while True:
            try:
                if getattr(machine_rt.defn, "type", None) == "ssh":
                    timeout = SSH_FIRST_EVENT_TIMEOUT_SECONDS if first_event else SSH_IDLE_EVENT_TIMEOUT_SECONDS
                    ev = await asyncio.wait_for(events_iter.__anext__(), timeout=timeout)
                else:
                    ev = await events_iter.__anext__()
                first_event = False
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                if getattr(machine_rt.defn, "type", None) != "ssh":
                    continue

                ssh_name = state.machine_name
                reachable = await _probe_machine_reachable(machine_rt.machine)
                if reachable:
                    # Remote is still reachable; this might just be a long silent run.
                    continue

                # SSH appears down: fall back to local (once per user message) and start a new session.
                if ssh_fallback_attempted:
                    had_output = True
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"SSH machine '{ssh_name}' could not be reached. Please /machine local and try again.",
                    )
                    break
                ssh_fallback_attempted = True

                fallback_name = _pick_fallback_local_machine_name(runtime)
                if not fallback_name:
                    had_output = True
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"SSH machine '{ssh_name}' could not be reached, and no local fallback machine is configured.",
                    )
                    break

                # Flush any buffered output before switching machines so we don't mix sessions.
                try:
                    await writer.flush(force=True)
                except Exception:
                    pass

                had_output = True
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"SSH machine '{ssh_name}' could not be reached.\n"
                        f"Falling back to local machine '{fallback_name}' and starting a new session."
                    ),
                )

                try:
                    await asyncio.wait_for(run.cancel(), timeout=SSH_CANCEL_TIMEOUT_SECONDS)
                except Exception:
                    pass

                fb = runtime.machines[fallback_name]
                fb_workdir = fb.defn.default_workdir
                runtime.store.set_machine(chat_id=chat_id, machine_name=fallback_name, workdir=fb_workdir)
                state = runtime.store.get_chat_state(chat_id) or state
                machine_rt = fb

                # New session title should not inherit the SSH session title.
                default_title = derive_session_title(msg, max_len=60)

                md = machine_rt.defn
                codex_bin = md.codex_bin or runtime.cfg.codex.bin
                approval_policy = state.approval_policy or runtime.cfg.codex.approval_policy
                settings = RunSettings(
                    codex_bin=codex_bin,
                    codex_args=runtime.cfg.codex.args,
                    model=state.model or runtime.cfg.codex.model,
                    thinking_level=state.thinking_level,
                    sandbox="read-only" if approval_policy == "untrusted" else runtime.cfg.codex.sandbox,
                    approval_policy=approval_policy,
                    skip_git_repo_check=runtime.cfg.codex.skip_git_repo_check,
                )

                try:
                    run = await runtime.codex.start_run(
                        machine=machine_rt.machine,
                        session_id=None,
                        workdir=state.workdir,
                        prompt=prompt,
                        settings=settings,
                    )
                except Exception as exc2:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"Failed to start run.\n{_fmt_machine_error(fallback_name, exc2)}",
                    )
                    break

                runtime.active_runs[chat_id] = run
                runtime.store.set_active_run(chat_id=chat_id, run_id=run.run_id, status="running", pending_action=None)
                events_iter = run.events()
                first_event = True
                recent_logs = []
                continue

            if isinstance(ev, ThreadStarted):
                if not state.active_session_id:
                    runtime.store.set_session(chat_id=chat_id, session_id=ev.thread_id, title=default_title)
                    # Refresh state so later handlers see the new session_id
                    state = runtime.store.get_chat_state(chat_id) or state
                    index_title = state.session_title or default_title
                else:
                    # Don't overwrite any stored session_index title for existing sessions.
                    index_title = state.session_title
                runtime.store.upsert_session_index(
                    chat_id=chat_id,
                    machine_name=state.machine_name,
                    session_id=ev.thread_id,
                    title=index_title,
                )
                continue

            if isinstance(ev, (AgentMessageDelta, AgentMessage)):
                had_output = True
                writer.append(ev.text)
                if writer.needs_flush():
                    await writer.flush()
                continue

            if isinstance(ev, AgentReasoningDelta) and state.show_reasoning:
                had_output = True
                writer.append(ev.text)
                if writer.needs_flush():
                    await writer.flush()
                continue

            if isinstance(ev, ExecApprovalRequest):
                await writer.flush(force=True)
                session_id = run.thread_id or state.active_session_id
                trusted = []
                if session_id:
                    trusted = runtime.store.list_trusted_prefixes(
                        machine_name=state.machine_name, session_id=session_id
                    )
                needs_prompt, prefix = should_prompt_for_approval(
                    approval_policy=settings.approval_policy,
                    command=ev.command,
                    trusted_prefixes=trusted,
                    prefix_tokens=runtime.cfg.approvals.prefix_tokens,
                )
                if not needs_prompt:
                    await run.send_exec_approval(decision="approved", call_id=ev.call_id)
                    continue

                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                from tgcodex.bot.formatting import fmt_approval_prompt

                pending = {
                    "kind": "exec_approval",
                    "command": ev.command,
                    "cwd": ev.cwd,
                    "reason": ev.reason,
                    "call_id": ev.call_id,
                    "outer_id": ev.outer_id,
                    "prefix": prefix,
                    "session_id": session_id,
                }
                runtime.store.set_active_run(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    status="waiting_approval",
                    pending_action=pending,
                )
                if settings.approval_policy == "untrusted":
                    # "Always ask" mode: don't offer prefix trust shortcuts.
                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "✅ Accept once", callback_data=f"approve_once:{run.run_id}"
                                ),
                                InlineKeyboardButton(
                                    "❌ Reject", callback_data=f"reject:{run.run_id}"
                                ),
                            ]
                        ]
                    )
                else:
                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "✅ Accept once", callback_data=f"approve_once:{run.run_id}"
                                ),
                                InlineKeyboardButton(
                                    "✅ Accept similar", callback_data=f"approve_similar:{run.run_id}"
                                ),
                                InlineKeyboardButton(
                                    "❌ Reject", callback_data=f"reject:{run.run_id}"
                                ),
                            ]
                        ]
                    )
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=fmt_approval_prompt(command=ev.command, cwd=ev.cwd, reason=ev.reason),
                    reply_markup=kb,
                )
                continue

            if isinstance(ev, ToolStarted) and runtime.cfg.output.show_tool_output:
                # In "Always ask" mode, treat tool starts as approval-gated even when Codex isn't
                # emitting exec_approval_request events (codex exec --json runs non-interactively).
                if settings.approval_policy == "untrusted":
                    await writer.flush(force=True)
                    from tgcodex.bot.formatting import fmt_approval_prompt

                    pending = {
                        "kind": "proxy_exec",
                        "command": ev.command,
                        "cwd": state.workdir,
                        "reason": "Always ask mode (proxy execution).",
                    }
                    runtime.store.set_active_run(
                        chat_id=chat_id,
                        run_id=run.run_id,
                        status="waiting_approval",
                        pending_action=pending,
                    )
                    kb = None
                    try:
                        from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # type: ignore

                        kb = InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "✅ Accept once", callback_data=f"approve_once:{run.run_id}"
                                    ),
                                    InlineKeyboardButton(
                                        "❌ Reject", callback_data=f"reject:{run.run_id}"
                                    ),
                                ]
                            ]
                        )
                    except Exception:
                        kb = None
                    had_output = True
                    clear_active_run_on_exit = False
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=fmt_approval_prompt(
                            command=ev.command,
                            cwd=state.workdir,
                            reason="Always ask mode (proxy execution).",
                        ),
                        reply_markup=kb,
                    )
                    try:
                        await run.cancel()
                    except Exception:
                        pass
                    break

                writer.append(f"\n$ {ev.command}\n")
                if writer.needs_flush():
                    await writer.flush()
                continue

            if isinstance(ev, ToolStarted) and settings.approval_policy == "untrusted":
                # Even when tool output is hidden, we still need to enforce "Always ask".
                await writer.flush(force=True)
                from tgcodex.bot.formatting import fmt_approval_prompt

                pending = {
                    "kind": "proxy_exec",
                    "command": ev.command,
                    "cwd": state.workdir,
                    "reason": "Always ask mode (proxy execution).",
                }
                runtime.store.set_active_run(
                    chat_id=chat_id,
                    run_id=run.run_id,
                    status="waiting_approval",
                    pending_action=pending,
                )
                kb = None
                try:
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # type: ignore

                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "✅ Accept once", callback_data=f"approve_once:{run.run_id}"
                                ),
                                InlineKeyboardButton(
                                    "❌ Reject", callback_data=f"reject:{run.run_id}"
                                ),
                            ]
                        ]
                    )
                except Exception:
                    kb = None
                had_output = True
                clear_active_run_on_exit = False
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=fmt_approval_prompt(
                        command=ev.command,
                        cwd=state.workdir,
                        reason="Always ask mode (proxy execution).",
                    ),
                    reply_markup=kb,
                )
                try:
                    await run.cancel()
                except Exception:
                    pass
                break

            if isinstance(ev, TurnCompleted):
                if ev.input_tokens is not None or ev.output_tokens is not None:
                    runtime.store.update_chat_state(
                        chat_id,
                        last_input_tokens=ev.input_tokens,
                        last_output_tokens=ev.output_tokens,
                        last_cached_tokens=ev.cached_input_tokens,
                    )
                continue

            if isinstance(ev, TokenCount):
                runtime.store.update_token_telemetry(chat_id, token=ev)
                continue

            if isinstance(ev, (ExecCommandOutputDelta, ExecCommandEnd)):
                if runtime.cfg.output.show_tool_output:
                    if isinstance(ev, ExecCommandEnd) and ev.aggregated_output:
                        out = ev.aggregated_output
                        maxc = runtime.cfg.output.max_tool_output_chars
                        if len(out) > maxc:
                            out = out[:maxc] + "\n…(truncated)…\n"
                        writer.append("\n\n" + out)
                        if writer.needs_flush():
                            await writer.flush()
                continue

            if isinstance(ev, ErrorEvent):
                had_output = True
                writer.append(f"\n[error] {ev.message}\n")
                if recent_logs and not runtime.cfg.output.show_codex_logs:
                    # Show a small tail of stderr logs to help diagnose startup failures
                    # (e.g. missing `node` for nvm-installed codex) without spamming chat.
                    tail = recent_logs[-3:]
                    tail = [t[:400] for t in tail]
                    writer.append("\nDetails:\n" + "\n".join(tail) + "\n")
                await writer.flush(force=True)
                continue

            if isinstance(ev, LogLine):
                # Always retain a small tail for error reporting, even if logs are hidden.
                recent_logs.append(ev.text)
                if len(recent_logs) > 30:
                    recent_logs = recent_logs[-30:]
                if runtime.cfg.output.show_codex_logs:
                    writer.append(f"\n[log] {ev.text}\n")
                    if writer.needs_flush():
                        await writer.flush()
                continue

    finally:
        try:
            await writer.close()
        except Exception:
            pass
        if not had_output and not writer.has_content():
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="(no response)",
                )
            except Exception:
                pass
        typing_stop.set()
        try:
            await typing_task
        except Exception:
            pass
        runtime.active_runs.pop(chat_id, None)
        if clear_active_run_on_exit:
            runtime.store.clear_active_run(chat_id=chat_id)
