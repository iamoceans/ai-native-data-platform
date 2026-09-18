"""Deterministic analysis kernel: canonical_67, additivity, drivers (A06-A08)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.analysis.compare import compare_totals
from app.analysis.contribution import decompose_contribution
from app.analysis.drivers import decompose_revenue
from app.analysis.join import join_results
from app.analysis.relations import JoinRelation, load_relations
from app.analysis.types import AnalysisError

ROOT = Path(__file__).resolve().parents[2]
METADATA_DIR = ROOT.parent / "metadata"


# ---------------------------------------------------------------------------
# canonical_67 (spec 16.3 example, exactly)
# ---------------------------------------------------------------------------
CANONICAL_BASELINE = [
    {"country": "US", "ad_network": "AppLovin", "revenue_usd": "2500", "impressions": "100000"},
    {"country": "DE", "ad_network": "AdMob", "revenue_usd": "4000", "impressions": "200000"},
    {"country": "JP", "ad_network": "Unity", "revenue_usd": "3500", "impressions": "200000"},
]
CANONICAL_CURRENT = [
    {"country": "US", "ad_network": "AppLovin", "revenue_usd": "1830", "impressions": "100000"},
    {"country": "DE", "ad_network": "AdMob", "revenue_usd": "3700", "impressions": "200000"},
    {"country": "JP", "ad_network": "Unity", "revenue_usd": "3470", "impressions": "200000"},
]


def test_canonical_67_totals():
    result = compare_totals(Decimal("10000"), Decimal("9000"))
    assert result.delta == Decimal("-1000")
    assert result.change_pct == Decimal("-0.1")
    assert result.baseline_zero is False


def test_canonical_67_contributions():
    result = decompose_contribution(
        CANONICAL_BASELINE,
        CANONICAL_CURRENT,
        dimension=["country", "ad_network"],
        value_column="revenue_usd",
        support_column="impressions",
        min_support=1000,
    )
    assert result.delta_total == Decimal("-1000")
    target = next(group for group in result.groups if group.key == ("US", "AppLovin"))
    assert target.delta == Decimal("-670")
    assert target.net_change_share == Decimal("0.67")
    assert target.contribution_pp == Decimal("-6.7")
    assert target.gross_decline_share == Decimal("0.67")
    assert target.low_support is False

    # The other exclusive groups net to -330 and every group is a partition.
    others = [group for group in result.groups if group.key != ("US", "AppLovin")]
    assert sum(group.delta for group in others) == Decimal("-330")
    assert result.checks["sum_matches_parent"] is True
    assert result.residual == Decimal("0")


def test_canonical_67_driver_decomposition():
    result = decompose_revenue(
        impressions_baseline="100000",
        impressions_current="100000",
        revenue_baseline="2500",
        revenue_current="1830",
    )
    assert result.ecpm_baseline == Decimal("25")
    assert result.ecpm_current == Decimal("18.3")
    assert result.impression_effect == Decimal("0")
    assert result.ecpm_effect == Decimal("-670")
    assert result.delta == Decimal("-670")
    assert result.residual == Decimal("0")
    assert result.status == "ok"


def test_driver_effects_sum_to_delta_for_random_values():
    cases = [
        ("100000", "120000", "2500", "3410.4"),
        ("50000", "40000", "1000", "999.99"),
        ("12345", "54321", "999.123456", "1500.654321"),
        ("1", "1000000", "0.001", "1000"),
    ]
    for i0, i1, r0, r1 in cases:
        result = decompose_revenue(
            impressions_baseline=i0,
            impressions_current=i1,
            revenue_baseline=r0,
            revenue_current=r1,
        )
        assert abs(result.impression_effect + result.ecpm_effect - result.delta) <= Decimal(
            "0.000001"
        ), (i0, i1, r0, r1)


def test_driver_undefined_when_impressions_zero():
    result = decompose_revenue(
        impressions_baseline="0",
        impressions_current="1000",
        revenue_baseline="0",
        revenue_current="10",
    )
    assert result.status == "undefined_impressions"
    assert result.impression_effect is None
    assert result.ecpm_effect is None
    assert result.delta == Decimal("10")


# ---------------------------------------------------------------------------
# contribution edge cases
# ---------------------------------------------------------------------------
def test_contribution_null_bucket_and_zero_fill():
    baseline = [
        {"dim": "A", "value": "100"},
        {"dim": None, "value": "50"},
        {"dim": "B", "value": "10"},
    ]
    current = [
        {"dim": "A", "value": "80"},
        {"dim": "C", "value": "30"},
    ]
    result = decompose_contribution(
        baseline,
        current,
        dimension=["dim"],
        value_column="value",
        merge_below_support=False,
    )
    deltas = {}
    for group in result.groups:
        deltas[group.key] = group.delta
    assert deltas[("A",)] == Decimal("-20")
    assert deltas[("B",)] == Decimal("-10")  # disappeared -> zero-filled
    assert deltas[("C",)] == Decimal("30")  # appeared -> zero baseline
    assert deltas[(None,)] == Decimal("-50")  # NULL bucket kept separate
    assert result.delta_total == Decimal("-50")
    assert sum(group.delta for group in result.groups) == result.delta_total
    assert any(group.label == "NULL" for group in result.groups)


def test_contribution_share_none_when_total_unchanged():
    rows = [{"dim": "A", "value": "10"}, {"dim": "B", "value": "10"}]
    result = decompose_contribution(rows, rows, dimension=["dim"], value_column="value")
    assert result.delta_total == 0
    assert all(group.net_change_share is None for group in result.groups)


def test_contribution_small_groups_merge_into_other():
    baseline = [
        {"dim": "big", "value": "1000", "impressions": "100000"},
        {"dim": "tiny1", "value": "10", "impressions": "50"},
        {"dim": "tiny2", "value": "20", "impressions": "60"},
    ]
    current = [
        {"dim": "big", "value": "900", "impressions": "100000"},
        {"dim": "tiny1", "value": "5", "impressions": "40"},
        {"dim": "tiny2", "value": "25", "impressions": "70"},
    ]
    result = decompose_contribution(
        baseline,
        current,
        dimension=["dim"],
        value_column="value",
        support_column="impressions",
        min_support=1000,
        merge_below_support=True,
    )
    keys = {group.key for group in result.groups}
    assert ("tiny1",) not in keys and ("tiny2",) not in keys
    other = next(group for group in result.groups if group.label == "OTHER")
    assert other.delta == Decimal("-5") + Decimal("5")
    assert result.delta_total == Decimal("-100")
    assert sum(group.delta for group in result.groups) == result.delta_total


def test_contribution_parent_mismatch_is_refused():
    rows = [{"dim": "A", "value": "100"}]
    with pytest.raises(AnalysisError) as excinfo:
        decompose_contribution(
            rows, rows, dimension=["dim"], value_column="value", parent_delta=-999
        )
    assert excinfo.value.code == "GROUP_SUM_MISMATCH"


def test_contribution_requires_dimension():
    with pytest.raises(AnalysisError):
        decompose_contribution([], [], dimension=[], value_column="value")


# ---------------------------------------------------------------------------
# relations registry
# ---------------------------------------------------------------------------
def test_relations_registry_loads_from_metadata():
    relations = load_relations(METADATA_DIR)
    assert "campaign_cohort_to_config" in relations
    relation = relations["campaign_cohort_to_config"]
    assert relation.as_of is True
    assert relation.relationship == "many_to_one"
    assert relation.left_keys == ("campaign_id",)


def test_relations_registry_rejects_unknown(workdir):
    with pytest.raises(AnalysisError) as excinfo:
        from app.analysis.relations import get_relation

        get_relation(workdir, "does-not-exist")
    assert excinfo.value.code == "RELATION_UNKNOWN"


# ---------------------------------------------------------------------------
# join
# ---------------------------------------------------------------------------
def _campaign_relation() -> JoinRelation:
    return JoinRelation(
        relation_id="campaign_cohort_to_config",
        left_dataset="demo.campaign_cohort_daily",
        right_dataset="public.campaign_config",
        left_keys=("campaign_id",),
        right_keys=("campaign_id",),
        relationship="many_to_one",
        left_time_column="cohort_date",
        right_valid_from="valid_from",
        right_valid_to="valid_to",
        amount_columns=("revenue_usd",),
    )


def test_join_as_of_matching_and_amount_preservation():
    left = [
        {"campaign_id": "c1", "cohort_date": "2026-09-01", "revenue_usd": "100"},
        {"campaign_id": "c1", "cohort_date": "2026-09-10", "revenue_usd": "200"},
        {"campaign_id": "missing", "cohort_date": "2026-09-05", "revenue_usd": "50"},
    ]
    right = [
        {"campaign_id": "c1", "valid_from": "2026-08-01", "valid_to": "2026-09-05", "channel": "old"},
        {"campaign_id": "c1", "valid_from": "2026-09-05", "valid_to": None, "channel": "new"},
    ]
    result = join_results(left, right, _campaign_relation())
    assert result.stats["output_rows"] == 3
    assert result.stats["matched"] == 2
    assert result.stats["unmatched"] == 1
    by_date = {row["cohort_date"]: row for row in result.rows}
    assert by_date["2026-09-01"]["channel"] == "old"
    assert by_date["2026-09-10"]["channel"] == "new"
    assert by_date["2026-09-05"]["channel"] is None  # NULL key never matches
    assert result.checks["amounts_preserved"] is True
    assert result.stats["unmatched_rate"].quantize(Decimal("0.000001")) == Decimal("0.333333")


def test_join_duplicate_keys_are_refused():
    relation = JoinRelation(
        relation_id="dup",
        left_dataset="l",
        right_dataset="r",
        left_keys=("id",),
        right_keys=("id",),
        relationship="many_to_one",
        left_time_column=None,
        right_valid_from=None,
        right_valid_to=None,
        amount_columns=(),
    )
    left = [{"id": "1", "value": "10"}]
    right = [{"id": "1", "label": "a"}, {"id": "1", "label": "b"}]
    with pytest.raises(AnalysisError) as excinfo:
        join_results(left, right, relation)
    assert excinfo.value.code == "JOIN_CARDINALITY_VIOLATION"


def test_join_composite_keys():
    relation = JoinRelation(
        relation_id="release",
        left_dataset="demo.ads_revenue_daily",
        right_dataset="ainative_source.app_release_config",
        left_keys=("platform", "app_version"),
        right_keys=("platform", "app_version"),
        relationship="many_to_one",
        left_time_column=None,
        right_valid_from=None,
        right_valid_to=None,
        amount_columns=("revenue_usd",),
    )
    left = [
        {"platform": "android", "app_version": "4.2.1", "revenue_usd": "100"},
        {"platform": "ios", "app_version": "9.9.9", "revenue_usd": "20"},
    ]
    right = [
        {"platform": "android", "app_version": "4.2.1", "release_at": "2026-09-10", "rollout_note": "ga"},
    ]
    result = join_results(left, right, relation)
    assert result.stats["matched"] == 1
    assert result.rows[0]["release_at"] == "2026-09-10"
    assert result.rows[1]["release_at"] is None
    assert result.checks["amounts_preserved"] is True


def test_join_federation_limit():
    relation = JoinRelation(
        relation_id="limit",
        left_dataset="l",
        right_dataset="r",
        left_keys=("id",),
        right_keys=("id",),
        relationship="many_to_one",
        left_time_column=None,
        right_valid_from=None,
        right_valid_to=None,
        amount_columns=(),
    )
    left = [{"id": str(i)} for i in range(11)]
    with pytest.raises(AnalysisError) as excinfo:
        join_results(left, [], relation, max_rows_per_side=10)
    assert excinfo.value.code == "FEDERATION_LIMIT"
