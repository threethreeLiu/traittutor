export type EvidenceState =
  | "insufficient_evidence"
  | "needs_support"
  | "developing"
  | "supported";

export type ChangeSignal = "none" | "needs_review" | "repaired" | "due_for_review";

export interface MasteryEvidence {
  evidenceState?: EvidenceState | null;
  changeSignal?: ChangeSignal | null;
}

export function masteryDisplay(evidence: MasteryEvidence): {
  evidenceState: EvidenceState;
  changeSignal: ChangeSignal;
} {
  return {
    evidenceState: evidence.evidenceState ?? "insufficient_evidence",
    changeSignal: evidence.changeSignal ?? "none",
  };
}
