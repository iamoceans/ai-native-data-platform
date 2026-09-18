"""Shared test fixtures.

Integration tests require AIND_DATABASE_URL to point at the control database
(started via `make db-up-dev`). Without it they are reported as skipped, never
as passed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# The demo generator package lives at the repository root (spec section 28).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Default local test environment (the Makefile/dev.ps1 export the real values).
os.environ.setdefault("AIND_LOG_LEVEL", "WARNING")
os.environ.setdefault("AIND_SOURCE_HOST_ALLOWLIST", "127.0.0.1,localhost")
os.environ.setdefault("AIND_SECRETS_DIR", str(ROOT / "infra" / "local-secrets"))
_TEST_RESULTS = Path(tempfile.gettempdir()) / "ainative-test-results"
os.environ.setdefault("AIND_RESULTS_DIR", str(_TEST_RESULTS))
# Ingestion payloads written by the in-process app must stay out of the repo
# (the containerized stack uses the shared docker volume instead).
os.environ.setdefault(
    "AIND_INGESTION_WORK_DIR", str(Path(tempfile.gettempdir()) / "ainative-test-ingestion")
)
os.environ.setdefault("AIND_CURSOR_SECRET", "test-cursor-secret-not-for-production")
os.environ.setdefault("AIND_QUEUE_TIMEOUT_SECONDS", "60")

pytestmark = []


@pytest.fixture()
def workdir():
    """Scratch directory owned by the test process (pytest's tmp_path can hit
    pre-existing ACLs on shared Windows temp roots)."""
    import shutil
    import uuid as _uuid

    base = Path(tempfile.gettempdir()) / "ainative-tests"
    path = base / _uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def database_available() -> bool:
    url = os.environ.get("AIND_DATABASE_URL")
    if not url:
        return False
    try:
        import psycopg

        prefixless = url.split("://", 1)[1]
        credentials, hostpart = prefixless.split("@", 1)
        user, password = credentials.split(":", 1)
        hostport, database = hostpart.split("/", 1)
        host, port = hostport.split(":", 1)
        with psycopg.connect(
            f"host={host} port={port} dbname={database} user={user} password={password}",
            connect_timeout=3,
        ):
            return True
    except Exception:
        return False


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires real PostgreSQL services")


def pytest_collection_modifyitems(config, items):
    if database_available():
        return
    skip = pytest.mark.skip(reason="AIND_DATABASE_URL not set or control DB unreachable")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def integration_env() -> dict:
    url = os.environ.get("AIND_DATABASE_URL")
    if not url:
        pytest.skip("AIND_DATABASE_URL not set")
    class _RedactedEnvironment(dict):
        def __repr__(self) -> str:
            return "<integration environment: credentials redacted>"

    return _RedactedEnvironment(
        database_url=url, source_url=os.environ.get("AIND_TEST_SOURCE_URL")
    )


@pytest.fixture(scope="session", autouse=True)
def migrated_control_db():
    if not database_available():
        yield False
        return
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:  # pragma: no cover - environment failure
        raise RuntimeError(f"alembic upgrade failed: {result.stderr}")
    yield True


@pytest.fixture()
def clean_db(migrated_control_db):
    if not migrated_control_db:
        pytest.skip("control DB unavailable")
    from sqlalchemy import text

    from app.db import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        # Fail fast instead of queueing behind a stray session (e.g. a test run
        # that was killed mid-transaction on another host/process).
        conn.execute(text("SET LOCAL lock_timeout = '15s'"))
        tables = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
            )
        ).fetchall()
        if tables:
            names = ", ".join(f'"{row[0]}"' for row in tables)
            conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def app(clean_db):
    _TEST_RESULTS.mkdir(parents=True, exist_ok=True)
    from app.db import get_settings as _unused  # noqa: F401
    from app.main import create_app
    from app.runtime import ResultStore, set_result_store

    set_result_store(ResultStore(_TEST_RESULTS / uuid.uuid4().hex))
    application = create_app()
    yield application
    set_result_store(None)


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture()
def bootstrap(clean_db):
    """Create roles, global capacity and an admin user."""
    import secrets

    from app.auth.passwords import hash_password
    from app.config import get_settings
    from app.constants import RoleName
    from app.db import session_scope
    from app.repositories import datasources as datasources_repo
    from app.repositories import policy as policy_repo
    from app.repositories import users as users_repo

    settings = get_settings()
    password = "admin-password-" + secrets.token_urlsafe(6)
    with session_scope() as session:
        roles = {}
        for name in RoleName:
            role = users_repo.get_role_by_name(session, name)
            if role is None:
                role = users_repo.create_role(session, name)
            roles[name] = role
        policy_repo.get_revision(session)
        datasources_repo.ensure_global_capacity(session, settings.global_concurrency)
        admin = users_repo.create_user(session, "admin", hash_password(password))
        users_repo.set_user_roles(
            session, admin.id, [roles[RoleName.ADMIN].id, roles[RoleName.ANALYST].id]
        )
        datasources_repo.ensure_user_capacity(session, admin.id, settings.per_user_concurrency)
    return {"username": "admin", "password": password}


def login(client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


@pytest.fixture()
def admin_client(client, bootstrap):
    csrf = login(client, bootstrap["username"], bootstrap["password"])
    client.headers.update({"X-CSRF-Token": csrf})
    return client


@pytest.fixture()
def viewer(bootstrap):
    """Create a viewer user; returns credentials."""
    import secrets

    from app.auth.passwords import hash_password
    from app.constants import RoleName
    from app.db import session_scope
    from app.repositories import datasources as datasources_repo
    from app.repositories import users as users_repo

    from app.config import get_settings

    password = "viewer-password-" + secrets.token_urlsafe(6)
    with session_scope() as session:
        role = users_repo.get_role_by_name(session, RoleName.VIEWER)
        user = users_repo.create_user(session, "viewer1", hash_password(password))
        users_repo.set_user_roles(session, user.id, [role.id])
        datasources_repo.ensure_user_capacity(session, user.id, get_settings().per_user_concurrency)
    return {"username": "viewer1", "password": password}
