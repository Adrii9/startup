"""M0's acceptance test: the write/read loop closes between two members.

Runs entirely without an assistant. Keep it that way -- opening three chat
windows should be for validating the magic moment, not for debugging a NULL.
"""

from __future__ import annotations

import pytest

from app import db, render, store


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db("Uni project")
    with db.connect() as c:
        yield c


def member(conn, name):
    return conn.execute("SELECT * FROM member WHERE name = ?", (name,)).fetchone()


def test_migration_adds_columns_without_touching_live_data(tmp_path, monkeypatch):
    """The production database already holds work. A schema change must reach it.

    CREATE TABLE IF NOT EXISTS skips an existing table silently, so without the
    migration step a deploy would look fine and the new column would never exist.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "old.db")
    with db.connect() as c:
        c.executescript(db.SCHEMA.replace("details       TEXT,", ""))
        c.execute("INSERT INTO workspace (slug, title) VALUES ('demo', 'Old')")
        c.execute("INSERT INTO member (workspace_id, name, token) VALUES (1, 'Adria', 't')")
        c.execute(
            "INSERT INTO event (workspace_id, member_id, summary) VALUES (1, 1, 'from before')"
        )
        assert "details" not in {r["name"] for r in c.execute("PRAGMA table_info(event)")}

    db.init_db()

    with db.connect() as c:
        assert "details" in {r["name"] for r in c.execute("PRAGMA table_info(event)")}
        rows = c.execute("SELECT summary, details FROM event").fetchall()
        assert rows[0]["summary"] == "from before"  # survived
        assert rows[0]["details"] is None
        # And the existing member keeps the token their connector already uses.
        assert c.execute("SELECT token FROM member WHERE name='Adria'").fetchone()["token"] == "t"


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


def test_delta_reaches_the_other_member(conn):
    adria, oscar = member(conn, "Adria"), member(conn, "Oscar")

    store.record(
        conn,
        oscar,
        agent="gemini",
        summary="generated the cover image",
        intent="minimalist, lots of whitespace",
        rejected=[{"option": "busy collage v1", "reason": "too crowded"}],
        artifact="cover-v3.png",
    )

    out = render.render(store.catch_up(conn, adria))
    assert "generated the cover image" in out
    assert "Oscar / gemini" in out
    assert "wanted: minimalist" in out
    assert "dropped: busy collage v1 -- too crowded" in out
    assert "artifact: cover-v3.png" in out


def test_cursor_only_reports_what_is_new(conn):
    adria, oscar = member(conn, "Adria"), member(conn, "Oscar")
    store.record(conn, oscar, summary="first thing", intent="x")
    store.catch_up(conn, adria)  # Adria is now up to date

    out = render.render(store.catch_up(conn, adria))
    assert "Nothing new since you last looked." in out

    store.record(conn, oscar, summary="second thing", intent="y")
    out = render.render(store.catch_up(conn, adria))
    assert "second thing" in out
    assert "first thing" not in out


def test_record_returns_the_delta_too(conn):
    """The whole point of the envelope: you learn what changed by doing anything."""
    adria, oscar = member(conn, "Adria"), member(conn, "Oscar")
    store.record(conn, oscar, agent="gemini", summary="oscar moved first", intent="z")

    out = render.render(store.record(conn, adria, agent="claude", summary="my own work", intent="w"))
    assert "Recorded as #" in out
    assert "oscar moved first" in out          # the delta rode along
    assert "my own work" not in out.split("WHAT'S NEW")[1]  # not our own echo


def test_superseded_decision_is_marked_dead(conn):
    adria, pau = member(conn, "Adria"), member(conn, "Pau")
    first = store.record(conn, adria, summary="use a formal tone", intent="academic audience")
    dead_id = int(first.result.split("#")[1].rstrip("."))

    store.record(
        conn, pau, agent="chatgpt", summary="switched to a plain tone", supersedes=dead_id
    )

    out = render.render(store.catch_up(conn, member(conn, "Oscar")))
    assert f"SUPERSEDES #{dead_id}" in out
    assert "SUPERSEDED by a later entry" in out


def test_missing_intent_nudges_instead_of_inventing(conn):
    out = render.render(store.record(conn, member(conn, "Adria"), summary="did a thing"))
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
    adria = member(conn, "Adria")
    store.record(
        conn, adria, summary="drafted the intro", intent="set the scene",
        section="intro", content="Once upon a time.",
    )
    out = render.render(store.catch_up(conn, member(conn, "Pau")))
    assert "[key: intro]" in out
    assert "Once upon a time." in out


def test_brief_holds_what_is_true_now_not_what_happened(conn):
    adria, oscar = member(conn, "Adria"), member(conn, "Oscar")
    store.record(conn, adria, kind="fact", summary="the deadline is 14 October")
    store.record(conn, oscar, kind="work", summary="exported the slides")
    store.record(conn, adria, kind="question", summary="who writes the conclusion?")

    out = render.render(store.catch_up(conn, member(conn, "Pau")))
    standing = out.split("WHERE THINGS STAND")[1].split("DOCUMENT")[0]

    assert "the deadline is 14 October" in standing
    assert "who writes the conclusion?" in standing
    assert "exported the slides" not in standing  # work scrolls away, it is history


def test_brief_collapses_to_one_line_per_item(conn):
    store.record(
        conn, member(conn, "Adria"), kind="decision",
        summary="plain tone throughout", intent="readable by non-specialists",
        details="Long body that should stay in the log.\nSecond line.\nThird line.",
    )
    standing = render.render(store.catch_up(conn, member(conn, "Pau"))).split(
        "WHERE THINGS STAND")[1].split("DOCUMENT")[0]

    assert "plain tone throughout — because: readable by non-specialists" in standing
    assert "Long body" not in standing          # the body lives in the log, not the index
    lines = [ln for ln in standing.strip().splitlines() if "plain tone" in ln]
    assert len(lines) == 1


def test_superseding_closes_a_question(conn):
    adria, pau = member(conn, "Adria"), member(conn, "Pau")
    asked = store.record(conn, adria, kind="question", summary="who writes the conclusion?")
    qid = int(asked.result.split("#")[1].rstrip("."))

    store.record(conn, pau, kind="fact", summary="Pau writes the conclusion", supersedes=qid)

    standing = render.render(store.catch_up(conn, member(conn, "Oscar"))).split(
        "WHERE THINGS STAND")[1].split("DOCUMENT")[0]
    assert "who writes the conclusion?" not in standing
    assert "Pau writes the conclusion" in standing


def test_details_reach_the_delta(conn):
    store.record(
        conn, member(conn, "Oscar"), agent="claude", kind="work",
        summary="drafted the abstract", details="Two paragraphs, 180 words, no citations.",
    )
    out = render.render(store.catch_up(conn, member(conn, "Adria")))
    assert "Two paragraphs, 180 words, no citations." in out
    assert "[work] drafted the abstract" in out


def test_kind_is_inferred_when_the_model_omits_it(conn):
    adria = member(conn, "Adria")
    store.record(conn, adria, summary="picked option B", rejected=[{"option": "A", "reason": "slow"}])
    standing = render.render(store.catch_up(conn, member(conn, "Pau"))).split(
        "WHERE THINGS STAND")[1].split("DOCUMENT")[0]
    assert "picked option B" in standing  # landed as a decision, not lost as work


def test_collision_hint_when_someone_just_touched_the_section(conn):
    adria, oscar = member(conn, "Adria"), member(conn, "Oscar")
    store.record(conn, oscar, summary="wrote intro", intent="a", section="intro", content="v1")
    out = render.render(
        store.record(conn, adria, summary="rewrote intro", intent="b", section="intro", content="v2")
    )
    assert "Oscar touched section 'intro'" in out
