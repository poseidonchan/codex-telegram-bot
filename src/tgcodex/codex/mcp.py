from __future__ import annotations

import json
from typing import Any

from tgcodex.machines.base import Machine


async def mcp_list(machine: Machine, *, codex_bin: str = "codex") -> list[dict[str, Any]]:
    res = await machine.exec_capture([codex_bin, "mcp", "list", "--json"], cwd=None)
    if res.exit_code != 0:
        raise RuntimeError(res.stderr.strip() or "codex mcp list failed")
    obj = json.loads(res.stdout)
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict) and "servers" in obj and isinstance(obj["servers"], list):
        return [x for x in obj["servers"] if isinstance(x, dict)]
    return []

