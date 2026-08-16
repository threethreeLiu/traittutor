'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import type { MouseEvent } from 'react'
import { flushSync } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { TraitTutorMark } from '@/components/brand/TraitTutorMark'
import { TraitTutorIcon, type TraitTutorIconName } from '@/components/brand/TraitTutorIcon'
import { LanguageSwitcher } from '@/components/common/LanguageSwitcher'
import { useUnifiedChat } from '@/context/UnifiedChatContext'

const items = [
  { href: '/home', label: 'Learn', icon: 'home' },
  { href: '/research', label: 'Research', icon: 'research' },
  { href: '/learning', label: 'My Learning', icon: 'learning' },
  { href: '/assist', label: 'Learning Tools', icon: 'chat' },
  { href: '/settings', label: 'Settings', icon: 'settings' },
] as const satisfies ReadonlyArray<{ href: string; label: string; icon: TraitTutorIconName }>

/** Compact, scroll-safe navigation used when the desktop sidebar is hidden. */
export function MobileNavigation({ onNewChat }: { onNewChat?: () => void }) {
  const pathname = usePathname() ?? ''
  const { t } = useTranslation()

  const handleChatEntryClick = (event: MouseEvent, href: string) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1) return
    if ((href === '/home' || href === '/assist') && onNewChat) onNewChat()
  }

  return (
    <header className="flex h-14 shrink-0 items-center gap-1 border-b border-[var(--border)] bg-[var(--secondary)] px-2 sm:px-3 md:hidden">
      <div className="flex shrink-0 items-center gap-0.5 sm:mr-1 sm:gap-1">
        <Link href="/" aria-label={t('Home')} className="grid h-10 w-10 place-items-center">
          <TraitTutorMark className="h-6 w-6" />
        </Link>
        <LanguageSwitcher />
      </div>
      <nav
        aria-label={t('Main navigation')}
        className="flex min-w-0 flex-1 items-center justify-between overflow-hidden min-[480px]:justify-start min-[480px]:gap-1 min-[480px]:overflow-x-auto [scrollbar-width:none]"
      >
        {items.map(({ href, label, icon }) => {
          const exact = href === '/home' || href === '/assist'
          const active = pathname === href || (!exact && pathname.startsWith(`${href}/`))
          return (
            <Link
              key={href}
              href={href}
              onClick={event => handleChatEntryClick(event, href)}
              aria-label={t(label)}
              title={t(label)}
              aria-current={active ? 'page' : undefined}
              className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-xs transition-colors min-[480px]:w-auto min-[480px]:gap-1.5 min-[480px]:px-2.5 ${active ? 'bg-[var(--accent)] font-medium text-[var(--foreground)]' : 'text-[var(--muted-foreground)] hover:bg-[var(--background)] hover:text-[var(--foreground)]'}`}
            >
              <TraitTutorIcon
                name={icon}
                size={16}
                strokeWidth={1.65}
                className={`shrink-0 ${active ? 'text-[var(--primary)]' : 'text-[var(--primary)]/75'}`}
              />
              <span className="hidden whitespace-nowrap min-[480px]:inline">{t(label)}</span>
            </Link>
          )
        })}
      </nav>
    </header>
  )
}

/** Workspace-only wrapper; utility layouts render MobileNavigation without chat state. */
export function WorkspaceMobileNavigation() {
  const { cancelStreamingTurn, newSession } = useUnifiedChat()

  const handleNewChat = () => {
    flushSync(() => {
      cancelStreamingTurn()
      newSession()
    })
  }

  return <MobileNavigation onNewChat={handleNewChat} />
}
