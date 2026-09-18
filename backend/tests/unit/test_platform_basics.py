"""Unit tests: secret resolver, datasource host policy, passwords, limits."""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.constants import ErrorCode
from app.datasource.secrets import SecretResolver
from app.datasource.service import validate_host
from app.errors import ApiError
from app.query.limits import effective_limits
from app.workers.common import _has_activity
from app.api.dto import QueryLimits


def test_reconciler_activity_ignores_nested_zero_counts():
    assert not _has_activity({"requeued": 0, "failed": 0})
    assert not _has_activity({"items": [], "cleanup": {"deleted": 0}})
    assert _has_activity({"requeued": 1, "failed": 0})
    assert _has_activity(["query-id"])


# ---------------------------------------------------------------------------
# SecretResolver
# ---------------------------------------------------------------------------
def test_secret_resolver_reads_mounted_file(workdir):
    (workdir / "source-postgres.json").write_text(
        json.dumps({"username": "reader", "password": "pw"}), encoding="utf-8"
    )
    resolver = SecretResolver(workdir)
    credentials = resolver.resolve("source-postgres")
    assert credentials.username == "reader"
    assert credentials.password == "pw"


@pytest.mark.parametrize(
    "bad_ref",
    [
        "../etc/passwd",
        "..",
        "a/../../b",
        "C:\\windows\\system32",
        "a b",
        "",
        "x",  # too short
    ],
)
def test_secret_resolver_rejects_path_like_refs(workdir, bad_ref):
    resolver = SecretResolver(workdir)
    with pytest.raises(ApiError):
        resolver.resolve(bad_ref)


def test_secret_resolver_rejects_missing_mount(workdir):
    resolver = SecretResolver(workdir)
    with pytest.raises(ApiError) as excinfo:
        resolver.resolve("not-mounted")
    assert excinfo.value.code == ErrorCode.DATASOURCE_UNAVAILABLE


def test_secret_resolver_rejects_malformed_json(workdir):
    (workdir / "bad.json").write_text("{not json", encoding="utf-8")
    resolver = SecretResolver(workdir)
    with pytest.raises(ApiError) as excinfo:
        resolver.resolve("bad")
    assert excinfo.value.code == ErrorCode.DATASOURCE_UNAVAILABLE


# ---------------------------------------------------------------------------
# host policy
# ---------------------------------------------------------------------------
def settings_with_allowlist(allowlist: str) -> Settings:
    return Settings(source_host_allowlist=allowlist, database_url="postgresql+psycopg://x/y")


def test_host_allowlist_accepts_listed_host():
    validate_host("127.0.0.1", settings_with_allowlist("127.0.0.1,localhost"))


def test_host_allowlist_accepts_cidr():
    validate_host("172.20.0.5", settings_with_allowlist("172.16.0.0/12"))


def test_host_allowlist_rejects_unlisted():
    with pytest.raises(ApiError) as excinfo:
        validate_host("10.1.2.3", settings_with_allowlist("127.0.0.1"))
    assert excinfo.value.code == ErrorCode.FORBIDDEN


def test_host_allowlist_rejects_metadata_endpoints():
    settings = settings_with_allowlist("0.0.0.0/0,169.254.0.0/16")
    with pytest.raises(ApiError):
        validate_host("169.254.169.254", settings)
    with pytest.raises(ApiError):
        validate_host("metadata.google.internal", settings)


def test_host_allowlist_rejects_unix_socket_syntax():
    with pytest.raises(ApiError):
        validate_host("/var/run/postgresql", settings_with_allowlist("127.0.0.1"))


# ---------------------------------------------------------------------------
# limits
# ---------------------------------------------------------------------------
def test_default_limits():
    settings = Settings(database_url="postgresql+psycopg://x/y")
    limits = effective_limits(None, settings)
    assert limits.max_rows == settings.default_max_rows
    assert limits.timeout_seconds == settings.default_timeout_seconds


def test_limits_are_clamped_to_hard_maximums():
    settings = Settings(
        database_url="postgresql+psycopg://x/y",
        hard_max_rows=1234,
        hard_max_timeout_seconds=60,
        hard_max_bytes=2 * 1024 * 1024,
    )
    limits = effective_limits(
        QueryLimits(max_rows=100_000, timeout_seconds=3600, max_bytes=100 * 1024 * 1024), settings
    )
    assert limits.max_rows == 1234
    assert limits.timeout_seconds == 60
    assert limits.max_bytes == 2 * 1024 * 1024


def test_limits_keep_smaller_requested_values():
    settings = Settings(database_url="postgresql+psycopg://x/y")
    limits = effective_limits(QueryLimits(max_rows=10, timeout_seconds=3), settings)
    assert limits.max_rows == 10
    assert limits.timeout_seconds == 3


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------
def test_password_hash_verify_roundtrip():
    from app.auth.passwords import hash_password, verify_password

    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery staple")
    assert not verify_password(hashed, "wrong password")


# ---------------------------------------------------------------------------
# login rate limiter
# ---------------------------------------------------------------------------
def test_login_rate_limiter_blocks_after_max_attempts():
    from app.auth.ratelimit import LoginRateLimiter

    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        assert limiter.check("user", "1.1.1.1")
        limiter.record_failure("user", "1.1.1.1")
    assert not limiter.check("user", "1.1.1.1")
    assert limiter.check("other", "1.1.1.1")
    limiter.reset("user", "1.1.1.1")
    assert limiter.check("user", "1.1.1.1")


def test_query_pagination_rejects_invalid_limits():
    from types import SimpleNamespace
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from app.api.deps import current_auth, get_db
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(
        user=SimpleNamespace(id=uuid4())
    )
    app.dependency_overrides[get_db] = lambda: None

    with TestClient(app, raise_server_exceptions=False) as client:
        malformed = client.get("/api/v1/queries?limit=abc")
        negative = client.get(f"/api/v1/queries/{uuid4()}/results?limit=-1")

    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "VALIDATION_ERROR"
    assert negative.status_code == 422
    assert negative.json()["error"]["code"] == "VALIDATION_ERROR"
