"use client";

import { Languages } from "lucide-react";

import { useAppShell } from "@/context/AppShellContext";
import type { AppLanguage } from "@/context/app-shell-storage";
import { apiFetch, apiUrl } from "@/lib/api";

type LanguageSwitcherProps = { className?: string };

/** A global UI-language control that persists without taking the user to Settings. */
export function LanguageSwitcher({ className = "" }: LanguageSwitcherProps) {
  const { language, setLanguage } = useAppShell();
  const chooseLanguage = (nextLanguage: AppLanguage) => {
    if (nextLanguage === language) return;
    setLanguage(nextLanguage);
    void apiFetch(apiUrl("/api/v1/settings/ui"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: nextLanguage }),
    }).catch(() => {
      // Keep the local choice when the service is temporarily unavailable.
    });
  };

  const nextLanguage: AppLanguage = language === "zh" ? "en" : "zh";
  return <button type="button" onClick={() => chooseLanguage(nextLanguage)} aria-label={language === "zh" ? "Switch interface language to English" : "切换界面语言为中文"} title={language === "zh" ? "English" : "中文"} className={`inline-flex h-9 w-9 items-center justify-center rounded-lg text-[var(--foreground)] transition-colors hover:bg-[var(--background)]/65 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] ${className}`}><Languages className="h-4 w-4" aria-hidden="true" /></button>;
}
