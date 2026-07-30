import { test, expect } from "@playwright/test";

// Minimal compliance and UX checks without external deps (axe-core)
// Focus on semantic landmarks, headings, alt text, link names, and basic error messaging.

const BASE_URL =
  process.env.WEB_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://localhost:3000";

async function expectAnyVisible(
  locators: import("@playwright/test").Locator[],
  message: string,
) {
  for (const loc of locators) {
    try {
      if (await loc.first().isVisible()) return;
    } catch {}
  }
  expect(false, message).toBe(true);
}

test.describe("Compliance :: Accessibility & Semantics", () => {
  test("home page exposes main landmark and H1", async ({ page }) => {
    await page.goto(`${BASE_URL}/`);
    await page.waitForSelector("main", { timeout: 5000 });
    const main = page.locator("main");
    await expect(main, "Missing <main> landmark").toBeVisible();

    const h1 = page.locator("h1").first();
    await expect(h1, "Missing top-level <h1>").toBeVisible();
  });

  test("images provide alt text", async ({ page }) => {
    await page.goto(`${BASE_URL}/`);
    const missingAltCount = await page.$$eval(
      "img",
      (imgs) =>
        imgs.filter((img) => {
          const alt = img.getAttribute("alt");
          return !alt || alt.trim().length === 0;
        }).length,
    );
    expect(missingAltCount, `Found ${missingAltCount} <img> without alt`).toBe(
      0,
    );
  });

  test("links have accessible names (text/aria-label/title)", async ({
    page,
  }) => {
    await page.goto(`${BASE_URL}/`);
    const namelessLinks = await page.$$eval(
      "a",
      (anchors) =>
        anchors.filter((a) => {
          const text = (a.textContent || "").trim();
          const aria = (a.getAttribute("aria-label") || "").trim();
          const title = (a.getAttribute("title") || "").trim();
          return text.length === 0 && aria.length === 0 && title.length === 0;
        }).length,
    );
    expect(
      namelessLinks,
      `Found ${namelessLinks} <a> without accessible name`,
    ).toBe(0);
  });

  test("viewport meta present for responsive UX", async ({ page }) => {
    await page.goto(`${BASE_URL}/`);
    const hasViewport = await page.$('meta[name="viewport"]');
    expect(!!hasViewport, "Missing viewport meta tag").toBe(true);
  });
});
