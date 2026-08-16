import { apiFetch, apiUrl, setRuntimeAuthEnabled } from "@/lib/api";
import { appRelativePath } from "@/lib/base-path";

// Auth state is resolved at runtime from the backend (`/api/v1/auth/status`),
// not from a build-time/env constant: the browser bundle never sees
// `TRAITTUTOR_AUTH_ENABLED` (not a `NEXT_PUBLIC_` var), and auth is runtime
// config that must not be baked into the bundle. Components observe it via the
// `useAuthStatus` hook (web/hooks/useAuthStatus.ts); `apiFetch`'s redirect gate
// is driven by `setRuntimeAuthEnabled`, which `fetchAuthStatus` calls below.

export interface AuthStatus {
  enabled: boolean;
  authenticated: boolean;
  user_id?: string;
  username?: string;
  /** Avatar marker: "", "icon:<name>:<color>", or "img:<version>". */
  avatar?: string;
}

/** Keep post-auth navigation within the TraitTutor app. */
export function safeAuthRedirect(next: string | null | undefined, fallback = "/"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return fallback;
  return appRelativePath(next);
}

/**
 * Call the backend to check whether the current session is authenticated.
 * Returns null on network error so callers can decide how to handle it.
 */
export async function fetchAuthStatus(): Promise<AuthStatus | null> {
  try {
    const res = await apiFetch(apiUrl("/api/v1/auth/status"));
    if (!res.ok) return null;
    const status: AuthStatus = await res.json();
    // Record the real auth state so apiFetch's in-session 401 → /login redirect
    // fires only when auth is actually enabled.
    setRuntimeAuthEnabled(Boolean(status.enabled));
    return status;
  } catch {
    return null;
  }
}

/**
 * POST credentials to the backend. Returns true on success.
 */
export async function login(
  username: string,
  password: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await apiFetch(apiUrl("/api/v1/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.trim(), password }),
      // A 401 here means "wrong credentials", not an expired session — handle it
      // inline as a form error instead of triggering the global login redirect.
      skipAuthRedirect: true,
    });

    if (res.ok) return { ok: true };

    const data = await res.json().catch(() => ({}));
    return { ok: false, error: extractDetail(data.detail) ?? "Login failed" };
  } catch {
    return { ok: false, error: "Could not reach the server" };
  }
}

/**
 * Normalise a FastAPI error detail to a plain string.
 * FastAPI can return detail as a string (HTTPException) or as an array of
 * validation error objects (422 Unprocessable Entity).
 */
function extractDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === "object" && first !== null && "msg" in first)
      return String((first as { msg: unknown }).msg);
  }
  return "Request failed";
}

/**
 * Register a new account. Public registration may be disabled server-side
 * (403); new accounts always get the "user" role — the first administrator
 * is created via the deployment bootstrap endpoint, never by registration.
 */
export async function register(
  username: string,
  password: string,
): Promise<{
  ok: boolean;
  error?: string;
}> {
  try {
    const res = await apiFetch(apiUrl("/api/v1/auth/register"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.trim(), password }),
      // Registration validation failures (e.g. 400/401) should surface inline
      // rather than bounce the user through the global login redirect.
      skipAuthRedirect: true,
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok)
      return { ok: true };
    return { ok: false, error: extractDetail(data.detail) };
  } catch {
    return { ok: false, error: "Could not reach the server" };
  }
}

/**
 * POST to the logout endpoint to clear the session cookie.
 */
export async function logout(): Promise<void> {
  try {
    await apiFetch(apiUrl("/api/v1/auth/logout"), {
      method: "POST",
    });
  } catch {
    // Ignore — we'll redirect regardless
  }
}
