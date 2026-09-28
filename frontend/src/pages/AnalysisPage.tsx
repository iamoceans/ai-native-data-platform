import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  cancelAnalysis,
  drilldownChart,
  getAnalysis,
  getAnalysisEvidence,
  getAnalysisMemory,
  getChart,
} from "../api/endpoints";
import { ChartPanel } from "../components/ChartPanel";
import { StatusBadge } from "../components/StatusBadge";

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);

/** Refusal reasons come from the extractor's validators; say them in plain words. */
const REFUSAL_LABELS: Record<string, string> = {
  ungrounded_statement: "过于笼统，未点名具体分组取值",
  near_duplicate: "与既有知识重复",
  statement_states_a_percentage: "出现百分比",
  statement_states_an_amount: "出现金额",
  statement_too_short: "过短",
  statement_too_long: "过长",
};

function refusalLabel(reason: string): string {
  const [code, detail] = reason.split(":");
  if (code === "unverifiable_figure") return `数字 ${detail} 无法核验`;
  return REFUSAL_LABELS[code] ?? code;
}

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
  // Learning runs right after an analysis is published, so this keeps looking
  // until the extraction for this run has landed (or the analysis is still busy).
  const memory = useQuery({
    queryKey: ["analysis-memory", id],
    queryFn: () => getAnalysisMemory(id),
    enabled: Boolean(id),
    refetchInterval: (query) => (query.state.data?.extraction ? false : 2500),
  });
  const extraction = (memory.data?.extraction ?? null) as Record<string, unknown> | null;
  const refused = (Array.isArray(extraction?.refused) ? extraction?.refused : []) as Array<Record<string, unknown>>;

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
      <section className="card" data-testid="analysis-memory">
        <div className="card-head">
          <h2>业务记忆</h2>
          <span className="muted small">
            {extraction && extraction.status === "extracted"
              ? `本次由 ${String(extraction.model_id ?? "model")} 提炼`
              : extraction
                ? `本次未提炼：${String(extraction.reason ?? "")}`
                : "尚未提炼（分析结束后由 agent worker 处理）"}
          </span>
        </div>
        {(memory.data?.used ?? []).length ? (
          <div>
            <p className="muted small">规划这次分析时参考了 {(memory.data?.used ?? []).length} 条已有知识：</p>
            <ul className="memory-list">
              {(memory.data?.used ?? []).map((item) => (
                <li className="memory-row" key={item.id} data-testid={`used-${item.id}`}>
                  <span className="memory-text">{item.statement}</span>
                  <span className="tag">{item.status === "confirmed" ? "已确认" : "待确认"}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="muted small">规划时还没有可参考的知识（这可能是该指标的第一次分析）。</p>
        )}
        {(memory.data?.learned ?? []).length ? (
          <div>
            <p className="muted small">这次新学到 {(memory.data?.learned ?? []).length} 条：</p>
            <ul className="memory-list">
              {(memory.data?.learned ?? []).map((item) => (
                <li className="memory-row" key={item.id} data-testid={`learned-${item.id}`}>
                  <span className="memory-text">{item.statement}</span>
                  <span className="tag warn">待确认</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {(memory.data?.reinforced ?? []).length ? (
          <p className="muted small">
            另有 {(memory.data?.reinforced ?? []).length} 条与既有知识重复，已记为再次印证（不新增条目）。
          </p>
        ) : null}
        {refused.length ? (
          <p className="muted small" data-testid="memory-refused">
            被拒绝的候选 {refused.length} 条：{refused.map((item) => refusalLabel(String(item.reason ?? ""))).join("、")}
          </p>
        ) : null}
      </section>
    </div>
  );
}
