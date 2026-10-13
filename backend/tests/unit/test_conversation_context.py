"""Conversation scope merging and the completeness bridge (design 6.1-6.2)."""

from __future__ import annotations

from app.agent.conversation.context import (
    COMPLETENESS_COMPLETE,
    COMPLETENESS_INCOMPLETE,
    COMPLETENESS_UNKNOWN,
    bridge_data_complete,
    completeness_from_bool,
    describe_scope,
    merge_scope_patch,
    missing_required_fields,
    scope_to_analysis_context,
)

FULL_PATCH = {
    "metric_key": "ads_revenue",
    "baseline_start": "2026-09-10",
    "baseline_end": "2026-09-11",
    "current_start": "2026-09-11",
    "current_end": "2026-09-12",
    "dimensions": ["country", "platform"],
    "filters": {"platform": "android"},
    "data_complete_state": COMPLETENESS_COMPLETE,
}


def test_patch_sets_fields_and_records_their_origin():
    merged = merge_scope_patch({}, FULL_PATCH, origin="ui")
    assert merged["metric_key"] == "ads_revenue"
    assert merged["field_origins"]["metric_key"] == "ui"
    assert missing_required_fields(merged) == []


def test_absent_fields_are_inherited_not_cleared():
    base = merge_scope_patch({}, FULL_PATCH, origin="ui")
    merged = merge_scope_patch(base, {"filters": {"platform": "ios"}}, origin="model")
    assert merged["metric_key"] == "ads_revenue"  # inherited
    assert merged["filters"] == {"platform": "ios"}  # replaced
    assert merged["field_origins"]["filters"] == "model"
    assert merged["field_origins"]["metric_key"] == "ui"


def test_clear_wins_over_a_value_in_the_same_patch():
    base = merge_scope_patch({}, FULL_PATCH, origin="ui")
    merged = merge_scope_patch(
        base, {"clear": ["filters"], "filters": {"platform": "ios"}}, origin="ui"
    )
    assert "filters" not in merged
    assert "filters" not in merged["field_origins"]


def test_missing_fields_are_reported_for_clarification():
    assert missing_required_fields({}) == [
        "metric_key",
        "baseline_start",
        "baseline_end",
        "current_start",
        "current_end",
    ]
    partial = merge_scope_patch({}, {"metric_key": "ads_revenue"})
    assert missing_required_fields(partial) == [
        "baseline_start",
        "baseline_end",
        "current_start",
        "current_end",
    ]


def test_completeness_is_three_state_and_only_complete_bridges_to_true():
    assert completeness_from_bool(None, source="caller") == COMPLETENESS_UNKNOWN
    assert completeness_from_bool(True, source="caller") == COMPLETENESS_COMPLETE
    assert completeness_from_bool(False, source="caller") == COMPLETENESS_INCOMPLETE
    assert bridge_data_complete({"data_complete_state": COMPLETENESS_COMPLETE}) is True
    # A user's claim of completeness is not verification: unknown stays false.
    assert bridge_data_complete({"data_complete_state": COMPLETENESS_UNKNOWN}) is False
    assert bridge_data_complete({"data_complete_state": COMPLETENESS_INCOMPLETE}) is False
    assert bridge_data_complete({}) is False


def test_scope_to_analysis_context_matches_the_analysis_dto():
    merged = merge_scope_patch({}, FULL_PATCH, origin="ui")
    payload = scope_to_analysis_context(merged)
    assert payload == {
        "metric_key": "ads_revenue",
        "baseline_start": "2026-09-10",
        "baseline_end": "2026-09-11",
        "current_start": "2026-09-11",
        "current_end": "2026-09-12",
        "dimensions": ["country", "platform"],
        "filters": {"platform": "android"},
        "data_complete": True,
    }
    # The dimension cap the analysis DTO enforces is applied here too.
    merged["dimensions"] = ["country", "platform", "app_version", "ad_network"]
    assert scope_to_analysis_context(merged)["dimensions"] == [
        "country",
        "platform",
        "app_version",
    ]


def test_describe_scope_is_number_free_and_shows_origins():
    merged = merge_scope_patch({}, FULL_PATCH, origin="ui")
    described = describe_scope(merged)
    assert described["metric_key"] == "ads_revenue"
    assert described["baseline"] == ["2026-09-10", "2026-09-11"]
    assert described["origins"]["metric_key"] == "ui"
    assert described["data_complete_state"] == COMPLETENESS_COMPLETE
