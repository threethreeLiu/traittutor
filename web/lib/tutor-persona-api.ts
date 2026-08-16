import { apiFetch, apiUrl } from "@/lib/api";

export type AddressTerm = "name" | "you" | "learner" | "classmate";
export type AvatarRef = "default" | "mentor" | "guide" | "study_buddy";
export type VoiceId = "default" | "calm" | "bright" | "steady";
export type TutorTone = "warm" | "neutral" | "energetic" | "calm";
export type PersonaIntensity = "low" | "medium" | "high";
export type FeedbackFormat = "concise" | "balanced" | "detailed" | "socratic";
export type PersonaProactivity = "off" | "reminders_only" | "moderate";
export type EmojiPolicy = "none" | "minimal" | "moderate";
export type TextScale = "standard" | "large" | "extra_large";

export interface QuietHours {
  enabled: boolean;
  start_local: string;
  end_local: string;
  timezone: string;
}

export interface PersonaAccessibilityPreferences {
  captions: boolean;
  reduced_motion: boolean;
  screen_reader_optimized: boolean;
  text_scale: TextScale;
}

/** Complete public whitelist. There is deliberately no prompt or instruction field. */
export interface TutorPersonaSettings {
  name: string;
  address_terms: AddressTerm[];
  avatar_ref: AvatarRef;
  voice_id: VoiceId;
  speech_rate: number;
  tone: TutorTone;
  directness: PersonaIntensity;
  humor_level: PersonaIntensity;
  encouragement_level: PersonaIntensity;
  feedback_format: FeedbackFormat;
  proactivity: PersonaProactivity;
  reminder_consent: boolean;
  emoji_policy: EmojiPolicy;
  quiet_hours: QuietHours;
  accessibility: PersonaAccessibilityPreferences;
  safety_version: "persona-safety-v1";
}

export interface TutorPersonaProfile {
  persona_id: string;
  version: number;
  settings: TutorPersonaSettings;
  created_at: string;
  updated_at: string;
}

export interface TutorPersonaContract {
  contract_version: "tutor-persona-contract.v1";
  persona_id: string;
  profile_version: number;
  identity: {
    display_name: string;
    address_terms: AddressTerm[];
    avatar_ref: AvatarRef;
  };
  expression: {
    tone: TutorTone;
    directness: PersonaIntensity;
    humor_level: PersonaIntensity;
    encouragement_level: PersonaIntensity;
    feedback_format: FeedbackFormat;
    proactivity: PersonaProactivity;
    emoji_policy: EmojiPolicy;
  };
  modality: {
    voice_id: VoiceId;
    speech_rate: number;
    accessibility: PersonaAccessibilityPreferences;
  };
  quiet_hours: QuietHours;
  safety_version: "persona-safety-v1";
}

export type TutorReminderStatus = "queued" | "delivered" | "read" | "cancelled";

export interface TutorReminder {
  reminder_id: string;
  kind: "review_due";
  reference_id: string;
  learning_path_id: string;
  subject_id: string;
  kc_id: string;
  due_at: string;
  status: TutorReminderStatus;
  queued_at: string;
  delivered_at: string | null;
  read_at: string | null;
  cancelled_at: string | null;
}

export class TutorPersonaApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly expectedVersion?: number,
    readonly actualVersion?: number,
  ) {
    super(message);
    this.name = "TutorPersonaApiError";
  }
}

interface ErrorPayload {
  detail?:
    | string
    | {
        code?: string;
        expected_version?: number;
        actual_version?: number;
      };
}

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;

  const payload = (await response.json().catch(() => ({}))) as ErrorPayload;
  const detail = payload.detail;
  if (detail && typeof detail === "object") {
    throw new TutorPersonaApiError(
      detail.code || `Request failed: ${response.status}`,
      response.status,
      detail.code,
      detail.expected_version,
      detail.actual_version,
    );
  }
  throw new TutorPersonaApiError(
    typeof detail === "string" ? detail : `Request failed: ${response.status}`,
    response.status,
  );
}

function personaRequest<T>(
  path = "",
  init: RequestInit = {},
): Promise<T> {
  return apiFetch(apiUrl(`/api/v1/tutor-personas${path}`), init).then(responseJson<T>);
}

export function getTutorPersona(signal?: AbortSignal): Promise<TutorPersonaProfile> {
  return personaRequest("", { cache: "no-store", signal });
}

export function replaceTutorPersona(
  settings: TutorPersonaSettings,
  expectedVersion: number,
  idempotencyKey: string,
): Promise<TutorPersonaProfile> {
  return personaRequest("", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      settings,
      expected_version: expectedVersion,
      idempotency_key: idempotencyKey,
    }),
  });
}

export function resetTutorPersona(
  expectedVersion: number,
  idempotencyKey: string,
): Promise<TutorPersonaProfile> {
  return personaRequest("/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_version: expectedVersion,
      idempotency_key: idempotencyKey,
    }),
  });
}

export function previewTutorPersona(
  settings: TutorPersonaSettings,
  signal?: AbortSignal,
): Promise<TutorPersonaContract> {
  return personaRequest("/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings }),
    signal,
  });
}

export function listTutorReminders(
  status: TutorReminderStatus = "delivered",
  signal?: AbortSignal,
): Promise<TutorReminder[]> {
  return personaRequest(`/reminders?status=${encodeURIComponent(status)}`, {
    cache: "no-store",
    signal,
  });
}

export function acknowledgeTutorReminder(reminderId: string): Promise<TutorReminder> {
  return personaRequest(`/reminders/${encodeURIComponent(reminderId)}/read`, {
    method: "POST",
  });
}

export function cancelTutorReminder(reminderId: string): Promise<TutorReminder> {
  return personaRequest(`/reminders/${encodeURIComponent(reminderId)}`, {
    method: "DELETE",
  });
}
