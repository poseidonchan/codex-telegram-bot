import os
import tempfile
import unittest
import asyncio
from pathlib import Path

from tgcodex.machines.paths import CdNotAllowed, resolve_cd, resolve_cd_local


class TestResolveCdLocal(unittest.TestCase):
    def test_allows_within_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ok").mkdir(parents=True)
            out = resolve_cd_local(
                current_workdir=str(root),
                new_path="ok",
                allowed_roots=[str(root)],
            )
            self.assertEqual(out, str((root / "ok").resolve()))

    def test_rejects_parent_escape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            outside = Path(td) / "outside"
            root.mkdir()
            outside.mkdir()
            with self.assertRaises(CdNotAllowed) as cm:
                resolve_cd_local(
                    current_workdir=str(root),
                    new_path="../outside",
                    allowed_roots=[str(root)],
                )
            exc = cm.exception
            self.assertTrue(hasattr(exc, "allowed_roots"))
            self.assertIn(str(root.resolve()), getattr(exc, "allowed_roots"))
            self.assertIn("allowed_roots", str(exc))

    def test_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            outside = Path(td) / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "link"
            try:
                os.symlink(str(outside), str(link))
            except (OSError, NotImplementedError):
                self.skipTest("symlink not supported")
            with self.assertRaises(CdNotAllowed):
                resolve_cd_local(
                    current_workdir=str(root),
                    new_path="link",
                    allowed_roots=[str(root)],
                )


class TestResolveCdRemote(unittest.TestCase):
    def test_tilde_is_expanded_remotely_not_locally(self) -> None:
        seen: list[str] = []

        async def fake_realpath(p: str) -> str:
            seen.append(p)
            # Minimal remote expansion behavior for testing.
            if p == "~":
                return "/home/cys"
            if p == "/home/cys":
                return "/home/cys"
            if p == "/tmp":
                return "/tmp"
            return p

        out = asyncio.run(
            resolve_cd(
                current_workdir="/home/cys",
                new_path="~",
                allowed_roots=["/home/cys", "/tmp"],
                realpath=fake_realpath,
            )
        )
        self.assertEqual(out, "/home/cys")
        self.assertIn("~", seen)

    def test_tilde_path_is_not_joined_under_cwd(self) -> None:
        seen: list[str] = []

        async def fake_realpath(p: str) -> str:
            seen.append(p)
            if p == "/home/cys":
                return "/home/cys"
            if p == "/tmp":
                return "/tmp"
            if p == "~/repo":
                return "/home/cys/repo"
            return p

        out = asyncio.run(
            resolve_cd(
                current_workdir="/home/cys/projects",
                new_path="~/repo",
                allowed_roots=["/home/cys", "/tmp"],
                realpath=fake_realpath,
            )
        )
        self.assertEqual(out, "/home/cys/repo")
        self.assertIn("~/repo", seen)
