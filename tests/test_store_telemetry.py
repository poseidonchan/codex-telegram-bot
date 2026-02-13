import os
import tempfile
import unittest

from tgcodex.codex.events import TokenCount
from tgcodex.state.store import Store


class TestStoreTokenTelemetry(unittest.TestCase):
    def test_update_token_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "state.sqlite3")
            store = Store(db_path)
            store.open()
            try:
                store.ensure_chat_state(
                    chat_id=1,
                    default_machine="local",
                    default_workdir="/tmp",
                    default_approval_policy="untrusted",
                    default_model=None,
                )
                ev = TokenCount(
                    model_context_window=1000,
                    total_tokens=123,
                    input_tokens=10,
                    output_tokens=5,
                    cached_input_tokens=2,
                    reasoning_output_tokens=None,
                    primary_used_percent=1.5,
                    primary_window_minutes=300,
                    primary_resets_at=1700000000,
                    secondary_used_percent=7.0,
                    secondary_window_minutes=10080,
                    secondary_resets_at=1700001000,
                    raw={},
                )
                store.update_token_telemetry(1, token=ev)
                state = store.get_chat_state(1)
                assert state is not None
                self.assertEqual(state.last_total_tokens, 123)
                self.assertEqual(state.last_context_window, 1000)
                self.assertEqual(state.last_context_remaining, 877)
                self.assertEqual(state.last_input_tokens, 10)
                self.assertEqual(state.last_output_tokens, 5)
                self.assertEqual(state.last_cached_tokens, 2)
                self.assertEqual(state.rate_primary_used_percent, 1.5)
                self.assertEqual(state.rate_primary_window_minutes, 300)
                self.assertEqual(state.rate_primary_resets_at, 1700000000)
                self.assertEqual(state.rate_secondary_used_percent, 7.0)
                self.assertEqual(state.rate_secondary_window_minutes, 10080)
                self.assertEqual(state.rate_secondary_resets_at, 1700001000)
            finally:
                store.close()

