import unittest

from tgcodex.bot.output_stream import BufferedTelegramWriter, OutputTuning


class _Msg:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edited: list[str] = []
        self._next_id = 1
        self.edit_should_fail = False

    async def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        self.sent.append(kwargs["text"])
        mid = self._next_id
        self._next_id += 1
        return _Msg(mid)

    async def edit_message_text(self, **kwargs):  # type: ignore[no-untyped-def]
        if self.edit_should_fail:
            raise Exception("Message edit failed (simulated Telegram API error)")
        self.edited.append(kwargs["text"])


class TestBufferedTelegramWriter(unittest.IsolatedAsyncioTestCase):
    async def test_rollover(self) -> None:
        bot = _FakeBot()
        w = BufferedTelegramWriter(
            bot=bot,
            chat_id=1,
            tuning=OutputTuning(
                flush_interval_ms=0,
                min_flush_chars=1,
                max_flush_delay_seconds=0,
                max_chars=5,
                typing_interval_seconds=1.0,
            ),
        )
        w.append("abcdef")
        await w.flush()
        self.assertEqual(len(bot.sent), 2)
        self.assertIn("abcde", bot.sent[0])
        self.assertIn("f", bot.sent[1])

    async def test_edits_before_rollover(self) -> None:
        bot = _FakeBot()
        w = BufferedTelegramWriter(
            bot=bot,
            chat_id=1,
            tuning=OutputTuning(
                flush_interval_ms=0,
                min_flush_chars=1,
                max_flush_delay_seconds=0,
                max_chars=10,
                typing_interval_seconds=1.0,
            ),
        )
        w.append("hi")
        await w.flush()
        w.append(" there")
        await w.flush()
        self.assertEqual(len(bot.sent), 1)
        self.assertGreaterEqual(len(bot.edited), 1)

    async def test_edit_failure_falls_back_to_new_message(self) -> None:
        """Test that when edit_message_text fails, we fall back to sending a new message."""
        bot = _FakeBot()
        w = BufferedTelegramWriter(
            bot=bot,
            chat_id=1,
            tuning=OutputTuning(
                flush_interval_ms=0,
                min_flush_chars=1,
                max_flush_delay_seconds=0,
                max_chars=100,
                typing_interval_seconds=1.0,
            ),
        )
        # Send initial message
        w.append("first message")
        await w.flush()
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(len(bot.edited), 0)

        # Now simulate edit failure (e.g., Telegram API rate limit or message deleted)
        bot.edit_should_fail = True

        # Try to append more content - should try to edit but fall back to new message
        w.append(" - updated content")
        await w.flush()

        # Should have sent a new message instead of editing
        self.assertEqual(len(bot.sent), 2, "Should have sent a second message when edit failed")
        self.assertIn("first message - updated content", bot.sent[1])
