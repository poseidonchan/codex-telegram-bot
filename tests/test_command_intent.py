import unittest

from tgcodex.codex.command_intent import needs_write_approval


class TestCommandIntent(unittest.TestCase):
    def test_needs_write_approval_unwraps_bash_lc(self) -> None:
        self.assertTrue(needs_write_approval("/bin/bash -lc 'cd /tmp && mkdir -p foo'"))

    def test_needs_write_approval_bash_lc_readonly(self) -> None:
        self.assertFalse(needs_write_approval("bash -lc 'cd /tmp && ls -la'"))

