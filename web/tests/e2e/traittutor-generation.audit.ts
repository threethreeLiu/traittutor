import { expect, test } from "@playwright/test";

test("courseware generation shows a saved learning result and visual", async ({ page }) => {
  await page.goto("/space/courseware");

  await expect(page.getByRole("heading", { name: /课件|courseware/i })).toBeVisible();
  await page.getByLabel(/学习包名称|learning pack name/i).fill("Playwright 光合作用测试");
  await page.getByLabel(/材料或已有题目|material or existing question/i).fill(
    "光合作用是植物利用光能将二氧化碳和水转化为有机物并释放氧气的过程。叶绿体是主要场所。",
  );
  await page.getByRole("button", { name: /开始生成|generate/i }).click();

  await expect(page.getByText(/已生成并保存|generated and saved/i)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("heading", { name: /光合作用|photosynthesis/i })).toBeVisible();
  await expect(page.locator("img[alt]").first()).toBeVisible({ timeout: 30_000 });
});
