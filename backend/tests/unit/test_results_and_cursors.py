"""Unit tests: result serialization and signed cursors (spec section 14)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.errors import ApiError
from app.results.cursor import (
    decode_list_cursor,
    decode_result_cursor,
    encode_list_cursor,
    encode_result_cursor,
)
from app.results.types import estimate_json_bytes, serialize_row, serialize_value

SECRET = "unit-test-secret"


def test_decimal_serializes_as_string():
    assert serialize_value(Decimal("123.450000")) == "123.450000"


def test_big_integer_serializes_as_string():
    assert serialize_value(2**60) == str(2**60)
    assert serialize_value(42) == 42


def test_float_special_values_become_null():
    assert serialize_value(float("nan")) is None
    assert serialize_value(float("inf")) is None


def test_datetime_serializes_as_isoformat():
    moment = datetime(2026, 9, 12, 1, 2, 3, tzinfo=timezone.utc)
    assert serialize_value(moment) == moment.isoformat()
    assert serialize_value(date(2026, 9, 12)) == "2026-09-12"


def test_json_structures_are_sanitized():
    payload = {"a": Decimal("1.5"), "b": [uuid.UUID(int=1)]}
    value = serialize_value(payload)
    assert value["a"] == "1.5"
    assert isinstance(value["b"][0], str)


def test_serialize_row_preserves_order():
    row = (Decimal("1"), None, "x")
    assert serialize_row(row) == ["1", None, "x"]


def test_estimate_json_bytes_is_reasonable():
    assert estimate_json_bytes(["abc"]) == len('["abc"]')


# ---------------------------------------------------------------------------
# cursors
# ---------------------------------------------------------------------------
def test_result_cursor_roundtrip():
    result_id = uuid.uuid4()
    token = encode_result_cursor(SECRET, result_id=result_id, offset=20, schema_hash="abc")
    assert decode_result_cursor(SECRET, token, result_id=result_id, schema_hash="abc") == 20


def test_result_cursor_rejects_other_result():
    token = encode_result_cursor(SECRET, result_id=uuid.uuid4(), offset=0, schema_hash="abc")
    with pytest.raises(ApiError):
        decode_result_cursor(SECRET, token, result_id=uuid.uuid4(), schema_hash="abc")


def test_cursor_signature_is_verified():
    token = encode_result_cursor(SECRET, result_id=uuid.uuid4(), offset=0, schema_hash="abc")
    payload, signature = token.split(".")
    raw = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    raw["offset"] = 9999
    forged = base64.urlsafe_b64encode(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")
    with pytest.raises(ApiError) as excinfo:
        decode_result_cursor(SECRET, f"{forged}.{signature}", result_id=uuid.UUID(raw["result_id"]), schema_hash="abc")
    assert "signature" in excinfo.value.message


def test_list_cursor_roundtrip_and_route_binding():
    token = encode_list_cursor(SECRET, route="GET /queries", offset=40)
    assert decode_list_cursor(SECRET, token, route="GET /queries") == 40
    with pytest.raises(ApiError):
        decode_list_cursor(SECRET, token, route="GET /datasets")
