import os
import unittest


class TestDaemonProcess(unittest.TestCase):
    def test_is_pid_running_self_pid_true(self) -> None:
        from tgcodex import daemon

        self.assertTrue(daemon.is_pid_running(os.getpid()))
