"""Business memory: what is allowed to be learned, and how it is learned.

The load-bearing rules are that a statement may never carry a figure the kernel
did not compute, and that learning can never change an analysis outcome.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.agent import memory
from app.agent.llm import FakeLLMProvider, ProviderResolution
from app.agent.memory import (
    MemoryExtraction,
    dedupe_key,
    evidence_digest,
    evidence_identifiers,
    normalize_statement,
    planner_brief,
    remember_completed,
    statement_violations,
)
from app.config import Settings

ALLOWED = {"country", "platform", "app_version", "ad_network", "4.2.1", "AppLovin", "US"}


def test_statements_may_not_carry_figures_but_may_carry_identifiers():
    assert statement_violations("安卓 4.2.1 与 AppLovin 组合是收入下降的主要来源", allowed_identifiers=ALLOWED) == []
    reasons = statement_violations("广告收入下降 67%", allowed_identifiers=ALLOWED)
    assert "statement_states_a_percentage" in reasons
    reasons = statement_violations("损失了 2500 USD", allowed_identifiers=ALLOWED)
    assert any(reason.startswith("unverifiable_figure") for reason in reasons)
    assert statement_violations("$1200 来自内购", allowed_identifiers=ALLOWED) != []
    # A version id that never appeared in the analysis is not a licence either.
    assert any(
        reason.endswith("4.3.0")
        for reason in statement_violations("4.3.0 版本下滑", allowed_identifiers={"4.2.1"})
    )
    assert statement_violations("短", allowed_identifiers=ALLOWED) == ["statement_too_short"]


def test_dedupe_key_ignores_whitespace_and_case_but_not_meaning():
    left = dedupe_key(metric_key="ads_revenue", kind="segment", statement="AppLovin   收入占比最高")
    right = dedupe_key(metric_key="ads_revenue", kind="segment", statement="applovin 收入占比最高")
    assert left == right
    other = dedupe_key(metric_key="ads_revenue", kind="caveat", statement="applovin 收入占比最高")
    assert left != other


def _task(**overrides) -> SimpleNamespace:
    state = {
        "metric_key": "ads_revenue",
        "dimensions": ["app_version"],
        "metric_definition": {
            "name": "广告收入",
            "unit": "USD",
            "aggregation_kind": "sum",
            "allowed_dimensions": ["country", "platform", "app_version", "ad_network"],
            "datasets": ["demo.ads_revenue_daily"],
            "notes": None,
        },
    }
    observation = {
        "contribution": {
            "dimension": ["app_version", "ad_network"],
            "groups": [
                {
                    "key": ["4.2.1", "AppLovin"],
                    "delta": "-670",
                    "net_change_share": "0.67",
                    "low_support": False,
                },
                {"key": ["4.3.0", "AdMob"], "delta": "120", "net_change_share": "-0.12", "low_support": False},
                {"key": [None, None], "delta": "0", "net_change_share": None, "low_support": True},
            ],
        }
    }
    base = {
        "id": uuid.uuid4(),
        "status": "COMPLETED",
        "question": "为什么收入下降",
        "context": {"dimensions": ["app_version"]},
        "state": {**state, "observations": observation},
        "final_report": {
            "limitations": ["变化在材料性阈值内，不构成需要归因的变化"],
            "claims": [{"id": "claim-1", "calculation_id": str(uuid.uuid4())}],
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_evidence_digest_ranks_segments_and_never_carries_amounts():
    digest = evidence_digest(_task())
    assert digest["metric"]["key"] == "ads_revenue"
    assert digest["breakdown_dimension"] == ["app_version", "ad_network"]
    assert digest["segment_values"]["app_version"] == ["4.2.1", "4.3.0"]
    # Ranked by the size of the change, and only a direction travels with it.
    assert [item["key"][0] for item in digest["notable_segments"]] == ["4.2.1", "4.3.0", None]
    assert digest["notable_segments"][0]["direction"] == "down"
    assert digest["notable_segments"][1]["direction"] == "up"
    assert "delta" not in digest["notable_segments"][0]
    identifiers = evidence_identifiers(digest)
    assert {"4.2.1", "4.3.0", "AppLovin", "AdMob", "app_version", "demo.ads_revenue_daily"} <= identifiers


def test_planner_brief_scopes_dimensions_to_the_allowlist(monkeypatch):
    row = SimpleNamespace(
        id=uuid.uuid4(),
        kind="segment",
        status="proposed",
        statement="AppLovin 是主要收入来源",
        scope_dimensions=["ad_network", "secret_column"],
    )
    monkeypatch.setattr(
        memory.memory_repo, "list_for_metric", lambda *args, **kwargs: [row]
    )
    brief, ids = planner_brief(
        None, metric_key="ads_revenue", allowed_dimensions=["country", "ad_network"]
    )
    assert ids == [row.id]
    assert brief[0]["dimensions"] == ["ad_network"]
    assert brief[0]["status"] == "proposed"


def _patched(monkeypatch, task: SimpleNamespace, provider, existing=()) -> dict:
    recorded: dict = {"upserts": [], "steps": [], "events": [], "existing": list(existing)}

    def _upsert(session, **kwargs):
        recorded["upserts"].append(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), **kwargs), len(recorded["upserts"]) == 1

    monkeypatch.setattr(memory.analyses_repo, "get_analysis_for_update", lambda *a, **k: task)
    monkeypatch.setattr(memory.memory_repo, "upsert", _upsert)
    monkeypatch.setattr(
        memory.memory_repo, "list_for_metric", lambda *a, **k: list(recorded["existing"])
    )
    monkeypatch.setattr(
        memory.analyses_repo,
        "add_step",
        lambda session, **kwargs: recorded["steps"].append(kwargs),
    )
    monkeypatch.setattr(
        memory.events_repo,
        "add_event",
        lambda session, **kwargs: recorded["events"].append(kwargs),
    )
    monkeypatch.setattr(
        memory, "resolve_provider", lambda settings: ProviderResolution(provider=provider)
    )
    return recorded


def _fake_provider(*payloads: dict) -> FakeLLMProvider:
    return FakeLLMProvider(list(payloads), model_id="fake-memory-v1")


def test_learning_stores_valid_statements_and_refuses_the_rest(monkeypatch):
    task = _task()
    recorded = _patched(
        monkeypatch,
        task,
        _fake_provider(
            {
                "memories": [
                    {"kind": "segment", "statement": "AppLovin 与 4.2.1 的组合是主要下滑来源", "dimensions": ["ad_network"]},
                    {"kind": "caveat", "statement": "净下降 67% 集中在单个分组", "dimensions": ["app_version"]},
                ]
            }
        ),
    )
    outcome = remember_completed(None, analysis_id=task.id, settings=Settings(database_url="postgresql+psycopg://x/y"))

    assert outcome["status"] == "extracted"
    assert len(recorded["upserts"]) == 1
    stored = recorded["upserts"][0]
    assert stored["metric_key"] == "ads_revenue"
    assert stored["scope_dimensions"] == ["ad_network"]
    assert stored["scope_datasets"] == ["demo.ads_revenue_daily"]
    assert stored["analysis_id"] == task.id
    assert outcome["refused"] and "statement_states_a_percentage" in outcome["refused"][0]["reason"]
    assert task.state["memory_learned_ids"] == outcome["created_ids"]
    assert recorded["steps"][0]["tool_name"] == "business_memory"
    assert recorded["events"][0]["event_type"] == "analysis.memory.extracted"


def test_dimensions_outside_the_allowlist_are_dropped_from_the_scope(monkeypatch):
    task = _task()
    recorded = _patched(
        monkeypatch,
        task,
        _fake_provider(
            {
                "memories": [
                    {"kind": "definition", "statement": "AppLovin 的收入按净额确认", "dimensions": ["secret_column", "country"]}
                ]
            }
        ),
    )
    remember_completed(None, analysis_id=task.id, settings=Settings(database_url="postgresql+psycopg://x/y"))
    assert recorded["upserts"][0]["scope_dimensions"] == ["country"]


def test_method_restatements_are_refused_as_ungrounded(monkeypatch):
    """The kernel's own caveats are already in the report; memory must add business facts."""
    task = _task()
    recorded = _patched(
        monkeypatch,
        task,
        _fake_provider(
            {
                "memories": [
                    {"kind": "caveat", "statement": "分解结果只说明相关性，不代表因果关系", "dimensions": []},
                    {"kind": "segment", "statement": "AppLovin 是主要下滑来源", "dimensions": ["ad_network"]},
                ]
            }
        ),
    )
    outcome = remember_completed(None, analysis_id=task.id, settings=Settings(database_url="postgresql+psycopg://x/y"))
    assert len(recorded["upserts"]) == 1
    assert outcome["refused"][0]["reason"] == "ungrounded_statement"


def test_paraphrases_of_a_known_statement_are_refused(monkeypatch):
    existing = SimpleNamespace(
        statement="ads_revenue movement concentrates on AppLovin across country, platform and ad network groups"
    )
    task = _task()
    recorded = _patched(
        monkeypatch,
        task,
        _fake_provider(
            {
                "memories": [
                    {
                        "kind": "segment",
                        "statement": "Ads revenue movement across country, platform and ad network concentrates on AppLovin",
                        "dimensions": ["country"],
                    }
                ]
            }
        ),
        existing=[existing],
    )
    outcome = remember_completed(None, analysis_id=task.id, settings=Settings(database_url="postgresql+psycopg://x/y"))
    assert recorded["upserts"] == []
    assert outcome["refused"][0]["reason"] == "near_duplicate"


def test_learning_degrades_loudly_without_a_model(monkeypatch):
    task = _task()
    recorded = _patched(monkeypatch, task, None)
    monkeypatch.setattr(
        memory,
        "resolve_provider",
        lambda settings: ProviderResolution(provider=None, warning="no key file"),
    )
    outcome = remember_completed(None, analysis_id=task.id, settings=Settings(database_url="postgresql+psycopg://x/y"))
    assert outcome["status"] == "skipped"
    assert recorded["upserts"] == []
    assert recorded["events"][0]["event_type"] == "analysis.memory.skipped"
    assert task.state["memory_extraction"]["reason"] == "no key file"


def test_learning_is_idempotent_and_skips_unfinished_analyses(monkeypatch):
    done = _task()
    done.state = {**done.state, "memory_extraction": {"status": "extracted"}}
    recorded = _patched(monkeypatch, done, _fake_provider({"memories": []}))
    outcome = remember_completed(None, analysis_id=done.id, settings=Settings(database_url="postgresql+psycopg://x/y"))
    assert outcome == {"status": "skipped", "reason": "already_extracted"}
    assert recorded["upserts"] == [] and recorded["events"] == []

    running = _task(status="OBSERVING")
    _patched(monkeypatch, running, _fake_provider({"memories": []}))
    outcome = remember_completed(None, analysis_id=running.id, settings=Settings(database_url="postgresql+psycopg://x/y"))
    assert outcome == {"status": "skipped", "reason": "not_terminal"}


def test_catch_up_learns_recent_analyses_that_never_got_extracted(monkeypatch):
    fresh = _task()
    already = _task()
    already.state = {**already.state, "memory_extraction": {"status": "extracted"}}
    monkeypatch.setattr(
        memory.analyses_repo, "list_recent_terminal", lambda *a, **k: [already, fresh]
    )
    seen: list[uuid.UUID] = []

    def _remember(session, *, analysis_id, settings):
        seen.append(analysis_id)
        return {"status": "extracted"}

    monkeypatch.setattr(memory, "remember_completed", _remember)
    outcome = memory.catch_up(None, settings=Settings(database_url="postgresql+psycopg://x/y"))
    assert seen == [fresh.id]
    assert outcome["processed"] == [str(fresh.id)]


def test_learning_logs_under_an_info_logger(monkeypatch, caplog):
    """Regression: the success path must not pass reserved names to ``extra``.

    ``logging`` reserves ``created``/``message``/``name`` on a LogRecord, so a
    counter named ``created`` raises KeyError while building the record - but
    only when the logger is actually enabled for INFO, which is the container's
    level and not pytest's default. This test turns INFO on to keep that failure
    inside the suite.
    """
    import logging

    caplog.set_level(logging.INFO, logger="app.agent.memory")
    task = _task()
    _patched(
        monkeypatch,
        task,
        _fake_provider({"memories": [{"kind": "segment", "statement": "AppLovin 占比最高", "dimensions": []}]}),
    )
    outcome = remember_completed(
        None, analysis_id=task.id, settings=Settings(database_url="postgresql+psycopg://x/y")
    )
    assert outcome["status"] == "extracted"
    assert any(record.message == "business memory updated" for record in caplog.records)


def test_unexpected_failures_are_recorded_once_and_not_retried(monkeypatch, caplog):
    """A defect must degrade to one visible marker, not a model-call retry storm."""
    task = _task()

    def _explode(*args, **kwargs):
        raise RuntimeError("boom")

    recorded = _patched(
        monkeypatch,
        task,
        _fake_provider({"memories": [{"kind": "segment", "statement": "AppLovin 占比最高", "dimensions": []}]}),
    )
    monkeypatch.setattr(memory.memory_repo, "upsert", _explode)
    outcome = remember_completed(
        None, analysis_id=task.id, settings=Settings(database_url="postgresql+psycopg://x/y")
    )
    assert outcome["status"] == "skipped"
    marker = task.state["memory_extraction"]
    assert marker["retryable"] is False and marker["reason"].startswith("internal_error")
    assert recorded["events"][0]["event_type"] == "analysis.memory.skipped"
    assert memory.catch_up.__doc__ is not None


def test_provider_errors_are_retryable_but_bounded(monkeypatch):
    class _Failing:
        def generate_structured(self, **kwargs):
            raise RuntimeError("429 too many requests")

    task = _task()
    _patched(monkeypatch, task, _Failing())
    settings = Settings(database_url="postgresql+psycopg://x/y")
    first = remember_completed(None, analysis_id=task.id, settings=settings)
    assert first["status"] == "skipped" and first["retryable"] is True
    assert task.state["memory_extraction"]["attempts"] == 1
    # Bounded: after MAX_ATTEMPTS the analysis is left alone.
    task.state = {**task.state, "memory_extraction": {**task.state["memory_extraction"], "attempts": memory.MAX_ATTEMPTS}}
    assert remember_completed(None, analysis_id=task.id, settings=settings) == {
        "status": "skipped",
        "reason": "attempts_exhausted",
    }


def test_catch_up_retries_only_retryable_attempts(monkeypatch):
    retryable = _task()
    retryable.state = {
        **retryable.state,
        "memory_extraction": {"status": "failed", "reason": "provider_error", "retryable": True, "attempts": 1},
    }
    settled = _task()
    settled.state = {
        **settled.state,
        "memory_extraction": {"status": "skipped", "reason": "no key file", "retryable": False, "attempts": 1},
    }
    monkeypatch.setattr(memory.analyses_repo, "list_recent_terminal", lambda *a, **k: [settled, retryable])
    seen: list[uuid.UUID] = []
    monkeypatch.setattr(
        memory,
        "remember_completed",
        lambda session, *, analysis_id, settings: (seen.append(analysis_id), {"status": "extracted"})[1],
    )
    memory.catch_up(None, settings=Settings(database_url="postgresql+psycopg://x/y"))
    assert seen == [retryable.id]


def test_extraction_schema_is_closed():
    with pytest.raises(Exception):
        MemoryExtraction.model_validate({"memories": [{"kind": "opinion", "statement": "自由文本"}]})
    with pytest.raises(Exception):
        MemoryExtraction.model_validate({"memories": [], "extra": 1})


def test_statement_normalisation_collapses_whitespace():
    assert normalize_statement("  AppLovin \n 占比  最高 ") == "AppLovin 占比 最高"


def test_curation_audit_records_the_previous_status(monkeypatch):
    """The audit must carry the status the row had before the decision.

    `memory_repo.set_status` mutates the row in place, so a route that read
    `row.status` while building the audit details recorded the *new* status as
    the previous one (observed live 2026-09-28: `memory.rejected` with
    `previous: "rejected"`).
    """
    from app.api.routes import memory as memory_routes
    from app.models.orm import BusinessMemory

    row = BusinessMemory(
        id=uuid.uuid4(), metric_key="ecpm", kind="segment",
        statement="US android is a notable up segment for eCPM.",
        scope_datasets=[], scope_dimensions=["country", "platform"],
        status="proposed", source="model", seen_count=1, reuse_count=0,
        dedupe_key="x",
    )
    captured: dict = {}

    class Session:
        def flush(self) -> None:
            pass

    monkeypatch.setattr(memory_routes.memory_repo, "get", lambda session, memory_id: row)
    monkeypatch.setattr(
        memory_routes.audit_repo, "add_audit",
        lambda session, **kwargs: captured.update(kwargs),
    )

    item = memory_routes._curate(
        Session(),
        SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4())),
        row.id,
        status="rejected",
        trace_id="test-trace",
    )

    assert item.status == "rejected"
    assert captured["action"] == "memory.rejected"
    assert captured["details"] == {"metric_key": "ecpm", "previous": "proposed"}
