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
  const isPersonality = pathname === "/settings/personality";
  const isAccount = pathname === "/settings/account";
  const isViewportPage = isPersonality || isAccount;
  const backLabel = i18n.language?.toLowerCase().startsWith("zh") ? "返回设置" : "Back to settings";

  if (isHub) {
    return (
      <div className="traittutor-scroll-area h-full overflow-y-auto bg-[var(--background)]">
        <div className="w-full px-4 py-6 pb-12 sm:px-8 sm:py-8 lg:px-10 xl:px-12 2xl:px-16">
          {children}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-[var(--background)]">
      <div
        className="w-full px-4 pt-5 sm:px-8 lg:px-10 xl:px-12 2xl:px-16"
      >
        <PageBackLink href="/settings">{backLabel}</PageBackLink>
      </div>
      <div
        className={`min-h-0 flex-1 overflow-x-hidden ${
          isViewportPage
            ? "traittutor-scroll-area overflow-y-auto lg:overflow-hidden"
            : "traittutor-scroll-area overflow-y-auto"
        }`}
      >
        <div
          className={`h-full w-full px-4 sm:px-8 lg:px-10 xl:px-12 2xl:px-16 ${
            isViewportPage ? "pb-4" : "pb-16"
          }`}
        >
          <div className={isViewportPage ? "mt-4 h-[calc(100%_-_1rem)]" : "mt-4"}>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}
