from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tgcodex.machines.base import Machine


@dataclass(frozen=True)
class SkillMeta:
    name: str
    path: str
    description: Optional[str]


async def list_skills(machine: Machine, *, limit: int = 200) -> list[SkillMeta]:
    roots = ["~/.codex/skills", "~/.codex/superpowers/skills"]
    # If the bot is configured with a non-default CODEX_HOME, skills may live outside ~/.codex.
    # Only apply this for local machines, since the bot process env won't match remote env vars.
    if getattr(machine, "type", None) == "local":
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            roots = [
                os.path.join(codex_home, "skills"),
                os.path.join(codex_home, "superpowers", "skills"),
            ] + roots

    paths: list[str] = []
    for r in roots:
        try:
            base = await machine.realpath(r)
            paths.extend(await machine.list_glob(os.path.join(base, "**", "SKILL.md")))
        except Exception:
            continue

    # De-dupe by full path (glob patterns can overlap on some systems).
    seen_paths: set[str] = set()
    out_by_name: dict[str, SkillMeta] = {}
    for p in paths:
        if p in seen_paths:
            continue
        seen_paths.add(p)

        try:
            txt = await machine.read_text(p)
        except Exception:
            continue

        name = Path(p).parent.name
        desc = _skill_description(txt)
        # Prefer the first match if duplicates exist; it tends to be the "real" skill.
        out_by_name.setdefault(name, SkillMeta(name=name, path=p, description=desc))

        if len(out_by_name) >= limit:
            break

    out = list(out_by_name.values())
    out.sort(key=lambda s: s.name.lower())
    return out


def _skill_description(md: str) -> Optional[str]:
    # Best-effort: try YAML frontmatter first.
    fm = _frontmatter(md)
    if fm and fm.get("description"):
        return fm["description"][:200]
    return _first_paragraph(md)


def _frontmatter(md: str) -> Optional[dict[str, str]]:
    """
    Parse simple YAML frontmatter (--- ... ---) into a dict of string keys/values.

    We avoid adding a YAML dependency; most skills only need `description: ...`.
    """
    lines = md.splitlines()
    if not lines:
        return None
    if lines[0].strip() != "---":
        return None
    out: dict[str, str] = {}
    # Scan until closing '---'
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v:
            out[k] = v
    return None


def _first_paragraph(md: str) -> Optional[str]:
    # Skip YAML frontmatter if present.
    lines = md.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                i = j + 1
                break
    for line in lines[i:]:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        # Skip raw frontmatter markers if present in non-standard files.
        if line == "---":
            continue
        return line[:200]
    return None
