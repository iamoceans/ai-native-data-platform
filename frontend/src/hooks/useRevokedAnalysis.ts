import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { AnalysisDetail } from "../api/endpoints";

/** Drop evidence already fetched before a read-time permission revocation. */
export function useRevokedAnalysis(id: string | null, analysis: AnalysisDetail | undefined, revoked: boolean) {
  const client = useQueryClient();
  const denied = revoked || Boolean(analysis?.state?.permission_revoked);
  const knownCharts = useRef(new Map<string, Set<string>>());
  const ids = knownCharts.current.get(id ?? "") ?? new Set<string>();
  for (const chart of (analysis?.final_report as { chart_ids?: string[] } | undefined)?.chart_ids ?? []) ids.add(chart);
  knownCharts.current.set(id ?? "", ids);
  const charts = JSON.stringify([...ids]);
  useEffect(() => {
    if (!id || !denied) return;
    void client.cancelQueries({ queryKey: ["analysis", id], exact: true });
    client.setQueryData<AnalysisDetail>(["analysis", id], previous => previous ? {
      ...previous, context: {}, plan: {}, final_report: null, steps: [], budget: {}, state: { permission_revoked: true },
    } : previous);
    for (const chartId of JSON.parse(charts) as string[]) {
      for (const prefix of ["chart", "chart-pages"]) {
        void client.cancelQueries({ queryKey: [prefix, chartId] });
        client.removeQueries({ queryKey: [prefix, chartId] });
      }
    }
    void client.cancelQueries({ queryKey: ["analysis-memory", id] });
    client.removeQueries({ queryKey: ["analysis-memory", id] });
  }, [client, id, denied, charts]);
  return denied;
}
