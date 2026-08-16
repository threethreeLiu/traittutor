'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { usePathname, useRouter } from 'next/navigation'
import { useTranslation } from 'react-i18next'
import { SidebarShell } from '@/components/sidebar/SidebarShell'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { LogoutButton } from '@/components/auth/LogoutButton'
import { useUnifiedChat } from '@/context/UnifiedChatContext'
import {
  deleteSession,
  listSessions,
  updateSessionTitle,
  type SessionSummary,
} from '@/lib/session-api'
import { dispatchLearningPacksInvalidated } from '@/lib/traittutor-api'
import { listResearchWorkspaces, type ResearchWorkspaceSummary } from '@/lib/research-workspace-api'
import { subscribeToResearchWorkspaces } from '@/lib/research-workspace-sync'

export default function WorkspaceSidebar() {
  const { t } = useTranslation()
  const router = useRouter()
  const pathname = usePathname()
  const chatRoot = pathname.startsWith('/assist') ? '/assist' : '/home'
  const workspaceMode = chatRoot === '/assist' ? 'assist' : 'learn'
  const isResearchPage = pathname === '/research' || pathname.startsWith('/research/')
  const {
    newSession,
    cancelStreamingTurn,
    selectedSessionId,
    sessionStatuses,
    sidebarRefreshToken,
  } = useUnifiedChat()
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [researchWorkspaces, setResearchWorkspaces] = useState<ResearchWorkspaceSummary[]>([])
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [pendingDeleteSession, setPendingDeleteSession] = useState<SessionSummary | null>(null)
  const [deletingSession, setDeletingSession] = useState(false)
  const hasLoadedSessionsRef = useRef(false)

  const refreshSessions = useCallback(async () => {
    if (!hasLoadedSessionsRef.current) {
      setLoadingSessions(true)
    }
    try {
      if (isResearchPage) {
        setResearchWorkspaces(await listResearchWorkspaces())
        setSessions([])
      } else {
        setSessions(await listSessions(50, 0, { force: true, mode: workspaceMode }))
        setResearchWorkspaces([])
      }
      hasLoadedSessionsRef.current = true
    } catch (error) {
      console.error('Failed to load sessions', error)
    } finally {
      setLoadingSessions(false)
    }
  }, [isResearchPage, workspaceMode])

  // First mount shows the skeleton; subsequent refreshes triggered by
  // ``sidebarRefreshToken`` (STREAM_END, server-side session bind,
  // turn deletion) silently swap in the new list. Resetting the ref
  // each refresh briefly re-renders the loading skeleton, which the
  // user perceives as a flicker on every message send / Answer Now.
  useEffect(() => {
    void refreshSessions()
  }, [refreshSessions, sidebarRefreshToken])

  useEffect(() => {
    if (!isResearchPage) return
    return subscribeToResearchWorkspaces(workspaces => {
      setResearchWorkspaces(workspaces)
      hasLoadedSessionsRef.current = true
      setLoadingSessions(false)
    })
  }, [isResearchPage])

  const orderedSessions = sessions
    .map((session, index) => {
      const runtime = sessionStatuses[session.session_id]
      return {
        index,
        session: runtime
          ? {
              ...session,
              status: runtime.status,
              active_turn_id: runtime.activeTurnId || session.active_turn_id,
            }
          : session,
      }
    })
    .sort((a, b) => {
      const aPriority = a.session.status === 'running' ? 0 : 1
      const bPriority = b.session.status === 'running' ? 0 : 1
      if (aPriority !== bPriority) return aPriority - bPriority
      return a.index - b.index
    })
    .map(({ session }) => session)

  // Cancel any in-flight streaming turn before starting a fresh session, so a
  // new chat never inherits a still-running turn (mirrors handleDeleteSession).
  const handleNewChat = useCallback(() => {
    flushSync(() => {
      cancelStreamingTurn()
      newSession()
    })
  }, [cancelStreamingTurn, newSession])

  const handleSelectSession = useCallback(
    async (sessionId: string) => {
      router.push(`${chatRoot}/${sessionId}`)
    },
    [chatRoot, router]
  )

  const handleSelectResearch = useCallback(
    async (workspaceId: string) => {
      router.push(`/research/${encodeURIComponent(workspaceId)}`)
    },
    [router]
  )

  const handleRenameSession = useCallback(async (sessionId: string, title: string) => {
    const updated = await updateSessionTitle(sessionId, title)
    setSessions(prev =>
      prev.map(session =>
        session.session_id === sessionId
          ? {
              ...session,
              title: updated.title,
              updated_at: updated.updated_at,
            }
          : session
      )
    )
  }, [])

  const handleDeleteSession = useCallback(
    (sessionId: string) => {
      const session = sessions.find(item => item.session_id === sessionId)
      if (session) setPendingDeleteSession(session)
    },
    [sessions]
  )

  const confirmDeleteSession = useCallback(async () => {
    if (!pendingDeleteSession || deletingSession) return
    setDeletingSession(true)
    const sessionId = pendingDeleteSession.session_id
    try {
      const result = await deleteSession(sessionId)
      setSessions(prev => prev.filter(session => session.session_id !== sessionId))
      // The server cascade may have removed the Packs linked to this Learn
      // session. Tell pack-list surfaces to refetch so they do not keep
      // showing entries that no longer exist until a manual reload.
      if (result.deleted_pack_ids?.length) {
        dispatchLearningPacksInvalidated(result.deleted_pack_ids)
      }
      if (selectedSessionId === sessionId) {
        cancelStreamingTurn()
        newSession()
        router.push(chatRoot)
      }
      setPendingDeleteSession(null)
    } finally {
      setDeletingSession(false)
    }
  }, [
    cancelStreamingTurn,
    chatRoot,
    deletingSession,
    newSession,
    pendingDeleteSession,
    router,
    selectedSessionId,
  ])

  return (
    <>
      <SidebarShell
        showSessions
        sessions={orderedSessions}
        sessionGroups={
          isResearchPage
            ? [
                {
                  label: t('Research') as string,
                  type: 'research' as const,
                  sessions: [...researchWorkspaces]
                    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
                    .map(workspace => ({
                      id: workspace.workspace_id,
                      session_id: workspace.workspace_id,
                      title: workspace.title,
                      created_at: Date.parse(workspace.created_at),
                      updated_at: Date.parse(workspace.updated_at),
                      message_count: 0,
                      last_message: '',
                    })),
                  onSelect: handleSelectResearch,
                },
              ]
            : undefined
        }
        activeSessionId={selectedSessionId}
        loadingSessions={loadingSessions}
        onNewChat={handleNewChat}
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
          {workspaceMode === 'learn' ? (
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
