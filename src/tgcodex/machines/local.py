from __future__ import annotations

import asyncio
import glob
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional

from tgcodex.machines.base import ExecResult, RunHandle


@dataclass
class _LocalRunHandle:
    proc: asyncio.subprocess.Process
    stdout_task: asyncio.Task[None]
    stderr_task: asyncio.Task[None]
    stdin_task: Optional[asyncio.Task[None]]

    def _kill_group(self, sig: int) -> None:
        """
        Best-effort: terminate/kill the entire process group so tool children don't
        keep running after the Codex CLI parent is stopped.
        """
        pid = getattr(self.proc, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            return
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except Exception:
            # Fall back to signaling only the parent.
            try:
                os.kill(pid, sig)
            except Exception:
                pass

    async def wait(self) -> int:
        rc = await self.proc.wait()
        await self.stdout_task
        await self.stderr_task
        if self.stdin_task is not None:
            self.stdin_task.cancel()
            try:
                await self.stdin_task
            except (asyncio.CancelledError, Exception):
                pass
        return int(rc)

    async def terminate(self) -> None:
        if self.proc.returncode is not None:
            return
        # Prefer process-group termination so spawned commands don't keep running.
        if os.name == "posix":
            self._kill_group(signal.SIGTERM)
        else:
            self.proc.terminate()

    async def kill(self) -> None:
        if self.proc.returncode is not None:
            return
        if os.name == "posix":
            self._kill_group(signal.SIGKILL)
        else:
            self.proc.kill()

    async def write_stdin(self, data: bytes) -> None:
        if self.proc.stdin is None:
            return
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()

    async def close_stdin(self) -> None:
        if self.proc.stdin is None:
            return
        try:
            self.proc.stdin.close()
        except Exception:
            pass


class LocalMachine:
    def __init__(self, *, name: str) -> None:
        self.name = name
        self.type = "local"

    async def run(
        self,
        argv: list[str],
        cwd: Optional[str],
        env: Optional[dict[str, str]],
        pty: bool,
        stdout_cb: Callable[[bytes], Awaitable[None]],
        stderr_cb: Callable[[bytes], Awaitable[None]],
        stdin_provider: Optional[AsyncIterator[bytes]],
    ) -> RunHandle:
        if pty:
            # PTY support can be added if needed; pipes are sufficient for Codex --json mode.
            pty = False

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Ensure the Codex CLI is a process-group leader so we can terminate the
            # entire group (including any spawned tool commands) on cancel.
            start_new_session=(os.name == "posix"),
        )

        async def pump(stream: asyncio.StreamReader, cb: Callable[[bytes], Awaitable[None]]) -> None:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                await cb(chunk)

        stdout_task = asyncio.create_task(pump(proc.stdout, stdout_cb))  # type: ignore[arg-type]
        stderr_task = asyncio.create_task(pump(proc.stderr, stderr_cb))  # type: ignore[arg-type]

        stdin_task: Optional[asyncio.Task[None]] = None
        if stdin_provider is not None:

            async def pump_stdin() -> None:
                assert proc.stdin is not None
                async for data in stdin_provider:
                    proc.stdin.write(data)
                    await proc.stdin.drain()
                try:
                    proc.stdin.close()
                except Exception:
                    pass

            stdin_task = asyncio.create_task(pump_stdin())

        return _LocalRunHandle(proc=proc, stdout_task=stdout_task, stderr_task=stderr_task, stdin_task=stdin_task)

    async def exec_capture(self, argv: list[str], cwd: Optional[str]) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await proc.communicate()
        return ExecResult(
            exit_code=int(proc.returncode or 0),
            stdout=(out_b or b"").decode("utf-8", "replace"),
            stderr=(err_b or b"").decode("utf-8", "replace"),
        )

    async def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    async def write_text(self, path: str, content: str, overwrite: bool) -> None:
        p = Path(path)
        if p.exists() and not overwrite:
            raise FileExistsError(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    async def list_glob(self, pattern: str) -> list[str]:
        return sorted(glob.glob(pattern, recursive=True))

    async def realpath(self, path: str) -> str:
        return str(Path(path).expanduser().resolve())
