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

def _db_path() -> Path:
    """Where the log lives.

    Hosts that mount a volume announce it (Railway sets RAILWAY_VOLUME_MOUNT_PATH),
    so follow that by default. Getting this wrong costs the whole log with no
    error raised anywhere, which is why it is worth detecting rather than
    trusting someone to remember a second environment variable.
    """
    explicit = os.environ.get("WORKSPACE_DB")
    if explicit:
        return Path(explicit)
    mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if mount:
        return Path(mount) / "workspace.db"
    return Path(__file__).resolve().parent.parent / "workspace.db"


DB_PATH = _db_path()

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
    details       TEXT,          -- the body; summary is only the headline
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


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to a database that predates them.

    CREATE TABLE IF NOT EXISTS silently skips an existing table, so a schema
    change would never reach a live workspace without this. Append-only data
    makes migrations cheap: nothing to rewrite, only columns to add.
    """
    have = {row["name"] for row in conn.execute("PRAGMA table_info(event)")}
    for column, ddl in [("details", "TEXT")]:
        if column not in have:
            conn.execute(f"ALTER TABLE event ADD COLUMN {column} {ddl}")
            print(f"migrated: event.{column} added", flush=True)


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
    # SQLite will not create the directory for us, and a host's mounted volume
    # may hand us an empty path. Crashing on a missing folder helps nobody.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Database: {DB_PATH}", flush=True)

    mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if not mount:
        print(
            "WARNING: no persistent volume detected. The log lives inside the "
            "container and every redeploy will silently wipe it.",
            flush=True,
        )
    elif not str(DB_PATH).startswith(mount.rstrip("/") + "/"):
        print(
            f"WARNING: a volume is mounted at {mount} but the database is at "
            f"{DB_PATH}, outside it. Every redeploy will silently wipe the log.",
            flush=True,
        )

    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
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

        # Printing a token puts it in the deploy log, in screen shares and in
        # screenshots. Only do it when we minted it ourselves and nobody could
        # otherwise know it; when tokens are configured, the operator has them.
        members = conn.execute(
            "SELECT name, token FROM member WHERE workspace_id = ? ORDER BY id", (workspace_id,)
        ).fetchall()
        if configured:
            print(f"Members: {', '.join(m['name'] for m in members)} (tokens from MEMBER_TOKENS)",
                  flush=True)
        else:
            print("Connector URLs -- append these paths to your public host:", flush=True)
            for m in members:
                print(f"  {m['name']:<8} /u/{m['token']}/mcp", flush=True)
