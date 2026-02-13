import json
import unittest

from tgcodex.codex.cli_runner import CodexRun


class _FakeHandle:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    async def write_stdin(self, data: bytes) -> None:
        self.writes.append(data)


class TestSendExecApproval(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_format_when_no_call_id(self) -> None:
        run = CodexRun(machine=None, handle=_FakeHandle())  # type: ignore[arg-type]
        await run.send_exec_approval(decision="approved")
        self.assertEqual(len(run.handle.writes), 1)  # type: ignore[attr-defined]
        payload = json.loads(run.handle.writes[0].decode("utf-8"))  # type: ignore[attr-defined]
        self.assertEqual(payload["type"], "exec_approval")
        self.assertEqual(payload["decision"], "approved")

    async def test_proto_op_format_when_call_id_present(self) -> None:
        run = CodexRun(machine=None, handle=_FakeHandle())  # type: ignore[arg-type]
        await run.send_exec_approval(decision="denied", call_id="call_123")
        self.assertEqual(len(run.handle.writes), 1)  # type: ignore[attr-defined]
        payload = json.loads(run.handle.writes[0].decode("utf-8"))  # type: ignore[attr-defined]
        self.assertIn("id", payload)
        self.assertIn("op", payload)
        self.assertEqual(payload["op"]["type"], "exec_approval")
        self.assertEqual(payload["op"]["approved"], False)
        self.assertEqual(payload["op"]["call_id"], "call_123")

