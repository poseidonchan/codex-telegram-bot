import unittest

from tgcodex.codex.sessions import extract_latest_token_count, read_latest_token_count
from tgcodex.machines.base import ExecResult


class TestSessionTokenCountExtraction(unittest.TestCase):
    def test_extract_latest_token_count(self) -> None:
        text = "\n".join(
            [
                '{"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"total_tokens":1},"model_context_window":10},"rate_limits":{"primary":{"used_percent":1.0,"window_minutes":300,"resets_at":1700}}}}',
                '{"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"total_tokens":2},"model_context_window":10},"rate_limits":{"primary":{"used_percent":2.0,"window_minutes":300,"resets_at":1701}}}}',
            ]
        )
        ev = extract_latest_token_count(text)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev.total_tokens, 2)
        self.assertEqual(ev.model_context_window, 10)
        self.assertEqual(ev.primary_used_percent, 2.0)


class _FakeMachine:
    type = "local"

    def __init__(self, *, stdout: str) -> None:
        self._stdout = stdout
        self.read_called = False
        self.exec_called = False

    async def realpath(self, path: str) -> str:  # type: ignore[no-untyped-def]
        return "/tmp/.codex/sessions"

    async def list_glob(self, pattern: str) -> list[str]:  # type: ignore[no-untyped-def]
        return ["/tmp/.codex/sessions/rollout-2026-02-10T00-00-00-019c0000-0000-0000-0000-000000000000.jsonl"]

    async def exec_capture(self, argv: list[str], cwd: str | None) -> ExecResult:  # type: ignore[no-untyped-def]
        self.exec_called = True
        return ExecResult(exit_code=0, stdout=self._stdout, stderr="")

    async def read_text(self, path: str) -> str:  # type: ignore[no-untyped-def]
        self.read_called = True
        return ""


class TestSessionTokenCountReading(unittest.IsolatedAsyncioTestCase):
    async def test_read_latest_token_count_uses_tail_fast_path(self) -> None:
        stdout = "\n".join(
            [
                '{"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"total_tokens":1},"model_context_window":10},"rate_limits":{"primary":{"used_percent":1.0,"window_minutes":300,"resets_at":1700}}}}',
                '{"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"total_tokens":2},"model_context_window":10},"rate_limits":{"primary":{"used_percent":2.0,"window_minutes":300,"resets_at":1701}}}}',
            ]
        )
        m = _FakeMachine(stdout=stdout)
        ev = await read_latest_token_count(m, session_id="019c0000-0000-0000-0000-000000000000")
        self.assertTrue(m.exec_called)
        self.assertFalse(m.read_called)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev.total_tokens, 2)
