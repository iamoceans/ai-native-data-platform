import { ErrorMessage } from "../components/ErrorMessage";
import { t, useLocale, formatDate } from "../i18n";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
  me,
  listSessionMessagePage,
  retryTurn,
  listSessionPage,
  listSkills,
  patchSession,
  type ConversationMessage,
  type SkillSummary,
  type TurnDetail,
  type AnalysisDetail,
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
// A conversation started from the landing page is titled with its question, so
// the rail reads as history instead of "New conversation" rows.
const SESSION_TITLE_LIMIT = 60;

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
  const [includeArchived, setIncludeArchived] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 820px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 820px)");
    const change = () => setNarrow(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useEffect(() => setDrawerOpen(false), [routeSessionId]);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  const sessionId = routeSessionId ?? null;
  const currentSessionRef = useRef(sessionId);
  currentSessionRef.current = sessionId;

  const metrics = useQuery({ queryKey: ["metrics"], queryFn: listMetrics });
  const profile = useQuery({ queryKey: ["me"], queryFn: me });
  const skills = useQuery({
    queryKey: ["skills"],
    queryFn: () => listSkills(false),
  });
  const sessions = useInfiniteQuery({
    queryKey: ["sessions", includeArchived],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => listSessionPage(includeArchived, pageParam),
    getNextPageParam: page => page.next_cursor || undefined,
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

  const messages = useInfiniteQuery({
    queryKey: ["session-messages", sessionId],
    queryFn: ({ pageParam }) => listSessionMessagePage(sessionId as string, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: Boolean(sessionId),
    // Fast while a turn runs, slow otherwise (another tab may still be active).
    refetchInterval: session.data?.active_turn_id ? 4000 : 15000,
  });
  const visibleMessages = useMemo(() => {
    const unique = new Map<string, ConversationMessage>();
    for (const page of [...(messages.data?.pages ?? [])].reverse())
      for (const message of page.items) unique.set(message.id, message);
    return [...unique.values()].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
  }, [messages.data]);

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

  // Landing on /ask with no session used to leave the composer typing-enabled
  // but its send button permanently disabled, with nothing saying why. Sending
  // a question now opens the conversation it belongs to instead.
  const startConversation = useMutation({
    mutationFn: (question: string) =>
      createAgentSession(question.slice(0, SESSION_TITLE_LIMIT)),
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

  // `scopePatchFromDraft` validates the filter text and throws on malformed
  // input, so it must be evaluated inside the caller's try/catch: escaping a
  // React event handler turns a typo into an uncaught page error.
  const submitTurn = (
    question: string,
    targetSessionId: string,
    scope: ScopeDraft,
    patch: ReturnType<typeof scopePatchFromDraft> | null,
    contextVersion: number,
  ) => {
    send.mutate({
      sessionId: targetSessionId,
      text: question,
      scope: { ...scope, dimensions: [...scope.dimensions] },
      patch,
      contextVersion,
      skillSelection: { mode: skillMode, version_ids: [...pickedSkills] },
      idempotencyKey: crypto.randomUUID(),
    });
  };

  const onSend = () => {
    const question = text.trim();
    if (
      !question ||
      session.data?.archived_at ||
      activeTurnId ||
      send.isPending ||
      legacySubmit.isPending ||
      bindSkills.isPending ||
      startConversation.isPending
    )
      return;
    try {
      if (!sessionId) {
        // The URL carries no conversation yet: open one titled after the
        // question and ask in it, so the first question of a visit is one click.
        startConversation.mutate(question, {
          onSuccess: (created) => {
            navigate(`/ask/${created.id}`);
            submitTurn(
              question,
              created.id,
              draftFromContext({}),
              null,
              created.context_version,
            );
          },
        });
        return;
      }
      if (!session.data) return;
      const scope = draft ?? confirmed;
      submitTurn(
        question,
        sessionId,
        scope,
        draft ? scopePatchFromDraft(draft, confirmed) : null,
        session.data.context_version,
      );
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

  const retry = useMutation({
    mutationFn: ({ origin, turnId, key }: { origin: string; turnId: string; key: string }) => retryTurn(origin, turnId, key),
    retry: false,
    onSuccess: (_turn, { origin }) => {
      queryClient.invalidateQueries({ queryKey: ["session", origin] });
      queryClient.invalidateQueries({ queryKey: ["session-messages", origin] });
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (origin === currentSessionRef.current) setError(null);
    },
    onError: (failure, { origin }) => {
      if (origin === currentSessionRef.current && failure instanceof ApiError) setError(failure);
    },
  });

  const busy =
    Boolean(activeTurnId) ||
    send.isPending ||
    legacySubmit.isPending ||
    bindSkills.isPending ||
    startConversation.isPending || retry.isPending;
  // A session in the URL that has not loaded yet is the only reason to hold the
  // send button back; without a session, sending opens one (see onSend).
  const sendBlocked = busy || Boolean(session.data?.archived_at) || !text.trim() || (Boolean(sessionId) && !session.data);
  const turn = activeTurn.data ?? null;

  return (
    <div className="conversation">
      <SessionList
        canManage={profile.data?.capabilities.includes("analysis.create") ?? false}
        includeArchived={includeArchived}
        onIncludeArchived={setIncludeArchived}
        sessions={[...new Map((sessions.data?.pages.flatMap(page => page.items) ?? []).map(item => [item.id, item])).values()]}
        hasMore={sessions.hasNextPage}
        loadingMore={sessions.isFetchingNextPage}
        loading={sessions.isPending}
        loadError={sessions.error}
        onLoadMore={() => sessions.fetchNextPage()}
        onReload={() => sessions.refetch()}
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
            aria-expanded={narrow ? drawerOpen : !scopeCollapsed}
            aria-controls={narrow ? "scope-drawer" : "scope-sidebar"}
            onClick={() => {
              if (narrow) { setScopeCollapsed(false); setDrawerOpen(true); }
              else setScopeCollapsed((value) => !value);
            }}
          >
            {narrow ? t("范围与技能") : scopeCollapsed ? t("显示范围面板") : t("隐藏范围面板")}
          </button>
        </header>

        <div className="conversation-body">
          <section
            className="thread"
            aria-label={t("消息")}
            data-testid="conversation-thread"
          >
            {messages.hasNextPage ? (
              <button className="ghost" data-testid="load-older-messages" disabled={messages.isFetching} onClick={() => messages.fetchNextPage()}>
                {messages.isFetchingNextPage ? t("正在加载…") : t("加载更早消息")}
              </button>
            ) : null}
            {messages.error ? <p className="notice error" role="alert"><ErrorMessage error={messages.error} /></p> : null}
            {visibleMessages.map((message) => (
              <MessageRow key={message.id} message={message} busy={busy} onRetry={(turnId) => {
                if (sessionId && !busy) retry.mutate({ origin: sessionId, turnId, key: crypto.randomUUID() });
              }} />
            ))}
            {turn ? <TurnProgress turn={turn} /> : null}
            {visibleMessages.length === 0 && !turn ? (
              <p className="muted" data-testid="conversation-empty">
                {sessionId
                  ? t(
                      "用自然语言提问。缺少指标或期间时我会先问清楚，不会替你默认。",
                    )
                  : t("直接提问就会开启一个新对话；也可以在左侧选择已有会话。")}
              </p>
            ) : null}
          </section>

          <div className="composer">
            {session.data?.archived_at ? <p className="notice">{t("会话已归档，恢复后可继续提问。")}</p> : null}
            <textarea
              data-testid="conversation-input"
              rows={3}
              placeholder={t("例如：上周收入为什么下降？")}
              value={text}
              disabled={busy || Boolean(session.data?.archived_at)}
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
                disabled={sendBlocked}
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

      <ScopeSidebar narrow={narrow} open={drawerOpen} onClose={() => setDrawerOpen(false)}>
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
      </ScopeSidebar>
    </div>
  );
}

function ScopeSidebar({ narrow, open, onClose, children }: { narrow: boolean; open: boolean; onClose: () => void; children: ReactNode }) {
  useLocale();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (!narrow || !element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
    const previousOverflow = document.body.style.overflow;
    if (open) document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      if (element.open) element.close();
    };
  }, [narrow, open]);
  if (!narrow) return <div id="scope-sidebar" className="conversation-side">{children}</div>;
  return (
    <dialog ref={dialog} id="scope-drawer" className="scope-drawer" aria-labelledby="scope-drawer-title" onCancel={onClose} onClose={onClose}>
      <header className="card-head">
        <h2 id="scope-drawer-title">{t("范围与技能")}</h2>
        <button type="button" className="ghost small" data-testid="scope-drawer-close" onClick={onClose}>{t("关闭")}</button>
      </header>
      <div className="conversation-side">{children}</div>
    </dialog>
  );
}

function AnalysisDiagnostics({ analysis }: { analysis: AnalysisDetail }) {
  useLocale();
  const budget = analysis.budget ?? {};
  const amount = (key: string) => typeof budget[key] === "number" ? String(budget[key]) : "—";
  return (
    <details data-testid="analysis-diagnostics">
      <summary>{t("本轮诊断")}</summary>
      <dl className="kv">
        <dt>{t("输入 tokens")}</dt><dd>{amount("input_tokens")} / {amount("max_input_tokens")}</dd>
        <dt>{t("输出 tokens")}</dt><dd>{amount("output_tokens")} / {amount("max_output_tokens")}</dd>
        <dt>{t("查询预算")}</dt><dd>{amount("queries")} / {amount("max_queries")}</dd>
        <dt>{t("工具调用预算")}</dt><dd>{amount("tool_calls")} / {amount("max_tool_calls")}</dd>
        <dt>{t("SQL 修复预算")}</dt><dd>{amount("sql_repairs")} / {amount("max_sql_repairs")}</dd>
        {typeof analysis.state?.model_id === "string" ? <><dt>{t("模型")}</dt><dd>{analysis.state.model_id}</dd></> : null}
        {typeof budget.deadline_at === "string" ? <><dt>{t("截止时间")}</dt><dd>{formatDate(budget.deadline_at, true)}</dd></> : null}
      </dl>
    </details>
  );
}

/** One committed message; the shape follows `content.kind`. */
function MessageRow({ message, busy, onRetry }: { message: ConversationMessage; busy: boolean; onRetry: (turnId: string) => void }) {
  useLocale();
  const content = message.content;
  const kind = String(
    content.kind ?? (message.role === "user" ? "user" : "unknown"),
  );
  const text = String(content.text ?? "");
  const analysisId = content.analysis_id ? String(content.analysis_id) : null;
  const isResult = kind === "analysis_result";
  const claims =
    (content.claims as { id: string; text: string }[] | undefined) ?? [];
  const limitations = Array.isArray(content.limitations)
    ? (content.limitations as string[])
    : [];
  const analysis = useQuery({
    queryKey: ["analysis", analysisId],
    queryFn: () => getAnalysis(analysisId as string),
    enabled: Boolean(analysisId) && (isResult || kind === "failure"),
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
      className={`message${message.role === "user" ? " user" : ""}`}
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
        <div className="message-body">
          <p className="message-text">{text}</p>
          <MetricCatalog metrics={content.metrics} />
        </div>
      ) : null}
      {kind === "failure" ? (
        <p className="notice error">
          <strong>{String(content.code ?? "FAILED")}</strong> —{" "}
          <ErrorMessage error={{ code: String(content.code ?? "FAILED"), message: String(content.message ?? "") }} />
        </p>
      ) : null}
      {message.role === "user" && message.turn_id && ["FAILED", "CANCELLED"].includes(message.turn_status ?? "") ? (
        <button className="ghost small" data-testid="retry-turn" disabled={busy} onClick={() => onRetry(message.turn_id as string)}>{t("重试本轮")}</button>
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
          {claims.length ? (
            <ul className="claims" data-testid="result-claims">
              {claims.map((claim) => (
                <li key={claim.id}>{claim.text}</li>
              ))}
            </ul>
          ) : null}
          {!claims.length && limitations.length ? (
            <div data-testid="result-no-claims">
              <p className="muted small">{t("本轮没有可发布的结论，原因：")}</p>
              <ul className="claims">
                {limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {claims.length && limitations.length ? (
            <details>
              <summary>{t("限制与口径")}</summary>
              <ul>
                {limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </details>
          ) : null}
          {chart.data ? <ChartPanel key={chart.data.id} chart={chart.data} /> : null}
          <SkillUsage
            snapshot={content.skills as Record<string, unknown> | undefined}
          />
        </div>
      ) : null}
      {analysis.data ? <AnalysisDiagnostics analysis={analysis.data} /> : null}
      {message.content_schema_version === 0 ? (
        <p className="muted small">{t("（历史消息，按兼容格式显示）")}</p>
      ) : null}
    </article>
  );
}

/** The registry as the platform reports it: keys, names, units, dimensions. */
function MetricCatalog({ metrics }: { metrics?: unknown }) {
  useLocale();
  const items = Array.isArray(metrics)
    ? (metrics as {
        metric_key?: unknown;
        name?: unknown;
        unit?: unknown;
        dimensions?: unknown;
      }[])
    : [];
  if (!items.length) return null;
  return (
    <ul className="metric-catalog" data-testid="message-metrics">
      {items.map((item) => (
        <li key={String(item.metric_key)}>
          <span className="tag">{String(item.metric_key)}</span>
          <span>{String(item.name ?? "")}</span>
          {item.unit ? (
            <span className="muted small">{String(item.unit)}</span>
          ) : null}
          {Array.isArray(item.dimensions) && item.dimensions.length ? (
            <span className="muted small">
              {t("维度：")}
              {(item.dimensions as string[]).join("、")}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** Templates use bound artifacts; model prose carries no figure. */
function ExplanationBody({ content }: { content: Record<string, unknown> }) {
  useLocale();
  const quotes =
    (content.quotes as {
      claim_id: string;
      text: string;
      analysis_id: string;
      explanation?: string;
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
              {quote.explanation ? <p className="muted small">{quote.explanation}</p> : null}
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
    <article className="message progress" data-testid="turn-progress">
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
