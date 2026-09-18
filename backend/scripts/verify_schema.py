#!/usr/bin/env python3
"""Verify the migrated control schema against the spec section 8 contract.

Checks table presence, the CHECK constraints and the named indexes. Exits
non-zero and prints differences when the migrated schema drifts from the
frozen DDL contract.
"""

from __future__ import annotations

import os
import sys

import psycopg

EXPECTED_TABLES = {
    "agent_messages",
    "agent_sessions",
    "analysis_artifacts",
    "analysis_steps",
    "analysis_tasks",
    "audit_logs",
    "auth_sessions",
    "datasets",
    "datasources",
    "execution_capacity",
    "execution_leases",
    "idempotency_keys",
    "ingestion_tasks",
    "metadata_snapshots",
    "metric_definitions",
    "permission_requests",
    "permissions",
    "policy_state",
    "query_dependencies",
    "query_jobs",
    "query_results",
    "roles",
    "saved_queries",
    "task_events",
    "task_queue",
    "user_roles",
    "users",
}

EXPECTED_CONSTRAINTS = {
    "ck_query_status",
    "ck_analysis_status",
    "ck_queue_state",
    "ck_step_status",
}

EXPECTED_INDEXES = {
    "ix_queries_owner_time",
    "ix_analyses_owner_time",
    "ix_queue_claim",
    "ix_events_resource",
    "ix_permissions_dataset",
    "ix_snapshots_dataset",
    "ix_audit_time",
    "ix_messages_session",
    "ix_analysis_session",
    "ix_artifacts_analysis",
    "ix_queries_analysis",
    "ix_dependencies_dataset",
    "ix_ingestions_source",
    "ix_leases_source",
    "ix_leases_user",
    "ix_results_expiry",
    "ix_roles_users",
}


def dsn() -> str:
    url = os.environ.get("AIND_DATABASE_URL", "")
    if not url:
        raise SystemExit("AIND_DATABASE_URL is required")
    # postgresql+psycopg://user:pass@host:port/db -> keyword form
    prefixless = url.split("://", 1)[1]
    credentials, hostpart = prefixless.split("@", 1)
    user, password = credentials.split(":", 1)
    hostport, database = hostpart.split("/", 1)
    host, port = hostport.split(":", 1)
    return f"host={host} port={port} dbname={database} user={user} password={password}"


def main() -> int:
    with psycopg.connect(dsn()) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        }
        constraints = {
            row[0]
            for row in conn.execute(
                """
                SELECT conname FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = 'public'
                """
            )
        }
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
            )
        }
    problems: list[str] = []
    tables.discard("alembic_version")
    missing_tables = EXPECTED_TABLES - tables
    if missing_tables:
        problems.append(f"missing tables: {sorted(missing_tables)}")
    extra_tables = tables - EXPECTED_TABLES
    if extra_tables:
        problems.append(f"unexpected tables: {sorted(extra_tables)}")
    missing_constraints = EXPECTED_CONSTRAINTS - constraints
    if missing_constraints:
        problems.append(f"missing constraints: {sorted(missing_constraints)}")
    missing_indexes = EXPECTED_INDEXES - indexes
    if missing_indexes:
        problems.append(f"missing indexes: {sorted(missing_indexes)}")
    if problems:
        print("schema verification FAILED")
        for item in problems:
            print(" -", item)
        return 1
    print(f"schema verification OK: {len(tables)} tables, {len(EXPECTED_CONSTRAINTS)} named state checks, {len(EXPECTED_INDEXES)} indexes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
