"""Domain logic. Deliberately free of any MCP or HTTP import.

Every wrapper in server.py -- MCP for assistants that speak it, plain HTTP for
the ones that do not, and the web -- calls straight into these functions.

Everything about entries is scoped to one project. Two numbers exist for every
entry: `event.id`, internal, global and monotonic, which the read cursor runs
on; and `event.seq`, the #N people see, counted per project. The boundary
translates between them, so a model can only ever name entries in the project
it is in.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import passwords

# Nudge thresholds, per the three rules in the architecture doc.
STALE_MINUTES = 30
STALE_EVENTS = 8
COLLISION_MINUTES = 10

SESSION_DAYS = 30
INVITE_DAYS = 7
TRASH_DAYS = 30

KINDS = ("decision", "fact", "question", "work")

# Levels that survive into the standing brief. The rest is history.
IN_BRIEF = ("decision", "fact", "question")

PALETTE = ("#7dd3fc", "#a78bfa", "#34d399", "#fbbf24", "#f87171", "#f472b6", "#22d3ee", "#a3e635")

FORMER_MEMBER = "Former member"


class Refused(Exception):
    """An operation the caller is not allowed to do, with a reason a person can read.

    `code` lets the web show the reason in the reader's language; the English
    message stays for everything else that only reads text, like the tools.
    """

    def __init__(self, message: str, code: str | None = None, **params):
        super().__init__(message)
        self.code = code
        self.params = params


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


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
    is_dead: bool = False       # superseded by a later event
    member_gone: bool = False   # the author has since deleted their account


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


# --- accounts and web sessions ----------------------------------------------------

USERNAME = re.compile(r"^[\w.\-]{3,30}$")


def create_account(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row:
    """Sign up. The username is also the name people see.

    Letters in any alphabet are fine -- "Adrià" is a username -- but no spaces,
    so it can be typed back unambiguously at the sign-in box.
    """
    username = (username or "").strip()
    if not USERNAME.match(username):
        raise Refused("A username is 3 to 30 letters, numbers, dots, dashes or underscores.",
                      "e_username_invalid")
    if len(password or "") < passwords.MIN_LENGTH:
        raise Refused(f"A password needs at least {passwords.MIN_LENGTH} characters.",
                      "e_password_short", n=passwords.MIN_LENGTH)
    taken = conn.execute(
        "SELECT 1 FROM account WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    if taken:
        raise Refused("That username is taken.", "e_username_taken")
    acc_id = conn.execute(
        "INSERT INTO account (username, password_hash, name) VALUES (?, ?, ?)",
        (username, passwords.hash_password(password), username),
    ).lastrowid
    return conn.execute("SELECT * FROM account WHERE id = ?", (acc_id,)).fetchone()


def verify_login(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    """The account, if the password matches. The same work is done whether or not
    the username exists, so a failed sign-in does not reveal which ones are taken."""
    row = conn.execute(
        "SELECT * FROM account WHERE username = ? COLLATE NOCASE AND deleted_at IS NULL",
        ((username or "").strip(),),
    ).fetchone()
    ok = passwords.verify(password or "", row["password_hash"] if row else passwords.DUMMY)
    return row if (row is not None and ok) else None


def change_password(conn: sqlite3.Connection, account_id: int, current: str, new: str,
                    keep_session: str | None = None) -> None:
    """Change it, and sign out every other browser.

    If the password is being changed because someone else learnt it, leaving
    their session alive would defeat the point.
    """
    row = conn.execute("SELECT password_hash FROM account WHERE id = ?", (account_id,)).fetchone()
    if row is None or not passwords.verify(current or "", row["password_hash"]):
        raise Refused("The current password is not right.", "e_bad_current")
    if len(new or "") < passwords.MIN_LENGTH:
        raise Refused(f"A password needs at least {passwords.MIN_LENGTH} characters.",
                      "e_password_short", n=passwords.MIN_LENGTH)
    conn.execute("UPDATE account SET password_hash = ? WHERE id = ?",
                 (passwords.hash_password(new), account_id))
    conn.execute("DELETE FROM session WHERE account_id = ? AND id_hash != ?",
                 (account_id, _hash(keep_session) if keep_session else ""))


def account_from_google(conn: sqlite3.Connection, sub: str, email: str, name: str,
                        picture: str = "") -> sqlite3.Row:
    """Find or create the account for a Google identity.

    Keyed on Google's `sub`, never on the email address: an address can change
    hands or be renamed, `sub` is the one stable thing Google gives out.
    """
    row = conn.execute("SELECT * FROM account WHERE google_sub = ?", (sub,)).fetchone()
    if row is None:
        acc_id = conn.execute(
            "INSERT INTO account (google_sub, email, name, picture) VALUES (?, ?, ?, ?)",
            (sub, email, name or email.split("@")[0], picture or None),
        ).lastrowid
    else:
        acc_id = row["id"]
        conn.execute(
            "UPDATE account SET email = ?, name = ?, picture = ? WHERE id = ?",
            (email, name or row["name"], picture or row["picture"], acc_id),
        )
    return conn.execute("SELECT * FROM account WHERE id = ?", (acc_id,)).fetchone()


def create_session(conn: sqlite3.Connection, account_id: int) -> str:
    raw = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO session (id_hash, account_id, expires_at) "
        f"VALUES (?, ?, datetime('now', '+{SESSION_DAYS} days'))",
        (_hash(raw), account_id),
    )
    return raw


def account_for_session(conn: sqlite3.Connection, raw: str | None) -> sqlite3.Row | None:
    if not raw:
        return None
    return conn.execute(
        """SELECT a.* FROM session s JOIN account a ON a.id = s.account_id
           WHERE s.id_hash = ? AND s.expires_at > datetime('now') AND a.deleted_at IS NULL""",
        (_hash(raw),),
    ).fetchone()


def end_session(conn: sqlite3.Connection, raw: str | None) -> None:
    if raw:
        conn.execute("DELETE FROM session WHERE id_hash = ?", (_hash(raw),))


# --- connections: one token per assistant -----------------------------------------


def create_connection(conn: sqlite3.Connection, account_id: int, label: str) -> tuple[dict, str]:
    """Mint a connector token. Returns the row and the raw token, which is never
    stored and so can never be shown again."""
    raw = "sc_" + secrets.token_urlsafe(24)
    label = label.strip()[:60] or "Assistant"
    conn_id = conn.execute(
        "INSERT INTO connection (account_id, label, token_hash, prefix) VALUES (?, ?, ?, ?)",
        (account_id, label, _hash(raw), raw[:9]),
    ).lastrowid
    return _connection_view(conn.execute("SELECT * FROM connection WHERE id = ?",
                                         (conn_id,)).fetchone()), raw


def _connection_view(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "label": row["label"], "prefix": row["prefix"],
            "created_at": row["created_at"], "last_used_at": row["last_used_at"]}


def list_connections(conn: sqlite3.Connection, account_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM connection WHERE account_id = ? AND revoked_at IS NULL ORDER BY id",
        (account_id,),
    ).fetchall()
    return [_connection_view(r) for r in rows]


def revoke_connection(conn: sqlite3.Connection, account_id: int, connection_id: int) -> bool:
    cur = conn.execute(
        "UPDATE connection SET revoked_at = datetime('now') "
        "WHERE id = ? AND account_id = ? AND revoked_at IS NULL",
        (connection_id, account_id),
    )
    return cur.rowcount > 0


def account_for_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """Who a connector URL belongs to, if it is still live."""
    if not token:
        return None
    row = conn.execute(
        """SELECT a.*, c.id AS connection_id FROM connection c
           JOIN account a ON a.id = c.account_id
           WHERE c.token_hash = ? AND c.revoked_at IS NULL AND a.deleted_at IS NULL""",
        (_hash(token),),
    ).fetchone()
    if row is not None:
        # Throttled: "last used" is for spotting a dead or leaked connection,
        # and minute precision is not worth a write on every tool call.
        conn.execute(
            "UPDATE connection SET last_used_at = datetime('now') WHERE id = ? AND "
            "(last_used_at IS NULL OR last_used_at < datetime('now', '-10 minutes'))",
            (row["connection_id"],),
        )
    return row


# --- projects and who is in them ----------------------------------------------------


def projects_for(conn: sqlite3.Connection, account_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT w.*, ms.role FROM workspace w
           JOIN membership ms ON ms.workspace_id = w.id
           WHERE ms.account_id = ? AND w.deleted_at IS NULL ORDER BY w.id""",
        (account_id,),
    ).fetchall()


def resolve_project(conn: sqlite3.Connection, account_id: int, slug: str | None) -> sqlite3.Row | None:
    """The project, and this person's role in it, but only if they are in it.

    A project someone is not a member of resolves exactly like one that does not
    exist: nobody learns which slugs are taken by probing for them.
    """
    if not slug:
        return None
    return conn.execute(
        """SELECT w.*, ms.role FROM workspace w
           JOIN membership ms ON ms.workspace_id = w.id
           WHERE w.slug = ? AND ms.account_id = ? AND w.deleted_at IS NULL""",
        (slug.strip().lower(), account_id),
    ).fetchone()


def _owner_project(conn, account_id: int, slug: str) -> sqlite3.Row:
    ws = resolve_project(conn, account_id, slug)
    if ws is None:
        raise Refused("No such project.")
    if ws["role"] != "owner":
        raise Refused("Only the project's owner can do that.")
    return ws


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40] or "project"


def create_project(conn: sqlite3.Connection, creator_id: int, title: str, colour: str = "") -> sqlite3.Row:
    """Make a project with its creator as the owner. Others join by invite."""
    title = title.strip()[:80]
    if not title:
        raise Refused("A project needs a name.")
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
        "INSERT INTO membership (account_id, workspace_id, role) VALUES (?, ?, 'owner')",
        (creator_id, ws_id),
    )
    return resolve_project(conn, creator_id, slug)


def update_project(conn: sqlite3.Connection, account_id: int, slug: str,
                   title: str | None = None, colour: str | None = None) -> sqlite3.Row:
    ws = _owner_project(conn, account_id, slug)
    if title is not None:
        if not title.strip():
            raise Refused("A project needs a name.")
        conn.execute("UPDATE workspace SET title = ? WHERE id = ?", (title.strip()[:80], ws["id"]))
    if colour is not None and colour in PALETTE:
        conn.execute("UPDATE workspace SET colour = ? WHERE id = ?", (colour, ws["id"]))
    return resolve_project(conn, account_id, slug)


def members_of(conn: sqlite3.Connection, ws_id: int) -> list[dict]:
    """Who is in the project, their role, and which assistant each last wrote with.

    This is what the web can honestly show about "sessions". It cannot see
    anyone's conversations; it can see which client last wrote under whose name.
    """
    rows = conn.execute(
        """SELECT a.id AS account_id, a.name, a.picture, ms.role,
                  (SELECT e.agent FROM event e WHERE e.account_id = a.id AND e.workspace_id = ?
                   ORDER BY e.id DESC LIMIT 1) AS agent,
                  (SELECT e.created_at FROM event e WHERE e.account_id = a.id AND e.workspace_id = ?
                   ORDER BY e.id DESC LIMIT 1) AS last_at
           FROM account a JOIN membership ms ON ms.account_id = a.id
           WHERE ms.workspace_id = ? ORDER BY (ms.role = 'owner') DESC, ms.joined_at""",
        (ws_id, ws_id, ws_id),
    ).fetchall()
    return [dict(r) for r in rows]


def remove_member(conn: sqlite3.Connection, requester_id: int, slug: str, target_id: int) -> None:
    """An owner removing someone, or anyone leaving.

    Their entries stay. They are the project's history, and other people's
    decisions build on them -- removing a collaborator does not rewrite what
    they contributed, any more than it does in Git.
    """
    ws = resolve_project(conn, requester_id, slug)
    if ws is None:
        raise Refused("No such project.")
    leaving = requester_id == target_id
    if not leaving and ws["role"] != "owner":
        raise Refused("Only the project's owner can remove people.")
    target = conn.execute(
        "SELECT role FROM membership WHERE account_id = ? AND workspace_id = ?",
        (target_id, ws["id"]),
    ).fetchone()
    if target is None:
        raise Refused("That person is not in this project.")
    if target["role"] == "owner":
        # A project must never be left without anyone able to manage it.
        raise Refused("The owner cannot leave. Hand the project to someone else first, "
                      "or delete it.")
    conn.execute("DELETE FROM membership WHERE account_id = ? AND workspace_id = ?",
                 (target_id, ws["id"]))
    conn.execute("DELETE FROM cursor WHERE account_id = ? AND workspace_id = ?",
                 (target_id, ws["id"]))


def transfer_ownership(conn: sqlite3.Connection, owner_id: int, slug: str, target_id: int) -> None:
    ws = _owner_project(conn, owner_id, slug)
    if target_id == owner_id:
        raise Refused("You already own it.")
    target = conn.execute(
        "SELECT 1 FROM membership WHERE account_id = ? AND workspace_id = ?",
        (target_id, ws["id"]),
    ).fetchone()
    if target is None:
        raise Refused("That person is not in this project.")
    conn.execute("UPDATE membership SET role = 'owner' WHERE account_id = ? AND workspace_id = ?",
                 (target_id, ws["id"]))
    conn.execute("UPDATE membership SET role = 'member' WHERE account_id = ? AND workspace_id = ?",
                 (owner_id, ws["id"]))


def unread_for(conn: sqlite3.Connection, account_id: int, ws_id: int) -> int:
    """Entries by other people this person's assistant has not caught up on yet."""
    row = conn.execute(
        "SELECT last_event_id FROM cursor WHERE account_id = ? AND workspace_id = ?",
        (account_id, ws_id),
    ).fetchone()
    since = row["last_event_id"] if row else 0
    return conn.execute(
        "SELECT COUNT(*) AS n FROM event WHERE workspace_id = ? AND id > ? AND account_id != ?",
        (ws_id, since, account_id),
    ).fetchone()["n"]


# --- invites -------------------------------------------------------------------------


def create_invite(conn: sqlite3.Connection, owner_id: int, slug: str,
                  max_uses: int | None = None) -> dict:
    ws = _owner_project(conn, owner_id, slug)
    code = secrets.token_urlsafe(9)
    conn.execute(
        "INSERT INTO invite (workspace_id, code, created_by, expires_at, max_uses) "
        f"VALUES (?, ?, ?, datetime('now', '+{INVITE_DAYS} days'), ?)",
        (ws["id"], code, owner_id, max_uses if max_uses and max_uses > 0 else None),
    )
    return _invite_view(conn.execute("SELECT * FROM invite WHERE code = ?", (code,)).fetchone())


def _invite_view(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "code": row["code"], "expires_at": row["expires_at"],
            "uses": row["uses"], "max_uses": row["max_uses"]}


def list_invites(conn: sqlite3.Connection, owner_id: int, slug: str) -> list[dict]:
    ws = _owner_project(conn, owner_id, slug)
    rows = conn.execute(
        """SELECT * FROM invite WHERE workspace_id = ? AND revoked_at IS NULL
           AND expires_at > datetime('now') AND (max_uses IS NULL OR uses < max_uses)
           ORDER BY id DESC""",
        (ws["id"],),
    ).fetchall()
    return [_invite_view(r) for r in rows]


def revoke_invite(conn: sqlite3.Connection, owner_id: int, slug: str, invite_id: int) -> None:
    ws = _owner_project(conn, owner_id, slug)
    conn.execute("UPDATE invite SET revoked_at = datetime('now') WHERE id = ? AND workspace_id = ?",
                 (invite_id, ws["id"]))


def _live_invite(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT i.*, w.slug, w.title, w.colour, a.name AS inviter
           FROM invite i JOIN workspace w ON w.id = i.workspace_id
           JOIN account a ON a.id = i.created_by
           WHERE i.code = ? AND i.revoked_at IS NULL AND i.expires_at > datetime('now')
             AND (i.max_uses IS NULL OR i.uses < i.max_uses) AND w.deleted_at IS NULL""",
        (code,),
    ).fetchone()


def preview_invite(conn: sqlite3.Connection, code: str, account_id: int | None) -> dict | None:
    """What someone holding the link may see before joining: the project's name
    and who invited them. The link is the capability, so that is all it needs."""
    inv = _live_invite(conn, code)
    if inv is None:
        return None
    already = bool(account_id) and conn.execute(
        "SELECT 1 FROM membership WHERE account_id = ? AND workspace_id = ?",
        (account_id, inv["workspace_id"]),
    ).fetchone() is not None
    return {"slug": inv["slug"], "title": inv["title"], "colour": inv["colour"],
            "inviter": inv["inviter"], "already_member": already}


def accept_invite(conn: sqlite3.Connection, account_id: int, code: str) -> str:
    inv = _live_invite(conn, code)
    if inv is None:
        raise Refused("This invite has expired or is no longer valid.")
    added = conn.execute(
        "INSERT OR IGNORE INTO membership (account_id, workspace_id, role) VALUES (?, ?, 'member')",
        (account_id, inv["workspace_id"]),
    ).rowcount
    if added:
        conn.execute("UPDATE invite SET uses = uses + 1 WHERE id = ?", (inv["id"],))
    return inv["slug"]


# --- deleting things ---------------------------------------------------------------


def delete_project(conn: sqlite3.Connection, owner_id: int, slug: str) -> None:
    """Into the trash for 30 days, restorable by its owner, then gone for good."""
    ws = _owner_project(conn, owner_id, slug)
    conn.execute("UPDATE workspace SET deleted_at = datetime('now') WHERE id = ?", (ws["id"],))


def trash_for(conn: sqlite3.Connection, account_id: int) -> list[dict]:
    rows = conn.execute(
        f"""SELECT w.slug, w.title, w.colour, w.deleted_at,
                   datetime(w.deleted_at, '+{TRASH_DAYS} days') AS purge_at
            FROM workspace w JOIN membership ms ON ms.workspace_id = w.id
            WHERE ms.account_id = ? AND ms.role = 'owner' AND w.deleted_at IS NOT NULL
            ORDER BY w.deleted_at DESC""",
        (account_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def restore_project(conn: sqlite3.Connection, owner_id: int, slug: str) -> None:
    row = conn.execute(
        """SELECT w.id FROM workspace w JOIN membership ms ON ms.workspace_id = w.id
           WHERE w.slug = ? AND ms.account_id = ? AND ms.role = 'owner'
             AND w.deleted_at IS NOT NULL""",
        (slug, owner_id),
    ).fetchone()
    if row is None:
        raise Refused("Nothing in the trash by that name.")
    conn.execute("UPDATE workspace SET deleted_at = NULL WHERE id = ?", (row["id"],))


def _purge_project(conn: sqlite3.Connection, ws_id: int) -> None:
    """The only place anything in a project's log is ever deleted.

    Append-only means history is never rewritten. It does not mean nobody may
    delete their own project: this runs only for a project its owner chose to
    delete, or for one left with no members at all.
    """
    conn.execute("DELETE FROM section_revision WHERE section_id IN "
                 "(SELECT id FROM section WHERE workspace_id = ?)", (ws_id,))
    conn.execute("UPDATE section SET current_revision_id = NULL WHERE workspace_id = ?", (ws_id,))
    conn.execute("DELETE FROM artifact WHERE workspace_id = ?", (ws_id,))
    # Entries point at each other (supersedes); unhook them before deleting.
    conn.execute("UPDATE event SET supersedes_id = NULL, section_id = NULL WHERE workspace_id = ?",
                 (ws_id,))
    for table in ("event", "section", "cursor", "invite", "membership"):
        conn.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (ws_id,))
    conn.execute("DELETE FROM workspace WHERE id = ?", (ws_id,))


def purge_deleted_projects(conn: sqlite3.Connection) -> int:
    ids = [r["id"] for r in conn.execute(
        f"SELECT id FROM workspace WHERE deleted_at < datetime('now', '-{TRASH_DAYS} days')"
    )]
    for ws_id in ids:
        _purge_project(conn, ws_id)
    return len(ids)


def delete_account(conn: sqlite3.Connection, account_id: int) -> None:
    """Remove a person, without breaking anyone else's history.

    Projects they own alongside other people have to be handed over first --
    deleting the account must not silently orphan work others depend on. A
    project only they were in goes with them. Everywhere else they leave, and
    the entries they wrote stay, attributed to an anonymous former member.
    """
    blocking = conn.execute(
        """SELECT w.title FROM workspace w JOIN membership ms ON ms.workspace_id = w.id
           WHERE ms.account_id = ? AND ms.role = 'owner' AND w.deleted_at IS NULL
             AND (SELECT COUNT(*) FROM membership m2 WHERE m2.workspace_id = w.id) > 1""",
        (account_id,),
    ).fetchall()
    if blocking:
        names = ", ".join(f"«{r['title']}»" for r in blocking)
        raise Refused(f"You still own projects other people are in: {names}. "
                      "Hand them to someone else, or delete them, first.",
                      "e_owns_shared", names=names)

    solo = [r["workspace_id"] for r in conn.execute(
        """SELECT ms.workspace_id FROM membership ms
           WHERE ms.account_id = ? AND ms.role = 'owner'""",
        (account_id,),
    )]
    for ws_id in solo:
        _purge_project(conn, ws_id)

    conn.execute("DELETE FROM membership WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM cursor WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM session WHERE account_id = ?", (account_id,))
    conn.execute("UPDATE connection SET revoked_at = datetime('now') "
                 "WHERE account_id = ? AND revoked_at IS NULL", (account_id,))
    # The username is freed for someone else to take. Their entries point at this
    # row's id, not at the name, so a new "Oscar" is never mistaken for the old.
    conn.execute(
        "UPDATE account SET username = NULL, password_hash = NULL, google_sub = NULL, "
        "email = NULL, picture = NULL, name = ?, deleted_at = datetime('now') WHERE id = ?",
        (FORMER_MEMBER, account_id),
    )


# --- input normalisation ------------------------------------------------------------


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


# --- reading -------------------------------------------------------------------------


def _events_since(
    conn: sqlite3.Connection, ws_id: int, since_id: int, exclude_account: int | None
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
        SELECT e.*, a.name AS member_name, a.deleted_at AS member_deleted,
               s.key AS section_key, ar.url AS artifact_url, r.id AS revision_id
        FROM event e
        JOIN account a ON a.id = e.account_id
        LEFT JOIN section s ON s.id = e.section_id
        LEFT JOIN artifact ar ON ar.event_id = e.id
        LEFT JOIN section_revision r ON r.event_id = e.id
        WHERE e.workspace_id = ? AND e.id > ?
    """
    params: list = [ws_id, since_id]
    if exclude_account is not None:
        sql += " AND e.account_id != ?"
        params.append(exclude_account)
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
                member_gone=r["member_deleted"] is not None,
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


def _advance_cursor(conn: sqlite3.Connection, account_id: int, ws_id: int) -> tuple[int, int]:
    """Move this person's cursor in this project to the newest entry.

    Returns (internal id, per-project #N) of where it was, so the delta can be
    queried on the first and shown to people as the second.
    """
    row = conn.execute(
        "SELECT last_event_id FROM cursor WHERE account_id = ? AND workspace_id = ?",
        (account_id, ws_id),
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
        """INSERT INTO cursor (account_id, workspace_id, last_event_id, last_sync_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(account_id, workspace_id)
           DO UPDATE SET last_event_id = excluded.last_event_id, last_sync_at = datetime('now')""",
        (account_id, ws_id, newest),
    )
    return since, since_seq


def _staleness_hint(conn: sqlite3.Connection, ws_id: int, account: sqlite3.Row) -> str | None:
    """Rule 2: this is what triggers the closing ritual without anyone remembering it."""
    last = conn.execute(
        "SELECT id, created_at FROM event WHERE account_id = ? AND workspace_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (account["id"], ws_id),
    ).fetchone()
    if last is None:
        return None
    minutes = (_utcnow() - _parse(last["created_at"])).total_seconds() / 60
    others = conn.execute(
        "SELECT COUNT(*) AS n FROM event WHERE workspace_id = ? AND id > ? AND account_id != ?",
        (ws_id, last["id"], account["id"]),
    ).fetchone()["n"]
    if minutes >= STALE_MINUTES or others >= STALE_EVENTS:
        return (
            f"{account['name']} has not recorded anything here for {int(minutes)} min "
            f"({others} entries by others since). When you finish this piece of work, call "
            "`record` with a summary of what you did, what was being aimed for, and what "
            "was tried and dropped."
        )
    return None


def _collision_hint(
    conn: sqlite3.Connection, ws_id: int, account_id: int, section_key: str
) -> str | None:
    """Rule 3: someone else was just in this section."""
    row = conn.execute(
        """
        SELECT e.seq, a.name, e.created_at
        FROM event e JOIN account a ON a.id = e.account_id
        JOIN section s ON s.id = e.section_id
        WHERE e.workspace_id = ? AND s.key = ? AND e.account_id != ?
        ORDER BY e.id DESC LIMIT 1
        """,
        (ws_id, section_key, account_id),
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
        "SELECT COUNT(DISTINCT account_id) AS n FROM event "
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
    live = [e for e in _events_since(conn, ws_id, 0, exclude_account=None) if not e.is_dead]
    return Brief(
        decisions=[e for e in live if e.level == "decision"],
        facts=[e for e in live if e.level == "fact"],
        questions=[e for e in live if e.level == "question"],
    )


def version(conn: sqlite3.Connection, ws_id: int) -> tuple:
    """A cheap marker of 'has anything changed in this project'.

    Append-only tables mean the counters only ever move forward. Membership and
    the project's own name and colour are in it too, so someone joining or a
    rename shows up without a reload.
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
        "SELECT COUNT(*) AS n, GROUP_CONCAT(account_id || role) AS r "
        "FROM membership WHERE workspace_id = ?", (ws_id,)
    ).fetchone()
    ws = conn.execute("SELECT title, colour, deleted_at FROM workspace WHERE id = ?",
                      (ws_id,)).fetchone()
    return (ev, rev, ms["n"], ms["r"], ws["title"] if ws else None,
            ws["colour"] if ws else None, ws["deleted_at"] if ws else "gone")


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
        "events": [asdict(e) for e in _events_since(conn, ws_id, 0, exclude_account=None)],
        "brief": asdict(build_brief(conn, ws_id)),
    }


# --- the two operations ------------------------------------------------------------


def catch_up(conn: sqlite3.Connection, account: sqlite3.Row, ws: sqlite3.Row) -> Envelope:
    since, since_seq = _advance_cursor(conn, account["id"], ws["id"])
    hints = [h for h in [_staleness_hint(conn, ws["id"], account)] if h]
    return Envelope(
        result=f"Caught up as {account['name']} in project '{ws['slug']}' ({ws['title']}).",
        since_id=since_seq,
        new_events=_events_since(conn, ws["id"], since, exclude_account=None),
        document=_read_document(conn, ws["id"]),
        brief=build_brief(conn, ws["id"]),
        hints=hints,
        state=_state(conn, ws["id"]),
    )


def record(
    conn: sqlite3.Connection,
    account: sqlite3.Row,
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
    since, since_seq = _advance_cursor(conn, account["id"], ws_id)
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
        hint = _collision_hint(conn, ws_id, account["id"], section)
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
        INSERT INTO event (workspace_id, seq, account_id, agent, kind, summary, details, intent,
                           intent_source, rejected_json, refs_json, section_id, supersedes_id)
        VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM event WHERE workspace_id = ?),
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ws_id,
            ws_id,
            account["id"],
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
        "UPDATE cursor SET last_event_id = ? WHERE account_id = ? AND workspace_id = ?",
        (event_id, account["id"], ws_id),
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
        new_events=_events_since(conn, ws_id, since, exclude_account=account["id"]),
        hints=hints,
        state=_state(conn, ws_id),
    )
