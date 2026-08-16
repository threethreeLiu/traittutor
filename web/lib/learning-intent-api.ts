import { apiFetch, apiUrl } from "./api";

export type LearnIntentMode = "conversation" | "learning_path";
export type LearnSafetyAction = "allow" | "confirm" | "block";

export interface LearnIntentResult {
  mode: LearnIntentMode;
  confidence: number;
  rationale: string;
  fallback_required: boolean;
  safety_action: LearnSafetyAction;
}

export async function routeLearnIntent(
  message: string,
  sessionId?: string,
  attachmentText?: string,
): Promise<LearnIntentResult> {
  const res = await apiFetch(apiUrl("/api/v1/learning/intent"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId, attachment_text: attachmentText }),
  });
  if (!res.ok) throw new Error(`Failed to route Learn intent: ${res.status}`);
  return res.json() as Promise<LearnIntentResult>;
}
