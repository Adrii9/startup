"""SQLite schema and connections.

The `event` table is append-only: never UPDATE, never DELETE. It is the single
source of truth. Every other table is a projection that could be rebuilt by
replaying the log, which is why `section` holds no content of its own.

A `member` is a person, with one token whatever number of projects they are in.
`membership` says which projects. Every event, section and cursor belongs to
exactly one project, and nothing crosses between them.
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

DEFAULT_COLOUR = "#7dd3fc"

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    colour     TEXT NOT NULL DEFAULT '#7dd3fc',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A person. One token each, whatever number of projects they are in.
CREATE TABLE IF NOT EXISTS member (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    token      TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS membership (
    member_id    INTEGER NOT NULL REFERENCES member(id),
    workspace_id INTEGER NOT NULL REFERENCES workspace(id),
    joined_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (member_id, workspace_id)
);

-- THE LOG. Append-only. event.id is monotonic and doubles as the read cursor;
-- seq is the number people see, counted per project so each starts at #1.
CREATE TABLE IF NOT EXISTS event (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id  INTEGER NOT NULL REFERENCES workspace(id),
    seq           INTEGER,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    agent         TEXT NOT NULL DEFAULT 'unknown',
    kind          TEXT NOT NULL DEFAULT 'work',
    summary       TEXT NOT NULL,
    details       TEXT,          -- the body; summary is only the headline
    intent        TEXT,
    intent_source TEXT,          -- 'stated' | 'inferred' | NULL
    rejected_json TEXT,          -- [{"option": ..., "reason": ...}] or NULL
    refs_json     TEXT,          -- internal ids of earlier entries in this project
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

-- Per person AND per project: catching up in one must never mark another read.
CREATE TABLE IF NOT EXISTS cursor (
    member_id     INTEGER NOT NULL REFERENCES member(id),
    workspace_id  INTEGER NOT NULL REFERENCES workspace(id),
    last_event_id INTEGER NOT NULL DEFAULT 0,
    last_sync_at  TEXT,
    PRIMARY KEY (member_id, workspace_id)
);
"""

ROSTER = ["Adria", "Oscar", "Pau"]


def _seed_tokens() -> dict[str, str]:
    """Tokens come from the environment, never from this file.

    A token is the write key, so it does not belong in source control. Set
    MEMBER_TOKENS as "Adria:xxx,Oscar:yyy,Pau:zzz"; with nothing set, each person
    gets a random one on first boot, printed once at startup. Existing people
    keep the token they already have, so restarts are safe.
    """
    raw = os.environ.get("MEMBER_TOKENS", "").strip()
    tokens = {}
    for part in raw.split(","):
        name, _, token = part.partition(":")
        if name.strip() and token.strip():
            tokens[name.strip()] = token.strip()
    return tokens


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _migrate_to_projects() -> None:
    """Turn a one-project database into a many-project one, keeping its data.

    Before projects, each member row belonged to a single workspace and each
    person had a single cursor. Now a member is a person, membership says which
    projects, and the cursor is per project. SQLite cannot drop a column or
    change a primary key in place, so both tables are rebuilt and copied across,
    which is the documented procedure -- with foreign keys off, since the rows
    they point at are being swapped underneath them.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "member") or "workspace_id" not in _columns(conn, "member"):
            return
        # Must be set outside a transaction, or SQLite ignores it silently.
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS membership (
                   member_id INTEGER NOT NULL, workspace_id INTEGER NOT NULL,
                   joined_at TEXT NOT NULL DEFAULT (datetime('now')),
                   PRIMARY KEY (member_id, workspace_id))"""
        )
        conn.execute(
            "INSERT OR IGNORE INTO membership (member_id, workspace_id) "
            "SELECT id, workspace_id FROM member"
        )

        conn.execute(
            """CREATE TABLE cursor_new (
                   member_id INTEGER NOT NULL, workspace_id INTEGER NOT NULL,
                   last_event_id INTEGER NOT NULL DEFAULT 0, last_sync_at TEXT,
                   PRIMARY KEY (member_id, workspace_id))"""
        )
        if _has_table(conn, "cursor"):
            conn.execute(
                "INSERT INTO cursor_new (member_id, workspace_id, last_event_id, last_sync_at) "
                "SELECT c.member_id, m.workspace_id, c.last_event_id, c.last_sync_at "
                "FROM cursor c JOIN member m ON m.id = c.member_id"
            )
            conn.execute("DROP TABLE cursor")
        conn.execute("ALTER TABLE cursor_new RENAME TO cursor")

        conn.execute(
            """CREATE TABLE member_new (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                   token TEXT NOT NULL UNIQUE,
                   created_at TEXT NOT NULL DEFAULT (datetime('now')))"""
        )
        conn.execute(
            "INSERT INTO member_new (id, name, token, created_at) "
            "SELECT id, name, token, created_at FROM member"
        )
        conn.execute("DROP TABLE member")
        conn.execute("ALTER TABLE member_new RENAME TO member")
        conn.execute("COMMIT")
        print("migrated: members are now people, with a membership per project", flush=True)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to a database that predates them.

    CREATE TABLE IF NOT EXISTS silently skips an existing table, so a schema
    change would never reach a live workspace without this.
    """
    have = _columns(conn, "event")
    for column, ddl in [("details", "TEXT"), ("refs_json", "TEXT"), ("seq", "INTEGER")]:
        if column not in have:
            conn.execute(f"ALTER TABLE event ADD COLUMN {column} {ddl}")
            print(f"migrated: event.{column} added", flush=True)

    # Number any entry that predates per-project numbering, in the order it was
    # written. Idempotent: only rows still missing a number are touched.
    conn.execute(
        """UPDATE event SET seq = (
               SELECT COUNT(*) FROM event e2
               WHERE e2.workspace_id = event.workspace_id AND e2.id <= event.id)
           WHERE seq IS NULL"""
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_event_seq ON event(workspace_id, seq)")

    if "colour" not in _columns(conn, "workspace"):
        conn.execute(
            f"ALTER TABLE workspace ADD COLUMN colour TEXT NOT NULL DEFAULT '{DEFAULT_COLOUR}'"
        )


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
    """Create the schema, migrate an older one, and seed the people. Idempotent."""
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

    if DB_PATH.exists():
        _migrate_to_projects()

    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)

        row = conn.execute("SELECT id FROM workspace WHERE slug = 'demo'").fetchone()
        if row is None:
            demo_id = conn.execute(
                "INSERT INTO workspace (slug, title) VALUES ('demo', ?)", (title,)
            ).lastrowid
        else:
            demo_id = row["id"]

        configured = _seed_tokens()
        for name in ROSTER:
            # Keyed on name, not token: a person who already exists keeps their
            # token, so a restart never mints a duplicate or breaks live URLs.
            existing = conn.execute(
                "SELECT id, token FROM member WHERE name = ?", (name,)
            ).fetchone()
            if existing is None:
                member_id = conn.execute(
                    "INSERT INTO member (name, token) VALUES (?, ?)",
                    (name, configured.get(name) or secrets.token_urlsafe(12)),
                ).lastrowid
            else:
                member_id = existing["id"]
                if configured.get(name) and configured[name] != existing["token"]:
                    # MEMBER_TOKENS is authoritative when set, so a leaked token
                    # can be rotated without destroying anything to do it.
                    conn.execute(
                        "UPDATE member SET token = ? WHERE id = ?", (configured[name], member_id)
                    )
                    print(f"rotated token for {name}", flush=True)
            # Everyone on the roster starts in the default project. Projects made
            # later have exactly the members their creator chose.
            conn.execute(
                "INSERT OR IGNORE INTO membership (member_id, workspace_id) VALUES (?, ?)",
                (member_id, demo_id),
            )

        # Printing a token puts it in the deploy log, in screen shares and in
        # screenshots. Only do it when we minted it ourselves and nobody could
        # otherwise know it; when tokens are configured, the operator has them.
        people = conn.execute("SELECT name, token FROM member ORDER BY id").fetchall()
        if configured:
            print(f"People: {', '.join(p['name'] for p in people)} (tokens from MEMBER_TOKENS)",
                  flush=True)
        else:
            print("Connector URLs -- append these paths to your public host:", flush=True)
            for p in people:
                print(f"  {p['name']:<8} /u/{p['token']}/mcp", flush=True)
