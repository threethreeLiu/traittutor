import fs from "node:fs";
import path from "node:path";

function listCodeFiles(dir) {
  const out = [];
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.name === "node_modules" || ent.name === ".next") continue;
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) out.push(...listCodeFiles(full));
    else if (ent.isFile() && ent.name.endsWith(".tsx")) out.push(full);
  }
  return out;
}

function toRel(p, root) {
  return path.relative(root, p).replaceAll("\\", "/");
}

function hasUiText(s) {
  // Very rough heuristic: letters / CJK / common punctuation sequences
  return /[A-Za-z\u4e00-\u9fff]/.test(s);
}

function auditFile(content) {
  const findings = [];

  // JSXText: > ... <
  // Avoid matching tags like ></ by requiring at least one non-whitespace char.
  const jsxTextRe = />\s*([^<{][^<]*?)\s*</g;
  for (const m of content.matchAll(jsxTextRe)) {
    const text = String(m[1] || "").trim();
    if (!text) continue;
    // Heuristics to avoid false positives (code / comments / long blocks)
    if (text.includes("\n") || text.includes("\r")) continue;
    if (text.length > 120) continue;
    if (text.includes("{") || text.includes("}") || text.includes("/*") || text.includes("*/"))
      continue;
    if (text.includes("=>") || text.includes("export ") || text.includes("import "))
      continue;
    // Ternary/JS fragments that often get captured by regex formatting
    if ((text.includes("?") || text.includes(":")) && (text.includes("(") || text.includes(")")))
      continue;
    if (text.startsWith(")")) continue;
    if (text.includes("&&") || text.includes("= ") || text.startsWith("=")) continue;
    if (text.includes("mark.") || text.includes("diff")) continue;
    if (text.includes(">/i") || text.includes("katex")) continue;
    // Common non-translatable tokens / file extensions / escapes
    if (text === ".md" || text === ".pdf" || text === "\\n") continue;
    if (text === "TraitTutor") continue;
    // Ignore obvious already-i18n'd inline markers
    if (text.includes('t("') || text.includes("t('")) continue;
    if (!hasUiText(text)) continue;
    // Skip single-char separators
    if (text.length <= 1) continue;
    findings.push({ kind: "jsxText", text });
  }

  // Attributes with literal string values
  const attrRe =
    /\b(title|placeholder|alt|aria-label)\s*=\s*"([^"]+)"/g;
  for (const m of content.matchAll(attrRe)) {
    const attr = m[1];
    const text = m[2];
    if (!text) continue;
    if (text.length > 160) continue;
    if (!hasUiText(text)) continue;
    findings.push({ kind: `attr:${attr}`, text });
  }

  // alert/confirm with literal strings
  const alertRe = /\b(alert|confirm)\(\s*"([^"]+)"\s*\)/g;
  for (const m of content.matchAll(alertRe)) {
    findings.push({ kind: `${m[1]}()`, text: m[2] });
  }

  return findings;
}

const webRoot = path.resolve(process.cwd());
const targets = [path.join(webRoot, "app"), path.join(webRoot, "components")].filter((p) =>
  fs.existsSync(p),
);

const strict = process.argv.includes("--strict");
const fileFilterIdx = process.argv.indexOf("--file");
const fileFilter =
  fileFilterIdx >= 0 ? String(process.argv[fileFilterIdx + 1] || "").trim() : "";
const showAll = process.argv.includes("--show-all");

const allFindings = [];
for (const dir of targets) {
  const files = listCodeFiles(dir);
  for (const f of files) {
    if (fileFilter && !toRel(f, webRoot).includes(fileFilter)) continue;
    const content = fs.readFileSync(f, "utf8");
    const findings = auditFile(content);
    if (findings.length) {
      allFindings.push({
        file: toRel(f, webRoot),
        findings,
      });
    }
  }
}

if (!allFindings.length) {
  console.log("[i18n:audit] OK (no obvious UI literals found)");
  process.exit(0);
}

console.log(`[i18n:audit] Found ${allFindings.length} files with potential UI literals`);
const fileLimit = showAll ? allFindings.length : 80;
for (const item of allFindings.slice(0, fileLimit)) {
  console.log(`\n- ${item.file}`);
  const perFileLimit = showAll ? item.findings.length : 10;
  for (const f of item.findings.slice(0, perFileLimit)) {
    console.log(`  - ${f.kind}: ${JSON.stringify(f.text)}`);
  }
  if (!showAll && item.findings.length > 10)
    console.log(`  - ... +${item.findings.length - 10} more`);
}
if (!showAll && allFindings.length > 80)
  console.log(`\n... and ${allFindings.length - 80} more files`);

if (strict) process.exit(1);
process.exit(0);
