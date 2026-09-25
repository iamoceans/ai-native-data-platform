import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  getDatasetContext,
  getDatasetLineage,
  getDatasetSchema,
  listDatasets,
  listMyPermissionRequests,
  requestDatasetAccess,
} from "../api/endpoints";
import { StatusBadge } from "../components/StatusBadge";

/** Marks the grain entries that address time, which Ask data reads from the period picker. */
function isTemporal(columnType: string): boolean {
  const type = columnType.toLowerCase();
  return type.includes("date") || type.includes("time");
}

export function CatalogPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const datasets = useQuery({
    queryKey: ["datasets", search],
    queryFn: () => listDatasets(search || undefined),
  });
  const context = useQuery({
    queryKey: ["dataset", selected],
    queryFn: () => getDatasetContext(selected as string),
    enabled: Boolean(selected),
  });
  const schema = useQuery({
    queryKey: ["dataset-schema", selected],
    queryFn: () => getDatasetSchema(selected as string),
    enabled: Boolean(selected),
  });
  const lineage = useQuery({
    queryKey: ["dataset-lineage", selected],
    queryFn: () => getDatasetLineage(selected as string),
    enabled: Boolean(selected),
  });
  const [reason, setReason] = useState("");
  const myRequests = useQuery({
    queryKey: ["my-permission-requests"],
    queryFn: listMyPermissionRequests,
  });
  const accessRequest = useMutation({
    mutationFn: () => requestDatasetAccess({ dataset_id: selected as string, reason }),
    onSuccess: () => {
      setReason("");
      setRequestNotice("申请已记录（REQUESTED）。管理员在 Permissions 页处理；批准后会创建真实授权。");
      queryClient.invalidateQueries({ queryKey: ["my-permission-requests"] });
    },
  });
  const [requestNotice, setRequestNotice] = useState<string | null>(null);

  return (
    <div className="catalog">
      <section className="card">
        <div className="card-head">
          <h1>Catalog</h1>
          <span className="muted small">Only datasets you are authorized to discover are listed.</span>
        </div>
        <input
          placeholder="Search tables…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          data-testid="catalog-search"
        />
        <ul className="dataset-list">
          {(datasets.data ?? []).map((dataset) => (
            <li key={dataset.id}>
              <button
                className={dataset.id === selected ? "dataset active" : "dataset"}
                onClick={() => setSelected(dataset.id)}
              >
                <span className="dataset-name">
                  {dataset.schema_name}.{dataset.object_name}
                </span>
                <span className="dataset-meta">
                  {dataset.object_type} · <StatusBadge status={dataset.sync_status} />
                </span>
              </button>
            </li>
          ))}
        </ul>
        {datasets.data && datasets.data.length === 0 ? (
          <p className="muted">
            No datasets visible. An administrator must register the data source, refresh its catalog and
            grant your role the discover permission.
          </p>
        ) : null}
      </section>

      {selected ? (
        <section className="card">
          <div className="card-head">
            <h2>{context.data ? `${context.data.schema_name}.${context.data.object_name}` : "Dataset"}</h2>
          </div>
          {context.data?.schema_hash ? (
            <p className="muted small">
              schema hash <code>{context.data.schema_hash.slice(0, 16)}…</code>
              {schema.data ? <> · source {schema.data.source}</> : null}
            </p>
          ) : null}
          {context.data &&
          (context.data.description ||
            (context.data.grain?.length ?? 0) > 0 ||
            (context.data.metric_keys ?? []).length > 0) ? (
            <div className="semantic-block" data-testid="dataset-semantics">
              <div className="card-head">
                <h3>语义维度（grain）</h3>
                <span className="muted small">来自 metadata/semantic，是可下钻维度的登记表</span>
              </div>
              {context.data.description ? <p className="muted small">{context.data.description}</p> : null}
              <div className="chips">
                {(context.data.grain ?? []).map((dimension) => (
                  <span className="chip mono-id" key={dimension} data-testid={`dataset-dimension-${dimension}`}>
                    {dimension}
                  </span>
                ))}
                {context.data.business_timezone ? (
                  <span className="chip">业务时区 {context.data.business_timezone}</span>
                ) : null}
                {context.data.currency ? <span className="chip">币种 {context.data.currency}</span> : null}
              </div>
              {(context.data.metric_keys ?? []).length > 0 ? (
                <p className="muted small">
                  已声明指标：{(context.data.metric_keys ?? []).join(" · ")} —{" "}
                  <Link to="/ask">去 Ask data 使用</Link>
                </p>
              ) : (
                <p className="muted small">
                  这张表还没有声明指标，因此它在 Ask data 里不可选；要按它的维度做分析，先在治理侧声明指标口径。
                </p>
              )}
              {(context.data.join_keys ?? []).length > 0 ? (
                <p className="muted small">
                  Join keys：{(context.data.join_keys ?? []).join(", ")}（跨源关联只走白名单 relation）
                </p>
              ) : null}
            </div>
          ) : null}
          {schema.data ? (
            <div className="table-scroll">
              <table className="result-table plain">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                    <th>Nullable</th>
                    <th>分析角色</th>
                  </tr>
                </thead>
                <tbody>
                  {schema.data.columns.map((column) => (
                    <tr key={column.name}>
                      <td>{column.name}</td>
                      <td>{column.type}</td>
                      <td>{column.nullable === null ? "—" : column.nullable ? "yes" : "no"}</td>
                      <td>
                        {(context.data?.grain ?? []).includes(column.name) ? (
                          <span className="tag">维度{isTemporal(column.type) ? " · 时间列" : ""}</span>
                        ) : (
                          <span className="muted small">非维度列</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">Loading schema…</p>
          )}
          <div className="card-head">
            <h3>Lineage（upstream, depth 2）</h3>
            {lineage.data ? <StatusBadge status={lineage.data.status} /> : null}
          </div>
          {lineage.data ? (
            <>
              <p className="muted small" data-testid="lineage-status">
                {lineage.data.status}
                {lineage.data.message ? ` — ${lineage.data.message}` : ""}
                {lineage.data.labels.length ? ` · 边标签：${lineage.data.labels.join(", ")}` : ""}
                {lineage.data.filtered_nodes ? ` · 权限过滤 ${lineage.data.filtered_nodes} 个节点` : ""}
              </p>
              <ul className="dataset-list">
                {lineage.data.nodes.map((node) => (
                  <li key={node.urn}>
                    <span className="dataset-name">{node.name}</span>
                    <span className="dataset-meta">
                      {node.platform ?? "unknown"} · {node.label}
                      {node.mapped ? " · 已映射" : " · 未映射"}
                    </span>
                  </li>
                ))}
              </ul>
              {!lineage.data.nodes.length ? (
                <p className="muted small">没有可见的上游节点（状态已如实说明，未做模拟）。</p>
              ) : null}
            </>
          ) : (
            <p className="muted small">Loading lineage…</p>
          )}

          <div className="card-head">
            <h3>申请查询权限</h3>
            <span className="muted small">申请进入管理员队列，Mock 批准不等于授权</span>
          </div>
          <div className="form-row">
            <label className="grow">
              理由
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
              提交申请
            </button>
          </div>
          {accessRequest.error instanceof ApiError ? (
            <p className="notice error">
              <strong>{accessRequest.error.code}</strong> — {accessRequest.error.message}
            </p>
          ) : null}
          {requestNotice ? (
            <p className="notice" data-testid="request-notice">
              {requestNotice}
            </p>
          ) : null}

          <div className="card-head">
            <h3>我的申请</h3>
            <span className="muted small">只有你自己看得到；批准即创建真实授权</span>
          </div>
          {(myRequests.data?.items ?? []).length ? (
            <table className="data-table" data-testid="my-requests-table">
              <thead>
                <tr>
                  <th scope="col">数据集</th>
                  <th scope="col">理由</th>
                  <th scope="col">状态</th>
                </tr>
              </thead>
              <tbody>
                {(myRequests.data?.items ?? []).map((item) => (
                  <tr key={item.id}>
                    <td>{item.dataset_id === selected ? "本数据集" : item.dataset_id.slice(0, 8)}</td>
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
                        {item.status}
                      </span>
                      {item.status === "MOCK_APPROVED" ? (
                        <span className="muted small"> 未授权</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted small">还没有提交过申请。</p>
          )}
        </section>
      ) : null}
    </div>
  );
}
