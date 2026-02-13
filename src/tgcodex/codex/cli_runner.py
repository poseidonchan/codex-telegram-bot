from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncIterator, Optional

from tgcodex.codex.events import (
    CodexEvent,
    ErrorEvent,
    LogLine,
    ThreadStarted,
    parse_event_obj,
    parse_json_line,
)
from tgcodex.machines.base import Machine, RunHandle


class CodexRun:
    def __init__(self, *, machine: Machine, handle: RunHandle) -> None:
        self.machine = machine
        self.handle = handle
        self.run_id = str(uuid.uuid4())
        self.thread_id: Optional[str] = None

        self._queue: asyncio.Queue[Optional[CodexEvent]] = asyncio.Queue()
        self._closed = False

    async def push_event(self, ev: CodexEvent) -> None:
        if isinstance(ev, ThreadStarted):
            self.thread_id = ev.thread_id
        await self._queue.put(ev)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)

    async def events(self) -> AsyncIterator[CodexEvent]:
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

    async def send_exec_approval(self, *, decision: str, call_id: Optional[str] = None) -> None:
        """
        Sends an approval decision back to Codex.

        Format is best-effort and may evolve with Codex versions. Current default:
          {"type":"exec_approval","decision":"approved"|"denied"}
        """
        # Newer protocol-style Codex streams use an "op" envelope. The approval payload carries a
        # `decision` string (e.g. "approved"/"denied", plus other variants in newer Codex builds).
        if call_id:
            payload = {
                "id": str(uuid.uuid4()),
                "op": {
                    "type": "exec_approval",
                    "decision": decision,
                    "call_id": call_id,
                },
            }
        else:
            payload = {"type": "exec_approval", "decision": decision}
        data = (json.dumps(payload) + "\n").encode("utf-8")
        await self.handle.write_stdin(data)


async def start_codex_process(
    *,
    machine: Machine,
    argv: list[str],
    cwd: Optional[str],
    env: Optional[dict[str, str]],
    pty: bool,
) -> CodexRun:
    run: Optional[CodexRun] = None
    stdout_buf = bytearray()
    stderr_buf = bytearray()

    async def on_stdout(chunk: bytes) -> None:
        nonlocal stdout_buf, run
        stdout_buf += chunk
        while True:
            idx = stdout_buf.find(b"\n")
            if idx < 0:
                return
            line = stdout_buf[:idx].decode("utf-8", "replace")
            del stdout_buf[: idx + 1]
            line = line.strip()
            if not line:
                continue
            obj, non_json = parse_json_line(line)
            if non_json is not None:
                if run is not None:
                    await run.push_event(LogLine(text=non_json))
                continue
            assert obj is not None
            for ev in parse_event_obj(obj):
                if run is not None:
                    await run.push_event(ev)

    async def on_stderr(chunk: bytes) -> None:
        nonlocal stderr_buf, run
        stderr_buf += chunk
        while True:
            idx = stderr_buf.find(b"\n")
            if idx < 0:
                return
            line = stderr_buf[:idx].decode("utf-8", "replace")
            del stderr_buf[: idx + 1]
            line = line.rstrip("\r")
            if not line.strip():
                continue
            if run is not None:
                await run.push_event(LogLine(text=line))

    handle = await machine.run(
        argv=argv,
        cwd=cwd,
        env=env,
        pty=pty,
        stdout_cb=on_stdout,
        stderr_cb=on_stderr,
        stdin_provider=None,
    )
    run = CodexRun(machine=machine, handle=handle)

    async def reap() -> None:
        try:
            rc = await run.wait()
            if rc != 0:
                await run.push_event(ErrorEvent(message=f"codex exited with {rc}"))
        except Exception as exc:
            # Avoid "Task exception was never retrieved" and surface failures to the chat.
            await run.push_event(ErrorEvent(message=f"codex runner error: {exc}"))
        finally:
            await run.close()

    asyncio.create_task(reap())
    return run
