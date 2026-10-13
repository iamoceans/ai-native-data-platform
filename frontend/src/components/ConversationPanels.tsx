import { useEffect, useState } from "react";
import type { SessionSummary, SkillSummary, TurnScopePatch } from "../api/endpoints";

/** Left column: the user's conversations, newest first. */
export function SessionList({
  sessions,
  currentId,
  onCreate,
  onSelect,
  busy,
}: {
  sessions: SessionSummary[];
  currentId: string | null;
  onCreate: () => void;
  onSelect: (sessionId: string) => void;
  busy?: boolean;
}) {
  return (
    <aside className="conversation-rail" aria-label="会话列表">
      <button
        type="button"
        className="primary block"
        data-testid="new-conversation"
        disabled={busy}
        onClick={onCreate}
      >
        新对话
      </button>
      <ul className="session-list" data-testid="session-list">
        {sessions.map((session) => (
          <li key={session.id}>
            <button
              type="button"
              className={`session-link ${session.id === currentId ? "active" : ""}`}
              data-testid={`session-${session.id}`}
              onClick={() => onSelect(session.id)}
            >
              <span className="session-title">{session.title || "未命名会话"}</span>
              <span className="muted small">
                {new Date(session.updated_at).toLocaleString()}
                {session.active_turn_id ? " · 进行中" : ""}
              </span>
            </button>
          </li>
        ))}
        {sessions.length === 0 ? <li className="muted small">还没有会话。</li> : null}
      </ul>
    </aside>
  );
}

export interface ScopeDraft {
  metricKey: string;
  baselineStart: string;
  baselineEnd: string;
  currentStart: string;
  currentEnd: string;
  dimensions: string[];
  filters: Record<string, string>;
  filterText: string;
  dataCompleteState: string;
}

/** The scope panel: prefilled from the confirmed scope, submitted as a patch. */
export function ScopePanel({
  draft,
  origins,
  metrics,
  onChange,
  onReset,
  collapsed,
  onToggle,
}: {
  draft: ScopeDraft;
  origins: Record<string, string>;
  metrics: { key: string; name: string; dimensions: string[] }[];
  onChange: (draft: ScopeDraft) => void;
  onReset: () => void;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const [filterError, setFilterError] = useState<string | null>(null);
  useEffect(() => setFilterError(null), [draft.filterText]);
  const metric = metrics.find((item) => item.key === draft.metricKey);
  const allowed = metric?.dimensions ?? [];
  return (
    <section className={`conversation-panel ${collapsed ? "collapsed" : ""}`} aria-label="分析范围">
      <div className="card-head">
        <h2>分析范围</h2>
        <button type="button" className="ghost small" data-testid="scope-toggle" onClick={onToggle}>
          {collapsed ? "展开" : "收起"}
        </button>
      </div>
      {collapsed ? (
        <p className="muted small" data-testid="scope-summary">
          {draft.metricKey || "未选指标"} · {draft.currentStart || "?"} → {draft.currentEnd || "?"}
          {draft.filterText ? ` · ${draft.filterText}` : ""}
        </p>
      ) : (
        <div className="stack">
          <label className="form-row">
            <span>指标</span>
            <select
              data-testid="scope-metric"
              value={draft.metricKey}
              onChange={(event) => onChange({ ...draft, metricKey: event.target.value, dimensions: [] })}
            >
              <option value="">（继承/未选）</option>
              {metrics.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.name}（{item.key}）
                </option>
              ))}
            </select>
            <OriginTag origin={origins.metric_key} />
          </label>
          <div className="form-row">
            <span>基准</span>
            <input
              type="date"
              data-testid="scope-baseline-start"
              value={draft.baselineStart}
              onChange={(event) => onChange({ ...draft, baselineStart: event.target.value })}
            />
            <input
              type="date"
              data-testid="scope-baseline-end"
              value={draft.baselineEnd}
              onChange={(event) => onChange({ ...draft, baselineEnd: event.target.value })}
            />
            <OriginTag origin={origins.baseline_start} />
          </div>
          <div className="form-row">
            <span>当前</span>
            <input
              type="date"
              data-testid="scope-current-start"
              value={draft.currentStart}
              onChange={(event) => onChange({ ...draft, currentStart: event.target.value })}
            />
            <input
              type="date"
              data-testid="scope-current-end"
              value={draft.currentEnd}
              onChange={(event) => onChange({ ...draft, currentEnd: event.target.value })}
            />
            <OriginTag origin={origins.current_start} />
          </div>
          <div className="form-row">
            <span>维度</span>
            <div className="chips">
              {allowed.map((dimension) => (
                <label key={dimension} className="chip" data-testid={`scope-dimension-${dimension}`}>
                  <input
                    type="checkbox"
                    checked={draft.dimensions.includes(dimension)}
                    onChange={(event) =>
                      onChange({
                        ...draft,
                        dimensions: event.target.checked
                          ? [...draft.dimensions, dimension].slice(0, 3)
                          : draft.dimensions.filter((item) => item !== dimension),
                      })
                    }
                  />
                  {dimension}
                </label>
              ))}
              {allowed.length === 0 ? <span className="muted small">先选指标</span> : null}
            </div>
          </div>
          <div className="form-row">
            <span>筛选</span>
            <input
              placeholder="platform=android（留空表示清除）"
              data-testid="scope-filters"
              value={draft.filterText}
              aria-invalid={Boolean(filterError)}
              onChange={(event) => onChange({ ...draft, filterText: event.target.value })}
              onBlur={() => {
                try {
                  parseFilters(draft.filterText);
                  setFilterError(null);
                } catch (failure) {
                  setFilterError((failure as Error).message);
                }
              }}
            />
          </div>
          {filterError ? <p className="notice error" role="alert">{filterError}</p> : null}
          <div className="form-row">
            <span>完整性</span>
            <select
              data-testid="scope-completeness"
              value={draft.dataCompleteState}
              onChange={(event) => onChange({ ...draft, dataCompleteState: event.target.value })}
            >
              <option value="unknown">未知</option>
              <option value="complete">完整</option>
              <option value="incomplete">已知不完整</option>
            </select>
          </div>
          <div className="row-actions">
            <button type="button" className="ghost small" data-testid="scope-reset" onClick={onReset}>
              恢复已确认范围
            </button>
          </div>
          <p className="muted small">
            这里的选择会作为本轮补丁提交；留空表示继承上一轮，筛选留空表示清除。
          </p>
        </div>
      )}
    </section>
  );
}

function OriginTag({ origin }: { origin?: string }) {
  if (!origin) return null;
  const label =
    origin === "ui" ? "本轮指定" : origin === "model" ? "模型解析" : origin === "inherited" ? "继承" : origin;
  return <span className="tag">{label}</span>;
}

export function parseFilters(value: string): Record<string, string> {
  if (!value.trim()) return {};
  const entries = value.split(",").map((part) => {
    const separator = part.indexOf("=");
    const key = part.slice(0, separator).trim();
    const raw = part.slice(separator + 1).trim();
    if (separator < 1 || !key || !raw || raw.includes("=")) {
      throw new Error("筛选格式应为 key=value，多个条件用逗号分隔；留空表示清除。");
    }
    return [key, raw] as const;
  });
  if (new Set(entries.map(([key]) => key)).size !== entries.length) {
    throw new Error("筛选条件不能重复指定同一个字段。");
  }
  return Object.fromEntries(entries);
}

/** Skill picker: the session's mode plus a per-turn manual selection (max 3). */
export function SkillPicker({
  skills,
  mode,
  selected,
  onModeChange,
  onSelectedChange,
  onBind,
  busy,
}: {
  skills: SkillSummary[];
  mode: "off" | "manual" | "auto";
  selected: string[];
  onModeChange: (mode: "off" | "manual" | "auto") => void;
  onSelectedChange: (versionIds: string[]) => void;
  onBind: () => void;
  busy: boolean;
}) {
  return (
    <section className="conversation-panel" aria-label="技能">
      <div className="card-head">
        <h2>技能</h2>
        <select
          data-testid="skill-mode"
          value={mode}
          disabled={busy}
          onChange={(event) => onModeChange(event.target.value as "off" | "manual" | "auto")}
        >
          <option value="auto">自动匹配</option>
          <option value="manual">手动选择</option>
          <option value="off">不使用</option>
        </select>
      </div>
      {mode !== "off" ? (
        <ul className="skill-list compact" data-testid="skill-picker">
          {skills
            .filter((skill) => skill.enabled && !skill.archived_at)
            .flatMap((skill) => (skill.versions.length
              ? skill.versions
              : skill.latest_version ? [skill.latest_version] : []).map((version) => {
              const versionId = version.id;
              const checked = selected.includes(versionId);
              return (
                <li key={versionId}>
                  <label className="chip">
                    <input
                      type="checkbox"
                      data-testid={`skill-pick-${versionId}`}
                      checked={checked}
                      disabled={busy || (!checked && selected.length >= 3)}
                      onChange={(event) =>
                        onSelectedChange(
                          event.target.checked
                            ? [...selected, versionId].slice(0, 3)
                            : selected.filter((item) => item !== versionId),
                        )
                      }
                    />
                    {skill.slug}
                    <span className="muted small"> v{version.version}{skill.source === "upload" ? " · 自建" : ""}</span>
                  </label>
                </li>
              );
            }))}
          <li className="muted small">最多选择 3 个固定版本；新版本不会自动替换已绑定版本。</li>
        </ul>
      ) : (
        <p className="muted small">
          本轮不加载任何技能。
        </p>
      )}
      {mode !== "off" ? <p className="muted small">{mode === "auto"
        ? "自动匹配内置技能及已保存到本会话的自建版本。选择后点击保存，让自建技能进入后续匹配。"
        : "所选版本直接用于本轮；保存后可在后续对话继续使用。"}</p> : null}
      <button type="button" className="ghost small" data-testid="skill-bind" disabled={busy} onClick={onBind}>
        保存到本会话
      </button>
    </section>
  );
}

/** The per-turn patch: only fields the user actually changed are sent. */
export function scopePatchFromDraft(draft: ScopeDraft, baseline: ScopeDraft): TurnScopePatch | null {
  const patch: TurnScopePatch = {};
  const filters = parseFilters(draft.filterText);
  if (draft.metricKey && draft.metricKey !== baseline.metricKey) patch.metric_key = draft.metricKey;
  for (const field of ["baselineStart", "baselineEnd", "currentStart", "currentEnd"] as const) {
    const key = field.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`) as
      | "baseline_start"
      | "baseline_end"
      | "current_start"
      | "current_end";
    if (draft[field] && draft[field] !== baseline[field]) patch[key] = draft[field];
  }
  if (draft.dimensions.join(",") !== baseline.dimensions.join(",")) {
    patch.dimensions = draft.dimensions;
  }
  const filtersChanged =
    JSON.stringify(filters) !== JSON.stringify(baseline.filters);
  if (filtersChanged) {
    patch.filters = filters;
    if (Object.keys(filters).length === 0 && Object.keys(baseline.filters).length > 0) {
      patch.clear = ["filters"];
    }
  }
  if (draft.dataCompleteState !== baseline.dataCompleteState) {
    patch.data_complete_state = draft.dataCompleteState;
  }
  return Object.keys(patch).length > 0 ? patch : null;
}

export function draftFromContext(context: Record<string, unknown>): ScopeDraft {
  const metricKey = String(context.metric_key ?? "");
  return {
    metricKey,
    baselineStart: String(context.baseline_start ?? ""),
    baselineEnd: String(context.baseline_end ?? ""),
    currentStart: String(context.current_start ?? ""),
    currentEnd: String(context.current_end ?? ""),
    dimensions: Array.isArray(context.dimensions) ? (context.dimensions as string[]) : [],
    filters: (context.filters as Record<string, string>) ?? {},
    filterText: Object.entries((context.filters as Record<string, string>) ?? {}).map(([key, value]) => `${key}=${value}`).join(","),
    dataCompleteState: String(context.data_complete_state ?? "unknown"),
  };
}
