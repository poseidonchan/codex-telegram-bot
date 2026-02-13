import unittest

from tgcodex.codex.adapter import CodexCLIAdapter, RunSettings


class TestCodexCLIAdapter(unittest.TestCase):
    def test_build_argv_no_resume(self) -> None:
        settings = RunSettings(
            codex_bin="codex",
            codex_args=(),
            model="gpt-5.3-codex",
            thinking_level="high",
            sandbox="workspace-write",
            approval_policy="untrusted",
            skip_git_repo_check=True,
        )
        argv = CodexCLIAdapter.build_argv(
            settings=settings,
            session_id=None,
            workdir="/tmp",
            prompt="hello",
        )
        self.assertEqual(argv[0], "codex")
        self.assertIn("exec", argv)
        self.assertIn("--json", argv)
        self.assertNotIn("resume", argv)
        self.assertNotIn("--color", argv)
        self.assertIn('projects."/tmp".trust_level="untrusted"', argv)

    def test_build_argv_resume_keeps_color_out_of_subcommand(self) -> None:
        settings = RunSettings(
            codex_bin="codex",
            codex_args=(),
            model=None,
            thinking_level=None,
            sandbox="workspace-write",
            approval_policy="untrusted",
            skip_git_repo_check=False,
        )
        argv = CodexCLIAdapter.build_argv(
            settings=settings,
            session_id="019c0000-0000-0000-0000-000000000000",
            workdir="/tmp",
            prompt="hello",
        )
        exec_idx = argv.index("exec")
        self.assertEqual(argv[exec_idx + 1], "resume")
        self.assertEqual(argv[exec_idx + 2], "019c0000-0000-0000-0000-000000000000")
        # `--json` must come after `resume <id>` (matches codex exec resume args).
        json_idx = argv.index("--json")
        self.assertGreater(json_idx, exec_idx + 2)
        # Regression: `--color` after `resume` makes codex exit with rc=2 on 0.98.0.
        self.assertNotIn("--color", argv)
        self.assertIn('projects."/tmp".trust_level="untrusted"', argv)
