import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { createAgentSession, createAnalysis, listMetrics } from "../api/endpoints";

export function AskPage() {
  const navigate = useNavigate();
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: listMetrics });
  const [question, setQuestion] = useState("为什么当前期间的收入发生变化？");
  const [metricKey, setMetricKey] = useState("total_revenue");
  const [baselineStart, setBaselineStart] = useState("2026-09-11");
  const [baselineEnd, setBaselineEnd] = useState("2026-09-12");
  const [currentStart, setCurrentStart] = useState("2026-09-12");
  const [currentEnd, setCurrentEnd] = useState("2026-09-13");
  const [dimensions, setDimensions] = useState<string[]>([]);

  const selected = useMemo(
    () => metrics.data?.find((metric) => metric.metric_key === metricKey),
    [metrics.data, metricKey],
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
          <label className="grow">
            Metric
            <select data-testid="analysis-metric" value={metricKey} onChange={(event) => { setMetricKey(event.target.value); setDimensions([]); }}>
              {(metrics.data ?? []).map((metric) => (
                <option key={metric.metric_key} value={metric.metric_key}>
                  {metric.name} · {metric.metric_key}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="period-grid">
          <label>Baseline start<input data-testid="baseline-start" type="date" value={baselineStart} onChange={(e) => setBaselineStart(e.target.value)} /></label>
          <label>Baseline end<input data-testid="baseline-end" type="date" value={baselineEnd} onChange={(e) => setBaselineEnd(e.target.value)} /></label>
          <label>Current start<input data-testid="current-start" type="date" value={currentStart} onChange={(e) => setCurrentStart(e.target.value)} /></label>
          <label>Current end<input data-testid="current-end" type="date" value={currentEnd} onChange={(e) => setCurrentEnd(e.target.value)} /></label>
        </div>
        {(selected?.allowed_dimensions?.length ?? 0) > 0 ? (
          <fieldset className="dimension-picker">
            <legend>Break down by up to three dimensions</legend>
            {(selected?.allowed_dimensions ?? []).map((dimension) => (
              <label className="check" key={dimension} data-testid={`dimension-${dimension}`}>
                <input
                  type="checkbox"
                  checked={dimensions.includes(dimension)}
                  disabled={!dimensions.includes(dimension) && dimensions.length >= 3}
                  onChange={(event) => setDimensions((current) => event.target.checked ? [...current, dimension] : current.filter((item) => item !== dimension))}
                />
                {dimension}
              </label>
            ))}
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
