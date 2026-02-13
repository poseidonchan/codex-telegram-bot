import unittest

from tgcodex.codex.skills import list_skills


class _FakeMachine:
    name = "fake"
    type = "local"

    def __init__(self) -> None:
        self._texts = {
            "/skills/.system/skill-creator/SKILL.md": """---
name: skill-creator
description: Guide for creating effective skills.
---

# Skill Creator

Longer body...
""",
            "/super/using-superpowers/SKILL.md": """---
name: using-superpowers
description: Use when starting any conversation.
---

# Using Superpowers

Longer body...
""",
        }

    async def realpath(self, path: str) -> str:  # type: ignore[override]
        if path == "~/.codex/skills":
            return "/skills"
        if path == "~/.codex/superpowers/skills":
            return "/super"
        return path

    async def list_glob(self, pattern: str) -> list[str]:  # type: ignore[override]
        if pattern.startswith("/skills"):
            return ["/skills/.system/skill-creator/SKILL.md"]
        if pattern.startswith("/super"):
            return ["/super/using-superpowers/SKILL.md"]
        return []

    async def read_text(self, path: str) -> str:  # type: ignore[override]
        return self._texts[path]


class TestSkills(unittest.IsolatedAsyncioTestCase):
    async def test_list_skills_includes_superpowers(self) -> None:
        machine = _FakeMachine()
        skills = await list_skills(machine, limit=200)
        names = [s.name for s in skills]
        self.assertIn("skill-creator", names)
        self.assertIn("using-superpowers", names)

        by_name = {s.name: s for s in skills}
        self.assertEqual(
            by_name["skill-creator"].description,
            "Guide for creating effective skills.",
        )
        self.assertEqual(
            by_name["using-superpowers"].description,
            "Use when starting any conversation.",
        )

