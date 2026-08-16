'use client'

import { useTranslation } from 'react-i18next'
import type { SessionSummary } from '@/lib/session-api'

interface ResearchSessionListProps {
  workspaces: SessionSummary[]
  activeWorkspaceId?: string | null
  loading?: boolean
  onSelect: (sessionId: string) => void | Promise<void>
}

export default function ResearchSessionList({
  workspaces,
  activeWorkspaceId,
  loading = false,
  onSelect,
}: ResearchSessionListProps) {
  const { t } = useTranslation()

  if (loading) {
    return (
      <div className="space-y-1.5 px-2 py-1">
        {[1, 2, 3].map(i => (
          <div key={i} className="h-4 w-3/4 animate-pulse rounded bg-[var(--muted)]/40" />
        ))}
      </div>
    )
  }

  if (workspaces.length === 0) {
    return (
      <div className="px-3 py-4 text-center text-[11px] text-[var(--muted-foreground)]/70">
        {t('No research workspaces yet')}
      </div>
    )
  }

  return (
    <div className="py-0.5">
      {workspaces.map(workspace => {
        const active = activeWorkspaceId === workspace.session_id
        return (
          <div
            key={workspace.session_id}
            onClick={() => void onSelect(workspace.session_id)}
            onKeyDown={event => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                void onSelect(workspace.session_id)
              }
            }}
            role="button"
            tabIndex={0}
            className={`group flex items-center gap-2 rounded-lg px-2.5 py-1.5 transition-colors ${
              active
                ? 'bg-[var(--accent)] font-medium text-[var(--foreground)] shadow-sm'
                : 'text-[var(--muted-foreground)] hover:bg-[var(--background)]/40 hover:text-[var(--foreground)]'
            }`}
          >
            <span className="text-[13px] opacity-70">📊</span>
            <span className={`min-w-0 flex-1 truncate text-[13px] ${active ? 'font-medium' : ''}`}>
              {workspace.title}
            </span>
          </div>
        )
      })}
    </div>
  )
}
