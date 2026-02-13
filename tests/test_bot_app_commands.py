import unittest

try:
    import telegram  # type: ignore  # noqa: F401
except Exception:  # pragma: no cover
    telegram = None


class _FakeBot:
    def __init__(self) -> None:
        self.commands = None

    async def set_my_commands(self, commands):  # type: ignore[no-untyped-def]
        self.commands = commands


class TestBotAppCommands(unittest.IsolatedAsyncioTestCase):
    def test_default_command_specs_excludes_backend(self) -> None:
        from tgcodex.bot.app import default_bot_command_specs

        cmds = [c for c, _ in default_bot_command_specs()]
        self.assertNotIn("backend", cmds)
        self.assertIn("start", cmds)
        self.assertIn("menu", cmds)

    @unittest.skipIf(telegram is None, "python-telegram-bot not installed")
    async def test_ensure_bot_commands_sets_my_commands(self) -> None:
        from tgcodex.bot.app import ensure_bot_commands

        bot = _FakeBot()
        await ensure_bot_commands(bot)

        self.assertIsNotNone(bot.commands)
        names = [c.command for c in bot.commands]
        self.assertNotIn("backend", names)
