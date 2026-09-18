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
};

export function StatusBadge({ status }: { status: string }) {
  const tone = TONE[status] ?? "muted";
  return <span className={`badge ${tone}`}>{LABELS[status] ?? status}</span>;
}
