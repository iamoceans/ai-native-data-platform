"""Result file storage: Arrow IPC + JSON, atomic publish (spec section 14).

Layout: RESULT_ROOT/<result-uuid>/result.json and result.arrow.
Publish uses write-to-temp -> fsync -> rename; readers only ever see complete
files. storage_key is a relative key; user input is never used as a path.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc

from app.constants import ErrorCode, SCHEMA_VERSION
from app.errors import ApiError


@dataclass(frozen=True)
class StoredResult:
    result_id: uuid.UUID
    storage_key: str
    content_hash: str
    byte_count: int
    row_count: int
    truncated: bool
    columns: list[dict]


def _arrow_type(type_name: str) -> pa.DataType:
    base = type_name.split("(", 1)[0].strip().lower()
    mapping = {
        "boolean": pa.bool_(),
        "smallint": pa.int16(),
        "integer": pa.int32(),
        "bigint": pa.int64(),
        "real": pa.float32(),
        "double precision": pa.float64(),
        "date": pa.date32(),
        "timestamp": pa.timestamp("us"),
        "timestamp with time zone": pa.timestamp("us", tz="UTC"),
        "timestamp without time zone": pa.timestamp("us"),
        "text": pa.string(),
        "character varying": pa.string(),
        "character": pa.string(),
        "uuid": pa.string(),
    }
    if base == "numeric":
        return pa.string()  # exactness preserved as text in M1
    return mapping.get(base, pa.string())


def _arrow_value(value, type_name: str):
    base = type_name.split("(", 1)[0].strip().lower()
    if value is None:
        return None
    if base == "numeric":
        return format(value, "f") if isinstance(value, Decimal) else str(value)
    if base in {"json", "jsonb"}:
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, (date, datetime, Decimal, bytes, bytearray, memoryview)):
        return value if not isinstance(value, (bytes, bytearray, memoryview)) else bytes(value).hex()
    return value


def build_arrow_table(columns: list[dict], rows: list[tuple]) -> pa.Table:
    arrays = []
    names = []
    for index, column in enumerate(columns):
        values = [_arrow_value(row[index], column["type"]) for row in rows]
        try:
            array = pa.array(values, type=_arrow_type(column["type"]))
        except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
            array = pa.array([None if v is None else str(v) for v in values], type=pa.string())
        arrays.append(array)
        names.append(column["name"])
    return pa.Table.from_arrays(arrays, names=names)


class ResultStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, storage_key: str) -> Path:
        candidate = (self._root / storage_key).resolve()
        if self._root not in candidate.parents:
            raise ApiError(ErrorCode.STORAGE_UNAVAILABLE, "invalid storage key")
        return candidate

    def publish(
        self,
        *,
        columns: list[dict],
        rows_json: list[list],
        rows_typed: list[tuple],
        truncated: bool,
    ) -> StoredResult:
        result_id = uuid.uuid4()
        key_dir = str(result_id)
        target = self._root / key_dir
        tmp = self._root / f".tmp-{result_id.hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "columns": columns,
                "rows": rows_json,
                "row_count": len(rows_json),
                "truncated": truncated,
            }
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            content_hash = hashlib.sha256(body).hexdigest()
            json_path = tmp / "result.json"
            arrow_path = tmp / "result.arrow"
            with open(json_path, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            table = build_arrow_table(columns, rows_typed)
            with open(arrow_path, "wb") as handle:
                writer = ipc.new_file(handle, table.schema)
                try:
                    writer.write_table(table)
                finally:
                    writer.close()
                handle.flush()
                os.fsync(handle.fileno())
            target.mkdir(parents=True, exist_ok=True)
            os.replace(json_path, target / "result.json")
            os.replace(arrow_path, target / "result.arrow")
            return StoredResult(
                result_id=result_id,
                storage_key=key_dir,
                content_hash=content_hash,
                byte_count=len(body),
                row_count=len(rows_json),
                truncated=truncated,
                columns=columns,
            )
        except OSError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            raise ApiError(
                ErrorCode.STORAGE_UNAVAILABLE, f"could not publish result files: {exc}"
            ) from exc
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)

    def read_json(self, storage_key: str) -> dict:
        path = self.path_for(storage_key) / "result.json"
        if not path.is_file():
            raise ApiError(ErrorCode.RESULT_UNAVAILABLE, "result file is missing")
        try:
            with open(path, "rb") as handle:
                payload = handle.read()
            return json.loads(payload.decode("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApiError(ErrorCode.RESULT_UNAVAILABLE, "result file is corrupt") from exc

    def delete(self, storage_key: str) -> None:
        try:
            directory = self.path_for(storage_key)
        except ApiError:
            return
        shutil.rmtree(directory, ignore_errors=True)


def columns_payload(handle_columns: list) -> list[dict]:
    """Stable column ids: c0, c1, ... (spec section 14: duplicate names get
    distinct ids that ChartSpec references)."""
    payload = []
    for index, column in enumerate(handle_columns):
        item = {
            "id": f"c{index}",
            "name": column.name,
            "type": column.type_name,
        }
        if column.scale is not None:
            item["scale"] = column.scale
        payload.append(item)
    return payload
