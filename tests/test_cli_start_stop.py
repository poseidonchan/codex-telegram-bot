import os
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

try:
    from typer.testing import CliRunner  # type: ignore

    try:
        from tgcodex.cli import app
    except SystemExit:
        # tgcodex.cli intentionally exits when optional CLI deps are missing.
        app = None  # type: ignore[assignment]
except Exception:
    CliRunner = None  # type: ignore[misc,assignment]
    app = None  # type: ignore[assignment]


def _write_min_config(path: Path) -> None:
    # Keep this YAML minimal but valid for validate_config().
    path.write_text(
        "\n".join(
            [
                "telegram:",
                "  token_env: TELEGRAM_BOT_TOKEN",
                "  allowed_user_ids: [1]",
                "",
                "state:",
                "  db_path: tgcodex.sqlite3",
                "",
                "codex:",
                "  bin: codex",
                "  args: []",
                "  model: null",
                "  sandbox: workspace-write",
                "  approval_policy: untrusted",
                "  skip_git_repo_check: true",
                "",
                "output:",
                "  flush_interval_ms: 250",
                "  min_flush_chars: 120",
                "  max_flush_delay_seconds: 2.0",
                "  max_chars: 3500",
                "  truncate: true",
                "  typing_interval_seconds: 4.0",
                "  show_codex_logs: false",
                "  show_tool_output: false",
                "  max_tool_output_chars: 1200",
                "",
                "approvals:",
                "  prefix_tokens: 2",
                "",
                "machines:",
                "  default: local",
                "  defs:",
                "    local:",
                "      type: local",
                "      default_workdir: /tmp",
                "      allowed_roots: [/tmp]",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@unittest.skipIf(CliRunner is None or app is None, "CLI deps (typer/PyYAML) not installed")
class TestStartStopStatus(unittest.TestCase):
    def test_start_writes_pid_file(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg_path = td_path / "config.yaml"
            _write_min_config(cfg_path)

            os.environ["TELEGRAM_BOT_TOKEN"] = "dummy"

            class _FakeProc:
                pid = 4242

            with patch("tgcodex.daemon.subprocess.Popen", return_value=_FakeProc()):
                with patch("tgcodex.daemon.pid_file_matches_running_process", return_value=True):
                    res = runner.invoke(app, ["start", "--config", str(cfg_path)])
            self.assertEqual(res.exit_code, 0, res.output)

            pid_file = td_path / ".tgcodex-bot" / "config.yaml.pid"
            self.assertTrue(pid_file.exists())
            payload = json.loads(pid_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["pid"], 4242)

    def test_start_fails_if_process_dies_immediately(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg_path = td_path / "config.yaml"
            _write_min_config(cfg_path)

            os.environ["TELEGRAM_BOT_TOKEN"] = "dummy"

            with patch("tgcodex.daemon.start_detached", return_value=4242):
                with patch("tgcodex.daemon.pid_file_matches_running_process", return_value=False):
                    res = runner.invoke(app, ["start", "--config", str(cfg_path)])
            self.assertNotEqual(res.exit_code, 0, res.output)
            self.assertIn("failed", res.output.lower())

    def test_start_waits_for_stable_health_before_success(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg_path = td_path / "config.yaml"
            _write_min_config(cfg_path)

            os.environ["TELEGRAM_BOT_TOKEN"] = "dummy"

            with patch("tgcodex.daemon.start_detached", return_value=4242):
                with patch(
                    "tgcodex.daemon.pid_file_matches_running_process",
                    side_effect=[True, False, False, False, False],
                ):
                    with patch("tgcodex.cli.time.monotonic", side_effect=[0.0, 0.1, 0.2, 0.3, 1.1]):
                        with patch("tgcodex.cli.time.sleep", return_value=None):
                            res = runner.invoke(app, ["start", "--config", str(cfg_path)])

            self.assertNotEqual(res.exit_code, 0, res.output)
            self.assertIn("failed", res.output.lower())
