"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { TraitTutorMark } from "@/components/brand/TraitTutorMark";
import { TraitTutorIcon, type TraitTutorIconName } from "@/components/brand/TraitTutorIcon";
import { LanguageSwitcher } from "@/components/common/LanguageSwitcher";

const items = [
  { href: "/home", label: "首页", icon: "home" },
  { href: "/space", label: "学习", icon: "learning" },
  { href: "/profile/learning-model", label: "学习模型", icon: "personality" },
  { href: "/space/personas", label: "角色", icon: "profile" },
  { href: "/settings", label: "设置", icon: "settings" },
] as const satisfies ReadonlyArray<{ href: string; label: string; icon: TraitTutorIconName }>;

/** Compact, scroll-safe navigation used when the desktop sidebar is hidden. */
export function MobileNavigation() {
  const pathname = usePathname() ?? "";

  return (
    <header className="flex h-14 shrink-0 items-center gap-1 border-b border-[var(--border)] bg-[var(--secondary)] px-3 md:hidden">
      <div className="mr-1 flex shrink-0 items-center gap-1">
        <Link href="/" aria-label="TraitTutor 首页" className="p-2">
          <TraitTutorMark className="h-6 w-6" />
        </Link>
        <LanguageSwitcher />
      </div>
      <nav aria-label="移动端主导航" className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none]">
        {items.map(({ href, label, icon }) => {
          const active = pathname === href || (href !== "/home" && pathname.startsWith(`${href}/`));
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`inline-flex min-h-9 items-center gap-1.5 rounded-md px-2.5 text-xs transition-colors ${active ? "bg-[var(--accent)] font-medium text-[var(--foreground)]" : "text-[var(--muted-foreground)] hover:bg-[var(--background)] hover:text-[var(--foreground)]"}`}
            >
              <TraitTutorIcon name={icon} size={16} strokeWidth={1.65} />
              {label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
