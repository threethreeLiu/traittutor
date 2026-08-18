import { expect, test } from "@playwright/test";

test("registration rejects an invalid username before calling the API", async ({ page }) => {
  let registrationRequests = 0;
  await page.route("**/api/v1/auth/register", async (route) => {
    registrationRequests += 1;
    await route.fulfill({ status: 500, json: { detail: "should not be called" } });
  });

  await page.goto("/register");
  await page.getByLabel(/邮箱或用户名|Email or username/).fill("ab");
  await page.getByLabel(/^密码$|^Password$/).fill("strong-pass-123");
  await page.getByLabel(/确认密码|Confirm password/).fill("strong-pass-123");
  await page.getByRole("button", { name: /创建账户|Create account/ }).click();

  await expect(page.getByText(/用户名须为 3|Username must use 3-64/)).toBeVisible();
  expect(registrationRequests).toBe(0);
});

test("registration enters the app only after the authenticated status is confirmed", async ({ page }) => {
  let statusRequests = 0;
  await page.route("**/api/v1/auth/status", async (route) => {
    statusRequests += 1;
    await route.fulfill({
      json: statusRequests === 1
        ? { enabled: true, authenticated: false, user_id: null, username: null }
        : { enabled: true, authenticated: true, user_id: "u_e2e", username: "e2e-user" },
    });
  });
  await page.route("**/api/v1/auth/register", (route) => route.fulfill({
    status: 201,
    json: { ok: true, user_id: "u_e2e", username: "e2e-user" },
  }));
  await page.route("**/api/v1/auth/login", (route) => route.fulfill({
    status: 200,
    json: { ok: true, user_id: "u_e2e", username: "e2e-user" },
  }));

  await page.goto("/register?next=/home");
  await page.getByLabel(/邮箱或用户名|Email or username/).fill("e2e-user");
  await page.getByLabel(/^密码$|^Password$/).fill("strong-pass-123");
  await page.getByLabel(/确认密码|Confirm password/).fill("strong-pass-123");
  await page.getByRole("button", { name: /创建账户|Create account/ }).click();

  await expect(page).toHaveURL(/\/home$/);
  expect(statusRequests).toBeGreaterThanOrEqual(2);
});
