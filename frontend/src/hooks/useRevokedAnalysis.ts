import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { AnalysisDetail } from "../api/endpoints";

/** Drop evidence already fetched before a read-time permission revocation. */
export function useRevokedAnalysis(
  id: string | null,
  analysis: AnalysisDetail | undefined,
  revoked: boolean,
) {
  const client = useQueryClient();
  const denied = revoked || Boolean(analysis?.state?.permission_revoked);
  const knownCharts = useRef(new Map<string, Set<string>>());
  const chartIds = knownCharts.current.get(id ?? "") ?? new Set<string>();
  const report = analysis?.final_report as { chart_ids?: string[] } | undefined;
  for (const chartId of report?.chart_ids ?? []) chartIds.add(chartId);
  knownCharts.current.set(id ?? "", chartIds);
  const chartIdsKey = JSON.stringify([...chartIds]);

  useEffect(() => {
    if (!id || !denied) return;
    const clearCachedQuery = (queryKey: readonly unknown[]) => {
      void client.cancelQueries({ queryKey });
      client.removeQueries({ queryKey });
    };
    void client.cancelQueries({ queryKey: ["analysis", id], exact: true });
    client.setQueryData<AnalysisDetail>(["analysis", id], previous => previous ? {
      ...previous,
      context: {},
      plan: {},
      final_report: null,
      steps: [],
      budget: {},
      state: { permission_revoked: true },
    } : previous);
    for (const chartId of JSON.parse(chartIdsKey) as string[]) {
      for (const prefix of ["chart", "chart-pages"]) {
        clearCachedQuery([prefix, chartId]);
      }
    }
    clearCachedQuery(["analysis-memory", id]);
  }, [client, id, denied, chartIdsKey]);
  return denied;
}
