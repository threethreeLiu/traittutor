"use client";

import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";

import { PageBackLink } from "@/components/navigation/PageBackLink";

const SETTINGS_HUB_HREF = "/settings";

// Consumer settings deliberately stay focused on personal controls. Runtime
// configuration remains available to deployment operators outside this flow.

export default function SettingsMain({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname() ?? "";
  const { i18n } = useTranslation();
  const isHub = pathname === SETTINGS_HUB_HREF;
  const backLabel = i18n.language?.toLowerCase().startsWith("zh") ? "返回设置" : "Back to settings";

  if (isHub) {
    return (
      <div className="h-full overflow-y-auto bg-[var(--background)] [scrollbar-gutter:stable]">
        <div className="mx-auto w-full max-w-5xl px-4 py-6 pb-12 sm:px-8 sm:py-8">
          {children}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-[var(--background)]">
      <div className="mx-auto w-full max-w-5xl px-4 pt-5 sm:px-8 lg:px-10">
        <PageBackLink href="/settings">{backLabel}</PageBackLink>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden [scrollbar-gutter:stable]">
        <div className="mx-auto w-full max-w-5xl px-4 pb-16 sm:px-8 lg:px-10">
          <div className="mt-4">{children}</div>
        </div>
      </div>
    </div>
  );
}
