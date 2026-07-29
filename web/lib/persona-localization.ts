import type { PersonaInfo } from "@/lib/personas-api";

type PersonaPresentation = Pick<PersonaInfo, "name" | "description">;
type PersonaSource = PersonaPresentation &
  Partial<Pick<PersonaInfo, "source" | "read_only">>;

const ZH_PRESETS: Record<string, PersonaPresentation> = {
  peer: {
    name: "学习伙伴",
    description: "和你一起思考、提出问题并探索不同角度的学习伙伴。",
  },
  teacher: {
    name: "苏格拉底导师",
    description: "通过提问和分步引导，帮助你建立理解的耐心导师。",
  },
  "research-assistant": {
    name: "研究助手",
    description: "专注引用、方法与批判性分析的严谨研究助手。",
  },
};

/** Localize only built-in read-only presets; user-authored names stay intact. */
export function presentPersona(
  persona: PersonaSource,
  language?: string,
): PersonaPresentation {
  if (
    language?.toLowerCase().startsWith("zh") &&
    persona.source === "admin" &&
    persona.read_only
  ) {
    return ZH_PRESETS[persona.name] ?? persona;
  }
  return persona;
}

/** Keep searching useful across both the stored slug and the visible label. */
export function personaSearchText(persona: PersonaSource, language?: string) {
  const display = presentPersona(persona, language);
  return [persona.name, persona.description, display.name, display.description]
    .join(" ")
    .toLowerCase();
}
