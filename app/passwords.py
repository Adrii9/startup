"""Password hashing, with nothing but the standard library.

scrypt is deliberately slow and memory-hungry: about 20 ms per hash here, which
nobody notices on sign-in and which makes trying millions of guesses against a
stolen copy of the database impractical. Each hash gets its own random salt, so
two people with the same password never share a stored value.

Stored as `scrypt$n$r$p$salt$hash`, so the cost can be raised later without
breaking the hashes already stored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

N, R, P = 2**14, 8, 1

MIN_LENGTH = 8


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, base64.b64decode(digest))


# Compared against when the username does not exist, so a failed sign-in takes
# the same time either way and the response does not reveal which usernames are
# taken.
DUMMY = hash_password("not a real password, only here to spend the same time")
