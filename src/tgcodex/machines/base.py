from __future__ import annotations

from dataclasses import dataclass
from typing import (
    AsyncIterator,
    Awaitable,
    Callable,
    Literal,
    Optional,
    Protocol,
)


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


class RunHandle(Protocol):
    async def wait(self) -> int: ...

    async def terminate(self) -> None: ...

    async def kill(self) -> None: ...

    async def write_stdin(self, data: bytes) -> None: ...

    async def close_stdin(self) -> None: ...


class Machine(Protocol):
    name: str
    type: Literal["local", "ssh"]

    async def run(
        self,
        argv: list[str],
        cwd: Optional[str],
        env: Optional[dict[str, str]],
        pty: bool,
        stdout_cb: Callable[[bytes], Awaitable[None]],
        stderr_cb: Callable[[bytes], Awaitable[None]],
        stdin_provider: Optional[AsyncIterator[bytes]],
    ) -> RunHandle: ...

    async def exec_capture(self, argv: list[str], cwd: Optional[str]) -> ExecResult: ...

    async def read_text(self, path: str) -> str: ...

    async def write_text(self, path: str, content: str, overwrite: bool) -> None: ...

    async def list_glob(self, pattern: str) -> list[str]: ...

    async def realpath(self, path: str) -> str: ...

