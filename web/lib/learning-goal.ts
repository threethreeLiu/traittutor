export type LearningStarterKind = "quiz" | "courseware" | "flashcards";

const LEARNING_GOAL_PATTERNS = [
  /我想(?:要)?学(?:习|会|懂)?/i,
  /(?:请|能不能)?教我/i,
  /带我(?:学习|入门)/i,
  /帮我(?:学习|备考|掌握)/i,
  /(?:入门|备考|掌握|学会).{1,48}/i,
  /i\s+(?:want|would like)\s+to\s+learn/i,
  /(?:teach|help)\s+me\s+(?:learn|study)/i,
  /(?:learn|study|get started with|prepare for)\s+.{2,80}/i,
];

export function isLearningGoalMessage(message: string): boolean {
  const normalized = message.trim().replace(/\s+/g, " ");
  if (normalized.length < 3 || normalized.length > 500) return false;
  return LEARNING_GOAL_PATTERNS.some((pattern) => pattern.test(normalized));
}

export function normalizeLearningGoal(message: string): string {
  return message.trim().replace(/\s+/g, " ").slice(0, 240);
}

export function learningStarterHref(
  kind: LearningStarterKind,
  goal: string,
  packId?: string | null,
): string {
  const params = new URLSearchParams({ goal: normalizeLearningGoal(goal) });
  params.set("autostart", "1");
  if (packId) params.set("pack", packId);
  if (kind === "quiz") {
    params.set("mode", "objective");
    params.set("questions", "3");
  }
  return `/space/${kind}?${params.toString()}`;
}
