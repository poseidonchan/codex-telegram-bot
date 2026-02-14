import unittest

from tgcodex.codex.command_intent import needs_write_approval


class TestCommandIntent(unittest.TestCase):
    def test_needs_write_approval_unwraps_bash_lc(self) -> None:
        self.assertTrue(needs_write_approval("/bin/bash -lc 'cd /tmp && mkdir -p foo'"))

    def test_needs_write_approval_bash_lc_readonly(self) -> None:
        self.assertFalse(needs_write_approval("bash -lc 'cd /tmp && ls -la'"))

    def test_script_execution_limitation(self) -> None:
        """
        KNOWN LIMITATION: Script execution is not detected as potentially dangerous.

        Python/shell scripts can write files indirectly, but the current heuristic
        doesn't catch this. This test documents the gap between the implementation
        and ideal real-world safety.

        In practice, Codex's approval policy should catch these via sandbox settings,
        but defense-in-depth at the bot level would be better.
        """
        # Currently these return False (not detected as write operations)
        self.assertFalse(needs_write_approval("python script.py"),
                        "Current implementation doesn't detect indirect writes via Python")
        self.assertFalse(needs_write_approval("python3 write_data.py"),
                        "Current implementation doesn't detect indirect writes via Python")
        self.assertFalse(needs_write_approval("sh deploy.sh"),
                        "Current implementation doesn't detect indirect writes via shell scripts")
        self.assertFalse(needs_write_approval("./build.sh"),
                        "Current implementation doesn't detect indirect writes via executable scripts")

        # These SHOULD return True for better real-world safety, but that would require
        # either: (1) treating ALL script executions as writes (too conservative), or
        # (2) analyzing script content (too complex/brittle), or
        # (3) relying entirely on Codex's sandbox (current approach)

    def test_needs_write_approval_detects_piped_writes(self) -> None:
        """Piped commands can write to files, should require approval."""
        self.assertTrue(needs_write_approval("echo 'data' | tee output.txt"))
        self.assertTrue(needs_write_approval("cat input.txt | grep pattern > output.txt"))

    def test_needs_write_approval_detects_redirection(self) -> None:
        """Shell redirection can write files."""
        self.assertTrue(needs_write_approval("echo 'hello' > file.txt"))
        self.assertTrue(needs_write_approval("cat data.txt >> log.txt"))

    def test_readonly_commands_do_not_need_approval(self) -> None:
        """Pure read operations should not need approval."""
        self.assertFalse(needs_write_approval("ls -la"))
        self.assertFalse(needs_write_approval("cat file.txt"))
        self.assertFalse(needs_write_approval("grep pattern file.txt"))
        self.assertFalse(needs_write_approval("find . -name '*.py'"))
        self.assertFalse(needs_write_approval("head -n 10 file.txt"))

