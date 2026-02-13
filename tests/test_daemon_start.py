import subprocess
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch


class TestDaemonStart(unittest.TestCase):
    def test_start_detached_writes_pid_file(self) -> None:
        from tgcodex import daemon

        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / ".tgcodex-bot" / "config.yaml.pid"
            log_file = Path(td) / ".tgcodex-bot" / "config.yaml.log"

            class _FakeProc:
                pid = 4242

            with patch("tgcodex.daemon.subprocess.Popen", return_value=_FakeProc()) as popen:
                pid = daemon.start_detached(
                    ["python3", "-c", "print('hi')"],
                    pid_file=pid_file,
                    log_file=log_file,
                    env={"X": "1"},
                    cwd="/tmp",
                )

            self.assertEqual(pid, 4242)
            self.assertTrue(pid_file.exists())
            payload = json.loads(pid_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["pid"], 4242)
            self.assertTrue(log_file.exists())

            # Ensure we detach and route output to the log.
            kwargs = popen.call_args.kwargs
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
            self.assertTrue(kwargs["start_new_session"])
            self.assertTrue(kwargs["close_fds"])
            self.assertEqual(kwargs["cwd"], "/tmp")
