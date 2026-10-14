import { useEffect, useRef, useState } from "react";
import { sessionEventsUrl } from "../api/endpoints";

export interface SessionStreamEvent {
  id: number | null;
  type: string;
  data: Record<string, unknown>;
}

/**
 * Subscribe to one conversation's event stream.
 *
 * The stream is a convenience, never the source of truth: every event only
 * triggers a refetch of the session, the active turn and the transcript, so a
 * dropped connection (or a proxy that buffers SSE) degrades to polling rather
 * than to a stale view. `connected` tells the caller which mode it is in.
 */
export function useSessionEvents(
  sessionId: string | null,
  onEvent: (event: SessionStreamEvent) => void,
  enabled: boolean,
  streamKey: string | null = null,
): { connected: boolean } {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!sessionId || !enabled) {
      setConnected(false);
      return;
    }
    const source = new EventSource(sessionEventsUrl(sessionId), { withCredentials: true });
    const forward = (type: string) => (message: MessageEvent<string>) => {
      if (typeof message.data !== "string") return;
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(message.data) as Record<string, unknown>;
      } catch {
        data = { raw: message.data };
      }
      if (type === "stream.end" || type === "stream.closed") {
        source.close();
        setConnected(false);
      }
      handler.current({
        id: message.lastEventId ? Number(message.lastEventId) : null,
        type,
        data,
      });
    };
    const types = [
      "message.created",
      "turn.state.changed",
      "turn.scope.resolved",
      "turn.skills.loaded",
      "turn.llm.degraded",
      "turn.patch.adjusted",
      "turn.prose.rejected",
      "analysis.linked",
      "analysis.completed",
      "analysis.partial",
      "analysis.failed",
      "analysis.cancelled",
      "analysis.permission.revoked",
      "stream.end",
      "stream.closed",
    ];
    const listeners = types.map((type) => {
      const listener = forward(type);
      source.addEventListener(type, listener as EventListener);
      return { type, listener };
    });
    source.onopen = () => setConnected(true);
    source.onerror = () => {
      // EventSource reconnects on its own; mark the connection as degraded so
      // the caller can poll instead of waiting for an event that may not come.
      setConnected(false);
    };
    return () => {
      listeners.forEach(({ type, listener }) =>
        source.removeEventListener(type, listener as EventListener),
      );
      source.close();
      setConnected(false);
    };
  }, [sessionId, enabled, streamKey]);

  return { connected };
}
