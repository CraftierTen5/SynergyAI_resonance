"""Password hashing and short-lived signed development sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1$" + _b64(salt) + "$" + _b64(digest)


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(actual, _unb64(expected))
    except (ValueError, TypeError):
        return False


def issue_token(*, user_id: str, institution_id: str, signing_key: str, ttl_seconds: int) -> str:
    payload = {
        "sub": user_id,
        "tid": institution_id,
        "exp": int(time.time()) + ttl_seconds,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def read_token(token: str, signing_key: str) -> dict[str, str]:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError
        data = json.loads(_unb64(body))
        if int(data["exp"]) <= int(time.time()):
            raise ValueError
        return {"user_id": str(data["sub"]), "institution_id": str(data["tid"])}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid or expired session") from exc


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
