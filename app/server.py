"""Transport wrappers.

Three surfaces over the same functions in store.py:

  * an MCP server, for Claude and ChatGPT, which speak it natively
  * a plain HTTP/JSON pair of endpoints, for anything that does not -- Gemini
    through its API, a local model, whatever comes next
  * the web view, for people, signed in with the same token their assistant uses

MCP is a convenience here, never a requirement, which is what keeps a
platform's connector policy from deciding whether a teammate can join.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

from . import db, linking, render, store

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


UNKNOWN_TOKEN = (
    "This connector is not recognised by the workspace. Its URL should end in "
    "/u/<your token>/mcp -- check it against the one you were given, and note "
    "that tokens change if the workspace is reset."
)


def _member(conn):
    """No fallback. An unrecognised token must fail, not quietly become someone.

    M0 let a tokenless call act as the first member so the loop could be tested
    with one assistant. Kept any longer, that turns a mistyped connector URL
    into entries silently attributed to the wrong person.
    """
    member = store.resolve_member(conn, _token())
    if member is None:
        raise ToolError(UNKNOWN_TOKEN)
    return member


def _unknown_project(conn, member, slug: str) -> str:
    """Never guess which project a call meant. Say which ones it could have.

    Writing to the wrong project is the same failure as writing under the wrong
    name, so it gets the same treatment: refuse, and make recovery one step.
    """
    options = ", ".join(
        f'"{p["slug"]}" ({p["title"]})' for p in store.projects_for(conn, member["id"])
    )
    said = f'"{slug}" is not a project {member["name"]} is in. ' if slug else "No project given. "
    return (
        f"{said}Pass one of these as project=: {options}. It should be in the "
        "instructions of the conversation you are working in."
    )


def _project(conn, member, slug: str):
    ws = store.resolve_project(conn, member["id"], slug)
    if ws is None:
        raise ToolError(_unknown_project(conn, member, slug))
    return ws


# --- the two tools -----------------------------------------------------------

ProjectArg = Annotated[
    str,
    Field(
        description=(
            "The project this conversation belongs to, e.g. 'tfg'. It is written in "
            "the instructions of the conversation you are working in. If you do not "
            "know it, pass an empty string: the error lists the projects you can use."
        )
    ),
]


@mcp.tool
def catch_up(project: ProjectArg) -> str:
    """Get up to speed on a shared project this team is working in.

    Returns where things stand right now -- the decisions in force, what the team
    knows, and what is still open -- then the document, then everything teammates
    have done since you last looked. Call this before starting any work in the
    project, and whenever you need to know where things stand. Other people are
    working here with their own AI assistants, so this changes often.
    """
    with db.connect() as conn:
        member = _member(conn)
        return render.render(store.catch_up(conn, member, _project(conn, member, project)))


@mcp.tool
def record(
    project: ProjectArg,
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
                "What a teammate needs in order to act: the reasoning, the numbers, "
                "what was agreed. Not the whole answer and not all the research -- a "
                "teammate's assistant reads this in every catch_up, so a wall of text "
                "here costs everyone. Write it for someone who was not in this "
                "conversation and never will be."
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
    refs: Annotated[
        list | None,
        Field(
            description=(
                "Entry numbers this one builds on, as [12, 47]. Use it when you are "
                "acting on, answering or extending something a teammate wrote -- it is "
                "what lets anyone trace where a piece of work came from."
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
                "Entry number this replaces: a decision it reverses, a question it "
                "answers, or a fact it corrects. Use it -- otherwise the standing "
                "summary keeps showing the dead one and teammates keep acting on it."
            )
        ),
    ] = 0,
) -> str:
    """Record something into a shared project so the team's other assistants see it.

    Call this whenever a decision is made, something is produced, something is
    dropped, or a fact about the project is established -- not on every turn, or
    the log becomes another chat transcript nobody reads. Also call it once when a
    stretch of work ends, summarising what was worked out and what was tried and
    abandoned: that part exists only in this conversation and is lost when it ends.

    Write for a teammate's assistant that cannot see any of this conversation.

    Returns confirmation plus anything teammates did in the meantime.
    """
    with db.connect() as conn:
        member = _member(conn)
        env = store.record(
            conn,
            member,
            _project(conn, member, project),
            agent=_agent(),
            summary=summary,
            details=details,
            kind=kind,
            intent=intent,
            rejected=rejected,
            refs=refs,
            section=section,
            content=content,
            artifact=artifact,
            supersedes=supersedes,
        )
        return render.render(env)


# --- the same thing over plain HTTP, for clients that do not speak MCP --------


def _http_member(conn, request: Request):
    return store.resolve_member(
        conn, request.query_params.get("k") or request.headers.get("x-member-token")
    )


@mcp.custom_route("/api/catch_up", methods=["GET", "POST"])
async def api_catch_up(request: Request) -> JSONResponse:
    with db.connect() as conn:
        member = _http_member(conn, request)
        if member is None:
            return JSONResponse({"error": UNKNOWN_TOKEN}, status_code=403)
        slug = request.query_params.get("project", "")
        ws = store.resolve_project(conn, member["id"], slug)
        if ws is None:
            return JSONResponse({"error": _unknown_project(conn, member, slug)}, status_code=400)
        return JSONResponse({"text": render.render(store.catch_up(conn, member, ws))})


@mcp.custom_route("/api/record", methods=["POST"])
async def api_record(request: Request) -> JSONResponse:
    payload = await request.json()
    with db.connect() as conn:
        member = _http_member(conn, request)
        if member is None:
            return JSONResponse({"error": UNKNOWN_TOKEN}, status_code=403)
        slug = payload.get("project") or request.query_params.get("project", "")
        ws = store.resolve_project(conn, member["id"], slug)
        if ws is None:
            return JSONResponse({"error": _unknown_project(conn, member, slug)}, status_code=400)
        env = store.record(
            conn,
            member,
            ws,
            agent=payload.get("agent", "http"),
            summary=payload["summary"],
            details=payload.get("details", ""),
            kind=payload.get("kind", ""),
            intent=payload.get("intent", ""),
            rejected=payload.get("rejected"),
            refs=payload.get("refs"),
            section=payload.get("section", ""),
            content=payload.get("content", ""),
            artifact=payload.get("artifact", ""),
            supersedes=int(payload.get("supersedes") or 0),
        )
        return JSONResponse({"text": render.render(env)})


# --- the web: signed in with the same token the assistant uses ----------------

COOKIE = "sc_token"
NOT_SIGNED_IN = JSONResponse({"error": "not signed in"}, status_code=401)


def _web_member(conn, request: Request):
    return store.resolve_member(conn, request.cookies.get(COOKIE))


def _is_local(request: Request) -> bool:
    host = request.headers.get("host", "")
    return host.startswith(("127.0.0.1", "localhost"))


@mcp.custom_route("/api/login", methods=["POST"])
async def api_login(request: Request) -> JSONResponse:
    """Sign in with a token, or with the whole connector URL.

    People have the connector URL, not the bare token -- that is what they were
    given and what they pasted into their assistant -- so accept either.

    The token goes into an HttpOnly cookie: page scripts never see it, so a
    script that got onto the page could not read it and send it elsewhere.
    """
    payload = await request.json()
    raw = (payload.get("token") or "").strip()
    match = re.search(r"/u/([A-Za-z0-9_.-]+)", raw)
    token = match.group(1) if match else raw
    with db.connect() as conn:
        member = store.resolve_member(conn, token)
    if member is None:
        return JSONResponse({"error": "That token does not match anyone."}, status_code=401)
    resp = JSONResponse({"name": member["name"]})
    resp.set_cookie(
        COOKIE, token, max_age=60 * 60 * 24 * 90, path="/",
        httponly=True, samesite="lax", secure=not _is_local(request),
    )
    return resp


@mcp.custom_route("/api/logout", methods=["POST"])
async def api_logout(request: Request) -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@mcp.custom_route("/api/me", methods=["GET"])
async def api_me(request: Request) -> JSONResponse:
    with db.connect() as conn:
        member = _web_member(conn, request)
        if member is None:
            return NOT_SIGNED_IN
        projects = [
            {
                "slug": p["slug"],
                "title": p["title"],
                "colour": p["colour"],
                "members": [m["name"] for m in store.members_of(conn, p["id"])],
                "unread": store.unread_for(conn, member["id"], p["id"]),
            }
            for p in store.projects_for(conn, member["id"])
        ]
        return JSONResponse(
            {"name": member["name"], "people": store.everyone(conn), "projects": projects,
             "palette": list(store.PALETTE)}
        )


@mcp.custom_route("/api/projects", methods=["POST"])
async def api_create_project(request: Request) -> JSONResponse:
    payload = await request.json()
    with db.connect() as conn:
        member = _web_member(conn, request)
        if member is None:
            return NOT_SIGNED_IN
        if not (payload.get("title") or "").strip():
            return JSONResponse({"error": "A project needs a name."}, status_code=400)
        ws = store.create_project(
            conn, member["id"], payload["title"], payload.get("colour", ""),
            [n for n in payload.get("members", []) if n != member["name"]],
        )
        return JSONResponse({"slug": ws["slug"], "title": ws["title"]})


@mcp.custom_route("/api/projects/{slug}/members", methods=["POST"])
async def api_add_member(request: Request) -> JSONResponse:
    payload = await request.json()
    with db.connect() as conn:
        member = _web_member(conn, request)
        if member is None:
            return NOT_SIGNED_IN
        ws = store.resolve_project(conn, member["id"], request.path_params["slug"])
        if ws is None:
            return JSONResponse({"error": "No such project."}, status_code=404)
        if store.add_member(conn, ws["id"], payload.get("name", "")) is None:
            return JSONResponse({"error": "Nobody by that name."}, status_code=404)
        return JSONResponse({"members": [m["name"] for m in store.members_of(conn, ws["id"])]})


@mcp.custom_route("/api/projects/{slug}/link", methods=["GET"])
async def api_link(request: Request) -> JSONResponse:
    """The text that links a conversation to this project.

    Deliberately does not include the person's connector URL or token: that is
    set up once per person, not per project, and it has no business being
    handed to a page script.
    """
    with db.connect() as conn:
        member = _web_member(conn, request)
        if member is None:
            return NOT_SIGNED_IN
        ws = store.resolve_project(conn, member["id"], request.path_params["slug"])
        if ws is None:
            return JSONResponse({"error": "No such project."}, status_code=404)
        lang = request.query_params.get("lang", "ca")
        return JSONResponse(
            {"slug": ws["slug"], "text": linking.instructions(ws["slug"], ws["title"], lang)}
        )


@mcp.custom_route("/api/state", methods=["GET"])
async def api_state(request: Request) -> JSONResponse:
    with db.connect() as conn:
        member = _web_member(conn, request)
        if member is None:
            return NOT_SIGNED_IN
        ws = store.resolve_project(conn, member["id"], request.query_params.get("project"))
        if ws is None:
            return JSONResponse({"error": "No such project."}, status_code=404)
        return JSONResponse(store.snapshot(conn, ws["id"]))


@mcp.custom_route("/events", methods=["GET"])
async def events(request: Request) -> Response:
    """Server-sent events for one project, driven by polling its high-water mark.

    Polling rather than an in-process broadcast on purpose: writes can arrive
    from any worker, and comparing three integers every second and a half costs
    nothing at this size. It also keeps store.py unaware that SSE exists.

    Membership is checked before the stream opens: a page can only ever follow
    a project its signed-in person is in.
    """
    with db.connect() as conn:
        member = _web_member(conn, request)
        if member is None:
            return NOT_SIGNED_IN
        ws = store.resolve_project(conn, member["id"], request.query_params.get("project"))
        if ws is None:
            return JSONResponse({"error": "No such project."}, status_code=404)
        ws_id = ws["id"]

    async def stream():
        last = None
        while True:
            if await request.is_disconnected():
                return
            with db.connect() as conn:
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


# --- the page and its assets --------------------------------------------------


STATIC = Path(__file__).resolve().parent / "static"
MIME = {".css": "text/css", ".js": "text/javascript", ".html": "text/html"}


@mcp.custom_route("/", methods=["GET"])
async def web_view(request: Request) -> HTMLResponse:
    """The shell. It holds no data of its own; everything it shows comes through
    the signed-in endpoints above, so serving it to anyone gives away nothing."""
    return HTMLResponse((STATIC / "index.html").read_text())


@mcp.custom_route("/s/{name}", methods=["GET"])
async def static_file(request: Request) -> Response:
    """Serve the shell's assets, and nothing else.

    Resolved against the static directory and checked to still be inside it, so
    a crafted name cannot walk out of the folder.
    """
    name = request.path_params["name"]
    path = (STATIC / name).resolve()
    if not path.is_file() or STATIC.resolve() not in path.parents:
        return PlainTextResponse("not found", status_code=404)
    return Response(path.read_bytes(), media_type=MIME.get(path.suffix, "application/octet-stream"))


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
