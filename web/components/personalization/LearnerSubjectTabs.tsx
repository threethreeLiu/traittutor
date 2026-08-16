"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export const LEARNER_SUBJECT_TABS = [
  "overview",
  "knowledge",
  "errors",
  "reviews",
  "misconceptions",
  "support",
  "governance",
] as const;

export type LearnerSubjectTab = (typeof LEARNER_SUBJECT_TABS)[number];

type TabDefinition = {
  id: LearnerSubjectTab;
  label: string;
};

function tabFromLocation(): LearnerSubjectTab {
  if (typeof window === "undefined") return "overview";
  const candidate = new URLSearchParams(window.location.search).get("tab");
  return LEARNER_SUBJECT_TABS.includes(candidate as LearnerSubjectTab)
    ? (candidate as LearnerSubjectTab)
    : "overview";
}

export function LearnerSubjectTabs({
  tabs,
  panels,
  ariaLabel,
}: {
  tabs: TabDefinition[];
  panels: Record<LearnerSubjectTab, ReactNode>;
  ariaLabel: string;
}) {
  const [activeTab, setActiveTab] = useState<LearnerSubjectTab>("overview");
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    const restoreTab = () => setActiveTab(tabFromLocation());
    restoreTab();
    window.addEventListener("popstate", restoreTab);
    return () => window.removeEventListener("popstate", restoreTab);
  }, []);

  const selectTab = useCallback((tab: LearnerSubjectTab, pushHistory = true) => {
    setActiveTab(tab);
    if (!pushHistory) return;
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    window.history.pushState({}, "", url);
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    const nextTab = tabs[nextIndex];
    selectTab(nextTab.id);
    tabRefs.current[nextIndex]?.focus();
  }

  return (
    <div className="space-y-5">
      <div className="overflow-x-auto border-b border-[var(--border)]">
        <div role="tablist" aria-label={ariaLabel} className="flex min-w-max gap-1">
          {tabs.map((tab, index) => {
            const selected = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                ref={(node) => { tabRefs.current[index] = node; }}
                type="button"
                role="tab"
                id={`subject-tab-${tab.id}`}
                aria-selected={selected}
                aria-controls={`subject-panel-${tab.id}`}
                tabIndex={selected ? 0 : -1}
                onClick={() => selectTab(tab.id)}
                onKeyDown={(event) => onKeyDown(event, index)}
                className={selected
                  ? "border-b-2 border-[var(--primary)] px-3 py-3 text-sm font-medium text-[var(--foreground)]"
                  : "border-b-2 border-transparent px-3 py-3 text-sm text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      <div
        role="tabpanel"
        id={`subject-panel-${activeTab}`}
        aria-labelledby={`subject-tab-${activeTab}`}
        tabIndex={0}
        className="space-y-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]"
      >
        {panels[activeTab]}
      </div>
    </div>
  );
}
