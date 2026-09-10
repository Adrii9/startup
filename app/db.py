"""SQLite schema and connections.

The `event` table is append-only: never UPDATE, never DELETE. It is the single
source of truth for a project. The one exception is a project its owner deleted
more than 30 days ago, which is purged whole -- see store.purge_deleted_projects.

Two credentials, deliberately separate:

  * a web session, for a person in a browser, created by signing in with Google
  * a connection token, one per assistant, which goes inside the connector URL

A leaked connector URL can therefore be revoked without signing anyone out,
and it can never be used to manage an account.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
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
-- A person. They sign up with a username and a password; Google sign-in is
-- supported too but only switched on when its credentials are configured.
-- Deleting an account keeps the row, stripped of everything identifying, so that
-- entries they wrote in shared projects still have an author and other people's
-- history does not break.
CREATE TABLE IF NOT EXISTS account (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT,
    password_hash TEXT,
    google_sub    TEXT UNIQUE,
    email         TEXT,
    name          TEXT NOT NULL,
    picture       TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at    TEXT
);

-- A signed-in browser. Only the hash is stored, so a copy of the database is
-- not a way into anyone's account.
CREATE TABLE IF NOT EXISTS session (
    id_hash    TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES account(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

-- One per assistant. The token goes in the connector URL; only its hash is
-- kept, which is why it can be shown exactly once.
CREATE TABLE IF NOT EXISTS connection (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL REFERENCES account(id),
    label        TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    revoked_at   TEXT
);

CREATE TABLE IF NOT EXISTS workspace (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    colour     TEXT NOT NULL DEFAULT '#7dd3fc',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS membership (
    account_id   INTEGER NOT NULL REFERENCES account(id),
    workspace_id INTEGER NOT NULL REFERENCES workspace(id),
    role         TEXT NOT NULL DEFAULT 'member',
    joined_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, workspace_id)
);

-- Invite links. Kept in plain text, unlike connection tokens: they expire in a
-- week, grant membership of one project, and an owner needs to copy them again.
CREATE TABLE IF NOT EXISTS invite (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspace(id),
    code         TEXT NOT NULL UNIQUE,
    created_by   INTEGER NOT NULL REFERENCES account(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT NOT NULL,
    max_uses     INTEGER,
    uses         INTEGER NOT NULL DEFAULT 0,
    revoked_at   TEXT
);

-- THE LOG. Append-only. event.id is monotonic and doubles as the read cursor;
-- seq is the number people see, counted per project so each starts at #1.
CREATE TABLE IF NOT EXISTS event (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id  INTEGER NOT NULL REFERENCES workspace(id),
    seq           INTEGER NOT NULL,
    account_id    INTEGER NOT NULL REFERENCES account(id),
    agent         TEXT NOT NULL DEFAULT 'unknown',
    kind          TEXT NOT NULL DEFAULT 'work',
    summary       TEXT NOT NULL,
    details       TEXT,
    intent        TEXT,
    intent_source TEXT,
    rejected_json TEXT,
    refs_json     TEXT,
    section_id    INTEGER REFERENCES section(id),
    supersedes_id INTEGER REFERENCES event(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (workspace_id, seq)
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

-- Per person AND per project: catching up in one must never mark another read.
CREATE TABLE IF NOT EXISTS cursor (
    account_id    INTEGER NOT NULL REFERENCES account(id),
    workspace_id  INTEGER NOT NULL REFERENCES workspace(id),
    last_event_id INTEGER NOT NULL DEFAULT 0,
    last_sync_at  TEXT,
    PRIMARY KEY (account_id, workspace_id)
);
"""


def _retire_old_database() -> None:
    """Move a database from before accounts out of the way, without deleting it.

    The move to accounts started from zero on purpose. The old file is renamed
    beside the new one rather than dropped, so it is still there if anyone ever
    wants something out of it. WAL and SHM files move with it, or the copy
    would be missing whatever had not been checkpointed yet.
    """
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    if not tables or "account" in tables:
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        src = Path(f"{DB_PATH}{suffix}")
        if src.exists():
            src.rename(Path(f"{DB_PATH}.before-accounts-{stamp}{suffix}"))
    print(f"retired a database from before accounts to {DB_PATH}.before-accounts-{stamp}",
          flush=True)


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


def init_db() -> None:
    """Create the schema. Idempotent. Nobody is seeded: people sign themselves up."""
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

    _retire_old_database()
    with connect() as conn:
        conn.executescript(SCHEMA)
        # An account table made before passwords existed gets the two columns.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(account)")}
        for column in ("username", "password_hash"):
            if column not in have:
                conn.execute(f"ALTER TABLE account ADD COLUMN {column} TEXT")
        # Unique regardless of case, so "Adria" and "adria" cannot both exist.
        # An index rather than a column constraint, because SQLite cannot add a
        # UNIQUE column to a table that already exists.
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_account_username "
                     "ON account(username COLLATE NOCASE)")
