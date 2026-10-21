import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { getQueryResults, type ResultPayload } from "../api/endpoints";
import { t, useLocale } from "../i18n";
import { ErrorMessage } from "./ErrorMessage";
import { ResultTable } from "./ResultTable";

// Mounted with key=queryId: results and outstanding requests belong to exactly
// one selected query, including when navigation returns to an earlier query.
export function QueryResults({ queryId }: { queryId: string }) {
  useLocale();
  const [pages, setPages] = useState<ResultPayload[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [retryable, setRetryable] = useState(false);
  const request = useRef<AbortController | null>(null);

  async function load(nextCursor?: string | null) {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    try {
      const response = await getQueryResults(queryId, nextCursor, controller.signal);
      if (controller.signal.aborted) return;
      const result = response.result;
      if (result) {
        setPages(previous => nextCursor ? [...previous, result] : [result]);
        setCursor(result.next_cursor ?? null);
      }
      setError(null);
      setRetryable(false);
    } catch (cause) {
      if (controller.signal.aborted) return;
      const invalid = cause instanceof ApiError && (
        [401, 403, 404, 410].includes(cause.status)
        || ["PERMISSION_DENIED", "RESULT_EXPIRED"].includes(cause.code)
      );
      if (invalid) {
        setPages([]);
        setCursor(null);
      }
      setError(cause instanceof ApiError
        ? { code: cause.code, message: cause.message }
        : { code: "UNKNOWN", message: "result loading failed" });
      setRetryable(!invalid);
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    void load();
    return () => {
      request.current?.abort();
      request.current = null;
    };
  }, [queryId]);

  const rows = pages.flatMap(page => page.rows);
  return <>
    {error ? <p className="notice error" role="alert" data-testid="result-error">
      <strong>{error.code}</strong> — <ErrorMessage error={error} />
    </p> : null}
    <ResultTable columns={pages[0]?.columns ?? []} rows={rows} truncated={pages.some(page => page.truncated)} />
    {cursor || retryable ? <button className="ghost" disabled={loading} onClick={() => void load(cursor)}>
      {loading ? t("Loading…") : retryable ? t("Retry result loading") : t("sql.loadMore", { count: rows.length })}
    </button> : null}
  </>;
}
