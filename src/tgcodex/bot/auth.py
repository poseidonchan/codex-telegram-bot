from __future__ import annotations


def is_allowed_user(user_id: int | None, *, allowed_user_ids: tuple[int, ...]) -> bool:
    if user_id is None:
        return False
    return int(user_id) in set(allowed_user_ids)

