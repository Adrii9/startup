"""Transport wrappers.

Three surfaces over the same functions in store.py:

  * an MCP server, for Claude and ChatGPT, which speak it natively
  * a plain HTTP/JSON pair of endpoints, for anything that does not -- Gemini
    through its API, a local model, whatever comes next
  * the web, for people, signed in with Google

The first two authenticate with a connection token from the connector URL. The
web authenticates with a session. The two never stand in for each other.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import secrets
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
    RedirectResponse,
    Response,
    StreamingResponse,
)

from . import auth, db, linking, render, store

mcp = FastMCP("shared-context")


# --- who is calling, from a connector ----------------------------------------------


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
    "This connector is not recognised. It may have been revoked, or the URL may be "
    "mistyped. Create a new connection in the web (Account → Connections) and put "
    "its URL in your assistant."
)


def _account(conn):
    """No fallback. An unrecognised or revoked token must fail, not become someone."""
    account = store.account_for_token(conn, _token())
    if account is None:
        raise ToolError(UNKNOWN_TOKEN)
    return account


def _unknown_project(conn, account, slug: str) -> str:
    """Never guess which project a call meant. Say which ones it could have.

    Writing to the wrong project is the same failure as writing under the wrong
    name, so it gets the same treatment: refuse, and make recovery one step.
    """
    projects = store.projects_for(conn, account["id"])
    options = ", ".join(f'"{p["slug"]}" ({p["title"]})' for p in projects) or "none yet"
    said = f'"{slug}" is not a project {account["name"]} is in. ' if slug else "No project given. "
    return (
        f"{said}Pass one of these as project=: {options}. It should be in the "
        "instructions of the conversation you are working in."
    )


def _project(conn, account, slug: str):
    ws = store.resolve_project(conn, account["id"], slug)
    if ws is None:
        raise ToolError(_unknown_project(conn, account, slug))
    return ws


# --- the two tools -----------------------------------------------------------------

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
        account = _account(conn)
        return render.render(store.catch_up(conn, account, _project(conn, account, project)))


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
        account = _account(conn)
        env = store.record(
            conn,
            account,
            _project(conn, account, project),
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


# --- the same thing over plain HTTP, for clients that do not speak MCP --------------


def _http_account(conn, request: Request):
    return store.account_for_token(
        conn, request.query_params.get("k") or request.headers.get("x-member-token")
    )


@mcp.custom_route("/api/catch_up", methods=["GET", "POST"])
async def api_catch_up(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _http_account(conn, request)
        if account is None:
            return JSONResponse({"error": UNKNOWN_TOKEN}, status_code=403)
        slug = request.query_params.get("project", "")
        ws = store.resolve_project(conn, account["id"], slug)
        if ws is None:
            return JSONResponse({"error": _unknown_project(conn, account, slug)}, status_code=400)
        return JSONResponse({"text": render.render(store.catch_up(conn, account, ws))})


@mcp.custom_route("/api/record", methods=["POST"])
async def api_record(request: Request) -> JSONResponse:
    payload = await request.json()
    with db.connect() as conn:
        account = _http_account(conn, request)
        if account is None:
            return JSONResponse({"error": UNKNOWN_TOKEN}, status_code=403)
        slug = payload.get("project") or request.query_params.get("project", "")
        ws = store.resolve_project(conn, account["id"], slug)
        if ws is None:
            return JSONResponse({"error": _unknown_project(conn, account, slug)}, status_code=400)
        env = store.record(
            conn,
            account,
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


# --- signing in -----------------------------------------------------------------------

SESSION = "sc_session"
OAUTH = "sc_oauth"


def _is_local(request: Request) -> bool:
    return request.headers.get("host", "").startswith(("127.0.0.1", "localhost"))


def _loopback_peer(request: Request) -> bool:
    """Whether the connection itself comes from this machine.

    The peer address, not the Host header: a header is whatever the client says
    it is, whereas behind Railway's proxy the peer is never a loopback address.
    """
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        return False


def _dev_login_allowed(request: Request) -> bool:
    """A sign-in without Google, for working on this locally. Two locks, both
    required: the variable is set, and the request physically comes from this
    machine. Setting DEV_LOGIN on a public server still cannot open it."""
    return os.environ.get("DEV_LOGIN") == "1" and _loopback_peer(request)


def _start_session(conn, request: Request, account_id: int, to: str) -> RedirectResponse:
    raw = store.create_session(conn, account_id)
    resp = RedirectResponse(to, status_code=303)
    resp.set_cookie(SESSION, raw, max_age=60 * 60 * 24 * store.SESSION_DAYS, path="/",
                    httponly=True, samesite="lax", secure=not _is_local(request))
    resp.delete_cookie(OAUTH, path="/")
    return resp


@mcp.custom_route("/api/config", methods=["GET"])
async def api_config(request: Request) -> JSONResponse:
    return JSONResponse({"google": auth.configured(), "dev": _dev_login_allowed(request)})


@mcp.custom_route("/auth/google", methods=["GET"])
async def auth_google(request: Request) -> Response:
    if not auth.configured():
        return PlainTextResponse(
            "Google sign-in is not configured on this server yet: set GOOGLE_CLIENT_ID "
            "and GOOGLE_CLIENT_SECRET.", status_code=503)
    state = secrets.token_urlsafe(24)
    nxt = auth.safe_next(request.query_params.get("next"))
    resp = RedirectResponse(auth.authorize_url(request, state), status_code=303)
    # The state goes out to Google in the URL and must come back matching this
    # cookie, which a third-party site cannot set: that is what stops someone
    # signing a victim into the attacker's own account.
    resp.set_cookie(OAUTH, f"{state}|{nxt}", max_age=600, path="/",
                    httponly=True, samesite="lax", secure=not _is_local(request))
    return resp


@mcp.custom_route("/auth/google/callback", methods=["GET"])
async def auth_google_callback(request: Request) -> Response:
    stored = request.cookies.get(OAUTH, "")
    state, _, nxt = stored.partition("|")
    got = request.query_params.get("state", "")
    if not state or not secrets.compare_digest(state, got):
        return PlainTextResponse("Sign-in expired or was interrupted. Please try again.",
                                 status_code=400)
    code = request.query_params.get("code")
    if not code:
        return RedirectResponse("/", status_code=303)
    try:
        who = await auth.identity(request, code)
    except Exception as e:
        print(f"google sign-in failed: {e!r}", flush=True)
        return PlainTextResponse("Google did not confirm the sign-in. Please try again.",
                                 status_code=502)
    if not who.get("email_verified"):
        return PlainTextResponse("That Google account has no verified email address.",
                                 status_code=403)
    with db.connect() as conn:
        account = store.account_from_google(conn, who["sub"], who["email"],
                                            who.get("name", ""), who.get("picture", ""))
        return _start_session(conn, request, account["id"], auth.safe_next(nxt))


@mcp.custom_route("/auth/dev", methods=["POST"])
async def auth_dev(request: Request) -> Response:
    if not _dev_login_allowed(request):
        return PlainTextResponse("not found", status_code=404)
    form = await request.form()
    name = (form.get("name") or "Dev").strip()
    with db.connect() as conn:
        account = store.account_from_google(conn, f"dev:{name.lower()}",
                                            f"{name.lower()}@dev.local", name)
        return _start_session(conn, request, account["id"],
                              auth.safe_next(form.get("next")))


@mcp.custom_route("/auth/logout", methods=["POST"])
async def auth_logout(request: Request) -> JSONResponse:
    with db.connect() as conn:
        store.end_session(conn, request.cookies.get(SESSION))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION, path="/")
    return resp


# --- the web's API -------------------------------------------------------------------

NOT_SIGNED_IN = {"error": "not signed in"}


def _web_account(conn, request: Request):
    return store.account_for_session(conn, request.cookies.get(SESSION))


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@mcp.custom_route("/api/me", methods=["GET"])
async def api_me(request: Request) -> JSONResponse:
    with db.connect() as conn:
        store.purge_deleted_projects(conn)
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        projects = [
            {
                "slug": p["slug"], "title": p["title"], "colour": p["colour"], "role": p["role"],
                "members": [m["name"] for m in store.members_of(conn, p["id"])],
                "unread": store.unread_for(conn, account["id"], p["id"]),
            }
            for p in store.projects_for(conn, account["id"])
        ]
        return JSONResponse({
            "account": {"id": account["id"], "name": account["name"],
                        "email": account["email"], "picture": account["picture"]},
            "projects": projects,
            "palette": list(store.PALETTE),
        })


@mcp.custom_route("/api/account", methods=["DELETE"])
async def api_delete_account(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        try:
            store.delete_account(conn, account["id"])
        except store.Refused as e:
            return _err(str(e), 409)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION, path="/")
    return resp


# connections: the connector URLs, one per assistant

@mcp.custom_route("/api/connections", methods=["GET", "POST"])
async def api_connections(request: Request) -> JSONResponse:
    body = await _body(request) if request.method == "POST" else {}
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        if request.method == "GET":
            return JSONResponse({"connections": store.list_connections(conn, account["id"])})
        row, raw = store.create_connection(conn, account["id"], body.get("label", ""))
        # The only time the raw token ever leaves the server. It is not stored,
        # so if this response is lost the connection has to be made again.
        return JSONResponse({**row, "url": f"{auth.public_url(request)}/u/{raw}/mcp"})


@mcp.custom_route("/api/connections/{cid:int}", methods=["DELETE"])
async def api_revoke_connection(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        if not store.revoke_connection(conn, account["id"], request.path_params["cid"]):
            return _err("No such connection.", 404)
        return JSONResponse({"ok": True})


# projects

@mcp.custom_route("/api/projects", methods=["POST"])
async def api_create_project(request: Request) -> JSONResponse:
    body = await _body(request)
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        try:
            ws = store.create_project(conn, account["id"], body.get("title", ""),
                                      body.get("colour", ""))
        except store.Refused as e:
            return _err(str(e))
        return JSONResponse({"slug": ws["slug"], "title": ws["title"]})


@mcp.custom_route("/api/projects/{slug}", methods=["PATCH", "DELETE"])
async def api_project(request: Request) -> JSONResponse:
    body = await _body(request) if request.method == "PATCH" else {}
    slug = request.path_params["slug"]
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        try:
            if request.method == "DELETE":
                store.delete_project(conn, account["id"], slug)
                return JSONResponse({"ok": True})
            ws = store.update_project(conn, account["id"], slug,
                                      body.get("title"), body.get("colour"))
            return JSONResponse({"slug": ws["slug"], "title": ws["title"], "colour": ws["colour"]})
        except store.Refused as e:
            return _err(str(e), 403)


@mcp.custom_route("/api/projects/{slug}/restore", methods=["POST"])
async def api_restore(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        try:
            store.restore_project(conn, account["id"], request.path_params["slug"])
        except store.Refused as e:
            return _err(str(e), 404)
        return JSONResponse({"ok": True})


@mcp.custom_route("/api/trash", methods=["GET"])
async def api_trash(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        return JSONResponse({"projects": store.trash_for(conn, account["id"])})


@mcp.custom_route("/api/projects/{slug}/members", methods=["GET"])
async def api_members(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        ws = store.resolve_project(conn, account["id"], request.path_params["slug"])
        if ws is None:
            return _err("No such project.", 404)
        return JSONResponse({"role": ws["role"], "you": account["id"],
                             "members": store.members_of(conn, ws["id"])})


@mcp.custom_route("/api/projects/{slug}/members/{aid:int}", methods=["DELETE"])
async def api_remove_member(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        try:
            store.remove_member(conn, account["id"], request.path_params["slug"],
                                request.path_params["aid"])
        except store.Refused as e:
            return _err(str(e), 403)
        return JSONResponse({"ok": True})


@mcp.custom_route("/api/projects/{slug}/owner", methods=["POST"])
async def api_transfer(request: Request) -> JSONResponse:
    body = await _body(request)
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        try:
            store.transfer_ownership(conn, account["id"], request.path_params["slug"],
                                     int(body.get("account_id") or 0))
        except store.Refused as e:
            return _err(str(e), 403)
        return JSONResponse({"ok": True})


# invites

@mcp.custom_route("/api/projects/{slug}/invites", methods=["GET", "POST"])
async def api_invites(request: Request) -> JSONResponse:
    body = await _body(request) if request.method == "POST" else {}
    slug = request.path_params["slug"]
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        try:
            if request.method == "POST":
                inv = store.create_invite(conn, account["id"], slug, body.get("max_uses"))
                return JSONResponse({**inv, "url": f"{auth.public_url(request)}/join/{inv['code']}"})
            invites = store.list_invites(conn, account["id"], slug)
            base = auth.public_url(request)
            return JSONResponse({"invites": [{**i, "url": f"{base}/join/{i['code']}"}
                                             for i in invites]})
        except store.Refused as e:
            return _err(str(e), 403)


@mcp.custom_route("/api/projects/{slug}/invites/{iid:int}", methods=["DELETE"])
async def api_revoke_invite(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        try:
            store.revoke_invite(conn, account["id"], request.path_params["slug"],
                                request.path_params["iid"])
        except store.Refused as e:
            return _err(str(e), 403)
        return JSONResponse({"ok": True})


@mcp.custom_route("/api/invites/{code}", methods=["GET"])
async def api_invite_preview(request: Request) -> JSONResponse:
    """Readable without signing in: the link itself is the permission to see
    which project it is for and who sent it, and nothing more."""
    with db.connect() as conn:
        account = _web_account(conn, request)
        info = store.preview_invite(conn, request.path_params["code"],
                                    account["id"] if account else None)
    if info is None:
        return _err("This invite has expired or is no longer valid.", 404)
    return JSONResponse({**info, "signed_in": account is not None})


@mcp.custom_route("/api/invites/{code}/accept", methods=["POST"])
async def api_invite_accept(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        try:
            slug = store.accept_invite(conn, account["id"], request.path_params["code"])
        except store.Refused as e:
            return _err(str(e), 404)
        return JSONResponse({"slug": slug})


@mcp.custom_route("/api/projects/{slug}/link", methods=["GET"])
async def api_link(request: Request) -> JSONResponse:
    """The text that links a conversation to this project.

    Deliberately does not include any connector URL: that is set up once per
    assistant, under Connections, not once per project.
    """
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        ws = store.resolve_project(conn, account["id"], request.path_params["slug"])
        if ws is None:
            return _err("No such project.", 404)
        lang = request.query_params.get("lang", "ca")
        return JSONResponse(
            {"slug": ws["slug"], "text": linking.instructions(ws["slug"], ws["title"], lang)}
        )


@mcp.custom_route("/api/state", methods=["GET"])
async def api_state(request: Request) -> JSONResponse:
    with db.connect() as conn:
        account = _web_account(conn, request)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        ws = store.resolve_project(conn, account["id"], request.query_params.get("project"))
        if ws is None:
            return _err("No such project.", 404)
        return JSONResponse({**store.snapshot(conn, ws["id"]), "role": ws["role"]})


@mcp.custom_route("/events", methods=["GET"])
async def events(request: Request) -> Response:
    """Server-sent events for one project, driven by polling its high-water mark.

    Membership is checked when the stream opens and again on every tick, so
    someone removed from a project, or signed out, stops receiving it at once
    rather than whenever they next reload.
    """
    raw = request.cookies.get(SESSION)
    slug = request.query_params.get("project")
    with db.connect() as conn:
        account = store.account_for_session(conn, raw)
        if account is None:
            return JSONResponse(NOT_SIGNED_IN, status_code=401)
        ws = store.resolve_project(conn, account["id"], slug)
        if ws is None:
            return _err("No such project.", 404)
        ws_id, account_id = ws["id"], account["id"]

    async def stream():
        last = None
        while True:
            if await request.is_disconnected():
                return
            with db.connect() as conn:
                still = store.account_for_session(conn, raw)
                if still is None or store.resolve_project(conn, account_id, slug) is None:
                    yield "event: gone\ndata: {}\n\n"
                    return
                now = store.version(conn, ws_id)
                if now != last:
                    ws_now = store.resolve_project(conn, account_id, slug)
                    payload = json.dumps({**store.snapshot(conn, ws_id), "role": ws_now["role"]},
                                         ensure_ascii=False)
                    last = now
                else:
                    payload = None
            yield f"data: {payload}\n\n" if payload is not None else ": keep-alive\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- the page and its assets --------------------------------------------------------


STATIC = Path(__file__).resolve().parent / "static"
MIME = {".css": "text/css", ".js": "text/javascript", ".html": "text/html"}


@mcp.custom_route("/", methods=["GET"])
async def web_view(request: Request) -> HTMLResponse:
    """The shell. It holds no data of its own; everything it shows comes through
    the signed-in endpoints above, so serving it to anyone gives away nothing."""
    return HTMLResponse((STATIC / "index.html").read_text())


@mcp.custom_route("/join/{code}", methods=["GET"])
async def join_view(request: Request) -> HTMLResponse:
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


# --- app assembly ------------------------------------------------------------------


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
    with db.connect() as conn:
        purged = store.purge_deleted_projects(conn)
    if purged:
        print(f"purged {purged} project(s) deleted more than {store.TRASH_DAYS} days ago", flush=True)
    if not auth.configured():
        print("WARNING: Google sign-in is not configured (GOOGLE_CLIENT_ID, "
              "GOOGLE_CLIENT_SECRET). Nobody can sign in to the web.", flush=True)
    if os.environ.get("DEV_LOGIN") == "1":
        print("DEV_LOGIN is on: sign-in without Google is open to this machine only.", flush=True)
    # stateless_http keeps connectors working across redeploys: there is no
    # session for a restart to lose, and we will be redeploying constantly.
    app = mcp.http_app(path="/mcp", stateless_http=True, allowed_hosts=["*"])
    return TokenPath(app)


app = build_app()
