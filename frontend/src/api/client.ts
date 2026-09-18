/** Typed API client: session cookie + CSRF header + unified error envelope. */

let csrfToken: string | null = readCookie("ainative_csrf");
const authListeners = new Set<() => void>();

export function readCookie(name: string): string | null {
  const match = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name}=`));
  if (!match) return null;
  return decodeURIComponent(match.slice(name.length + 1));
}

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function onUnauthenticated(listener: () => void): () => void {
  authListeners.add(listener);
  return () => authListeners.delete(listener);
}

export class ApiError extends Error {
  code: string;
  status: number;
  retryable: boolean;
  details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    retryable: boolean,
    details: Record<string, unknown>,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.details = details;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken && ["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(path, {
    method,
    headers,
    credentials: "include",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) {
      authListeners.forEach((listener) => listener());
    }
    const envelope = (payload ?? {}) as {
      error?: { code?: string; message?: string; retryable?: boolean; details?: Record<string, unknown> };
    };
    throw new ApiError(
      response.status,
      envelope.error?.code ?? "UNKNOWN",
      envelope.error?.message ?? `request failed with ${response.status}`,
      envelope.error?.retryable ?? false,
      envelope.error?.details ?? {},
    );
  }
  return payload as T;
}
