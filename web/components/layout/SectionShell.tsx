'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'

// Sections that own their full height + scroll. They must NOT be squeezed into
// the centered, padded document
// container the list-style sections use.
const FULL_BLEED: string[] = ['/learning']

function isFullBleed(pathname: string): boolean {
  return FULL_BLEED.some(p => pathname === p || pathname.startsWith(`${p}/`))
}

// Utility sections return to the canonical learning-pack list rather than
// recreating a parallel hub.
function BackToLearning() {
  const { t } = useTranslation()
  return (
    <Link
      href="/learning"
      className="group inline-flex items-center gap-1.5 text-[13px] text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
    >
      <ArrowLeft
        size={15}
        strokeWidth={1.8}
        className="transition-transform group-hover:-translate-x-0.5"
      />
      {t('My Learning')}
    </Link>
  )
}

export default function SectionShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname() ?? ''
  if (isFullBleed(pathname)) {
    return (
      <div className="flex h-full min-h-0 flex-col bg-[var(--background)]">
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto bg-[var(--background)] [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-5xl px-4 py-6 pb-12 sm:px-8 sm:py-8">
        <div className="mb-5">
          <BackToLearning />
        </div>
        {children}
      </div>
    </div>
  )
}
