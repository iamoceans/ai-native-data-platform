/** Opaque retry keys only: never store questions, scope or credentials. */
const pending = new Map<string, string>();

function storageKey(accountId: string, sessionId: string, turnId: string): string {
  return `ainative.turn-retry.v1:${JSON.stringify([accountId, sessionId, turnId])}`;
}

export function pendingTurnRetryKey(accountId: string, sessionId: string, turnId: string): string {
  const operation = storageKey(accountId, sessionId, turnId);
  const cached = pending.get(operation);
  if (cached) return cached;
  let saved: string | null = null;
  try { saved = sessionStorage.getItem(operation); } catch { /* Storage may be disabled. */ }
  const key = saved && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(saved)
    ? saved : crypto.randomUUID();
  pending.set(operation, key);
  try { sessionStorage.setItem(operation, key); } catch { /* Keep in-memory recovery. */ }
  return key;
}

export function forgetTurnRetryKey(accountId: string, sessionId: string, turnId: string): void {
  const operation = storageKey(accountId, sessionId, turnId);
  pending.delete(operation);
  try { sessionStorage.removeItem(operation); } catch { /* Storage may be disabled. */ }
}
