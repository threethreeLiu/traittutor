"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { TraitTutorMark } from "@/components/brand/TraitTutorMark";
import { TraitTutorIcon, type TraitTutorIconName } from "@/components/brand/TraitTutorIcon";
import { LanguageSwitcher } from "@/components/common/LanguageSwitcher";
import { useAppShell } from "@/context/AppShellContext";
import {
  ChevronDown,
  House,
  Lock,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import SessionList from "@/components/SessionList";
import type { SessionSummary } from "@/lib/session-api";
import { Tooltip } from "@/components/ui/Tooltip";
import { useCapabilityAccess } from "@/components/access/CapabilityAccessContext";
import type { Capability } from "@/lib/capability-routes";

interface NavEntry {
  href: string;
  label: string;
  icon: LucideIcon;
  traitTutorIcon?: TraitTutorIconName;
  tooltipKey?: string;
  exact?: boolean;
  /** Model capability this feature needs; locked when the user lacks it. */
  requires?: Capability;
}

const PRIMARY_NAV: NavEntry[] = [
  {
    href: "/home",
    label: "Home",
    icon: House,
    traitTutorIcon: "home",
    tooltipKey: "Home tooltip",
    exact: true,
    requires: "llm",
  },
  {
    href: "/space",
    label: "My Learning",
    icon: UserRound,
    traitTutorIcon: "learning",
    tooltipKey: "Space tooltip",
  },
];

const SECONDARY_NAV: NavEntry[] = [
  { href: "/space/traittutor", label: "Learning Profile", icon: UserRound, traitTutorIcon: "personality" },
  { href: "/space/personas", label: "Personas", icon: UserRound, traitTutorIcon: "profile" },
  { href: "/settings", label: "Settings", icon: Settings, traitTutorIcon: "settings" },
];
const RECENTS_COLLAPSED_KEY = "traittutor.sidebar.recentsCollapsed";

function isNavEntryActive(pathname: string | null, item: NavEntry): boolean {
  const current = pathname || "/";
  if (item.exact) {
    return current === item.href;
  }
  return current === item.href || current.startsWith(`${item.href}/`);
}

function sessionIdFromPath(pathname: string | null): string | null {
  const match = (pathname || "").match(/^\/home\/([^/?#]+)/);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

interface SidebarShellProps {
  sessions?: SessionSummary[];
  activeSessionId?: string | null;
  loadingSessions?: boolean;
  showSessions?: boolean;
  /** Clicking the Chat nav item resets to a fresh session via this handler. */
  onNewChat?: () => void;
  onSelectSession?: (sessionId: string) => void | Promise<void>;
  onRenameSession?: (sessionId: string, title: string) => void | Promise<void>;
  onDeleteSession?: (sessionId: string) => void | Promise<void>;
  /**
   * Footer content rendered below the nav. Pass a render function to receive
   * the current ``collapsed`` state so footer controls (e.g. Sign out) can
   * switch to their icon-only variant when the rail is collapsed.
   */
  footerSlot?: ReactNode | ((collapsed: boolean) => ReactNode);
}

export function SidebarShell({
  sessions = [],
  activeSessionId = null,
  loadingSessions = false,
  showSessions = false,
  onNewChat,
  onSelectSession,
  onRenameSession,
  onDeleteSession,
  footerSlot,
}: SidebarShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useTranslation();
  const { has } = useCapabilityAccess();
  const { sidebarCollapsed: collapsed, setSidebarCollapsed: setCollapsed } =
    useAppShell();

  const navLocked = (item: NavEntry) =>
    item.requires ? !has(item.requires) : false;
  const effectiveActiveSessionId =
    sessionIdFromPath(pathname) || activeSessionId || null;
  const lockedTooltip = t("Locked — contact your administrator to get access.");
  const renderedFooter =
    typeof footerSlot === "function" ? footerSlot(collapsed) : footerSlot;
  const [recentsCollapsed, setRecentsCollapsed] = useState(false);

  // Hydrate Recents collapse from localStorage after first render to stay SSR-safe.
  useEffect(() => {
    if (typeof window === "undefined") return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRecentsCollapsed(
      window.localStorage.getItem(RECENTS_COLLAPSED_KEY) === "1",
    );
  }, []);

  const toggleRecents = () => {
    setRecentsCollapsed((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(RECENTS_COLLAPSED_KEY, next ? "1" : "0");
      }
      return next;
    });
  };

  const handleHomeClick = (event: React.MouseEvent) => {
    // Always reset to a fresh session (mirrors the old "New Chat" affordance);
    // let modifier-clicks fall through to default Link behavior so middle-click
    // open-in-new-tab still works.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1)
      return;
    event.preventDefault();
    onNewChat?.();
    router.push("/home");
  };

  /* ---- Collapsed state ---- */
  if (collapsed) {
    return (
      <aside className="group/sb relative hidden h-[100dvh] w-[60px] shrink-0 flex-col items-center bg-[var(--secondary)] py-3 transition-all duration-200 md:flex">
        {/* Header: logo + collapse toggle (toggle replaces logo on hover) */}
        <div className="relative mb-2 flex h-9 w-9 items-center justify-center">
          <Link
            href="/"
            aria-label="TraitTutor"
            className="flex items-center justify-center transition-opacity duration-150 group-hover/sb:opacity-0"
          >
            <TraitTutorMark className="h-[22px] w-[22px]" />
          </Link>
          <button
            onClick={() => setCollapsed(false)}
            className="absolute inset-0 flex items-center justify-center rounded-lg text-[var(--muted-foreground)] opacity-0 transition-all duration-150 hover:bg-[var(--background)]/60 hover:text-[var(--foreground)] group-hover/sb:opacity-100"
            aria-label={t("Expand sidebar")}
          >
            <PanelLeftOpen size={16} />
          </button>
        </div>
        <LanguageSwitcher className="mb-2" />

        {/* Primary nav */}
        <nav className="mt-1 flex w-full flex-col items-center gap-1 px-1.5">
          {PRIMARY_NAV.map((item) => {
            const active = isNavEntryActive(pathname, item);
            const locked = navLocked(item);
            const description = locked
              ? lockedTooltip
              : item.tooltipKey
                ? t(item.tooltipKey)
                : undefined;
            if (locked) {
              return (
                <Tooltip
                  key={item.href}
                  label={t(item.label)}
                  description={description}
                  side="right"
                >
                  <div
                    aria-label={`${t(item.label)} — ${lockedTooltip}`}
                    aria-disabled
                    className="relative flex h-9 w-9 cursor-not-allowed items-center justify-center rounded-xl text-[var(--muted-foreground)]/40"
                  >
                    {item.traitTutorIcon ? <TraitTutorIcon name={item.traitTutorIcon} size={18} strokeWidth={1.6} /> : <item.icon size={18} strokeWidth={1.6} />}
                    <Lock
                      size={10}
                      strokeWidth={2}
                      className="absolute bottom-1 right-1 text-[var(--muted-foreground)]/70"
                    />
                  </div>
                </Tooltip>
              );
            }
            return (
              <Tooltip
                key={item.href}
                label={t(item.label)}
                description={description}
                side="right"
              >
                <Link
                  href={item.href}
                  onClick={item.href === "/home" ? handleHomeClick : undefined}
                  aria-label={t(item.label)}
                  className={`relative flex h-9 w-9 items-center justify-center rounded-xl transition-all duration-150 ${
                    active
                      ? "bg-[var(--accent)] text-[var(--foreground)] shadow-sm"
                      : "text-[var(--foreground)]/85 hover:bg-[var(--background)]/60 hover:text-[var(--foreground)]"
                  }`}
                >
                  {item.traitTutorIcon ? <TraitTutorIcon name={item.traitTutorIcon} size={18} strokeWidth={active ? 2 : 1.6} /> : <item.icon size={18} strokeWidth={active ? 2 : 1.6} />}
                </Link>
              </Tooltip>
            );
          })}
        </nav>

        <div className="flex-1" />

        {/* Secondary nav + footer */}
        <div className="flex w-full flex-col items-center gap-1 px-1.5">
          <div className="my-1 h-px w-7 bg-[var(--border)]/40" />
          {SECONDARY_NAV.map((item) => {
            const active = isNavEntryActive(pathname, item);
            return (
              <Link
                key={item.href}
                href={item.href}
                title={t(item.label) as string}
                className={`relative flex h-9 w-9 items-center justify-center rounded-xl transition-all duration-150 ${
                  active
                    ? "bg-[var(--accent)] text-[var(--foreground)] shadow-sm"
                    : "text-[var(--foreground)]/85 hover:bg-[var(--background)]/60 hover:text-[var(--foreground)]"
                }`}
              >
                {item.traitTutorIcon ? <TraitTutorIcon name={item.traitTutorIcon} size={18} strokeWidth={active ? 2 : 1.6} /> : <item.icon size={18} strokeWidth={active ? 2 : 1.6} />}
              </Link>
            );
          })}
          {renderedFooter}
        </div>
      </aside>
    );
  }

  /* ---- Expanded state ---- */
  return (
    <aside className="hidden h-[100dvh] w-[220px] shrink-0 flex-col bg-[var(--secondary)] transition-all duration-200 md:flex">
      {/* Header: logo + collapse toggle */}
      <div className="flex h-14 items-center gap-2 px-4">
        <Link href="/" aria-label="TraitTutor" className="group flex shrink-0 items-center">
          <TraitTutorMark className="h-[22px] w-[22px] transition-transform duration-200 group-hover:scale-105" />
        </Link>
        <LanguageSwitcher className="shrink-0" />
        <button
          onClick={() => setCollapsed(true)}
          className="ml-auto rounded-md p-1 text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
          aria-label={t("Collapse sidebar")}
        >
          <PanelLeftClose size={15} />
        </button>
      </div>

      {/* Primary nav */}
      <nav className="px-2 pt-1">
        <div className="space-y-px">
          {PRIMARY_NAV.map((item) => {
            const active = isNavEntryActive(pathname, item);
            const locked = navLocked(item);
            if (locked) {
              return (
                <Tooltip
                  key={item.href}
                  label={t(item.label)}
                  description={lockedTooltip}
                  side="right"
                >
                  <div
                    aria-label={`${t(item.label)} — ${lockedTooltip}`}
                    aria-disabled
                    className="flex cursor-not-allowed items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] text-[var(--muted-foreground)]/40"
                  >
                    {item.traitTutorIcon ? <TraitTutorIcon name={item.traitTutorIcon} size={16} strokeWidth={1.5} /> : <item.icon size={16} strokeWidth={1.5} />}
                    <span>{t(item.label)}</span>
                    <Lock size={13} strokeWidth={1.8} className="ml-auto" />
                  </div>
                </Tooltip>
              );
            }
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={item.href === "/home" ? handleHomeClick : undefined}
                className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] transition-colors ${
                  active
                    ? "bg-[var(--accent)] font-medium text-[var(--foreground)]"
                    : "text-[var(--foreground)]/85 hover:bg-[var(--background)]/60 hover:text-[var(--foreground)]"
                }`}
              >
                {item.traitTutorIcon ? <TraitTutorIcon name={item.traitTutorIcon} size={16} strokeWidth={active ? 1.9 : 1.5} /> : <item.icon size={16} strokeWidth={active ? 1.9 : 1.5} />}
                <span>{t(item.label)}</span>
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Chat history — its own region below the nav, takes remaining height */}
      {showSessions && onSelectSession && onRenameSession && onDeleteSession ? (
        <section
          className={`mt-4 flex min-h-0 flex-col ${
            recentsCollapsed ? "" : "flex-1"
          }`}
        >
          <button
            type="button"
            onClick={toggleRecents}
            className="group/recents mx-2 flex items-center justify-between rounded-md px-2 py-1 text-left text-[11.5px] font-normal text-[var(--muted-foreground)]/60 transition-colors hover:bg-[var(--background)]/40 hover:text-[var(--muted-foreground)]"
            aria-expanded={!recentsCollapsed}
            aria-label={
              recentsCollapsed
                ? (t("Show recents") as string)
                : (t("Hide recents") as string)
            }
          >
            <span>{t("Recents")}</span>
            <ChevronDown
              size={13}
              strokeWidth={1.7}
              className={`transition-all duration-200 ${
                recentsCollapsed
                  ? "-rotate-90 opacity-60"
                  : "rotate-0 opacity-0 group-hover/recents:opacity-60"
              }`}
            />
          </button>
          {!recentsCollapsed && (
            <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 pt-0.5">
              <SessionList
                sessions={sessions}
                activeSessionId={effectiveActiveSessionId}
                loading={loadingSessions}
                onSelect={onSelectSession}
                onRename={onRenameSession}
                onDelete={onDeleteSession}
                compact
              />
            </div>
          )}
        </section>
      ) : null}

      {/* When recents is collapsed or unavailable, fill the gap above the footer. */}
      {(!showSessions ||
        !onSelectSession ||
        !onRenameSession ||
        !onDeleteSession ||
        recentsCollapsed) && <div className="flex-1" />}

      {/* Secondary nav + footer */}
      <div className="border-t border-[var(--border)]/40 px-2 py-2">
        {SECONDARY_NAV.map((item) => {
          const active = isNavEntryActive(pathname, item);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] transition-colors ${
                active
                  ? "bg-[var(--accent)] font-medium text-[var(--foreground)]"
                  : "text-[var(--foreground)]/85 hover:bg-[var(--background)]/60 hover:text-[var(--foreground)]"
              }`}
            >
              {item.traitTutorIcon ? <TraitTutorIcon name={item.traitTutorIcon} size={16} strokeWidth={active ? 1.9 : 1.5} /> : <item.icon size={16} strokeWidth={active ? 1.9 : 1.5} />}
              <span>{t(item.label)}</span>
            </Link>
          );
        })}
        {renderedFooter}
      </div>
    </aside>
  );
}
