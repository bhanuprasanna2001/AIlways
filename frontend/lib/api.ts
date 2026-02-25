// ---------------------------------------------------------------------------
// Client-side API fetcher — handles CSRF, error parsing, auth redirect
// ---------------------------------------------------------------------------

// Prevents multiple simultaneous 401 responses from triggering parallel
// redirects (e.g. when SWR fires several fetchers at once on mount).
let redirecting = false;

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Read the csrf_token cookie (JS-readable, set by backend on login). */
function getCsrfToken(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

type FetchOptions = {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
};

/**
 * Typed fetch wrapper for BFF API routes.
 *
 * - Automatically injects X-CSRF-Token on mutating requests.
 * - Redirects to /signin on 401 (session expired).
 * - Parses error responses into ApiError with a clean message.
 * - Supports both JSON and FormData bodies.
 */
export async function apiFetch<T>(
  url: string,
  options: FetchOptions = {},
): Promise<T> {
  const { method = "GET", body, signal } = options;
  const headers: Record<string, string> = {};

  // CSRF token for state-changing requests
  if (method !== "GET" && method !== "HEAD") {
    headers["X-CSRF-Token"] = getCsrfToken();
  }

  // JSON body (FormData sets its own Content-Type with boundary)
  if (body !== undefined && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(url, {
    method,
    headers,
    body:
      body instanceof FormData
        ? body
        : body !== undefined
          ? JSON.stringify(body)
          : undefined,
    signal,
  });

  // Session expired — redirect to sign-in (deduplicated)
  if (res.status === 401) {
    if (typeof window !== "undefined" && !redirecting) {
      redirecting = true;
      window.location.href = "/signin";
    }
    throw new ApiError(401, "Session expired");
  }

  // Reset the redirect guard on any successful response so future
  // 401s (after re-authentication) can still trigger a redirect.
  redirecting = false;

  const text = await res.text();

  // Handle empty responses (204, etc.)
  if (!text) {
    if (!res.ok) throw new ApiError(res.status, "Server error");
    return {} as T;
  }

  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    if (!res.ok) throw new ApiError(res.status, text);
    throw new ApiError(res.status, "Invalid server response");
  }

  if (!res.ok) {
    const obj = data as Record<string, unknown>;
    const detail = obj?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d: { msg: string }) => d.msg).join(". ")
      : typeof detail === "string"
        ? detail
        : "Something went wrong";
    throw new ApiError(res.status, message);
  }

  return data as T;
}

/** SWR-compatible fetcher — wraps apiFetch for GET requests. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const fetcher = (url: string): Promise<any> => apiFetch(url);
