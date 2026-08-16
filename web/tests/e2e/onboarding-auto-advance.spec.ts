import { expect, test } from '@playwright/test'

test('the onboarding assessment advances after selecting an answer', async ({ page }) => {
  let submittedAnswers: Record<string, number> | null = null
  let createdProfile: Record<string, unknown> | null = null
  const questions = Array.from({ length: 10 }, (_, index) => ({
    id: index + 1,
    text: `Question ${index + 1}`,
    trait: 'O',
    reverse: false,
  }))

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === '/api/v1/auth/status') {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: 'e2e' } })
      return
    }
    if (url.pathname === '/api/v1/traittutor/profile/profiles' && request.method() === 'GET') {
      await route.fulfill({ json: { profiles: createdProfile ? [createdProfile] : [] } })
      return
    }
    if (url.pathname === '/api/v1/traittutor/profile/profiles' && request.method() === 'POST') {
      submittedAnswers = (request.postDataJSON() as { answers: Record<string, number> }).answers
      createdProfile = {
        profile_id: 'profile-1',
        scores: { O: 8, C: 7, E: 6, A: 8, N: 5 },
        levels: { O: 'high', C: 'medium', E: 'medium', A: 'high', N: 'medium' },
        dominant_traits: [],
        summary: 'Created',
        answers: submittedAnswers,
        created_at: '2026-08-11T00:00:00+00:00',
      }
      await route.fulfill({ json: createdProfile })
      return
    }
    if (url.pathname === '/api/v1/traittutor/profile/questions') {
      await route.fulfill({
        json: {
          instrument: 'TIPI',
          scale: { min: 1, max: 5, neutral: 3 },
          options: [
            { value: 1, label: '非常不同意' },
            { value: 2, label: '有些不同意' },
            { value: 3, label: '既不同意也不反对' },
            { value: 4, label: '有些同意' },
            { value: 5, label: '非常同意' },
          ],
          questions,
          traits: [],
          usage_boundary: 'Teaching support only',
        },
      })
      return
    }
    if (url.pathname === '/api/v1/settings') {
      await route.fulfill({ json: { catalog: {} } })
      return
    }
    if (url.pathname.includes('/sessions')) {
      await route.fulfill({ json: { sessions: [] } })
      return
    }
    await route.fulfill({ status: 200, json: {} })
  })

  await page.goto('/settings/personality')
  const dialog = page.getByRole('dialog', {
    name: /调整你的学习支持方式|Tune your learning support/,
  })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('1 / 10')

  await dialog.getByRole('button', { name: /非常同意|Agree strongly/ }).click()

  await expect(dialog).toContainText('2 / 10')
  await expect(dialog).toContainText('Question 2')

  for (let question = 2; question <= 10; question += 1) {
    await dialog.getByRole('button', { name: /非常同意|Agree strongly/ }).click()
  }

  await expect(dialog).toContainText('10 / 10')
  await dialog.getByRole('button', { name: /完成并开始学习|Finish and start learning/ }).click()
  await expect(dialog).toHaveCount(0)
  expect(submittedAnswers).toEqual(
    Object.fromEntries(Array.from({ length: 10 }, (_, index) => [String(index + 1), 5]))
  )
  await expect(page.getByRole('img', { name: /大五画像雷达图|Big Five radar chart/ })).toBeVisible()
})
