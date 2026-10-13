"""Personal and builtin skill versions stay explicit, visible and immutable."""
from __future__ import annotations

import uuid
from types import SimpleNamespace as NS

import pytest

from app.agent.conversation import skills as loader
from app.config import Settings
from app.skills.frontmatter import SkillError


def skill(source="upload", *, archived=False):
    return NS(id=uuid.uuid4(), slug="personal-method", source=source, enabled=True,
              archived_at="archived" if archived else None)


def version(identity, number=1):
    return NS(id=uuid.uuid4(), skill_id=identity.id, version=number, manifest={"description": "method"},
              content_hash="hash", files=[{"path": "SKILL.md", "content": "Original method"}])


def arrange(monkeypatch, identities, versions, latest=None):
    monkeypatch.setattr(loader.skills_repo, "list_visible", lambda *args, **kw: identities)
    monkeypatch.setattr(loader.skills_repo, "get_visible_skill", lambda db, id, owner:
                        next((s for s in identities if s.id == id), None))
    monkeypatch.setattr(loader.skills_repo, "get_version", lambda db, id:
                        next((v for v in versions if v.id == id), None))
    monkeypatch.setattr(loader.skills_repo, "latest_version", lambda db, id:
                        (latest or {}).get(id) or next((v for v in reversed(versions) if v.skill_id == id), None))


def test_manual_turn_can_select_own_upload_without_prior_session_binding(monkeypatch):
    identity = skill()
    selected = version(identity)
    arrange(monkeypatch, [identity], [selected])
    block, record = loader.load_for_turn(None, settings=Settings(), session_row=NS(skill_config={}),
        owner_user_id=uuid.uuid4(), metric_key=None,
        user_selection={"mode": "manual", "version_ids": [str(selected.id)]}, model_choice=None)
    assert "Original method" in block
    assert record.selected == [str(selected.id)]
    assert record.loaded[0]["version_id"] == str(selected.id)


def test_pinned_upload_precedes_builtin_pool_cap_and_keeps_exact_version(monkeypatch):
    personal = skill()
    pinned, newer = version(personal), version(personal, 2)
    builtins = [skill("builtin") for _ in range(12)]
    builtin_versions = [version(s) for s in builtins]
    arrange(monkeypatch, [*builtins, personal], [*builtin_versions, pinned, newer])
    pool = loader.candidate_pool(None, session_row=NS(skill_config={"version_ids": [str(pinned.id)]}),
        owner_user_id=uuid.uuid4(), metric_key=None)
    assert pool[0]["version_id"] == str(pinned.id)
    assert str(newer.id) not in [entry["version_id"] for entry in pool]
    assert len(pool) == loader.DEFAULT_POOL_LIMIT


def test_auto_never_loads_an_unbound_upload(monkeypatch):
    personal = skill()
    selected = version(personal)
    arrange(monkeypatch, [personal], [selected])
    assert loader.candidate_pool(None, session_row=NS(skill_config={}),
        owner_user_id=uuid.uuid4(), metric_key=None) == []


def test_archived_pinned_upload_is_unavailable_even_if_enabled(monkeypatch):
    personal = skill(archived=True)
    selected = version(personal)
    arrange(monkeypatch, [personal], [selected])
    with pytest.raises(SkillError):
        loader.load_for_turn(None, settings=Settings(),
            session_row=NS(skill_config={"mode": "manual", "version_ids": [str(selected.id)]}),
            owner_user_id=uuid.uuid4(), metric_key=None, user_selection=None, model_choice=None)


def test_manual_cannot_load_a_version_owned_by_another_user(monkeypatch):
    private = skill()
    selected = version(private)
    arrange(monkeypatch, [], [selected])
    with pytest.raises(SkillError):
        loader.load_for_turn(None, settings=Settings(), session_row=NS(skill_config={}),
            owner_user_id=uuid.uuid4(), metric_key=None,
            user_selection={"mode": "manual", "version_ids": [str(selected.id)]}, model_choice=None)


def test_explicit_empty_manual_selection_does_not_inherit_bound_versions(monkeypatch):
    identity = skill()
    selected = version(identity)
    arrange(monkeypatch, [identity], [selected])
    block, record = loader.load_for_turn(None, settings=Settings(),
        session_row=NS(skill_config={"mode": "manual", "version_ids": [str(selected.id)]}),
        owner_user_id=uuid.uuid4(), metric_key=None,
        user_selection={"mode": "manual", "version_ids": []}, model_choice=None)
    assert block == ""
    assert record.selected == []
