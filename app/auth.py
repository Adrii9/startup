"""Signing in with Google.

The server-side authorization code flow: the browser is sent to Google, Google
sends it back with a one-time code, and the server swaps that code for the
person's identity directly with Google, using the client secret. Nothing about
the identity ever passes through the browser, so there is no token for a page
script to tamper with.

No passwords are stored here, and account recovery is Google's problem rather
than ours -- which is most of the reason for doing it this way.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx
from starlette.requests import Request

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def public_url(request: Request) -> str:
    """The address Google must send people back to.

    Set PUBLIC_URL in production. Behind Railway's proxy the request arrives as
    plain http, and a redirect URI that disagrees with the one registered at
    Google by so much as the scheme is rejected outright.
    """
    explicit = os.environ.get("PUBLIC_URL")
    if explicit:
        return explicit.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


def redirect_uri(request: Request) -> str:
    return public_url(request) + "/auth/google/callback"


def safe_next(value: str | None) -> str:
    """Where to land after signing in. Same-site relative paths only.

    Without this the sign-in round trip is an open redirect: a link to our login
    page could bounce a freshly signed-in person anywhere on the internet.
    """
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def authorize_url(request: Request, state: str) -> str:
    return GOOGLE_AUTH + "?" + urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })


async def identity(request: Request, code: str) -> dict:
    """Swap the one-time code for who this person is, straight from Google."""
    async with httpx.AsyncClient(timeout=10) as client:
        tok = await client.post(GOOGLE_TOKEN, data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": redirect_uri(request),
            "grant_type": "authorization_code",
        })
        tok.raise_for_status()
        info = await client.get(GOOGLE_USERINFO, headers={
            "Authorization": f"Bearer {tok.json()['access_token']}"
        })
        info.raise_for_status()
        return info.json()
