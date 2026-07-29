"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Bot, Check, ChevronDown, Settings2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import ProviderIcon from "@/components/common/ProviderIcon";
import type { LLMSelection } from "@/lib/unified-ws";
import {
  llmSelectionKey,
  sameLLMSelection,
  type LLMOption,
} from "@/lib/llm-options";

function formatContextWindow(value?: number) {
  if (!value) return "";
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)}M ctx`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}k ctx`;
  return `${value} ctx`;
}

function providerLabel(option: LLMOption) {
  return (
    option.provider_label || option.provider || option.profile_name || "LLM"
  );
}

function ModelOptionRow({
  option,
  selected,
  onSelect,
}: {
  option: LLMOption;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  // Official model ID as the primary label (what gets sent to the API),
  // per design. The user-given nickname and profile live in the tooltip.
  const modelLabel = option.model || option.model_name;
  const contextWindow = formatContextWindow(option.context_window);
  // Long model ids ("google/gemini-3-flash-preview") get ellipsized by the
  // inline layout; hovering the row reveals the full id as an overlay. The
  // scrollWidth check at mouseenter time keeps the overlay away from rows
  // that aren't actually truncated.
  const nameRef = useRef<HTMLSpanElement>(null);
  const [revealFull, setRevealFull] = useState(false);
  return (
    <button
      type="button"
      title={`${option.model_name} | ${option.profile_name}`}
      onClick={onSelect}
      onMouseEnter={() => {
        const el = nameRef.current;
        setRevealFull(!!el && el.scrollWidth > el.clientWidth + 1);
      }}
      onMouseLeave={() => setRevealFull(false)}
      className={`relative flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
        selected ? "bg-[var(--primary)]/[0.06]" : "hover:bg-[var(--muted)]/45"
      }`}
    >
      <ProviderIcon
        provider={option.provider}
        size={14}
        className={
          selected ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]"
        }
      />
      <span
        ref={nameRef}
        className="min-w-0 truncate text-[12.5px] font-medium text-[var(--foreground)]"
      >
        {modelLabel}
      </span>
      {option.is_active_default && (
        <span className="shrink-0 rounded-full bg-[var(--muted)] px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
          {t("Default")}
        </span>
      )}
      <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--muted-foreground)]">
        {providerLabel(option)}
      </span>
      {contextWindow ? (
        <span className="shrink-0 text-[11px] text-[var(--muted-foreground)]">
          {contextWindow}
        </span>
      ) : null}
      {selected && (
        <Check
          size={14}
          strokeWidth={2}
          className="shrink-0 text-[var(--primary)]"
        />
      )}
      {revealFull && (
        <span className="pointer-events-none absolute inset-x-1.5 top-1/2 z-10 -translate-y-1/2 break-all rounded-lg border border-[var(--border)] bg-[var(--popover)] px-2 py-1 text-[12px] font-medium text-[var(--foreground)] shadow-md">
          {modelLabel}
        </span>
      )}
    </button>
  );
}

function ModelGroup({
  label,
  options,
  selectedKey,
  onSelect,
}: {
  label: string;
  options: LLMOption[];
  selectedKey: string;
  onSelect: (selection: LLMSelection) => void;
}) {
  return (
    <section className="py-1 first:pt-0 last:pb-0">
      <p className="px-3 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
        {label}
      </p>
      {options.map((option) => {
        const selection = {
          profile_id: option.profile_id,
          model_id: option.model_id,
        };
        const optionKey = llmSelectionKey(selection);
        return (
          <ModelOptionRow
            key={optionKey}
            option={option}
            selected={optionKey === selectedKey}
            onSelect={() => onSelect(selection)}
          />
        );
      })}
    </section>
  );
}

export default function ModelSelector({
  options,
  activeDefault,
  value,
  loading,
  error,
  allowSystemDefault = false,
  systemDefaultLabel,
  systemDefaultDetail,
  helperText,
  placement = "top",
  onChange,
}: {
  options: LLMOption[];
  activeDefault: LLMSelection | null;
  value: LLMSelection | null;
  loading: boolean;
  error: boolean;
  allowSystemDefault?: boolean;
  systemDefaultLabel?: string;
  systemDefaultDetail?: string;
  helperText?: string;
  placement?: "top" | "bottom";
  onChange: (selection: LLMSelection | null) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const selectedSelection = allowSystemDefault
    ? value
    : (value ?? activeDefault);
  const selectedKey = llmSelectionKey(selectedSelection);
  const selectedOption = useMemo(
    () =>
      options.find((option) => sameLLMSelection(option, selectedSelection)) ??
      null,
    [options, selectedSelection],
  );
  const groups = useMemo(() => {
    const grouped = new Map<string, LLMOption[]>();
    for (const option of options) {
      const label = `${providerLabel(option)} · ${option.profile_name}`;
      grouped.set(label, [...(grouped.get(label) ?? []), option]);
    }
    return [...grouped.entries()];
  }, [options]);

  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current && !rootRef.current.contains(target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const defaultLabel = systemDefaultLabel || t("System default");
  const defaultDetail =
    systemDefaultDetail || t("Use the active default model from Settings");
  const needsConfiguration =
    !loading && !error && options.length === 0 && !allowSystemDefault;
  const disabled = loading || error;
  const label = loading
    ? t("Loading models")
    : error
      ? t("Models unavailable")
      : allowSystemDefault && !selectedSelection
        ? defaultLabel
        : // Official model ID, consistent with the dropdown rows.
          selectedOption?.model ||
          selectedOption?.model_name ||
          t("Select model");
  const menuPlacementClass =
    placement === "bottom" ? "top-full mt-1.5" : "bottom-full mb-1.5";

  if (needsConfiguration) {
    return (
      <Link
        href="/settings/llm"
        title={t("Add a model in Settings to use it in chat")}
        className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2 text-[13px] font-medium text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
      >
        <Settings2 size={16} strokeWidth={1.7} />
        <span>{t("Configure model")}</span>
      </Link>
    );
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        aria-label={t("Select model")}
        aria-expanded={open}
        className={`inline-flex h-8 max-w-[230px] shrink-0 items-center gap-1.5 rounded-lg px-2 text-[13px] font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.97] ${
          disabled
            ? "cursor-not-allowed text-[var(--border)]"
            : open
              ? "bg-[var(--muted)] text-[var(--foreground)]"
              : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
        }`}
      >
        {error ? (
          <AlertCircle size={16} strokeWidth={1.7} className="shrink-0" />
        ) : selectedOption ? (
          <ProviderIcon provider={selectedOption.provider} size={16} />
        ) : (
          <Bot size={16} strokeWidth={1.7} className="shrink-0" />
        )}
        <span className="min-w-0 truncate whitespace-nowrap">
          {label}
        </span>
        <span className="flex shrink-0 items-center">
          <ChevronDown
            size={13}
            className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </span>
      </button>

      {open && !disabled && (
        <div
          className={`absolute right-0 z-50 ${menuPlacementClass} w-[min(280px,calc(100vw-32px))] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--popover)] shadow-lg backdrop-blur-md`}
        >
          {helperText ? (
            <div className="border-b border-[var(--border)]/50 px-3 py-1.5 text-[11px] text-[var(--muted-foreground)]">
              {helperText}
            </div>
          ) : null}
          <div className="max-h-[280px] overflow-y-auto py-1">
            {allowSystemDefault && (
              <button
                type="button"
                title={defaultDetail}
                onClick={() => {
                  onChange(null);
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
                  selectedKey === ""
                    ? "bg-[var(--primary)]/[0.06]"
                    : "hover:bg-[var(--muted)]/45"
                }`}
              >
                <Bot
                  size={14}
                  strokeWidth={1.7}
                  className={`shrink-0 ${
                    selectedKey === ""
                      ? "text-[var(--primary)]"
                      : "text-[var(--muted-foreground)]"
                  }`}
                />
                <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-[var(--foreground)]">
                  {defaultLabel}
                </span>
                {selectedKey === "" && (
                  <Check
                    size={14}
                    strokeWidth={2}
                    className="shrink-0 text-[var(--primary)]"
                  />
                )}
              </button>
            )}
            {groups.map(([groupLabel, groupOptions]) => (
              <ModelGroup
                key={groupLabel}
                label={groupLabel}
                options={groupOptions}
                selectedKey={selectedKey}
                onSelect={(selection) => {
                  onChange(selection);
                  setOpen(false);
                }}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
