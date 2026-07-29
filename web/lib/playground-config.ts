import { loadFromStorage, saveToStorage } from "@/lib/persistence";

const STORAGE_KEY = "playground_capability_configs";
export const FRONTEND_HIDDEN_TOOLS = new Set(["geogebra_analysis"]);

export function filterFrontendTools(tools: string[]): string[] {
  return tools.filter((tool) => !FRONTEND_HIDDEN_TOOLS.has(tool));
}

export interface CapabilityPlaygroundConfig {
  enabledTools: string[];
  knowledgeBase: string;
  config?: Record<string, unknown>;
}

export type CapabilityPlaygroundConfigMap = Record<
  string,
  CapabilityPlaygroundConfig
>;

export function loadCapabilityPlaygroundConfigs(): CapabilityPlaygroundConfigMap {
  return loadFromStorage<CapabilityPlaygroundConfigMap>(STORAGE_KEY, {});
}

export function resolveCapabilityPlaygroundConfig(
  configs: CapabilityPlaygroundConfigMap,
  capabilityName: string,
  defaultTools: string[],
): CapabilityPlaygroundConfig {
  const stored = configs[capabilityName];
  return {
    enabledTools: Array.from(
      new Set(filterFrontendTools(stored?.enabledTools ?? defaultTools)),
    ),
    knowledgeBase: stored?.knowledgeBase ?? "",
    config:
      stored?.config && typeof stored.config === "object" ? stored.config : {},
  };
}

export function saveCapabilityPlaygroundConfig(
  configs: CapabilityPlaygroundConfigMap,
  capabilityName: string,
  config: CapabilityPlaygroundConfig,
): CapabilityPlaygroundConfigMap {
  const next = {
    ...configs,
    [capabilityName]: {
      enabledTools: Array.from(
        new Set(filterFrontendTools(config.enabledTools)),
      ),
      knowledgeBase: config.knowledgeBase,
      config:
        config.config && typeof config.config === "object" ? config.config : {},
    },
  };
  saveToStorage(STORAGE_KEY, next);
  return next;
}
