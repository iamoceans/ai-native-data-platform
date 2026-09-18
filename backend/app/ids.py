"""Small helpers: ids, hashing, UTC clock."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def token_hash(token: str) -> str:
    """Hash for bearer-like tokens (sessions, CSRF). Never store the raw token."""
    return sha256_hex(f"ainative:{token}")


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
