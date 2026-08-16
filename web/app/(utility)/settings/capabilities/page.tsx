"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";
import {
  SettingRow,
  SettingSection,
  SettingsPageHeader,
} from "@/components/settings/shared";
import { useSettings } from "@/components/settings/SettingsContext";

interface ChatBlock {
  temperature: number;
  max_rounds: number;
  stage_budgets: {
    exploring: number;
    responding: number;
  };
}

interface ResearchBlock {
  temperature: number;
  max_tokens: number;
  researching: {
    note_agent_mode?: string;
    tool_timeout: number;
    tool_max_retries: number;
    paper_search_years_limit: number;
  };
}

interface CapabilitiesSettingsDTO {
  chat: ChatBlock;
  research: ResearchBlock;
}

function isValidCapabilitiesDTO(
  value: unknown,
): value is CapabilitiesSettingsDTO {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  const chat = v.chat as Record<string, unknown> | undefined;
  const research = v.research as Record<string, unknown> | undefined;
  return (
    !!chat &&
    typeof chat.temperature === "number" &&
    typeof chat.max_rounds === "number" &&
    !!chat.stage_budgets &&
    !!research &&
    typeof research.temperature === "number" &&
    typeof research.max_tokens === "number" &&
    !!research.researching
  );
}

export default function CapabilitiesSettingsPage() {
  const { t } = useTranslation();
  const { registerExtension } = useSettings();
  const [settings, setSettings] = useState<CapabilitiesSettingsDTO | null>(
    null,
  );
  const [serverSnapshot, setServerSnapshot] =
    useState<CapabilitiesSettingsDTO | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(apiUrl("/api/v1/capabilities/settings"));
      if (!res.ok) {
        setLoadError(
          t("Failed to load capability settings (HTTP {{status}}).", {
            status: res.status,
          }),
        );
        return;
      }
      const data: unknown = await res.json();
      if (!isValidCapabilitiesDTO(data)) {
        setLoadError(t("The backend returned an unexpected capability settings payload."));
        return;
      }
      setSettings(data);
      setServerSnapshot(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(
        err instanceof Error
          ? err.message
          : t("Failed to load capability settings."),
      );
    }
  }, [t]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const dirty =
    !!settings &&
    !!serverSnapshot &&
    JSON.stringify(settings) !== JSON.stringify(serverSnapshot);

  const settingsRef = useRef(settings);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  const save = useCallback(async () => {
    const current = settingsRef.current;
    if (!current) return;
    const res = await apiFetch(apiUrl("/api/v1/capabilities/settings"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(current),
    });
    if (!res.ok) {
      throw new Error(
        t("Failed to save capability settings (HTTP {{status}})", {
          status: res.status,
        }),
      );
    }
    const data: unknown = await res.json();
    if (!isValidCapabilitiesDTO(data)) {
      throw new Error(t("Backend returned an unexpected payload after save."));
    }
    setSettings(data);
    setServerSnapshot(data);
  }, [t]);

  useEffect(() => {
    registerExtension("capabilities", { dirty, save });
    return () => registerExtension("capabilities", null);
  }, [dirty, save, registerExtension]);

  function patchChat<K extends keyof ChatBlock>(key: K, value: ChatBlock[K]) {
    if (!settings) return;
    setSettings({ ...settings, chat: { ...settings.chat, [key]: value } });
  }

  function patchStageBudget(
    stage: keyof ChatBlock["stage_budgets"],
    value: number,
  ) {
    if (!settings) return;
    setSettings({
      ...settings,
      chat: {
        ...settings.chat,
        stage_budgets: { ...settings.chat.stage_budgets, [stage]: value },
      },
    });
  }

  function patchResearch(value: Partial<ResearchBlock>) {
    if (!settings) return;
    setSettings({
      ...settings,
      research: { ...settings.research, ...value },
    });
  }

  function patchResearching(value: Partial<ResearchBlock["researching"]>) {
    if (!settings) return;
    setSettings({
      ...settings,
      research: {
        ...settings.research,
        researching: { ...settings.research.researching, ...value },
      },
    });
  }

  if (loadError) {
    return (
      <div className="grid h-[60vh] place-items-center px-6">
        <div className="max-w-xl rounded-lg border border-[var(--border)] bg-[var(--background)] p-4 text-[13px] text-[var(--muted-foreground)]">
          <div className="mb-2 font-medium text-[var(--foreground)]">
            {t("Couldn't load capability settings")}
          </div>
          <div>{loadError}</div>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-3 rounded-md border border-[var(--border)] bg-[var(--card)] px-3 py-1 text-[12px] hover:bg-[var(--muted)]"
          >
            {t("Retry")}
          </button>
        </div>
      </div>
    );
  }

  if (!settings) {
    return (
      <div className="grid h-[60vh] place-items-center text-[13px] text-[var(--muted-foreground)]">
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    );
  }

  return (
    <div data-tour="tour-capabilities">
      <SettingsPageHeader
        title={t("Capabilities")}
        description={t("Runtime settings for chat and Deep Research.")}
      />

      <SettingSection
        title={t("Chat")}
        description={t(
          "General conversation. Courseware, flashcards, and quizzes use TraitTutor's dedicated generation workflow.",
        )}
      >
        <NumberRow
          label={t("Temperature")}
          value={settings.chat.temperature}
          onChange={(n) => patchChat("temperature", n)}
          min={0}
          max={2}
          step={0.05}
          isFloat
        />
        <NumberRow
          label={t("Max rounds")}
          value={settings.chat.max_rounds}
          onChange={(n) => patchChat("max_rounds", n)}
          min={1}
          max={50}
        />
        <NumberRow
          label={t("Exploring max tokens")}
          value={settings.chat.stage_budgets.exploring}
          onChange={(n) => patchStageBudget("exploring", n)}
          min={256}
          max={200000}
          step={100}
        />
        <NumberRow
          label={t("Responding max tokens")}
          value={settings.chat.stage_budgets.responding}
          onChange={(n) => patchStageBudget("responding", n)}
          min={256}
          max={200000}
          step={100}
        />
      </SettingSection>

      <SettingSection
        title={t("Deep Research")}
        description={t("Source-grounded research from the home composer.")}
      >
        <NumberRow
          label={t("Temperature")}
          value={settings.research.temperature}
          onChange={(n) => patchResearch({ temperature: n })}
          min={0}
          max={2}
          step={0.05}
          isFloat
        />
        <NumberRow
          label={t("Max tokens")}
          value={settings.research.max_tokens}
          onChange={(n) => patchResearch({ max_tokens: n })}
          min={256}
          max={200000}
          step={100}
        />
        <NumberRow
          label={t("Tool timeout seconds")}
          value={settings.research.researching.tool_timeout}
          onChange={(n) => patchResearching({ tool_timeout: n })}
          min={1}
          max={600}
        />
        <NumberRow
          label={t("Tool max retries")}
          value={settings.research.researching.tool_max_retries}
          onChange={(n) => patchResearching({ tool_max_retries: n })}
          min={0}
          max={10}
        />
        <NumberRow
          label={t("Paper search years limit")}
          value={settings.research.researching.paper_search_years_limit}
          onChange={(n) => patchResearching({ paper_search_years_limit: n })}
          min={1}
          max={50}
        />
      </SettingSection>
    </div>
  );
}

interface NumberRowProps {
  label: string;
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
  isFloat?: boolean;
}

function NumberRow({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  isFloat = false,
}: NumberRowProps) {
  return (
    <SettingRow
      title={label}
      control={
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return;
            const n = isFloat ? parseFloat(raw) : parseInt(raw, 10);
            if (!Number.isNaN(n)) onChange(n);
          }}
          className="w-28 rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-right text-[12px] outline-none focus:border-[var(--primary)]"
        />
      }
    />
  );
}
