import asyncio
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from typing import Any, Optional
from unittest import mock

from tgcodex.bot.commands import on_start, on_text_message
from tgcodex.codex.events import AgentMessage, ExecApprovalRequest, ThreadStarted, ToolStarted
from tgcodex.config import (
    ApprovalsConfig,
    CodexConfig,
    Config,
    LocalMachineDef,
    MachinesConfig,
    OutputConfig,
    SSHAuthDef,
    SSHMachineDef,
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


class _FakeUpdateNoUser:
    def __init__(self, *, chat_id: int, text: str) -> None:
        self.effective_chat = _FakeChat(id=chat_id)
        self.effective_user = None
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

    async def start_session(self, *, machine: Any, thread_id: Any, workdir: str, settings: Any) -> Any:
        self.last_settings = settings
        return self._run

    async def send_user_message(self, *, session: Any, prompt: str, settings: Any) -> None:
        return


class _FailingCodex:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def start_session(self, *, machine: Any, thread_id: Any, workdir: str, settings: Any) -> Any:
        raise self._exc

    async def send_user_message(self, *, session: Any, prompt: str, settings: Any) -> None:
        raise RuntimeError("send_user_message should not be called")


class TestProxyApprovalFlow(unittest.IsolatedAsyncioTestCase):
    async def test_ssh_events_stall_falls_back_to_local_and_starts_new_session(self) -> None:
        from tgcodex.machines.base import ExecResult

        class _RunStall:
            def __init__(self) -> None:
                self.run_id = "run-ssh-stall"
                self.thread_id: Optional[str] = None
                self.cancel_called = False

            async def events(self):  # type: ignore[override]
                await asyncio.Event().wait()
                if False:  # pragma: no cover
                    yield None

            async def cancel(self) -> None:
                self.cancel_called = True

        class _RunOK:
            def __init__(self) -> None:
                self.run_id = "run-local-ok"
                self.thread_id: Optional[str] = None
                self._events = [
                    ThreadStarted(thread_id="sess-local-1"),
                    AgentMessage(text="hello from local"),
                ]

            async def events(self):  # type: ignore[override]
                for ev in self._events:
                    if isinstance(ev, ThreadStarted):
                        self.thread_id = ev.thread_id
                    yield ev

            async def cancel(self) -> None:
                return

        class _CodexStallSshThenOk:
            def __init__(self) -> None:
                self.calls: list[str] = []
                self._stall = _RunStall()
                self._ok = _RunOK()

            async def start_session(self, *, machine: Any, thread_id: Any, workdir: str, settings: Any) -> Any:
                self.calls.append(str(getattr(machine, "type", "unknown")))
                if getattr(machine, "type", None) == "ssh":
                    return self._stall
                return self._ok

            async def send_user_message(self, *, session: Any, prompt: str, settings: Any) -> None:
                return

        class _FakeSshMachine:
            name = "sshbox"
            type = "ssh"

            async def exec_capture(self, argv: list[str], cwd: Optional[str]) -> ExecResult:
                raise TimeoutError("unreachable")

        class _FakeLocalMachine:
            name = "local"
            type = "local"

            async def exec_capture(self, argv: list[str], cwd: Optional[str]) -> ExecResult:
                return ExecResult(exit_code=0, stdout="", stderr="")

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
                        default="sshbox",
                        defs={
                            "sshbox": SSHMachineDef(
                                type="ssh",
                                host="example.invalid",
                                user="ubuntu",
                                port=22,
                                default_workdir="/home/ubuntu",
                                allowed_roots=("/home/ubuntu",),
                                auth=SSHAuthDef(use_agent=True, key_path=None),
                                known_hosts="~/.ssh/known_hosts",
                                connect_timeout_seconds=0.1,
                            ),
                            "local": LocalMachineDef(
                                type="local",
                                default_workdir="/tmp",
                                allowed_roots=("/tmp",),
                            ),
                        },
                    ),
                )

                runtime = type("RT", (), {})()
                runtime.cfg = cfg
                runtime.store = store
                runtime.machines = {
                    "sshbox": type("MR", (), {"machine": _FakeSshMachine(), "defn": cfg.machines.defs["sshbox"]})(),
                    "local": type("MR", (), {"machine": _FakeLocalMachine(), "defn": cfg.machines.defs["local"]})(),
                }
                runtime.codex = _CodexStallSshThenOk()
                runtime.active_runs = {}

                bot = _FakeBot()
                update = _FakeUpdate(chat_id=1, user_id=123, text="hello")
                context = _FakeContext(bot=bot, runtime=runtime)

                with (
                    mock.patch("tgcodex.bot.commands.SSH_FIRST_EVENT_TIMEOUT_SECONDS", 0.05, create=True),
                    mock.patch("tgcodex.bot.commands.SSH_IDLE_EVENT_TIMEOUT_SECONDS", 0.05, create=True),
                    mock.patch("tgcodex.bot.commands.SSH_LIVENESS_PROBE_TIMEOUT_SECONDS", 0.05, create=True),
                ):
                    await asyncio.wait_for(on_text_message(update, context), timeout=1.0)

                st = store.get_chat_state(1)
                assert st is not None
                self.assertEqual(st.machine_name, "local")
                self.assertEqual(st.active_session_id, "sess-local-1")
                self.assertTrue(bot.messages)
                self.assertIn("Falling back to local machine", bot.messages[0].get("text", ""))
                self.assertIn("hello from local", bot.messages[-1].get("text", ""))
                self.assertEqual(runtime.codex.calls, ["ssh", "local"])
            finally:
                store.close()

    async def test_ssh_start_run_failure_falls_back_to_local_and_starts_new_session(self) -> None:
        class _RunOK:
            def __init__(self) -> None:
                self.run_id = "run-2"
                self.thread_id: Optional[str] = None
                self._events = [
                    ThreadStarted(thread_id="sess-local-1"),
                    AgentMessage(text="hello from local"),
                ]

            async def events(self):  # type: ignore[override]
                for ev in self._events:
                    if isinstance(ev, ThreadStarted):
                        self.thread_id = ev.thread_id
                    yield ev

            async def cancel(self) -> None:
                return

        class _CodexFailSshThenOk:
            def __init__(self) -> None:
                self.calls: list[str] = []
                self._run = _RunOK()

            async def start_session(self, *, machine: Any, thread_id: Any, workdir: str, settings: Any) -> Any:
                self.calls.append(str(getattr(machine, "type", "unknown")))
                if getattr(machine, "type", None) == "ssh":
                    raise TimeoutError("ssh connect timeout")
                return self._run

            async def send_user_message(self, *, session: Any, prompt: str, settings: Any) -> None:
                return

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
                        default="sshbox",
                        defs={
                            "sshbox": SSHMachineDef(
                                type="ssh",
                                host="example.invalid",
                                user="ubuntu",
                                port=22,
                                default_workdir="/home/ubuntu",
                                allowed_roots=("/home/ubuntu",),
                                auth=SSHAuthDef(use_agent=True, key_path=None),
                                known_hosts="~/.ssh/known_hosts",
                                connect_timeout_seconds=0.1,
                            ),
                            "local": LocalMachineDef(
                                type="local",
                                default_workdir="/tmp",
                                allowed_roots=("/tmp",),
                            ),
                        },
                    ),
                )

                runtime = type("RT", (), {})()
                runtime.cfg = cfg
                runtime.store = store
                runtime.machines = {
                    "sshbox": type("MR", (), {"machine": type("M", (), {"type": "ssh"})(), "defn": cfg.machines.defs["sshbox"]})(),
                    "local": type("MR", (), {"machine": type("M", (), {"type": "local"})(), "defn": cfg.machines.defs["local"]})(),
                }
                runtime.codex = _CodexFailSshThenOk()
                runtime.active_runs = {}

                bot = _FakeBot()
                update = _FakeUpdate(chat_id=1, user_id=123, text="hello")
                context = _FakeContext(bot=bot, runtime=runtime)

                await on_text_message(update, context)

                st = store.get_chat_state(1)
                assert st is not None
                self.assertEqual(st.machine_name, "local")
                self.assertEqual(st.active_session_id, "sess-local-1")
                self.assertTrue(bot.messages)
                # First message should mention fallback.
                self.assertIn("Falling back to local machine", bot.messages[0].get("text", ""))
                # Second message should include the run output.
                self.assertIn("hello from local", bot.messages[-1].get("text", ""))
                self.assertEqual(runtime.codex.calls, ["ssh", "local"])
            finally:
                store.close()

    async def test_untrusted_prompts_on_exec_approval_request_and_keeps_pending_action(self) -> None:
        class _RunNeedsApproval(_FakeRun):
            def __init__(self) -> None:
                super().__init__()
                self._gate = asyncio.Event()
                self._events = [
                    ThreadStarted(thread_id="sess-1"),
                    ExecApprovalRequest(
                        command="mkdir -p foo",
                        cwd="/tmp",
                        reason="needs approval",
                        call_id="call-1",
                    ),
                ]

            async def events(self):  # type: ignore[override]
                for ev in self._events:
                    if isinstance(ev, ThreadStarted):
                        self.thread_id = ev.thread_id
                    yield ev
                await self._gate.wait()
                if False:  # pragma: no cover
                    yield None

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

                run = _RunNeedsApproval()
                runtime = type("RT", (), {})()
                runtime.cfg = cfg
                runtime.store = store
                runtime.machines = {"local": type("MR", (), {"machine": object(), "defn": cfg.machines.defs["local"]})()}
                runtime.codex = _FakeCodex(run)
                runtime.active_runs = {}

                bot = _FakeBot()
                update = _FakeUpdate(chat_id=1, user_id=123, text="create foo")
                context = _FakeContext(bot=bot, runtime=runtime)

                task = asyncio.create_task(on_text_message(update, context))
                try:
                    # Wait until the approval prompt is recorded.
                    for _ in range(50):
                        active = store.get_active_run(1)
                        if active is not None and active.status == "waiting_approval":
                            break
                        await asyncio.sleep(0.01)

                    active = store.get_active_run(1)
                    self.assertIsNotNone(active)
                    assert active is not None
                    self.assertEqual(active.status, "waiting_approval")
                    pending = json.loads(active.pending_action_json or "{}")
                    self.assertEqual(pending.get("type"), "approval_request")
                    # Fake runs emit ExecApprovalRequest directly (no app-server raw method),
                    # so the bot defaults this to a command approval request.
                    self.assertEqual(pending.get("request_kind"), "commandExecution")
                    self.assertEqual(pending.get("command"), "mkdir -p foo")
                    self.assertFalse(run.cancel_called)
                    self.assertEqual(runtime.codex.last_settings.sandbox, "workspace-write")
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            finally:
                store.close()

    async def test_untrusted_does_not_prompt_on_readonly_tool_started(self) -> None:
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
                self.assertIsNone(active)
                # Agent output should still be delivered.
                self.assertTrue(bot.messages)
                self.assertIn("ok", bot.messages[-1].get("text", ""))
                self.assertFalse(run.cancel_called)
            finally:
                store.close()

    async def test_start_run_failure_sends_error_instead_of_silence(self) -> None:
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
                runtime.codex = _FailingCodex(TimeoutError("ssh connect timeout"))
                runtime.active_runs = {}

                bot = _FakeBot()
                update = _FakeUpdate(chat_id=1, user_id=123, text="hello")
                context = _FakeContext(bot=bot, runtime=runtime)

                await on_text_message(update, context)

                self.assertTrue(bot.messages)
                text = bot.messages[-1].get("text", "")
                self.assertIn("Failed to start run", text)
                self.assertIn("ssh connect timeout", text)
                self.assertIsNone(store.get_active_run(1))
            finally:
                store.close()

    async def test_missing_effective_user_denies_instead_of_crashing(self) -> None:
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
                runtime.codex = _FailingCodex(RuntimeError("should not be called"))
                runtime.active_runs = {}

                bot = _FakeBot()
                update = _FakeUpdateNoUser(chat_id=1, text="hello")
                context = _FakeContext(bot=bot, runtime=runtime)

                await on_text_message(update, context)

                self.assertTrue(bot.messages)
                self.assertEqual(bot.messages[-1]["text"], "Unauthorized")
            finally:
                store.close()

    async def test_missing_effective_user_denies_on_start(self) -> None:
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
                runtime.codex = _FailingCodex(RuntimeError("should not be called"))
                runtime.active_runs = {}

                bot = _FakeBot()
                update = _FakeUpdateNoUser(chat_id=1, text="/start")
                context = _FakeContext(bot=bot, runtime=runtime)

                await on_start(update, context)

                self.assertTrue(bot.messages)
                self.assertEqual(bot.messages[-1]["text"], "Unauthorized")
            finally:
                store.close()
