import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  cancelQuery,
  getQuery,
  getQueryResults,
  listDatasources,
  submitQuery,
  type QueryDetail,
  type ResultPayload,
} from "../api/endpoints";
import { ResultTable } from "../components/ResultTable";
import { StatusBadge } from "../components/StatusBadge";
import { useQueryEvents } from "../hooks/useQueryEvents";

const TERMINAL = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST"]);

export function SqlWorkspacePage() {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [datasourceId, setDatasourceId] = useState("");
  const [sql, setSql] = useState("");
  const [maxRows, setMaxRows] = useState(1000);
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [queryId, setQueryId] = useState<string | null>(params.get("query"));
  const [submitError, setSubmitError] = useState<{ code: string; message: string } | null>(null);
  const [resultError, setResultError] = useState<{ code: string; message: string } | null>(null);
  const [pages, setPages] = useState<ResultPayload[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);

  const datasources = useQuery({ queryKey: ["datasources"], queryFn: listDatasources });
  const detail = useQuery({
    queryKey: ["query", queryId],
    queryFn: () => getQuery(queryId as string),
    enabled: Boolean(queryId),
    refetchInterval: (query) => {
      const data = query.state.data as QueryDetail | undefined;
      if (!data) return 1000;
      return TERMINAL.has(data.status) ? false : 1000;
    },
  });

  useQueryEvents(
    queryId,
    () => {
      queryClient.invalidateQueries({ queryKey: ["query", queryId] });
    },
    Boolean(queryId),
  );

  // Load the first result page once a query succeeds.
  useEffect(() => {
    if (!queryId) return;
    const status = detail.data?.status;
    if (status !== "SUCCEEDED") {
      setPages([]);
      setCursor(null);
      setResultError(null);
      return;
    }
    let cancelled = false;
    getQueryResults(queryId)
      .then((response) => {
        if (cancelled) return;
        if (response.result) {
          setPages([response.result]);
          setCursor(response.result.next_cursor ?? null);
          setResultError(null);
        }
      })
      .catch((error) => {
        if (cancelled) return;
        setResultError(
          error instanceof ApiError
            ? { code: error.code, message: error.message }
            : { code: "UNKNOWN", message: "result loading failed" },
        );
      });
    return () => {
      cancelled = true;
    };
  }, [queryId, detail.data?.status, detail.data?.finished_at]);

  const submit = useMutation({
    mutationFn: () =>
      submitQuery({ datasourceId, sql, parameters: {}, maxRows, timeoutSeconds }),
    onSuccess: (response) => {
      setSubmitError(null);
      setPages([]);
      setCursor(null);
      setQueryId(response.query_id);
      setParams({ query: response.query_id });
    },
    onError: (error) => {
      setSubmitError(
        error instanceof ApiError
          ? { code: error.code, message: error.message }
          : { code: "UNKNOWN", message: "submit failed" },
      );
    },
  });

  const cancel = useMutation({
    mutationFn: () => cancelQuery(queryId as string),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["query", queryId] }),
  });

  const rows = useMemo(() => pages.flatMap((page) => page.rows), [pages]);
  const columns = pages[0]?.columns ?? [];
  const truncated = pages.some((page) => page.truncated);

  async function loadMore() {
    if (!queryId || !cursor) return;
    const response = await getQueryResults(queryId, cursor);
    if (response.result) {
      setPages((previous) => [...previous, response.result as ResultPayload]);
      setCursor(response.result.next_cursor ?? null);
    }
  }

  const running = detail.data ? !TERMINAL.has(detail.data.status) : false;

  return (
    <div className="workspace">
      <section className="card">
        <div className="card-head">
          <h1>SQL workspace</h1>
          <span className="muted small">
            SELECT only · one statement · server-enforced timeouts and row limits
          </span>
        </div>
        <div className="form-row">
          <label className="grow">
            Data source
            <select
              value={datasourceId}
              onChange={(event) => setDatasourceId(event.target.value)}
              data-testid="datasource-select"
            >
              <option value="">Choose a data source…</option>
              {(datasources.data ?? []).map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name} ({source.kind})
                </option>
              ))}
            </select>
          </label>
          <label>
            Max rows
            <input
              type="number"
              min={1}
              max={10000}
              value={maxRows}
              onChange={(event) => setMaxRows(Number(event.target.value))}
              data-testid="max-rows"
            />
          </label>
          <label>
            Timeout (s)
            <input
              type="number"
              min={1}
              max={120}
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
              data-testid="timeout"
            />
          </label>
        </div>
        <textarea
          className="sql-editor"
          value={sql}
          onChange={(event) => setSql(event.target.value)}
          placeholder={"SELECT dt, SUM(revenue_usd) AS revenue\nFROM your_table\nWHERE dt >= :start\nGROUP BY dt ORDER BY dt"}
          spellCheck={false}
          data-testid="sql-editor"
        />
        <div className="form-row">
          <button
            className="primary"
            disabled={!datasourceId || !sql.trim() || submit.isPending}
            onClick={() => submit.mutate()}
            data-testid="run-query"
          >
            {submit.isPending ? "Submitting…" : "Run query"}
          </button>
          {queryId ? (
            <button className="ghost" disabled={!running || cancel.isPending} onClick={() => cancel.mutate()}>
              {running ? "Cancel" : "Cancel (finished)"}
            </button>
          ) : null}
          <span className="muted small">Idempotency-Key is applied per submission.</span>
        </div>
        {submitError ? (
          <p className="notice error" data-testid="submit-error">
            <strong>{submitError.code}</strong> — {submitError.message}
          </p>
        ) : null}
      </section>

      {queryId && detail.data ? (
        <section className="card">
          <div className="card-head">
            <h2>
              Query <code className="mono-id">{queryId.slice(0, 8)}</code>{" "}
              <StatusBadge status={detail.data.status} />
            </h2>
            <span className="muted small">
              created {new Date(detail.data.created_at).toLocaleString()} · attempt {detail.data.attempt}
            </span>
          </div>
          {detail.data.error ? (
            <p className="notice error" data-testid="query-error">
              <strong>{detail.data.error.code}</strong> — {detail.data.error.message}
            </p>
          ) : null}
          <details className="evidence">
            <summary>Validated SQL (what the engine executed)</summary>
            <pre>{detail.data.validated_sql ?? "—"}</pre>
          </details>
          {detail.data.status === "SUCCEEDED" ? (
            <>
              {resultError ? (
                <p className="notice error" data-testid="result-error">
                  <strong>{resultError.code}</strong> — {resultError.message}
                </p>
              ) : null}
              <ResultTable columns={columns} rows={rows} truncated={truncated} />
              {cursor ? (
                <button className="ghost" onClick={loadMore}>
                  Load more ({rows.length} rows shown)
                </button>
              ) : null}
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
