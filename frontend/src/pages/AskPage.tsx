import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { createAgentSession, createAnalysis, listMetrics, type MetricSummary } from "../api/endpoints";

/**
 * The dimension list on this page is not a fixed menu: it is the selected
 * metric's declared `allowed_dimensions`, which the metric compiler enforces
 * (app/metrics/compiler.py rejects anything else with
 * METRIC_DIMENSION_NOT_ALLOWED). The picker therefore shows the metric's whole
 * declared grain and marks the entries that are time columns rather than
 * breakdown dimensions, so the list is readable as a contract instead of as a
 * short menu of unclear origin.
 */

const MAX_DIMENSIONS = 3;

/** Plain-language reading of the aggregation constraint a metric carries. */
const AGGREGATION_KINDS: Record<string, string> = {
  sum: "互斥分区内可直接相加",
  sum_of_aggregates: "先按数据源分别聚合到相同日期与维度再加总，不能直接 JOIN 明细表",
  ratio_of_sums: "比率一律按「分子总和 ÷ 分母总和」计算，不能先算每日或每组比率再平均",
  distinct_per_day: "单日去重口径，跨日只能取日均，多天不能相加",
};

function aggregationLabel(kind: string, notes: string | null | undefined): string {
  const reading = AGGREGATION_KINDS[kind];
  if (reading) return `${kind} — ${reading}`;
  return notes ?? kind;
}

function isTimeColumn(name: string): boolean {
  return name === "dt" || name === "date" || name.endsWith("_date") || name.endsWith("_at");
}

function datasetLabel(metric: MetricSummary): string {
  const datasets = metric.datasets ?? [];
  if (datasets.length === 0) return "未标注数据集";
  if (datasets.length === 1) return datasets[0];
  return `跨数据集 · ${datasets.join(" + ")}`;
}

export function AskPage() {
  const navigate = useNavigate();
  const metricsQuery = useQuery({ queryKey: ["metrics"], queryFn: listMetrics });
  const metrics = useMemo(() => metricsQuery.data ?? [], [metricsQuery.data]);

  const [question, setQuestion] = useState("为什么当前期间的收入发生变化？");
  const [datasetFilter, setDatasetFilter] = useState("");
  const [requestedMetric, setRequestedMetric] = useState("total_revenue");
  const [requestedDimensions, setRequestedDimensions] = useState<string[]>([]);
  const [baselineStart, setBaselineStart] = useState("2026-09-11");
  const [baselineEnd, setBaselineEnd] = useState("2026-09-12");
  const [currentStart, setCurrentStart] = useState("2026-09-12");
  const [currentEnd, setCurrentEnd] = useState("2026-09-13");

  const datasets = useMemo(() => {
    const names = new Set<string>();
    metrics.forEach((metric) => (metric.datasets ?? []).forEach((name) => names.add(name)));
    return [...names].sort();
  }, [metrics]);

  const visibleMetrics = useMemo(
    () =>
      datasetFilter
        ? metrics.filter((metric) => (metric.datasets ?? []).includes(datasetFilter))
        : metrics,
    [metrics, datasetFilter],
  );

  // Filtering by dataset narrows the list; if it hides the current metric, fall
  // back to the first one still on screen rather than submitting a hidden key.
  const metricKey = visibleMetrics.some((metric) => metric.metric_key === requestedMetric)
    ? requestedMetric
    : visibleMetrics[0]?.metric_key ?? "";

  const selected = useMemo(
    () => metrics.find((metric) => metric.metric_key === metricKey),
    [metrics, metricKey],
  );

  const allowed = useMemo(() => selected?.allowed_dimensions ?? [], [selected]);
  const closedDimensions = useMemo(
    () => (selected?.grain ?? []).filter((name) => !allowed.includes(name)),
    [selected, allowed],
  );
  // Re-validated against the selected metric on every render, so changing the
  // metric or the dataset filter can never submit a stale dimension.
  const dimensions = useMemo(
    () => requestedDimensions.filter((name) => allowed.includes(name)).slice(0, MAX_DIMENSIONS),
    [requestedDimensions, allowed],
  );

  const metricGroups = useMemo(() => {
    const groups = new Map<string, MetricSummary[]>();
    visibleMetrics.forEach((metric) => {
      const label = datasetLabel(metric);
      groups.set(label, [...(groups.get(label) ?? []), metric]);
    });
    return [...groups.entries()].sort((left, right) => left[0].localeCompare(right[0]));
  }, [visibleMetrics]);

  const siblings = useMemo(
    () =>
      selected
        ? metrics.filter(
            (metric) =>
              metric.metric_key !== selected.metric_key &&
              (metric.datasets ?? []).some((dataset) =>
                (selected.datasets ?? []).includes(dataset),
              ),
          )
        : [],
    [metrics, selected],
  );

  const submit = useMutation({
    mutationFn: async () => {
      const session = await createAgentSession(question.slice(0, 80));
      return createAnalysis({
        sessionId: session.id,
        question,
        metricKey,
        baselineStart,
        baselineEnd,
        currentStart,
        currentEnd,
        dimensions,
        dataComplete: true,
      });
    },
    onSuccess: (result) => navigate(`/analyses/${result.analysis_id}`),
  });

  const error = submit.error instanceof ApiError ? submit.error : null;

  return (
    <div className="workspace">
      <section className="card ask-hero">
        <span className="eyebrow">Governed analysis</span>
        <h1>Ask your data</h1>
        <p className="muted">
          Every number is produced by the Query Gateway and remains linked to its SQL, metric version, and result hash.
        </p>
        <textarea
          className="sql-editor ask-input"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          data-testid="analysis-question"
        />
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Analysis scope</h2>
          <span className="muted small">Periods are half-open: start included, end excluded.</span>
        </div>

        <div className="form-row">
          <label className="scope-dataset">
            数据集
            <select
              data-testid="dataset-filter"
              value={datasetFilter}
              onChange={(event) => setDatasetFilter(event.target.value)}
            >
              <option value="">全部数据集（{metrics.length} 个指标）</option>
              {datasets.map((dataset) => (
                <option key={dataset} value={dataset}>
                  {dataset}
                </option>
              ))}
            </select>
          </label>
          <label className="grow">
            指标
            <select
              data-testid="analysis-metric"
              value={metricKey}
              onChange={(event) => {
                setRequestedMetric(event.target.value);
                setRequestedDimensions([]);
              }}
            >
              {metricGroups.map(([label, items]) => (
                <optgroup key={label} label={label}>
                  {items.map((metric) => (
                    <option key={metric.metric_key} value={metric.metric_key}>
                      {metric.name} · {metric.metric_key}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
        </div>

        {selected ? (
          <div className="metric-provenance" data-testid="metric-provenance">
            <div className="chips">
              {(selected.datasets ?? []).map((dataset) => (
                <span className="chip mono-id" key={dataset}>
                  {dataset}
                </span>
              ))}
              <span className="chip">口径版本 v{selected.version}</span>
              <span className="chip">{selected.aggregation_kind}</span>
            </div>
            <dl className="metric-facts">
              <div>
                <dt>单位</dt>
                <dd>{selected.currency ? `${selected.unit} · ${selected.currency}` : selected.unit}</dd>
              </div>
              <div>
                <dt>业务时区</dt>
                <dd>{selected.timezone}</dd>
              </div>
              <div>
                <dt>日期列</dt>
                <dd className="mono-id">{(selected.grain ?? []).find(isTimeColumn) ?? "—"}</dd>
              </div>
              <div>
                <dt>公式</dt>
                <dd>
                  <code>{selected.formula}</code>
                </dd>
              </div>
            </dl>
            <p className="muted small">
              聚合约束：{aggregationLabel(selected.aggregation_kind, selected.notes)}
            </p>
            {selected.notes ? <p className="muted small">口径说明：{selected.notes}</p> : null}
            {siblings.length > 0 ? (
              <p className="muted small">
                同一数据集的其它指标：
                {siblings.map((metric) => (
                  <button
                    className="link-button"
                    key={metric.metric_key}
                    data-testid={`metric-sibling-${metric.metric_key}`}
                    onClick={() => {
                      setRequestedMetric(metric.metric_key);
                      setRequestedDimensions([]);
                    }}
                  >
                    {metric.name}
                  </button>
                ))}
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="period-grid">
          <label>Baseline start<input data-testid="baseline-start" type="date" value={baselineStart} onChange={(e) => setBaselineStart(e.target.value)} /></label>
          <label>Baseline end<input data-testid="baseline-end" type="date" value={baselineEnd} onChange={(e) => setBaselineEnd(e.target.value)} /></label>
          <label>Current start<input data-testid="current-start" type="date" value={currentStart} onChange={(e) => setCurrentStart(e.target.value)} /></label>
          <label>Current end<input data-testid="current-end" type="date" value={currentEnd} onChange={(e) => setCurrentEnd(e.target.value)} /></label>
        </div>

        {selected && (allowed.length > 0 || closedDimensions.length > 0) ? (
          <fieldset className="dimension-picker" data-testid="dimension-source">
            <legend>Break down by up to three dimensions</legend>
            <div className="dimension-options">
              {allowed.map((dimension) => (
                <label className="check" key={dimension} data-testid={`dimension-${dimension}`}>
                  <input
                    type="checkbox"
                    checked={dimensions.includes(dimension)}
                    disabled={!dimensions.includes(dimension) && dimensions.length >= MAX_DIMENSIONS}
                    onChange={(event) =>
                      setRequestedDimensions((current) =>
                        event.target.checked
                          ? [...current.filter((item) => allowed.includes(item)), dimension]
                          : current.filter((item) => item !== dimension),
                      )
                    }
                  />
                  {dimension}
                </label>
              ))}
              {closedDimensions.map((dimension) => (
                <label
                  className="check closed"
                  key={dimension}
                  data-testid={`dimension-closed-${dimension}`}
                  title={`${dimension} 是该指标粒度里的时间列，由上面的期间选择控制，不作为下钻维度`}
                >
                  <input type="checkbox" disabled />
                  {dimension}
                  <span className="muted small">{isTimeColumn(dimension) ? "时间列 · 由期间控制" : "未开放为维度"}</span>
                </label>
              ))}
            </div>
            <p className="muted small">
              可勾选的维度 = 该指标口径声明的 allowed_dimensions（
              <code>metadata/metrics/{selected.metric_key}.yaml</code>），也就是它所在数据集里可加总的非度量列。
              数据集还有哪些列、哪些被登记为维度，见 <Link to="/catalog">Catalog</Link>；要开放新维度需要先在治理侧声明该指标的口径。
            </p>
          </fieldset>
        ) : null}

        {error ? <p className="notice error"><strong>{error.code}</strong> — {error.message}</p> : null}
        <button
          className="primary"
          disabled={!question.trim() || !metricKey || submit.isPending}
          onClick={() => submit.mutate()}
          data-testid="start-analysis"
        >
          {submit.isPending ? "Starting…" : "Start analysis"}
        </button>
      </section>
    </div>
  );
}
