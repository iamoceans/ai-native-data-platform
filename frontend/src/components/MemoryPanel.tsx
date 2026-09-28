import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { confirmMemory, listMemory, me, rejectMemory, type MemoryItem } from "../api/endpoints";

/**
 * The learned half of the platform, made visible.
 *
 * Every row here came out of a finished governed analysis and is stamped with the
 * analysis it came from; statements never carry numbers, so the figures still
 * live only in the evidence chain. A fresh row is 待确认: the planner already
 * reads it as a hint, but only an administrator can turn it into settled
 * business knowledge (or reject it for good).
 */

const KIND_LABELS: Record<string, string> = {
  segment: "分组",
  caveat: "口径/限制",
  definition: "定义",
  follow_up: "待追查",
};

const STATUS_LABELS: Record<string, string> = {
  proposed: "待确认",
  confirmed: "已确认",
  rejected: "已否决",
};

function MemoryRow({
  item,
  canCurate,
  busy,
  onCurate,
}: {
  item: MemoryItem;
  canCurate: boolean;
  busy: boolean;
  onCurate: (status: "confirmed" | "rejected") => void;
}) {
  return (
    <li className="memory-row" data-testid={`memory-${item.id}`}>
      <div className="memory-statement">
        <span className="tag">{KIND_LABELS[item.kind] ?? item.kind}</span>
        <span className={item.status === "rejected" ? "memory-text rejected" : "memory-text"}>
          {item.statement}
        </span>
      </div>
      <div className="memory-meta">
        <span className={`tag ${item.status === "proposed" ? "warn" : ""}`}>
          {STATUS_LABELS[item.status] ?? item.status}
        </span>
        {(item.scope_dimensions ?? []).length ? (
          <span className="muted small">维度：{(item.scope_dimensions ?? []).join(" · ")}</span>
        ) : null}
        <span className="muted small">
          被调用 {item.reuse_count} 次 · 重复学到 {Math.max(0, item.seen_count - 1)} 次
        </span>
        {item.analysis_id ? (
          <Link className="muted small" to={`/analyses/${item.analysis_id}`}>
            来源分析 {item.analysis_id.slice(0, 8)}
          </Link>
        ) : null}
        {canCurate && item.status !== "rejected" ? (
          <span className="row-actions">
            {item.status === "proposed" ? (
              <button
                className="ghost small"
                disabled={busy}
                data-testid={`memory-confirm-${item.id}`}
                onClick={() => onCurate("confirmed")}
              >
                确认
              </button>
            ) : null}
            <button
              className="ghost small"
              disabled={busy}
              data-testid={`memory-reject-${item.id}`}
              onClick={() => onCurate("rejected")}
            >
              否决
            </button>
          </span>
        ) : null}
      </div>
    </li>
  );
}

export function MemoryPanel({ metricKey }: { metricKey: string }) {
  const queryClient = useQueryClient();
  const [showRejected, setShowRejected] = useState(false);
  const memory = useQuery({
    queryKey: ["memory", metricKey],
    queryFn: () => listMemory(metricKey),
    enabled: Boolean(metricKey),
  });
  const profile = useQuery({ queryKey: ["me"], queryFn: me });
  const canCurate = (profile.data?.capabilities ?? []).includes("admin.manage");
  const curate = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "confirmed" | "rejected" }) =>
      status === "confirmed" ? confirmMemory(id) : rejectMemory(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memory", metricKey] }),
  });

  const items = memory.data?.items ?? [];
  // Rejected rows stay in the store (they suppress re-learning the same idea)
  // but they are not part of the working knowledge, so they stay collapsed.
  const active = items.filter((item) => item.status !== "rejected");
  const rejected = items.filter((item) => item.status === "rejected");
  const visible = showRejected ? items : active;
  // Server-side scoped counts: the list is paged, the totals are not.
  const confirmed = memory.data?.counts?.confirmed ?? 0;
  const proposed = memory.data?.counts?.proposed ?? 0;
  const rejectedTotal = memory.data?.counts?.rejected ?? rejected.length;

  return (
    <div className="memory-panel" data-testid="memory-panel">
      <div className="card-head">
        <h3>业务记忆</h3>
        <span className="muted small" data-testid="memory-counts">
          已确认 {confirmed} · 待确认 {proposed}
          {rejectedTotal ? ` · 已否决 ${rejectedTotal}` : ""}
        </span>
      </div>
      <p className="muted small">
        每次分析结束后，模型只从该次分析已验证的结论里提炼语句（不含任何数字，且必须点名出现过的分组取值），
        下次提问时作为选维度的提示。待确认项可被否决，否决后不再使用，也不会被再学一遍。
      </p>
      {visible.length ? (
        <ul className="memory-list">
          {visible.map((item) => (
            <MemoryRow
              key={item.id}
              item={item}
              canCurate={canCurate}
              busy={curate.isPending}
              onCurate={(status) => curate.mutate({ id: item.id, status })}
            />
          ))}
        </ul>
      ) : (
        <p className="muted small">
          {memory.isLoading ? "读取中…" : "这个指标还没有学到任何东西——完成一次分析后，这里会出现它的第一条业务知识。"}
        </p>
      )}
      {rejected.length ? (
        <button
          className="ghost small"
          data-testid="memory-toggle-rejected"
          onClick={() => setShowRejected((current) => !current)}
        >
          {showRejected ? "隐藏已否决" : `显示已否决（${rejectedTotal}）`}
        </button>
      ) : null}
    </div>
  );
}
