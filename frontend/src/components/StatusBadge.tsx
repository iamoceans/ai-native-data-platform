import { t, useLocale } from "../i18n";
const LABELS: Record<string, string> = {
  APPROVED: "Approved",
  REJECTED: "Rejected",
  RESOLVING: "Resolving",
  ANALYZING: "Analyzing",
  RESPONDING: "Responding",
  WAITING_INPUT: "Waiting for input",
  UNKNOWN: "unknown",
  DEGRADED: "Degraded",
  QUEUED: "Queued",
  RUNNING: "Running",
  SUCCEEDED: "Succeeded",
  FAILED: "Failed",
  CANCEL_REQUESTED: "Cancelling",
  CANCELLED: "Cancelled",
  TIMED_OUT: "Timed out",
  LOST: "Lost",
  REQUESTED: "Requested",
  MOCK_APPROVED: "Mock approved",
  SYNCED: "Synced",
  PENDING: "Pending",
  HEALTHY: "Healthy",
  UNAVAILABLE: "Unavailable",
  // Analysis states (spec 19): the raw code stays visible in the badge title.
  CREATED: "status.created",
  UNDERSTANDING: "Understanding",
  RETRIEVING: "Retrieving",
  PLANNING: "Planning",
  EXECUTING: "Executing",
  OBSERVING: "Observing",
  SYNTHESIZING: "Synthesizing",
  COMPLETED: "Completed",
  PARTIAL: "Partial",
};

const TONE: Record<string, string> = {
  SUCCEEDED: "ok",
  RUNNING: "run",
  QUEUED: "wait",
  CANCEL_REQUESTED: "wait",
  FAILED: "bad",
  CANCELLED: "muted",
  TIMED_OUT: "bad",
  LOST: "bad",
  HEALTHY: "ok",
  SYNCED: "ok",
  COMPLETED: "ok",
  PARTIAL: "wait",
  CREATED: "wait",
  UNDERSTANDING: "run",
  RETRIEVING: "run",
  PLANNING: "run",
  EXECUTING: "run",
  OBSERVING: "run",
  SYNTHESIZING: "run",
};

export function statusLabel(status: string): string {
  return t(LABELS[status] ?? status);
}

export function StatusBadge({ status }: { status: string }) {
  useLocale();
  const tone = TONE[status] ?? "muted";
  return (
    <span className={`badge ${tone}`} title={status}>
      {statusLabel(status)}
    </span>
  );
}
