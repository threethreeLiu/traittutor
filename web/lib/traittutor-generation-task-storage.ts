export type PendingGenerationTask = {
  generationId: string;
  status?: "queued" | "running" | "failed" | "cancelled" | "interrupted";
  surface?: "chat" | "space";
  sessionId?: string;
  createdAt?: string;
  packId?: string;
};

const KEY_PREFIX = "traittutor:pending-generation:";

function storage(): Storage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

export function pendingGenerationTaskKey(scope: string): string {
  return `${KEY_PREFIX}${scope}`;
}

export function readPendingGenerationTasks(scope: string): PendingGenerationTask[] {
  try {
    const raw = storage()?.getItem(pendingGenerationTaskKey(scope));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    const rows = Array.isArray(parsed) ? parsed : [parsed]; // one-record v1 migration
    return rows.flatMap((value) => {
      const task = value as Partial<PendingGenerationTask>;
      if (typeof task.generationId !== "string" || !task.generationId.trim()) return [];
      return [{
        generationId: task.generationId,
        ...(typeof task.packId === "string" && task.packId ? { packId: task.packId } : {}),
        ...(typeof task.status === "string" ? { status: task.status } : {}),
        ...(task.surface === "chat" || task.surface === "space" ? { surface: task.surface } : {}),
        ...(typeof task.sessionId === "string" && task.sessionId ? { sessionId: task.sessionId } : {}),
        ...(typeof task.createdAt === "string" && task.createdAt ? { createdAt: task.createdAt } : {}),
      }];
    });
  } catch {
    return [];
  }
}

export function writePendingGenerationTask(scope: string, task: PendingGenerationTask): void {
  const existing = readPendingGenerationTasks(scope);
  const previous = existing.find((entry) => entry.generationId === task.generationId);
  const next = [...existing.filter((entry) => entry.generationId !== task.generationId), { ...previous, ...task }];
  storage()?.setItem(pendingGenerationTaskKey(scope), JSON.stringify(next));
}

export function removePendingGenerationTask(scope: string, generationId: string): void {
  const next = readPendingGenerationTasks(scope).filter((entry) => entry.generationId !== generationId);
  if (next.length) storage()?.setItem(pendingGenerationTaskKey(scope), JSON.stringify(next));
  else storage()?.removeItem(pendingGenerationTaskKey(scope));
}
