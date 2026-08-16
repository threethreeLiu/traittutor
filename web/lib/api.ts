// API configuration and utility functions.
//
// The frontend bundle is now URL-agnostic: the browser issues requests against
// the frontend origin (`:3782/api/...` and `:3782/api/.../ws`), and
// `web/proxy.ts` rewrites `/api/*` and `/ws/*` to the configured backend on
// every request. This means there is no build-time or runtime URL substitution
// in the bundle, and no placeholder token to keep alive. `apiUrl` and `wsUrl`
// stay as one-liner pass-throughs so the dozens of existing call sites continue
// to compile and work without modification.

import { appPath } from "@/lib/base-path";

/**
 * Construct a full API URL from a path.
 *
 * Pass-through: returns the path unchanged. The actual backend URL is
 * determined at request time by `web/proxy.ts`, which reads
 * `TRAITTUTOR_API_BASE_URL` (exported by the container entrypoint from
 * `data/user/settings/system.json`).
 *
 * @param path - API path (e.g., '/api/v1/knowledge/list')
 * @returns A browser-visible path, including the production mount prefix.
 */
export function apiUrl(path: string): string {
  return appPath(path);
}

/**
 * Construct a WebSocket URL from a path.
 *
 * Pass-through: returns the path unchanged. `proxy.ts` rewrites `/ws/*` to
 * the configured backend, and the runtime upgrades to `ws://` /
 * `wss://` based on the backend's scheme.
 *
 * @param path - WebSocket path (e.g., '/api/v1/solve')
 * @returns The same path, unchanged
 */
export function wsUrl(path: string): string {
  return appPath(path);
}

export interface ApiErrorPayload {
  message: string;
  code?: string;
  detail?: unknown;
}

function apiDetailMessage(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (Array.isArray(value)) {
    for (const item of value) {
      const message = apiDetailMessage(item);
      if (message) return message;
    }
    return null;
  }
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  return (
    apiDetailMessage(record.message) ??
    apiDetailMessage(record.msg) ??
    apiDetailMessage(record.detail)
  );
}

/** Parse FastAPI string, structured, and Pydantic-list error bodies uniformly. */
export async function parseApiError(response: Response): Promise<ApiErrorPayload> {
  let message = `Request failed: ${response.status}`;
  let code: string | undefined;
  let detail: unknown;
  try {
    const data = (await response.json()) as { detail?: unknown };
    detail = data.detail;
    message = apiDetailMessage(detail) ?? message;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const candidate = (detail as Record<string, unknown>).code;
      code = typeof candidate === "string" ? candidate : undefined;
      if (!apiDetailMessage(detail) && code) message = code;
    }
  } catch {
    // Keep the status-based fallback for non-JSON and empty responses.
  }
  return { message, code, detail };
}

/**
 * Parse a "TRAITTUTOR_AUTH_ENABLED"-style flag at runtime.
 *
 * Used by both `apiFetch` (frontend) and `web/proxy.ts` (auth redirect) to
 * decide whether to gate requests. Evaluated with a runtime regex so the
 * value can be set by the container entrypoint on every start (no build-time
 * inlining).
 */
export function parseAuthEnabled(raw: string | undefined): boolean {
  return /^(1|true|yes|on)$/i.test((raw ?? "").trim());
}

// Whether auth is enabled, learned at runtime — NOT from a build-time env var.
// The browser bundle never sees `TRAITTUTOR_AUTH_ENABLED` (it isn't a
// `NEXT_PUBLIC_` var, so Next.js does not inline it), and auth is a runtime
// setting that must not be baked at build time anyway. `fetchAuthStatus()` in
// `web/lib/auth.ts` calls `setRuntimeAuthEnabled()` once the backend reports the
// real state. Until then it defaults to `false`, so a stray 401 in the default
// auth-disabled deployment never bounces the user to /login. The server-side
// gate (web/proxy.ts middleware) enforces auth independently; this flag only
// drives the client's in-session 401 → /login redirect.
let runtimeAuthEnabled = false;

/** Record the backend-reported auth state for `apiFetch`'s 401 redirect gate. */
export function setRuntimeAuthEnabled(enabled: boolean): void {
  runtimeAuthEnabled = enabled;
}

/**
 * Authenticated fetch wrapper. Behaves identically to `fetch` but automatically
 * redirects to /login when the backend returns 401 (expired / invalid token).
 *
 * Pass `skipAuthRedirect: true` for endpoints where a 401 is an expected,
 * recoverable response that the caller wants to handle inline — most notably
 * the login/register endpoints, where 401 means "wrong credentials" and must
 * surface as a form error rather than reload the page.
 */
export async function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit & { skipAuthRedirect?: boolean },
): Promise<Response> {
  const { skipAuthRedirect, ...fetchInit } = init ?? {};
  const res = await fetch(input, { credentials: "include", ...fetchInit });

  if (
    res.status === 401 &&
    runtimeAuthEnabled &&
    !skipAuthRedirect &&
    typeof window !== "undefined"
  ) {
    const next = encodeURIComponent(window.location.pathname);
    window.location.href = appPath(`/login?next=${next}`);
    return new Promise(() => {});
  }

  return res;
}
