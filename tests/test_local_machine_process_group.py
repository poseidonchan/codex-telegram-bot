import asyncio
import os
import tempfile
import unittest

from tgcodex.machines.local import LocalMachine


class TestLocalMachineProcessGroup(unittest.IsolatedAsyncioTestCase):
    async def test_kill_terminates_spawned_children(self) -> None:
        """
        Regression test: if the Codex CLI process is killed but its child tool process
        survives, a "rejected" command can still finish executing in the background.

        LocalMachine.run() should start the process in its own process group and
        terminate/kill the entire group.
        """
        lm = LocalMachine(name="local")

        with tempfile.TemporaryDirectory() as td:
            marker = os.path.join(td, "marker.txt")

            async def _sink(_: bytes) -> None:
                return None

            # Spawn a child that creates the marker after a short delay. If we only
            # kill the parent, the child can survive and write the marker.
            cmd = f"bash -lc 'sleep 0.8; echo ok > {marker!s}' & sleep 10"

            handle = await lm.run(
                argv=["bash", "-lc", cmd],
                cwd=td,
                env=None,
                pty=False,
                stdout_cb=_sink,
                stderr_cb=_sink,
                stdin_provider=None,
            )
            try:
                await asyncio.sleep(0.15)

                await handle.terminate()
                await handle.kill()
            finally:
                # Ensure the parent is reaped even if terminate/kill raise.
                try:
                    await asyncio.wait_for(handle.wait(), timeout=2.0)
                except Exception:
                    pass

            # Wait longer than the child delay; marker must not be created.
            await asyncio.sleep(1.1)
            self.assertFalse(os.path.exists(marker))

