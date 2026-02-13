from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from tgcodex.codex.app_server_rpc import JsonRpcConnection, JsonRpcIncoming, RequestId
from tgcodex.codex.events import (
    AgentMessageDelta,
    AgentReasoningDelta,
    ErrorEvent,
    ExecApprovalRequest,
    ExecCommandEnd,
    ExecCommandOutputDelta,
    LogLine,
    ThreadStarted,
    TokenCount,
    ToolStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from tgcodex.machines.base import Machine, RunHandle


@dataclass(frozen=True)
class AppServerSettings:
    codex_bin: str
    codex_args: tuple[str, ...]
    model: Optional[str]
    thinking_level: Optional[str]
    sandbox: Optional[str]
    approval_mode: str  # on-request|yolo


def _approval_policy_from_mode(mode: str) -> str:
    if mode == "yolo":
        return "never"
    return "on-request"


def _developer_instructions_for_mode(mode: str) -> Optional[str]:
    # IMPORTANT: In tgcodex, approvals are enforced via Codex app-server's protocol-level
    # approval requests (which the Telegram bot renders as inline buttons). Do not let the
    # model fall back to "Reply YES" style text confirmations, which are easy to confuse with
    # the real approval gate.
    base = (
        "Approval UX requirements (Telegram client integration):\n"
        "- Never ask the user to approve actions by replying YES/NO in chat text.\n"
        "- Do not treat chat text as an approval signal.\n"
        "- For any command execution or file change that needs approval, rely on the built-in approval gate:\n"
        "  the system will pause and the client will show inline Approve/Reject buttons.\n"
        "- Do not duplicate the approval prompt in natural language.\n"
    )
    return base


class AppServerSession:
    """
    Live Codex app-server process + a single active turn stream.

    This object is stored in memory (runtime.active_runs[chat_id]) so callback handlers can
    respond to server-initiated approval requests.
    """

    def __init__(self, *, machine: Machine, handle: RunHandle) -> None:
        self.machine = machine
        self.handle = handle
        self.run_id = str(uuid.uuid4())

        self.thread_id: Optional[str] = None
        self.turn_id: Optional[str] = None

        self._queue: asyncio.Queue[Optional[Any]] = asyncio.Queue()
        self._closed = False

        async def on_req(req: JsonRpcIncoming) -> None:
            await self._on_server_request(req)

        async def on_notif(notif: JsonRpcIncoming) -> None:
            await self._on_notification(notif)

        async def on_log(line: str) -> None:
            await self.push_event(LogLine(text=line))

        self.rpc = JsonRpcConnection(
            handle=handle,
            on_server_request=on_req,
            on_notification=on_notif,
            on_log=on_log,
        )

    async def push_event(self, ev: Any) -> None:
        if isinstance(ev, ThreadStarted):
            self.thread_id = ev.thread_id
        await self._queue.put(ev)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.rpc.close()
        except Exception:
            pass
        await self._queue.put(None)

    async def events(self) -> AsyncIterator[Any]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def wait(self) -> int:
        return await self.handle.wait()

    async def cancel(self) -> None:
        try:
            await self.handle.terminate()
        except Exception:
            pass
        try:
            await self.handle.kill()
        except Exception:
            pass
        try:
            await self.handle.close_stdin()
        except Exception:
            pass

    async def respond_approval(
        self,
        *,
        rpc_id: RequestId,
        request_kind: str,
        decision: str,
        execpolicy_amendment: Optional[list[str]] = None,
    ) -> None:
        """
        Send a response to a server approval request.

        request_kind:
          - commandExecution
          - fileChange
        decision:
          - accept | acceptForSession | decline | cancel
        """
        if request_kind == "commandExecution":
            if decision == "acceptWithExecpolicyAmendment":
                assert execpolicy_amendment is not None
                result = {
                    "decision": {
                        "acceptWithExecpolicyAmendment": {
                            "execpolicy_amendment": execpolicy_amendment
                        }
                    }
                }
            else:
                result = {"decision": decision}
        elif request_kind == "fileChange":
            result = {"decision": decision}
        else:
            raise ValueError(f"Unknown request_kind: {request_kind}")

        await self.rpc.respond(request_id=rpc_id, result=result)

    async def _on_server_request(self, req: JsonRpcIncoming) -> None:
        rid = req.id
        method = req.method or ""
        params = req.params or {}

        if rid is None:
            return

        if method == "item/commandExecution/requestApproval":
            cmd = params.get("command")
            cwd = params.get("cwd")
            reason = params.get("reason")
            cmd_s = cmd if isinstance(cmd, str) else ""
            cwd_s = cwd if isinstance(cwd, str) else None
            reason_s = reason if isinstance(reason, str) else None
            # Reuse the existing ExecApprovalRequest event shape; call_id carries the RPC request id.
            await self.push_event(
                ExecApprovalRequest(
                    command=cmd_s,
                    cwd=cwd_s,
                    reason=reason_s,
                    call_id=str(rid),
                    raw=req.obj,
                )
            )
            return

        if method == "item/fileChange/requestApproval":
            # File-change approvals are surfaced as an ExecApprovalRequest-like event for now.
            reason = params.get("reason")
            reason_s = reason if isinstance(reason, str) else None
            await self.push_event(
                ExecApprovalRequest(
                    command="[file change approval]",
                    cwd=None,
                    reason=reason_s,
                    call_id=str(rid),
                    raw=req.obj,
                )
            )
            return

        # Unknown server request: decline to avoid deadlocking the turn.
        try:
            await self.rpc.respond(request_id=rid, result={"decision": "decline"})
        except Exception:
            pass

    async def _on_notification(self, notif: JsonRpcIncoming) -> None:
        method = notif.method or ""
        params = notif.params or {}

        if method == "thread/started":
            thread = params.get("thread")
            if isinstance(thread, dict):
                tid = thread.get("id")
                if isinstance(tid, str) and tid:
                    await self.push_event(ThreadStarted(thread_id=tid))
            return

        if method == "turn/started":
            turn = params.get("turn")
            if isinstance(turn, dict):
                tid = turn.get("id")
                if isinstance(tid, str) and tid:
                    self.turn_id = tid
            await self.push_event(TurnStarted())
            return

        if method == "turn/completed":
            turn = params.get("turn")
            # If the turn failed, surface an error.
            if isinstance(turn, dict):
                status = turn.get("status")
                if status == "failed":
                    err = turn.get("error")
                    msg = None
                    if isinstance(err, dict):
                        msg = err.get("message")
                    await self.push_event(TurnFailed(message=str(msg) if msg else "turn failed"))
                else:
                    await self.push_event(TurnCompleted())
            else:
                await self.push_event(TurnCompleted())
            # Close stream after completion.
            await self.close()
            return

        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str) and delta:
                await self.push_event(AgentMessageDelta(text=delta))
            return

        if method == "item/reasoning/textDelta":
            delta = params.get("delta")
            if isinstance(delta, str) and delta:
                await self.push_event(AgentReasoningDelta(text=delta))
            return

        if method == "item/commandExecution/outputDelta":
            delta = params.get("delta")
            if isinstance(delta, str) and delta:
                await self.push_event(ExecCommandOutputDelta(text=delta, raw=notif.obj))
            return

        if method == "item/started":
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "commandExecution":
                cmd = item.get("command")
                if isinstance(cmd, str) and cmd:
                    await self.push_event(ToolStarted(command=cmd))
            return

        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "commandExecution":
                exit_code = item.get("exitCode")
                agg = item.get("aggregatedOutput")
                await self.push_event(
                    ExecCommandEnd(
                        exit_code=int(exit_code) if isinstance(exit_code, int) else None,
                        aggregated_output=agg if isinstance(agg, str) else None,
                        raw=notif.obj,
                    )
                )
            return

        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage")
            # Best-effort mapping into the existing TokenCount shape so /status continues to work.
            if isinstance(usage, dict):
                total = usage.get("total")
                mcw = usage.get("modelContextWindow")
                model_context_window = int(mcw) if isinstance(mcw, int) else None
                total_tokens = None
                it = ot = cit = rot = None
                if isinstance(total, dict):
                    total_tokens = total.get("totalTokens") if isinstance(total.get("totalTokens"), int) else None
                    it = total.get("inputTokens") if isinstance(total.get("inputTokens"), int) else None
                    ot = total.get("outputTokens") if isinstance(total.get("outputTokens"), int) else None
                    cit = total.get("cachedInputTokens") if isinstance(total.get("cachedInputTokens"), int) else None
                    rot = total.get("reasoningOutputTokens") if isinstance(total.get("reasoningOutputTokens"), int) else None
                await self.push_event(
                    TokenCount(
                        model_context_window=model_context_window,
                        total_tokens=total_tokens,
                        input_tokens=it,
                        output_tokens=ot,
                        cached_input_tokens=cit,
                        reasoning_output_tokens=rot,
                        primary_used_percent=None,
                        primary_window_minutes=None,
                        primary_resets_at=None,
                        secondary_used_percent=None,
                        secondary_window_minutes=None,
                        secondary_resets_at=None,
                        raw=notif.obj,
                    )
                )
            return

        if method == "account/rateLimits/updated":
            rl = params.get("rateLimits")
            p = None
            s = None
            if isinstance(rl, dict):
                p = rl.get("primary")
                s = rl.get("secondary")
            def _unpack(win: Any) -> tuple[Optional[float], Optional[int], Optional[int]]:
                if not isinstance(win, dict):
                    return (None, None, None)
                used = win.get("usedPercent")
                mins = win.get("windowDurationMins")
                resets = win.get("resetsAt")
                up = float(used) if isinstance(used, (int, float)) and not isinstance(used, bool) else None
                return (up, int(mins) if isinstance(mins, int) else None, int(resets) if isinstance(resets, int) else None)
            p_used, p_mins, p_resets = _unpack(p)
            s_used, s_mins, s_resets = _unpack(s)
            await self.push_event(
                TokenCount(
                    model_context_window=None,
                    total_tokens=None,
                    input_tokens=None,
                    output_tokens=None,
                    cached_input_tokens=None,
                    reasoning_output_tokens=None,
                    primary_used_percent=p_used,
                    primary_window_minutes=p_mins,
                    primary_resets_at=p_resets,
                    secondary_used_percent=s_used,
                    secondary_window_minutes=s_mins,
                    secondary_resets_at=s_resets,
                    raw=notif.obj,
                )
            )
            return

        if method == "error":
            err = params.get("error")
            msg = None
            if isinstance(err, dict):
                msg = err.get("message")
            await self.push_event(ErrorEvent(message=str(msg) if msg else "error", raw=params))
            return


class AppServerBackend:
    async def start_session(
        self,
        *,
        machine: Machine,
        thread_id: Optional[str],
        workdir: str,
        settings: AppServerSettings,
    ) -> AppServerSession:
        # Normalize/resolve the workdir before passing it to Codex.
        try:
            workdir = await machine.realpath(workdir)
        except Exception:
            workdir = os.path.normpath(workdir)

        argv = [settings.codex_bin, "app-server", *settings.codex_args]

        session: Optional[AppServerSession] = None

        async def on_stdout(chunk: bytes) -> None:
            if session is not None:
                await session.rpc.feed_stdout(chunk)

        async def on_stderr(chunk: bytes) -> None:
            if session is not None:
                await session.rpc.feed_stderr(chunk)

        handle = await machine.run(
            argv=argv,
            cwd=workdir,
            env=None,
            pty=False,
            stdout_cb=on_stdout,
            stderr_cb=on_stderr,
            stdin_provider=None,
        )
        session = AppServerSession(machine=machine, handle=handle)

        async def reap() -> None:
            try:
                rc = await session.wait()
                if rc != 0:
                    await session.push_event(ErrorEvent(message=f"codex app-server exited with {rc}"))
            except Exception as exc:
                await session.push_event(ErrorEvent(message=f"codex app-server runner error: {exc}"))
            finally:
                await session.close()

        asyncio.create_task(reap())

        # Handshake.
        await session.rpc.request(
            method="initialize",
            params={"clientInfo": {"name": "tgcodex", "version": "0.0"}},
        )

        approval_policy = _approval_policy_from_mode(settings.approval_mode)
        developer_instructions = _developer_instructions_for_mode(settings.approval_mode)

        if thread_id:
            res = await session.rpc.request(
                method="thread/resume",
                params={
                    "threadId": thread_id,
                    "cwd": workdir,
                    "approvalPolicy": approval_policy,
                    "sandbox": settings.sandbox,
                    "developerInstructions": developer_instructions,
                    "model": settings.model,
                },
            )
        else:
            res = await session.rpc.request(
                method="thread/start",
                params={
                    "cwd": workdir,
                    "approvalPolicy": approval_policy,
                    "sandbox": settings.sandbox,
                    "developerInstructions": developer_instructions,
                    "model": settings.model,
                },
            )

        # Thread id is needed for turn/start; also emit ThreadStarted for existing bot logic.
        tid: Optional[str] = None
        if isinstance(res, dict):
            thread = res.get("thread")
            if isinstance(thread, dict):
                t = thread.get("id")
                if isinstance(t, str) and t:
                    tid = t
        if tid:
            await session.push_event(ThreadStarted(thread_id=tid))

        return session

    async def send_user_message(
        self, *, session: AppServerSession, prompt: str, settings: AppServerSettings
    ) -> None:
        if not session.thread_id:
            raise RuntimeError("Session missing thread_id")

        params: dict[str, Any] = {
            "threadId": session.thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if settings.model:
            params["model"] = settings.model
        if settings.thinking_level:
            params["effort"] = settings.thinking_level

        await session.rpc.request(method="turn/start", params=params)
