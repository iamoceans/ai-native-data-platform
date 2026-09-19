const LABELS: Record<string, string> = {
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
  CREATED: "Created",
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

export function StatusBadge({ status }: { status: string }) {
  const tone = TONE[status] ?? "muted";
  return <span className={`badge ${tone}`} title={status}>{LABELS[status] ?? status}</span>;
}
