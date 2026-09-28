/** Thin typed wrappers around the generated OpenAPI schema. */

import { apiFetch, setCsrfToken } from "./client";
import type { components } from "./schema";

export type Profile = components["schemas"]["ProfileResponse"];
export type LoginResponse = components["schemas"]["LoginResponse"];
export type Datasource = components["schemas"]["DatasourceResponse"];
export type DatasetSummary = components["schemas"]["DatasetSummary"];
export type DatasetContext = components["schemas"]["DatasetContext"];
export type QueryDetail = components["schemas"]["QueryDetail"];
export type QueryListResponse = components["schemas"]["QueryListResponse"];
export type QueryResultResponse = components["schemas"]["QueryResultResponse"];
export type ResultPayload = components["schemas"]["ResultPayload"];
export type ColumnInfo = components["schemas"]["ColumnInfo"];
export type DatasetSchemaResponse = components["schemas"]["DatasetSchemaResponse"];
export type MetricSummary = components["schemas"]["MetricSummary"];
export type MemoryItem = components["schemas"]["MemoryItem"];
export type AnalysisMemoryResponse = components["schemas"]["AnalysisMemoryResponse"];
export type AnalysisDetail = components["schemas"]["AnalysisDetail"];
export type AnalysisListResponse = components["schemas"]["AnalysisListResponse"];

export async function login(username: string, password: string): Promise<Profile> {
  const response = await apiFetch<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body: { username, password },
  });
  setCsrfToken(response.csrf_token);
  return response.profile;
}

export async function logout(): Promise<void> {
  await apiFetch<void>("/api/v1/auth/logout", { method: "POST" });
  setCsrfToken(null);
}

export async function me(): Promise<Profile> {
  return apiFetch<Profile>("/api/v1/auth/me");
}

export async function listDatasources(): Promise<Datasource[]> {
  const response = await apiFetch<{ items: Datasource[] }>("/api/v1/datasources");
  return response.items;
}

export async function listDatasets(query?: string): Promise<DatasetSummary[]> {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  const suffix = params.size ? `?${params.toString()}` : "";
  const response = await apiFetch<{ items: DatasetSummary[] }>(`/api/v1/datasets${suffix}`);
  return response.items;
}

export async function getDatasetContext(datasetId: string): Promise<DatasetContext> {
  return apiFetch<DatasetContext>(`/api/v1/datasets/${datasetId}`);
}

export async function getDatasetSchema(datasetId: string): Promise<DatasetSchemaResponse> {
  return apiFetch<DatasetSchemaResponse>(`/api/v1/datasets/${datasetId}/schema`);
}

export interface SubmitQueryInput {
  datasourceId: string;
  sql: string;
  parameters: Record<string, unknown>;
  maxRows: number;
  timeoutSeconds: number;
}

export async function submitQuery(input: SubmitQueryInput): Promise<{ query_id: string; status: string }> {
  return apiFetch<{ query_id: string; status: string }>("/api/v1/queries", {
    method: "POST",
    body: {
      datasource_id: input.datasourceId,
      sql: input.sql,
      parameters: input.parameters,
      limits: { max_rows: input.maxRows, timeout_seconds: input.timeoutSeconds },
    },
  });
}

export async function getQuery(queryId: string): Promise<QueryDetail> {
  return apiFetch<QueryDetail>(`/api/v1/queries/${queryId}`);
}

export async function listQueries(status?: string): Promise<QueryListResponse> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  const suffix = params.size ? `?${params.toString()}` : "";
  return apiFetch<QueryListResponse>(`/api/v1/queries${suffix}`);
}

export async function cancelQuery(queryId: string): Promise<QueryDetail> {
  return apiFetch<QueryDetail>(`/api/v1/queries/${queryId}/cancel`, { method: "POST" });
}

export async function getQueryResults(
  queryId: string,
  cursor?: string | null,
): Promise<QueryResultResponse> {
  const params = new URLSearchParams({ limit: "100" });
  if (cursor) params.set("cursor", cursor);
  return apiFetch<QueryResultResponse>(`/api/v1/queries/${queryId}/results?${params.toString()}`);
}

export async function listMetrics(): Promise<MetricSummary[]> {
  const response = await apiFetch<{ items: MetricSummary[] }>("/api/v1/metrics");
  return response.items;
}

export async function createAgentSession(title: string): Promise<{ id: string }> {
  return apiFetch<{ id: string }>("/api/v1/sessions", {
    method: "POST",
    body: { title },
  });
}

export interface CreateAnalysisInput {
  sessionId: string;
  question: string;
  metricKey: string;
  baselineStart: string;
  baselineEnd: string;
  currentStart: string;
  currentEnd: string;
  dimensions: string[];
  dataComplete: boolean;
}

export async function createAnalysis(input: CreateAnalysisInput): Promise<{ analysis_id: string; status: string }> {
  return apiFetch<{ analysis_id: string; status: string }>("/api/v1/analyses", {
    method: "POST",
    body: {
      session_id: input.sessionId,
      question: input.question,
      context: {
        metric_key: input.metricKey,
        baseline_start: input.baselineStart,
        baseline_end: input.baselineEnd,
        current_start: input.currentStart,
        current_end: input.currentEnd,
        dimensions: input.dimensions,
        filters: {},
        data_complete: input.dataComplete,
      },
    },
  });
}

export async function getAnalysis(analysisId: string): Promise<AnalysisDetail> {
  return apiFetch<AnalysisDetail>(`/api/v1/analyses/${analysisId}`);
}

export async function listAnalyses(): Promise<AnalysisListResponse> {
  return apiFetch<AnalysisListResponse>("/api/v1/analyses");
}

export async function cancelAnalysis(analysisId: string): Promise<AnalysisDetail> {
  return apiFetch<AnalysisDetail>(`/api/v1/analyses/${analysisId}/cancel`, { method: "POST" });
}

export async function getAnalysisEvidence(analysisId: string): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/api/v1/analyses/${analysisId}/evidence`);
}

// ---------------------------------------------------------------------------
// Business memory: statements learned from earlier analyses (advisory only).
// ---------------------------------------------------------------------------
export async function listMemory(
  metricKey?: string,
  limit = 200,
): Promise<{ items: MemoryItem[]; counts: Record<string, number> }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (metricKey) params.set("metric_key", metricKey);
  return apiFetch(`/api/v1/memory?${params.toString()}`);
}

export async function confirmMemory(memoryId: string): Promise<MemoryItem> {
  return apiFetch<MemoryItem>(`/api/v1/memory/${memoryId}/confirm`, { method: "POST" });
}

export async function rejectMemory(memoryId: string): Promise<MemoryItem> {
  return apiFetch<MemoryItem>(`/api/v1/memory/${memoryId}/reject`, { method: "POST" });
}

export async function getAnalysisMemory(analysisId: string): Promise<AnalysisMemoryResponse> {
  return apiFetch<AnalysisMemoryResponse>(`/api/v1/analyses/${analysisId}/memory`);
}

// ---------------------------------------------------------------------------
// Charts (spec section 25): controlled specs plus bounded data.
// ---------------------------------------------------------------------------
export type ChartField = { field: string; type: "category" | "quantitative" | "temporal"; unit?: string | null };

export type ChartSpec = {
  schema_version: 1;
  kind: "line" | "bar" | "table";
  title: string;
  data_ref: { type: "calculation" | "result"; id: string };
  encoding: { x: ChartField; y: ChartField; series?: ChartField | null };
  sort?: { field: string; direction: "asc" | "desc" } | null;
  interaction: { tooltip: boolean; zoom: boolean; drilldown_dimensions: string[] };
  evidence_ids: string[];
  empty_state: string;
};

export type ChartView = {
  schema_version: 1;
  id: string;
  spec: ChartSpec;
  data: Array<{ group: string | null; key: string; dimension_values: Array<string | null>; delta: string; net_change_share: string | null }>;
  total_points: number;
  offset: number;
  limit: number;
  truncated: boolean;
  dropped_points: number;
  empty_state: string;
};

export async function getChart(chartId: string, limit = 200): Promise<ChartView> {
  return apiFetch<ChartView>(`/api/v1/charts/${chartId}?limit=${limit}`);
}

export async function drilldownChart(
  chartId: string,
  input: { dimension: string; value: string; period?: "baseline" | "current" },
): Promise<{ analysis_id: string; status: string; parent_id: string; filters: Record<string, string> }> {
  return apiFetch(`/api/v1/charts/${chartId}/drilldown`, {
    method: "POST",
    body: { dimension: input.dimension, value: input.value, period: input.period ?? "current" },
  });
}

// ---------------------------------------------------------------------------
// Administration (spec sections 22 and 24: /admin/datasources, /admin/permissions)
//
// Credentials never travel through these calls: a datasource carries a
// `secret_ref` that the platform resolves from its mounted secret files.
// ---------------------------------------------------------------------------
export type AdminRole = { id: string; name: string; capabilities: string[] };
export type AdminUser = {
  id: string;
  username: string;
  active: boolean;
  roles: AdminRole[];
  created_at: string;
};
export type AdminGrant = {
  id: string;
  role_id: string;
  dataset_id: string;
  action: "discover" | "query";
  expires_at: string | null;
  created_by: string;
};
export type PermissionRequestStatus = "REQUESTED" | "MOCK_APPROVED" | "APPROVED" | "REJECTED";
export type PermissionRequest = {
  id: string;
  user_id: string;
  dataset_id: string;
  reason: string;
  status: PermissionRequestStatus;
  created_at: string;
};
export type AuditEntry = {
  id: number;
  actor_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  trace_id: string;
  outcome: string;
  details: Record<string, unknown>;
  created_at: string;
};
export type DatasourceTestResult = {
  status: string;
  server_version: string | null;
  latency_ms: number | null;
  checked_at: string;
  capabilities: Record<string, unknown> | null;
  error: { code: string; message: string } | null;
};
export type CatalogRefreshResult = {
  datasource_id: string;
  registered: number;
  updated: number;
  deactivated: number;
  items: Array<{ id: string; object_name: string; object_type: string; sync_status: string }>;
  skipped: Array<Record<string, unknown>>;
};
export type IngestionTask = {
  id: string;
  datasource_id: string;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  summary: Record<string, unknown> | null;
  error: { code?: string; message?: string } | null;
};

export async function listAdminUsers(): Promise<{ items: AdminUser[] }> {
  return apiFetch("/api/v1/admin/users");
}

export async function createAdminUser(input: {
  username: string;
  password: string;
  role_ids: string[];
}): Promise<AdminUser> {
  return apiFetch("/api/v1/admin/users", { method: "POST", body: input });
}

export async function updateUserRoles(userId: string, roleIds: string[]): Promise<AdminUser> {
  return apiFetch(`/api/v1/admin/users/${userId}/roles`, {
    method: "PUT",
    body: { role_ids: roleIds },
  });
}

export async function listAdminRoles(): Promise<AdminRole[]> {
  return apiFetch("/api/v1/admin/roles");
}

export async function createAdminRole(name: string): Promise<AdminRole> {
  return apiFetch("/api/v1/admin/roles", { method: "POST", body: { name } });
}

export async function listGrants(): Promise<{ items: AdminGrant[] }> {
  return apiFetch("/api/v1/admin/grants");
}

export async function createGrant(input: {
  role_id: string;
  dataset_id: string;
  action: "discover" | "query";
  expires_at?: string | null;
}): Promise<AdminGrant> {
  return apiFetch("/api/v1/admin/grants", { method: "POST", body: input });
}

export async function deleteGrant(grantId: string): Promise<void> {
  await apiFetch(`/api/v1/admin/grants/${grantId}`, { method: "DELETE" });
}

export async function listPermissionRequests(
  status?: PermissionRequest["status"],
): Promise<{ items: PermissionRequest[] }> {
  const suffix = status ? `?status=${status}` : "";
  return apiFetch(`/api/v1/admin/permission-requests${suffix}`);
}

export async function mockApprovePermissionRequest(requestId: string): Promise<PermissionRequest> {
  return apiFetch(`/api/v1/admin/permission-requests/${requestId}/mock-approve`, { method: "POST" });
}

export async function approvePermissionRequest(
  requestId: string,
  input: { role_id: string; action: "discover" | "query"; expires_at?: string | null },
): Promise<PermissionRequest> {
  return apiFetch(`/api/v1/admin/permission-requests/${requestId}/approve`, {
    method: "POST",
    body: { role_id: input.role_id, action: input.action, expires_at: input.expires_at ?? null },
  });
}

export async function rejectPermissionRequest(
  requestId: string,
  note?: string,
): Promise<PermissionRequest> {
  return apiFetch(`/api/v1/admin/permission-requests/${requestId}/reject`, {
    method: "POST",
    body: { note: note ?? null },
  });
}

/** The requester's own requests and their states (never anybody else's). */
export async function listMyPermissionRequests(): Promise<{ items: PermissionRequest[] }> {
  return apiFetch("/api/v1/permission-requests");
}

export async function requestDatasetAccess(input: {
  dataset_id: string;
  reason: string;
}): Promise<PermissionRequest> {
  return apiFetch("/api/v1/permission-requests", { method: "POST", body: input });
}

export async function listAudits(): Promise<{ items: AuditEntry[] }> {
  return apiFetch("/api/v1/admin/audits");
}

export async function createDatasource(input: {
  name: string;
  kind: "postgres" | "mysql" | "doris" | "spark";
  connection_config: Record<string, unknown>;
  secret_ref: string;
}): Promise<Datasource> {
  return apiFetch("/api/v1/datasources", { method: "POST", body: input });
}

export async function updateDatasource(
  datasourceId: string,
  input: { version: number; connection_config?: Record<string, unknown>; enabled?: boolean },
): Promise<Datasource> {
  return apiFetch(`/api/v1/datasources/${datasourceId}`, { method: "PATCH", body: input });
}

export async function testDatasource(datasourceId: string): Promise<DatasourceTestResult> {
  return apiFetch(`/api/v1/datasources/${datasourceId}/test`, { method: "POST" });
}

export async function refreshCatalog(
  datasourceId: string,
  input: { schemas: string[]; secure_views: string[] },
): Promise<CatalogRefreshResult> {
  return apiFetch(`/api/v1/admin/datasources/${datasourceId}/catalog-refresh`, {
    method: "POST",
    body: input,
  });
}

export async function syncDatasource(datasourceId: string): Promise<IngestionTask> {
  return apiFetch(`/api/v1/datasources/${datasourceId}/sync`, { method: "POST" });
}

export async function getIngestionTask(taskId: string): Promise<IngestionTask> {
  return apiFetch(`/api/v1/ingestions/${taskId}`);
}

export type LineageResponse = {
  schema_version: number;
  dataset_id: string;
  direction: "upstream" | "downstream";
  depth: number;
  status: string;
  message: string | null;
  nodes: Array<{ urn: string; dataset_id: string | null; name: string; platform: string | null; label: string; mapped: boolean }>;
  edges: Array<Record<string, unknown>>;
  filtered_nodes: number;
  labels: string[];
  analysis_evidence: Array<Record<string, unknown>>;
};

export async function getDatasetLineage(
  datasetId: string,
  direction: "upstream" | "downstream" = "upstream",
  depth = 2,
): Promise<LineageResponse> {
  return apiFetch(`/api/v1/datasets/${datasetId}/lineage?direction=${direction}&depth=${depth}`);
}
