from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from tgcodex.config import Config
from tgcodex.codex.adapter import CodexCLIAdapter
from tgcodex.machines.base import Machine
from tgcodex.machines.local import LocalMachine
from tgcodex.machines.ssh import SSHMachine
from tgcodex.state.store import Store


def _require_ptb() -> None:  # pragma: no cover
    try:
        import telegram  # noqa: F401
        import telegram.ext  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "python-telegram-bot is required to run the bot. Install project deps (see README.md)."
        ) from exc


@dataclass
class MachineRuntime:
    machine: Machine
    defn: Any


@dataclass
class BotRuntime:
    cfg: Config
    store: Store
    machines: dict[str, MachineRuntime]
    codex: CodexCLIAdapter
    active_runs: dict[int, Any]  # chat_id -> CodexRun (in-memory)


def default_bot_command_specs() -> tuple[tuple[str, str], ...]:
    """
    Commands shown in Telegram's "Menu" button and slash command suggestions.

    Telegram caches these per bot. Setting them on startup ensures stale/removed
    commands (e.g. from older versions) disappear without manual BotFather edits.
    """

    return (
        ("start", "Health check"),
        ("menu", "Show commands"),
        ("status", "Show current session/machine"),
        ("botstatus", "Show bot info"),
        ("new", "Clear session (start fresh)"),
        ("rename", "Rename current session"),
        ("resume", "Resume a past session"),
        ("machine", "Switch machine"),
        ("cd", "Change working directory"),
        ("approval", "Set approval policy"),
        ("reasoning", "Toggle reasoning output"),
        ("plan", "Toggle plan mode"),
        ("compact", "Compact current session"),
        ("model", "Pick model (and thinking level)"),
        ("skills", "List available skills"),
        ("mcp", "List MCP servers"),
        ("exit", "Cancel active run"),
    )


async def ensure_bot_commands(bot: Any) -> None:
    # `set_my_commands` exists on telegram.Bot/ExtBot.
    from telegram import BotCommand

    cmds = [BotCommand(command=c, description=d) for c, d in default_bot_command_specs()]
    await bot.set_my_commands(cmds)


def build_machines(cfg: Config) -> dict[str, MachineRuntime]:
    out: dict[str, MachineRuntime] = {}
    for name, md in cfg.machines.defs.items():
        if md.type == "local":
            out[name] = MachineRuntime(machine=LocalMachine(name=name), defn=md)
        elif md.type == "ssh":
            out[name] = MachineRuntime(
                machine=SSHMachine(
                    name=name,
                    host=md.host,
                    user=md.user,
                    port=md.port,
                    known_hosts=md.known_hosts,
                    use_agent=md.auth.use_agent,
                    key_path=md.auth.key_path,
                ),
                defn=md,
            )
    return out


def run_bot(cfg: Config) -> None:
    _require_ptb()
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        filters,
    )

    token = os.environ.get(cfg.telegram.token_env)
    if not token:
        raise RuntimeError(f"Env var {cfg.telegram.token_env} not set")

    store = Store(cfg.state.db_path)
    store.open()

    runtime = BotRuntime(
        cfg=cfg,
        store=store,
        machines=build_machines(cfg),
        codex=CodexCLIAdapter(),
        active_runs={},
    )

    async def _post_init(app: Application) -> None:
        try:
            await ensure_bot_commands(app.bot)
        except Exception as exc:
            # Non-fatal: bot can run even if Telegram API is temporarily unavailable.
            print(f"[tgcodex-bot] Warning: failed to set bot commands: {exc}")

    app = Application.builder().token(token).post_init(_post_init).build()
    app.bot_data["runtime"] = runtime

    from tgcodex.bot.callbacks import on_callback_query
    from tgcodex.bot.commands import (
        on_cd,
        on_compact,
        on_exit,
        on_machine,
        on_menu,
        on_mcp,
        on_model,
        on_new,
        on_plan,
        on_rename,
        on_reasoning,
        on_resume,
        on_skills,
        on_start,
        on_status,
        on_botstatus,
        on_set_approval,
        on_text_message,
    )

    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("menu", on_menu))
    app.add_handler(CommandHandler("status", on_status))
    app.add_handler(CommandHandler("botstatus", on_botstatus))
    app.add_handler(CommandHandler("new", on_new))
    app.add_handler(CommandHandler("rename", on_rename))
    app.add_handler(CommandHandler("exit", on_exit))
    app.add_handler(CommandHandler("machine", on_machine))
    app.add_handler(CommandHandler("cd", on_cd))
    app.add_handler(CommandHandler("approval", on_set_approval))
    app.add_handler(CommandHandler("reasoning", on_reasoning))
    app.add_handler(CommandHandler("plan", on_plan))
    app.add_handler(CommandHandler("compact", on_compact))
    app.add_handler(CommandHandler("model", on_model))
    app.add_handler(CommandHandler("skills", on_skills))
    app.add_handler(CommandHandler("mcp", on_mcp))
    app.add_handler(CommandHandler("resume", on_resume))

    app.add_handler(CallbackQueryHandler(on_callback_query))

    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), on_text_message)
    )

    app.run_polling(close_loop=False)
