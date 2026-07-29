export const SKILL_NAME_PATTERN = "^[a-z0-9][a-z0-9-]{0,63}$";
export const SKILL_NAME_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

export function slugifySkillName(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/[\s_]+/g, "-")
    .replace(/[^a-z0-9-]/g, "")
    .replace(/-{2,}/g, "-")
    .replace(/^-+/, "");
}

export function isValidSkillName(value: string): boolean {
  return SKILL_NAME_RE.test(value);
}
