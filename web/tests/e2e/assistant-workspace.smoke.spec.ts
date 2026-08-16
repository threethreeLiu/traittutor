import { expect, test } from '@playwright/test'

const NOW = '2026-08-10T01:00:00+00:00'
const PERSONA_SETTINGS = {
  name: 'TraitTutor',
  address_terms: ['you'],
  avatar_ref: 'default',
  voice_id: 'default',
  speech_rate: 1,
  tone: 'warm',
  directness: 'medium',
  humor_level: 'low',
  encouragement_level: 'medium',
  feedback_format: 'balanced',
  proactivity: 'off',
  reminder_consent: false,
  emoji_policy: 'minimal',
  quiet_hours: { enabled: false, start_local: '22:00', end_local: '08:00', timezone: 'UTC' },
  accessibility: {
    captions: true,
    reduced_motion: false,
    screen_reader_optimized: false,
    text_scale: 'standard',
  },
  safety_version: 'persona-safety-v1',
}

test('Assistant session does not invent a configured tutor from the product default', async ({
  page,
}) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('traittutor:onboarding-profile-dismissed', 'true')
  })
  await page.route('**/api/v1/**', async route => {
    const url = route.request().url()
    if (url.endsWith('/auth/status')) {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: 'smoke' } })
      return
    }
    if (url.endsWith('/tutor-personas')) {
      await route.fulfill({
        json: {
          persona_id: 'persona-e2e',
          version: 1,
          settings: PERSONA_SETTINGS,
          created_at: NOW,
          updated_at: NOW,
        },
      })
      return
    }
    if (url.includes('/sessions/assistant-workspace')) {
      await route.fulfill({
        json: {
          id: 'assistant-workspace',
          session_id: 'assistant-workspace',
          title: 'Draft a report',
          created_at: 1,
          updated_at: 2,
          preferences: { workspace_mode: 'assist' },
          messages: [
            {
              id: 1,
              session_id: 'assistant-workspace',
              role: 'user',
              content: 'Draft a concise research report for a client.',
              events: [],
              attachments: [],
              created_at: 1,
            },
            {
              id: 2,
              session_id: 'assistant-workspace',
              role: 'assistant',
              content: 'I will prepare an outline and evidence summary.',
              events: [],
              attachments: [],
              created_at: 2,
            },
          ],
        },
      })
      return
    }
    if (url.includes('/sessions')) {
      await route.fulfill({ json: { sessions: [] } })
      return
    }
    if (url.endsWith('/settings')) {
      await route.fulfill({ json: { catalog: {} } })
      return
    }
    if (url.includes('/learner/overview')) {
      await route.fulfill({ json: { subjects: [] } })
      return
    }
    await route.fulfill({ status: 200, json: {} })
  })

  await page.goto('/assist/assistant-workspace')
  await expect(page.getByRole('region', { name: /Task workspace|任务工作区/ })).toHaveCount(0)
  await expect(page.getByText('I will prepare an outline and evidence summary.')).toBeVisible()
  // The tutor chip always shows the current tutor's name from the persona
  // endpoint — a "Tutor not configured" placeholder never shipped. With the
  // valid persona mocked above, the configured name must render and link to
  // the mentor settings page, proving the product default isn't invented.
  const tutorChip = page.getByRole('link', { name: /当前导师：TraitTutor/ })
  await expect(tutorChip).toBeVisible()
  await expect(tutorChip).toHaveAttribute('href', '/settings/tutor')
  await expect(page.getByRole('option', { name: /Mentor|导师/ })).toHaveCount(0)
})
