import { ErrorMessage } from "../components/ErrorMessage";
import { t, useLocale, formatDate } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { analysisPollInterval } from "../api/polling";
import {
  cancelTurn,
  createAgentSession,
  createAnalysis,
  createTurn,
  getAnalysis,
  getChart,
  getSession,
  getTurn,
  listMetrics,
  listSessionMessages,
  listSessions,
  listSkills,
  patchSession,
  type ConversationMessage,
  type SkillSummary,
  type TurnDetail,
} from "../api/endpoints";
import { ChartPanel } from "../components/ChartPanel";
import {
  draftFromContext,
  parseFilters,
  ScopePanel,
  scopePatchFromDraft,
  SessionList,
  SkillPicker,
  type ScopeDraft,
} from "../components/ConversationPanels";
import { MemoryPanel } from "../components/MemoryPanel";
import { StatusBadge } from "../components/StatusBadge";
import { useSessionEvents } from "../hooks/useSessionEvents";

const ACTIVE_TURN_STATES = ["QUEUED", "RESOLVING", "ANALYZING", "RESPONDING"];

/**
 * Askdata: a conversation over the governed analysis pipeline.
 *
 * The transcript is the record; live progress is rendered from the active turn
 * and its analysis, never inserted as extra chat messages (design 5.2). A new
 * conversation is created on demand and the URL carries the session, so a
 * refresh resumes exactly where the user was. When the conversational flag is
 * off the same question falls back to the one-shot analysis path.
 */
export function AskPage() {
  useLocale();
  const { sessionId: routeSessionId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [edits, setEdits] = useState<{
    sessionId: string | null;
    fields: Partial<ScopeDraft>;
  } | null>(null);
  const [skillsEdited, setSkillsEdited] = useState(false);
  const [skillMode, setSkillMode] = useState<"off" | "manual" | "auto">("auto");
  const [pickedSkills, setPickedSkills] = useState<string[]>([]);
  const [scopeCollapsed, setScopeCollapsed] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  const sessionId = routeSessionId ?? null;
  const currentSessionRef = useRef(sessionId);
  currentSessionRef.current = sessionId;

  const metrics = useQuery({ queryKey: ["metrics"], queryFn: listMetrics });
  const skills = useQuery({
    queryKey: ["skills"],
    queryFn: () => listSkills(false),
  });
  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: () => listSessions(false),
  });

  // The session query polls while a turn is active: without it a missed SSE
  // event would leave the composer disabled forever, because `active_turn_id`
  // is what decides whether the user may send.
  const session = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => getSession(sessionId as string),
    enabled: Boolean(sessionId),
    refetchInterval: (query) =>
      query.state.data?.active_turn_id ? 2000 : 15000,
  });

  const messages = useQuery({
    queryKey: ["session-messages", sessionId],
    queryFn: () => listSessionMessages(sessionId as string),
    enabled: Boolean(sessionId),
    // Fast while a turn runs, slow otherwise (another tab may still be active).
    refetchInterval: (query) =>
      query.state.data?.some(
        (item) => item.turn_id === session.data?.active_turn_id,
      )
        ? 4000
        : 15000,
  });

  const activeTurnId = session.data?.active_turn_id ?? null;
  const activeTurn = useQuery({
    queryKey: ["turn", sessionId, activeTurnId],
    queryFn: () => getTurn(sessionId as string, activeTurnId as string),
    enabled: Boolean(sessionId && activeTurnId),
    refetchInterval: 1500,
  });

  // Live updates: an event only triggers a refetch, so a dropped stream degrades
  // to the polling above instead of showing a stale conversation.
  const { connected } = useSessionEvents(
    sessionId,
    (event) => {
      if (event.type === "stream.end") return;
      queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      queryClient.invalidateQueries({
        queryKey: ["session-messages", sessionId],
      });
      queryClient.invalidateQueries({ queryKey: ["turn", sessionId] });
    },
    Boolean(sessionId && activeTurnId),
    activeTurnId,
  );

  const confirmed = useMemo(
    () => draftFromContext(session.data?.resolved_context ?? {}),
    [session.data?.resolved_context],
  );
  // Only user edits are stored. Every untouched field follows the live confirmed
  // context, including updates that arrive after createTurn has returned.
  const draft = session.data
    ? {
        ...confirmed,
        ...(edits?.sessionId === sessionId ? edits.fields : {}),
      }
    : null;
  const setDraft = (next: ScopeDraft | null) => {
    if (!next || !draft) {
      setEdits(null);
      return;
    }
    const fields = { ...(edits?.sessionId === sessionId ? edits.fields : {}) };
    for (const key of Object.keys(next) as (keyof ScopeDraft)[]) {
      if (JSON.stringify(next[key]) === JSON.stringify(draft[key])) continue;
      if (JSON.stringify(next[key]) === JSON.stringify(confirmed[key])) {
        delete fields[key];
      } else {
        Object.assign(fields, { [key]: next[key] });
      }
    }
    setEdits({ sessionId, fields });
  };
  useEffect(() => {
    setEdits(null);
    setText("");
    setError(null);
    setSkillsEdited(false);
    setSkillMode("auto");
    setPickedSkills([]);
  }, [sessionId]);
  useEffect(() => {
    if (!session.data || skillsEdited) return;
    const config = session.data.skill_config;
    setSkillMode(
      config.mode === "manual" || config.mode === "off" ? config.mode : "auto",
    );
    setPickedSkills(
      Array.isArray(config.version_ids)
        ? config.version_ids.filter(
            (id): id is string => typeof id === "string",
          )
        : [],
    );
  }, [session.data, skillsEdited]);

  const metricOptions = useMemo(
    () =>
      (metrics.data ?? []).map((metric) => ({
        key: metric.metric_key,
        name: metric.name,
        dimensions: metric.allowed_dimensions ?? [],
      })),
    [metrics.data],
  );

  const newConversation = useMutation({
    mutationFn: () => createAgentSession(t("新对话")),
    onSuccess: (created) => {
      setDraft(null);
      setText("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      navigate(`/ask/${created.id}`);
    },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure : null),
  });

  type Submission = {
    sessionId: string;
    text: string;
    scope: ScopeDraft;
    patch: ReturnType<typeof scopePatchFromDraft> | null;
    contextVersion: number;
    skillSelection: { mode: "off" | "manual" | "auto"; version_ids: string[] };
    idempotencyKey: string;
  };
  const send = useMutation({
    mutationFn: (submission: Submission) => {
      return createTurn(submission.sessionId, {
        text: submission.text,
        idempotencyKey: submission.idempotencyKey,
        expectedContextVersion: submission.contextVersion,
        scopePatch: submission.patch,
        skillSelection: submission.skillSelection,
      });
    },
    onSuccess: (_created, { sessionId: originSessionId }) => {
      if (originSessionId === currentSessionRef.current) {
        setText("");
        setError(null);
        setDraft(null);
      }
      queryClient.invalidateQueries({ queryKey: ["session", originSessionId] });
      queryClient.invalidateQueries({
        queryKey: ["session-messages", originSessionId],
      });
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (failure, submission) => {
      const originSessionId = submission.sessionId;
      if (originSessionId === currentSessionRef.current) {
        if (
          failure instanceof ApiError &&
          failure.code === "CONVERSATIONAL_DISABLED"
        ) {
          legacySubmit.mutate(submission);
          return;
        }
        setError(
          failure instanceof ApiError
            ? failure
            : new ApiError(400, "INVALID_SCOPE", failure.message, false, {}),
        );
      }
      queryClient.invalidateQueries({ queryKey: ["session", originSessionId] });
    },
  });

  const bindSkills = useMutation({
    mutationFn: (originSessionId: string) =>
      patchSession(originSessionId, {
        expected_context_version: session.data?.context_version,
        skill_config: { mode: skillMode, version_ids: pickedSkills },
      }),
    onSuccess: (updated, originSessionId) => {
      queryClient.setQueryData(["session", updated.id], updated);
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (originSessionId === currentSessionRef.current) {
        setSkillsEdited(false);
        setError(null);
      }
    },
    onError: (failure, originSessionId) => {
      if (originSessionId === currentSessionRef.current) {
        setError(failure instanceof ApiError ? failure : null);
      }
      queryClient.invalidateQueries({ queryKey: ["session", originSessionId] });
    },
  });

  const stop = useMutation({
    mutationFn: () => cancelTurn(sessionId as string, activeTurnId as string),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      queryClient.invalidateQueries({
        queryKey: ["session-messages", sessionId],
      });
    },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure : null),
  });

  // Legacy fallback: with the conversational flag off the turn API answers 503,
  // so the same question goes through the one-shot analysis path instead.
  const legacySubmit = useMutation({
    retry: false,
    mutationFn: (submission: Submission) => {
      const scope = submission.scope;
      return createAnalysis({
        sessionId: submission.sessionId,
        question: submission.text,
        idempotencyKey: submission.idempotencyKey,
        metricKey: scope.metricKey,
        baselineStart: scope.baselineStart,
        baselineEnd: scope.baselineEnd,
        currentStart: scope.currentStart,
        currentEnd: scope.currentEnd,
        dimensions: scope.dimensions,
        filters: parseFilters(scope.filterText),
        dataComplete: scope.dataCompleteState === "complete",
      });
    },
    onSuccess: (created, submission) => {
      if (submission.sessionId === currentSessionRef.current)
        navigate(`/analyses/${created.analysis_id}`);
    },
    onError: (failure, submission) => {
      if (submission.sessionId === currentSessionRef.current)
        setError(failure instanceof ApiError ? failure : null);
    },
  });

  const onSend = () => {
    if (
      !text.trim() ||
      !sessionId ||
      !session.data ||
      activeTurnId ||
      send.isPending ||
      legacySubmit.isPending ||
      bindSkills.isPending
    )
      return;
    try {
      const scope = draft ?? confirmed;
      send.mutate({
        sessionId,
        text: text.trim(),
        scope: { ...scope, dimensions: [...scope.dimensions] },
        patch: draft ? scopePatchFromDraft(draft, confirmed) : null,
        contextVersion: session.data.context_version,
        skillSelection: { mode: skillMode, version_ids: [...pickedSkills] },
        idempotencyKey: crypto.randomUUID(),
      });
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? failure
          : new ApiError(
              400,
              "INVALID_SCOPE",
              failure instanceof Error ? failure.message : String(failure),
              false,
              {},
            ),
      );
    }
  };

  const busy =
    Boolean(activeTurnId) ||
    send.isPending ||
    legacySubmit.isPending ||
    bindSkills.isPending;
  const turn = activeTurn.data ?? null;

  return (
    <div className="conversation">
      <SessionList
        sessions={sessions.data ?? []}
        currentId={sessionId}
        onCreate={() => newConversation.mutate()}
        onSelect={(id) => {
          setDraft(null);
          setError(null);
          navigate(`/ask/${id}`);
        }}
        busy={newConversation.isPending}
      />

      <main className="conversation-main" aria-label={t("对话")}>
        <header className="conversation-head">
          <div>
            <h1 data-testid="conversation-title">
              {session.data?.title ?? t("新对话")}
            </h1>
            <p className="muted small">
              {connected
                ? t("实时更新已连接")
                : t("实时更新不可用，按轮询刷新")}
              {turn ? t(" · 本轮：{0}", { 0: t(turn.intent ?? "解析中") }) : ""}
            </p>
          </div>
          <button
            type="button"
            className="ghost small"
            data-testid="scope-panel-toggle"
            onClick={() => setScopeCollapsed((value) => !value)}
          >
            {scopeCollapsed ? t("显示范围面板") : t("隐藏范围面板")}
          </button>
        </header>

        <div className="conversation-body">
          <section
            className="thread"
            aria-label={t("消息")}
            data-testid="conversation-thread"
          >
            {(messages.data ?? []).map((message) => (
              <MessageRow key={message.id} message={message} />
            ))}
            {turn ? <TurnProgress turn={turn} /> : null}
            {(messages.data ?? []).length === 0 && !turn ? (
              <p className="muted">
                {t(
                  "用自然语言提问。缺少指标或期间时我会先问清楚，不会替你默认。",
                )}
              </p>
            ) : null}
          </section>

          <div className="composer">
            <textarea
              data-testid="conversation-input"
              rows={3}
              placeholder={t("例如：上周收入为什么下降？")}
              value={text}
              disabled={busy}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey))
                  onSend();
              }}
              ref={composerRef}
            />
            <div className="composer-actions">
              {busy ? (
                <button
                  type="button"
                  className="ghost"
                  data-testid="conversation-stop"
                  disabled={!activeTurnId || stop.isPending}
                  onClick={() => stop.mutate()}
                >
                  {t("停止本轮")}
                </button>
              ) : null}
              <button
                type="button"
                className="primary"
                data-testid="conversation-send"
                disabled={busy || !text.trim() || !sessionId}
                onClick={onSend}
              >
                {busy ? t("等待当前分析完成") : t("发送")}
              </button>
            </div>
            {error ? (
              <p className="notice error" data-testid="conversation-error">
                <strong>{error.code}</strong> — <ErrorMessage error={error} />
              </p>
            ) : null}
          </div>
        </div>
      </main>

      <div className="conversation-side">
        {draft ? (
          <ScopePanel
            draft={draft}
            origins={
              (session.data?.resolved_context?.field_origins as Record<
                string,
                string
              >) ?? {}
            }
            metrics={metricOptions}
            onChange={setDraft}
            onReset={() => setEdits(null)}
            collapsed={scopeCollapsed}
            onToggle={() => setScopeCollapsed((value) => !value)}
          />
        ) : null}
        <SkillPicker
          skills={(skills.data ?? []) as SkillSummary[]}
          mode={skillMode}
          selected={pickedSkills}
          onModeChange={(mode) => {
            setSkillsEdited(true);
            setSkillMode(mode);
          }}
          onSelectedChange={(ids) => {
            setSkillsEdited(true);
            setPickedSkills(ids);
          }}
          onBind={() => {
            if (sessionId) bindSkills.mutate(sessionId);
          }}
          busy={busy || !session.data}
        />
        {draft?.metricKey ? <MemoryPanel metricKey={draft.metricKey} /> : null}
      </div>
    </div>
  );
}

/** One committed message; the shape follows `content.kind`. */
function MessageRow({ message }: { message: ConversationMessage }) {
  useLocale();
  const content = message.content;
  const kind = String(
    content.kind ?? (message.role === "user" ? "user" : "unknown"),
  );
  const text = String(content.text ?? "");
  const analysisId = content.analysis_id ? String(content.analysis_id) : null;
  const isResult = kind === "analysis_result";
  const analysis = useQuery({
    queryKey: ["analysis", analysisId],
    queryFn: () => getAnalysis(analysisId as string),
    enabled: Boolean(analysisId) && isResult,
    refetchInterval: (query) => analysisPollInterval(query.state.data?.status),
  });
  const chartId =
    (analysis.data?.final_report as { chart_ids?: string[] } | undefined)
      ?.chart_ids?.[0] ?? null;
  const chart = useQuery({
    queryKey: ["chart", chartId],
    queryFn: () => getChart(chartId as string),
    enabled: Boolean(chartId),
  });

  return (
    <article
      className={`message ${message.role}`}
      data-testid={`message-${kind}`}
    >
      <header className="message-head">
        <span className="tag">
          {message.role === "user" ? t("你") : t("平台")}
        </span>
        <span className="muted small">
          {formatDate(message.created_at, true)}
        </span>
      </header>

      {kind === "user" ? <p className="message-text">{text}</p> : null}
      {kind === "clarify" ? (
        <div className="message-body">
          <p>{text}</p>
          {Array.isArray(content.missing) &&
          (content.missing as string[]).length ? (
            <p className="muted small">
              {t("缺少：")}
              {(content.missing as string[])
                .map((field) => t(field))
                .join(", ")}
            </p>
          ) : null}
        </div>
      ) : null}
      {kind === "explanation" ? <ExplanationBody content={content} /> : null}
      {kind === "help" || kind === "unsupported" ? (
        <p className="message-text">{text}</p>
      ) : null}
      {kind === "failure" ? (
        <p className="notice error">
          <strong>{String(content.code ?? "FAILED")}</strong> —{" "}
          <ErrorMessage error={{ code: String(content.code ?? "FAILED"), message: String(content.message ?? "") }} />
        </p>
      ) : null}
      {isResult ? (
        <div className="message-body">
          <div className="message-head">
            <StatusBadge status={String(content.status ?? "UNKNOWN")} />
            {content.partial ? (
              <span className="badge warn">{t("部分结果")}</span>
            ) : null}
            {analysisId ? (
              <a
                href={`/analyses/${analysisId}`}
                data-testid="analysis-deeplink"
              >
                {t("查看完整报告")}
              </a>
            ) : null}
          </div>
          <ul className="claims">
            {(
              content.claims as { id: string; text: string }[] | undefined
            )?.map((claim) => (
              <li key={claim.id}>{claim.text}</li>
            ))}
          </ul>
          {Array.isArray(content.limitations) &&
          (content.limitations as string[]).length ? (
            <details>
              <summary>{t("限制与口径")}</summary>
              <ul>
                {(content.limitations as string[]).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </details>
          ) : null}
          {chart.data ? <ChartPanel chart={chart.data} /> : null}
          <SkillUsage
            snapshot={content.skills as Record<string, unknown> | undefined}
          />
        </div>
      ) : null}
      {message.content_schema_version === 0 ? (
        <p className="muted small">{t("（历史消息，按兼容格式显示）")}</p>
      ) : null}
    </article>
  );
}

/** Explanations quote stored claims verbatim; the prose carries no figure. */
function ExplanationBody({ content }: { content: Record<string, unknown> }) {
  useLocale();
  const quotes =
    (content.quotes as {
      claim_id: string;
      text: string;
      analysis_id: string;
    }[]) ?? [];
  const dropped = (content.dropped_references as string[]) ?? [];
  return (
    <div className="message-body">
      {String(content.text ?? "") ? (
        <p className="message-text">{String(content.text)}</p>
      ) : null}
      {quotes.length ? (
        <ul className="quotes" data-testid="explanation-quotes">
          {quotes.map((quote) => (
            <li key={`${quote.analysis_id}-${quote.claim_id}`}>
              <span className="tag">{quote.claim_id}</span>
              <span>{quote.text}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {content.evidence_state === "unavailable" ? (
        <p className="notice warn">
          {t("引用的证据当前不可用，无法展示具体数字。")}
        </p>
      ) : null}
      {dropped.length ? (
        <p className="muted small">
          {t("references.skipped", { count: dropped.length })}
        </p>
      ) : null}
    </div>
  );
}

/** Live progress of the active turn: a status line, not a chat message. */
function TurnProgress({ turn }: { turn: TurnDetail }) {
  useLocale();
  const analysis = useQuery({
    queryKey: ["analysis", turn.analysis_id],
    queryFn: () => getAnalysis(turn.analysis_id as string),
    enabled: Boolean(turn.analysis_id),
    refetchInterval: 1500,
  });
  const steps = analysis.data?.steps ?? [];
  return (
    <article className="message assistant progress" data-testid="turn-progress">
      <header className="message-head">
        <span className="tag">{t("平台")}</span>
        <StatusBadge status={turn.status} />
        <span className="muted small">
          {turn.intent ? t("意图：{0}", { 0: t(turn.intent) }) : t("解析中…")}
        </span>
      </header>
      {steps.length ? (
        <ul className="steps">
          {steps.map((step) => (
            <li key={step.key}>
              <span className="muted small">{step.key}</span>{" "}
              <StatusBadge status={step.status} />
            </li>
          ))}
        </ul>
      ) : null}
      {turn.error ? (
        <p className="notice error">
          <strong>{turn.error.code}</strong> —{" "}
          <ErrorMessage error={turn.error} />
        </p>
      ) : null}
      {ACTIVE_TURN_STATES.includes(turn.status) ? (
        <p className="muted small">
          {t("可以停止本轮；完成后结果会作为一条消息出现。")}
        </p>
      ) : null}
    </article>
  );
}

function SkillUsage({ snapshot }: { snapshot?: Record<string, unknown> }) {
  useLocale();
  const loaded =
    (snapshot?.loaded as { slug: string; version: number }[]) ?? [];
  const mode = snapshot?.mode ? String(snapshot.mode) : null;
  if (!mode) return null;
  const label =
    mode === "off"
      ? t("未使用")
      : loaded.length
        ? loaded.map((item) => `${item.slug} v${item.version}`).join("、")
        : t("无匹配");
  return (
    <p className="muted small" data-testid="skill-usage">
      {t("技能：")}
      {label}
    </p>
  );
}
