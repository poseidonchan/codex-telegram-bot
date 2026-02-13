from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from tgcodex.util.time import monotonic


@dataclass
class OutputTuning:
    flush_interval_ms: int
    min_flush_chars: int
    max_flush_delay_seconds: float
    max_chars: int
    typing_interval_seconds: float


class BufferedTelegramWriter:
    """
    Buffered writer that edits a single Telegram message until it reaches max_chars, then rolls
    over to a new message.
    """

    def __init__(self, *, bot: Any, chat_id: int, tuning: OutputTuning) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._tuning = tuning

        self._msg_id: Optional[int] = None
        self._current = ""
        self._buffer = ""
        self._last_flush = monotonic()

    def append(self, text: str) -> None:
        self._buffer += text

    def needs_flush(self) -> bool:
        if not self._buffer:
            return False
        now = monotonic()
        if len(self._buffer) >= self._tuning.min_flush_chars:
            return True
        if (now - self._last_flush) * 1000.0 >= self._tuning.flush_interval_ms:
            return True
        if (now - self._last_flush) >= self._tuning.max_flush_delay_seconds:
            return True
        return False

    async def flush(self, *, force: bool = False) -> None:
        if not self._buffer and not force:
            return
        if not self._buffer and force:
            self._last_flush = monotonic()
            return

        while self._buffer:
            space = self._tuning.max_chars - len(self._current)
            if space <= 0:
                # Start a new message.
                self._msg_id = None
                self._current = ""
                space = self._tuning.max_chars

            take = self._buffer[:space]
            self._buffer = self._buffer[space:]
            self._current += take
            await self._send_or_edit(self._current)

        self._last_flush = monotonic()

    def has_content(self) -> bool:
        """Returns True if any content has been sent to Telegram (a message was created)."""
        return self._msg_id is not None

    async def close(self) -> None:
        await self.flush(force=True)

    async def _send_or_edit(self, text: str) -> None:
        # Send plain text (no HTML/Markdown parse mode) so chat output doesn't show up as code.
        payload = text.replace("```", "")
        if self._msg_id is None:
            msg = await self._bot.send_message(
                chat_id=self._chat_id,
                text=payload,
                disable_web_page_preview=True,
            )
            self._msg_id = msg.message_id
        else:
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._msg_id,
                    text=payload,
                    disable_web_page_preview=True,
                )
            except Exception:
                # Editing can fail if Telegram thinks it is "not modified" or if message is too old.
                msg = await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=payload,
                    disable_web_page_preview=True,
                )
                self._msg_id = msg.message_id


async def typing_loop(*, bot: Any, chat_id: int, interval_seconds: float, stop: asyncio.Event) -> None:
    try:
        while not stop.is_set():
            try:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        return
