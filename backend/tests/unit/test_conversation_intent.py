"""Conversation intent: the closed decision and what the server accepts from it."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.agent.conversation import intent as intent_layer
from app.agent.conversation.intent import (
    ConversationDecision,
    IntentUnavailable,
    estimate_input_tokens,
    parse_decision,
    resolve_relative_periods,
    validate_patch,
    validate_prose,
)

METRICS = {"ads_revenue": {"country", "platform", "ad_network"}, "dau": {"country", "platform"}}


def decision(**payload) -> ConversationDecision:
    return ConversationDecision.model_validate(payload)


def test_decision_schema_is_closed():
    with pytest.raises(Exception):
        decision(intent="run_sql")
    with pytest.raises(Exception):
        decision(intent="analyze", sql="DROP TABLE users")
    with pytest.raises(Exception):
        decision(intent="analyze", tools=["shell"])


def test_patch_keeps_only_what_the_platform_has():
    patch, reasons = validate_patch(
        decision(
            intent="analyze",
            scope_patch={
                "metric_key": "not_a_metric",
                "baseline_start": "2026-09-10",
                "baseline_end": "not-a-date",
                "dimensions": ["country", "warehouse"],
            },
        ),
        metrics=METRICS,
    )
    assert "metric_key" not in patch
    assert patch["baseline_start"] == "2026-09-10"
    assert "baseline_end" not in patch
    # Dimensions are only filtered when the metric is known and allowed.
    assert any(reason.startswith("unknown_metric") for reason in reasons)
    assert any(reason.startswith("invalid_date") for reason in reasons)
    assert "dimensions_without_metric" in reasons


def test_dimensions_are_filtered_against_the_metric_allowlist():
    patch, reasons = validate_patch(
        decision(
            intent="analyze",
            scope_patch={"metric_key": "dau", "dimensions": ["country", "ad_network"]},
        ),
        metrics=METRICS,
    )
    assert patch["dimensions"] == ["country"]
    assert "dimensions_not_allowed:ad_network" in reasons


def test_clear_and_filters_survive_validation():
    patch, reasons = validate_patch(
        decision(
            intent="analyze",
            scope_patch={"clear": ["filters"], "filters": {"platform": "android"}},
        ),
        metrics=METRICS,
    )
    assert patch["clear"] == ["filters"]
    assert patch["filters"] == {"platform": "android"}
    assert reasons == []


def test_relative_period_is_frozen_to_absolute_dates():
    item = decision(
        intent="analyze",
        scope_patch={
            "metric_key": "ads_revenue",
            "comparison": "previous_period",
            "current_start": "2026-09-08",
            "current_end": "2026-09-15",
        },
    )
    notes = resolve_relative_periods(item, now=date(2026, 9, 15))
    assert item.scope_patch.baseline_start == "2026-09-01"
    assert item.scope_patch.baseline_end == "2026-09-08"
    assert notes and notes[0].startswith("previous_period:")


def test_explicit_periods_are_never_overwritten_by_the_relation():
    item = decision(
        intent="analyze",
        scope_patch={
            "comparison": "previous_period",
            "baseline_start": "2026-08-01",
            "baseline_end": "2026-08-08",
            "current_start": "2026-09-08",
            "current_end": "2026-09-15",
        },
    )
    assert resolve_relative_periods(item, now=date(2026, 9, 15)) == []
    assert item.scope_patch.baseline_start == "2026-08-01"


def test_prose_may_not_introduce_a_figure():
    item = decision(
        intent="explain",
        reply="收入下降了 12%，主要来自 US。",
        references=[{"analysis_id": str(uuid.uuid4()), "claim_id": "claim-1"}],
    )
    reasons = validate_prose(item, allowed_identifiers={"US", "claim-1"})
    assert reasons and reasons[0].startswith("reply:")

    clean = decision(intent="explain", reply="收入下降主要来自 US。")
    assert validate_prose(clean, allowed_identifiers={"US"}) == []


def test_parse_decision_rejects_an_invalid_payload():
    with pytest.raises(IntentUnavailable):
        parse_decision({"intent": "analyze", "unexpected": 1})


def test_input_estimate_counts_every_message():
    messages = [{"role": "system", "content": "a" * 400}, {"role": "user", "content": "b" * 400}]
    assert estimate_input_tokens(messages) == 202
