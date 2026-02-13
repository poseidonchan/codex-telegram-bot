import os
import tempfile
import unittest

from tgcodex.config import (
    ApprovalsConfig,
    CodexConfig,
    Config,
    LocalMachineDef,
    MachinesConfig,
    OutputConfig,
    StateConfig,
    TelegramConfig,
    validate_config,
)


class TestConfigValidation(unittest.TestCase):
    def test_validate_config_accepts_tilde_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as _td:
            old = os.environ.get("TGCODEX_TEST_TOKEN")
            os.environ["TGCODEX_TEST_TOKEN"] = "x"
            try:
                cfg = Config(
                    telegram=TelegramConfig(
                        token_env="TGCODEX_TEST_TOKEN",
                        allowed_user_ids=(1,),
                    ),
                    state=StateConfig(db_path="tgcodex.sqlite3"),
                    codex=CodexConfig(
                        bin="codex",
                        args=(),
                        model=None,
                        sandbox="workspace-write",
                        approval_policy="untrusted",
                        skip_git_repo_check=True,
                    ),
                    output=OutputConfig(
                        flush_interval_ms=250,
                        min_flush_chars=120,
                        max_flush_delay_seconds=2.0,
                        max_chars=3500,
                        truncate=True,
                        typing_interval_seconds=4.0,
                        show_codex_logs=False,
                        show_tool_output=False,
                        max_tool_output_chars=1200,
                    ),
                    approvals=ApprovalsConfig(prefix_tokens=2),
                    machines=MachinesConfig(
                        default="local",
                        defs={
                            "local": LocalMachineDef(
                                type="local",
                                default_workdir="~/",
                                allowed_roots=("~/",),
                            )
                        },
                    ),
                )

                errors = validate_config(cfg, validate_binaries=False)
                joined = "\n".join(errors)
                self.assertNotIn("allowed_roots must be absolute", joined)
            finally:
                if old is None:
                    os.environ.pop("TGCODEX_TEST_TOKEN", None)
                else:
                    os.environ["TGCODEX_TEST_TOKEN"] = old
