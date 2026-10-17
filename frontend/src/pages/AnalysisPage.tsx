import { useRevokedAnalysis } from "../hooks/useRevokedAnalysis";
import { t, useLocale } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  cancelAnalysis,
  rerunAnalysis,
  drilldownChart,
  getAnalysis,
  getAnalysisEvidence,
  getAnalysisMemory,
  getChart,
} from "../api/endpoints";
import { ErrorMessage } from "../components/ErrorMessage";
import { ChartPanel } from "../components/ChartPanel";
import { StatusBadge, statusLabel } from "../components/StatusBadge";

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
  if (code === "unverifiable_figure")
    return t("数字 {0} 无法核验", { 0: detail });
  return t(REFUSAL_LABELS[code] ?? code);
}

export function AnalysisPage() {
  useLocale();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const rerunKey = useMemo(() => crypto.randomUUID(), [id]);
  const rerun = useMutation({ mutationFn: () => rerunAnalysis(id, rerunKey),
    onSuccess: result => { queryClient.invalidateQueries({ queryKey: ["analyses"] }); navigate(`/analyses/${result.analysis_id}`); } });
  const [evidence, setEvidence] = useState<Record<string, unknown> | null>(
    null,
  );
  const evidenceRequest = useRef(0);
  const detail = useQuery({
    queryKey: ["analysis", id],
    queryFn: () => getAnalysis(id),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      TERMINAL.has(query.state.data?.status ?? "") ? 15000 : 1000,
  });
  const cancel = useMutation({
    mutationFn: () => cancelAnalysis(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["analysis", id] }),
  });
  const revoked = useRevokedAnalysis(id, detail.data, false);
  useEffect(() => { evidenceRequest.current++; setEvidence(null); }, [id, revoked]);
  const report = (revoked ? null : detail.data?.final_report) as
    | Record<string, unknown>
    | null
    | undefined;
  const claims =
    (report?.claims as Array<Record<string, unknown>> | undefined) ?? [];
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
    enabled: Boolean(id) && !revoked,
    refetchInterval: (query) => (query.state.data?.extraction ? false : 2500),
  });
  const extraction = (memory.data?.extraction ?? null) as Record<
    string,
    unknown
  > | null;
  const refused = (
    Array.isArray(extraction?.refused) ? extraction?.refused : []
  ) as Array<Record<string, unknown>>;

  if (detail.isLoading)
    return (
      <section className="card">
        <p className="muted">{t("Loading analysis…")}</p>
      </section>
    );
  if (!detail.data)
    return (
      <section className="card">
        <p className="notice error">{t("Analysis is unavailable.")}</p>
      </section>
    );

  return (
    <div className="workspace">
      <section className="card">
        <div className="card-head">
          <div>
            <span className="eyebrow">
              {t("Analysis")} {id.slice(0, 8)}
            </span>
            <h1>{detail.data.question}</h1>
          </div>
          <StatusBadge status={detail.data.status} />
        </div>
        <p className="muted small">
          {t("Checkpoint")} {detail.data.checkpoint_version}{" "}
          {t("· policy revision")} {detail.data.policy_revision}
        </p>
        {TERMINAL.has(detail.data.status) ? <>
          <button className="ghost" data-testid="rerun-analysis" disabled={rerun.isPending || revoked} onClick={() => rerun.mutate()}>{t("重新分析")}</button>
          <p className="muted small">{t("使用当前指标定义重新查询，创建新报告并保留原报告；完整性需重新确认。")}</p>
          {rerun.isError ? <p className="notice error" role="alert"><ErrorMessage error={rerun.error} /></p> : null}
        </> : null}
        {!TERMINAL.has(detail.data.status) ? (
          <button
            className="ghost"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            {t("Cancel analysis")}
          </button>
        ) : null}
      </section>

      {revoked ? <p className="notice error" data-testid="permission-revoked"><ErrorMessage error={{ code: "PERMISSION_DENIED", message: "" }} /></p> : null}
      {!revoked && chart.data ? (
        <ChartPanel
          key={chart.data.id}
          chart={chart.data}
          busy={drill.isPending}
          onDrilldown={(dimension, value) => drill.mutate({ dimension, value })}
        />
      ) : null}

      {report ? (
        <section className="card">
          <div className="card-head">
            <h2>{t("Evidence-backed findings")}</h2>
            <span className="muted small">
              {statusLabel(String(report.status))}
            </span>
          </div>
          {claims.length ? (
            claims.map((claim) => (
              <article className="claim" key={String(claim.id)}>
                <span className="eyebrow">{t(String(claim.kind))}</span>
                <p>{String(claim.text)}</p>
              </article>
            ))
          ) : (
            <p className="notice">
              {t(
                "No numeric conclusion was issued because the evidence was incomplete.",
              )}
            </p>
          )}
          {limitations.length ? (
            <ul className="muted small">
              {limitations.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
          <button
            className="ghost"
            onClick={async () => {
              const request = ++evidenceRequest.current;
              const result = await getAnalysisEvidence(id);
              if (request === evidenceRequest.current) setEvidence(result);
            }}
          >
            {t("Load evidence")}
          </button>
          {evidence ? (
            <details className="evidence" open>
              <summary>{t("SQL and calculations")}</summary>
              <pre>{JSON.stringify(evidence, null, 2)}</pre>
            </details>
          ) : null}
        </section>
      ) : null}

      <section className="card">
        <div className="card-head">
          <h2>{t("Execution steps")}</h2>
          <span className="muted small">{t("Query Gateway only")}</span>
        </div>
        <ol className="step-list">
          {(detail.data.steps ?? []).map((step) => (
            <li key={step.key}>
              <code>{step.key}</code>
              <span>{step.tool_name}</span>
              <StatusBadge status={step.status} />
            </li>
          ))}
        </ol>
        {detail.data.state.last_error ? (
          <pre className="notice error">
            {JSON.stringify(detail.data.state.last_error, null, 2)}
          </pre>
        ) : null}
      </section>
      {!revoked ? <section className="card" data-testid="analysis-memory">
        <div className="card-head">
          <h2>{t("业务记忆")}</h2>
          <span className="muted small">
            {extraction && extraction.status === "extracted"
              ? t("本次由 {0} 提炼", {
                  0: String(extraction.model_id ?? "model"),
                })
              : extraction
                ? t("本次未提炼：{0}", { 0: String(extraction.reason ?? "") })
                : t("尚未提炼（分析结束后由 agent worker 处理）")}
          </span>
        </div>
        {(memory.data?.used ?? []).length ? (
          <div>
            <p className="muted small">
              {t("memory.used", { count: (memory.data?.used ?? []).length })}
            </p>
            <ul className="memory-list">
              {(memory.data?.used ?? []).map((item) => (
                <li
                  className="memory-row"
                  key={item.id}
                  data-testid={`used-${item.id}`}
                >
                  <span className="memory-text">{item.statement}</span>
                  <span className="tag">
                    {item.status === "confirmed" ? t("已确认") : t("待确认")}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="muted small">
            {t("规划时还没有可参考的知识（这可能是该指标的第一次分析）。")}
          </p>
        )}
        {(memory.data?.learned ?? []).length ? (
          <div>
            <p className="muted small">
              {t("memory.learned", {
                count: (memory.data?.learned ?? []).length,
              })}
            </p>
            <ul className="memory-list">
              {(memory.data?.learned ?? []).map((item) => (
                <li
                  className="memory-row"
                  key={item.id}
                  data-testid={`learned-${item.id}`}
                >
                  <span className="memory-text">{item.statement}</span>
                  <span className="tag warn">{t("待确认")}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {(memory.data?.reinforced ?? []).length ? (
          <p className="muted small">
            {t("memory.reinforced", {
              count: (memory.data?.reinforced ?? []).length,
            })}
          </p>
        ) : null}
        {refused.length ? (
          <p className="muted small" data-testid="memory-refused">
            {t("memory.refused", {
              count: refused.length,
              reasons: refused
                .map((item) => refusalLabel(String(item.reason ?? "")))
                .join(", "),
            })}
          </p>
        ) : null}
      </section> : null}
    </div>
  );
}
