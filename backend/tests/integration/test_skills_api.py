"""Skill library acceptance: S01, S02, S03, S07 (design 13.2)."""

from __future__ import annotations

import io
import secrets
import zipfile

import pytest

from app.auth.passwords import hash_password
from app.config import get_settings
from app.constants import RoleName
from app.db import session_scope
from app.metrics.registry import MetricDefinitionModel, sync_metric_definitions
from app.repositories import datasources as datasources_repo
from app.repositories import users as users_repo
from app.skills.service import sync_builtins

pytestmark = pytest.mark.integration

BODY = "# 收入变化分析\n先明确期间、时区和指标口径。\n"

FRONT_MATTER = """---
name: {name}
description: 分析收入变化，优先核实口径、完整性和渠道贡献。
metadata:
  askdata:
    schema_version: 1
    metric_keys: [{metrics}]
    preferred_dimensions: [{dimensions}]
    required_inputs: [metric_key, baseline_period, current_period]
{extra}---
"""


def skill_md(
    name: str = "revenue-diagnosis",
    *,
    metrics: str = "fixture_revenue",
    dimensions: str = "country, platform",
    extra: str = "",
    body: str = BODY,
) -> str:
    return FRONT_MATTER.format(
        name=name, metrics=metrics, dimensions=dimensions, extra=extra
    ) + body


def zip_bundle(*, name: str = "revenue-diagnosis", with_reference: bool = True) -> bytes:
    extra = "    references: [references/notes.md]\n" if with_reference else ""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{name}/SKILL.md", skill_md(name=name, extra=extra))
        if with_reference:
            archive.writestr(f"{name}/references/notes.md", "口径说明。\n")
    return buffer.getvalue()


def upload(client, *, filename: str, data: bytes, path: str = "/api/v1/skills", expected_hash=None):
    payload = {"expected_hash": expected_hash} if expected_hash else {}
    return client.post(
        path,
        files={"file": (filename, data, "application/octet-stream")},
        data=payload,
    )


@pytest.fixture()
def skills_env(admin_client):
    """A metric for the applicability check, plus a second analyst identity."""
    definition = MetricDefinitionModel.model_validate(
        {
            "metric_key": "fixture_revenue",
            "version": 1,
            "name": "Fixture revenue",
            "dataset": "public.fixture_metrics",
            "components": {"revenue": {"aggregation": "sum", "column": "revenue_usd"}},
            "formula": "revenue",
            "unit": "USD",
            "currency": "USD",
            "timezone": "UTC",
            "grain": ["dt", "country", "platform"],
            "allowed_dimensions": ["country", "platform"],
            "aggregation_kind": "sum",
            "freshness": {"date_column": "dt", "completeness_source": "fixture"},
        }
    )
    with session_scope() as session:
        sync_metric_definitions(session, [definition])
    return admin_client


@pytest.fixture()
def analyst_client(client):
    """A second analyst on its own HTTP client (the shared one keeps admin's session)."""
    from fastapi.testclient import TestClient

    password = "analyst-password-" + secrets.token_urlsafe(6)
    with session_scope() as session:
        role = users_repo.get_role_by_name(session, RoleName.ANALYST)
        user = users_repo.create_user(session, "analyst2", hash_password(password))
        users_repo.set_user_roles(session, user.id, [role.id])
        datasources_repo.ensure_user_capacity(
            session, user.id, get_settings().per_user_concurrency
        )
    fresh = TestClient(client.app)
    login = fresh.post("/api/v1/auth/login", json={"username": "analyst2", "password": password})
    assert login.status_code == 200, login.text
    fresh.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
    return fresh


def test_s01_preview_then_import_and_read_back(skills_env):
    client = skills_env
    bundle = zip_bundle()

    preview = upload(client, filename="skill.zip", data=bundle, path="/api/v1/skills/import-preview")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["name"] == "revenue-diagnosis"
    assert body["compatibility"]["applicable"] is True
    assert body["body_preview"].startswith("# 收入变化分析")
    assert [item["path"] for item in body["files"]] == ["SKILL.md", "references/notes.md"]

    created = upload(
        client, filename="skill.zip", data=bundle, expected_hash=body["content_hash"]
    )
    assert created.status_code == 201, created.text
    skill = created.json()
    assert skill["version_count"] == 1
    assert skill["latest_version"]["content_hash"] == body["content_hash"]

    listing = client.get("/api/v1/skills").json()["items"]
    assert [row["slug"] for row in listing] == ["revenue-diagnosis"]

    version_id = skill["latest_version"]["id"]
    detail = client.get(f"/api/v1/skills/{skill['id']}/versions/{version_id}").json()
    assert {item["path"] for item in detail["files"]} == {"SKILL.md", "references/notes.md"}


def test_preview_hash_mismatch_is_refused(skills_env):
    client = skills_env
    bundle = zip_bundle()
    response = upload(
        client, filename="skill.zip", data=bundle, expected_hash="0" * 64
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SKILL_INVALID"


def test_s02_same_content_replays_and_new_content_versions(skills_env):
    client = skills_env
    first = upload(client, filename="SKILL.md", data=skill_md().encode())
    assert first.status_code == 201, first.text
    skill_id = first.json()["id"]

    replay = upload(client, filename="SKILL.md", data=skill_md().encode())
    assert replay.status_code == 201
    assert replay.json()["version_count"] == 1  # same hash: no new version

    changed = skill_md(body=BODY + "补充一行说明。\n").encode()
    second = upload(
        client, filename="SKILL.md", data=changed, path=f"/api/v1/skills/{skill_id}/versions"
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2

    versions = client.get(f"/api/v1/skills/{skill_id}").json()["versions"]
    assert [row["version"] for row in versions] == [1, 2]
    # Version 1 content is immutable: it still holds the original body.
    first_version = client.get(
        f"/api/v1/skills/{skill_id}/versions/{versions[0]['id']}"
    ).json()
    assert "补充一行说明" not in first_version["files"][0]["content"]


def test_s03_another_analyst_cannot_see_or_touch_the_skill(skills_env, analyst_client):
    owner = skills_env
    created = upload(owner, filename="SKILL.md", data=skill_md(name="s03-private-skill").encode())
    assert created.status_code == 201, created.text
    skill_id = created.json()["id"]

    other = analyst_client
    assert other.get(f"/api/v1/skills/{skill_id}").status_code == 404
    assert other.get("/api/v1/skills").json()["items"] == []
    assert other.patch(f"/api/v1/skills/{skill_id}", json={"enabled": False}).status_code == 404
    assert (
        upload(other, filename="SKILL.md", data=skill_md(name="s03-private-skill").encode(),
               path=f"/api/v1/skills/{skill_id}/versions").status_code
        == 404
    )


def test_s07_disable_and_archive_are_identity_level(skills_env):
    client = skills_env
    created = upload(client, filename="SKILL.md", data=skill_md().encode())
    skill_id = created.json()["id"]

    disabled = client.patch(f"/api/v1/skills/{skill_id}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["version_count"] == 1  # content untouched

    archived = client.patch(f"/api/v1/skills/{skill_id}", json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert client.get("/api/v1/skills").json()["items"] == []
    listed = client.get("/api/v1/skills", params={"include_archived": True}).json()["items"]
    assert [row["id"] for row in listed] == [skill_id]


def test_applicability_is_recorded_and_never_extended(skills_env):
    client = skills_env
    unknown = upload(
        client,
        filename="SKILL.md",
        data=skill_md(name="unknown-metric-skill", metrics="not_a_metric").encode(),
    )
    assert unknown.status_code == 201  # saved, but not applicable
    compatibility = unknown.json()["latest_version"]["compatibility"]
    assert compatibility["applicable"] is False
    assert compatibility["unknown_metric_keys"] == ["not_a_metric"]

    bad_dimension = upload(
        client,
        filename="SKILL.md",
        data=skill_md(name="bad-dimension-skill", dimensions="warehouse").encode(),
    )
    assert bad_dimension.status_code == 201
    compatibility = bad_dimension.json()["latest_version"]["compatibility"]
    assert compatibility["applicable"] is False
    assert compatibility["invalid_dimensions"] == {"fixture_revenue": ["warehouse"]}


def test_builtins_are_synced_read_only_and_cannot_be_shadowed(skills_env):
    client = skills_env
    with session_scope() as session:
        report = sync_builtins(session, settings=get_settings())
    assert report["created"] >= 1

    listing = client.get("/api/v1/skills").json()["items"]
    builtins = [row for row in listing if row["source"] == "builtin"]
    assert {row["slug"] for row in builtins} == {
        "revenue-change-reading",
        "channel-contribution",
        "data-completeness-check",
    }
    assert all(row["latest_version"] for row in builtins)

    builtin_id = builtins[0]["id"]
    assert client.patch(f"/api/v1/skills/{builtin_id}", json={"enabled": False}).status_code == 403

    shadow = upload(client, filename="SKILL.md", data=skill_md(name="channel-contribution").encode())
    assert shadow.status_code == 409
    assert shadow.json()["error"]["code"] == "CONFLICT"


def test_personal_skill_quota_at_50_preserves_idempotent_replay(skills_env):
    client = skills_env
    assert get_settings().skills_per_user_max == 50
    for index in range(50):
        response = upload(client, filename='SKILL.md', data=skill_md(name=f'quota-skill-{index}').encode())
        assert response.status_code == 201, response.text
    denied = upload(client, filename='SKILL.md', data=skill_md(name='one-too-many').encode())
    assert denied.status_code == 413 and denied.json()['error']['code'] == 'SKILL_TOO_LARGE'
    replay = upload(client, filename='SKILL.md', data=skill_md(name='quota-skill-0').encode())
    assert replay.status_code == 201 and replay.json()['version_count'] == 1


def test_skill_version_quota_at_20_preserves_existing_hash(skills_env):
    client = skills_env
    assert get_settings().skill_versions_max == 20
    first = upload(client, filename='SKILL.md', data=skill_md().encode())
    path = f"/api/v1/skills/{first.json()['id']}/versions"
    for index in range(2, 21):
        response = upload(client, filename='SKILL.md', data=skill_md(body=BODY + f'方法说明 {index}\n').encode(), path=path)
        assert response.status_code == 201 and response.json()['version'] == index
    denied = upload(client, filename='SKILL.md', data=skill_md(body=BODY + '超出配额\n').encode(), path=path)
    assert denied.status_code == 413 and denied.json()['error']['code'] == 'SKILL_TOO_LARGE'
    replay = upload(client, filename='SKILL.md', data=skill_md().encode(), path=path)
    assert replay.status_code == 201 and replay.json()['version'] == 1
