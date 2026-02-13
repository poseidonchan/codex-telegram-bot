import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestDaemonStop(unittest.TestCase):
    def test_stop_missing_pid_file_returns_false(self) -> None:
        from tgcodex import daemon

        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "missing.pid"
            self.assertFalse(daemon.stop(pid_file, timeout_seconds=0.0))

    def test_stop_removes_stale_pid_file(self) -> None:
        from tgcodex import daemon

        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "bot.pid"
            pid_file.write_text("123\n", encoding="utf-8")

            with patch("tgcodex.daemon.is_pid_running", return_value=False):
                stopped = daemon.stop(pid_file, timeout_seconds=0.0)

            self.assertFalse(stopped)
            self.assertFalse(pid_file.exists())

    def test_stop_sends_sigterm_and_removes_pid_on_success(self) -> None:
        from tgcodex import daemon

        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "bot.pid"
            pid_file.write_text("123\n", encoding="utf-8")

            with patch("tgcodex.daemon.os.kill") as kill:
                with patch(
                    "tgcodex.daemon._read_proc_cmdline",
                    return_value=("python", "-m", "tgcodex.cli", "run"),
                ):
                    with patch(
                        "tgcodex.daemon.is_pid_running",
                        side_effect=[True, False],
                    ):
                        stopped = daemon.stop(pid_file, timeout_seconds=0.0)

            self.assertTrue(stopped)
            kill.assert_called_once_with(123, signal.SIGTERM)
            self.assertFalse(pid_file.exists())

    def test_stop_escalates_to_sigkill_if_sigterm_does_not_stop_in_time(self) -> None:
        from tgcodex import daemon

        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "bot.pid"
            pid_file.write_text("123\n", encoding="utf-8")

            with patch("tgcodex.daemon.os.kill") as kill:
                with patch(
                    "tgcodex.daemon._read_proc_cmdline",
                    return_value=("python", "-m", "tgcodex.cli", "run"),
                ):
                    with patch(
                        "tgcodex.daemon.is_pid_running",
                        side_effect=[True, True, False],
                    ):
                        stopped = daemon.stop(pid_file, timeout_seconds=0.0)

            self.assertTrue(stopped)
            self.assertEqual(
                kill.call_args_list,
                [((123, signal.SIGTERM),), ((123, signal.SIGKILL),)],
            )
            self.assertFalse(pid_file.exists())

    def test_stop_refuses_to_kill_unexpected_process(self) -> None:
        from tgcodex import daemon

        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "bot.pid"
            pid_file.write_text("123\n", encoding="utf-8")

            with patch("tgcodex.daemon.is_pid_running", return_value=True):
                with patch("tgcodex.daemon._is_expected_process", return_value=False):
                    with patch("tgcodex.daemon.os.kill") as kill:
                        stopped = daemon.stop(pid_file, timeout_seconds=0.0)

            self.assertFalse(stopped)
            kill.assert_not_called()
            self.assertFalse(pid_file.exists())

    def test_stop_refuses_when_identity_cannot_be_verified(self) -> None:
        from tgcodex import daemon

        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "bot.pid"
            pid_file.write_text("123\n", encoding="utf-8")

            with patch("tgcodex.daemon.is_pid_running", return_value=True):
                with patch("tgcodex.daemon._read_proc_cmdline", return_value=None):
                    with patch("tgcodex.daemon._read_proc_start_time", return_value=None):
                        with patch("tgcodex.daemon.os.kill") as kill:
                            stopped = daemon.stop(pid_file, timeout_seconds=0.0)

            self.assertFalse(stopped)
            kill.assert_not_called()
