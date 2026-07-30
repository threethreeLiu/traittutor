import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  pendingGenerationTaskKey,
  readPendingGenerationTasks,
  removePendingGenerationTask,
  writePendingGenerationTask,
} from "@/lib/traittutor-generation-task-storage";

const api = readFileSync(path.resolve(process.cwd(), "lib/traittutor-api.ts"), "utf8");
const chatPanel = readFileSync(path.resolve(process.cwd(), "components/chat/home/ChatGenerationPanel.tsx"), "utf8");
const workbench = readFileSync(path.resolve(process.cwd(), "components/space/StudyToolWorkbench.tsx"), "utf8");

test("generation task API exposes cancel, retry, and resumable SSE subscriptions", () => {
  assert.match(api, /cancelTraitTutorGenerationTask/);
  assert.match(api, /retryTraitTutorGenerationTask/);
  assert.match(api, /after_seq=/);
  assert.match(api, /"cancelled", "interrupted", "retry_queued"/);
  assert.match(api, /GenerationTaskStatus = "queued" \| "running" \| "completed" \| "failed" \| "cancelled" \| "interrupted"/);
});

test("chat and workspace generation surfaces provide cancellation and retry controls", () => {
  for (const source of [chatPanel, workbench]) {
    assert.match(source, /cancelTraitTutorGenerationTask/);
    assert.match(source, /retryTraitTutorGenerationTask/);
    assert.match(source, /taskStatus/);
    assert.match(source, /retryable/);
  }
});

test("pending generation task recovery is scope-isolated and clears only its own task", () => {
  const values = new Map<string, string>();
  const previousWindow = globalThis.window;
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { localStorage: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    } },
  });
  try {
    writePendingGenerationTask("chat:session-a:quiz", { generationId: "task-a" });
    writePendingGenerationTask("chat:session-b:quiz", { generationId: "task-b", packId: "pack-b" });
    writePendingGenerationTask("chat:session-a:quiz", { generationId: "task-a-2", status: "running" });
    assert.deepEqual(readPendingGenerationTasks("chat:session-a:quiz").map((task) => task.generationId).sort(), ["task-a", "task-a-2"]);
    assert.deepEqual(readPendingGenerationTasks("chat:session-b:quiz"), [{ generationId: "task-b", packId: "pack-b" }]);
    assert.deepEqual(readPendingGenerationTasks("chat:session-c:quiz"), []);

    removePendingGenerationTask("chat:session-a:quiz", "task-a");
    assert.deepEqual(readPendingGenerationTasks("chat:session-a:quiz"), [{ generationId: "task-a-2", status: "running" }]);
    assert.deepEqual(readPendingGenerationTasks("chat:session-b:quiz"), [{ generationId: "task-b", packId: "pack-b" }]);

    values.set(pendingGenerationTaskKey("chat:session-b:quiz"), "not-json");
    assert.deepEqual(readPendingGenerationTasks("chat:session-b:quiz"), []);
  } finally {
    Object.defineProperty(globalThis, "window", { configurable: true, value: previousWindow });
  }
});
