import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "../api/client";
import {
  createDatasource,
  getIngestionTask,
  listDatasources,
  refreshCatalog,
  syncDatasource,
  testDatasource,
  type CatalogRefreshResult,
  type DatasourceTestResult,
} from "../api/endpoints";
import { PermissionGate } from "../components/PermissionGate";
import { StatusBadge } from "../components/StatusBadge";

/**
 * Datasources (spec section 24): connection test, catalog refresh, ingestion
 * sync, health and capabilities - the states an administrator has to tell apart.
 *
 * Credentials are not part of this screen: a datasource only names a
 * `secret_ref` that the platform resolves from its mounted secret files, so no
 * password ever travels through the browser (spec 22/24).
 */
export function AdminDatasourcesPage() {
  const queryClient = useQueryClient();
  const datasources = useQuery({ queryKey: ["admin-datasources"], queryFn: listDatasources });
  const [tests, setTests] = useState<Record<string, DatasourceTestResult>>({});
  const [refreshes, setRefreshes] = useState<Record<string, CatalogRefreshResult>>({});
  const [ingestions, setIngestions] = useState<Record<string, string>>({});
  const [refreshForm, setRefreshForm] = useState<{ schemas: string; secureViews: string }>({
    schemas: "public",
    secureViews: "",
  });
  const [register, setRegister] = useState({
    name: "",
    kind: "postgres" as "postgres" | "mysql" | "doris",
    host: "",
    port: "",
    database: "",
    secretRef: "",
  });
  const [error, setError] = useState<ApiError | null>(null);

  const test = useMutation({
    mutationFn: (datasourceId: string) => testDatasource(datasourceId),
    onSuccess: (result, datasourceId) => {
      setTests((current) => ({ ...current, [datasourceId]: result }));
      queryClient.invalidateQueries({ queryKey: ["admin-datasources"] });
    },
    onError: (failure) => setError(failure instanceof ApiError ? failure : null),
  });

  const refresh = useMutation({
    mutationFn: (datasourceId: string) =>
      refreshCatalog(datasourceId, {
        schemas: refreshForm.schemas.split(",").map((item) => item.trim()).filter(Boolean),
        secure_views: refreshForm.secureViews
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      }),
    onSuccess: (result, datasourceId) => {
      setRefreshes((current) => ({ ...current, [datasourceId]: result }));
      queryClient.invalidateQueries({ queryKey: ["admin-datasources"] });
    },
    onError: (failure) => setError(failure instanceof ApiError ? failure : null),
  });

  const sync = useMutation({
    mutationFn: (datasourceId: string) => syncDatasource(datasourceId),
    onSuccess: async (task, datasourceId) => {
      setIngestions((current) => ({ ...current, [datasourceId]: task.id }));
      // Poll once so a finished task shows its real state instead of "QUEUED".
      for (let attempt = 0; attempt < 10; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const latest = await getIngestionTask(task.id);
        setIngestions((current) => ({
          ...current,
          [datasourceId]: `${latest.id}|${latest.status}`,
        }));
        if (["SUCCEEDED", "FAILED", "LOST"].includes(latest.status)) break;
      }
    },
    onError: (failure) => setError(failure instanceof ApiError ? failure : null),
  });

  const created = useMutation({
    mutationFn: () =>
      createDatasource({
        name: register.name,
        kind: register.kind,
        connection_config: {
          host: register.host,
          port: Number(register.port),
          database: register.database,
          connect_timeout_seconds: 5,
        },
        secret_ref: register.secretRef,
      }),
    onSuccess: () => {
      setRegister({ name: "", kind: "postgres", host: "", port: "", database: "", secretRef: "" });
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["admin-datasources"] });
    },
    onError: (failure) => setError(failure instanceof ApiError ? failure : null),
  });

  return (
    <PermissionGate capability="admin.manage">
      <div className="workspace" data-testid="admin-datasources">
        <section className="card">
          <div className="card-head">
            <div>
              <span className="eyebrow">Administration</span>
              <h1>Datasources</h1>
            </div>
            <span className="muted small">凭据只以 secret_ref 引用，不下发到前端</span>
          </div>
          <p className="muted small">
            连接测试与目录刷新是管理员操作；注册目标的 host 仍需匹配管理员配置的网络白名单。
          </p>
          {error ? (
            <p className="notice error">
              <strong>{error.code}</strong> — {error.message}
            </p>
          ) : null}

          <table className="data-table" data-testid="datasource-table">
            <thead>
              <tr>
                <th scope="col">名称</th>
                <th scope="col">类型</th>
                <th scope="col">目标</th>
                <th scope="col">健康</th>
                <th scope="col">能力</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {(datasources.data ?? []).map((datasource) => {
                const testResult = tests[datasource.id];
                const refreshResult = refreshes[datasource.id];
                const ingestion = ingestions[datasource.id];
                const capabilities = datasource.capabilities ?? {};
                return (
                  <tr key={datasource.id}>
                    <th scope="row">
                      {datasource.name}
                      {!datasource.enabled ? <span className="tag warn">disabled</span> : null}
                    </th>
                    <td>{datasource.kind}</td>
                    <td className="mono small">
                      {String(datasource.connection_config.host ?? "?")}:
                      {String(datasource.connection_config.port ?? "?")}/
                      {String(datasource.connection_config.database ?? "?")}
                    </td>
                    <td>
                      <StatusBadge status={datasource.health_status} />
                      {testResult ? (
                        <span className="muted small" data-testid="datasource-test-result">
                          {" "}
                          {testResult.status}
                          {testResult.latency_ms != null ? ` · ${testResult.latency_ms} ms` : ""}
                          {testResult.server_version ? ` · ${testResult.server_version}` : ""}
                          {testResult.error ? ` · ${testResult.error.code}` : ""}
                        </span>
                      ) : null}
                    </td>
                    <td className="small">
                      {String(capabilities.dialect ?? "—")}
                      {capabilities.cancel ? " · cancel" : ""}
                      {capabilities.server_timeout ? " · timeout" : ""}
                      {capabilities.transactional_read_only ? " · read-only" : ""}
                    </td>
                    <td className="row-actions">
                      <button
                        className="ghost small"
                        data-testid={`test-${datasource.name}`}
                        disabled={test.isPending}
                        onClick={() => test.mutate(datasource.id)}
                      >
                        测试连接
                      </button>
                      <button
                        className="ghost small"
                        data-testid={`refresh-${datasource.name}`}
                        disabled={refresh.isPending}
                        onClick={() => refresh.mutate(datasource.id)}
                      >
                        刷新目录
                      </button>
                      <button
                        className="ghost small"
                        data-testid={`sync-${datasource.name}`}
                        disabled={sync.isPending}
                        onClick={() => sync.mutate(datasource.id)}
                      >
                        同步元数据
                      </button>
                      {refreshResult ? (
                        <span className="muted small" data-testid="catalog-refresh-result">
                          注册 {refreshResult.registered} · 更新 {refreshResult.updated} · 下线{" "}
                          {refreshResult.deactivated} · 跳过 {refreshResult.skipped.length}
                        </span>
                      ) : null}
                      {ingestion ? (
                        <span className="muted small" data-testid="ingestion-state">
                          采集 {ingestion.split("|")[1] ?? "已提交"}
                        </span>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>目录刷新参数</h2>
            <span className="muted small">secure_views 需管理员确认无敏感列后才可暴露</span>
          </div>
          <div className="form-row">
            <label className="grow">
              schemas（逗号分隔）
              <input
                data-testid="refresh-schemas"
                value={refreshForm.schemas}
                onChange={(event) =>
                  setRefreshForm((current) => ({ ...current, schemas: event.target.value }))
                }
              />
            </label>
            <label className="grow">
              secure views（逗号分隔，可空）
              <input
                data-testid="refresh-secure-views"
                value={refreshForm.secureViews}
                onChange={(event) =>
                  setRefreshForm((current) => ({ ...current, secureViews: event.target.value }))
                }
              />
            </label>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>注册数据源</h2>
            <span className="muted small">密码由挂载密钥文件提供，不在此填写</span>
          </div>
          <div className="form-row">
            <label>
              名称
              <input
                data-testid="new-datasource-name"
                value={register.name}
                onChange={(event) =>
                  setRegister((current) => ({ ...current, name: event.target.value }))
                }
              />
            </label>
            <label>
              类型
              <select
                data-testid="new-datasource-kind"
                value={register.kind}
                onChange={(event) =>
                  setRegister((current) => ({
                    ...current,
                    kind: event.target.value as typeof register.kind,
                  }))
                }
              >
                <option value="postgres">postgres</option>
                <option value="mysql">mysql</option>
                <option value="doris">doris</option>
              </select>
            </label>
            <label>
              host
              <input
                data-testid="new-datasource-host"
                value={register.host}
                onChange={(event) =>
                  setRegister((current) => ({ ...current, host: event.target.value }))
                }
              />
            </label>
            <label>
              port
              <input
                data-testid="new-datasource-port"
                value={register.port}
                onChange={(event) =>
                  setRegister((current) => ({ ...current, port: event.target.value }))
                }
              />
            </label>
            <label>
              database
              <input
                data-testid="new-datasource-database"
                value={register.database}
                onChange={(event) =>
                  setRegister((current) => ({ ...current, database: event.target.value }))
                }
              />
            </label>
            <label>
              secret_ref
              <input
                data-testid="new-datasource-secret-ref"
                value={register.secretRef}
                onChange={(event) =>
                  setRegister((current) => ({ ...current, secretRef: event.target.value }))
                }
              />
            </label>
          </div>
          <button
            className="primary"
            data-testid="register-datasource"
            disabled={
              created.isPending ||
              !register.name ||
              !register.host ||
              !register.port ||
              !register.database ||
              !register.secretRef
            }
            onClick={() => created.mutate()}
          >
            {created.isPending ? "注册中…" : "注册数据源"}
          </button>
        </section>
      </div>
    </PermissionGate>
  );
}
