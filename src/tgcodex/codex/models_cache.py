from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from tgcodex.machines.base import Machine


@dataclass(frozen=True)
class ReasoningLevel:
    effort: str
    description: Optional[str] = None


@dataclass(frozen=True)
class ModelEntry:
    slug: str
    display_name: Optional[str]
    default_reasoning_level: Optional[str]
    supported_reasoning_levels: tuple[ReasoningLevel, ...]


@dataclass(frozen=True)
class ModelsCache:
    fetched_at: Optional[int]
    etag: Optional[str]
    client_version: Optional[str]
    models: tuple[ModelEntry, ...]


async def read_models_cache(machine: Machine) -> ModelsCache:
    path = await machine.realpath("~/.codex/models_cache.json")
    raw = await machine.read_text(path)
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("models_cache.json is not an object")

    models_raw = obj.get("models")
    models: list[ModelEntry] = []
    if isinstance(models_raw, list):
        for m in models_raw:
            if not isinstance(m, dict):
                continue
            slug = m.get("slug")
            if not isinstance(slug, str) or not slug:
                continue
            levels_raw = m.get("supported_reasoning_levels") or []
            levels: list[ReasoningLevel] = []
            if isinstance(levels_raw, list):
                for lv in levels_raw:
                    if not isinstance(lv, dict):
                        continue
                    effort = lv.get("effort")
                    if not isinstance(effort, str) or not effort:
                        continue
                    desc = lv.get("description")
                    levels.append(
                        ReasoningLevel(
                            effort=effort,
                            description=desc if isinstance(desc, str) else None,
                        )
                    )
            models.append(
                ModelEntry(
                    slug=slug,
                    display_name=m.get("display_name")
                    if isinstance(m.get("display_name"), str)
                    else None,
                    default_reasoning_level=m.get("default_reasoning_level")
                    if isinstance(m.get("default_reasoning_level"), str)
                    else None,
                    supported_reasoning_levels=tuple(levels),
                )
            )

    return ModelsCache(
        fetched_at=obj.get("fetched_at") if isinstance(obj.get("fetched_at"), int) else None,
        etag=obj.get("etag") if isinstance(obj.get("etag"), str) else None,
        client_version=obj.get("client_version") if isinstance(obj.get("client_version"), str) else None,
        models=tuple(models),
    )

