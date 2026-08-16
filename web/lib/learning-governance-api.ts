import { apiFetch, apiUrl } from "@/lib/api";
import type { ChangeSignal, EvidenceState } from "@/lib/mastery-display";

export type GovernanceAttributionStatus = "verified" | "attribution_pending";
export type ErrorRecordStatus = "open" | "repaired" | "relapsed";

export interface GovernanceError {
  error_id: string;
  question_id: string;
  subject_id: string;
  kc_id: string;
  module_id: string;
  error_type: "structural" | "deviation" | "application" | "metacognitive";
  status: ErrorRecordStatus;
  attribution_status: GovernanceAttributionStatus;
  source_event_ids: string[];
  created_at: number;
  repaired_at: number | null;
  relapsed_at: number | null;
  last_seen_at: number | null;
}

export interface GovernanceRepair {
  error_id: string;
  subject_id: string;
  kc_id: string;
  status: ErrorRecordStatus;
  attribution_status: GovernanceAttributionStatus;
  attempt_count: number;
  successful_attempt_count: number;
  last_attempt_at: number | null;
}

export interface GovernanceMisconception {
  hypothesis_id: string;
  subject_id: string;
  kc_ids: string[];
  pattern: string;
  status: "candidate" | "confirmed" | "resolved";
  attribution_status: GovernanceAttributionStatus;
  evidence_count: number;
  created_at: string;
  updated_at: string;
}

export interface GovernanceReview {
  review_id: string;
  learning_path_id: string;
  subject_id: string;
  kc_id: string;
  knowledge_type: "memory" | "concept" | "procedure" | "design";
  due_at: number;
  priority: number;
  status: "due" | "upcoming";
  attribution_status: GovernanceAttributionStatus;
  interval_index: number;
}

export interface LearningGovernanceData {
  errors: GovernanceError[];
  repairs: GovernanceRepair[];
  misconceptions: GovernanceMisconception[];
  reviews: GovernanceReview[];
}

/** Safe canonical BKT projection; no owner id, answer, rubric, or raw event. */
export interface LearnerSubjectLearningState {
  subject_id: string;
  source_revision: string;
  param_version: string;
  calibrated: boolean;
  strong_event_count: number;
  knowledge: Array<{
    kc_id: string;
    evidence_state: EvidenceState;
    change_signal: ChangeSignal;
    verified_observation_count: number;
    model_version: string;
    stage_policy_version: string;
  }>;
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function governanceUrl(path: string, subjectId: string, kcId?: string): string {
  const query = new URLSearchParams({ subject_id: subjectId });
  if (kcId) query.set("kc_id", kcId);
  return apiUrl(`/api/v1/${path}?${query.toString()}`);
}

function get<T>(path: string, subjectId: string, kcId?: string, signal?: AbortSignal) {
  return apiFetch(governanceUrl(path, subjectId, kcId), {
    cache: "no-store",
    signal,
  }).then(json<T>);
}

export async function getLearningGovernance(
  subjectId: string,
  options: { kcId?: string; signal?: AbortSignal } = {},
): Promise<LearningGovernanceData> {
  const { kcId, signal } = options;
  const [errors, repairs, misconceptions, reviews] = await Promise.all([
    get<GovernanceError[]>("errors", subjectId, kcId, signal),
    get<GovernanceRepair[]>("repairs", subjectId, kcId, signal),
    get<GovernanceMisconception[]>("misconceptions", subjectId, kcId, signal),
    get<GovernanceReview[]>("reviews", subjectId, kcId, signal),
  ]);
  return { errors, repairs, misconceptions, reviews };
}

export async function getLearnerSubjectLearningState(
  subjectId: string,
  signal?: AbortSignal,
): Promise<LearnerSubjectLearningState> {
  return get<LearnerSubjectLearningState>("learning-state", subjectId, undefined, signal);
}
