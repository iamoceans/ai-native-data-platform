import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { MemoryPanel } from "../components/MemoryPanel";
import {
  createAgentSession,
  createAnalysis,
  getDatasetContext,
  getDatasetSchema,
  listDatasets,
  listMetrics,
  type ColumnInfo,
  type MetricSummary,
} from "../api/endpoints";

/**
 * Two things drive this page. The dataset picker answers "what is in this table
 * and can I analyse it": it reads the platform catalog (grain, description,
 * currency) plus the live schema, so a table without a declared metric says so
 * instead of just being absent. The metric picker then pins the governed
 * contract: dimensions are the metric's declared `allowed_dimensions`, which the
 * compiler enforces (app/metrics/compiler.py rejects anything else with
 * METRIC_DIMENSION_NOT_ALLOWED), so the declared grain is shown as it is -
 * checkable dimensions, plus the time column that the period picker owns.
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

function isNumericType(columnType: string): boolean {
  return /int|decimal|double|float|numeric|long/i.test(columnType);
}

function datasetLabel(metric: MetricSummary): string {
  const datasets = metric.datasets ?? [];
  if (datasets.length === 0) return "未标注数据集";
  if (datasets.length === 1) return datasets[0];
  return `跨数据集 · ${datasets.join(" + ")}`;
}

function qualifier(schemaName: string, objectName: string): string {
  return `${schemaName}.${objectName}`;
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

  const catalogQuery = useQuery({ queryKey: ["datasets"], queryFn: () => listDatasets() });

  // Dataset options come from the catalog, so a table without a declared metric
  // is still visible and can say why it cannot be analysed, plus any dataset a
  // metric names that the catalog did not return to this user. Datasets that
  // have metrics sort first.
  const datasetOptions = useMemo(() => {
    const metricCounts = new Map<string, number>();
    metrics.forEach((metric) =>
      (metric.datasets ?? []).forEach((name) =>
        metricCounts.set(name, (metricCounts.get(name) ?? 0) + 1),
      ),
    );
    (catalogQuery.data ?? []).forEach((dataset) => {
      const key = qualifier(dataset.schema_name, dataset.object_name);
      if (!metricCounts.has(key)) metricCounts.set(key, 0);
    });
    return [...metricCounts.entries()]
      .map(([name, metricCount]) => ({ name, metricCount }))
      .sort((left, right) =>
        left.metricCount === right.metricCount
          ? left.name.localeCompare(right.name)
          : right.metricCount - left.metricCount,
      );
  }, [metrics, catalogQuery.data]);

  // The selected dataset is looked up in the catalog for its semantic context
  // and live schema; both lookups degrade to "not readable" instead of blocking
  // the metric path.
  const datasetId = useMemo(
    () =>
      (catalogQuery.data ?? []).find(
        (dataset) => qualifier(dataset.schema_name, dataset.object_name) === datasetFilter,
      )?.id,
    [catalogQuery.data, datasetFilter],
  );
  const contextQuery = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => getDatasetContext(datasetId as string),
    enabled: Boolean(datasetId),
  });
  const schemaQuery = useQuery({
    queryKey: ["dataset-schema", datasetId],
    queryFn: () => getDatasetSchema(datasetId as string),
    enabled: Boolean(datasetId),
  });

  const datasetMetrics = useMemo(
    () => metrics.filter((metric) => (metric.datasets ?? []).includes(datasetFilter)),
    [metrics, datasetFilter],
  );
  const datasetGrain = useMemo(
    () => contextQuery.data?.grain ?? datasetMetrics[0]?.grain ?? [],
    [contextQuery.data, datasetMetrics],
  );
  // Measure candidates: numeric columns that are not part of the declared grain.
  const datasetMeasures = useMemo(
    () => {
      const grain = new Set(datasetGrain);
      return (schemaQuery.data?.columns ?? []).filter(
        (column: ColumnInfo) => isNumericType(column.type) && !grain.has(column.name),
      );
    },
    [schemaQuery.data, datasetGrain],
  );

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
              {datasetOptions.map((option) => (
                <option key={option.name} value={option.name}>
                  {option.name}
                  {option.metricCount > 0 ? ` · ${option.metricCount} 个指标` : " · 未声明指标"}
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

        {datasetFilter ? (
          <div className="dataset-profile" data-testid="dataset-profile">
            <div className="card-head">
              <h3 className="mono-id">{datasetFilter}</h3>
              <span className="muted small">
                {contextQuery.isLoading
                  ? "读取目录上下文…"
                  : contextQuery.data
                    ? "来自平台目录 + 语义登记"
                    : "该数据集不在你的目录可见范围内"}
              </span>
            </div>
            {contextQuery.data?.description ? (
              <p className="muted small">{contextQuery.data.description}</p>
            ) : null}
            <div className="chips">
              {(datasetGrain ?? []).map((dimension) => (
                <span className="chip mono-id" key={dimension} data-testid={`profile-dimension-${dimension}`}>
                  {dimension}
                  {isTimeColumn(dimension) ? " · 时间列" : ""}
                </span>
              ))}
              {contextQuery.data?.business_timezone ? (
                <span className="chip">业务时区 {contextQuery.data.business_timezone}</span>
              ) : null}
              {contextQuery.data?.currency ? <span className="chip">币种 {contextQuery.data.currency}</span> : null}
            </div>
            <p className="muted small" data-testid="profile-measures">
              度量列：
              {datasetMeasures.length > 0
                ? datasetMeasures.map((column) => column.name).join(" · ")
                : "无可加总的数值列（配置类表）"}
            </p>
            {datasetMetrics.length > 0 ? (
              <p className="muted small">
                这张表已声明的指标（点选即切换分析口径）：
                {datasetMetrics.map((metric) => (
                  <button
                    className="link-button"
                    key={metric.metric_key}
                    data-testid={`profile-metric-${metric.metric_key}`}
                    onClick={() => {
                      setRequestedMetric(metric.metric_key);
                      setRequestedDimensions([]);
                    }}
                  >
                    {metric.name}
                  </button>
                ))}
              </p>
            ) : (
              <p className="notice warn" data-testid="profile-not-analysable">
                这张表在 Ask data 里还不能分析：它没有声明任何指标，因此没有可用的聚合口径与版本。
                {datasetMeasures.length > 0
                  ? "它确实有度量列，需要先在治理侧为该表声明指标（口径、聚合方式、可用维度）才能进入分析。"
                  : "它也没有可加总的度量列，属于配置/清单类表，只能作为分析中的关联背景。"}
              </p>
            )}
          </div>
        ) : null}

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
                <dd>{selected.currency && selected.currency !== selected.unit ? `${selected.unit} · ${selected.currency}` : selected.unit}</dd>
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

        {metricKey ? <MemoryPanel metricKey={metricKey} /> : null}

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
