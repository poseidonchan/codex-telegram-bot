import unittest

from tgcodex.bot.sessions_ui import derive_session_title, format_resume_label


class TestSessionsUI(unittest.TestCase):
    def test_derive_session_title_normalizes_and_truncates(self) -> None:
        self.assertEqual(derive_session_title("  hello   world  "), "hello world")
        self.assertEqual(derive_session_title(""), "Untitled")

        long = "x" * 200
        t = derive_session_title(long, max_len=20)
        self.assertLessEqual(len(t), 20)
        self.assertTrue(t.endswith("..."))

    def test_format_resume_label_uses_title_or_untitled(self) -> None:
        now = 2000
        self.assertEqual(format_resume_label(title="My session", updated_at=None, now_ts=now), "My session")

        # Relative age suffix makes unlabeled sessions human-readable without exposing IDs.
        label = format_resume_label(title=None, updated_at=now - 3600, now_ts=now)
        self.assertIn("Untitled", label)
        self.assertIn("1h ago", label)

