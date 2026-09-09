"""Turn an Envelope into the text an assistant reads.

This is the whole answer to "you cannot push to an LLM": the delta rides on
every single response, so any assistant learns what the others did the moment
it touches the workspace for any reason at all.
"""

from __future__ import annotations

from .store import Brief, Envelope, EventView, SectionView

RULE = "─" * 52


def _event_block(e: EventView) -> str:
    head = f"#{e.id} · {e.member_name} / {e.agent} · [{e.kind}] {e.summary}"
    lines = [head]
    pad = "      "
    if e.details:
        lines += [f"{pad}{line}" for line in e.details.strip().splitlines()]
    if e.is_dead:
        lines.append(f"{pad}✗ SUPERSEDED by a later entry -- no longer valid")
    if e.supersedes_id:
        lines.append(f"{pad}⚠ SUPERSEDES #{e.supersedes_id} -- that decision is dead")
    if e.intent:
        label = "wanted" if e.intent_source == "stated" else "assistant inferred"
        lines.append(f"{pad}{label}: {e.intent}")
    for r in e.rejected:
        reason = f" -- {r['reason']}" if r.get("reason") else ""
        lines.append(f"{pad}dropped: {r['option']}{reason}")
    if e.section_key:
        lines.append(f"{pad}section: {e.section_key}")
    if e.artifact_url:
        lines.append(f"{pad}artifact: {e.artifact_url}")
    return "\n".join(lines)


def _brief_line(e: EventView) -> str:
    """One line per item. This is an index, not a transcript.

    A decision carries its reason because a decision without its reason gets
    quietly re-litigated; everything else is just the headline, and the body
    lives in the log for whoever needs it.
    """
    line = f"#{e.id} · {e.summary}"
    if e.kind == "decision" and e.intent:
        line += f" — because: {e.intent}"
    return line


def _brief_block(brief: Brief) -> str:
    groups = [
        ("Decisions in force", brief.decisions),
        ("What we know", brief.facts),
        ("Still open", brief.questions),
    ]
    out = []
    for label, items in groups:
        if not items:
            continue
        out.append(f"{label}:")
        out += [f"  {_brief_line(e)}" for e in items]
        out.append("")
    return "\n".join(out).rstrip() or "(nothing established yet)"


def _document_block(sections: list[SectionView]) -> str:
    if not sections:
        return "(the document is empty -- nothing has been written yet)"
    out = []
    for s in sections:
        out.append(f"## {s.title}  [key: {s.key}]")
        out.append(s.content.strip() or "(empty)")
        out.append("")
    return "\n".join(out).rstrip()


def render(env: Envelope) -> str:
    parts = [env.result, ""]

    # The brief goes first: what is true now matters more than what happened.
    if env.brief is not None:
        parts += [f"{RULE}\nWHERE THINGS STAND\n{RULE}", _brief_block(env.brief), ""]

    if env.document is not None:
        parts += [f"{RULE}\nDOCUMENT\n{RULE}", _document_block(env.document), ""]

    parts.append(f"{RULE}\nWHAT'S NEW (since #{env.since_id})\n{RULE}")
    if env.new_events:
        parts += [_event_block(e) for e in env.new_events]
    else:
        parts.append("Nothing new since you last looked.")
    parts.append("")

    s = env.state
    parts += [
        f"{RULE}\nSTATE\n{RULE}",
        f"Workspace: {s['title']}",
        f"Sections: {s['sections']} · Entries: {s['events']} · "
        f"Members active today: {s['members_active_today']}",
    ]

    if env.hints:
        parts += ["", f"{RULE}\nHINTS\n{RULE}"]
        parts += [f"! {h}" for h in env.hints]

    return "\n".join(parts)
