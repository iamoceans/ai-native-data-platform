import { useEffect, useRef } from "react";

export interface StreamEvent {
  id: number | null;
  type: string;
  data: Record<string, unknown>;
}

/**
 * Subscribe to a query's SSE stream. EventSource reconnects automatically with
 * Last-Event-ID; the caller refetches the authoritative detail on each event.
 */
export function useQueryEvents(
  queryId: string | null,
  onEvent: (event: StreamEvent) => void,
  enabled: boolean,
): void {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!queryId || !enabled) return;
    const source = new EventSource(`/api/v1/queries/${queryId}/events`, { withCredentials: true });
    const forward = (type: string) => (message: MessageEvent<string>) => {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(message.data) as Record<string, unknown>;
      } catch {
        data = { raw: message.data };
      }
      handler.current({
        id: message.lastEventId ? Number(message.lastEventId) : null,
        type,
        data,
      });
    };
    const types = [
      "query.queued",
      "query.started",
      "query.cancel_requested",
      "query.finished",
      "query.failed",
      "query.lost",
      "stream.end",
      "stream.closed",
      "error",
    ];
    const listeners = types.map((type) => {
      const listener = forward(type);
      source.addEventListener(type, listener as EventListener);
      return { type, listener };
    });
    source.onerror = () => {
      // EventSource reconnects on its own; nothing to do here.
    };
    return () => {
      listeners.forEach(({ type, listener }) => source.removeEventListener(type, listener as EventListener));
      source.close();
    };
  }, [queryId, enabled]);
}
