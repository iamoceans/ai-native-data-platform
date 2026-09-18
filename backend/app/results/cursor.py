"""Signed pagination cursors (spec section 14).

A cursor is bound to the result id, the offset and the schema hash, and is
signed with the platform cursor secret so clients cannot forge pages.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

from app.constants import ErrorCode
from app.errors import ApiError


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _encode(secret: str, data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{token}.{_sign(secret, token)[:32]}"


def _decode(secret: str, cursor: str) -> dict:
    try:
        token, signature = cursor.split(".", 1)
    except ValueError as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "malformed cursor") from exc
    if not hmac.compare_digest(signature, _sign(secret, token)[:32]):
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor signature is invalid")
    padded = token + "=" * (-len(token) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor payload is invalid") from exc


def encode_result_cursor(
    secret: str, *, result_id: uuid.UUID, offset: int, schema_hash: str
) -> str:
    return _encode(
        secret,
        {"kind": "result", "result_id": str(result_id), "offset": offset, "schema_hash": schema_hash},
    )


def decode_result_cursor(secret: str, cursor: str, *, result_id: uuid.UUID, schema_hash: str) -> int:
    payload = _decode(secret, cursor)
    if payload.get("kind") != "result":
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor kind mismatch")
    if payload.get("result_id") != str(result_id) or payload.get("schema_hash") != schema_hash:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor does not belong to this result")
    try:
        offset = int(payload["offset"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor offset is invalid") from exc
    if offset < 0:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor offset is invalid")
    return offset


def encode_list_cursor(secret: str, *, route: str, offset: int) -> str:
    return _encode(secret, {"kind": "list", "route": route, "offset": offset})


def decode_list_cursor(secret: str, cursor: str, *, route: str) -> int:
    payload = _decode(secret, cursor)
    if payload.get("kind") != "list" or payload.get("route") != route:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor does not belong to this listing")
    try:
        offset = int(payload["offset"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor offset is invalid") from exc
    if offset < 0:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor offset is invalid")
    return offset
