import unittest

from tgcodex.bot.formatting import fmt_status
from tgcodex.state.models import ChatState


class TestFormatting(unittest.TestCase):
    def test_status_includes_context_percent(self) -> None:
        state = ChatState(
            chat_id=1,
            machine_name="local",
            workdir="/tmp",
            active_session_id="sid",
            session_title="t",
            approval_policy="untrusted",
            approval_mode="on-request",
            sandbox_mode=None,
            model=None,
            thinking_level=None,
            show_reasoning=False,
            plan_mode=False,
            last_input_tokens=None,
            last_output_tokens=None,
            last_cached_tokens=None,
            last_total_tokens=123,
            last_context_window=1000,
            last_context_remaining=250,
            rate_primary_used_percent=None,
            rate_primary_window_minutes=None,
            rate_primary_resets_at=None,
            rate_secondary_used_percent=None,
            rate_secondary_window_minutes=None,
            rate_secondary_resets_at=None,
            updated_at=0,
        )
        out = fmt_status(state, run=None)
        self.assertIn("Context remaining", out)
        self.assertIn("250 / 1,000", out)
        self.assertIn("25.0%", out)
