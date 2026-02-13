from __future__ import annotations

import time


def monotonic() -> float:
    return time.monotonic()


def now_ts() -> int:
    return int(time.time())

