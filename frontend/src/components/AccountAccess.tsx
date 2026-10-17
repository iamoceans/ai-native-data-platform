import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getMyAccess, listMyPermissionRequestPage, type AccountAccess } from "../api/endpoints";
import { capabilityNames } from "../api/capabilities";
import { StatusBadge } from "./StatusBadge";
import { ErrorMessage } from "./ErrorMessage";
import { formatDate, t, useLocale } from "../i18n";

const actionName = (action: string) => t(action === "discover" ? "发现表" : "查询数据");
const domainName = (domain: string | null) => domain ?? t("未声明业务域");

export function AccountAccessPanel({ accountId }: { accountId: string }) {
  useLocale();
  const access = useInfiniteQuery({
    queryKey: ["my-access", accountId], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => getMyAccess(pageParam),
    getNextPageParam: page => page.next_cursor ?? undefined,
    refetchInterval: 15000,
  });
  const first = access.data?.pages[0];
  const items = access.data?.pages.flatMap(page => page.items) ?? [];
  const sources = new Map<string, AccountAccess["items"]>();
  for (const item of items) {
    const group = sources.get(item.datasource_id) ?? [];
    group.push(item);
    sources.set(item.datasource_id, group);
  }
  return <section className="card stack account-access" data-testid="account-access">
    <div className="card-head"><h2>{t("我的数据权限")}</h2><button className="ghost" disabled={access.isFetching} onClick={() => access.refetch()}>{t("刷新权限")}</button></div>
    <p className="muted small">{t("权限来自当前角色，过期或撤销后不再显示；查询需要同时具备发现和查询授权，且数据源已启用。")}</p>
    {access.isPending ? <p role="status">{t("加载中…")}</p> : null}
    {access.isError ? <p className="notice error" role="alert"><ErrorMessage error={access.error} /></p> : first ? <>
      <dl className="kv">
        <dt>{t("用户 ID")}</dt><dd>{first.profile.id}</dd>
        <dt>{t("账户状态")}</dt><dd>{t(first.active ? "已启用" : "已停用")}</dd>
        <dt>{t("创建时间")}</dt><dd>{formatDate(first.created_at)}</dd>
        <dt>{t("最近成功登录（审计保留期内）")}</dt><dd>{first.last_login_at ? formatDate(first.last_login_at) : t("暂无记录")}</dd>
        <dt>{t("权限检查时间")}</dt><dd>{formatDate(first.checked_at)}</dd>
      </dl>
      <details><summary>{t("各角色的平台权限")}</summary><ul>{first.roles.map(role => <li key={role.name}><strong>{role.name}</strong> · {role.capabilities.map(capability => t(capabilityNames[capability] ?? capability)).join(" · ") || t("无平台权限")}</li>)}</ul></details>
      <p>{t("可访问 {0} 个数据源、{1} 张表", { 0: first.datasource_count, 1: first.total })}</p>
      {first.domains.length ? <div className="table-scroll"><table className="data-table" data-testid="access-domains"><thead><tr><th>{t("业务域")}</th><th>{t("授权表数")}</th><th>{t("可查询表数")}</th></tr></thead><tbody>{first.domains.map(domain => <tr key={JSON.stringify(domain.domain)}><td>{domainName(domain.domain)}</td><td>{domain.dataset_count}</td><td>{domain.queryable_count}</td></tr>)}</tbody></table></div> : null}
      {!first.total ? <p className="notice">{t("当前账号暂无有效数据授权。")}</p> : null}
      {[...sources].map(([id, rows]) => <section className="stack" key={id} data-testid="access-source">
        <h3>{rows[0].datasource_name} <span className="tag">{rows[0].datasource_kind}</span> {!rows[0].datasource_enabled ? <span className="badge warn">{t("数据源已停用")}</span> : null}</h3>
        <div className="table-scroll"><table className="data-table"><thead><tr><th>{t("表")}</th><th>{t("数据库")}</th><th>{t("业务域")}</th><th>{t("可用操作")}</th><th>{t("授权角色与有效期")}</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.dataset_id} data-testid="access-dataset">
            <td>{row.actions.includes("discover") ? <Link to={`/datasets/${row.dataset_id}`}>{row.qualifier}</Link> : row.qualifier}</td>
            <td>{row.catalog || row.namespace}</td>
            <td>{domainName(row.domain)}</td>
            <td>{row.actions.map(actionName).join(" · ")}<p className="muted small">{t(row.query_available ? "可查询" : "当前不可查询")}</p></td>
            <td>{row.grants.map(grant => <div key={`${grant.role}:${grant.action}`}>{grant.role} · {actionName(grant.action)} · {grant.expires_at ? formatDate(grant.expires_at) : t("长期有效")}</div>)}</td>
          </tr>)}</tbody></table></div>
      </section>)}
      {access.hasNextPage ? <button className="ghost" disabled={access.isFetching} onClick={() => access.fetchNextPage()}>{t("加载更多权限")}</button> : null}
    </> : null}
    <Link to="/catalog">{t("浏览目录并申请数据权限")}</Link>
  </section>;
}

export function MyPermissionRequests({ accountId }: { accountId: string }) {
  useLocale();
  const requests = useInfiniteQuery({
    queryKey: ["my-permission-requests", accountId], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => listMyPermissionRequestPage(pageParam),
    getNextPageParam: page => page.next_cursor ?? undefined,
    refetchInterval: 15000,
  });
  const rows = requests.data?.pages.flatMap(page => page.items) ?? [];
  return <section className="card stack" data-testid="my-permission-requests">
    <div className="card-head"><h2>{t("我的申请")}</h2><button className="ghost" disabled={requests.isFetching} onClick={() => requests.refetch()}>{t("刷新")}</button></div>
    <p className="muted small">{t("申请状态不等同于当前授权，请以上方有效权限为准。")}</p>
    {requests.isPending ? <p role="status">{t("加载中…")}</p> : requests.isError ? <p className="notice error" role="alert"><ErrorMessage error={requests.error} /></p> : rows.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>{t("数据集 ID")}</th><th>{t("申请原因")}</th><th>{t("状态")}</th><th>{t("创建时间")}</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td><code>{row.dataset_id}</code></td><td>{row.reason}</td><td><StatusBadge status={row.status} /></td><td>{formatDate(row.created_at)}</td></tr>)}</tbody></table></div> : <p className="muted">{t("暂无权限申请。")}</p>}
    {!requests.isError && requests.hasNextPage ? <button className="ghost" disabled={requests.isFetching} onClick={() => requests.fetchNextPage()}>{t("加载更多申请")}</button> : null}
  </section>;
}
