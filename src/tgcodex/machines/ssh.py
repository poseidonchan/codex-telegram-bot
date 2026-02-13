from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from tgcodex.machines.base import ExecResult, RunHandle


def _require_asyncssh():  # pragma: no cover
    try:
        import asyncssh  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "asyncssh is required for ssh machines. Install project deps (see README.md)."
        ) from exc
    return asyncssh


@dataclass
class _SSHRunHandle:
    conn: Any  # asyncssh.SSHClientConnection
    process: Any  # asyncssh.SSHClientProcess
    stdout_task: asyncio.Task[None]
    stderr_task: asyncio.Task[None]

    async def wait(self) -> int:
        try:
            await self.process.wait()
            await self.stdout_task
            await self.stderr_task
            return int(self.process.exit_status or 0)
        finally:
            try:
                self.conn.close()
                await self.conn.wait_closed()
            except Exception:
                pass

    async def terminate(self) -> None:
        try:
            self.process.terminate()
        except Exception:
            pass
        try:
            self.conn.close()
            await self.conn.wait_closed()
        except Exception:
            pass

    async def kill(self) -> None:
        try:
            self.process.kill()
        except Exception:
            pass
        try:
            self.conn.close()
            await self.conn.wait_closed()
        except Exception:
            pass

    async def write_stdin(self, data: bytes) -> None:
        try:
            self.process.stdin.write(data)
            await self.process.stdin.drain()
        except Exception:
            pass

    async def close_stdin(self) -> None:
        try:
            self.process.stdin.close()
        except Exception:
            pass


class SSHMachine:
    def __init__(
        self,
        *,
        name: str,
        host: str,
        user: str,
        port: int,
        known_hosts: str,
        use_agent: bool,
        key_path: Optional[str],
    ) -> None:
        self.name = name
        self.type = "ssh"
        self._host = host
        self._user = user
        self._port = port
        self._known_hosts = known_hosts
        self._use_agent = use_agent
        self._key_path = key_path

    async def _connect(self):
        asyncssh = _require_asyncssh()
        # asyncssh uses `agent_path` (not Paramiko's `allow_agent`). Explicitly disable
        # agent usage when configured, to avoid surprising auth behavior.
        kwargs: dict[str, object] = {}
        if not self._use_agent:
            kwargs["agent_path"] = None

        return await asyncssh.connect(
            self._host,
            username=self._user,
            port=self._port,
            known_hosts=os.path.expanduser(self._known_hosts),
            client_keys=[os.path.expanduser(self._key_path)] if self._key_path else None,
            agent_forwarding=False,
            **kwargs,
        )

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
        # Note: stdin_provider is not supported for SSH runs yet; use write_stdin on the handle.
        if stdin_provider is not None:
            raise NotImplementedError("stdin_provider not supported for SSHMachine.run")

        cmd = " ".join(_shell_quote(a) for a in argv)

        # If the command is an absolute path to a script which uses a shebang like
        # `#!/usr/bin/env node`, non-interactive SSH sessions may not have nvm/Homebrew
        # PATH initialized. Prepend the command's directory to PATH so `env` can
        # locate the sibling interpreter (common for nvm-installed Node CLIs).
        if argv and os.path.isabs(argv[0]):
            bindir = os.path.dirname(argv[0])
            cmd = f"PATH={_shell_quote(bindir)}:$PATH; export PATH; {cmd}"
        if cwd:
            cmd = f"cd {_shell_quote(cwd)} && {cmd}"
        if env:
            exports = " ".join(f"{k}={_shell_quote(v)}" for k, v in env.items())
            cmd = f"env {exports} {cmd}"

        conn = await self._connect()
        # Connection lifetime is tied to the process; asyncssh keeps it alive.
        # Match LocalMachine behavior: stream bytes (not decoded strings) so downstream
        # consumers can treat stdout/stderr as raw bytes.
        proc = await conn.create_process(cmd, term_type="xterm" if pty else None, encoding=None)

        async def pump(stream, cb):  # type: ignore[no-untyped-def]
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", "replace")
                await cb(chunk)

        stdout_task = asyncio.create_task(pump(proc.stdout, stdout_cb))
        stderr_task = asyncio.create_task(pump(proc.stderr, stderr_cb))
        return _SSHRunHandle(conn=conn, process=proc, stdout_task=stdout_task, stderr_task=stderr_task)

    async def exec_capture(self, argv: list[str], cwd: Optional[str]) -> ExecResult:
        cmd = " ".join(_shell_quote(a) for a in argv)
        if cwd:
            cmd = f"cd {_shell_quote(cwd)} && {cmd}"
        async with await self._connect() as conn:
            res = await conn.run(cmd, check=False)
            return ExecResult(exit_code=int(res.exit_status or 0), stdout=res.stdout or "", stderr=res.stderr or "")

    async def read_text(self, path: str) -> str:
        async with await self._connect() as conn:
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(path, "r") as f:
                    data = await f.read()
                    return data

    async def write_text(self, path: str, content: str, overwrite: bool) -> None:
        async with await self._connect() as conn:
            async with conn.start_sftp_client() as sftp:
                if not overwrite:
                    try:
                        await sftp.stat(path)
                    except FileNotFoundError:
                        pass
                    else:
                        raise FileExistsError(path)
                parent = os.path.dirname(path) or "."
                try:
                    await sftp.mkdir(parent)
                except Exception:
                    pass
                async with sftp.open(path, "w") as f:
                    await f.write(content)

    async def list_glob(self, pattern: str) -> list[str]:
        code = (
            "import glob, os, sys; "
            "pat=os.path.expanduser(sys.argv[1]); "
            "print('\\n'.join(sorted(glob.glob(pat, recursive=True))))"
        )
        res = await self.exec_capture(["python3", "-c", code, pattern], cwd=None)
        if res.exit_code != 0:
            return []
        return [line for line in res.stdout.splitlines() if line.strip()]

    async def realpath(self, path: str) -> str:
        # Use remote python for robust tilde expansion + symlink resolution.
        code = (
            "import os,sys; "
            "print(os.path.realpath(os.path.expanduser(sys.argv[1])))"
        )
        res = await self.exec_capture(["python3", "-c", code, path], cwd=None)
        if res.exit_code != 0:
            raise RuntimeError(res.stderr.strip() or "realpath failed")
        return res.stdout.strip()


def _shell_quote(s: str) -> str:
    # Minimal POSIX shell quoting.
    if s == "":
        return "''"
    if all(c.isalnum() or c in "._-/:=@" for c in s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"
