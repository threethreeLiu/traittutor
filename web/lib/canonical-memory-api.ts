import { apiFetch, apiUrl } from "@/lib/api";

const MEMORY_ROOT = "/api/v1/memories";

export type MemoryScope =
  | "conversation"
  | "research"
  | "project"
  | "subject"
  | "global";
export type MemoryProvenance = "explicit" | "inferred";
export type MemorySensitivity = "public" | "personal" | "sensitive";
export type MemoryCandidateStatus =
  | "candidate"
  | "conflict"
  | "activated"
  | "rejected"
  | "deleted";
export type MemoryItemStatus =
  | "candidate"
  | "active"
  | "superseded"
  | "dormant"
  | "deleted";
export type MemoryAssertionState =
  | "verified"
  | "inferred_confirmed";

export interface MemoryCandidate {
  candidate_id: string;
  scope: MemoryScope;
  scope_id: string | null;
  subject_id: string | null;
  kc_id: string | null;
  key: string;
  value: string;
  provenance: MemoryProvenance;
  status: MemoryCandidateStatus;
  confidence: number;
  sensitivity: MemorySensitivity;
  evidence_refs: string[];
  source_ref: string | null;
  proposed_supersedes_id: string | null;
  conflict_memory_ids: string[];
  valid_from: string | null;
  valid_until: string | null;
  created_at: string;
}

export interface MemoryItem {
  memory_id: string;
  scope: MemoryScope;
  scope_id: string | null;
  subject_id: string | null;
  kc_id: string | null;
  key: string;
  value: string | null;
  redacted: boolean;
  provenance: MemoryProvenance;
  status: MemoryItemStatus;
  confidence: number;
  sensitivity: MemorySensitivity;
  valid_from: string;
  valid_until: string | null;
  supersedes_id: string | null;
  evidence_refs: string[];
  source_ref: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryConflict {
  scope: MemoryScope;
  scope_id: string | null;
  subject_id: string | null;
  kc_id: string | null;
  key: string;
  candidate_id: string;
  candidate_value: string;
  memory_ids: string[];
  values: string[];
}

export interface LongTermIndexEntry {
  entry_id: string;
  index_version: number;
  generation: number;
  content_hash: string;
  claim_count: number;
  assertion_states: MemoryAssertionState[];
  updated_at: string;
}

export interface LongTermIndexStatus {
  generation: number;
  entries: LongTermIndexEntry[];
}

export interface MemoryMutationResult {
  item: MemoryItem;
  invalidated_index_generation: number;
}

/** Metadata-only rollup of one snapshot's reads within a scope and purpose. */
export interface MemoryAccessSummary {
  snapshot_id: string;
  created_at: string;
  scope: string;
  purpose: string;
  result_count: number;
}

export interface CanonicalMemorySnapshot {
  candidates: MemoryCandidate[];
  items: MemoryItem[];
  conflicts: MemoryConflict[];
  index: LongTermIndexStatus;
}

export class CanonicalMemoryApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(message);
    this.name = "CanonicalMemoryApiError";
  }
}

export async function getCanonicalMemorySnapshot(
  signal?: AbortSignal,
): Promise<CanonicalMemorySnapshot> {
  const [candidates, items, conflicts, index] = await Promise.all([
    requestJson<MemoryCandidate[]>("/candidates", { cache: "no-store", signal }),
    requestJson<MemoryItem[]>("/items", { cache: "no-store", signal }),
    requestJson<MemoryConflict[]>("/conflicts", { cache: "no-store", signal }),
    requestJson<LongTermIndexStatus>("/index/status", { cache: "no-store", signal }),
  ]);
  return { candidates, items, conflicts, index };
}

export async function listMemoryAccessSummaries(
  signal?: AbortSignal,
): Promise<MemoryAccessSummary[]> {
  const payload = await requestJson<unknown>("/access-records", {
    cache: "no-store",
    signal,
  });
  if (!Array.isArray(payload)) {
    throw new CanonicalMemoryApiError("Memory access records returned an invalid response.", 200);
  }

  const grouped = new Map<string, MemoryAccessSummary>();
  for (const value of payload) {
    if (!isRecord(value)) {
      throw new CanonicalMemoryApiError("Memory access records returned an invalid item.", 200);
    }
    const snapshotId = requiredText(value.snapshot_id, "snapshot_id");
    const createdAt = requiredText(value.created_at, "created_at");
    const scope = requiredText(value.scope, "scope");
    const purpose = requiredText(value.purpose, "purpose");
    const groupId = JSON.stringify([snapshotId, createdAt, scope, purpose]);
    const current = grouped.get(groupId);
    grouped.set(groupId, current
      ? { ...current, result_count: current.result_count + 1 }
      : {
          snapshot_id: snapshotId,
          created_at: createdAt,
          scope,
          purpose,
          result_count: 1,
        });
  }

  return [...grouped.values()].sort((left, right) =>
    right.created_at.localeCompare(left.created_at)
  );
}

export function activateMemoryCandidate(
  candidateId: string,
  operationId: string,
): Promise<MemoryItem> {
  return requestJson(`/candidates/${segment(candidateId)}/activate`,
    jsonRequest("POST", { operation_id: operationId, confirmed: true }));
}

export function rejectMemoryCandidate(
  candidateId: string,
  operationId: string,
): Promise<MemoryCandidate> {
  return requestJson(`/candidates/${segment(candidateId)}/reject`,
    jsonRequest("POST", { operation_id: operationId }));
}

export function supersedeMemoryConflict(
  candidateId: string,
  operationId: string,
): Promise<MemoryItem> {
  return requestJson(`/conflicts/${segment(candidateId)}/supersede`,
    jsonRequest("POST", { operation_id: operationId, confirmed: true }));
}

export function deactivateMemoryItem(
  memoryId: string,
  operationId: string,
): Promise<MemoryMutationResult> {
  return requestJson(`/items/${segment(memoryId)}/deactivate`,
    jsonRequest("POST", { operation_id: operationId }));
}

export function deleteMemoryItem(
  memoryId: string,
  operationId: string,
): Promise<MemoryMutationResult> {
  return requestJson(`/items/${segment(memoryId)}`,
    jsonRequest("DELETE", { operation_id: operationId }));
}

export function rebuildLongTermIndex(
  entryId: string,
  scope?: MemoryScope,
): Promise<LongTermIndexStatus> {
  return requestJson(
    "/index/rebuild",
    jsonRequest("POST", { entry_id: entryId, ...(scope ? { scope } : {}) }),
  );
}

export function createMemoryOperationId(action: string): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `memory-ui-${action}-${suffix}`;
}

function segment(value: string): string {
  return encodeURIComponent(value);
}

function jsonRequest(
  method: "POST" | "DELETE",
  body: object,
): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(apiUrl(`${MEMORY_ROOT}${path}`), init);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new CanonicalMemoryApiError(
      cause instanceof Error ? cause.message : "Memory service unavailable.",
      null,
    );
  }

  if (!response.ok) {
    throw new CanonicalMemoryApiError(await readError(response), response.status);
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new CanonicalMemoryApiError("Memory service returned invalid JSON.", response.status);
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    // Preserve the status-only fallback for malformed error responses.
  }
  return `Memory request failed (${response.status}).`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function requiredText(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new CanonicalMemoryApiError(`Memory access record is missing ${field}.`, 200);
  }
  return value;
}
