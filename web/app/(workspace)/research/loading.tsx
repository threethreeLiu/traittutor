"use client";

import { useTranslation } from "react-i18next";

export default function ResearchLoading() {
  const { i18n } = useTranslation();
  const label = i18n.language.toLowerCase().startsWith("zh") ? "正在加载研究工作区" : "Loading research workspace";
  return (
    <main className="h-full overflow-y-auto px-4 py-8 sm:px-6 lg:px-10" aria-busy="true" aria-label={label}>
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="h-28 animate-pulse rounded-xl bg-[var(--muted)]/55" />
        <div className="grid gap-5 md:grid-cols-2">
          <div className="h-52 animate-pulse rounded-xl bg-[var(--muted)]/55" />
          <div className="h-52 animate-pulse rounded-xl bg-[var(--muted)]/55" />
        </div>
      </div>
    </main>
  );
}
