import tempfile
import unittest
from pathlib import Path

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


@unittest.skipIf(CliRunner is None or app is None, "CLI deps (typer/PyYAML) not installed")
class TestSetupWizard(unittest.TestCase):
    def test_setup_writes_config_yaml(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yaml"

            # Accept default token env var, set allowed user IDs, set workdir and
            # allow roots, skip SSH machine setup.
            user_input = "\n".join(
                [
                    "",  # telegram.token_env (default)
                    "12345",  # telegram.allowed_user_ids
                    "",  # state.db_path (default)
                    "",  # codex.bin (default)
                    "/tmp",  # machines.local.default_workdir
                    "/tmp,/var/tmp",  # machines.local.allowed_roots
                    "n",  # add ssh machine?
                ]
            )
            res = runner.invoke(
                app, ["setup", "--config", str(cfg_path)], input=user_input + "\n"
            )
            self.assertEqual(res.exit_code, 0, res.output)

            import yaml  # type: ignore

            obj = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(obj["telegram"]["token_env"], "TELEGRAM_BOT_TOKEN")
            self.assertEqual(obj["telegram"]["allowed_user_ids"], [12345])
            self.assertEqual(obj["state"]["db_path"], "tgcodex.sqlite3")
            self.assertEqual(obj["codex"]["bin"], "codex")
            self.assertEqual(obj["machines"]["default"], "local")
            self.assertEqual(obj["machines"]["defs"]["local"]["default_workdir"], "/tmp")
            self.assertEqual(obj["machines"]["defs"]["local"]["allowed_roots"], ["/tmp", "/var/tmp"])

    def test_setup_refuses_to_overwrite_without_force(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yaml"
            cfg_path.write_text("telegram: {}\n", encoding="utf-8")

            res = runner.invoke(app, ["setup", "--config", str(cfg_path)], input="\n")
            self.assertNotEqual(res.exit_code, 0)

    def test_setup_rejects_ssh_with_empty_allowed_roots(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yaml"

            user_input = "\n".join(
                [
                    "",  # telegram.token_env
                    "12345",  # allowed user ids
                    "",  # db_path
                    "",  # codex bin
                    "/tmp",  # default_workdir
                    "/tmp",  # local allowed_roots
                    "y",  # add ssh machine
                    "box",  # name
                    "example.com",  # host
                    "ubuntu",  # user
                    "",  # port default
                    "/home/ubuntu",  # ssh default_workdir
                    "",  # ssh allowed_roots (invalid)
                    "y",  # use agent
                    "",  # key path
                    "",  # known_hosts default
                    "",  # codex_bin override
                ]
            )
            res = runner.invoke(
                app, ["setup", "--config", str(cfg_path)], input=user_input + "\n"
            )
            self.assertNotEqual(res.exit_code, 0, res.output)
            self.assertIn("allowed_roots", res.output)
