import { apiFetch, apiUrl } from "@/lib/api";

export interface ProfileInfo {
  id: string;
  username: string;
  created_at: string;
  disabled?: boolean;
}

export async function changeAccountPassword(current_password: string, new_password: string): Promise<void> {
  const res = await apiFetch(apiUrl("/api/v1/auth/account/password"), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password, new_password }) });
  if (!res.ok) throw new Error(extractDetail(await res.json().catch(() => ({})), "Failed to change password"));
}

export async function deleteAccount(current_password: string, confirmation: string): Promise<void> {
  const res = await apiFetch(apiUrl("/api/v1/auth/account"), { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password, confirmation }) });
  if (!res.ok) throw new Error(extractDetail(await res.json().catch(() => ({})), "Failed to delete account"));
}

function extractDetail(data: unknown, fallback: string): string {
  if (typeof data === "object" && data !== null && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

/** Fetch the signed-in user's own profile. */
export async function getProfile(): Promise<ProfileInfo> {
  const res = await apiFetch(apiUrl("/api/v1/auth/profile"));
  if (!res.ok) throw new Error("Failed to fetch profile");
  return res.json();
}
