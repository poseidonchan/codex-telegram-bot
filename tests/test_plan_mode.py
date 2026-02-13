import os
import tempfile
import unittest
from dataclasses import dataclass
from typing import Any

from tgcodex.bot.commands import on_plan
from tgcodex.config import (
    ApprovalsConfig,
    CodexConfig,
    Config,
    LocalMachineDef,
    MachinesConfig,
    OutputConfig,
    StateConfig,
    TelegramConfig,
)
from tgcodex.state.store import Store


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> Any:  # python-telegram-bot returns Message
        self.messages.append(kwargs)
        return None


@dataclass
class _FakeUser:
    id: int


@dataclass
class _FakeChat:
    id: int


class _FakeUpdate:
    def __init__(self, *, chat_id: int, user_id: int) -> None:
        self.effective_chat = _FakeChat(id=chat_id)
        self.effective_user = _FakeUser(id=user_id)


class _FakeApplication:
    def __init__(self, runtime: Any) -> None:
        self.bot_data = {"runtime": runtime}


class _FakeContext:
    def __init__(self, *, bot: Any, runtime: Any) -> None:
        self.bot = bot
        self.application = _FakeApplication(runtime)


class TestPlanModeToggle(unittest.IsolatedAsyncioTestCase):
    async def test_plan_mode_messages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "state.sqlite3")
            store = Store(db_path)
            store.open()
            try:
                cfg = Config(
                    telegram=TelegramConfig(token_env="DUMMY", allowed_user_ids=(123,)),
                    state=StateConfig(db_path=db_path),
                    codex=CodexConfig(
                        bin="codex",
                        args=(),
                        model=None,
                        sandbox="workspace-write",
                        approval_policy="untrusted",
                        skip_git_repo_check=True,
                    ),
                    output=OutputConfig(
                        flush_interval_ms=999999,
                        min_flush_chars=999999,
                        max_flush_delay_seconds=999999.0,
                        max_chars=3500,
                        truncate=True,
                        typing_interval_seconds=999999.0,
                        show_codex_logs=False,
                        show_tool_output=False,
                        max_tool_output_chars=1200,
                    ),
                    approvals=ApprovalsConfig(prefix_tokens=2),
                    machines=MachinesConfig(
                        default="local",
                        defs={
                            "local": LocalMachineDef(
                                type="local",
                                default_workdir="/tmp",
                                allowed_roots=("/tmp",),
                            )
                        },
                    ),
                )

                runtime = type("RT", (), {})()
                runtime.cfg = cfg
                runtime.store = store
                runtime.machines = {"local": type("MR", (), {"defn": cfg.machines.defs["local"]})()}

                bot = _FakeBot()
                update = _FakeUpdate(chat_id=1, user_id=123)
                context = _FakeContext(bot=bot, runtime=runtime)

                await on_plan(update, context)
                self.assertIn("Plan mode enabled", bot.messages[-1]["text"])

                await on_plan(update, context)
                self.assertEqual("Plan mode disabled.", bot.messages[-1]["text"])
            finally:
                store.close()

