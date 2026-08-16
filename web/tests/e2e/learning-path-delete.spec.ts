import { expect, test } from "@playwright/test";

test("learning paths support single and multi-select deletion", async ({ page }) => {
  let packs = ["Algebra", "Biology", "Chemistry"].map((title, index) => ({
    pack_id: `pack-${index + 1}`,
    title,
    goal: { text: `${title} goal`, status: "active" },
    materials: [],
    material_revisions: [],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    component_plans: [],
    due_review_count: 0,
    created_at: "2026-08-11T00:00:00+00:00",
    updated_at: "2026-08-11T00:00:00+00:00",
  }));

  await page.addInitScript(() => {
    window.localStorage.setItem("traittutor:onboarding-profile-dismissed", "true");
    window.localStorage.setItem("traittutor-language", "en");
  });
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/auth/status") {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: "e2e" } });
      return;
    }
    if (url.pathname === "/api/v1/learning-packs" && request.method() === "GET") {
      await route.fulfill({ json: { packs, total: packs.length } });
      return;
    }
    if (url.pathname === "/api/v1/learning-packs" && request.method() === "DELETE") {
      const body = request.postDataJSON() as { pack_ids: string[] };
      const deletedIds = body.pack_ids.filter((id) => packs.some((pack) => pack.pack_id === id));
      packs = packs.filter((pack) => !deletedIds.includes(pack.pack_id));
      await route.fulfill({
        json: { deleted_ids: deletedIds, missing_ids: [], deleted_count: deletedIds.length },
      });
      return;
    }
    if (url.pathname.startsWith("/api/v1/learning-packs/") && request.method() === "DELETE") {
      const deletedId = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
      packs = packs.filter((pack) => pack.pack_id !== deletedId);
      await route.fulfill({ json: { deleted_id: deletedId } });
      return;
    }
    if (url.pathname === "/api/v1/settings") {
      await route.fulfill({ json: { catalog: {} } });
      return;
    }
    if (url.pathname === "/api/v1/research/workspaces") {
      await route.fulfill({ json: { workspaces: [] } });
      return;
    }
    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto("/learning");
  await expect(page.getByRole("heading", { name: "Algebra goal" })).toBeVisible();

  await page.getByRole("button", { name: /Algebra goal/ }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Algebra goal");
  await expect(dialog.locator("[data-autofocus]")).toBeFocused();
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe("hidden");
  await page.getByRole("alertdialog").getByRole("button", { name: /Delete|确认删除/ }).click();
  await expect(page.getByRole("heading", { name: "Algebra goal" })).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText(/Deleted 1 learning path|已删除 1 条学习路径/);

  await page.getByRole("checkbox", { name: /Biology goal/ }).check();
  await page.getByRole("checkbox", { name: /Chemistry goal/ }).check();
  await page.getByRole("button", { name: /Delete selected \(2\)|删除已选（2）/ }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: /Delete|确认删除/ }).click();

  await expect(page.getByRole("heading", { name: "Biology goal" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Chemistry goal" })).toHaveCount(0);
  const emptyState = page.getByTestId("learning-empty-state");
  await expect(emptyState.getByText(/No active learning goal yet|还没有进行中的学习目标/)).toBeVisible();
  await expect(emptyState.getByRole("status")).toContainText(
    /Deleted 2 learning paths|已删除 2 条学习路径/,
  );
  const [receiptBox, emptyCardBox] = await Promise.all([
    emptyState.getByTestId("learning-mutation-success").boundingBox(),
    emptyState.getByTestId("learning-empty-card").boundingBox(),
  ]);
  expect(receiptBox).not.toBeNull();
  expect(emptyCardBox).not.toBeNull();
  expect(receiptBox!.y).toBeLessThan(emptyCardBox!.y);
  expect(receiptBox!.y + receiptBox!.height).toBeGreaterThan(emptyCardBox!.y);
});
