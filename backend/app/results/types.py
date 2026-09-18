"""Result value serialization (spec section 14).

Decimals and integers beyond the JS safe range travel as strings; NULL stays
null; NaN/Infinity never enter JSON. The same conversion is used for the JSON
result file and the API responses so they can never disagree.
"""

from __future__ import annotations

import base64
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

JS_SAFE_INT = 2**53 - 1


def serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > JS_SAFE_INT:
            return str(value)
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, (dict, list)):
        return _sanitize_json(value)
    if isinstance(value, (str,)):
        return value
    return str(value)


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    return serialize_value(value)


def serialize_row(row: tuple) -> list[Any]:
    return [serialize_value(value) for value in row]


def estimate_json_bytes(payload: Any) -> int:
    import json

    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))
