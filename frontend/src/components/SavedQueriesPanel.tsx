import { useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { deleteSavedQuery, listSavedQueryPage, saveQuery, type Datasource, type SavedQuery } from "../api/endpoints";
import { ErrorMessage } from "./ErrorMessage";
import { t, useLocale } from "../i18n";

export function SavedQueriesPanel({ datasourceId, sql, sources, onLoad, loadDisabled }: {
  datasourceId: string; sql: string; sources: Datasource[]; onLoad: (query: SavedQuery) => void; loadDisabled: boolean;
}) {
  useLocale();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const saved = useInfiniteQuery({ queryKey: ["saved-queries"], enabled: open,
    initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => listSavedQueryPage(pageParam),
    getNextPageParam: page => page.next_cursor || undefined });
  const save = useMutation({ mutationFn: (snapshot: { title: string; datasource_id: string; sql: string }) => saveQuery(snapshot),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["saved-queries"] }); } });
  const remove = useMutation({ mutationFn: deleteSavedQuery, onSuccess: () => { client.invalidateQueries({ queryKey: ["saved-queries"] }); } });
  const items = [...new Map((saved.data?.pages.flatMap(page => page.items) ?? []).map(item => [item.id, item])).values()];
  return <details className="card" data-testid="saved-queries" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{t("已保存的查询")}</summary>
    <div className="stack">
      <p className="muted small">{t("载入只替换编辑器内容，点击运行后才会执行查询。")}</p>
      <form className="form-row" onSubmit={event => { event.preventDefault(); save.mutate({ title: title.trim(), datasource_id: datasourceId, sql }); }}>
        <label className="grow">{t("查询名称")}<input data-testid="saved-query-title" value={title} maxLength={200} onChange={event => { setTitle(event.target.value); save.reset(); }} required /></label>
        <button className="primary" data-testid="save-query" disabled={!title.trim() || !sql.trim() || !datasourceId || save.isPending || remove.isPending}>{t("保存当前 SQL")}</button>
      </form>
      {save.isSuccess ? <p role="status" data-testid="saved-query-notice">{t("查询已保存")}</p> : null}
      {save.isError || remove.isError || saved.isError ? <p className="notice error" role="alert"><ErrorMessage error={save.error ?? remove.error ?? saved.error ?? {}} /></p> : null}
      {saved.isPending ? <p>{t("加载中…")}</p> : null}
      {!saved.isPending && !saved.isError && !items.length ? <p>{t("还没有保存的查询。")}</p> : null}
      <ul className="saved-query-list">{items.map(query => {
        const source = sources.find(item => item.id === query.datasource_id && item.enabled);
        return <li key={query.id} className="stack" data-testid={`saved-query-${query.id}`}>
          <strong>{query.title}</strong><span className="muted small">{source?.name ?? t("数据源不可用")}</span>
          <div className="row-actions"><button type="button" className="ghost" data-testid={`load-saved-${query.id}`} disabled={loadDisabled || !source} onClick={() => onLoad(query)}>{t("载入编辑器")}</button>
            <button type="button" className="ghost" data-testid={`delete-saved-${query.id}`} disabled={remove.isPending || save.isPending} onClick={() => remove.mutate(query.id)}>{t("删除")}</button></div>
        </li>;
      })}</ul>
      {saved.hasNextPage ? <button type="button" className="ghost" data-testid="more-saved-queries" disabled={saved.isFetchingNextPage} onClick={() => saved.fetchNextPage()}>{t("加载更多已保存查询")}</button> : null}
      {saved.isError ? <button type="button" onClick={() => saved.refetch()}>{t("重试")}</button> : null}
    </div>
  </details>;
}
