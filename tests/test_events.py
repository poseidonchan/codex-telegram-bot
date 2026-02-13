import unittest

from tgcodex.codex.events import (
    ExecApprovalRequest,
    ExecCommandEnd,
    ThreadStarted,
    TokenCount,
    ToolStarted,
    parse_event_obj,
)


class TestEventParsing(unittest.TestCase):
    def test_session_meta_maps_to_thread_started(self) -> None:
        obj = {
            "type": "session_meta",
            "payload": {
                "id": "019c0000-0000-0000-0000-000000000000",
            },
        }
        evs = parse_event_obj(obj)
        self.assertEqual(len(evs), 1)
        self.assertIsInstance(evs[0], ThreadStarted)
        self.assertEqual(evs[0].thread_id, "019c0000-0000-0000-0000-000000000000")

    def test_exec_approval_request(self) -> None:
        obj = {
            "type": "exec_approval_request",
            "command": "rm -rf foo",
            "cwd": "/tmp",
            "reason": "test",
        }
        evs = parse_event_obj(obj)
        self.assertEqual(len(evs), 1)
        self.assertIsInstance(evs[0], ExecApprovalRequest)
        self.assertEqual(evs[0].command, "rm -rf foo")

    def test_item_started_exec_approval_request(self) -> None:
        obj = {
            "type": "item.started",
            "item": {
                "type": "exec_approval_request",
                "command": ["rm", "-rf", "foo"],
                "cwd": "/tmp",
                "reason": "test",
            },
        }
        evs = parse_event_obj(obj)
        self.assertEqual(len(evs), 1)
        self.assertIsInstance(evs[0], ExecApprovalRequest)
        self.assertEqual(evs[0].command, "rm -rf foo")
        self.assertEqual(evs[0].cwd, "/tmp")
        self.assertEqual(evs[0].reason, "test")
        self.assertIsNone(evs[0].call_id)

    def test_token_count(self) -> None:
        obj = {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 8,
                        "output_tokens": 2,
                        "total_tokens": 123,
                    },
                    "last_token_usage": {
                        "input_tokens": 11,
                        "cached_input_tokens": 9,
                        "output_tokens": 3,
                        "total_tokens": 124,
                    },
                    "model_context_window": 1000,
                },
                "rate_limits": {
                    "primary": {"used_percent": 5.0, "window_minutes": 300, "resets_at": 1700000000},
                    "secondary": {"used_percent": 7.0, "window_minutes": 10080, "resets_at": 1700001000},
                },
            },
        }
        evs = parse_event_obj(obj)
        self.assertEqual(len(evs), 1)
        self.assertIsInstance(evs[0], TokenCount)
        # Prefer per-turn usage when present.
        self.assertEqual(evs[0].total_tokens, 124)
        self.assertEqual(evs[0].model_context_window, 1000)
        self.assertEqual(evs[0].input_tokens, 11)
        self.assertEqual(evs[0].cached_input_tokens, 9)
        self.assertEqual(evs[0].output_tokens, 3)
        self.assertEqual(evs[0].primary_used_percent, 5.0)

    def test_function_call_exec_command_maps_to_tool_started(self) -> None:
        obj = {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": "{\"cmd\":\"ls -la\"}",
                "call_id": "call_abc123",
            },
        }
        evs = parse_event_obj(obj)
        self.assertEqual(len(evs), 1)
        self.assertIsInstance(evs[0], ToolStarted)
        self.assertEqual(evs[0].command, "ls -la")

    def test_function_call_exec_command_with_escalation_maps_to_exec_approval_request(self) -> None:
        obj = {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": "{\"cmd\":\"rm -rf foo\",\"sandbox_permissions\":\"require_escalated\",\"justification\":\"delete foo\"}",
                "call_id": "call_abc123",
            },
        }
        evs = parse_event_obj(obj)
        self.assertEqual(len(evs), 1)
        self.assertIsInstance(evs[0], ExecApprovalRequest)
        self.assertEqual(evs[0].command, "rm -rf foo")
        self.assertEqual(evs[0].reason, "delete foo")
        self.assertEqual(evs[0].call_id, "call_abc123")

    def test_proto_envelope_exec_approval_request(self) -> None:
        obj = {
            "id": "0",
            "msg": {
                "type": "exec_approval_request",
                "call_id": "call_123",
                "command": ["bash", "-lc", "rm -f README.md"],
                "cwd": "/tmp",
                "reason": "test",
            },
        }
        evs = parse_event_obj(obj)
        self.assertEqual(len(evs), 1)
        self.assertIsInstance(evs[0], ExecApprovalRequest)
        self.assertEqual(evs[0].call_id, "call_123")
        self.assertEqual(evs[0].outer_id, "0")
        self.assertIn("rm -f README.md", evs[0].command)

    def test_function_call_output_maps_to_exec_command_end(self) -> None:
        obj = {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_abc123",
                "output": "ok",
            },
        }
        evs = parse_event_obj(obj)
        self.assertEqual(len(evs), 1)
        self.assertIsInstance(evs[0], ExecCommandEnd)
        self.assertIsNone(evs[0].exit_code)
        self.assertEqual(evs[0].aggregated_output, "ok")
