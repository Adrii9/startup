"""Domain logic. Deliberately free of any MCP or HTTP import.

Both wrappers in server.py (MCP for assistants that speak it, plain HTTP for the
ones that do not) call straight into these two functions. That decoupling is what
keeps us from being hostage to any one platform's connector support.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# Nudge thresholds, per the three rules in the architecture doc.
STALE_MINUTES = 30
STALE_EVENTS = 8
COLLISION_MINUTES = 10


KINDS = ("decision", "fact", "question", "work")


@dataclass
class EventView:
    id: int
    member_name: str
    agent: str
    kind: str
    summary: str
    details: str | None
    intent: str | None
    intent_source: str | None
    rejected: list[dict]
    section_key: str | None
    supersedes_id: int | None
    artifact_url: str | None
    created_at: str
    is_dead: bool = False  # superseded by a later event


@dataclass
class SectionView:
    key: str
    title: str
    content: str
    updated_at: str


@dataclass
class Brief:
    """What is true *now*, as opposed to what happened.

    Computed from the log every time, never written. Entries collapse to one
    line each: this is an index, and its job is to stay small enough to sit in
    every assistant's context no matter how long the project runs.
    """

    decisions: list[EventView] = field(default_factory=list)
    facts: list[EventView] = field(default_factory=list)
    questions: list[EventView] = field(default_factory=list)


@dataclass
class Envelope:
    """What every tool returns: the result, plus the delta, always."""

    result: str
    since_id: int
    new_events: list[EventView] = field(default_factory=list)
    document: list[SectionView] | None = None
    brief: Brief | None = None
    hints: list[str] = field(default_factory=list)
    state: dict = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def resolve_member(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute("SELECT * FROM member WHERE token = ?", (token,)).fetchone()


def normalise_rejected(raw) -> list[dict]:
    """Be liberal in what we accept.

    Three different models will send this three different ways: a list of dicts,
    a list of strings, a single string, or a JSON string. All of them mean the
    same thing, so all of them are accepted.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return [{"option": raw, "reason": ""}]
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append({"option": item, "reason": ""})
        elif isinstance(item, dict):
            out.append(
                {
                    "option": str(item.get("option") or item.get("what") or ""),
                    "reason": str(item.get("reason") or item.get("why") or ""),
                }
            )
    return [r for r in out if r["option"]]


def _dead_event_ids(conn: sqlite3.Connection, workspace_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT DISTINCT supersedes_id FROM event "
        "WHERE workspace_id = ? AND supersedes_id IS NOT NULL",
        (workspace_id,),
    )
    return {r["supersedes_id"] for r in rows}


def _events_since(
    conn: sqlite3.Connection, workspace_id: int, since_id: int, exclude_member: int | None
) -> list[EventView]:
    sql = """
        SELECT e.*, m.name AS member_name, s.key AS section_key, a.url AS artifact_url
        FROM event e
        JOIN member m ON m.id = e.member_id
        LEFT JOIN section s ON s.id = e.section_id
        LEFT JOIN artifact a ON a.event_id = e.id
        WHERE e.workspace_id = ? AND e.id > ?
    """
    params: list = [workspace_id, since_id]
    if exclude_member is not None:
        sql += " AND e.member_id != ?"
        params.append(exclude_member)
    sql += " ORDER BY e.id"

    dead = _dead_event_ids(conn, workspace_id)
    return [
        EventView(
            id=r["id"],
            member_name=r["member_name"],
            agent=r["agent"],
            kind=r["kind"],
            summary=r["summary"],
            details=r["details"],
            intent=r["intent"],
            intent_source=r["intent_source"],
            rejected=json.loads(r["rejected_json"]) if r["rejected_json"] else [],
            section_key=r["section_key"],
            supersedes_id=r["supersedes_id"],
            artifact_url=r["artifact_url"],
            created_at=r["created_at"],
            is_dead=r["id"] in dead,
        )
        for r in conn.execute(sql, params)
    ]


def _read_document(conn: sqlite3.Connection, workspace_id: int) -> list[SectionView]:
    rows = conn.execute(
        """
        SELECT s.key, s.title, s.updated_at, COALESCE(r.content, '') AS content
        FROM section s
        LEFT JOIN section_revision r ON r.id = s.current_revision_id
        WHERE s.workspace_id = ?
        ORDER BY s.id
        """,
        (workspace_id,),
    )
    return [
        SectionView(key=r["key"], title=r["title"], content=r["content"], updated_at=r["updated_at"])
        for r in rows
    ]


def _advance_cursor(conn: sqlite3.Connection, member_id: int) -> int:
    """Return the old cursor, then move it to the newest event."""
    row = conn.execute(
        "SELECT last_event_id FROM cursor WHERE member_id = ?", (member_id,)
    ).fetchone()
    since = row["last_event_id"] if row else 0
    newest = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM event").fetchone()["m"]
    conn.execute(
        "INSERT INTO cursor (member_id, last_event_id, last_sync_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(member_id) DO UPDATE SET last_event_id = ?, last_sync_at = datetime('now')",
        (member_id, newest, newest),
    )
    return since


def _staleness_hint(conn: sqlite3.Connection, workspace_id: int, member: sqlite3.Row) -> str | None:
    """Rule 2: this is what triggers the closing ritual without anyone remembering it."""
    last = conn.execute(
        "SELECT id, created_at FROM event WHERE member_id = ? ORDER BY id DESC LIMIT 1",
        (member["id"],),
    ).fetchone()
    if last is None:
        return None
    minutes = (_utcnow() - _parse(last["created_at"])).total_seconds() / 60
    others = conn.execute(
        "SELECT COUNT(*) AS n FROM event WHERE workspace_id = ? AND id > ? AND member_id != ?",
        (workspace_id, last["id"], member["id"]),
    ).fetchone()["n"]
    if minutes >= STALE_MINUTES or others >= STALE_EVENTS:
        return (
            f"{member['name']} has not recorded anything for {int(minutes)} min "
            f"({others} entries by others since). When you finish this piece of work, call "
            "`record` with a summary of what you did, what was being aimed for, and what "
            "was tried and dropped."
        )
    return None


def _collision_hint(
    conn: sqlite3.Connection, workspace_id: int, member_id: int, section_key: str
) -> str | None:
    """Rule 3: someone else was just in this section."""
    row = conn.execute(
        """
        SELECT e.id, m.name, e.created_at
        FROM event e JOIN member m ON m.id = e.member_id
        JOIN section s ON s.id = e.section_id
        WHERE e.workspace_id = ? AND s.key = ? AND e.member_id != ?
        ORDER BY e.id DESC LIMIT 1
        """,
        (workspace_id, section_key, member_id),
    ).fetchone()
    if row is None:
        return None
    minutes = (_utcnow() - _parse(row["created_at"])).total_seconds() / 60
    if minutes <= COLLISION_MINUTES:
        return (
            f"{row['name']} touched section '{section_key}' {int(minutes)} min ago "
            f"(entry #{row['id']}). Read it before overwriting."
        )
    return None


def _state(conn: sqlite3.Connection, workspace_id: int) -> dict:
    ws = conn.execute("SELECT title FROM workspace WHERE id = ?", (workspace_id,)).fetchone()
    sections = conn.execute(
        "SELECT COUNT(*) AS n FROM section WHERE workspace_id = ?", (workspace_id,)
    ).fetchone()["n"]
    events = conn.execute(
        "SELECT COUNT(*) AS n FROM event WHERE workspace_id = ?", (workspace_id,)
    ).fetchone()["n"]
    members = conn.execute(
        "SELECT COUNT(DISTINCT member_id) AS n FROM event "
        "WHERE workspace_id = ? AND created_at >= datetime('now', '-1 day')",
        (workspace_id,),
    ).fetchone()["n"]
    return {
        "title": ws["title"],
        "sections": sections,
        "events": events,
        "members_active_today": members,
    }


def build_brief(conn: sqlite3.Connection, ws_id: int) -> Brief:
    """Everything still in force, grouped by kind.

    'Still in force' is simply 'not superseded by a later entry', which is why
    supersedes_id doubles as the way to answer a question or reverse a decision
    -- one pointer, three meanings, no extra schema.
    """
    live = [e for e in _events_since(conn, ws_id, 0, exclude_member=None) if not e.is_dead]
    return Brief(
        decisions=[e for e in live if e.kind == "decision"],
        facts=[e for e in live if e.kind == "fact"],
        questions=[e for e in live if e.kind == "question"],
    )


def workspace_id(conn: sqlite3.Connection, slug: str = "demo") -> int:
    return conn.execute("SELECT id FROM workspace WHERE slug = ?", (slug,)).fetchone()["id"]


def version(conn: sqlite3.Connection, ws_id: int) -> tuple[int, int]:
    """A cheap marker of 'has anything changed'.

    Two integers are enough because both tables are append-only, so neither
    counter can ever move backwards.
    """
    ev = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM event WHERE workspace_id = ?", (ws_id,)
    ).fetchone()["m"]
    rev = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM section_revision").fetchone()["m"]
    return (ev, rev)


def snapshot(conn: sqlite3.Connection, ws_id: int) -> dict:
    """Everything the human-facing page needs, in one read."""
    state = _state(conn, ws_id)
    brief = build_brief(conn, ws_id)
    return {
        "title": state["title"],
        "members_active_today": state["members_active_today"],
        "sections": [asdict(s) for s in _read_document(conn, ws_id)],
        "events": [asdict(e) for e in _events_since(conn, ws_id, 0, exclude_member=None)],
        "brief": asdict(brief),
    }


# --- the two operations ------------------------------------------------------


def catch_up(conn: sqlite3.Connection, member: sqlite3.Row) -> Envelope:
    workspace_id = member["workspace_id"]
    since = _advance_cursor(conn, member["id"])
    hints = [h for h in [_staleness_hint(conn, workspace_id, member)] if h]
    return Envelope(
        result=f"Caught up as {member['name']}.",
        since_id=since,
        new_events=_events_since(conn, workspace_id, since, exclude_member=None),
        document=_read_document(conn, workspace_id),
        brief=build_brief(conn, workspace_id),
        hints=hints,
        state=_state(conn, workspace_id),
    )


def record(
    conn: sqlite3.Connection,
    member: sqlite3.Row,
    *,
    agent: str = "unknown",
    summary: str,
    details: str = "",
    kind: str = "",
    intent: str = "",
    intent_source: str = "stated",
    rejected=None,
    section: str = "",
    section_title: str = "",
    content: str = "",
    artifact: str = "",
    supersedes: int = 0,
) -> Envelope:
    workspace_id = member["workspace_id"]
    rejected_list = normalise_rejected(rejected)

    # The delta is computed against the cursor BEFORE this write, so the caller
    # never sees its own entry echoed back as news.
    since = _advance_cursor(conn, member["id"])
    hints: list[str] = []

    section_id = None
    if section:
        hint = _collision_hint(conn, workspace_id, member["id"], section)
        if hint:
            hints.append(hint)
        row = conn.execute(
            "SELECT id FROM section WHERE workspace_id = ? AND key = ?", (workspace_id, section)
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO section (workspace_id, key, title) VALUES (?, ?, ?)",
                (workspace_id, section, section_title or section.replace("-", " ").title()),
            )
            section_id = cur.lastrowid
        else:
            section_id = row["id"]

    if kind not in KINDS:
        # Infer rather than reject: a wrong guess here is recoverable, a failed
        # tool call in the middle of someone's work is not.
        kind = "decision" if (rejected_list or supersedes) else "work"

    cur = conn.execute(
        """
        INSERT INTO event (workspace_id, member_id, agent, kind, summary, details, intent,
                           intent_source, rejected_json, section_id, supersedes_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            member["id"],
            agent,
            kind,
            summary,
            details or None,
            intent or None,
            intent_source if intent else None,
            json.dumps(rejected_list, ensure_ascii=False) if rejected_list else None,
            section_id,
            supersedes or None,
        ),
    )
    event_id = cur.lastrowid

    if section_id and content:
        rev = conn.execute(
            "INSERT INTO section_revision (section_id, event_id, content) VALUES (?, ?, ?)",
            (section_id, event_id, content),
        )
        conn.execute(
            "UPDATE section SET current_revision_id = ?, updated_at = datetime('now') WHERE id = ?",
            (rev.lastrowid, section_id),
        )

    if artifact:
        conn.execute(
            "INSERT INTO artifact (workspace_id, event_id, url) VALUES (?, ?, ?)",
            (workspace_id, event_id, artifact),
        )

    # The cursor was advanced before the insert, so bring it past our own event.
    conn.execute(
        "UPDATE cursor SET last_event_id = ? WHERE member_id = ?", (event_id, member["id"])
    )

    # Rule 1: the anti-fabrication nudge, delivered at the exact moment it lands.
    if not intent:
        hints.append(
            "No intent recorded for this entry. If the person said what they were "
            "after, add it with another `record` call. If they did not, leave it blank "
            "-- do not guess."
        )

    return Envelope(
        result=f"Recorded as #{event_id}.",
        since_id=since,
        new_events=_events_since(conn, workspace_id, since, exclude_member=member["id"]),
        hints=hints,
        state=_state(conn, workspace_id),
    )
