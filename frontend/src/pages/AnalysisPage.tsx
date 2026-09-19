import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  cancelAnalysis,
  drilldownChart,
  getAnalysis,
  getAnalysisEvidence,
  getChart,
} from "../api/endpoints";
import { ChartPanel } from "../components/ChartPanel";
import { StatusBadge } from "../components/StatusBadge";

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);

export function AnalysisPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [evidence, setEvidence] = useState<Record<string, unknown> | null>(null);
  const detail = useQuery({
    queryKey: ["analysis", id],
    queryFn: () => getAnalysis(id),
    enabled: Boolean(id),
    refetchInterval: (query) => TERMINAL.has(query.state.data?.status ?? "") ? false : 1000,
  });
  const cancel = useMutation({
    mutationFn: () => cancelAnalysis(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["analysis", id] }),
  });
  const report = detail.data?.final_report as Record<string, unknown> | null | undefined;
  const claims = (report?.claims as Array<Record<string, unknown>> | undefined) ?? [];
  const limitations = (report?.limitations as string[] | undefined) ?? [];
  const chartIds = (report?.chart_ids as string[] | undefined) ?? [];
  const chart = useQuery({
    queryKey: ["chart", chartIds[0]],
    queryFn: () => getChart(chartIds[0]),
    enabled: chartIds.length > 0,
  });
  const drill = useMutation({
    mutationFn: ({ dimension, value }: { dimension: string; value: string }) =>
      drilldownChart(chartIds[0], { dimension, value }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["analysis", id] });
      navigate(`/analyses/${result.analysis_id}`);
    },
  });

  if (detail.isLoading) return <section className="card"><p className="muted">Loading analysis…</p></section>;
  if (!detail.data) return <section className="card"><p className="notice error">Analysis is unavailable.</p></section>;

  return (
    <div className="workspace">
      <section className="card">
        <div className="card-head">
          <div><span className="eyebrow">Analysis {id.slice(0, 8)}</span><h1>{detail.data.question}</h1></div>
          <StatusBadge status={detail.data.status} />
        </div>
        <p className="muted small">Checkpoint {detail.data.checkpoint_version} · policy revision {detail.data.policy_revision}</p>
        {!TERMINAL.has(detail.data.status) ? (
          <button className="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel analysis</button>
        ) : null}
      </section>

      {chart.data ? (
        <ChartPanel
          chart={chart.data}
          busy={drill.isPending}
          onDrilldown={(dimension, value) => drill.mutate({ dimension, value })}
        />
      ) : null}

      {report ? (
        <section className="card">
          <div className="card-head"><h2>Evidence-backed findings</h2><span className="muted small">{String(report.status)}</span></div>
          {claims.length ? claims.map((claim) => (
            <article className="claim" key={String(claim.id)}>
              <span className="eyebrow">{String(claim.kind)}</span>
              <p>{String(claim.text)}</p>
            </article>
          )) : <p className="notice">No numeric conclusion was issued because the evidence was incomplete.</p>}
          {limitations.length ? <ul className="muted small">{limitations.map((item) => <li key={item}>{item}</li>)}</ul> : null}
          <button className="ghost" onClick={async () => setEvidence(await getAnalysisEvidence(id))}>Load evidence</button>
          {evidence ? <details className="evidence" open><summary>SQL and calculations</summary><pre>{JSON.stringify(evidence, null, 2)}</pre></details> : null}
        </section>
      ) : null}

      <section className="card">
        <div className="card-head"><h2>Execution steps</h2><span className="muted small">Query Gateway only</span></div>
        <ol className="step-list">
          {(detail.data.steps ?? []).map((step) => (
            <li key={step.key}><code>{step.key}</code><span>{step.tool_name}</span><StatusBadge status={step.status} /></li>
          ))}
        </ol>
        {detail.data.state.last_error ? <pre className="notice error">{JSON.stringify(detail.data.state.last_error, null, 2)}</pre> : null}
      </section>
    </div>
  );
}
