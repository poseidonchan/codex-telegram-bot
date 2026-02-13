import asyncio
import os
import tempfile
import unittest
from dataclasses import dataclass
from typing import Any, Optional

from tgcodex.bot.commands import on_text_message
from tgcodex.codex.events import AgentMessage, ThreadStarted
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

    async def send_message(self, **kwargs: Any) -> Any:
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
    def __init__(self, *, run_id: str, finish: asyncio.Event) -> None:
        self.run_id = run_id
        self.thread_id: Optional[str] = None
        self._finish = finish

    async def events(self):  # type: ignore[override]
        yield ThreadStarted(thread_id="sess-1")
        await self._finish.wait()
        yield AgentMessage(text="hello")

    async def cancel(self) -> None:
        return


class _BlockingStartCodex:
    def __init__(self, *, start_gate: asyncio.Event, finish_gate: asyncio.Event) -> None:
        self.calls = 0
        self._start_gate = start_gate
        self._finish_gate = finish_gate

    async def start_run(self, *, machine: Any, session_id: Any, workdir: str, prompt: str, settings: Any) -> Any:
        self.calls += 1
        await self._start_gate.wait()
        return _FakeRun(run_id=f"run-{self.calls}", finish=self._finish_gate)


class TestBotNonBlocking(unittest.IsolatedAsyncioTestCase):
    async def test_on_text_message_serializes_start_run_per_chat(self) -> None:
        start_gate = asyncio.Event()
        finish_gate = asyncio.Event()

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
                runtime.machines = {"local": type("MR", (), {"machine": object(), "defn": cfg.machines.defs["local"]})()}
                runtime.codex = _BlockingStartCodex(start_gate=start_gate, finish_gate=finish_gate)
                runtime.active_runs = {}

                bot = _FakeBot()
                context = _FakeContext(bot=bot, runtime=runtime)
                u1 = _FakeUpdate(chat_id=1, user_id=123, text="hello 1")
                u2 = _FakeUpdate(chat_id=1, user_id=123, text="hello 2")

                t1 = asyncio.create_task(on_text_message(u1, context))
                t2 = asyncio.create_task(on_text_message(u2, context))

                await asyncio.sleep(0.05)
                # Without a per-chat lock, both tasks can call start_run before active_run is set.
                self.assertEqual(runtime.codex.calls, 1)

                start_gate.set()
                await asyncio.sleep(0.05)

                # Second message should be rejected while the first run is active.
                await asyncio.wait_for(t2, timeout=1.0)
                self.assertTrue(any("Run in progress" in (m.get("text") or "") for m in bot.messages))

                finish_gate.set()
                await asyncio.wait_for(t1, timeout=1.0)
            finally:
                store.close()

    def test_text_message_handler_is_non_blocking(self) -> None:
        # When a run is in progress, we still need to process callback queries (e.g. /model clicks,
        # exec approvals). With PTB's default concurrent_updates=1, the text handler must be non-blocking.
        from telegram.ext import MessageHandler

        from tgcodex.bot.app import build_application

        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "state.sqlite3")
            cfg = Config(
                telegram=TelegramConfig(token_env="TELEGRAM_BOT_TOKEN", allowed_user_ids=(123,)),
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
                    flush_interval_ms=250,
                    min_flush_chars=120,
                    max_flush_delay_seconds=2.0,
                    max_chars=3500,
                    truncate=True,
                    typing_interval_seconds=4.0,
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
            os.environ["TELEGRAM_BOT_TOKEN"] = "123:ABC"
            app = build_application(cfg)
            try:
                mh = None
                for handlers in app.handlers.values():
                    for h in handlers:
                        if isinstance(h, MessageHandler) and getattr(h, "callback", None) == on_text_message:
                            mh = h
                            break
                    if mh is not None:
                        break
                self.assertIsNotNone(mh)
                assert mh is not None
                self.assertFalse(bool(mh.block))
            finally:
                rt = app.bot_data.get("runtime")
                if rt is not None:
                    try:
                        rt.store.close()
                    except Exception:
                        pass

