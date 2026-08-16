/**
 * Public path helpers for deployments mounted below another website.
 *
 * Next's Link and router APIs consume app-relative paths and add `basePath`
 * themselves. Browser APIs do not. Keep both representations explicit so a
 * login redirect can never prefix `/traittutor-all-web` twice.
 */
const rawBasePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
export const appBasePath = rawBasePath === "/" ? "" : rawBasePath.replace(/\/$/, "");

function normalizedPath(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

/** Convert an app-relative path to a browser-visible URL, idempotently. */
export function appPath(path: string): string {
  const normalized = normalizedPath(path);
  if (
    !appBasePath ||
    normalized === appBasePath ||
    normalized.startsWith(`${appBasePath}/`)
  ) {
    return normalized;
  }
  return `${appBasePath}${normalized}`;
}

/** Remove the public mount prefix before passing a path to Next Router. */
export function appRelativePath(path: string): string {
  const normalized = normalizedPath(path);
  if (appBasePath && normalized === appBasePath) return "/";
  if (appBasePath && normalized.startsWith(`${appBasePath}/`)) {
    return normalized.slice(appBasePath.length) || "/";
  }
  return normalized;
}
