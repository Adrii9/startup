"""The write/read loop, projects, and the web's signed-in surface.

Runs entirely without an assistant. Keep it that way -- opening three chat
windows should be for validating the magic moment, not for debugging a NULL.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

# Before anything imports app.server, whose import runs init_db: keep that
# first boot away from the real workspace.db in the project folder.
os.environ.setdefault("WORKSPACE_DB", os.path.join(tempfile.mkdtemp(), "import.db"))

import pytest  # noqa: E402

from app import db, render, store  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db("Uni project")
    with db.connect() as c:
        yield c


def member(conn, name):
    return conn.execute("SELECT * FROM member WHERE name = ?", (name,)).fetchone()


def project(conn, slug="demo"):
    return conn.execute("SELECT * FROM workspace WHERE slug = ?", (slug,)).fetchone()


def rec(conn, name, ws=None, **kw):
    return store.record(conn, member(conn, name), ws or project(conn), **kw)


def cu(conn, name, ws=None):
    return render.render(store.catch_up(conn, member(conn, name), ws or project(conn)))


def standing(out):
    return out.split("WHERE THINGS STAND")[1].split("DOCUMENT")[0]


# --- migration ---------------------------------------------------------------

PRE_PROJECTS_SCHEMA = """
CREATE TABLE workspace (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE member (id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspace(id), name TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE event (id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspace(id),
    member_id INTEGER NOT NULL REFERENCES member(id), agent TEXT NOT NULL DEFAULT 'unknown',
    kind TEXT NOT NULL DEFAULT 'work', summary TEXT NOT NULL, intent TEXT, intent_source TEXT,
    rejected_json TEXT, section_id INTEGER, supersedes_id INTEGER REFERENCES event(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE cursor (member_id INTEGER PRIMARY KEY REFERENCES member(id),
    last_event_id INTEGER NOT NULL DEFAULT 0, last_sync_at TEXT);
"""


def test_a_database_from_before_projects_migrates_with_its_data(tmp_path, monkeypatch):
    """What production may be holding. Every row must come through, and every
    person must keep the token their connector already uses."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "old.db")
    raw = sqlite3.connect(tmp_path / "old.db")
    raw.executescript(PRE_PROJECTS_SCHEMA)
    raw.execute("INSERT INTO workspace (slug, title) VALUES ('demo', 'Old')")
    raw.execute("INSERT INTO member (workspace_id, name, token) VALUES (1, 'Adria', 'tok-a')")
    raw.execute("INSERT INTO member (workspace_id, name, token) VALUES (1, 'Oscar', 'tok-o')")
    raw.execute("INSERT INTO event (workspace_id, member_id, summary) VALUES (1, 1, 'first')")
    raw.execute("INSERT INTO event (workspace_id, member_id, summary) VALUES (1, 2, 'second')")
    raw.execute("INSERT INTO cursor (member_id, last_event_id) VALUES (1, 2)")
    raw.commit()
    raw.close()

    db.init_db()

    with db.connect() as c:
        assert "workspace_id" not in {r["name"] for r in c.execute("PRAGMA table_info(member)")}
        assert store.resolve_member(c, "tok-a")["name"] == "Adria"
        assert store.resolve_member(c, "tok-o")["name"] == "Oscar"
        # Both people are still in the project they were in.
        assert [p["slug"] for p in store.projects_for(c, 1)] == ["demo"]
        # History intact, and numbered per project in the order it was written.
        rows = c.execute("SELECT summary, seq FROM event ORDER BY id").fetchall()
        assert [(r["summary"], r["seq"]) for r in rows] == [("first", 1), ("second", 2)]
        # Adria had read both; that must survive, now scoped to the project.
        cur = c.execute("SELECT * FROM cursor WHERE member_id = 1").fetchone()
        assert cur["workspace_id"] == 1 and cur["last_event_id"] == 2
        # And the migrated database takes new writes normally.
        adria = store.resolve_member(c, "tok-a")
        env = store.record(c, adria, project(c), summary="after the migration")
        assert "Recorded as #3" in env.result


def test_migration_runs_once_and_is_then_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "twice.db")
    db.init_db()
    with db.connect() as c:
        rec(c, "Adria", summary="survives two boots")
    db.init_db()
    db.init_db()
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM event").fetchone()["n"] == 1


# --- identity ----------------------------------------------------------------


def test_unknown_token_resolves_to_nobody(conn):
    """The server turns this into a refusal. Silently becoming member #1 is how
    a mistyped connector URL ends up writing entries under someone else's name."""
    assert store.resolve_member(conn, "not-a-real-token") is None
    assert store.resolve_member(conn, "") is None
    assert store.resolve_member(conn, None) is None
    real = conn.execute("SELECT token FROM member WHERE name='Oscar'").fetchone()["token"]
    assert store.resolve_member(conn, real)["name"] == "Oscar"


def test_configured_tokens_are_used_and_survive_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cfg.db")
    monkeypatch.setenv("MEMBER_TOKENS", "Adria:aaa111,Oscar:bbb222,Pau:ccc333")
    db.init_db()
    with db.connect() as c:
        assert store.resolve_member(c, "bbb222")["name"] == "Oscar"

    # A later boot without the variable must not re-mint anyone's token.
    monkeypatch.delenv("MEMBER_TOKENS")
    db.init_db()
    with db.connect() as c:
        assert store.resolve_member(c, "bbb222")["name"] == "Oscar"


def test_setting_member_tokens_rotates_a_leaked_one(tmp_path, monkeypatch):
    """Rotation must not require destroying the workspace to get it."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "rot.db")
    db.init_db()
    with db.connect() as c:
        oscar = member(c, "Oscar")
        rec(c, "Oscar", summary="work done before the rotation")
        leaked = oscar["token"]

    monkeypatch.setenv("MEMBER_TOKENS", "Oscar:fresh-secret")
    db.init_db()

    with db.connect() as c:
        assert store.resolve_member(c, leaked) is None            # old one is dead
        assert store.resolve_member(c, "fresh-secret")["id"] == oscar["id"]  # same person
        assert c.execute("SELECT summary FROM event").fetchone()["summary"] == (
            "work done before the rotation"
        )


# --- projects ----------------------------------------------------------------


def test_a_project_is_only_visible_to_its_members(conn):
    adria = member(conn, "Adria")
    tfg = store.create_project(conn, adria["id"], "Memoria TFG", members=["Oscar"])

    assert store.resolve_project(conn, adria["id"], "memoria-tfg")["id"] == tfg["id"]
    assert store.resolve_project(conn, member(conn, "Oscar")["id"], "memoria-tfg") is not None
    # Pau was not named, so to him it does not exist -- not even as "forbidden".
    assert store.resolve_project(conn, member(conn, "Pau")["id"], "memoria-tfg") is None


def test_nothing_crosses_between_projects(conn):
    adria = member(conn, "Adria")
    tfg = store.create_project(conn, adria["id"], "TFG", members=["Oscar"])

    rec(conn, "Oscar", tfg, kind="fact", summary="the TFG deadline is 14 October")
    rec(conn, "Adria", kind="fact", summary="demo-only fact")

    out = cu(conn, "Adria", tfg)
    assert "the TFG deadline is 14 October" in out
    assert "demo-only fact" not in out

    snap = store.snapshot(conn, project(conn)["id"])
    assert [e["summary"] for e in snap["events"]] == ["demo-only fact"]


def test_every_project_counts_its_own_entries_from_one(conn):
    adria = member(conn, "Adria")
    a = store.create_project(conn, adria["id"], "A")
    b = store.create_project(conn, adria["id"], "B")
    assert "#1" in rec(conn, "Adria", a, summary="a1").result
    assert "#2" in rec(conn, "Adria", a, summary="a2").result
    assert "#1" in rec(conn, "Adria", b, summary="b1").result


def test_catching_up_in_one_project_does_not_mark_another_read(conn):
    adria = member(conn, "Adria")
    a = store.create_project(conn, adria["id"], "A", members=["Oscar"])
    b = store.create_project(conn, adria["id"], "B", members=["Oscar"])
    rec(conn, "Oscar", a, summary="news in A")
    rec(conn, "Oscar", b, summary="news in B")

    cu(conn, "Adria", a)
    assert store.unread_for(conn, adria["id"], a["id"]) == 0
    assert store.unread_for(conn, adria["id"], b["id"]) == 1
    assert "news in B" in cu(conn, "Adria", b)


def test_an_entry_cannot_supersede_or_cite_one_in_another_project(conn):
    adria = member(conn, "Adria")
    other = store.create_project(conn, adria["id"], "Other")
    rec(conn, "Adria", summary="decision in demo", kind="decision")   # demo #1

    env = rec(conn, "Adria", other, summary="tries to reach across", supersedes=1, refs=[1])
    out = render.render(env)
    assert "does not exist in this project" in out

    # And the demo decision is still alive: nothing was reached.
    assert "decision in demo" in standing(cu(conn, "Oscar"))


def test_unknown_project_error_lists_the_ones_you_can_use(conn):
    from app import server

    adria = member(conn, "Adria")
    store.create_project(conn, adria["id"], "Memoria TFG")
    msg = server._unknown_project(conn, adria, "nope")
    assert '"nope" is not a project Adria is in' in msg
    assert '"demo"' in msg and '"memoria-tfg"' in msg
    assert "No project given" in server._unknown_project(conn, adria, "")


def test_project_slugs_stay_unique(conn):
    adria = member(conn, "Adria")
    assert store.create_project(conn, adria["id"], "Web")["slug"] == "web"
    assert store.create_project(conn, adria["id"], "Web")["slug"] == "web-2"
    assert store.create_project(conn, adria["id"], "¡¡!!")["slug"] == "project"


# --- the loop, inside a project -------------------------------------------------


def test_delta_reaches_the_other_member(conn):
    rec(
        conn, "Oscar", agent="gemini", summary="generated the cover image",
        intent="minimalist, lots of whitespace",
        rejected=[{"option": "busy collage v1", "reason": "too crowded"}], artifact="cover-v3.png",
    )
    out = cu(conn, "Adria")
    assert "generated the cover image" in out
    assert "Oscar / gemini" in out
    assert "wanted: minimalist" in out
    assert "dropped: busy collage v1 -- too crowded" in out
    assert "artifact: cover-v3.png" in out


def test_cursor_only_reports_what_is_new(conn):
    rec(conn, "Oscar", summary="first thing", intent="x")
    cu(conn, "Adria")  # Adria is now up to date
    assert "Nothing new since you last looked." in cu(conn, "Adria")

    rec(conn, "Oscar", summary="second thing", intent="y")
    out = cu(conn, "Adria")
    assert "second thing" in out
    assert "first thing" not in out


def test_record_returns_the_delta_too(conn):
    """The whole point of the envelope: you learn what changed by doing anything."""
    rec(conn, "Oscar", agent="gemini", summary="oscar moved first", intent="z")
    out = render.render(rec(conn, "Adria", agent="claude", summary="my own work", intent="w"))
    assert "Recorded as #" in out
    assert "oscar moved first" in out          # the delta rode along
    assert "my own work" not in out.split("WHAT'S NEW")[1]  # not our own echo


def test_every_response_says_which_project_it_is(conn):
    assert 'pass project="demo"' in cu(conn, "Adria")


def test_superseded_decision_is_marked_dead(conn):
    first = rec(conn, "Adria", summary="use a formal tone", intent="academic audience")
    dead = int(first.result.split("#")[1].split()[0])
    rec(conn, "Pau", agent="chatgpt", summary="switched to a plain tone", supersedes=dead)

    out = cu(conn, "Oscar")
    assert f"SUPERSEDES #{dead}" in out
    assert "SUPERSEDED by a later entry" in out


def test_missing_intent_nudges_instead_of_inventing(conn):
    out = render.render(rec(conn, "Adria", summary="did a thing"))
    assert "No intent recorded" in out
    assert "do not guess" in out


def test_rejected_accepts_whatever_shape_a_model_sends(conn):
    assert store.normalise_rejected("just a string") == [{"option": "just a string", "reason": ""}]
    assert store.normalise_rejected(["a", "b"])[1] == {"option": "b", "reason": ""}
    assert store.normalise_rejected('[{"option": "x", "reason": "y"}]') == [
        {"option": "x", "reason": "y"}
    ]
    assert store.normalise_rejected({"what": "p", "why": "q"}) == [{"option": "p", "reason": "q"}]
    assert store.normalise_rejected(None) == []


def test_section_write_projects_into_the_document(conn):
    rec(conn, "Adria", summary="drafted the intro", intent="set the scene",
        section="intro", content="Once upon a time.")
    out = cu(conn, "Pau")
    assert "[key: intro]" in out
    assert "Once upon a time." in out


def test_brief_holds_what_is_true_now_not_what_happened(conn):
    rec(conn, "Adria", kind="fact", summary="the deadline is 14 October")
    rec(conn, "Oscar", kind="work", summary="exported the slides")
    rec(conn, "Adria", kind="question", summary="who writes the conclusion?")

    s = standing(cu(conn, "Pau"))
    assert "the deadline is 14 October" in s
    assert "who writes the conclusion?" in s
    assert "exported the slides" not in s  # work scrolls away, it is history


def test_brief_collapses_to_one_line_per_item(conn):
    rec(conn, "Adria", kind="decision", summary="plain tone throughout",
        intent="readable by non-specialists",
        details="Long body that should stay in the log.\nSecond line.\nThird line.")
    s = standing(cu(conn, "Pau"))
    assert "plain tone throughout — because: readable by non-specialists" in s
    assert "Long body" not in s          # the body lives in the log, not the index
    assert len([ln for ln in s.strip().splitlines() if "plain tone" in ln]) == 1


def test_superseding_closes_a_question(conn):
    asked = rec(conn, "Adria", kind="question", summary="who writes the conclusion?")
    qid = int(asked.result.split("#")[1].split()[0])
    rec(conn, "Pau", kind="fact", summary="Pau writes the conclusion", supersedes=qid)

    s = standing(cu(conn, "Oscar"))
    assert "who writes the conclusion?" not in s
    assert "Pau writes the conclusion" in s


def test_details_reach_the_delta(conn):
    rec(conn, "Oscar", agent="claude", kind="work", summary="drafted the abstract",
        details="Two paragraphs, 180 words, no citations.")
    out = cu(conn, "Adria")
    assert "Two paragraphs, 180 words, no citations." in out
    # Declared as work, and with no structure to promote it, it reads as a note.
    assert "[note] drafted the abstract" in out


def test_kind_is_inferred_when_the_model_omits_it(conn):
    rec(conn, "Adria", summary="picked option B", rejected=[{"option": "A", "reason": "slow"}])
    assert "picked option B" in standing(cu(conn, "Pau"))  # a decision, not lost as work


def test_level_is_derived_from_structure_not_from_what_the_model_claims(conn):
    lvl = lambda **kw: store.derive_level(**kw)[0]

    # An artifact outranks a dropped option: producing a file with a choice along
    # the way stays work, and does not crowd the standing brief.
    assert lvl(artifact_url="cover.png", rejected=[{"option": "a", "reason": "b"}]) == "artifact"

    # Structure promotes work it was never told about.
    assert lvl(kind="work", rejected=[{"option": "a", "reason": "b"}]) == "decision"
    assert lvl(kind="work", supersedes_id=7) == "decision"

    # But it never demotes: a declared decision stays one with no structure at all.
    assert lvl(kind="decision") == "decision"
    assert lvl(kind="fact") == "fact"
    assert lvl(kind="question") == "question"

    # supersedes means "this replaces that", which is just as often a question
    # being answered as a decision being reversed. It must not overwrite a
    # declared kind, or every answer lands in the wrong column of the brief.
    assert lvl(kind="fact", supersedes_id=3) == "fact"
    assert lvl(kind="question", supersedes_id=3) == "question"

    # Supersedes outranks even an artifact -- replacing something is a decision.
    assert lvl(supersedes_id=3, artifact_url="x.png") == "decision"

    assert lvl(kind="work", section_key="intro", has_content=True) == "write"
    assert lvl(kind="work") == "note"


def test_artifact_with_a_dropped_option_stays_out_of_the_brief(conn):
    """The rule Adria picked, end to end rather than at the unit."""
    rec(conn, "Oscar", kind="work", summary="Cover image generated", artifact="cover-v3.png",
        rejected=[{"option": "busy collage", "reason": "illegible below 200px"}])
    out = cu(conn, "Pau")
    assert "Cover image generated" not in standing(out)
    assert "[artifact] Cover image generated" in out  # still in the history


def test_refs_survive_whatever_shape_they_arrive_in(conn):
    assert store.normalise_refs([12, "47", "#3"]) == [12, 47, 3]
    assert store.normalise_refs("#8") == [8]
    assert store.normalise_refs([5, 5, "x", 0, -2]) == [5]
    assert store.normalise_refs(None) == []

    rec(conn, "Adria", summary="first")
    out = render.render(rec(conn, "Oscar", summary="builds on it", refs=[1]))
    assert "builds on: #1" not in out           # not echoed back to its own author
    assert "builds on: #1" in cu(conn, "Pau")


def test_collision_hint_when_someone_just_touched_the_section(conn):
    rec(conn, "Oscar", summary="wrote intro", intent="a", section="intro", content="v1")
    out = render.render(rec(conn, "Adria", summary="rewrote intro", intent="b",
                            section="intro", content="v2"))
    assert "Oscar touched section 'intro'" in out


# --- the web's signed-in surface ------------------------------------------------


@pytest.fixture
def web(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app import server

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "web.db")
    monkeypatch.setenv("MEMBER_TOKENS", "Adria:tok-a,Oscar:tok-o,Pau:tok-p")
    db.init_db()
    # localhost, so the session cookie is not marked Secure over plain http.
    with TestClient(server.app, base_url="http://localhost") as client:
        yield client


def test_the_web_reveals_nothing_until_signed_in(web):
    assert web.get("/api/me").status_code == 401
    assert web.get("/api/state?project=demo").status_code == 401
    assert web.get("/events?project=demo").status_code == 401
    assert web.get("/").status_code == 200        # the shell itself holds no data


def test_signing_in_accepts_the_whole_connector_url(web):
    r = web.post("/api/login", json={"token": "https://x.up.railway.app/u/tok-a/mcp"})
    assert r.status_code == 200 and r.json()["name"] == "Adria"
    me = web.get("/api/me").json()
    assert me["name"] == "Adria" and [p["slug"] for p in me["projects"]] == ["demo"]


def test_a_bad_token_does_not_sign_anyone_in(web):
    assert web.post("/api/login", json={"token": "nope"}).status_code == 401
    assert web.get("/api/me").status_code == 401


def test_a_page_cannot_read_a_project_its_person_is_not_in(web):
    web.post("/api/login", json={"token": "tok-a"})
    slug = web.post("/api/projects", json={"title": "Private", "members": []}).json()["slug"]
    assert web.get(f"/api/state?project={slug}").status_code == 200

    web.post("/api/logout")
    web.post("/api/login", json={"token": "tok-p"})
    assert web.get(f"/api/state?project={slug}").status_code == 404
    assert web.get(f"/events?project={slug}").status_code == 404
    assert web.get(f"/api/projects/{slug}/link").status_code == 404


def test_creating_a_project_and_linking_a_conversation(web):
    web.post("/api/login", json={"token": "tok-a"})
    r = web.post("/api/projects", json={"title": "Memoria TFG", "members": ["Oscar"]})
    slug = r.json()["slug"]
    assert slug == "memoria-tfg"

    me = web.get("/api/me").json()
    tfg = next(p for p in me["projects"] if p["slug"] == slug)
    assert tfg["members"] == ["Adria", "Oscar"]

    link = web.get(f"/api/projects/{slug}/link?lang=ca").json()["text"]
    assert 'project="memoria-tfg"' in link
    assert "tok-a" not in link              # the token has no business in page scripts


def test_the_token_cookie_is_out_of_reach_of_page_scripts(web):
    r = web.post("/api/login", json={"token": "tok-a"})
    assert "httponly" in r.headers["set-cookie"].lower()
