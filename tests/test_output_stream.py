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

    async def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        self.sent.append(kwargs["text"])
        mid = self._next_id
        self._next_id += 1
        return _Msg(mid)

    async def edit_message_text(self, **kwargs):  # type: ignore[no-untyped-def]
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
