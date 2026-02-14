from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    # Minimal schema, versioning can be added later if needed.
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_state (
          chat_id INTEGER PRIMARY KEY,
          machine_name TEXT NOT NULL,
          workdir TEXT NOT NULL,
          active_session_id TEXT,
          session_title TEXT,
          approval_policy TEXT NOT NULL,
          approval_mode TEXT,
          sandbox_mode TEXT,
          model TEXT,
          thinking_level TEXT,
          show_reasoning INTEGER NOT NULL DEFAULT 0,
          plan_mode INTEGER NOT NULL DEFAULT 0,
          last_input_tokens INTEGER,
          last_output_tokens INTEGER,
          last_cached_tokens INTEGER,
          last_total_tokens INTEGER,
          last_context_window INTEGER,
          last_context_remaining INTEGER,
          rate_primary_used_percent REAL,
          rate_primary_window_minutes INTEGER,
          rate_primary_resets_at INTEGER,
          rate_secondary_used_percent REAL,
          rate_secondary_window_minutes INTEGER,
          rate_secondary_resets_at INTEGER,
          updated_at INTEGER NOT NULL
        )
        """
    )
    # Migration: add token columns to existing databases that pre-date this schema.
    for col, typ in [
        ("approval_mode", "TEXT"),
        ("sandbox_mode", "TEXT"),
        ("last_input_tokens", "INTEGER"),
        ("last_output_tokens", "INTEGER"),
        ("last_cached_tokens", "INTEGER"),
        ("last_total_tokens", "INTEGER"),
        ("last_context_window", "INTEGER"),
        ("last_context_remaining", "INTEGER"),
        ("rate_primary_used_percent", "REAL"),
        ("rate_primary_window_minutes", "INTEGER"),
        ("rate_primary_resets_at", "INTEGER"),
        ("rate_secondary_used_percent", "REAL"),
        ("rate_secondary_window_minutes", "INTEGER"),
        ("rate_secondary_resets_at", "INTEGER"),
    ]:
        try:
            cur.execute(f"ALTER TABLE chat_state ADD COLUMN {col} {typ}")
        except Exception:
            pass  # Column already exists

    # Best-effort backfill: older DBs won't have approval_mode populated.
    # Map legacy Codex approval policies to the closest user-facing mode:
    # - never -> yolo
    # - everything else -> on-request
    try:
        cur.execute(
            """
            UPDATE chat_state
            SET approval_mode = CASE approval_policy
              WHEN 'never' THEN 'yolo'
              ELSE 'on-request'
            END
            WHERE approval_mode IS NULL OR approval_mode = ''
            """
        )
    except Exception:
        pass
    # Backward compat: earlier builds used approval_mode='always'; coerce to on-request.
    try:
        cur.execute(
            """
            UPDATE chat_state
            SET approval_mode = 'on-request'
            WHERE approval_mode = 'always'
            """
        )
    except Exception:
        pass
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS session_index (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL,
          machine_name TEXT NOT NULL,
          session_id TEXT NOT NULL,
          title TEXT,
          created_at INTEGER,
          last_used_at INTEGER,
          UNIQUE(machine_name, session_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trusted_prefixes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          machine_name TEXT NOT NULL,
          session_id TEXT NOT NULL,
          prefix TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          UNIQUE(machine_name, session_id, prefix)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS active_run (
          chat_id INTEGER PRIMARY KEY,
          run_id TEXT NOT NULL,
          status TEXT NOT NULL,
          pending_action_json TEXT,
          updated_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()
