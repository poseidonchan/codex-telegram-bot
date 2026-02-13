import os
import tempfile
import unittest
from dataclasses import dataclass
from typing import Any, Optional

from tgcodex.bot.commands import on_text_message
from tgcodex.codex.events import AgentMessage, ThreadStarted, ToolStarted
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
        self.chat_actions: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> Any:  # python-telegram-bot returns Message
        self.messages.append({"method": "send_message", **kwargs})

        class _Msg:
            message_id = 1

        return _Msg()

    async def edit_message_text(self, **kwargs: Any) -> Any:
        self.messages.append({"method": "edit_message_text", **kwargs})
        return None

    async def send_chat_action(self, **kwargs: Any) -> Any:
        self.chat_actions.append(kwargs)
        return None


@dataclass
class _FakeUser:
    id: int


@dataclass
class _FakeChat:
    id: int


@dataclass
class _FakeMessage:
    text: str


class _FakeUpdate:
    def __init__(self, *, chat_id: int, user_id: int, text: str) -> None:
        self.effective_chat = _FakeChat(id=chat_id)
        self.effective_user = _FakeUser(id=user_id)
        self.message = _FakeMessage(text=text)


class _FakeApplication:
    def __init__(self, runtime: Any) -> None:
        self.bot_data = {"runtime": runtime}


class _FakeContext:
    def __init__(self, *, bot: Any, runtime: Any) -> None:
        self.bot = bot
        self.application = _FakeApplication(runtime)


class _FakeRun:
    def __init__(self) -> None:
        self.run_id = "run-1"
        self.thread_id: Optional[str] = None
        self._events = [
            ThreadStarted(thread_id="sess-1"),
            ToolStarted(command="mkdir -p foo"),
        ]
        self.cancel_called = False

    async def events(self):  # type: ignore[override]
        for ev in self._events:
            if isinstance(ev, ThreadStarted):
                self.thread_id = ev.thread_id
            yield ev

    async def cancel(self) -> None:  # matches CodexRun.cancel
        self.cancel_called = True


class _FakeCodex:
    def __init__(self, run: _FakeRun) -> None:
        self._run = run
        self.last_settings = None

    async def start_run(self, *, machine: Any, session_id: Any, workdir: str, prompt: str, settings: Any) -> Any:
        self.last_settings = settings
        return self._run


class TestProxyApprovalFlow(unittest.IsolatedAsyncioTestCase):
    async def test_untrusted_prompts_on_tool_started_and_keeps_pending_action(self) -> None:
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

                run = _FakeRun()
                runtime = type("RT", (), {})()
                runtime.cfg = cfg
                runtime.store = store
                runtime.machines = {"local": type("MR", (), {"machine": object(), "defn": cfg.machines.defs["local"]})()}
                runtime.codex = _FakeCodex(run)
                runtime.active_runs = {}

                bot = _FakeBot()
                update = _FakeUpdate(chat_id=1, user_id=123, text="create foo")
                context = _FakeContext(bot=bot, runtime=runtime)

                await on_text_message(update, context)

                active = store.get_active_run(1)
                self.assertIsNotNone(active)
                assert active is not None
                self.assertEqual(active.status, "waiting_approval")
                self.assertIn("proxy", active.pending_action_json or "")
                self.assertTrue(run.cancel_called)
                # Settings must be coerced to read-only in untrusted mode (defense-in-depth).
                self.assertEqual(runtime.codex.last_settings.sandbox, "read-only")
            finally:
                store.close()

    async def test_untrusted_prompts_on_readonly_tool_too(self) -> None:
        class _RunRO(_FakeRun):
            def __init__(self) -> None:
                super().__init__()
                self._events = [
                    ThreadStarted(thread_id="sess-1"),
                    ToolStarted(command="ls -la"),
                    AgentMessage(text="ok"),
                ]

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

                run = _RunRO()
                runtime = type("RT", (), {})()
                runtime.cfg = cfg
                runtime.store = store
                runtime.machines = {"local": type("MR", (), {"machine": object(), "defn": cfg.machines.defs["local"]})()}
                runtime.codex = _FakeCodex(run)
                runtime.active_runs = {}

                bot = _FakeBot()
                update = _FakeUpdate(chat_id=1, user_id=123, text="say hi")
                context = _FakeContext(bot=bot, runtime=runtime)

                await on_text_message(update, context)

                active = store.get_active_run(1)
                self.assertIsNotNone(active)
                assert active is not None
                self.assertEqual(active.status, "waiting_approval")
                self.assertIn("proxy", active.pending_action_json or "")
                self.assertTrue(run.cancel_called)
            finally:
                store.close()
