"""The Ask surface must answer natural-language questions, not go silent.

Two defects motivated these tests (2026-10-16):

* a conversational turn defaults to completeness `unknown`, and the report used
  to drop *every* claim in that state, so a normal question produced an empty
  answer bubble;
* "哪些指标 / 有哪些数据" was answered with a fixed paragraph that named nothing,
  even though the metric registry is right there.
"""

from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

from app.agent.conversation import orchestrator
from app.agent.report import build_comparison_report
from app.constants import CompletenessState


def _session(definitions: list[dict]) -> Mock:
    rows = [NS(metric_key=item["metric_key"], definition=item) for item in definitions]

    class _Rows:
        def scalars(self):
            return iter(rows)

    session = Mock()
    session.execute.return_value = _Rows()
    return session


DEFINITIONS = [
    {
        "metric_key": "ads_revenue",
        "version": 1,
        "name": "广告收入",
        "dataset": "demo.ads_revenue_daily",
        "formula": "sum(impressions * ecpm) / 1000",
        "unit": "USD",
        "allowed_dimensions": ["country", "platform", "ad_network"],
        "aggregation_kind": "sum",
    },
    {
        "metric_key": "dau",
        "version": 2,
        "name": "日活跃用户数",
        "dataset": "demo.user_daily",
        "formula": "count(distinct user_id)",
        "unit": "users",
        "allowed_dimensions": ["country", "platform"],
        "aggregation_kind": "non_additive",
    },
    # A malformed definition must not break the answer.
    {"metric_key": "broken", "version": 1, "name": "坏指标"},
]


def test_metric_catalog_offers_only_valid_definitions_in_key_order():
    catalog = orchestrator._metric_catalog(_session(list(reversed(DEFINITIONS))))
    assert [item["metric_key"] for item in catalog] == ["ads_revenue", "dau"]
    assert catalog[0]["name"] == "广告收入"
    assert catalog[0]["dimensions"] == ["ad_network", "country", "platform"]
    assert catalog[0]["datasets"] == ["demo.ads_revenue_daily"]


def test_metric_table_is_the_validation_view_of_the_same_catalog():
    table = orchestrator._metric_table(_session(DEFINITIONS))
    assert table == {
        "ads_revenue": {"country", "platform", "ad_network"},
        "dau": {"country", "platform"},
    }


def test_help_reply_carries_the_registry_and_a_pasteable_example():
    catalog = orchestrator._metric_catalog(_session(DEFINITIONS))
    content = orchestrator.help_reply(None, metrics=catalog)
    assert content["kind"] == "help"
    assert [item["metric_key"] for item in content["metrics"]] == ["ads_revenue", "dau"]
    assert "ads_revenue" in content["text"]
    assert "上周相比前一周" in content["text"]


def test_help_reply_keeps_model_prose_and_still_lists_metrics():
    content = orchestrator.help_reply("我还能解释已有结果。", metrics=[{"metric_key": "dau"}])
    assert content["text"] == "我还能解释已有结果。"
    assert content["metrics"] == [{"metric_key": "dau"}]


def test_unsupported_reply_points_at_what_exists():
    content = orchestrator.unsupported_reply("没有这个指标。", metrics=[{"metric_key": "dau"}])
    assert content["kind"] == "unsupported"
    assert content["text"] == "没有这个指标。"
    assert content["metrics"] == [{"metric_key": "dau"}]


def test_unverified_window_reports_the_change_and_names_the_gap():
    report = build_comparison_report(
        analysis_id="a1",
        question="ads_revenue 上周为什么下降？",
        comparison={},
        calculation_id="c1",
        calculation={"baseline": "5000", "current": "4000", "change": "-1000", "change_pct": "-0.2"},
        query_ids=["q1"],
        dataset_ids=["d1"],
        metric_key="ads_revenue",
        metric_version=1,
        evidence_available_until=None,
        completeness=CompletenessState.UNKNOWN.value,
    )
    assert report["status"] == "PARTIAL"
    assert len(report["claims"]) == 1
    assert "广告" not in report["claims"][0]["text"]  # keys, never prose
    assert "-1000" in report["claims"][0]["text"]
    assert any("未验证" in item for item in report["limitations"])
    assert report["hypotheses"] == []


def test_known_incomplete_window_withholds_even_the_change():
    report = build_comparison_report(
        analysis_id="a2",
        question="ads_revenue 上周为什么下降？",
        comparison={},
        calculation_id="c2",
        calculation={"baseline": "5000", "current": "4000", "change": "-1000", "change_pct": "-0.2"},
        query_ids=["q1"],
        dataset_ids=["d1"],
        metric_key="ads_revenue",
        metric_version=1,
        evidence_available_until=None,
        completeness=CompletenessState.INCOMPLETE.value,
    )
    assert report["status"] == "PARTIAL"
    assert report["claims"] == []
    assert any("已知窗口数据不完整" in item for item in report["limitations"])
