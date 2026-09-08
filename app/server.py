"""Transport wrappers.

Two of them, on purpose:

  * an MCP server, for Claude and ChatGPT, which speak it natively
  * a plain HTTP/JSON pair of endpoints, for anything that does not -- Gemini
    through its API, a local model, whatever comes next

Both call the same functions in store.py. MCP is a convenience here, never a
requirement, which is what keeps a platform's connector policy from deciding
whether a teammate can join.
"""

from __future__ import annotations

import html
import json
import re
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from . import db, render, store

mcp = FastMCP("shared-context")


# --- who is calling ----------------------------------------------------------


def _request() -> Request | None:
    try:
        from fastmcp.server.dependencies import get_http_request

        return get_http_request()
    except Exception:
        return None  # in-process client: no HTTP layer at all


def _token() -> str | None:
    req = _request()
    if req is None:
        return None
    return req.headers.get("x-member-token") or req.query_params.get("k")


def _agent() -> str:
    """Read the MCP client name off the initialize handshake.

    Free provenance: this is what lets the log say "Oscar / gemini" without
    anyone having to declare anything.
    """
    name = ""
    try:
        from fastmcp.server.dependencies import get_context

        # Note: the Python attribute is snake_case; `clientInfo` is only the JSON alias.
        name = (get_context().session.client_params.client_info.name or "").lower()
    except Exception:
        pass
    if not name:
        # Plain-HTTP callers, and any client whose handshake we could not read.
        req = _request()
        if req is not None:
            name = req.headers.get("user-agent", "").lower()
    for needle, label in (
        ("claude", "claude"),
        ("chatgpt", "chatgpt"),
        ("openai", "chatgpt"),
        ("gemini", "gemini"),
        ("google", "gemini"),
    ):
        if needle in name:
            return label
    return name.split("/")[0][:24] or "unknown"


def _member(conn):
    member = store.resolve_member(conn, _token())
    # M0 only: a bare /mcp with no token acts as the first seeded member so the
    # loop can be tested with a single assistant. M1 makes the token required.
    return member or store.default_member(conn)


# --- the two tools -----------------------------------------------------------


@mcp.tool
def catch_up() -> str:
    """Get up to speed on the shared workspace this team is working in.

    Returns the current document, everything teammates have done since you last
    looked, and which decisions have since been overturned. Call this when you
    start working, and any time you need to know where things stand. Other people
    are working here with their own AI assistants, so this changes often.
    """
    with db.connect() as conn:
        return render.render(store.catch_up(conn, _member(conn)))


@mcp.tool
def record(
    summary: Annotated[
        str, Field(description="What was done or decided. Factual, one or two lines.")
    ],
    intent: Annotated[
        str,
        Field(
            description=(
                "What the person was actually trying to achieve. Leave EMPTY if they "
                "did not say -- do not guess or reconstruct it. An invented rationale "
                "is worse than none, because teammates' assistants will read it as fact."
            )
        ),
    ] = "",
    rejected: Annotated[
        list | None,
        Field(
            description=(
                "Options considered and dropped, as [{\"option\": ..., \"reason\": ...}]. "
                "This is the part that is normally lost forever when the chat ends. "
                "Empty if nothing was dropped."
            )
        ),
    ] = None,
    section: Annotated[
        str, Field(description="Key of the document section this touches, e.g. 'intro'.")
    ] = "",
    content: Annotated[
        str, Field(description="Full new content for that section, if you are writing one.")
    ] = "",
    artifact: Annotated[
        str, Field(description="URL of a file or image that was produced.")
    ] = "",
    supersedes: Annotated[
        int,
        Field(
            description=(
                "Entry number this decision overturns, if it reverses an earlier one. "
                "Use it -- otherwise teammates keep acting on the dead decision."
            )
        ),
    ] = 0,
) -> str:
    """Record something into the shared workspace so the team's other assistants see it.

    Call this when work is done, a decision is made, or something is dropped. Also
    call it once when you finish a stretch of work, summarising what was attempted
    and discarded along the way -- that is the part teammates cannot recover from
    their own conversations.

    Returns confirmation plus anything teammates did in the meantime.
    """
    with db.connect() as conn:
        env = store.record(
            conn,
            _member(conn),
            agent=_agent(),
            summary=summary,
            intent=intent,
            rejected=rejected,
            section=section,
            content=content,
            artifact=artifact,
            supersedes=supersedes,
        )
        return render.render(env)


# --- the same thing over plain HTTP, for clients that do not speak MCP --------


@mcp.custom_route("/api/catch_up", methods=["GET", "POST"])
async def api_catch_up(request: Request) -> JSONResponse:
    with db.connect() as conn:
        member = store.resolve_member(
            conn, request.query_params.get("k") or request.headers.get("x-member-token")
        ) or store.default_member(conn)
        env = store.catch_up(conn, member)
        return JSONResponse({"text": render.render(env)})


@mcp.custom_route("/api/record", methods=["POST"])
async def api_record(request: Request) -> JSONResponse:
    payload = await request.json()
    with db.connect() as conn:
        member = store.resolve_member(
            conn, request.query_params.get("k") or request.headers.get("x-member-token")
        ) or store.default_member(conn)
        env = store.record(
            conn,
            member,
            agent=payload.get("agent", "http"),
            summary=payload["summary"],
            intent=payload.get("intent", ""),
            rejected=payload.get("rejected"),
            section=payload.get("section", ""),
            content=payload.get("content", ""),
            artifact=payload.get("artifact", ""),
            supersedes=int(payload.get("supersedes") or 0),
        )
        return JSONResponse({"text": render.render(env)})


# --- the human view ----------------------------------------------------------


@mcp.custom_route("/", methods=["GET"])
async def web_view(request: Request) -> HTMLResponse:
    """Deliberately readable without connecting any AI at all.

    This is the answer to the onboarding problem: a new teammate opens a link,
    sees the project moving, and wires up their own assistant afterwards.
    """
    with db.connect() as conn:
        ws = conn.execute("SELECT id, title FROM workspace WHERE slug = 'demo'").fetchone()
        sections = store._read_document(conn, ws["id"])
        events = store._events_since(conn, ws["id"], 0, exclude_member=None)

    doc = "".join(
        f"<h3>{html.escape(s.title)}</h3><pre>{html.escape(s.content or '(empty)')}</pre>"
        for s in sections
    ) or "<p><em>Nothing written yet.</em></p>"

    feed = ""
    for e in reversed(events):
        dead = " style='opacity:.45;text-decoration:line-through'" if e.is_dead else ""
        bits = [f"<b>#{e.id}</b> {html.escape(e.member_name)} / {html.escape(e.agent)}"]
        bits.append(html.escape(e.summary))
        if e.intent:
            bits.append(f"<i>wanted:</i> {html.escape(e.intent)}")
        for r in e.rejected:
            bits.append(f"<i>dropped:</i> {html.escape(r['option'])} {html.escape(r.get('reason',''))}")
        if e.supersedes_id:
            bits.append(f"<b>supersedes #{e.supersedes_id}</b>")
        feed += f"<li{dead}>{'<br>'.join(bits)}</li>"

    return HTMLResponse(
        f"""<!doctype html><meta charset=utf-8>
<meta http-equiv=refresh content=5>
<title>{html.escape(ws['title'])}</title>
<style>
 body{{font:14px/1.55 -apple-system,system-ui,sans-serif;margin:0;background:#fbfbfa;color:#222}}
 .wrap{{display:grid;grid-template-columns:1fr 1fr;gap:28px;max-width:1100px;margin:0 auto;padding:28px}}
 pre{{white-space:pre-wrap;background:#fff;border:1px solid #e6e6e3;border-radius:6px;padding:12px;margin:.4em 0}}
 ul{{list-style:none;padding:0}} li{{background:#fff;border:1px solid #e6e6e3;border-radius:6px;padding:10px 12px;margin-bottom:8px}}
 h1{{font-size:18px;padding:20px 28px 0;margin:0}} h2{{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#888}}
 @media(max-width:800px){{.wrap{{grid-template-columns:1fr}}}}
</style>
<h1>{html.escape(ws['title'])}</h1>
<div class=wrap>
  <div><h2>Document</h2>{doc}</div>
  <div><h2>Log</h2><ul>{feed or '<li><em>No entries yet.</em></li>'}</ul></div>
</div>"""
    )


# --- app assembly ------------------------------------------------------------


class TokenPath:
    """Accept /u/<token>/... and hand the token down as a header.

    A path segment survives proxies and client-side URL rewriting more reliably
    than a query string, and every assistant we care about will happily take a
    connector URL with a path in it.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            match = re.match(r"^/u/([A-Za-z0-9_.-]+)(/.*)$", scope.get("path", ""))
            if match:
                scope = dict(scope)
                scope["path"] = match.group(2)
                scope["raw_path"] = match.group(2).encode()
                headers = [(k, v) for k, v in scope["headers"] if k != b"x-member-token"]
                headers.append((b"x-member-token", match.group(1).encode()))
                scope["headers"] = headers
        await self.app(scope, receive, send)


def build_app():
    db.init_db()
    # stateless_http keeps connectors working across redeploys: there is no
    # session for a restart to lose, and we will be redeploying constantly.
    app = mcp.http_app(path="/mcp", stateless_http=True, allowed_hosts=["*"])
    return TokenPath(app)


app = build_app()
