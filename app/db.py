"""SQLite schema and connections.

The `event` table is append-only: never UPDATE, never DELETE. It is the single
source of truth. Every other table is a projection that could be rebuilt by
replaying the log, which is why `section` holds no content of its own.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(
    os.environ.get("WORKSPACE_DB", Path(__file__).resolve().parent.parent / "workspace.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS member (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspace(id),
    name         TEXT NOT NULL,
    token        TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- THE LOG. Append-only. event.id is monotonic and doubles as the read cursor.
CREATE TABLE IF NOT EXISTS event (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id  INTEGER NOT NULL REFERENCES workspace(id),
    member_id     INTEGER NOT NULL REFERENCES member(id),
    agent         TEXT NOT NULL DEFAULT 'unknown',
    kind          TEXT NOT NULL DEFAULT 'work',
    summary       TEXT NOT NULL,
    intent        TEXT,
    intent_source TEXT,          -- 'stated' | 'inferred' | NULL
    rejected_json TEXT,          -- [{"option": ..., "reason": ...}] or NULL
    section_id    INTEGER REFERENCES section(id),
    supersedes_id INTEGER REFERENCES event(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_event_ws ON event(workspace_id, id);

-- PROJECTIONS ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS section (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id        INTEGER NOT NULL REFERENCES workspace(id),
    key                 TEXT NOT NULL,
    title               TEXT NOT NULL,
    current_revision_id INTEGER,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (workspace_id, key)
);

CREATE TABLE IF NOT EXISTS section_revision (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL REFERENCES section(id),
    event_id   INTEGER NOT NULL REFERENCES event(id),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS artifact (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspace(id),
    event_id     INTEGER NOT NULL REFERENCES event(id),
    kind         TEXT NOT NULL DEFAULT 'link',
    url          TEXT NOT NULL,
    caption      TEXT
);

CREATE TABLE IF NOT EXISTS cursor (
    member_id     INTEGER PRIMARY KEY REFERENCES member(id),
    last_event_id INTEGER NOT NULL DEFAULT 0,
    last_sync_at  TEXT
);
"""

ROSTER = ["Adria", "Oscar", "Pau"]


def _seed_tokens() -> dict[str, str]:
    """Tokens come from the environment, never from this file.

    A token is the write key to the workspace, so it does not belong in source
    control. Set MEMBER_TOKENS as "Adria:xxx,Oscar:yyy,Pau:zzz"; with nothing
    set, each member gets a random one on first boot, printed once at startup.
    Existing members keep the token they already have, so restarts are safe.
    """
    raw = os.environ.get("MEMBER_TOKENS", "").strip()
    tokens = {}
    for part in raw.split(","):
        name, _, token = part.partition(":")
        if name.strip() and token.strip():
            tokens[name.strip()] = token.strip()
    return tokens


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(title: str = "Shared workspace") -> None:
    """Create the schema and seed the single M0 workspace. Idempotent."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT id FROM workspace WHERE slug = 'demo'").fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO workspace (slug, title) VALUES ('demo', ?)", (title,)
            )
            workspace_id = cur.lastrowid
        else:
            workspace_id = row["id"]

        configured = _seed_tokens()
        for name in ROSTER:
            # Keyed on name, not token: a member who already exists keeps their
            # token, so a restart never mints a duplicate or breaks live URLs.
            existing = conn.execute(
                "SELECT id FROM member WHERE workspace_id = ? AND name = ?",
                (workspace_id, name),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO member (workspace_id, name, token) VALUES (?, ?, ?)",
                    (workspace_id, name, configured.get(name) or secrets.token_urlsafe(12)),
                )
        for member in conn.execute(
            "SELECT id FROM member WHERE workspace_id = ?", (workspace_id,)
        ):
            conn.execute(
                "INSERT OR IGNORE INTO cursor (member_id, last_event_id) VALUES (?, 0)",
                (member["id"],),
            )

        print("Connector URLs -- append these paths to your public host:", flush=True)
        for m in conn.execute(
            "SELECT name, token FROM member WHERE workspace_id = ? ORDER BY id", (workspace_id,)
        ):
            print(f"  {m['name']:<8} /u/{m['token']}/mcp", flush=True)
