const SUPPRESSED_SESSION_PROPERTY = '__traittutorSuppressedHistoricalSessionId'

type NavigationWindow = Window & {
  __traittutorSuppressedHistoricalSessionId?: string | null
}

export function suppressHistoricalSessionUrlSync(sessionId: string | null): void {
  ;(window as NavigationWindow)[SUPPRESSED_SESSION_PROPERTY] = sessionId
}

export function getSuppressedHistoricalSessionId(): string | null {
  return (window as NavigationWindow)[SUPPRESSED_SESSION_PROPERTY] ?? null
}
