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


def test_collision_hint_when_someone_just_touched_the_section(conn):
    adria, oscar = member(conn, "Adria"), member(conn, "Oscar")
    store.record(conn, oscar, summary="wrote intro", intent="a", section="intro", content="v1")
    out = render.render(
        store.record(conn, adria, summary="rewrote intro", intent="b", section="intro", content="v2")
    )
    assert "Oscar touched section 'intro'" in out
