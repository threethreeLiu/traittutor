import type { ComponentType, SVGProps } from "react";

/** Compatibility shim while persisted pre-cleanup chat traces are readable. */
export function agentGlyph(_kind?: string): ComponentType<SVGProps<SVGSVGElement>> | null {
  return null;
}
