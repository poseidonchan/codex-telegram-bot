from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from tgcodex.machines.base import RunHandle


JsonObject = dict[str, Any]
RequestId = str | int


class JsonRpcError(RuntimeError):
    def __init__(self, *, error: Any) -> None:
        super().__init__(str(error))
        self.error = error


class JsonLineBuffer:
    """
    Incremental newline-delimited JSON buffer.

    Codex app-server communicates over stdio using one JSON object per line.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        self._buf += chunk
        out: list[str] = []
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                return out
            line = self._buf[:idx].decode("utf-8", "replace")
            del self._buf[: idx + 1]
            line = line.strip()
            if line:
                out.append(line)


@dataclass(frozen=True)
class JsonRpcIncoming:
    obj: JsonObject

    @property
    def id(self) -> Optional[RequestId]:
        rid = self.obj.get("id")
        return rid if isinstance(rid, (str, int)) else None

    @property
    def method(self) -> Optional[str]:
        m = self.obj.get("method")
        return m if isinstance(m, str) else None

    @property
    def params(self) -> Optional[JsonObject]:
        p = self.obj.get("params")
        return p if isinstance(p, dict) else None

    @property
    def is_response(self) -> bool:
        return self.id is not None and self.method is None

    @property
    def is_request(self) -> bool:
        return self.id is not None and self.method is not None

    @property
    def is_notification(self) -> bool:
        return self.id is None and self.method is not None


class JsonRpcConnection:
    """
    Minimal JSON-RPC-like helper for Codex app-server.

    Codex app-server uses newline-delimited JSON objects (no `jsonrpc` field).
    Requests from the client include: {id, method, params}
    Responses include: {id, result} or {id, error}
    Server requests/notifications include: {id?, method, params}
    """

    def __init__(
        self,
        *,
        handle: RunHandle,
        on_server_request: Callable[[JsonRpcIncoming], Awaitable[None]],
        on_notification: Callable[[JsonRpcIncoming], Awaitable[None]],
        on_log: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self._handle = handle
        self._on_server_request = on_server_request
        self._on_notification = on_notification
        self._on_log = on_log

        self._next_id = 1
        self._pending: dict[RequestId, asyncio.Future[Any]] = {}
        self._closed = False

        self._stdout = JsonLineBuffer()
        self._stderr = JsonLineBuffer()

    async def close(self) -> None:
        self._closed = True
        # Fail any pending requests so callers don't hang on shutdown.
        for _rid, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(JsonRpcError(error="connection closed"))
        self._pending.clear()

    async def write_obj(self, obj: JsonObject) -> None:
        if self._closed:
            raise JsonRpcError(error="connection closed")
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
        await self._handle.write_stdin(data)

    async def request(self, *, method: str, params: JsonObject) -> Any:
        rid: RequestId = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[rid] = fut
        await self.write_obj({"id": rid, "method": method, "params": params})
        return await fut

    async def respond(self, *, request_id: RequestId, result: JsonObject) -> None:
        await self.write_obj({"id": request_id, "result": result})

    async def feed_stdout(self, chunk: bytes) -> None:
        for line in self._stdout.feed(chunk):
            try:
                obj = json.loads(line)
            except Exception:
                if self._on_log is not None:
                    await self._on_log(line)
                continue
            if not isinstance(obj, dict):
                continue
            await self._handle_incoming(JsonRpcIncoming(obj=obj))

    async def feed_stderr(self, chunk: bytes) -> None:
        for line in self._stderr.feed(chunk):
            if self._on_log is not None:
                await self._on_log(line)

    async def _handle_incoming(self, incoming: JsonRpcIncoming) -> None:
        if incoming.is_response:
            rid = incoming.id
            assert rid is not None
            fut = self._pending.pop(rid, None)
            if fut is None or fut.done():
                return
            if "error" in incoming.obj and incoming.obj["error"] is not None:
                fut.set_exception(JsonRpcError(error=incoming.obj.get("error")))
                return
            fut.set_result(incoming.obj.get("result"))
            return

        if incoming.is_request:
            await self._on_server_request(incoming)
            return

        if incoming.is_notification:
            await self._on_notification(incoming)
            return

        # Unknown / log-like object.
        if self._on_log is not None:
            await self._on_log(json.dumps(incoming.obj))

