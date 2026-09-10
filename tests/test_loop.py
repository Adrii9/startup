"""Accounts, connections, projects, invites -- and the write/read loop inside them.

Runs entirely without an assistant and without Google. Keep it that way --
opening three chat windows should be for validating the magic moment, not for
debugging a NULL.
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
    db.init_db()
    with db.connect() as c:
        yield c


def acc(conn, name):
    return store.account_from_google(conn, f"g-{name}", f"{name.lower()}@example.com", name)


def team(conn):
    """Adria owns a project that Oscar and Pau are in, the usual cast."""
    a, o, p = acc(conn, "Adria"), acc(conn, "Oscar"), acc(conn, "Pau")
    ws = store.create_project(conn, a["id"], "Memoria TFG")
    code = store.create_invite(conn, a["id"], ws["slug"])["code"]
    store.accept_invite(conn, o["id"], code)
    store.accept_invite(conn, p["id"], code)
    return a, o, p, ws


def as_member(conn, account, ws):
    return store.resolve_project(conn, account["id"], ws["slug"])


def rec(conn, account, ws, **kw):
    return store.record(conn, account, as_member(conn, account, ws), **kw)


def cu(conn, account, ws):
    return render.render(store.catch_up(conn, account, as_member(conn, account, ws)))


def standing(out):
    return out.split("WHERE THINGS STAND")[1].split("DOCUMENT")[0]


# --- starting from zero ------------------------------------------------------------


def test_a_database_from_before_accounts_is_moved_aside_not_deleted(tmp_path, monkeypatch):
    path = tmp_path / "workspace.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE member (id INTEGER PRIMARY KEY, name TEXT)")
    old.execute("CREATE TABLE event (id INTEGER PRIMARY KEY, summary TEXT)")
    old.execute("INSERT INTO event (summary) VALUES ('the tortilla has no onion')")
    old.commit()
    old.close()

    db.init_db()

    kept = list(tmp_path.glob("workspace.db.before-accounts-*"))
    assert [p for p in kept if not str(p).endswith(("-wal", "-shm"))], "old file must be kept"
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM event").fetchone()["n"] == 0
        assert c.execute("SELECT name FROM sqlite_master WHERE name='account'").fetchone()
    backup = sqlite3.connect([p for p in kept if not str(p).endswith(("-wal", "-shm"))][0])
    assert backup.execute("SELECT summary FROM event").fetchone()[0] == "the tortilla has no onion"


def test_booting_twice_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "twice.db")
    db.init_db()
    with db.connect() as c:
        a = acc(c, "Adria")
        store.create_project(c, a["id"], "Kept")
    db.init_db()
    with db.connect() as c:
        assert [p["title"] for p in store.projects_for(c, a["id"])] == ["Kept"]


# --- accounts and sessions -------------------------------------------------------------


def test_an_account_is_its_google_identity_not_its_email(conn):
    first = store.account_from_google(conn, "sub-1", "old@example.com", "Adria")
    again = store.account_from_google(conn, "sub-1", "new@example.com", "Adrià")
    assert first["id"] == again["id"]
    assert again["email"] == "new@example.com" and again["name"] == "Adrià"
    other = store.account_from_google(conn, "sub-2", "old@example.com", "Someone")
    assert other["id"] != first["id"]      # same address, different person


def test_a_session_is_stored_only_as_a_hash(conn):
    a = acc(conn, "Adria")
    raw = store.create_session(conn, a["id"])
    assert store.account_for_session(conn, raw)["id"] == a["id"]
    assert conn.execute("SELECT 1 FROM session WHERE id_hash = ?", (raw,)).fetchone() is None
    store.end_session(conn, raw)
    assert store.account_for_session(conn, raw) is None


def test_an_expired_session_lets_nobody_in(conn):
    a = acc(conn, "Adria")
    raw = store.create_session(conn, a["id"])
    conn.execute("UPDATE session SET expires_at = datetime('now', '-1 minute')")
    assert store.account_for_session(conn, raw) is None


# --- connections -------------------------------------------------------------------------


def test_a_connection_token_is_shown_once_and_never_stored(conn):
    a = acc(conn, "Adria")
    row, raw = store.create_connection(conn, a["id"], "Claude")
    assert raw.startswith("sc_") and row["prefix"] == raw[:9]
    assert "token" not in row and raw not in str(row)
    assert conn.execute("SELECT 1 FROM connection WHERE token_hash = ?", (raw,)).fetchone() is None
    assert store.account_for_token(conn, raw)["id"] == a["id"]


def test_revoking_a_connection_kills_it_and_only_it(conn):
    a = acc(conn, "Adria")
    claude, t1 = store.create_connection(conn, a["id"], "Claude")
    _, t2 = store.create_connection(conn, a["id"], "ChatGPT")
    assert store.revoke_connection(conn, a["id"], claude["id"])
    assert store.account_for_token(conn, t1) is None
    assert store.account_for_token(conn, t2)["id"] == a["id"]
    assert [c["label"] for c in store.list_connections(conn, a["id"])] == ["ChatGPT"]


def test_nobody_can_revoke_someone_elses_connection(conn):
    a, o = acc(conn, "Adria"), acc(conn, "Oscar")
    mine, raw = store.create_connection(conn, a["id"], "Claude")
    assert not store.revoke_connection(conn, o["id"], mine["id"])
    assert store.account_for_token(conn, raw) is not None


def test_a_connection_records_when_it_was_last_used(conn):
    a = acc(conn, "Adria")
    row, raw = store.create_connection(conn, a["id"], "Claude")
    assert row["last_used_at"] is None
    store.account_for_token(conn, raw)
    assert store.list_connections(conn, a["id"])[0]["last_used_at"] is not None


# --- projects and roles -------------------------------------------------------------------


def test_the_creator_owns_the_project_and_nobody_else_is_in_it(conn):
    a, o = acc(conn, "Adria"), acc(conn, "Oscar")
    ws = store.create_project(conn, a["id"], "Private")
    assert ws["role"] == "owner"
    assert store.resolve_project(conn, o["id"], ws["slug"]) is None


def test_only_the_owner_can_rename_invite_or_delete(conn):
    a, o, _, ws = team(conn)
    for attempt in (
        lambda: store.update_project(conn, o["id"], ws["slug"], title="Mine now"),
        lambda: store.create_invite(conn, o["id"], ws["slug"]),
        lambda: store.delete_project(conn, o["id"], ws["slug"]),
        lambda: store.transfer_ownership(conn, o["id"], ws["slug"], o["id"]),
    ):
        with pytest.raises(store.Refused):
            attempt()
    assert store.update_project(conn, a["id"], ws["slug"], title="Renamed")["title"] == "Renamed"


def test_nothing_crosses_between_projects(conn):
    a, o, _, tfg = team(conn)
    other = store.create_project(conn, a["id"], "Other")
    rec(conn, o, tfg, kind="fact", summary="the TFG deadline is 14 October")
    rec(conn, a, other, kind="fact", summary="only in the other project")

    out = cu(conn, a, tfg)
    assert "the TFG deadline is 14 October" in out
    assert "only in the other project" not in out


def test_every_project_counts_its_own_entries_from_one(conn):
    a = acc(conn, "Adria")
    x = store.create_project(conn, a["id"], "X")
    y = store.create_project(conn, a["id"], "Y")
    assert "#1" in rec(conn, a, x, summary="x1").result
    assert "#2" in rec(conn, a, x, summary="x2").result
    assert "#1" in rec(conn, a, y, summary="y1").result


def test_an_entry_cannot_reach_into_another_project(conn):
    a, _, _, tfg = team(conn)
    other = store.create_project(conn, a["id"], "Other")
    rec(conn, a, tfg, summary="decision in the TFG", kind="decision")
    out = render.render(rec(conn, a, other, summary="tries to reach across",
                            supersedes=1, refs=[1]))
    assert "does not exist in this project" in out
    assert "decision in the TFG" in standing(cu(conn, a, tfg))


def test_catching_up_in_one_project_does_not_mark_another_read(conn):
    a, o, _, tfg = team(conn)
    other = store.create_project(conn, a["id"], "Other")
    store.accept_invite(conn, o["id"], store.create_invite(conn, a["id"], other["slug"])["code"])
    rec(conn, o, tfg, summary="news in the TFG")
    rec(conn, o, other, summary="news elsewhere")
    cu(conn, a, tfg)
    assert store.unread_for(conn, a["id"], tfg["id"]) == 0
    assert store.unread_for(conn, a["id"], other["id"]) == 1


def test_unknown_project_error_lists_only_your_projects(conn):
    from app import server

    a, _, p, ws = team(conn)
    store.create_project(conn, a["id"], "Adria only")
    msg = server._unknown_project(conn, p, "nope")
    assert '"nope" is not a project Pau is in' in msg
    assert f'"{ws["slug"]}"' in msg
    assert "adria-only" not in msg          # nobody learns what else exists
    assert "No project given" in server._unknown_project(conn, p, "")


def test_project_slugs_stay_unique(conn):
    a = acc(conn, "Adria")
    assert store.create_project(conn, a["id"], "Web")["slug"] == "web"
    assert store.create_project(conn, a["id"], "Web")["slug"] == "web-2"
    assert store.create_project(conn, a["id"], "¡¡!!")["slug"] == "project"
    with pytest.raises(store.Refused):
        store.create_project(conn, a["id"], "   ")


# --- invites -------------------------------------------------------------------------------


def test_an_invite_link_makes_you_a_member_not_an_owner(conn):
    a, o = acc(conn, "Adria"), acc(conn, "Oscar")
    ws = store.create_project(conn, a["id"], "TFG")
    code = store.create_invite(conn, a["id"], ws["slug"])["code"]

    preview = store.preview_invite(conn, code, o["id"])
    assert preview["title"] == "TFG" and preview["inviter"] == "Adria"
    assert preview["already_member"] is False

    assert store.accept_invite(conn, o["id"], code) == ws["slug"]
    assert store.resolve_project(conn, o["id"], ws["slug"])["role"] == "member"


def test_accepting_twice_joins_once_and_counts_once(conn):
    a, o = acc(conn, "Adria"), acc(conn, "Oscar")
    ws = store.create_project(conn, a["id"], "TFG")
    inv = store.create_invite(conn, a["id"], ws["slug"], max_uses=2)
    store.accept_invite(conn, o["id"], inv["code"])
    store.accept_invite(conn, o["id"], inv["code"])
    assert conn.execute("SELECT uses FROM invite").fetchone()["uses"] == 1


def test_a_used_up_expired_or_revoked_invite_lets_nobody_in(conn):
    a, o = acc(conn, "Adria"), acc(conn, "Oscar")
    ws = store.create_project(conn, a["id"], "TFG")

    one = store.create_invite(conn, a["id"], ws["slug"], max_uses=1)
    store.accept_invite(conn, acc(conn, "Pau")["id"], one["code"])
    with pytest.raises(store.Refused):
        store.accept_invite(conn, o["id"], one["code"])

    old = store.create_invite(conn, a["id"], ws["slug"])
    conn.execute("UPDATE invite SET expires_at = datetime('now', '-1 minute') WHERE id = ?",
                 (old["id"],))
    assert store.preview_invite(conn, old["code"], None) is None

    gone = store.create_invite(conn, a["id"], ws["slug"])
    store.revoke_invite(conn, a["id"], ws["slug"], gone["id"])
    with pytest.raises(store.Refused):
        store.accept_invite(conn, o["id"], gone["code"])
    assert store.resolve_project(conn, o["id"], ws["slug"]) is None


# --- removing people ----------------------------------------------------------------------


def test_removing_someone_cuts_their_access_but_keeps_what_they_wrote(conn):
    a, o, _, ws = team(conn)
    rec(conn, o, ws, kind="decision", summary="Plain tone throughout")
    store.remove_member(conn, a["id"], ws["slug"], o["id"])

    assert store.resolve_project(conn, o["id"], ws["slug"]) is None
    assert "Plain tone throughout" in standing(cu(conn, a, ws))


def test_a_member_can_leave_and_cannot_remove_anyone_else(conn):
    a, o, p, ws = team(conn)
    with pytest.raises(store.Refused):
        store.remove_member(conn, o["id"], ws["slug"], p["id"])
    store.remove_member(conn, o["id"], ws["slug"], o["id"])      # leaving
    assert store.resolve_project(conn, o["id"], ws["slug"]) is None


def test_the_owner_cannot_leave_without_handing_it_over(conn):
    a, o, _, ws = team(conn)
    with pytest.raises(store.Refused, match="Hand the project"):
        store.remove_member(conn, a["id"], ws["slug"], a["id"])
    store.transfer_ownership(conn, a["id"], ws["slug"], o["id"])
    assert store.resolve_project(conn, o["id"], ws["slug"])["role"] == "owner"
    store.remove_member(conn, a["id"], ws["slug"], a["id"])      # now allowed
    assert store.resolve_project(conn, a["id"], ws["slug"]) is None


# --- deleting things -----------------------------------------------------------------------


def test_a_deleted_project_waits_in_the_trash_and_can_come_back(conn):
    a, o, _, ws = team(conn)
    rec(conn, o, ws, summary="work worth keeping")
    store.delete_project(conn, a["id"], ws["slug"])

    assert store.resolve_project(conn, o["id"], ws["slug"]) is None
    assert [t["slug"] for t in store.trash_for(conn, a["id"])] == [ws["slug"]]
    assert store.trash_for(conn, o["id"]) == []         # only its owner sees it there

    store.restore_project(conn, a["id"], ws["slug"])
    assert store.resolve_project(conn, o["id"], ws["slug"]) is not None   # Oscar is back in
    assert [e["summary"] for e in store.snapshot(conn, ws["id"])["events"]] == ["work worth keeping"]


def test_after_thirty_days_a_deleted_project_is_gone_for_good(conn):
    a, o, _, ws = team(conn)
    rec(conn, o, ws, summary="doomed", section="intro", content="text", artifact="x.png")
    store.delete_project(conn, a["id"], ws["slug"])
    conn.execute("UPDATE workspace SET deleted_at = datetime('now', '-31 days')")

    assert store.purge_deleted_projects(conn) == 1
    for table in ("workspace", "event", "section", "section_revision", "artifact",
                  "membership", "invite", "cursor"):
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0, table


def test_deleting_an_account_is_refused_while_it_owns_other_peoples_work(conn):
    a, _, _, ws = team(conn)
    with pytest.raises(store.Refused, match="Memoria TFG"):
        store.delete_account(conn, a["id"])


def test_a_deleted_account_leaves_its_entries_behind_anonymously(conn):
    a, o, _, ws = team(conn)
    solo = store.create_project(conn, o["id"], "Oscar alone")
    _, raw = store.create_connection(conn, o["id"], "Claude")
    session = store.create_session(conn, o["id"])
    rec(conn, o, ws, kind="fact", summary="Oscar's contribution")
    rec(conn, o, solo, summary="nobody else cares")

    store.delete_account(conn, o["id"])

    out = cu(conn, a, ws)
    assert "Oscar's contribution" in out and "Former member" in out
    assert "Oscar /" not in out
    assert store.account_for_token(conn, raw) is None
    assert store.account_for_session(conn, session) is None
    assert conn.execute("SELECT 1 FROM workspace WHERE slug = ?",
                        (solo["slug"],)).fetchone() is None     # a solo project goes with them
    gone = conn.execute("SELECT * FROM account WHERE id = ?", (o["id"],)).fetchone()
    assert gone["email"] is None and gone["google_sub"] is None


# --- the loop, inside a project ---------------------------------------------------------------


def test_delta_reaches_the_other_member(conn):
    a, o, _, ws = team(conn)
    rec(conn, o, ws, agent="gemini", summary="generated the cover image",
        intent="minimalist, lots of whitespace",
        rejected=[{"option": "busy collage v1", "reason": "too crowded"}], artifact="cover-v3.png")
    out = cu(conn, a, ws)
    assert "generated the cover image" in out
    assert "Oscar / gemini" in out
    assert "wanted: minimalist" in out
    assert "dropped: busy collage v1 -- too crowded" in out
    assert "artifact: cover-v3.png" in out


def test_cursor_only_reports_what_is_new(conn):
    a, o, _, ws = team(conn)
    rec(conn, o, ws, summary="first thing", intent="x")
    cu(conn, a, ws)
    assert "Nothing new since you last looked." in cu(conn, a, ws)
    rec(conn, o, ws, summary="second thing", intent="y")
    out = cu(conn, a, ws)
    assert "second thing" in out and "first thing" not in out


def test_record_returns_the_delta_too(conn):
    """The whole point of the envelope: you learn what changed by doing anything."""
    a, o, _, ws = team(conn)
    rec(conn, o, ws, agent="gemini", summary="oscar moved first", intent="z")
    out = render.render(rec(conn, a, ws, agent="claude", summary="my own work", intent="w"))
    assert "oscar moved first" in out
    assert "my own work" not in out.split("WHAT'S NEW")[1]


def test_every_response_says_which_project_it_is(conn):
    a, _, _, ws = team(conn)
    assert f'pass project="{ws["slug"]}"' in cu(conn, a, ws)


def test_superseded_decision_is_marked_dead(conn):
    a, o, p, ws = team(conn)
    first = rec(conn, a, ws, summary="use a formal tone", intent="academic audience")
    dead = int(first.result.split("#")[1].split()[0])
    rec(conn, p, ws, agent="chatgpt", summary="switched to a plain tone", supersedes=dead)
    out = cu(conn, o, ws)   # a third person, who has seen neither entry yet
    assert f"SUPERSEDES #{dead}" in out and "SUPERSEDED by a later entry" in out


def test_missing_intent_nudges_instead_of_inventing(conn):
    a, _, _, ws = team(conn)
    out = render.render(rec(conn, a, ws, summary="did a thing"))
    assert "No intent recorded" in out and "do not guess" in out


def test_rejected_accepts_whatever_shape_a_model_sends():
    assert store.normalise_rejected("just a string") == [{"option": "just a string", "reason": ""}]
    assert store.normalise_rejected(["a", "b"])[1] == {"option": "b", "reason": ""}
    assert store.normalise_rejected('[{"option": "x", "reason": "y"}]') == [
        {"option": "x", "reason": "y"}]
    assert store.normalise_rejected({"what": "p", "why": "q"}) == [{"option": "p", "reason": "q"}]
    assert store.normalise_rejected(None) == []


def test_section_write_projects_into_the_document(conn):
    a, _, p, ws = team(conn)
    rec(conn, a, ws, summary="drafted the intro", intent="set the scene",
        section="intro", content="Once upon a time.")
    out = cu(conn, p, ws)
    assert "[key: intro]" in out and "Once upon a time." in out


def test_brief_holds_what_is_true_now_not_what_happened(conn):
    a, o, p, ws = team(conn)
    rec(conn, a, ws, kind="fact", summary="the deadline is 14 October")
    rec(conn, o, ws, kind="work", summary="exported the slides")
    rec(conn, a, ws, kind="question", summary="who writes the conclusion?")
    s = standing(cu(conn, p, ws))
    assert "the deadline is 14 October" in s and "who writes the conclusion?" in s
    assert "exported the slides" not in s


def test_brief_collapses_to_one_line_per_item(conn):
    a, _, p, ws = team(conn)
    rec(conn, a, ws, kind="decision", summary="plain tone throughout",
        intent="readable by non-specialists", details="Long body.\nSecond line.")
    s = standing(cu(conn, p, ws))
    assert "plain tone throughout — because: readable by non-specialists" in s
    assert "Long body" not in s


def test_superseding_closes_a_question(conn):
    a, o, p, ws = team(conn)
    asked = rec(conn, a, ws, kind="question", summary="who writes the conclusion?")
    qid = int(asked.result.split("#")[1].split()[0])
    rec(conn, p, ws, kind="fact", summary="Pau writes the conclusion", supersedes=qid)
    s = standing(cu(conn, o, ws))
    assert "who writes the conclusion?" not in s and "Pau writes the conclusion" in s


def test_level_is_derived_from_structure_not_from_what_the_model_claims():
    lvl = lambda **kw: store.derive_level(**kw)[0]
    assert lvl(artifact_url="cover.png", rejected=[{"option": "a", "reason": "b"}]) == "artifact"
    assert lvl(kind="work", rejected=[{"option": "a", "reason": "b"}]) == "decision"
    assert lvl(kind="work", supersedes_id=7) == "decision"
    assert lvl(kind="decision") == "decision"
    assert lvl(kind="fact", supersedes_id=3) == "fact"
    assert lvl(kind="question", supersedes_id=3) == "question"
    assert lvl(supersedes_id=3, artifact_url="x.png") == "decision"
    assert lvl(kind="work", section_key="intro", has_content=True) == "write"
    assert lvl(kind="work") == "note"


def test_artifact_with_a_dropped_option_stays_out_of_the_brief(conn):
    a, o, p, ws = team(conn)
    rec(conn, o, ws, kind="work", summary="Cover image generated", artifact="cover-v3.png",
        rejected=[{"option": "busy collage", "reason": "illegible below 200px"}])
    out = cu(conn, p, ws)
    assert "Cover image generated" not in standing(out)
    assert "[artifact] Cover image generated" in out


def test_refs_survive_whatever_shape_they_arrive_in(conn):
    assert store.normalise_refs([12, "47", "#3"]) == [12, 47, 3]
    assert store.normalise_refs([5, 5, "x", 0, -2]) == [5]
    a, o, p, ws = team(conn)
    rec(conn, a, ws, summary="first")
    rec(conn, o, ws, summary="builds on it", refs=[1])
    assert "builds on: #1" in cu(conn, p, ws)


def test_collision_hint_when_someone_just_touched_the_section(conn):
    a, o, _, ws = team(conn)
    rec(conn, o, ws, summary="wrote intro", intent="a", section="intro", content="v1")
    out = render.render(rec(conn, a, ws, summary="rewrote intro", intent="b",
                            section="intro", content="v2"))
    assert "Oscar touched section 'intro'" in out


# --- the web ---------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app import server

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "web.db")
    monkeypatch.delenv("DEV_LOGIN", raising=False)
    db.init_db()
    # localhost, so session cookies are not marked Secure over plain http.
    with TestClient(server.app, base_url="http://localhost") as client:
        yield client


def sign_in(client, name):
    """Stand-in for a finished Google round trip: an account and a session cookie."""
    with db.connect() as c:
        account = acc(c, name)
        raw = store.create_session(c, account["id"])
    client.cookies.set("sc_session", raw)
    return account


def test_the_web_reveals_nothing_until_signed_in(web):
    for path in ("/api/me", "/api/connections", "/api/trash", "/api/state?project=x",
                 "/events?project=x"):
        assert web.get(path).status_code == 401, path
    assert web.get("/").status_code == 200        # the shell itself holds no data


def test_sign_in_without_google_is_closed_even_when_switched_on(web, monkeypatch):
    # TestClient's peer is not a loopback address, exactly like a public server.
    monkeypatch.setenv("DEV_LOGIN", "1")
    assert web.post("/auth/dev", data={"name": "Mallory"}).status_code == 404
    assert web.get("/api/config").json()["dev"] is False


def test_the_google_callback_rejects_a_state_it_did_not_issue(web, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    web.cookies.set("sc_oauth", "expected|/")
    r = web.get("/auth/google/callback?state=forged&code=abc", follow_redirects=False)
    assert r.status_code == 400


def test_a_google_sign_in_makes_an_account_and_a_session(web, monkeypatch):
    from app import auth

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")

    async def fake_identity(request, code):
        assert code == "one-time"
        return {"sub": "g-42", "email": "adria@example.com", "email_verified": True,
                "name": "Adrià", "picture": ""}

    monkeypatch.setattr(auth, "identity", fake_identity)
    web.cookies.set("sc_oauth", "st4te|/#memoria")
    r = web.get("/auth/google/callback?state=st4te&code=one-time", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/#memoria"
    assert "httponly" in r.headers["set-cookie"].lower()
    assert web.get("/api/me").json()["account"]["name"] == "Adrià"


def test_signing_in_cannot_be_bounced_off_to_another_site(web, monkeypatch):
    from app import auth

    for hostile in ("//evil.example/steal", "https://evil.example", "/\\evil.example",
                    "evil.example", ""):
        assert auth.safe_next(hostile) == "/", hostile
    assert auth.safe_next("/#memoria-tfg") == "/#memoria-tfg"
    assert auth.safe_next("/join/abc") == "/join/abc"

    # And end to end: whatever `next` was asked for, only "/" is carried.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    r = web.get("/auth/google?next=//evil.example/steal", follow_redirects=False)
    state_cookie = r.headers["set-cookie"].split("sc_oauth=")[1].split(";")[0].strip('"')
    assert state_cookie.endswith("|/") and "evil" not in state_cookie


def test_a_new_connection_url_is_shown_once_and_works(web):
    sign_in(web, "Adria")
    made = web.post("/api/connections", json={"label": "Claude"}).json()
    assert made["url"].endswith("/mcp") and "/u/sc_" in made["url"]
    listed = web.get("/api/connections").json()["connections"]
    assert listed[0]["label"] == "Claude" and "url" not in listed[0]

    token = made["url"].split("/u/")[1].split("/")[0]
    web.post("/api/projects", json={"title": "TFG"})
    r = web.get(f"/u/{token}/api/catch_up?project=tfg")
    assert r.status_code == 200 and "Caught up as Adria" in r.json()["text"]

    web.delete(f"/api/connections/{made['id']}")
    assert web.get(f"/u/{token}/api/catch_up?project=tfg").status_code == 403


def test_inviting_a_friend_end_to_end(web):
    sign_in(web, "Adria")
    slug = web.post("/api/projects", json={"title": "Memoria TFG"}).json()["slug"]
    inv = web.post(f"/api/projects/{slug}/invites", json={}).json()
    code = inv["url"].rsplit("/", 1)[1]

    web.cookies.clear()
    preview = web.get(f"/api/invites/{code}").json()     # readable before signing in
    assert preview["title"] == "Memoria TFG" and preview["signed_in"] is False
    assert web.post(f"/api/invites/{code}/accept").status_code == 401

    sign_in(web, "Oscar")
    assert web.post(f"/api/invites/{code}/accept").json()["slug"] == slug
    me = web.get("/api/me").json()
    assert [(p["slug"], p["role"]) for p in me["projects"]] == [(slug, "member")]


def test_a_page_cannot_read_a_project_its_person_is_not_in(web):
    sign_in(web, "Adria")
    slug = web.post("/api/projects", json={"title": "Private"}).json()["slug"]
    web.cookies.clear()
    sign_in(web, "Pau")
    for path in (f"/api/state?project={slug}", f"/events?project={slug}",
                 f"/api/projects/{slug}/link", f"/api/projects/{slug}/members"):
        assert web.get(path).status_code == 404, path


def test_a_member_is_refused_owner_actions_over_http(web):
    sign_in(web, "Adria")
    slug = web.post("/api/projects", json={"title": "TFG"}).json()["slug"]
    code = web.post(f"/api/projects/{slug}/invites", json={}).json()["code"]
    web.cookies.clear()
    sign_in(web, "Oscar")
    web.post(f"/api/invites/{code}/accept")
    assert web.delete(f"/api/projects/{slug}").status_code == 403
    assert web.patch(f"/api/projects/{slug}", json={"title": "Mine"}).status_code == 403
    assert web.post(f"/api/projects/{slug}/invites", json={}).status_code == 403


def test_the_link_text_carries_the_project_and_no_secret(web):
    sign_in(web, "Adria")
    web.post("/api/connections", json={"label": "Claude"})
    slug = web.post("/api/projects", json={"title": "Memoria TFG"}).json()["slug"]
    text = web.get(f"/api/projects/{slug}/link?lang=ca").json()["text"]
    assert f'project="{slug}"' in text and "sc_" not in text
