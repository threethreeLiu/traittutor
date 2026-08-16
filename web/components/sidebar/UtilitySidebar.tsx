'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslation } from 'react-i18next'
import { SidebarShell } from '@/components/sidebar/SidebarShell'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { LogoutButton } from '@/components/auth/LogoutButton'
import { useAppShell } from '@/context/AppShellContext'
import {
  deleteSession,
  listSessions,
  updateSessionTitle,
  type SessionSummary,
} from '@/lib/session-api'
import { dispatchLearningPacksInvalidated } from '@/lib/traittutor-api'

export default function UtilitySidebar() {
  const { t } = useTranslation()
  const router = useRouter()
  const { activeSessionId, setActiveSessionId } = useAppShell()
  const [learnSessions, setLearnSessions] = useState<SessionSummary[]>([])
  const [assistSessions, setAssistSessions] = useState<SessionSummary[]>([])
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [pendingDeleteSession, setPendingDeleteSession] = useState<SessionSummary | null>(null)
  const [deletingSession, setDeletingSession] = useState(false)
  const hasLoadedSessionsRef = useRef(false)

  const refreshSessions = useCallback(async () => {
    if (!hasLoadedSessionsRef.current) {
      setLoadingSessions(true)
    }
    try {
      const [learn, assist] = await Promise.all([
        listSessions(50, 0, { force: true, mode: 'learn' }),
        listSessions(50, 0, { force: true, mode: 'assist' }),
      ])
      setLearnSessions(learn)
      setAssistSessions(assist)

      hasLoadedSessionsRef.current = true
    } catch (error) {
      console.error('Failed to load sessions', error)
    } finally {
      setLoadingSessions(false)
    }
  }, [])

  useEffect(() => {
    void refreshSessions()
  }, [refreshSessions])

  const handleSelectSession = useCallback(
    async (sessionId: string) => {
      setActiveSessionId(sessionId)
      const session = [...learnSessions, ...assistSessions].find(
        item => item.session_id === sessionId
      )
      router.push(`${session?.mode === 'assist' ? '/assist' : '/home'}/${sessionId}`)
    },
    [assistSessions, learnSessions, router, setActiveSessionId]
  )

  const handleRenameSession = useCallback(async (sessionId: string, title: string) => {
    const updated = await updateSessionTitle(sessionId, title)
    const applyTitle = (prev: SessionSummary[]) =>
      prev.map(session =>
        session.session_id === sessionId
          ? {
              ...session,
              title: updated.title,
              updated_at: updated.updated_at,
            }
          : session
      )
    setLearnSessions(applyTitle)
    setAssistSessions(applyTitle)
  }, [])

  const handleDeleteSession = useCallback(
    (sessionId: string) => {
      const session = [...learnSessions, ...assistSessions].find(
        item => item.session_id === sessionId
      )
      if (session) setPendingDeleteSession(session)
    },
    [assistSessions, learnSessions]
  )

  const confirmDeleteSession = useCallback(async () => {
    if (!pendingDeleteSession || deletingSession) return
    setDeletingSession(true)
    const sessionId = pendingDeleteSession.session_id
    try {
      const result = await deleteSession(sessionId)
      const removeSession = (prev: SessionSummary[]) =>
        prev.filter(session => session.session_id !== sessionId)
      setLearnSessions(removeSession)
      setAssistSessions(removeSession)
      // Same cascade notice as WorkspaceSidebar: linked Packs may be gone.
      if (result.deleted_pack_ids?.length) {
        dispatchLearningPacksInvalidated(result.deleted_pack_ids)
      }
      if (activeSessionId === sessionId) {
        setActiveSessionId(null)
      }
      setPendingDeleteSession(null)
    } finally {
      setDeletingSession(false)
    }
  }, [activeSessionId, deletingSession, pendingDeleteSession, setActiveSessionId])

  return (
    <>
      <SidebarShell
        showSessions
        sessionGroups={[
          { label: t('Learning conversations') as string, sessions: learnSessions },
          { label: t('Assistant conversations') as string, sessions: assistSessions },
        ]}
        activeSessionId={activeSessionId}
        loadingSessions={loadingSessions}
        onNewChat={() => setActiveSessionId(null)}
        onSelectSession={handleSelectSession}
        onRenameSession={handleRenameSession}
        onDeleteSession={handleDeleteSession}
        footerSlot={collapsed => <LogoutButton collapsed={collapsed} />}
      />
      <ConfirmDialog
        open={pendingDeleteSession !== null}
        title={t('Delete this chat history?')}
        confirmLabel={t('Delete')}
        cancelLabel={t('Cancel')}
        busy={deletingSession}
        busyLabel={t('Deleting...')}
        tone="danger"
        onCancel={() => setPendingDeleteSession(null)}
        onConfirm={() => void confirmDeleteSession()}
      >
        <div className="space-y-2">
          <p>
            {pendingDeleteSession?.title ?? t('This chat history will be permanently removed.')}
          </p>
          {pendingDeleteSession?.mode === 'learn' ? (
            <p className="text-sm text-[var(--muted-foreground)]">
              {t(
                'The learning path created from this conversation will also be removed from My learning.'
              )}
            </p>
          ) : null}
        </div>
      </ConfirmDialog>
    </>
  )
}
