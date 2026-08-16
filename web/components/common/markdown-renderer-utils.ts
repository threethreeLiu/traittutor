import { Children, isValidElement, type ReactNode } from "react";

export function extractMarkdownText(children: ReactNode): string {
  return Children.toArray(children)
    .map((child) => {
      if (typeof child === "string" || typeof child === "number") {
        return String(child);
      }

      if (isValidElement<{ children?: ReactNode }>(child)) {
        return extractMarkdownText(child.props.children);
      }

      return "";
    })
    .join("");
}

export function markdownHeadingId(children: ReactNode): string | undefined {
  const text = extractMarkdownText(children)
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-");
  return text || undefined;
}

export function hasRenderableMarkdownChildren(children: ReactNode): boolean {
  return (
    extractMarkdownText(children).replace(/[\s\u200B-\u200D\uFEFF]/g, "")
      .length > 0
  );
}

export function hasRenderableDetailsBody(children: ReactNode): boolean {
  return Children.toArray(children).some((child) => {
    if (typeof child === "string" || typeof child === "number") {
      return String(child).replace(/[\s\u200B-\u200D\uFEFF]/g, "").length > 0;
    }

    if (!isValidElement(child)) return false;
    if (
      typeof child.type === "string" &&
      child.type.toLowerCase() === "summary"
    ) {
      return false;
    }

    return true;
  });
}

export function stripLeadingHeadingHashes(children: ReactNode): ReactNode {
  const items = Children.toArray(children);
  if (items.length > 0 && typeof items[0] === "string") {
    const cleaned = items[0].replace(/^#{1,6}\s+/, "");
    if (cleaned !== items[0]) return [cleaned, ...items.slice(1)];
  }
  return children;
}
