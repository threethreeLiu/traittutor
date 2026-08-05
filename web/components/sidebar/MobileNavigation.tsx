"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { TraitTutorMark } from "@/components/brand/TraitTutorMark";
import { TraitTutorIcon, type TraitTutorIconName } from "@/components/brand/TraitTutorIcon";
import { LanguageSwitcher } from "@/components/common/LanguageSwitcher";

const items = [
  { href: "/home", label: "Learn", icon: "home" },
  { href: "/assist", label: "Assistant", icon: "chat" },
  { href: "/space", label: "Learning Space", icon: "learning" },
  { href: "/profile/learning-model", label: "Learner Model", icon: "personality" },
  { href: "/space/personas", label: "Personas", icon: "profile" },
  { href: "/settings", label: "Settings", icon: "settings" },
] as const satisfies ReadonlyArray<{ href: string; label: string; icon: TraitTutorIconName }>;

/** Compact, scroll-safe navigation used when the desktop sidebar is hidden. */
export function MobileNavigation() {
  const pathname = usePathname() ?? "";
  const { t } = useTranslation();

  return (
    <header className="flex h-14 shrink-0 items-center gap-1 border-b border-[var(--border)] bg-[var(--secondary)] px-3 md:hidden">
      <div className="mr-1 flex shrink-0 items-center gap-1">
        <Link href="/" aria-label={t("Home")} className="p-2">
          <TraitTutorMark className="h-6 w-6" />
        </Link>
        <LanguageSwitcher />
      </div>
      <nav aria-label={t("Main navigation")} className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none]">
        {items.map(({ href, label, icon }) => {
          const isChatRoot = href === "/home" || href === "/assist";
          const active = pathname === href || (!isChatRoot && pathname.startsWith(`${href}/`));
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`inline-flex min-h-9 items-center gap-1.5 rounded-md px-2.5 text-xs transition-colors ${active ? "bg-[var(--accent)] font-medium text-[var(--foreground)]" : "text-[var(--muted-foreground)] hover:bg-[var(--background)] hover:text-[var(--foreground)]"}`}
            >
              <TraitTutorIcon name={icon} size={16} strokeWidth={1.65} />
              {t(label)}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
