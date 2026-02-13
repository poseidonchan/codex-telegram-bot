import tempfile
import unittest
from pathlib import Path


class TestDaemonPaths(unittest.TestCase):
    def test_runtime_paths_are_derived_from_config_dir(self) -> None:
        from tgcodex import daemon

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            self.assertEqual(
                daemon.runtime_dir_for_config(cfg),
                Path(td) / ".tgcodex-bot",
            )
            self.assertEqual(
                daemon.pid_file_for_config(cfg),
                Path(td) / ".tgcodex-bot" / "config.yaml.pid",
            )
            self.assertEqual(
                daemon.log_file_for_config(cfg),
                Path(td) / ".tgcodex-bot" / "config.yaml.log",
            )

    def test_read_pid_missing_returns_none(self) -> None:
        from tgcodex import daemon

        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.pid"
            self.assertIsNone(daemon.read_pid(missing))
