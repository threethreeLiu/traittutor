import { defaultSchema, type Options } from "rehype-sanitize";

import { MARKDOWN_ALLOWED_HTML_TAG_NAMES } from "@/lib/markdown-display";

/**
 * Hast-level sanitize schema for the raw-HTML markdown path.
 *
 * The renderer pipes untrusted content (LLM output, extracted learning
 * material) through `rehype-raw`; without a structural sanitizer every
 * attribute on an allowed tag — `<img onerror>`, `<a javascript:>` — would
 * reach the DOM. The text-level escape in `markdown-display.ts` strips the
 * common cases, but a regex is not a parser: this schema is the spec-level
 * boundary, and both layers share one tag allowlist.
 *
 * Rules inherited from `defaultSchema` (GitHub-derived): no script/style/
 * iframe/form control semantics, `src`/`href` restricted to http(s) and a
 * small mail/irc set (no `data:`), id/name clobber protection. On top we
 * admit the app's passive-media and MathML tags with passive-only
 * attributes.
 */

// Media controls are deliberately passive: no autoplay, no form behavior.
// `src` must be listed per element (it is not in the schema's wildcard set)
// and stays constrained to http(s) by `defaultSchema.protocols`.
const MEDIA_ATTRIBUTES: Record<string, string[]> = {
  video: ["src", "controls", "poster", "preload", "muted", "loop", "playsInline"],
  audio: ["src", "controls", "preload", "muted", "loop"],
  source: ["src", "type"],
  track: ["src", "kind", "srclang", "label"],
  img: ["loading", "decoding"],
};

export function buildMarkdownSanitizeSchema(): Options {
  type AttributeMap = NonNullable<Options["attributes"]>;
  const defaultAttributes: AttributeMap = defaultSchema.attributes ?? {};
  const attributes: AttributeMap = { ...defaultAttributes };
  for (const [tag, names] of Object.entries(MEDIA_ATTRIBUTES)) {
    attributes[tag] = Array.from(
      new Set([...(defaultAttributes[tag] ?? []), ...names]),
    ) as AttributeMap[string];
  }
  return {
    ...defaultSchema,
    tagNames: Array.from(
      new Set([...(defaultSchema.tagNames ?? []), ...MARKDOWN_ALLOWED_HTML_TAG_NAMES]),
    ),
    attributes,
  };
}
