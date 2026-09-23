import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "../api/client";
import {
  getDatasetContext,
  getDatasetLineage,
  getDatasetSchema,
  listDatasets,
  requestDatasetAccess,
} from "../api/endpoints";
import { StatusBadge } from "../components/StatusBadge";

export function CatalogPage() {
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
  const accessRequest = useMutation({
    mutationFn: () => requestDatasetAccess({ dataset_id: selected as string, reason }),
    onSuccess: () => {
      setReason("");
      setRequestNotice("申请已记录（REQUESTED）。管理员可在 Permissions 页看到；Mock 批准不会产生真实授权。");
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
          {schema.data ? (
            <div className="table-scroll">
              <table className="result-table plain">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                    <th>Nullable</th>
                  </tr>
                </thead>
                <tbody>
                  {schema.data.columns.map((column) => (
                    <tr key={column.name}>
                      <td>{column.name}</td>
                      <td>{column.type}</td>
                      <td>{column.nullable === null ? "—" : column.nullable ? "yes" : "no"}</td>
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
        </section>
      ) : null}
    </div>
  );
}
