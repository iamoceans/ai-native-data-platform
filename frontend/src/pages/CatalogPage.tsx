import { ErrorMessage } from "../components/ErrorMessage";
import { t, useLocale, message, translateMessage, type Message } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  UNDECLARED_DOMAIN,
  getDatasetContext,
  getDatasetFacets,
  getDatasetLineage,
  getDatasetSchema,
  listDatasets,
  listMyPermissionRequests,
  requestDatasetAccess,
} from "../api/endpoints";
import { StatusBadge, statusLabel } from "../components/StatusBadge";
import { datasetName } from "../components/datasetName";

/** Marks the grain entries that address time, which Ask data reads from the period picker. */
function isTemporal(columnType: string): boolean {
  const type = columnType.toLowerCase();
  return type.includes("date") || type.includes("time");
}

export function CatalogPage() {
  const { id } = useParams();
  return <DatasetCatalog key={id ?? "catalog"} selected={id ?? null} />;
}

function DatasetCatalog({ selected }: { selected: string | null }) {
  useLocale();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [datasourceId, setDatasourceId] = useState("");
  const [schemaFilter, setSchemaFilter] = useState("");
  const [domain, setDomain] = useState("");
  const filters = {
    q: search || undefined,
    datasourceId: datasourceId || undefined,
    schema: schemaFilter || undefined,
    domain: domain || undefined,
  };
  const hasFilters = Boolean(search || datasourceId || schemaFilter || domain);
  const facets = useQuery({
    queryKey: ["dataset-facets"],
    queryFn: getDatasetFacets,
  });
  const datasets = useQuery({
    queryKey: ["datasets", filters],
    queryFn: () => listDatasets(filters),
  });
  const datasourceNames = new Map(
    (facets.data?.datasources ?? []).map((item) => [item.id, item.name]),
  );
  const context = useQuery({
    queryKey: ["dataset", selected],
    queryFn: () => getDatasetContext(selected as string),
    enabled: Boolean(selected),
  });
  const schema = useQuery({
    queryKey: ["dataset-schema", selected],
    queryFn: () => getDatasetSchema(selected as string),
    enabled: Boolean(context.data) && !context.isError,
  });
  const lineage = useQuery({
    queryKey: ["dataset-lineage", selected],
    queryFn: () => getDatasetLineage(selected as string),
    enabled: Boolean(context.data) && !context.isError,
  });
  const [reason, setReason] = useState("");
  const myRequests = useQuery({
    queryKey: ["my-permission-requests"],
    queryFn: listMyPermissionRequests,
  });
  const accessRequest = useMutation({
    mutationFn: () =>
      requestDatasetAccess({ dataset_id: selected as string, reason }),
    onSuccess: () => {
      setReason("");
      setRequestNotice(
        message(
          "申请已记录（REQUESTED）。管理员在 Permissions 页处理；批准后会创建真实授权。",
        ),
      );
      queryClient.invalidateQueries({ queryKey: ["my-permission-requests"] });
    },
  });
  const [requestNotice, setRequestNotice] = useState<Message | null>(null);

  return (
    <div className="catalog">
      <section className="card">
        <div className="card-head">
          <h1>{t("Catalog")}</h1>
          <span className="muted small">
            {t("Only datasets you are authorized to discover are listed.")}
          </span>
        </div>
        <input
          placeholder={t("Search tables…")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          data-testid="catalog-search"
        />
        <div className="filter-bar" data-testid="catalog-filters">
          <label>
            {t("Datasources")}
            <select
              data-testid="filter-datasource"
              value={datasourceId}
              onChange={(event) => setDatasourceId(event.target.value)}
            >
              <option value="">{t("全部数据源")}</option>
              {(facets.data?.datasources ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.kind} ({item.count})
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("库（schema）")}
            <select
              data-testid="filter-schema"
              value={schemaFilter}
              onChange={(event) => setSchemaFilter(event.target.value)}
            >
              <option value="">{t("全部库")}</option>
              {(facets.data?.schemas ?? []).map((item) => (
                <option key={item.value} value={item.value}>
                  {item.value} ({item.count})
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("业务域")}
            <select
              data-testid="filter-domain"
              value={domain}
              onChange={(event) => setDomain(event.target.value)}
            >
              <option value="">{t("全部业务域")}</option>
              {(facets.data?.domains ?? []).map((item) => (
                <option key={item.value} value={item.value}>
                  {item.value} ({item.count})
                </option>
              ))}
              {facets.data?.undeclared_domain ? (
                <option value={UNDECLARED_DOMAIN}>
                  {t("未声明业务域")} ({facets.data.undeclared_domain})
                </option>
              ) : null}
            </select>
          </label>
          <button
            type="button"
            className="ghost"
            data-testid="clear-filters"
            disabled={!hasFilters}
            onClick={() => {
              setSearch("");
              setDatasourceId("");
              setSchemaFilter("");
              setDomain("");
            }}
          >
            {t("清空筛选")}
          </button>
        </div>
        <p className="muted small" data-testid="catalog-count">
          {t("显示 {0} / 共 {1} 个可发现数据集", {
            0: datasets.data?.items.length ?? 0,
            1: datasets.data?.total ?? 0,
          })}
          {facets.data
            ? t(" · 业务域来自 metadata/semantic 声明")
            : t(" · 正在加载筛选条件…")}
        </p>
        <ul className="dataset-list">
          {(datasets.data?.items ?? []).map((dataset) => (
            <li key={dataset.id}>
              <Link
                to={`/datasets/${encodeURIComponent(dataset.id)}`}
                aria-current={dataset.id === selected ? "page" : undefined}
                className={
                  dataset.id === selected ? "dataset active" : "dataset"
                }
              >
                <span className="dataset-name">{datasetName(dataset)}</span>
                <span className="dataset-meta">
                  {dataset.domain ? (
                    <span className="chip" data-testid="row-domain">
                      {dataset.domain}
                    </span>
                  ) : null}
                  {datasourceNames.get(dataset.datasource_id) ? (
                    <span data-testid="row-datasource">
                      {datasourceNames.get(dataset.datasource_id)} ·
                    </span>
                  ) : null}
                  <span>{dataset.object_type}</span> ·{" "}
                  <StatusBadge status={dataset.sync_status} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
        {datasets.error ? <p className="notice error" role="alert"><ErrorMessage error={datasets.error} /></p> : null}
        {datasets.data && datasets.data.items.length === 0 ? (
          <p className="muted">
            {hasFilters
              ? t("当前筛选条件下没有可见的数据集，清空筛选或换一个条件。")
              : t(
                  "No datasets visible. An administrator must register the data source, refresh its catalog and grant your role the discover permission.",
                )}
          </p>
        ) : null}
      </section>

      {selected ? (
        <section className="card">
          <Link to="/catalog">{t("Back to catalog")}</Link>
          {context.isError ? (
            <p className="notice error" role="alert" data-testid="dataset-error">
              <ErrorMessage error={context.error} />
            </p>
          ) : !context.data ? (
            <p className="muted" role="status">{t("Loading dataset…")}</p>
          ) : (
            <>
              <div className="card-head">
                <h2>{datasetName(context.data)}</h2>
              </div>
              {context.data.schema_hash ? (
                <p className="muted small">
                  {t("schema hash")}{" "}
                  <code>{context.data.schema_hash.slice(0, 16)}…</code>
                  {schema.data ? (
                    <>
                      {" "}
                      {t("· source")} {schema.data.source}
                    </>
                  ) : null}
                </p>
              ) : null}
              {context.data.description ||
              context.data.domain ||
              (context.data.grain?.length ?? 0) > 0 ||
              (context.data.metric_keys ?? []).length > 0 ? (
                <div className="semantic-block" data-testid="dataset-semantics">
                  <div className="card-head">
                    <h3>{t("语义维度（grain）")}</h3>
                    <span className="muted small">
                      {t("来自 metadata/semantic，是可下钻维度的登记表")}
                    </span>
                  </div>
                  {context.data.description ? (
                    <p className="muted small">{context.data.description}</p>
                  ) : null}
                  <div className="chips">
                    {(context.data.grain ?? []).map((dimension) => (
                      <span
                        className="chip mono-id"
                        key={dimension}
                        data-testid={`dataset-dimension-${dimension}`}
                      >
                        {dimension}
                      </span>
                    ))}
                    {context.data.business_timezone ? (
                      <span className="chip">
                        {t("业务时区")} {context.data.business_timezone}
                      </span>
                    ) : null}
                    {context.data.domain ? (
                      <span className="chip" data-testid="context-domain">
                        {t("业务域")} {context.data.domain}
                      </span>
                    ) : null}
                    {context.data.currency ? (
                      <span className="chip">
                        {t("币种")} {context.data.currency}
                      </span>
                    ) : null}
                  </div>
                  {(context.data.metric_keys ?? []).length > 0 ? (
                    <p className="muted small">
                      {t("已声明指标：")}
                      {(context.data.metric_keys ?? []).join(" · ")} —{" "}
                      <Link to="/ask">{t("去 Ask data 使用")}</Link>
                    </p>
                  ) : (
                    <p className="muted small">
                      {t(
                        "这张表还没有声明指标，因此它在 Ask data 里不可选；要按它的维度做分析，先在治理侧声明指标口径。",
                      )}
                    </p>
                  )}
                  {(context.data.join_keys ?? []).length > 0 ? (
                    <p className="muted small">
                      {t("Join keys：")}
                      {(context.data.join_keys ?? []).join(", ")}
                      {t("（跨源关联只走白名单 relation）")}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {schema.data ? (
                <div className="table-scroll">
                  <table className="result-table plain">
                    <thead>
                      <tr>
                        <th>{t("Column")}</th>
                        <th>{t("Type")}</th>
                        <th>{t("Nullable")}</th>
                        <th>{t("分析角色")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {schema.data.columns.map((column) => (
                        <tr key={column.name}>
                          <td>{column.name}</td>
                          <td>{column.type}</td>
                          <td>
                            {column.nullable === null
                              ? "—"
                              : column.nullable
                                ? t("yes")
                                : t("no")}
                          </td>
                          <td>
                            {(context.data?.grain ?? []).includes(column.name) ? (
                              <span className="tag">
                                {t("维度")}
                                {isTemporal(column.type) ? t(" · 时间列") : ""}
                              </span>
                            ) : (
                              <span className="muted small">{t("非维度列")}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : schema.isError ? (
                <p className="notice error" role="alert"><ErrorMessage error={schema.error} /></p>
              ) : (
                <p className="muted">{t("Loading schema…")}</p>
              )}
              <div className="card-head">
                <h3>{t("Lineage（upstream, depth 2）")}</h3>
                {lineage.data ? (
                  <StatusBadge status={t(lineage.data.status)} />
                ) : null}
              </div>
              {lineage.data ? (
                <>
                  <p className="muted small" data-testid="lineage-status">
                    {t(lineage.data.status)}
                    {lineage.data.message ? ` — ${lineage.data.message}` : ""}
                    {lineage.data.labels.length
                      ? t(" · 边标签：{0}", {
                          0: lineage.data.labels
                            .map((label) => t(label))
                            .join(", "),
                        })
                      : ""}
                    {lineage.data.filtered_nodes
                      ? t(" · 权限过滤 {0} 个节点", {
                          0: lineage.data.filtered_nodes,
                        })
                      : ""}
                  </p>
                  <ul className="dataset-list">
                    {lineage.data.nodes.map((node) => (
                      <li key={node.urn}>
                        <span className="dataset-name">{node.name}</span>
                        <span className="dataset-meta">
                          {node.platform ?? t("unknown")} · {t(node.label)}
                          {node.mapped ? t(" · 已映射") : t(" · 未映射")}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {!lineage.data.nodes.length ? (
                    <p className="muted small">
                      {t("没有可见的上游节点（状态已如实说明，未做模拟）。")}
                    </p>
                  ) : null}
                </>
              ) : lineage.isError ? (
                <p className="notice error" role="alert"><ErrorMessage error={lineage.error} /></p>
              ) : (
                <p className="muted small">{t("Loading lineage…")}</p>
              )}

              <div className="card-head">
                <h3>{t("申请查询权限")}</h3>
                <span className="muted small">
                  {t("申请进入管理员队列，Mock 批准不等于授权")}
                </span>
              </div>
              <div className="form-row">
                <label className="grow">
                  {t("理由")}{" "}
                  <input
                    data-testid="request-reason"
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </label>
                <button
                  className="ghost"
                  data-testid="request-access"
                  disabled={accessRequest.isPending || reason.trim().length === 0}
                  onClick={() => accessRequest.mutate()}
                >
                  {t("提交申请")}
                </button>
              </div>
              {accessRequest.error instanceof ApiError ? (
                <p className="notice error">
                  <strong>{accessRequest.error.code}</strong> —{" "}
                  <ErrorMessage error={accessRequest.error} />
                </p>
              ) : null}
              {requestNotice ? (
                <p className="notice" data-testid="request-notice">
                  {translateMessage(requestNotice)}
                </p>
              ) : null}

              <div className="card-head">
                <h3>{t("我的申请")}</h3>
                <span className="muted small">
                  {t("只有你自己看得到；批准即创建真实授权")}
                </span>
              </div>
              {(myRequests.data?.items ?? []).length ? (
                <table className="data-table" data-testid="my-requests-table">
                  <thead>
                    <tr>
                      <th scope="col">{t("数据集")}</th>
                      <th scope="col">{t("理由")}</th>
                      <th scope="col">{t("状态")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(myRequests.data?.items ?? []).map((item) => (
                      <tr key={item.id}>
                        <td>
                          {item.dataset_id === selected
                            ? t("本数据集")
                            : item.dataset_id.slice(0, 8)}
                        </td>
                        <td className="small">{item.reason}</td>
                        <td>
                          <span
                            className={`badge ${
                              item.status === "APPROVED"
                                ? "ok"
                                : item.status === "MOCK_APPROVED"
                                  ? "wait"
                                  : "muted"
                            }`}
                          >
                            {statusLabel(item.status)}
                          </span>
                          {item.status === "MOCK_APPROVED" ? (
                            <span className="muted small"> {t("未授权")}</span>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="muted small">{t("还没有提交过申请。")}</p>
              )}
            </>
          )}
        </section>
      ) : null}
    </div>
  );
}
