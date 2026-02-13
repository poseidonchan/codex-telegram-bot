from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from tgcodex.state.migrations import migrate
from tgcodex.state.models import ActiveRun, ChatState, SessionIndexRow


def _now_ts() -> int:
    return int(time.time())


class Store:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        if self._conn is not None:
            return
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        migrate(conn)
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Store not opened")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def ensure_chat_state(
        self,
        *,
        chat_id: int,
        default_machine: str,
        default_workdir: str,
        default_approval_policy: str,
        default_model: Optional[str],
        default_thinking_level: Optional[str] = None,
    ) -> ChatState:
        existing = self.get_chat_state(chat_id)
        if existing is not None:
            return existing

        now = _now_ts()
        self.conn.execute(
            """
            INSERT INTO chat_state (
              chat_id, machine_name, workdir, active_session_id, session_title,
              approval_policy, model, thinking_level, show_reasoning, plan_mode, updated_at
            ) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, 0, 0, ?)
            """,
            (
                chat_id,
                default_machine,
                default_workdir,
                default_approval_policy,
                default_model,
                default_thinking_level,
                now,
            ),
        )
        self.conn.commit()
        return ChatState(
            chat_id=chat_id,
            machine_name=default_machine,
            workdir=default_workdir,
            active_session_id=None,
            session_title=None,
            approval_policy=default_approval_policy,
            model=default_model,
            thinking_level=default_thinking_level,
            show_reasoning=False,
            plan_mode=False,
            last_input_tokens=None,
            last_output_tokens=None,
            last_cached_tokens=None,
            last_total_tokens=None,
            last_context_window=None,
            last_context_remaining=None,
            rate_primary_used_percent=None,
            rate_primary_window_minutes=None,
            rate_primary_resets_at=None,
            rate_secondary_used_percent=None,
            rate_secondary_window_minutes=None,
            rate_secondary_resets_at=None,
            updated_at=now,
        )

    def get_chat_state(self, chat_id: int) -> Optional[ChatState]:
        row = self.conn.execute(
            "SELECT * FROM chat_state WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row is None:
            return None
        return ChatState(
            chat_id=int(row["chat_id"]),
            machine_name=str(row["machine_name"]),
            workdir=str(row["workdir"]),
            active_session_id=row["active_session_id"],
            session_title=row["session_title"],
            approval_policy=str(row["approval_policy"]),
            model=row["model"],
            thinking_level=row["thinking_level"],
            show_reasoning=bool(row["show_reasoning"]),
            plan_mode=bool(row["plan_mode"]),
            last_input_tokens=row["last_input_tokens"] if row["last_input_tokens"] is not None else None,
            last_output_tokens=row["last_output_tokens"] if row["last_output_tokens"] is not None else None,
            last_cached_tokens=row["last_cached_tokens"] if row["last_cached_tokens"] is not None else None,
            last_total_tokens=row["last_total_tokens"] if row["last_total_tokens"] is not None else None,
            last_context_window=row["last_context_window"] if row["last_context_window"] is not None else None,
            last_context_remaining=row["last_context_remaining"] if row["last_context_remaining"] is not None else None,
            rate_primary_used_percent=float(row["rate_primary_used_percent"]) if row["rate_primary_used_percent"] is not None else None,
            rate_primary_window_minutes=row["rate_primary_window_minutes"] if row["rate_primary_window_minutes"] is not None else None,
            rate_primary_resets_at=row["rate_primary_resets_at"] if row["rate_primary_resets_at"] is not None else None,
            rate_secondary_used_percent=float(row["rate_secondary_used_percent"]) if row["rate_secondary_used_percent"] is not None else None,
            rate_secondary_window_minutes=row["rate_secondary_window_minutes"] if row["rate_secondary_window_minutes"] is not None else None,
            rate_secondary_resets_at=row["rate_secondary_resets_at"] if row["rate_secondary_resets_at"] is not None else None,
            updated_at=int(row["updated_at"]),
        )

    def update_chat_state(self, chat_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = _now_ts()
        cols = ", ".join(f"{k} = ?" for k in fields.keys())
        vals = list(fields.values())
        vals.append(chat_id)
        self.conn.execute(
            f"UPDATE chat_state SET {cols} WHERE chat_id = ?", vals  # noqa: S608
        )
        self.conn.commit()

    def update_token_telemetry(self, chat_id: int, *, token: Any) -> None:
        """
        Persist best-effort token and rate-limit telemetry emitted by Codex (token_count events).

        `token` is expected to be a `tgcodex.codex.events.TokenCount`, but is typed as `Any` here
        to keep state/store decoupled from Codex event parsing.
        """
        fields: dict[str, Any] = {}

        total = getattr(token, "total_tokens", None)
        window = getattr(token, "model_context_window", None)
        if isinstance(total, int):
            fields["last_total_tokens"] = total
        if isinstance(window, int):
            fields["last_context_window"] = window
        if isinstance(total, int) and isinstance(window, int):
            fields["last_context_remaining"] = max(window - total, 0)

        # Token usage breakdown (if provided).
        it = getattr(token, "input_tokens", None)
        ot = getattr(token, "output_tokens", None)
        cit = getattr(token, "cached_input_tokens", None)
        if isinstance(it, int):
            fields["last_input_tokens"] = it
        if isinstance(ot, int):
            fields["last_output_tokens"] = ot
        if isinstance(cit, int):
            fields["last_cached_tokens"] = cit

        # Rate limits (if provided).
        p_used = getattr(token, "primary_used_percent", None)
        if isinstance(p_used, (int, float)) and not isinstance(p_used, bool):
            fields["rate_primary_used_percent"] = float(p_used)
        p_win = getattr(token, "primary_window_minutes", None)
        if isinstance(p_win, int):
            fields["rate_primary_window_minutes"] = p_win
        p_reset = getattr(token, "primary_resets_at", None)
        if isinstance(p_reset, int):
            fields["rate_primary_resets_at"] = p_reset

        s_used = getattr(token, "secondary_used_percent", None)
        if isinstance(s_used, (int, float)) and not isinstance(s_used, bool):
            fields["rate_secondary_used_percent"] = float(s_used)
        s_win = getattr(token, "secondary_window_minutes", None)
        if isinstance(s_win, int):
            fields["rate_secondary_window_minutes"] = s_win
        s_reset = getattr(token, "secondary_resets_at", None)
        if isinstance(s_reset, int):
            fields["rate_secondary_resets_at"] = s_reset

        if fields:
            self.update_chat_state(chat_id, **fields)

    def set_machine(self, *, chat_id: int, machine_name: str, workdir: str) -> None:
        self.update_chat_state(chat_id, machine_name=machine_name, workdir=workdir, **self._cleared_session_fields())

    def set_workdir(self, *, chat_id: int, workdir: str) -> None:
        self.update_chat_state(chat_id, workdir=workdir, **self._cleared_session_fields())

    def clear_session(self, *, chat_id: int) -> None:
        self.update_chat_state(chat_id, **self._cleared_session_fields())

    @staticmethod
    def _cleared_session_fields() -> dict[str, Any]:
        # Reset per-session telemetry so /status doesn't show stale values after /new, /cd, etc.
        return {
            "active_session_id": None,
            "session_title": None,
            "last_input_tokens": None,
            "last_output_tokens": None,
            "last_cached_tokens": None,
            "last_total_tokens": None,
            "last_context_window": None,
            "last_context_remaining": None,
            "rate_primary_used_percent": None,
            "rate_primary_window_minutes": None,
            "rate_primary_resets_at": None,
            "rate_secondary_used_percent": None,
            "rate_secondary_window_minutes": None,
            "rate_secondary_resets_at": None,
        }

    def set_session(
        self,
        *,
        chat_id: int,
        session_id: str,
        title: Optional[str] = None,
    ) -> None:
        self.update_chat_state(chat_id, active_session_id=session_id, session_title=title)

    def set_session_title(self, *, chat_id: int, title: str) -> None:
        self.update_chat_state(chat_id, session_title=title)

    def upsert_session_index(
        self,
        *,
        chat_id: int,
        machine_name: str,
        session_id: str,
        title: Optional[str],
    ) -> None:
        now = _now_ts()
        self.conn.execute(
            """
            INSERT INTO session_index (chat_id, machine_name, session_id, title, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(machine_name, session_id) DO UPDATE SET
              chat_id = excluded.chat_id,
              title = COALESCE(excluded.title, session_index.title),
              last_used_at = excluded.last_used_at
            """,
            (chat_id, machine_name, session_id, title, now, now),
        )
        self.conn.commit()

    def list_session_index(
        self,
        *,
        chat_id: int,
        machine_name: str,
        limit: int = 10,
    ) -> list[SessionIndexRow]:
        cur = self.conn.execute(
            """
            SELECT * FROM session_index
            WHERE chat_id = ? AND machine_name = ?
            ORDER BY (last_used_at IS NULL) ASC, last_used_at DESC, created_at DESC
            LIMIT ?
            """,
            (chat_id, machine_name, limit),
        )
        out: list[SessionIndexRow] = []
        for row in cur.fetchall():
            out.append(SessionIndexRow(
                id=int(row["id"]),
                chat_id=int(row["chat_id"]),
                machine_name=str(row["machine_name"]),
                session_id=str(row["session_id"]),
                title=row["title"],
                created_at=row["created_at"] if row["created_at"] is not None else None,
                last_used_at=row["last_used_at"] if row["last_used_at"] is not None else None,
            ))
        return out

    def get_session_index(self, *, machine_name: str, session_id: str) -> Optional[SessionIndexRow]:
        row = self.conn.execute(
            "SELECT * FROM session_index WHERE machine_name = ? AND session_id = ?",
            (machine_name, session_id),
        ).fetchone()
        if row is None:
            return None
        return SessionIndexRow(
            id=int(row["id"]),
            chat_id=int(row["chat_id"]),
            machine_name=str(row["machine_name"]),
            session_id=str(row["session_id"]),
            title=row["title"],
            created_at=row["created_at"] if row["created_at"] is not None else None,
            last_used_at=row["last_used_at"] if row["last_used_at"] is not None else None,
        )

    def list_trusted_prefixes(self, *, machine_name: str, session_id: str) -> list[str]:
        cur = self.conn.execute(
            """
            SELECT prefix FROM trusted_prefixes
            WHERE machine_name = ? AND session_id = ?
            ORDER BY prefix
            """,
            (machine_name, session_id),
        )
        return [str(r["prefix"]) for r in cur.fetchall()]

    def add_trusted_prefix(self, *, machine_name: str, session_id: str, prefix: str) -> bool:
        now = _now_ts()
        try:
            self.conn.execute(
                """
                INSERT INTO trusted_prefixes (machine_name, session_id, prefix, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (machine_name, session_id, prefix, now),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_active_run(self, chat_id: int) -> Optional[ActiveRun]:
        row = self.conn.execute(
            "SELECT * FROM active_run WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row is None:
            return None
        return ActiveRun(
            chat_id=int(row["chat_id"]),
            run_id=str(row["run_id"]),
            status=str(row["status"]),
            pending_action_json=row["pending_action_json"],
            updated_at=int(row["updated_at"]),
        )

    def set_active_run(
        self,
        *,
        chat_id: int,
        run_id: str,
        status: str,
        pending_action: Optional[dict[str, Any]] = None,
    ) -> None:
        now = _now_ts()
        pending_action_json = json.dumps(pending_action) if pending_action is not None else None
        self.conn.execute(
            """
            INSERT INTO active_run (chat_id, run_id, status, pending_action_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
              run_id = excluded.run_id,
              status = excluded.status,
              pending_action_json = excluded.pending_action_json,
              updated_at = excluded.updated_at
            """,
            (chat_id, run_id, status, pending_action_json, now),
        )
        self.conn.commit()

    def clear_active_run(self, *, chat_id: int) -> None:
        self.conn.execute("DELETE FROM active_run WHERE chat_id = ?", (chat_id,))
        self.conn.commit()
