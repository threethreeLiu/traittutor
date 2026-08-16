'use client'

import Link from 'next/link'
import { Loader2, Settings2, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getTutorPersona, type TutorPersonaProfile } from '@/lib/tutor-persona-api'

export default function TutorProfileButton() {
  const [profile, setProfile] = useState<TutorPersonaProfile | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    void getTutorPersona(controller.signal)
      .then(next => {
        if (!controller.signal.aborted) setProfile(next)
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [])
  // settings is only required by the TS type; the persona endpoint can return
  // an empty/partial profile (e.g. feature not yet configured), so guard the
  // whole chain — a cosmetic header button must never take down the page.
  const label = profile?.settings?.name || '导师'
  return (
    <Link
      href="/settings/tutor"
      aria-label={`当前导师：${label}。管理导师设置`}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center gap-1.5 rounded-lg px-0 text-[14px] font-medium text-[var(--muted-foreground)] transition hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)] sm:w-auto sm:max-w-[150px] sm:px-2"
    >
      {profile ? (
        <UserRound size={16} strokeWidth={1.7} />
      ) : (
        <Loader2 size={16} strokeWidth={1.7} className="animate-spin" />
      )}
      <span className="hidden truncate sm:block">{label}</span>
      <Settings2 className="hidden sm:block" size={13} strokeWidth={1.7} />
    </Link>
  )
}
