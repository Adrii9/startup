"""Domain logic. Deliberately free of any MCP or HTTP import.

Both wrappers in server.py (MCP for assistants that speak it, plain HTTP for the
ones that do not) call straight into these functions. That decoupling is what
keeps us from being hostage to any one platform's connector support.

Everything here is scoped to one project. Two numbers exist for every entry:
`event.id`, internal, global and monotonic, which the read cursor runs on; and
`event.seq`, the #N people see, counted per project. The boundary translates
between them, so a model can only ever name entries in the project it is in.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# Nudge thresholds, per the three rules in the architecture doc.
STALE_MINUTES = 30
STALE_EVENTS = 8
COLLISION_MINUTES = 10


KINDS = ("decision", "fact", "question", "work")

# Levels that survive into the standing brief. The rest is history.
IN_BRIEF = ("decision", "fact", "question")

PALETTE = ("#7dd3fc", "#a78bfa", "#34d399", "#fbbf24", "#f87171", "#f472b6", "#22d3ee", "#a3e635")


def derive_level(
    *, supersedes_id=None, artifact_url=None, rejected=(), kind="", section_key=None, has_content=False
) -> tuple[str, str]:
    """What an entry counts as, and why.

    Derived from the fields the writer actually filled in, never from what they
    claim. A model cannot inflate the importance of its own entry, and three
    different models cannot drift apart on what counts as a decision, because
    none of them is being asked to judge.

    Structure can only ever *promote*, never demote. A declared fact answering
    an open question stays a fact; work that dropped an option becomes a
    decision whether or not the writer thought to say so. Within plain work, an
    artifact outranks a dropped option, so producing a file with a choice along
    the way does not crowd the brief.
    """
    # Declaration sets the category where the writer gave one. `supersedes` is
    # orthogonal to it: it says "this replaces that", and that can just as well
    # be a question being answered or a fact corrected as a decision reversed.
    if kind == "fact":
        return "fact", "recorded as a fact"
    if kind == "question":
        return "question", "recorded as a question"
    if kind == "decision":
        return "decision", "recorded as a decision"

    # Nothing useful declared: let the structure speak for whatever came in as
    # plain work, promoting it when it turns out to be more than that.
    if supersedes_id:
        return "decision", "supersedes is set"
    if artifact_url:
        return "artifact", "an artifact came with it"
    if rejected:
        return "decision", "rejected is not empty"
    if section_key and has_content:
        return "write", "section and content came with it"
    return "note", "only a summary came with it"


@dataclass
class EventView:
    id: int                     # the per-project #N, never the internal id
    member_name: str
    agent: str
    kind: str
    summary: str
    details: str | None
    intent: str | None
    intent_source: str | None
    rejected: list[dict]
    section_key: str | None
    supersedes_id: int | None   # also a per-project #N
    artifact_url: str | None
    created_at: str
    refs: list[int] = field(default_factory=list)
    level: str = "note"
    level_source: str = ""
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
    since_id: int               # per-project #N of the last entry already seen
    new_events: list[EventView] = field(default_factory=list)
    document: list[SectionView] | None = None
    brief: Brief | None = None
    hints: list[str] = field(default_factory=list)
    state: dict = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


# --- people and projects -----------------------------------------------------


def resolve_member(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute("SELECT * FROM member WHERE token = ?", (token,)).fetchone()


def projects_for(conn: sqlite3.Connection, member_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT w.* FROM workspace w
           JOIN membership ms ON ms.workspace_id = w.id
           WHERE ms.member_id = ? ORDER BY w.id""",
        (member_id,),
    ).fetchall()


def resolve_project(conn: sqlite3.Connection, member_id: int, slug: str | None) -> sqlite3.Row | None:
    """The project, but only if this person is in it.

    A project someone is not a member of resolves exactly like one that does not
    exist: nobody learns which slugs are taken by probing for them.
    """
    if not slug:
        return None
    return conn.execute(
        """SELECT w.* FROM workspace w
           JOIN membership ms ON ms.workspace_id = w.id
           WHERE w.slug = ? AND ms.member_id = ?""",
        (slug.strip().lower(), member_id),
    ).fetchone()


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40] or "project"


def create_project(
    conn: sqlite3.Connection, creator_id: int, title: str, colour: str = "", members: list[str] = ()
) -> sqlite3.Row:
    """Make a project, with its creator in it and whoever else they named."""
    title = title.strip()[:80] or "Untitled"
    base = slugify(title)
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM workspace WHERE slug = ?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    if colour not in PALETTE:
        count = conn.execute("SELECT COUNT(*) AS n FROM workspace").fetchone()["n"]
        colour = PALETTE[count % len(PALETTE)]

    ws_id = conn.execute(
        "INSERT INTO workspace (slug, title, colour) VALUES (?, ?, ?)", (slug, title, colour)
    ).lastrowid
    conn.execute(
        "INSERT INTO membership (member_id, workspace_id) VALUES (?, ?)", (creator_id, ws_id)
    )
    for name in members:
        add_member(conn, ws_id, name)
    return conn.execute("SELECT * FROM workspace WHERE id = ?", (ws_id,)).fetchone()


def add_member(conn: sqlite3.Connection, ws_id: int, name: str) -> sqlite3.Row | None:
    person = conn.execute("SELECT * FROM member WHERE name = ?", (name.strip(),)).fetchone()
    if person is None:
        return None
    conn.execute(
        "INSERT OR IGNORE INTO membership (member_id, workspace_id) VALUES (?, ?)",
        (person["id"], ws_id),
    )
    return person


def everyone(conn: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM member ORDER BY id")]


def members_of(conn: sqlite3.Connection, ws_id: int) -> list[dict]:
    """Who is in the project, and which assistant each last wrote with.

    This is what the web can honestly show about "sessions". It cannot see
    anyone's conversations; it can see which client last wrote under whose name.
    """
    rows = conn.execute(
        """SELECT m.name,
                  (SELECT e.agent FROM event e WHERE e.member_id = m.id AND e.workspace_id = ?
                   ORDER BY e.id DESC LIMIT 1) AS agent,
                  (SELECT e.created_at FROM event e WHERE e.member_id = m.id AND e.workspace_id = ?
                   ORDER BY e.id DESC LIMIT 1) AS last_at
           FROM member m JOIN membership ms ON ms.member_id = m.id
           WHERE ms.workspace_id = ? ORDER BY m.id""",
        (ws_id, ws_id, ws_id),
    ).fetchall()
    return [dict(r) for r in rows]


def unread_for(conn: sqlite3.Connection, member_id: int, ws_id: int) -> int:
    """Entries by other people this person's assistant has not caught up on yet."""
    row = conn.execute(
        "SELECT last_event_id FROM cursor WHERE member_id = ? AND workspace_id = ?",
        (member_id, ws_id),
    ).fetchone()
    since = row["last_event_id"] if row else 0
    return conn.execute(
        "SELECT COUNT(*) AS n FROM event WHERE workspace_id = ? AND id > ? AND member_id != ?",
        (ws_id, since, member_id),
    ).fetchone()["n"]


# --- input normalisation ------------------------------------------------------


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


def normalise_refs(raw) -> list[int]:
    """Entry numbers this one builds on. Accept whatever shape a model sends."""
    if raw is None:
        return []
    if isinstance(raw, (int, str)):
        raw = [raw]
    out = []
    for item in raw:
        try:
            out.append(int(str(item).lstrip("#").strip()))
        except ValueError:
            continue
    return [n for n in dict.fromkeys(out) if n > 0]


def _seq_to_id(conn: sqlite3.Connection, ws_id: int, seq: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM event WHERE workspace_id = ? AND seq = ?", (ws_id, seq)
    ).fetchone()
    return row["id"] if row else None


# --- reading -----------------------------------------------------------------


def _events_since(
    conn: sqlite3.Connection, ws_id: int, since_id: int, exclude_member: int | None
) -> list[EventView]:
    seq_of = {
        r["id"]: r["seq"]
        for r in conn.execute("SELECT id, seq FROM event WHERE workspace_id = ?", (ws_id,))
    }
    dead = {
        r["supersedes_id"]
        for r in conn.execute(
            "SELECT DISTINCT supersedes_id FROM event WHERE workspace_id = ? "
            "AND supersedes_id IS NOT NULL",
            (ws_id,),
        )
    }

    sql = """
        SELECT e.*, m.name AS member_name, s.key AS section_key, a.url AS artifact_url,
               r.id AS revision_id
        FROM event e
        JOIN member m ON m.id = e.member_id
        LEFT JOIN section s ON s.id = e.section_id
        LEFT JOIN artifact a ON a.event_id = e.id
        LEFT JOIN section_revision r ON r.event_id = e.id
        WHERE e.workspace_id = ? AND e.id > ?
    """
    params: list = [ws_id, since_id]
    if exclude_member is not None:
        sql += " AND e.member_id != ?"
        params.append(exclude_member)
    sql += " ORDER BY e.id"

    out = []
    for r in conn.execute(sql, params):
        rejected = json.loads(r["rejected_json"]) if r["rejected_json"] else []
        level, source = derive_level(
            supersedes_id=r["supersedes_id"],
            artifact_url=r["artifact_url"],
            rejected=rejected,
            kind=r["kind"],
            section_key=r["section_key"],
            has_content=r["revision_id"] is not None,
        )
        refs = json.loads(r["refs_json"]) if r["refs_json"] else []
        out.append(
            EventView(
                id=r["seq"],
                member_name=r["member_name"],
                agent=r["agent"],
                kind=r["kind"],
                summary=r["summary"],
                details=r["details"],
                intent=r["intent"],
                intent_source=r["intent_source"],
                rejected=rejected,
                section_key=r["section_key"],
                supersedes_id=seq_of.get(r["supersedes_id"]),
                artifact_url=r["artifact_url"],
                created_at=r["created_at"],
                refs=[seq_of[x] for x in refs if x in seq_of],
                level=level,
                level_source=source,
                is_dead=r["id"] in dead,
            )
        )
    return out


def _read_document(conn: sqlite3.Connection, ws_id: int) -> list[SectionView]:
    rows = conn.execute(
        """
        SELECT s.key, s.title, s.updated_at, COALESCE(r.content, '') AS content
        FROM section s
        LEFT JOIN section_revision r ON r.id = s.current_revision_id
        WHERE s.workspace_id = ?
        ORDER BY s.id
        """,
        (ws_id,),
    )
    return [
        SectionView(key=r["key"], title=r["title"], content=r["content"], updated_at=r["updated_at"])
        for r in rows
    ]


def _advance_cursor(conn: sqlite3.Connection, member_id: int, ws_id: int) -> tuple[int, int]:
    """Move this person's cursor in this project to the newest entry.

    Returns (internal id, per-project #N) of where it was, so the delta can be
    queried on the first and shown to people as the second.
    """
    row = conn.execute(
        "SELECT last_event_id FROM cursor WHERE member_id = ? AND workspace_id = ?",
        (member_id, ws_id),
    ).fetchone()
    since = row["last_event_id"] if row else 0
    since_seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS s FROM event WHERE workspace_id = ? AND id <= ?",
        (ws_id, since),
    ).fetchone()["s"]
    newest = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM event WHERE workspace_id = ?", (ws_id,)
    ).fetchone()["m"]
    conn.execute(
        """INSERT INTO cursor (member_id, workspace_id, last_event_id, last_sync_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(member_id, workspace_id)
           DO UPDATE SET last_event_id = excluded.last_event_id, last_sync_at = datetime('now')""",
        (member_id, ws_id, newest),
    )
    return since, since_seq


def _staleness_hint(conn: sqlite3.Connection, ws_id: int, member: sqlite3.Row) -> str | None:
    """Rule 2: this is what triggers the closing ritual without anyone remembering it."""
    last = conn.execute(
        "SELECT id, created_at FROM event WHERE member_id = ? AND workspace_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (member["id"], ws_id),
    ).fetchone()
    if last is None:
        return None
    minutes = (_utcnow() - _parse(last["created_at"])).total_seconds() / 60
    others = conn.execute(
        "SELECT COUNT(*) AS n FROM event WHERE workspace_id = ? AND id > ? AND member_id != ?",
        (ws_id, last["id"], member["id"]),
    ).fetchone()["n"]
    if minutes >= STALE_MINUTES or others >= STALE_EVENTS:
        return (
            f"{member['name']} has not recorded anything here for {int(minutes)} min "
            f"({others} entries by others since). When you finish this piece of work, call "
            "`record` with a summary of what you did, what was being aimed for, and what "
            "was tried and dropped."
        )
    return None


def _collision_hint(
    conn: sqlite3.Connection, ws_id: int, member_id: int, section_key: str
) -> str | None:
    """Rule 3: someone else was just in this section."""
    row = conn.execute(
        """
        SELECT e.seq, m.name, e.created_at
        FROM event e JOIN member m ON m.id = e.member_id
        JOIN section s ON s.id = e.section_id
        WHERE e.workspace_id = ? AND s.key = ? AND e.member_id != ?
        ORDER BY e.id DESC LIMIT 1
        """,
        (ws_id, section_key, member_id),
    ).fetchone()
    if row is None:
        return None
    minutes = (_utcnow() - _parse(row["created_at"])).total_seconds() / 60
    if minutes <= COLLISION_MINUTES:
        return (
            f"{row['name']} touched section '{section_key}' {int(minutes)} min ago "
            f"(entry #{row['seq']}). Read it before overwriting."
        )
    return None


def _state(conn: sqlite3.Connection, ws_id: int) -> dict:
    ws = conn.execute("SELECT slug, title FROM workspace WHERE id = ?", (ws_id,)).fetchone()
    sections = conn.execute(
        "SELECT COUNT(*) AS n FROM section WHERE workspace_id = ?", (ws_id,)
    ).fetchone()["n"]
    events = conn.execute(
        "SELECT COUNT(*) AS n FROM event WHERE workspace_id = ?", (ws_id,)
    ).fetchone()["n"]
    members = conn.execute(
        "SELECT COUNT(DISTINCT member_id) AS n FROM event "
        "WHERE workspace_id = ? AND created_at >= datetime('now', '-1 day')",
        (ws_id,),
    ).fetchone()["n"]
    return {
        "slug": ws["slug"],
        "title": ws["title"],
        "sections": sections,
        "events": events,
        "members_active_today": members,
    }


def build_brief(conn: sqlite3.Connection, ws_id: int) -> Brief:
    """Everything still in force, grouped by what it counts as.

    'Still in force' is simply 'not superseded by a later entry', which is why
    supersedes doubles as the way to answer a question or reverse a decision --
    one pointer, three meanings, no extra schema.
    """
    live = [e for e in _events_since(conn, ws_id, 0, exclude_member=None) if not e.is_dead]
    return Brief(
        decisions=[e for e in live if e.level == "decision"],
        facts=[e for e in live if e.level == "fact"],
        questions=[e for e in live if e.level == "question"],
    )


def version(conn: sqlite3.Connection, ws_id: int) -> tuple[int, int, int]:
    """A cheap marker of 'has anything changed in this project'.

    Append-only tables mean the counters only ever move forward. Membership is
    in it too, so someone being added shows up without a reload.
    """
    ev = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM event WHERE workspace_id = ?", (ws_id,)
    ).fetchone()["m"]
    rev = conn.execute(
        """SELECT COALESCE(MAX(r.id), 0) AS m FROM section_revision r
           JOIN section s ON s.id = r.section_id WHERE s.workspace_id = ?""",
        (ws_id,),
    ).fetchone()["m"]
    ms = conn.execute(
        "SELECT COUNT(*) AS n FROM membership WHERE workspace_id = ?", (ws_id,)
    ).fetchone()["n"]
    return (ev, rev, ms)


def snapshot(conn: sqlite3.Connection, ws_id: int) -> dict:
    """Everything the human-facing page needs about one project, in one read."""
    state = _state(conn, ws_id)
    colour = conn.execute("SELECT colour FROM workspace WHERE id = ?", (ws_id,)).fetchone()["colour"]
    return {
        "slug": state["slug"],
        "title": state["title"],
        "colour": colour,
        "members_active_today": state["members_active_today"],
        "members": members_of(conn, ws_id),
        "sections": [asdict(s) for s in _read_document(conn, ws_id)],
        "events": [asdict(e) for e in _events_since(conn, ws_id, 0, exclude_member=None)],
        "brief": asdict(build_brief(conn, ws_id)),
    }


# --- the two operations ------------------------------------------------------


def catch_up(conn: sqlite3.Connection, member: sqlite3.Row, ws: sqlite3.Row) -> Envelope:
    since, since_seq = _advance_cursor(conn, member["id"], ws["id"])
    hints = [h for h in [_staleness_hint(conn, ws["id"], member)] if h]
    return Envelope(
        result=f"Caught up as {member['name']} in project '{ws['slug']}' ({ws['title']}).",
        since_id=since_seq,
        new_events=_events_since(conn, ws["id"], since, exclude_member=None),
        document=_read_document(conn, ws["id"]),
        brief=build_brief(conn, ws["id"]),
        hints=hints,
        state=_state(conn, ws["id"]),
    )


def record(
    conn: sqlite3.Connection,
    member: sqlite3.Row,
    ws: sqlite3.Row,
    *,
    agent: str = "unknown",
    summary: str,
    details: str = "",
    kind: str = "",
    intent: str = "",
    intent_source: str = "stated",
    rejected=None,
    refs=None,
    section: str = "",
    section_title: str = "",
    content: str = "",
    artifact: str = "",
    supersedes: int = 0,
) -> Envelope:
    ws_id = ws["id"]
    rejected_list = normalise_rejected(rejected)

    # The delta is computed against the cursor BEFORE this write, so the caller
    # never sees its own entry echoed back as news.
    since, since_seq = _advance_cursor(conn, member["id"], ws_id)
    hints: list[str] = []

    # Entry numbers arrive as the #N people see. Translate them inside this
    # project only: a number from another project simply does not resolve, so
    # nothing can reach across. Say so rather than dropping it silently.
    supersedes_pk = None
    if supersedes:
        supersedes_pk = _seq_to_id(conn, ws_id, int(supersedes))
        if supersedes_pk is None:
            hints.append(
                f"Entry #{supersedes} does not exist in this project, so nothing was "
                "superseded. Check the number with catch_up."
            )
    ref_pks, missing = [], []
    for n in normalise_refs(refs):
        pk = _seq_to_id(conn, ws_id, n)
        (ref_pks if pk else missing).append(pk or n)
    if missing:
        hints.append(
            f"Entries {', '.join('#' + str(n) for n in missing)} do not exist in this "
            "project and were left out of refs."
        )

    section_id = None
    if section:
        hint = _collision_hint(conn, ws_id, member["id"], section)
        if hint:
            hints.append(hint)
        row = conn.execute(
            "SELECT id FROM section WHERE workspace_id = ? AND key = ?", (ws_id, section)
        ).fetchone()
        if row is None:
            section_id = conn.execute(
                "INSERT INTO section (workspace_id, key, title) VALUES (?, ?, ?)",
                (ws_id, section, section_title or section.replace("-", " ").title()),
            ).lastrowid
        else:
            section_id = row["id"]

    # `kind` is only a declaration; what an entry counts as is derived on read by
    # derive_level. An unrecognised value costs nothing, so never reject.
    if kind not in KINDS:
        kind = "work"

    # seq is computed inside the INSERT, as one statement, so two people writing
    # at the same moment cannot both be handed the same number.
    event_id = conn.execute(
        """
        INSERT INTO event (workspace_id, seq, member_id, agent, kind, summary, details, intent,
                           intent_source, rejected_json, refs_json, section_id, supersedes_id)
        VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM event WHERE workspace_id = ?),
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ws_id,
            ws_id,
            member["id"],
            agent,
            kind,
            summary,
            details or None,
            intent or None,
            intent_source if intent else None,
            json.dumps(rejected_list, ensure_ascii=False) if rejected_list else None,
            json.dumps(ref_pks) if ref_pks else None,
            section_id,
            supersedes_pk,
        ),
    ).lastrowid
    seq = conn.execute("SELECT seq FROM event WHERE id = ?", (event_id,)).fetchone()["seq"]

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
            (ws_id, event_id, artifact),
        )

    # The cursor was advanced before the insert, so bring it past our own event.
    conn.execute(
        "UPDATE cursor SET last_event_id = ? WHERE member_id = ? AND workspace_id = ?",
        (event_id, member["id"], ws_id),
    )

    # Rule 1: the anti-fabrication nudge, delivered at the exact moment it lands.
    if not intent:
        hints.append(
            "No intent recorded for this entry. If the person said what they were "
            "after, add it with another `record` call. If they did not, leave it blank "
            "-- do not guess."
        )

    return Envelope(
        result=f"Recorded as #{seq} in project '{ws['slug']}'.",
        since_id=since_seq,
        new_events=_events_since(conn, ws_id, since, exclude_member=member["id"]),
        hints=hints,
        state=_state(conn, ws_id),
    )
