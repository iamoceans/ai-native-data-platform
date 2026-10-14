/** Poll running analyses; terminal transcript entries need no periodic requests. */
const TERMINAL_ANALYSES = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);
export function analysisPollInterval(status: string | undefined): number | false {
  return status && TERMINAL_ANALYSES.has(status) ? false : 3000;
}
