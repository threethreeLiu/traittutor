import { expect, test, type Page } from '@playwright/test'

const settings = {
  name: 'TraitTutor',
  address_terms: ['you'],
  avatar_ref: 'guide',
  voice_id: 'calm',
  speech_rate: 1,
  tone: 'warm',
  directness: 'medium',
  humor_level: 'low',
  encouragement_level: 'medium',
  feedback_format: 'balanced',
  proactivity: 'reminders_only',
  reminder_consent: false,
  emoji_policy: 'minimal',
  quiet_hours: {
    enabled: false,
    start_local: '22:00',
    end_local: '07:00',
    timezone: 'Asia/Shanghai',
  },
  accessibility: {
    captions: true,
    reduced_motion: false,
    screen_reader_optimized: false,
    text_scale: 'standard',
  },
  safety_version: 'persona-safety-v1',
}

function profile(version = 1, nextSettings = settings) {
  return {
    persona_id: 'persona-e2e',
    version,
    settings: nextSettings,
    created_at: '2026-08-10T01:00:00+00:00',
    updated_at: '2026-08-10T01:00:00+00:00',
  }
}

function preview(nextSettings: typeof settings, version = 1) {
  return {
    contract_version: 'tutor-persona-contract.v1',
    persona_id: 'persona-e2e',
    profile_version: version,
    identity: {
      display_name: nextSettings.name,
      address_terms: nextSettings.address_terms,
      avatar_ref: nextSettings.avatar_ref,
    },
    expression: {
      tone: nextSettings.tone,
      directness: nextSettings.directness,
      humor_level: nextSettings.humor_level,
      encouragement_level: nextSettings.encouragement_level,
      feedback_format: nextSettings.feedback_format,
      proactivity: nextSettings.proactivity,
      emoji_policy: nextSettings.emoji_policy,
    },
    modality: {
      voice_id: nextSettings.voice_id,
      speech_rate: nextSettings.speech_rate,
      accessibility: nextSettings.accessibility,
    },
    quiet_hours: nextSettings.quiet_hours,
    safety_version: 'persona-safety-v1',
  }
}

async function installTutorRoutes(
  page: Page,
  options: { loadGate?: Promise<void>; failLoad?: boolean; save409Once?: boolean } = {}
) {
  let current = profile()
  let saveAttempt = 0
  let reminders: Array<Record<string, unknown>> = [
    {
      reminder_id: 'rem-e2e',
      kind: 'review_due',
      reference_id: 'review-e2e',
      learning_path_id: 'path-e2e',
      subject_id: 'computer-science',
      kc_id: 'time-complexity',
      due_at: '2026-08-10T02:00:00+00:00',
      status: 'delivered',
      queued_at: '2026-08-10T01:00:00+00:00',
      delivered_at: '2026-08-10T01:01:00+00:00',
      read_at: null,
      cancelled_at: null,
    },
  ]
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/auth/status') {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: 'e2e' } })
      return
    }
    if (path === '/api/v1/tutor-personas' && request.method() === 'GET') {
      await options.loadGate
      if (options.failLoad) {
        await route.fulfill({ status: 503, json: { detail: 'Tutor profile store unavailable' } })
      } else {
        await route.fulfill({ json: current })
      }
      return
    }
    if (path === '/api/v1/tutor-personas/preview' && request.method() === 'POST') {
      const body = request.postDataJSON() as { settings: typeof settings }
      expect(body).not.toHaveProperty('owner_id')
      await route.fulfill({ json: preview(body.settings, current.version) })
      return
    }
    if (path === '/api/v1/tutor-personas/reminders' && request.method() === 'GET') {
      await route.fulfill({ json: reminders })
      return
    }
    if (path === '/api/v1/tutor-personas/reminders/rem-e2e/read' && request.method() === 'POST') {
      const next = { ...reminders[0], status: 'read', read_at: '2026-08-10T02:01:00+00:00' }
      reminders = []
      await route.fulfill({ json: next })
      return
    }
    if (path === '/api/v1/tutor-personas/reminders/rem-e2e' && request.method() === 'DELETE') {
      const next = {
        ...reminders[0],
        status: 'cancelled',
        cancelled_at: '2026-08-10T02:01:00+00:00',
      }
      reminders = []
      await route.fulfill({ json: next })
      return
    }
    if (path === '/api/v1/tutor-personas' && request.method() === 'PUT') {
      saveAttempt += 1
      const body = request.postDataJSON() as {
        settings: typeof settings
        expected_version: number
        idempotency_key: string
      }
      expect(body).not.toHaveProperty('owner_id')
      expect(body.expected_version).toBe(current.version)
      expect(body.idempotency_key).toMatch(/^persona-save-/)
      if (options.save409Once && saveAttempt === 1) {
        await route.fulfill({
          status: 409,
          json: { detail: { code: 'version_conflict', expected_version: 1, actual_version: 2 } },
        })
        return
      }
      current = profile(current.version + 1, body.settings)
      await route.fulfill({ json: current })
      return
    }
    await route.fulfill({ status: 200, json: {} })
  })
}

test('shows loading and a recoverable tutor-profile read failure', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => {
    release = resolve
  })
  await installTutorRoutes(page, { loadGate: gate, failLoad: true })

  await page.goto('/settings/tutor')
  await expect(page.getByLabel(/Loading Tutor Persona|正在加载 Tutor Persona/)).toBeVisible()
  release()
  const loadAlert = page
    .locator('[role="alert"]')
    .filter({ hasText: 'Tutor profile store unavailable' })
  await expect(loadAlert).toBeVisible()
  await expect(page.getByRole('button', { name: /Try again|重试/ })).toBeVisible()
})

test('edits closed settings, previews them, and saves without hidden prompts', async ({ page }) => {
  await installTutorRoutes(page)
  await page.goto('/settings/tutor')
  await expect(
    page.getByRole('heading', { name: /Tutor settings|导师设置/ })
  ).toBeVisible()

  await page.getByLabel(/Display name|显示名称/).fill('Socratic Guide')
  await page.getByLabel(/Tone|语气/).selectOption('calm')
  await expect(
    page.getByRole('heading', { name: /Deterministic expression preview|确定性表达预览/ })
  ).toBeVisible()
  await expect(page.getByText('Socratic Guide')).toBeVisible()
  await page.getByRole('button', { name: /Save Tutor Persona|保存 Tutor Persona/ }).click()
  await expect(
    page.locator('[role="status"]').filter({ hasText: /saved as version 2|已保存为版本 2/ })
  ).toBeVisible()
})

test('opens a delivered review reminder and can acknowledge it', async ({ page }) => {
  await installTutorRoutes(page)
  await page.goto('/settings/tutor')

  const reminder = page.getByText(
    /Knowledge component time-complexity is due for review|知识点 time-complexity 已到复习时间/
  )
  await expect(reminder).toBeVisible()
  await expect(page.getByRole('link', { name: /Review now|开始复习/ })).toHaveAttribute(
    'href',
    '/settings/learning-model/computer-science?tab=reviews'
  )
  await page.getByRole('button', { name: /Mark read|标为已读/ }).click()
  await expect(reminder).not.toBeVisible()
  await expect(
    page.getByText(/There are no pending review reminders|当前没有待处理的复习提醒/)
  ).toBeVisible()
})

test('a version 409 preserves the draft and moves focus to recovery', async ({ page }) => {
  await installTutorRoutes(page, { save409Once: true })
  await page.goto('/settings/tutor')
  await page.getByLabel(/Display name|显示名称/).fill('Do not overwrite me')
  await page.getByRole('button', { name: /Save Tutor Persona|保存 Tutor Persona/ }).click()

  const alert = page
    .locator('[role="alert"]')
    .filter({ hasText: /changed in another window|另一个窗口更新/ })
  await expect(alert).toBeVisible()
  await expect(alert).toContainText(/Page version 1 · Server version 2|页面版本 1 · 服务端版本 2/)
  await expect(alert).toBeFocused()
  await expect(page.getByLabel(/Display name|显示名称/)).toHaveValue('Do not overwrite me')
  await expect(page.getByRole('button', { name: /Load latest version|加载最新版本/ })).toBeVisible()
})
