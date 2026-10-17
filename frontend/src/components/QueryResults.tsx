import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { getQueryResults } from "../api/endpoints";
import { t, useLocale } from "../i18n";
import { ErrorMessage } from "./ErrorMessage";
import { ResultTable } from "./ResultTable";

/** Signed cursors are remembered for previous/next navigation, never synthesized. */
export function QueryResults({ queryId }: { queryId: string }) {
  useLocale();
  const [paging, setPaging] = useState({ size: 50, index: 0, cursors: [null] as (string | null)[] });
  const cursor = paging.cursors[paging.index];
  const page = useQuery({
    queryKey: ["query-results", queryId, paging.size, cursor],
    queryFn: () => getQueryResults(queryId, cursor, paging.size),
    // Revalidate even when going backward: permissions and retention can change.
    staleTime: 0,
    gcTime: 0,
  });
  const result = !page.isError && !page.isFetching ? page.data?.result : null;
  const total = page.data?.result?.row_count;
  const pageCount = total === undefined ? null : Math.max(1, Math.ceil(total / paging.size));

  return <div className="stack" data-testid="query-results">
    <nav className="form-row" aria-label={t("结果分页")}>
      <label>{t("每页行数")}
        <select data-testid="result-page-size" value={paging.size}
          onChange={event => setPaging({ size: Number(event.target.value), index: 0, cursors: [null] })}>
          {[20, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}
        </select>
      </label>
      <button className="ghost" data-testid="result-previous" disabled={page.isFetching || paging.index === 0}
        onClick={() => setPaging(previous => ({ ...previous, index: previous.index - 1 }))}>{t("上一页")}</button>
      <button className="ghost" data-testid="result-next" disabled={page.isFetching || !result?.next_cursor}
        onClick={() => {
          if (!result?.next_cursor) return;
          const next = result.next_cursor;
          setPaging(previous => ({ ...previous, index: previous.index + 1,
            cursors: [...previous.cursors.slice(0, previous.index + 1), next] }));
        }}>{t("下一页")}</button>
      {pageCount !== null && !page.isError ? <span className="muted small" data-testid="result-page-count" role="status">
        {t("第 {0} 页 / 共 {1} 页 · 共 {2} 行", { 0: paging.index + 1, 1: pageCount, 2: total ?? 0 })}
      </span> : null}
    </nav>
    {page.isFetching ? <p className="muted" role="status">{t("加载结果中…")}</p> : null}
    {page.isError ? <div className="notice error" role="alert" data-testid="result-error">
      <strong>{page.error instanceof ApiError ? page.error.code : "UNKNOWN"}</strong> — <ErrorMessage error={page.error} />
      <button className="ghost" data-testid="result-retry" onClick={() => page.refetch()}>{t("重试")}</button>
    </div> : null}
    {result ? <ResultTable columns={result.columns} rows={result.rows} truncated={result.truncated}
      rowOffset={paging.index * paging.size} /> : null}
  </div>;
}
