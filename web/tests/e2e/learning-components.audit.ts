import { expect, test } from "@playwright/test";

const plan = {
  plan_id: "plan-e2e",
  pack_id: "pack-e2e",
  version: 1,
  goal: "Understand linear functions",
  subject_ref: { subject_id: "mathematics", label: "Mathematics" },
  support_state_snapshot: {
    subject_id: "mathematics",
    source: "default",
    dimensions: {},
    boundary: "Teaching support only; no diagnosis or ability label.",
  },
  components: [
    {
      component_id: "cmp-goal",
      component_type: "goal_map",
      executor: "deterministic",
      label_zh: "目标地图",
      label_en: "Goal map",
      concept_refs: [],
      support_dimensions: ["goal_planning"],
      bkt_stage: "unobserved",
      modality: "text",
      dependencies: [],
      required: true,
      reason: "Make the learning target visible.",
      evidence_refs: [],
      completion_event: "goal_confirmed",
      status: "pending",
    },
    {
      component_id: "cmp-diagnostic",
      component_type: "diagnostic_check",
      executor: "assessment",
      label_zh: "起点诊断",
      label_en: "Starting diagnostic",
      concept_refs: ["slope"],
      support_dimensions: ["monitoring_regulation"],
      bkt_stage: "unobserved",
      modality: "interactive",
      dependencies: ["cmp-goal"],
      required: true,
      reason: "There is no graded subject evidence yet.",
      evidence_refs: [],
      completion_event: "quiz_answer",
      status: "pending",
    },
  ],
  status: "active",
  created_at: "2026-08-03T00:00:00+00:00",
  updated_at: "2026-08-03T00:00:00+00:00",
};

const pack = {
  pack_id: "pack-e2e",
  title: "Linear functions",
  goal: { text: "Understand linear functions", status: "active" },
  sources: [],
  material: { source_type: "paste", title: "Linear functions", text: "Slope and intercept" },
  artifacts: { courseware: [], flashcards: [], quiz: [] },
  flashcard_progress: {},
  quiz_attempts: [],
  component_plans: [plan],
  active_plan_id: "plan-e2e",
  component_progress: {},
  created_at: "2026-08-03T00:00:00+00:00",
  updated_at: "2026-08-03T00:00:00+00:00",
};

test.describe("TraitTutor learning component product", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/v1/learning-packs", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ json: { packs: [pack], total: 1 } });
        return;
      }
      await route.continue();
    });
    await page.route("**/api/v1/learning-packs/pack-e2e", async (route) => {
      await route.fulfill({ json: pack });
    });
  });

  test("opens an active path in the unified canvas", async ({ page }) => {
    await page.goto("/space/learning");
    await expect(page.getByRole("heading", { name: /My learning|我的学习/ })).toBeVisible();
    await expect(page.getByText("Understand linear functions")).toBeVisible();
    await page.getByText("Understand linear functions").click();
    await expect(page).toHaveURL(/\/space\/learning\/pack-e2e/);
    await expect(page.getByRole("heading", { name: /Goal map|目标地图/ })).toBeVisible();
    await expect(page.getByText(/Learning rationale|学习依据/)).toBeVisible();
    await expect(page.getByText(/Starting diagnostic|起点诊断/)).toBeVisible();

    const sidebarWidth = await page.locator("aside").first().evaluate((sidebar) => sidebar.getBoundingClientRect().width);
    expect(sidebarWidth).toBe(60);

    const geometry = await page.locator(".learning-canvas").evaluate((canvas) => {
      const rect = canvas.getBoundingClientRect();
      const parent = canvas.parentElement?.getBoundingClientRect();
      return { width: rect.width, height: rect.height, parentWidth: parent?.width ?? 0 };
    });
    expect(geometry.width).toBeGreaterThan(1000);
    expect(Math.abs(geometry.width - geometry.parentWidth)).toBeLessThanOrEqual(1);
    expect(geometry.height).toBeGreaterThan(700);
    if (process.env.CAPTURE_UI === "1") {
      await page.screenshot({ path: "test-results/learning-canvas-desktop.png", fullPage: true });
    }
  });

  test("keeps the learning path usable on a mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/space/learning/pack-e2e");
    await expect(page.getByRole("heading", { name: /Goal map|目标地图/ })).toBeVisible();
    const why = page.getByRole("button", { name: /Why this step|为什么这一步/ });
    await why.click();
    await expect(page.getByText(/Learning rationale|学习依据/).last()).toBeVisible();
    if (process.env.CAPTURE_UI === "1") {
      await page.screenshot({ path: "test-results/learning-canvas-mobile.png", fullPage: true });
    }
  });
});
