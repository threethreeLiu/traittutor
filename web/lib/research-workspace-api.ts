import { apiFetch, apiUrl } from "@/lib/api";

const RESEARCH_ROOT = "/api/v1/research/workspaces";

export const RESEARCH_RUN_STATES = [
  "draft",
  "queued",
  "running",
  "pausing",
  "paused",
  "cancelling",
  "cancelled",
  "completed",
  "failed",
  "needs_review",
] as const;

export type ResearchRunState = (typeof RESEARCH_RUN_STATES)[number];
export type ResearchRunAction = "pause" | "resume" | "cancel" | "retry";
export type ResearchSourcePolicy = "web" | "knowledge_base" | "mixed";

export interface ResearchKnowledgeBaseBinding {
  resource_id: string;
  display_name: string;
  source: "admin" | "user";
}

export interface ResearchWorkspaceSummary {
  workspace_id: string;
  title: string;
  subject_id: string | null;
  status: "active" | "archived" | "deleted";
  revision: number;
  active_brief_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResearchBrief {
  brief_id: string;
  workspace_id: string;
  version: number;
  question: string;
  objectives: string[];
  constraints: string[];
  source_policy: ResearchSourcePolicy;
  knowledge_base: ResearchKnowledgeBaseBinding | null;
  continuation: {
    parent_run_id: string;
    report_id: string;
    report_revision: number;
  } | null;
  created_at: string;
}

export interface ResearchRun {
  run_id: string;
  workspace_id: string;
  brief_id: string;
  brief_version: number;
  status: ResearchRunState;
  revision: number;
  fencing_epoch: number;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResearchSource {
  source_id: string;
  workspace_id: string;
  url: string;
  title: string;
  excerpt: string | null;
  retrieved_at: string;
  revision: number;
  status: "active" | "invalidated";
  invalidated_at: string | null;
  invalidation_reason: string | null;
}

export interface ResearchClaim {
  claim_id: string;
  workspace_id: string;
  run_id: string;
  text: string;
  kind: "grounded" | "inference";
  source_ids: string[];
  created_at: string;
  revision: number;
  evidence_status: "active" | "needs_review";
  review_required_source_ids: string[];
}

export interface ResearchNote {
  note_id: string;
  workspace_id: string;
  body: string;
  source_ids: string[];
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface ResearchReport {
  report_id: string;
  workspace_id: string;
  run_id: string;
  body: string;
  claims: ResearchClaim[];
  created_at: string;
  revision: number;
  evidence_status: "active" | "needs_review";
  review_required_source_ids: string[];
}

export interface ResearchWorkspaceDetail {
  workspace: ResearchWorkspaceSummary;
  brief: ResearchBrief | null;
  runs: ResearchRun[];
  sources: ResearchSource[];
  claims: ResearchClaim[];
  notes: ResearchNote[];
  reports: ResearchReport[];
}

export interface SaveResearchBriefInput {
  question: string;
  objectives: string[];
  constraints: string[];
  source_policy: ResearchSourcePolicy;
  knowledge_base_ref?: string;
  expected_workspace_revision: number;
  idempotency_key: string;
}

export class ResearchApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly kind: "http" | "network" | "contract",
  ) {
    super(message);
    this.name = "ResearchApiError";
  }
}

export async function listResearchWorkspaces(
  signal?: AbortSignal,
): Promise<ResearchWorkspaceSummary[]> {
  const payload = await requestJson(RESEARCH_ROOT, { method: "GET", signal });
  return array(payload, "workspaces").map(parseWorkspace);
}

export async function createResearchWorkspace(
  input: { title: string; idempotency_key: string },
  signal?: AbortSignal,
): Promise<ResearchWorkspaceSummary> {
  const payload = await requestJson(RESEARCH_ROOT, jsonRequest("POST", input, signal));
  return parseWorkspace(payload);
}

export async function getResearchWorkspace(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<ResearchWorkspaceDetail> {
  const root = workspacePath(workspaceId);
  const [workspacePayload, briefsPayload, runsPayload, sourcesPayload, notesPayload] = await Promise.all([
    requestJson(root, { method: "GET", cache: "no-store", signal }),
    requestJson(`${root}/briefs`, { method: "GET", cache: "no-store", signal }),
    requestJson(`${root}/runs`, { method: "GET", cache: "no-store", signal }),
    requestJson(`${root}/sources`, { method: "GET", cache: "no-store", signal }),
    requestJson(`${root}/notes`, { method: "GET", cache: "no-store", signal }),
  ]);
  const workspace = parseWorkspace(workspacePayload);
  const briefs = array(briefsPayload, "briefs").map(parseBrief);
  const runs = array(runsPayload, "runs").map(parseRun);
  const reports = (
    await Promise.all(
      runs
        .filter((run) => run.status === "completed" || run.status === "needs_review")
        .map((run) => getResearchReport(workspaceId, run.run_id, signal)),
    )
  ).filter((report): report is ResearchReport => report !== null);
  const claims = deduplicateClaims(reports.flatMap((report) => report.claims));
  return {
    workspace,
    brief: selectActiveBrief(workspace, briefs),
    runs,
    sources: array(sourcesPayload, "sources").map(parseSource),
    claims,
    notes: array(notesPayload, "notes").map(parseNote),
    reports,
  };
}

export async function saveResearchBrief(
  workspaceId: string,
  input: SaveResearchBriefInput,
  briefId?: string,
  signal?: AbortSignal,
): Promise<ResearchBrief> {
  const path = briefId
    ? `${workspacePath(workspaceId)}/briefs/${encodeURIComponent(briefId)}`
    : `${workspacePath(workspaceId)}/briefs`;
  const payload = await requestJson(
    path,
    jsonRequest(briefId ? "PUT" : "POST", input, signal),
  );
  return parseBrief(payload);
}

export async function startResearchRun(
  workspaceId: string,
  input: { brief_id: string; brief_version: number; idempotency_key: string },
  signal?: AbortSignal,
): Promise<ResearchRun> {
  const payload = await requestJson(
    `${workspacePath(workspaceId)}/runs`,
    jsonRequest("POST", input, signal),
  );
  return parseRun(payload);
}

export async function continueResearchRun(
  workspaceId: string,
  runId: string,
  input: SaveResearchBriefInput & { parent_report_revision: number },
  signal?: AbortSignal,
): Promise<ResearchRun> {
  const payload = await requestJson(
    `${workspacePath(workspaceId)}/runs/${encodeURIComponent(runId)}/follow-up`,
    jsonRequest("POST", input, signal),
  );
  return parseRun(payload);
}

export async function applyResearchRunAction(
  workspaceId: string,
  runId: string,
  input: {
    action: ResearchRunAction;
    expected_revision: number;
    expected_status: ResearchRunState;
    idempotency_key: string;
  },
  signal?: AbortSignal,
): Promise<ResearchRun> {
  const payload = await requestJson(
    `${workspacePath(workspaceId)}/runs/${encodeURIComponent(runId)}/${input.action}`,
    jsonRequest("POST", {
      expected_revision: input.expected_revision,
      expected_status: input.expected_status,
      idempotency_key: input.idempotency_key,
    }, signal),
  );
  return parseRun(payload);
}

export async function createResearchNote(
  workspaceId: string,
  input: { body: string; source_ids: string[]; idempotency_key: string },
  signal?: AbortSignal,
): Promise<ResearchNote> {
  const payload = await requestJson(
    `${workspacePath(workspaceId)}/notes`,
    jsonRequest("POST", input, signal),
  );
  return parseNote(payload);
}

export async function invalidateResearchSource(
  workspaceId: string,
  sourceId: string,
  input: {
    expected_revision: number;
    expected_status: "active";
    idempotency_key: string;
    reason?: string;
  },
  signal?: AbortSignal,
): Promise<ResearchSource> {
  const payload = await requestJson(
    `${workspacePath(workspaceId)}/sources/${encodeURIComponent(sourceId)}`,
    jsonRequest("DELETE", input, signal),
  );
  return parseSource(payload);
}

function workspacePath(workspaceId: string): string {
  return `${RESEARCH_ROOT}/${encodeURIComponent(workspaceId)}`;
}

function jsonRequest(method: "POST" | "PUT" | "DELETE", body: object, signal?: AbortSignal): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  };
}

async function requestJson(path: string, init: RequestInit): Promise<unknown> {
  let response: Response;
  try {
    response = await apiFetch(apiUrl(path), init);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ResearchApiError(
      cause instanceof Error ? cause.message : "The research service could not be reached.",
      null,
      "network",
    );
  }

  if (!response.ok) {
    throw new ResearchApiError(await readError(response), response.status, "http");
  }
  try {
    return await response.json();
  } catch {
    throw contractError("The research service returned invalid JSON.");
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const payload = record(await response.json(), "error response");
    const detail = payload.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (isRecord(detail) && typeof detail.message === "string" && detail.message.trim()) {
      return detail.message;
    }
    if (typeof payload.message === "string" && payload.message.trim()) return payload.message;
  } catch {
    // A malformed error response is still represented by its HTTP status below.
  }
  return `Research request failed (${response.status}).`;
}

function parseWorkspace(value: unknown): ResearchWorkspaceSummary {
  const item = record(value, "workspace");
  const status = text(item.status, "workspace.status");
  if (status !== "active" && status !== "archived" && status !== "deleted") {
    throw contractError(`Unknown workspace status: ${status}`);
  }
  return {
    workspace_id: text(item.workspace_id, "workspace.workspace_id"),
    title: text(item.title, "workspace.title"),
    subject_id: nullableText(item.subject_id, "workspace.subject_id"),
    status,
    revision: integer(item.revision, "workspace.revision"),
    active_brief_id: nullableText(item.active_brief_id, "workspace.active_brief_id"),
    created_at: text(item.created_at, "workspace.created_at"),
    updated_at: text(item.updated_at, "workspace.updated_at"),
  };
}

function parseBrief(value: unknown): ResearchBrief {
  const item = record(value, "brief");
  const sourcePolicy = text(item.source_policy, "brief.source_policy");
  if (sourcePolicy !== "web" && sourcePolicy !== "knowledge_base" && sourcePolicy !== "mixed") {
    throw contractError(`Unknown source policy: ${sourcePolicy}`);
  }
  return {
    brief_id: text(item.brief_id, "brief.brief_id"),
    workspace_id: text(item.workspace_id, "brief.workspace_id"),
    version: integer(item.version, "brief.version"),
    question: text(item.question, "brief.question"),
    objectives: stringArray(item.objectives, "brief.objectives"),
    constraints: stringArray(item.constraints, "brief.constraints"),
    source_policy: sourcePolicy,
    knowledge_base: parseKnowledgeBaseBinding(item.knowledge_base),
    continuation: parseContinuation(item.continuation),
    created_at: text(item.created_at, "brief.created_at"),
  };
}

function parseContinuation(value: unknown): ResearchBrief["continuation"] {
  if (value === null || value === undefined) return null;
  const item = record(value, "brief.continuation");
  return {
    parent_run_id: text(item.parent_run_id, "brief.continuation.parent_run_id"),
    report_id: text(item.report_id, "brief.continuation.report_id"),
    report_revision: integer(item.report_revision, "brief.continuation.report_revision"),
  };
}

function parseKnowledgeBaseBinding(value: unknown): ResearchKnowledgeBaseBinding | null {
  if (value === null || value === undefined) return null;
  const item = record(value, "brief.knowledge_base");
  const source = text(item.source, "brief.knowledge_base.source");
  if (source !== "admin" && source !== "user") {
    throw contractError(`Unknown knowledge-base source: ${source}`);
  }
  return {
    resource_id: text(item.resource_id, "brief.knowledge_base.resource_id"),
    display_name: text(item.display_name, "brief.knowledge_base.display_name"),
    source,
  };
}

function parseRun(value: unknown): ResearchRun {
  const item = record(value, "run");
  const status = text(item.status, "run.status");
  if (!isRunState(status)) {
    // Unknown states must never be treated as active or terminal by the UI.
    throw contractError(`Unknown research run state: ${status}`);
  }
  return {
    run_id: text(item.run_id, "run.run_id"),
    workspace_id: text(item.workspace_id, "run.workspace_id"),
    brief_id: text(item.brief_id, "run.brief_id"),
    brief_version: integer(item.brief_version, "run.brief_version"),
    status,
    revision: integer(item.revision, "run.revision"),
    fencing_epoch: integer(item.fencing_epoch, "run.fencing_epoch"),
    failure_reason: nullableText(item.failure_reason, "run.failure_reason"),
    created_at: text(item.created_at, "run.created_at"),
    updated_at: text(item.updated_at, "run.updated_at"),
  };
}

function parseSource(value: unknown): ResearchSource {
  const item = record(value, "source");
  const status = textOrDefault(item.status, "source.status", "active");
  if (status !== "active" && status !== "invalidated") {
    throw contractError(`Unknown source status: ${status}`);
  }
  return {
    source_id: text(item.source_id, "source.source_id"),
    workspace_id: text(item.workspace_id, "source.workspace_id"),
    url: text(item.url, "source.url"),
    title: text(item.title, "source.title"),
    excerpt: nullableText(item.excerpt, "source.excerpt"),
    retrieved_at: text(item.retrieved_at, "source.retrieved_at"),
    revision: integerOrDefault(item.revision, "source.revision", 1),
    status,
    invalidated_at: nullableText(item.invalidated_at, "source.invalidated_at"),
    invalidation_reason: nullableText(item.invalidation_reason, "source.invalidation_reason"),
  };
}

function parseClaim(value: unknown): ResearchClaim {
  const item = record(value, "claim");
  const kind = text(item.kind, "claim.kind");
  if (kind !== "grounded" && kind !== "inference") {
    throw contractError(`Unknown claim kind: ${kind}`);
  }
  const evidenceStatus = textOrDefault(item.evidence_status, "claim.evidence_status", "active");
  if (evidenceStatus !== "active" && evidenceStatus !== "needs_review") {
    throw contractError(`Unknown claim evidence status: ${evidenceStatus}`);
  }
  return {
    claim_id: text(item.claim_id, "claim.claim_id"),
    workspace_id: text(item.workspace_id, "claim.workspace_id"),
    run_id: text(item.run_id, "claim.run_id"),
    text: text(item.text, "claim.text"),
    kind,
    source_ids: stringArray(item.source_ids, "claim.source_ids"),
    created_at: text(item.created_at, "claim.created_at"),
    revision: integerOrDefault(item.revision, "claim.revision", 1),
    evidence_status: evidenceStatus,
    review_required_source_ids: stringArrayOrDefault(item.review_required_source_ids, "claim.review_required_source_ids"),
  };
}

function parseNote(value: unknown): ResearchNote {
  const item = record(value, "note");
  return {
    note_id: text(item.note_id, "note.note_id"),
    workspace_id: text(item.workspace_id, "note.workspace_id"),
    body: text(item.body, "note.body"),
    source_ids: stringArray(item.source_ids, "note.source_ids"),
    revision: integer(item.revision, "note.revision"),
    created_at: text(item.created_at, "note.created_at"),
    updated_at: text(item.updated_at, "note.updated_at"),
  };
}

function parseReport(value: unknown): ResearchReport {
  const item = record(value, "report");
  const evidenceStatus = textOrDefault(item.evidence_status, "report.evidence_status", "active");
  if (evidenceStatus !== "active" && evidenceStatus !== "needs_review") {
    throw contractError(`Unknown report evidence status: ${evidenceStatus}`);
  }
  return {
    report_id: text(item.report_id, "report.report_id"),
    workspace_id: text(item.workspace_id, "report.workspace_id"),
    run_id: text(item.run_id, "report.run_id"),
    body: text(item.body, "report.body"),
    claims: array(item.claims, "report.claims").map(parseClaim),
    created_at: text(item.created_at, "report.created_at"),
    revision: integerOrDefault(item.revision, "report.revision", 1),
    evidence_status: evidenceStatus,
    review_required_source_ids: stringArrayOrDefault(item.review_required_source_ids, "report.review_required_source_ids"),
  };
}

async function getResearchReport(
  workspaceId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<ResearchReport | null> {
  try {
    const payload = await requestJson(
      `${workspacePath(workspaceId)}/runs/${encodeURIComponent(runId)}/report`,
      { method: "GET", cache: "no-store", signal },
    );
    return parseReport(payload);
  } catch (cause) {
    // A completed state and its durable report can become visible in adjacent
    // reads. A missing report is therefore an empty state, not a fake report.
    if (cause instanceof ResearchApiError && cause.status === 404) return null;
    throw cause;
  }
}

function selectActiveBrief(
  workspace: ResearchWorkspaceSummary,
  briefs: ResearchBrief[],
): ResearchBrief | null {
  const matching = briefs.filter((brief) => brief.brief_id === workspace.active_brief_id);
  if (!matching.length) return null;
  return [...matching].sort((a, b) => b.version - a.version)[0];
}

function deduplicateClaims(claims: ResearchClaim[]): ResearchClaim[] {
  return [...new Map(claims.map((claim) => [claim.claim_id, claim])).values()];
}

function isRunState(value: string): value is ResearchRunState {
  return (RESEARCH_RUN_STATES as readonly string[]).includes(value);
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) throw contractError(`Invalid ${label}.`);
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw contractError(`Invalid ${label}.`);
  return value;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) throw contractError(`Invalid ${label}.`);
  return value;
}

function textOrDefault(value: unknown, label: string, fallback: string): string {
  if (value == null) return fallback;
  return text(value, label);
}

function nullableText(value: unknown, label: string): string | null {
  if (value == null) return null;
  return text(value, label);
}

function integer(value: unknown, label: string): number {
  if (!Number.isInteger(value) || (value as number) < 0) throw contractError(`Invalid ${label}.`);
  return value as number;
}

function integerOrDefault(value: unknown, label: string, fallback: number): number {
  if (value == null) return fallback;
  return integer(value, label);
}

function stringArray(value: unknown, label: string): string[] {
  return array(value, label).map((entry, index) => text(entry, `${label}[${index}]`));
}

function stringArrayOrDefault(value: unknown, label: string): string[] {
  if (value == null) return [];
  return stringArray(value, label);
}

function contractError(message: string): ResearchApiError {
  return new ResearchApiError(message, null, "contract");
}
