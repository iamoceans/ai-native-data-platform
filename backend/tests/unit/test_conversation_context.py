"""Conversation scope merging and the completeness bridge (design 6.1-6.2)."""

from __future__ import annotations

from app.agent.conversation.context import (
    COMPLETENESS_COMPLETE,
    COMPLETENESS_INCOMPLETE,
    COMPLETENESS_UNKNOWN,
    analysis_completeness,
    bridge_data_complete,
    completeness_from_bool,
    describe_scope,
    invalid_period_fields,
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


def test_an_empty_period_blocks_the_analysis_instead_of_reporting_zero():
    """`end == start` is an empty window, not a quiet "没有变化".

    A model reading the dates as inclusive produced exactly that, and the empty
    query was published as `变化 0` - a wrong answer about the wrong period.
    """
    merged = merge_scope_patch(
        {},
        {
            "metric_key": "ads_revenue",
            "baseline_start": "2026-09-10",
            "baseline_end": "2026-09-10",
            "current_start": "2026-09-11",
            "current_end": "2026-09-11",
        },
    )
    assert invalid_period_fields(merged) == [
        "baseline_start",
        "baseline_end",
        "current_start",
        "current_end",
    ]
    assert missing_required_fields(merged) == [
        "baseline_start",
        "baseline_end",
        "current_start",
        "current_end",
    ]
    # A single day written the platform's way is valid: [D, D+1).
    ok = merge_scope_patch(
        {},
        {
            "metric_key": "ads_revenue",
            "baseline_start": "2026-09-10",
            "baseline_end": "2026-09-11",
            "current_start": "2026-09-11",
            "current_end": "2026-09-12",
        },
    )
    assert invalid_period_fields(ok) == []
    assert missing_required_fields(ok) == []
    # An inverted range is rejected too, and an unparseable date is not silently
    # treated as ordered.
    assert invalid_period_fields({"current_start": "2026-09-12", "current_end": "2026-09-11"}) == [
        "current_start",
        "current_end",
    ]
    assert invalid_period_fields({"baseline_start": "not-a-date", "baseline_end": "2026-09-11"}) == [
        "baseline_start",
        "baseline_end",
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
        # The legacy boolean cannot say "nobody verified this"; the tri-state
        # rides along so the report can distinguish unknown from incomplete.
        "completeness": COMPLETENESS_COMPLETE,
    }
    # The dimension cap the analysis DTO enforces is applied here too.
    merged["dimensions"] = ["country", "platform", "app_version", "ad_network"]
    assert scope_to_analysis_context(merged)["dimensions"] == [
        "country",
        "platform",
        "app_version",
    ]


def test_analysis_completeness_keeps_unknown_apart_from_incomplete():
    assert analysis_completeness({"data_complete_state": COMPLETENESS_UNKNOWN}) == (
        COMPLETENESS_UNKNOWN
    )
    assert analysis_completeness({"data_complete_state": COMPLETENESS_INCOMPLETE}) == (
        COMPLETENESS_INCOMPLETE
    )
    # A context that carries only the legacy boolean falls back to it.
    assert analysis_completeness({"data_complete": False}) == COMPLETENESS_INCOMPLETE
    assert analysis_completeness({"data_complete": True}) == COMPLETENESS_COMPLETE
    assert analysis_completeness({}) == COMPLETENESS_UNKNOWN


def test_describe_scope_is_number_free_and_shows_origins():
    merged = merge_scope_patch({}, FULL_PATCH, origin="ui")
    described = describe_scope(merged)
    assert described["metric_key"] == "ads_revenue"
    assert described["baseline"] == ["2026-09-10", "2026-09-11"]
    assert described["origins"]["metric_key"] == "ui"
    assert described["data_complete_state"] == COMPLETENESS_COMPLETE
