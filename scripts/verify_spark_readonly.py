#!/usr/bin/env python3
"""Verify source-side denial of writes in an explicitly dedicated Spark catalog.

Use private JSON connection files, never passwords in command arguments. The
owner creates random disposable tables only in a pre-created aind_authz_* schema.
The reader must SELECT successfully and get an authorization error on every
write. Syntax errors, timeouts and missing tables never count as authorization.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import sys
import uuid

from impala.dbapi import connect


@dataclass
class Verification:
    passed: bool = False
    checks: list[str] = field(default_factory=list)
    reason: str = "not_started"


def authorization_denial(error: Exception) -> bool:
    message = str(error).lower()
    return bool(re.search(r"permission denied|access denied|not authorized|authorizationexception|"
                          r"authorization failed|insufficient[_ ]privilege|sqlstate.?42501", message))


def verify_source(reader, owner, database: str) -> Verification:
    if not re.fullmatch(r"aind_authz_[a-z0-9_]{1,80}", database):
        raise ValueError("A pre-created dedicated aind_authz_* database is required")
    name = f"{database}.probe_{uuid.uuid4().hex}"
    auxiliary = name + "_new"
    renamed = name + "_renamed"
    result = Verification()
    created = False
    try:
        result.reason = "fixture_setup_failed"
        created = True
        owner.execute(f"CREATE TABLE {name} (id INT, amount INT) USING PARQUET")
        owner.execute(f"INSERT INTO {name} VALUES (1, 7)")

        def snapshot(cursor):
            cursor.execute(f"SELECT * FROM {name} ORDER BY id")
            return tuple(item[0] for item in cursor.description), cursor.fetchall()

        result.reason = "initial_read_failed"
        baseline = snapshot(owner)
        if baseline != (("id", "amount"), [(1, 7)]):
            result.reason = "owner_seed_mismatch"
            return result
        if snapshot(reader) != baseline:
            result.reason = "reader_snapshot_mismatch"
            return result
        statements = [
            ("insert", f"INSERT INTO {name} VALUES (2, 9)"),
            ("overwrite", f"INSERT OVERWRITE TABLE {name} SELECT 2, 9"),
            ("truncate", f"TRUNCATE TABLE {name}"),
            ("alter", f"ALTER TABLE {name} ADD COLUMNS (extra INT)"),
            ("rename", f"ALTER TABLE {name} RENAME TO {renamed}"),
            ("create_as_select", f"CREATE TABLE {auxiliary} USING PARQUET AS SELECT * FROM {name}"),
            ("drop", f"DROP TABLE {name}"),
        ]
        for label, sql in statements:
            try:
                reader.execute(sql)
            except Exception as error:
                if not authorization_denial(error):
                    result.reason = f"{label}:not_an_authorization_denial"
                    return result
            else:
                result.reason = f"{label}:write_allowed"
                return result
            result.reason = f"{label}:post_denial_read_failed"
            if snapshot(owner) != baseline or snapshot(reader) != baseline:
                result.reason = f"{label}:source_changed"
                return result
            result.checks.append(label)
        result.passed, result.reason = True, "all_writes_denied_and_source_unchanged"
    except Exception:
        # Remote errors can echo connection details or credentials: report the
        # current phase, never their raw text.
        pass
    finally:
        if created:
            for table in (name, auxiliary, renamed):
                try:
                    owner.execute(f"DROP TABLE IF EXISTS {table}")
                except Exception:
                    result.passed, result.reason = False, "fixture_cleanup_failed"
    return result


def connection_file(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"host", "port", "user", "password", "database", "ca_cert", "auth_mechanism", "use_ssl"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Invalid connection file")
    if payload.get("auth_mechanism") != "PLAIN" or payload.get("use_ssl") is not True:
        raise ValueError("Read-only acceptance requires PLAIN over verified TLS")
    for key in ("host", "user", "password", "database"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError("Missing connection field")
    if not re.fullmatch(r"aind_authz_[a-z0-9_]{1,80}", payload["database"]):
        raise ValueError("A dedicated aind_authz_* database is required")
    return {**payload, "timeout": 20, "verify_cert": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader-config", type=Path, required=True)
    parser.add_argument("--owner-config", type=Path, required=True)
    args = parser.parse_args()
    connections = []
    try:
        reader_config, owner_config = connection_file(args.reader_config), connection_file(args.owner_config)
        if any(reader_config.get(key) != owner_config.get(key) for key in ("host", "port", "database")):
            raise ValueError("Reader and owner must address the same dedicated source")
        for config in (reader_config, owner_config):
            connections.append(connect(**config))
        cursors = [connection.cursor() for connection in connections]
        try:
            result = verify_source(*cursors, reader_config["database"])
        finally:
            for cursor in cursors:
                cursor.close()
    except Exception:
        print(json.dumps({"passed": False, "reason": "configuration_connection_or_setup_failed"}))
        return 1
    finally:
        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass
    print(json.dumps(asdict(result)))
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
