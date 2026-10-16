"""Run compiled aggregate SQL on real rows before observing/reporting results.

SQLite verifies SQL empty-aggregate semantics locally; it is not engine-matrix
acceptance for PostgreSQL, MySQL, Doris or Spark.
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.agent import runner
from app.agent.report import build_comparison_report
from app.metrics.compiler import compile_metric
from app.metrics.registry import load_metric_definitions


@pytest.fixture
def sources():
    connection = sqlite3.connect(":memory:")
    connection.execute("ATTACH DATABASE ':memory:' AS demo")
    for table in ("ads_revenue_daily", "iap_revenue_daily", "user_daily"):
        connection.execute(
            f"CREATE TABLE demo.{table} (dt TEXT, country TEXT, revenue_usd NUMERIC, impressions INTEGER, dau INTEGER)"
        )
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def definitions():
    return {d.metric_key: d for d in load_metric_definitions(Path(__file__).resolve().parents[3] / "metadata")}


def observe(monkeypatch, sources, definition, dimensions=None, filters=None):
    descriptors, payloads, jobs = [], {}, {}
    dataset_ids = {name: str(uuid.uuid4()) for name in definition.all_datasets}
    for period, start, end in (
        ("baseline", "2026-09-10", "2026-09-11"),
        ("current", "2026-09-11", "2026-09-12"),
    ):
        compiled = compile_metric(definition, dimensions=dimensions, filters=filters, period_start=start, period_end=end)
        for query in compiled.queries:
            query_id = uuid.uuid4()
            cursor = sources.execute(query.sql, query.parameters)
            payloads[str(query_id)] = {
                "columns": [{"name": column[0]} for column in cursor.description],
                "rows": cursor.fetchall(),
            }
            descriptors.append({
                **query.as_dict(), "query_id": str(query_id), "period": period,
                "composition": compiled.composition, "dataset_id": dataset_ids[query.dataset], "role": "primary",
            })
            jobs[query_id] = NS(id=query_id, status="SUCCEEDED")
    results = iter(NS(storage_key=d["query_id"], expires_at=None) for d in descriptors)
    session = NS(execute=lambda *args: NS(scalar_one=lambda: next(results)))
    monkeypatch.setattr(runner, "get_result_store", lambda: NS(read_json=lambda key: payloads[key]))
    monkeypatch.setattr(runner.analyses_repo, "list_steps", lambda *args: [])
    monkeypatch.setattr(runner, "_event", lambda *args, **kwargs: None)
    task = NS(id=uuid.uuid4(), state={"dimensions": dimensions or [], "metric_definition": {"aggregation_kind": definition.aggregation_kind}})
    runner._observe(session, task=task, descriptors=descriptors, jobs=jobs)
    return task.state["observations"]


def report(observation, completeness="complete"):
    return build_comparison_report(
        analysis_id="analysis", question="compare revenue", comparison={
            "baseline": ["2026-09-10", "2026-09-11"], "current": ["2026-09-11", "2026-09-12"],
        }, calculation_id="calculation", calculation=observation,
        query_ids=observation["primary_query_ids"], dataset_ids=observation["dataset_ids"],
        metric_key="revenue", metric_version=1, evidence_available_until=None,
        completeness=completeness, chart_ids=["would-be-chart"],
    )


@pytest.mark.parametrize("dimensions", [[], ["country"]])
@pytest.mark.parametrize("completeness", ["complete", "unknown", "incomplete"])
def test_empty_windows_never_claim_zero(monkeypatch, sources, definitions, dimensions, completeness):
    observation = observe(monkeypatch, sources, definitions["ads_revenue"], dimensions)
    assert observation["baseline"] is None and observation["current"] is None
    assert observation["change"] is None
    assert observation["coverage"]["baseline"][0]["source_rows"] == 0
    result = report(observation, completeness)
    assert result["status"] == "PARTIAL"
    assert result["claims"] == result["hypotheses"] == result["chart_ids"] == []
    assert sum("没有匹配数据" in limitation for limitation in result["limitations"]) == 2
    assert not any("变化 0" in limitation for limitation in result["limitations"])
    assert result["materiality"]["absolute_threshold"] is None


@pytest.mark.parametrize("dimensions", [[], ["country"]])
@pytest.mark.parametrize("completeness", ["complete", "unknown"])
def test_rows_with_zero_values_still_report_a_real_zero(monkeypatch, sources, definitions, dimensions, completeness):
    sources.executemany("INSERT INTO demo.ads_revenue_daily (dt, country, revenue_usd) VALUES (?, 'US', 0)", [("2026-09-10",), ("2026-09-11",)])
    observation = observe(monkeypatch, sources, definitions["ads_revenue"], dimensions)
    assert observation["change"] == "0"
    assert all(observation["coverage"][period][0]["source_rows"] == 1 for period in ("baseline", "current"))
    result = report(observation, completeness)
    assert "变化 0" in result["claims"][0]["text"]
    assert result["status"] == ("COMPLETED" if completeness == "complete" else "PARTIAL")


@pytest.mark.parametrize("present", ["2026-09-10", "2026-09-11"])
def test_one_empty_period_is_not_a_drop_or_growth(monkeypatch, sources, definitions, present):
    sources.execute("INSERT INTO demo.ads_revenue_daily (dt, country, revenue_usd) VALUES (?, 'US', 100)", (present,))
    observation = observe(monkeypatch, sources, definitions["ads_revenue"], ["country"])
    assert observation["change"] is None
    assert "contribution" not in observation
    assert observation["target"] is observation["drivers"] is None
    result = report(observation)
    assert result["claims"] == []
    assert sum("没有匹配数据" in limitation for limitation in result["limitations"]) == 1


@pytest.mark.parametrize("metric", ["ecpm", "dau"])
def test_empty_nonadditive_windows_finish_with_a_limitation(monkeypatch, sources, definitions, metric):
    observation = observe(monkeypatch, sources, definitions[metric], ["country"])
    assert report(observation)["status"] == "PARTIAL"
    # Use only period-total evidence, not a double count of totals + breakdowns.
    assert len(observation["coverage"]["baseline"]) == 1


@pytest.mark.parametrize("formula", ["ads + iap", "coalesce(ads, iap)"])
def test_missing_required_source_is_not_zero_filled_by_composition(monkeypatch, sources, definitions, formula):
    for dt in ("2026-09-10", "2026-09-11"):
        sources.execute("INSERT INTO demo.ads_revenue_daily (dt, country, revenue_usd) VALUES (?, 'US', 100)", (dt,))
    definition = definitions["total_revenue"].model_copy(update={"formula": formula})
    observation = observe(monkeypatch, sources, definition, ["country"])
    assert [source["source_rows"] for source in observation["coverage"]["current"]] == [1, 0]
    assert report(observation)["claims"] == []
    assert observation["baseline"] is None


def test_filters_are_applied_to_source_coverage(monkeypatch, sources, definitions):
    for dt in ("2026-09-10", "2026-09-11"):
        sources.execute("INSERT INTO demo.ads_revenue_daily (dt, country, revenue_usd) VALUES (?, 'US', 100)", (dt,))
    observation = observe(monkeypatch, sources, definitions["ads_revenue"], filters={"country": "DE"})
    assert report(observation)["claims"] == []
    assert observation["coverage"]["baseline"][0]["source_rows"] == 0


def test_count_metric_distinguishes_empty_input_from_a_zero_count(monkeypatch, sources, definitions):
    definition = definitions["ads_revenue"].model_copy(deep=True)
    definition.components["revenue"] = definition.components["revenue"].model_copy(update={"aggregation": "count"})
    empty = observe(monkeypatch, sources, definition)
    assert report(empty)["claims"] == []
    for dt in ("2026-09-10", "2026-09-11"):
        sources.execute("INSERT INTO demo.ads_revenue_daily (dt, revenue_usd) VALUES (?, NULL)", (dt,))
    populated = observe(monkeypatch, sources, definition)
    assert "变化 0" in report(populated)["claims"][0]["text"]


def test_coverage_alias_does_not_shadow_metric_alias(sources, definitions):
    definition = definitions["ads_revenue"].model_copy(update={"metric_key": "__aind_source_rows"})
    query = compile_metric(definition, period_start="2026-09-10", period_end="2026-09-11").queries[0]
    cursor = sources.execute(query.sql, query.parameters)
    names = [column[0] for column in cursor.description]
    assert len(set(names)) == len(names)
    assert query.metric_alias in names and query.source_row_count_alias in names


@pytest.mark.parametrize("value", [-1, "NaN", "Infinity", "0.5", None, True])
def test_corrupt_counts_are_not_treated_as_no_data(value):
    with pytest.raises(runner.AnalysisExecutionError, match="invalid source-row"):
        runner._source_row_count({"columns": [{"name": "n"}], "rows": [[value]]}, {"source_row_count_alias": "n"})


def test_old_query_results_require_resubmission():
    with pytest.raises(runner.AnalysisExecutionError, match="resubmit"):
        runner._source_row_count({"columns": [{"name": "revenue"}], "rows": [[0]]}, {})


def test_old_calculation_checkpoints_cannot_publish_without_coverage(monkeypatch):
    monkeypatch.setattr(runner.analyses_repo, "list_queries", lambda *args: [])
    task = NS(id=uuid.uuid4(), state={"observations": {"calculation_version": 2, "change": "0"}})
    with pytest.raises(runner.AnalysisExecutionError, match="resubmit"):
        runner._synthesizing(None, task=task)


@pytest.mark.parametrize("populated", [False, True])
def test_synthesis_retains_coverage_in_evidence_and_publishes_the_right_status(monkeypatch, sources, definitions, populated):
    if populated:
        for dt in ("2026-09-10", "2026-09-11"):
            sources.execute("INSERT INTO demo.ads_revenue_daily (dt, revenue_usd) VALUES (?, 0)", (dt,))
    observation = observe(monkeypatch, sources, definitions["ads_revenue"])
    artifacts = []
    def artifact(repo, session, **kwargs):
        artifacts.append(kwargs)
        return {"id": "calculation", **kwargs["content"]}
    monkeypatch.setattr(runner, "_artifact", artifact)
    monkeypatch.setattr(runner, "_metric_definition", lambda *args: (None, definitions["ads_revenue"]))
    monkeypatch.setattr(runner.analyses_repo, "list_queries", lambda *args: [])
    monkeypatch.setattr(runner, "_set_status", lambda session, task, target: setattr(task, "status", target))
    task = NS(
        id=uuid.uuid4(), question="compare", conversation_turn_id=uuid.uuid4(),
        context={"completeness": "complete"},
        state={"observations": observation, "metric_key": "ads_revenue"},
    )
    assert runner._synthesizing(None, task=task)
    assert artifacts[0]["kind"] == "period_comparison"
    assert artifacts[0]["content"]["coverage"] == observation["coverage"]
    assert artifacts[0]["query_ids"] == observation["primary_query_ids"]
    assert task.final_report["status"] == ("COMPLETED" if populated else "PARTIAL")
    assert bool(task.final_report["claims"]) == populated


def test_submission_checkpoints_the_coverage_alias_without_extra_queries(monkeypatch, definitions):
    from app.config import Settings
    compiled = compile_metric(definitions["total_revenue"], period_start="2026-09-10", period_end="2026-09-11")
    submitted = []
    def submit(session, **kwargs):
        submitted.append(kwargs["request"])
        return NS(id=uuid.uuid4()), False
    monkeypatch.setattr(runner, "_resolve_dataset", lambda *args, **kwargs: NS(id=uuid.uuid4(), datasource_id=uuid.uuid4()))
    monkeypatch.setattr(runner, "submit_query", submit)
    monkeypatch.setattr(runner.analyses_repo, "add_step", lambda *args, **kwargs: None)
    descriptors = runner._submit_compiled(
        None, task=NS(id=uuid.uuid4()), auth=NS(role_ids=[]), compiled=compiled,
        period="baseline", settings=Settings(),
    )
    assert len(submitted) == len(descriptors) == 2
    for descriptor, request in zip(descriptors, submitted, strict=True):
        assert f"COUNT(*) AS {descriptor['source_row_count_alias']}" in request.sql
