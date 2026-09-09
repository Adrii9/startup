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

import asyncio
import json
import re
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse

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
        ("antigravity", "gemini"),
        ("google", "gemini"),
    ):
        if needle in name:
            return label

    # Generic runtimes tell a teammate nothing -- "node" could be any client at
    # all. Log the raw handshake so an unmapped assistant can be added above
    # rather than guessed at.
    print(f"unmapped MCP client: {name!r}", flush=True)
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

    Returns where things stand right now -- the decisions in force, what the team
    knows, and what is still open -- then the document, then everything teammates
    have done since you last looked. Call this before starting any work in this
    project, and whenever you need to know where things stand. Other people are
    working here with their own AI assistants, so this changes often.
    """
    with db.connect() as conn:
        return render.render(store.catch_up(conn, _member(conn)))


@mcp.tool
def record(
    summary: Annotated[
        str,
        Field(
            description=(
                "The headline, one line. This is what teammates see in the standing "
                "summary of the project, so make it read on its own."
            )
        ),
    ],
    details: Annotated[
        str,
        Field(
            description=(
                "The substance: what was worked out, the reasoning, the numbers, the "
                "wording that was agreed. Write it for a teammate's assistant that was "
                "not in this conversation and never will be. Several lines is normal."
            )
        ),
    ] = "",
    kind: Annotated[
        str,
        Field(
            description=(
                "One of: 'decision' (we chose this over that), 'fact' (something now "
                "true about the project), 'question' (open, unresolved), 'work' "
                "(something produced). Decisions, facts and open questions stay in the "
                "standing summary; work scrolls away into history."
            )
        ),
    ] = "",
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
                "Entry number this replaces: a decision it reverses, or a question it "
                "answers. Use it -- otherwise the standing summary keeps showing the "
                "dead decision and teammates keep acting on it."
            )
        ),
    ] = 0,
) -> str:
    """Record something into the shared workspace so the team's other assistants see it.

    Call this whenever a decision is made, something is produced, something is
    dropped, or a fact about the project is established -- not on every turn, or
    the log becomes another chat transcript nobody reads. Also call it once when a
    stretch of work ends, summarising what was worked out and what was tried and
    abandoned: that part exists only in this conversation and is lost when it ends.

    Write for a teammate's assistant that cannot see any of this conversation.

    Returns confirmation plus anything teammates did in the meantime.
    """
    with db.connect() as conn:
        env = store.record(
            conn,
            _member(conn),
            agent=_agent(),
            summary=summary,
            details=details,
            kind=kind,
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
            details=payload.get("details", ""),
            kind=payload.get("kind", ""),
            intent=payload.get("intent", ""),
            rejected=payload.get("rejected"),
            section=payload.get("section", ""),
            content=payload.get("content", ""),
            artifact=payload.get("artifact", ""),
            supersedes=int(payload.get("supersedes") or 0),
        )
        return JSONResponse({"text": render.render(env)})


# --- the human view ----------------------------------------------------------


PAGE = (Path(__file__).resolve().parent / "static" / "index.html").read_text()


@mcp.custom_route("/", methods=["GET"])
async def web_view(request: Request) -> HTMLResponse:
    """Deliberately readable without connecting any AI at all.

    This is the answer to the onboarding problem: a new teammate opens a link,
    sees the project moving, and wires up their own assistant afterwards.
    """
    return HTMLResponse(PAGE)


@mcp.custom_route("/api/state", methods=["GET"])
async def api_state(request: Request) -> JSONResponse:
    with db.connect() as conn:
        return JSONResponse(store.snapshot(conn, store.workspace_id(conn)))


@mcp.custom_route("/events", methods=["GET"])
async def events(request: Request) -> StreamingResponse:
    """Server-sent events, driven by polling the log's high-water mark.

    Polling rather than an in-process broadcast on purpose: writes can arrive
    from any worker, and comparing two integers every second and a half costs
    nothing at this size. It also keeps store.py unaware that SSE exists.
    """

    async def stream():
        last = None
        while True:
            if await request.is_disconnected():
                return
            with db.connect() as conn:
                ws_id = store.workspace_id(conn)
                now = store.version(conn, ws_id)
                if now != last:
                    payload = json.dumps(store.snapshot(conn, ws_id), ensure_ascii=False)
                    last = now
                else:
                    payload = None
            if payload is not None:
                yield f"data: {payload}\n\n"
            else:
                yield ": keep-alive\n\n"  # keeps proxies from closing the connection
            await asyncio.sleep(1.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
