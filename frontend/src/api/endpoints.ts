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
