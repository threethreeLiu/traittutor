import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relativePath: string) =>
  readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

test("language selection is global rather than duplicated in appearance settings", () => {
  const appearance = read("app/(utility)/settings/appearance/page.tsx");
  const switcher = read("components/common/LanguageSwitcher.tsx");
  const sidebar = read("components/sidebar/SidebarShell.tsx");
  const mobileNavigation = read("components/sidebar/MobileNavigation.tsx");

  assert.doesNotMatch(appearance, /updateLanguage/);
  assert.match(switcher, /\/api\/v1\/settings\/ui/);
  assert.doesNotMatch(switcher, /<span className="ml-1">/);
  assert.match(sidebar, /<LanguageSwitcher/);
  assert.match(mobileNavigation, /<LanguageSwitcher/);
});

test("glass is the no-preference theme across the client and server defaults", () => {
  const theme = read("lib/theme.ts");
  const themeScript = read("components/ThemeScript.tsx");
  const settingsRouter = read("../traittutor/api/routers/settings.py");
  const interfaceSettings = read("../traittutor/services/settings/interface_settings.py");

  assert.match(theme, /return "glass"/);
  assert.match(themeScript, /traittutor-theme', 'glass'/);
  assert.match(settingsRouter, /"theme": "glass"/);
  assert.match(interfaceSettings, /"theme": "glass"/);
});

test("favicon links respect the production base path", () => {
  const layout = read("app/layout.tsx");
  assert.match(layout, /NEXT_PUBLIC_BASE_PATH/);
  assert.match(layout, /iconUrl\("\/favicon\.svg"\)/);
  assert.match(layout, /traittutor-icon-v2/);
  assert.doesNotMatch(layout, /url:\s*"\/favicon-/);
});
