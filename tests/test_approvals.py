import unittest

from tgcodex.codex.approvals import command_prefix, should_prompt_for_approval


class TestApprovals(unittest.TestCase):
    def test_command_prefix(self) -> None:
        self.assertEqual(command_prefix("git status -sb", prefix_tokens=2), "git status")

    def test_should_prompt_untrusted(self) -> None:
        needs, prefix = should_prompt_for_approval(
            approval_policy="untrusted",
            command="git status",
            trusted_prefixes=[],
            prefix_tokens=2,
        )
        self.assertTrue(needs)
        self.assertEqual(prefix, "git status")

        needs2, _ = should_prompt_for_approval(
            approval_policy="untrusted",
            command="git status",
            trusted_prefixes=["git status"],
            prefix_tokens=2,
        )
        self.assertTrue(needs2)

    def test_should_prompt_on_request_respects_trusted_prefix(self) -> None:
        needs, _ = should_prompt_for_approval(
            approval_policy="on-request",
            command="git status",
            trusted_prefixes=[],
            prefix_tokens=2,
        )
        self.assertTrue(needs)

        needs2, _ = should_prompt_for_approval(
            approval_policy="on-request",
            command="git status",
            trusted_prefixes=["git status"],
            prefix_tokens=2,
        )
        self.assertFalse(needs2)
