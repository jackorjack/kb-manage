"""Password and session primitives using Python's standard library."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Tuple


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("password must contain at least 8 characters")
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=64)
    encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii")
    return f"scrypt${n}${r}${p}${encode(salt)}${encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, digest_value = stored.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def password_pair(password: str) -> Tuple[str, str]:
    return password, hash_password(password)
