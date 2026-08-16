import { expect, test, type Page } from "@playwright/test";

type SectionStatus = "ready" | "empty" | "unavailable" | "stale" | "rebuilding";

interface SectionMeta {
  status: SectionStatus;
  updated_at: string | null;
  source_refs: string[];
  unavailable_sources: string[];
}

const updatedAt = "2026-08-11T08:00:00+00:00";

function meta(status: SectionStatus = "ready"): SectionMeta {
  return {
    status,
    updated_at: updatedAt,
    source_refs: ["learning-model:e2e-safe-summary"],
    unavailable_sources: [],
  };
}

const confirmedSubject = {
  subject_id: "math",
  label: "Mathematics",
  last_activity_at: updatedAt,
  covered_kc_count: 3,
  strong_evidence_count: 2,
  open_error_count: 1,
  due_review_count: 1,
  source_refs: ["learner-event:event-1", "knowledge-state:math"],
};

const pendingSubject = {
  subject_id: "pending-physics",
  label: "Physics notes",
  created_at: updatedAt,
  source_refs: ["learning-pack:pending-pack"],
  possible_duplicate_subject_ids: [],
};

const subjectRef = {
  subject_id: "math",
  label: "Mathematics",
  path: ["Mathematics"],
  confidence: 1,
  source: "user",
  confirmed: true,
};

const learnerProfileResponse = {
  scope: "subject",
  subject: subjectRef,
  inference_enabled: true,
  preferences: [],
  concept_signals: [],
  strategy_evidence: [],
  understanding: null,
  updated_at: updatedAt,
  needs_rebuild: false,
};

const overviewResponse = {
  generated_at: updatedAt,
  today: {
    meta: meta(),
    active_subject_count: 1,
    due_review_count: 1,
    open_error_count: 1,
    attribution_pending_count: 0,
    latest_activity_at: updatedAt,
  },
  confirmed_subjects: {
    meta: meta(),
    items: [confirmedSubject],
  },
  pending_subjects: {
    meta: meta(),
    items: [pendingSubject],
  },
  task_queue: {
    meta: meta(),
    items: [{
      task_id: "repair-error-1",
      subject_id: "math",
      kind: "error_repair",
      due_at: updatedAt,
      source_refs: ["error:error-1"],
    }],
  },
  support: {
    meta: meta(),
    inference_enabled: true,
    confirmed_preference_count: 1,
    confirmed_reflection_count: 1,
    compass_signal_count: 1,
  },
};

const detailResponse = {
  generated_at: updatedAt,
  header: {
    subject_id: "math",
    label: "Mathematics",
    confirmed: true,
    updated_at: updatedAt,
    data_status: "ready",
  },
  tabs: {
    overview: { meta: meta(), item_count: 3, actionable_count: 2 },
    knowledge: {
      meta: meta(),
      item_count: 3,
      actionable_count: 1,
      model_version: "bkt-v1-uncalibrated",
      mapping_version: "kc-map-v1",
      mastery_items: [{
        kc_id: "algebra-signs",
        evidence_state: "insufficient_evidence",
        change_signal: "none",
        verified_observation_count: 2,
        model_version: "bkt-v1-uncalibrated",
        stage_policy_version: "bkt-stage-policy-v1",
      }],
    },
    errors: { meta: meta(), item_count: 1, actionable_count: 1 },
    reviews: { meta: meta(), item_count: 1, actionable_count: 1 },
    misconceptions: { meta: meta(), item_count: 1, actionable_count: 1 },
    support: { meta: meta(), item_count: 3, actionable_count: 1 },
    governance: { meta: meta(), item_count: 2, actionable_count: 0 },
  },
  allowed_actions: ["continue_learning", "start_review", "repair_error", "view_evidence"],
};

async function installLearningModelRoutes(
  page: Page,
  options: { unavailableOverviewSection?: "today" | "confirmed_subjects" | "pending_subjects" | "task_queue" | "support" } = {},
) {
  await page.addInitScript(() => {
    window.localStorage.setItem("traittutor:onboarding-profile-dismissed", "true");
  });

  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());

    if (url.pathname === "/api/v1/auth/status") {
      await route.fulfill({
        json: { enabled: false, authenticated: true, username: "learning-model-e2e" },
      });
      return;
    }

    if (url.pathname === "/api/v1/research/workspaces") {
      await route.fulfill({ json: [] });
      return;
    }

    if (url.pathname === "/api/v1/learning-model/overview") {
      const response = structuredClone(overviewResponse);
      const unavailable = options.unavailableOverviewSection;
      if (unavailable) {
        response[unavailable].meta = {
          ...response[unavailable].meta,
          status: "unavailable",
          updated_at: null,
          source_refs: [],
          unavailable_sources: ["error-store"],
        };
      }
      await route.fulfill({ json: response });
      return;
    }

    if (url.pathname === "/api/v1/learning-model/subjects/math") {
      await route.fulfill({ json: detailResponse });
      return;
    }

    if (url.pathname === "/api/v1/memory/learner/subjects/math") {
      await route.fulfill({ json: learnerProfileResponse });
      return;
    }

    if (url.pathname === "/api/v1/memory/learner/evidence") {
      await route.fulfill({ json: { evidence: [] } });
      return;
    }

    if (url.pathname === "/api/v1/memory/learner/subjects/math/knowledge-graph") {
      await route.fulfill({ json: { subject: subjectRef, nodes: [], edges: [], source_refs: [] } });
      return;
    }

    if (url.pathname === "/api/v1/memory/learner/reflections") {
      await route.fulfill({
        json: {
          reflections: [],
          summary: { candidate: 0, confirmed: 0, rejected: 0, stale: 0, needs_rebuild: 0, applies_to_compass: 0 },
        },
      });
      return;
    }

    if (url.pathname === "/api/v1/memory/learner/context/preview") {
      await route.fulfill({
        json: {
          purpose: "courseware",
          subject: subjectRef,
          active_goal: null,
          plan: { rationale: [], srl_support: [] },
          memory_snapshot: null,
          relevant_concept_signals: [],
          constraints: [],
          evidence_refs: [],
          degraded: false,
          degradation_reason: null,
        },
      });
      return;
    }

    if (url.pathname === "/api/v1/learning-state") {
      await route.fulfill({
        json: {
          subject_id: "math",
          source_revision: "e2e",
          param_version: "bkt-v1-uncalibrated",
          calibrated: false,
          strong_event_count: 2,
          knowledge: [],
        },
      });
      return;
    }

    if (["/api/v1/errors", "/api/v1/repairs", "/api/v1/misconceptions", "/api/v1/reviews"].includes(url.pathname)) {
      await route.fulfill({ json: [] });
      return;
    }

    // App-shell requests are intentionally kept separate from the two page
    // read models. No fallback contains answers, rubrics, raw event text, or
    // data belonging to another owner.
    await route.fulfill({ status: 200, json: {} });
  });
}

function area(page: Page, name: RegExp) {
  return page.locator("section").filter({
    has: page.getByRole("heading", { name }),
  }).first();
}

test("overview renders five independent areas, isolates pending subjects, and keeps unknown mastery non-numeric", async ({ page }) => {
  await installLearningModelRoutes(page);
  await page.goto("/settings/learning-model");

  const today = area(page, /Today(?:'s|’s) learning summary|今日学习摘要/i);
  const subjects = area(page, /My subjects|我的学科/i);
  const pending = area(page, /Pending subjects|待确认学科/i);
  const tasks = area(page, /Learning task queue|学习任务队列/i);
  const governance = area(page, /Profile governance|画像治理/i);

  await expect(today).toBeVisible();
  await expect(subjects).toBeVisible();
  await expect(pending).toBeVisible();
  await expect(tasks).toBeVisible();
  await expect(governance).toBeVisible();

  await expect(subjects.getByText("Mathematics", { exact: true })).toHaveCount(1);
  await expect(subjects.getByText("Physics notes", { exact: true })).toHaveCount(0);
  await expect(pending.getByText("Physics notes", { exact: true })).toBeVisible();

  await expect(subjects).toContainText(/Insufficient evidence|Evidence insufficient|证据不足/i);
  await expect(subjects.getByText(/\b\d+(?:\.\d+)?%\b/)).toHaveCount(0);
  await expect(subjects.getByRole("progressbar")).toHaveCount(0);
});

test("task actions deep-link to the matching subject tab", async ({ page }) => {
  await installLearningModelRoutes(page);
  await page.goto("/settings/learning-model");

  const tasks = area(page, /Learning task queue|学习任务队列/i);
  const repair = tasks.getByRole("link", { name: /Repair error|修复错题/i });
  await expect(repair).toHaveAttribute("href", "/settings/learning-model/math?tab=errors");
  await repair.click();

  await expect(page).toHaveURL(/\/settings\/learning-model\/math\?tab=errors$/);
  await expect(page.getByRole("tab", { name: /Errors and repairs|错题与修复/i })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("all seven subject tabs switch through the query string and survive refresh", async ({ page }) => {
  await installLearningModelRoutes(page);
  await page.goto("/settings/learning-model/math?tab=misconceptions");

  const tabs = page.getByRole("tablist");
  const expectedTabs = [
    /Overview|总览/i,
    /Knowledge and KC|知识与 KC/i,
    /Errors and repairs|错题与修复/i,
    /Review plan|复习计划/i,
    /Misconception candidates|误区候选/i,
    /Teaching support|教学支持/i,
    /Data and governance|数据与治理/i,
  ];
  for (const name of expectedTabs) {
    await expect(tabs.getByRole("tab", { name })).toBeVisible();
  }

  const misconceptions = tabs.getByRole("tab", { name: /Misconception candidates|误区候选/i });
  await expect(misconceptions).toHaveAttribute("aria-selected", "true");
  await page.reload();
  await expect(page).toHaveURL(/\?tab=misconceptions$/);
  await expect(page.getByRole("tab", { name: /Misconception candidates|误区候选/i })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await page.getByRole("tab", { name: /Review plan|复习计划/i }).click();
  await expect(page).toHaveURL(/\?tab=reviews$/);
  await expect(page.getByRole("tab", { name: /Review plan|复习计划/i })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("an unavailable overview area does not hide the remaining ready areas", async ({ page }) => {
  await installLearningModelRoutes(page, { unavailableOverviewSection: "task_queue" });
  await page.goto("/settings/learning-model");

  await expect(area(page, /Today(?:'s|’s) learning summary|今日学习摘要/i)).toBeVisible();
  await expect(area(page, /My subjects|我的学科/i).getByText("Mathematics", { exact: true })).toBeVisible();
  await expect(area(page, /Pending subjects|待确认学科/i).getByText("Physics notes", { exact: true })).toBeVisible();
  await expect(area(page, /Profile governance|画像治理/i)).toBeVisible();

  const tasks = area(page, /Learning task queue|学习任务队列/i);
  await expect(tasks).toBeVisible();
  await expect(tasks).toContainText(/temporarily unavailable|暂时不可用|不可用/i);
});
