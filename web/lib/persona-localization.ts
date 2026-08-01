import type { PersonaInfo } from "@/lib/personas-api";

type PersonaPresentation = Pick<PersonaInfo, "name" | "description">;
type PersonaSource = PersonaPresentation &
  Partial<Pick<PersonaInfo, "source" | "read_only">>;

const ZH_PRESETS: Record<string, PersonaPresentation> = {
  "learning-companion": {
    name: "学习共振",
    description: "围绕你的目标、材料和学习画像一起推进对话与下一步。",
  },
  "evidence-researcher": {
    name: "证据研究员",
    description: "区分材料证据、推断与不确定性，沉淀可复用研究上下文。",
  },
  "lesson-designer": {
    name: "讲解设计师",
    description: "按材料分析、SLR 支持和薄弱概念设计课件、闪卡、测验与讲解。",
  },
};

/** Localize known built-in preset slugs. Older deployments did not mark these
 * records as admin/read-only, so source metadata must not gate their display. */
export function presentPersona(
  persona: PersonaSource,
  language?: string,
): PersonaPresentation {
  if (language?.toLowerCase().startsWith("zh")) {
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
