import { expect, test, type Page } from '@playwright/test'

async function installNavigationRoutes(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem('traittutor:onboarding-profile-dismissed', 'true')
  })
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/v1/auth/status') {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: 'e2e' } })
      return
    }
    if (url.pathname === '/api/v1/sessions/session-42') {
      await route.fulfill({
        json: {
          id: 'session-42',
          session_id: 'session-42',
          title: 'Canonical thread',
          created_at: 1,
          updated_at: 2,
          preferences: { workspace_mode: 'learn' },
          messages: [
            {
              id: 1,
              session_id: 'session-42',
              role: 'user',
              content: 'Canonical route reached',
              events: [],
              attachments: [],
              created_at: 1,
            },
          ],
        },
      })
      return
    }
    if (url.pathname.includes('/sessions')) {
      await route.fulfill({ json: { sessions: [] } })
      return
    }
    if (url.pathname === '/api/v1/settings') {
      await route.fulfill({ json: { catalog: {} } })
      return
    }
    if (url.pathname === '/api/v1/memory/learner/overview') {
      await route.fulfill({
        json: {
          global: {
            scope: 'global',
            subject: null,
            inference_enabled: true,
            preferences: [],
            concept_signals: [],
            strategy_evidence: [],
            understanding: null,
            updated_at: '2026-08-10T01:00:00+00:00',
            needs_rebuild: false,
          },
          subjects: [],
          inference_enabled: true,
          pending_subjects: [],
        },
      })
      return
    }
    if (url.pathname === '/api/v1/memory/learner/evidence') {
      await route.fulfill({ json: { evidence: [] } })
      return
    }
    if (url.pathname === '/api/v1/memory/learner/reflections') {
      await route.fulfill({ json: { reflections: [], summary: {} } })
      return
    }
    if (url.pathname === '/api/v1/learning-packs') {
      await route.fulfill({
        json: {
          packs: Array.from({ length: 12 }, (_, index) => ({
            pack_id: `pack-${index + 1}`,
            title: `Learning path ${index + 1}`,
            goal: { text: `Learning goal ${index + 1}`, status: 'active' },
            material: {},
            artifacts: { courseware: [], flashcards: [], quiz: [] },
            flashcard_progress: {},
            quiz_attempts: [],
            due_review_count: 0,
            created_at: '2026-08-10T01:00:00+00:00',
            updated_at: '2026-08-10T01:00:00+00:00',
          })),
        },
      })
      return
    }
    await route.fulfill({ status: 200, json: {} })
  })
}

test('the assistant opens the assistant workspace', async ({ page }) => {
  await installNavigationRoutes(page)
  await page.goto('/home')

  const assistantLink = page.getByRole('link', { name: /Learning Tools|学习工具/, exact: true }).first()
  await expect(assistantLink).toBeVisible()
  await expect(assistantLink).toHaveAttribute('href', '/assist')
  await expect(page.locator('a[href="/settings"]').first()).toBeVisible()
  await assistantLink.click()
  await expect(page).toHaveURL(/\/assist$/)
  await expect(
    page.getByRole('heading', { name: /Start your learning task|开始你的学习任务/ })
  ).toBeVisible()
})

test('the home learning list deduplicates repeated pack identities', async ({ page }) => {
  await installNavigationRoutes(page)
  await page.route('**/api/v1/learning-packs', async route => {
    if (route.request().method() !== 'GET') {
      await route.continue()
      return
    }
    const pack = {
      pack_id: 'duplicate-pack',
      title: 'Older title',
      goal: { text: 'Duplicate goal', status: 'active' },
      material: {},
      artifacts: { courseware: [], flashcards: [], quiz: [] },
      flashcard_progress: {},
      quiz_attempts: [],
      due_review_count: 0,
      created_at: '2026-08-10T01:00:00+00:00',
      updated_at: '2026-08-10T01:00:00+00:00',
    }
    await route.fulfill({
      json: {
        packs: [
          pack,
          {
            ...pack,
            title: 'Newer title',
            updated_at: '2026-08-11T01:00:00+00:00',
          },
        ],
      },
    })
  })

  const consoleWarnings: string[] = []
  page.on('console', message => {
    if (message.type() === 'warning') consoleWarnings.push(message.text())
  })
  await page.goto('/home')

  await expect(page.getByText('Newer title', { exact: true })).toBeVisible()
  await expect(page.getByText('Older title', { exact: true })).toHaveCount(0)
  await expect(page.locator('a[href="/learning/duplicate-pack"]')).toHaveCount(1)
  expect(consoleWarnings.filter(message => message.includes('same key'))).toEqual([])
})

test('the root drops retired workspace query parameters', async ({ page }) => {
  await installNavigationRoutes(page)
  await page.goto('/?session=session-42&capability=deep_question&tool=web_search')

  await expect(page).toHaveURL(/\/home$/)
})

test('the brand artwork follows all four persisted themes', async ({ page }) => {
  await installNavigationRoutes(page)
  await page.goto('/home')

  const mark = page.getByTestId('traittutor-mark').first()
  const homeIcon = page.locator('a[href="/home"] [data-icon-name="home"]').first()
  const researchIcon = page.locator('a[href="/research"] [data-icon-name="research"]').first()
  const themes = ['light', 'dark', 'snow', 'teal'] as const

  for (const theme of themes) {
    await page.evaluate(nextTheme => {
      window.localStorage.setItem('traittutor-theme', nextTheme)
    }, theme)
    await page.reload()
    await expect(mark).toBeVisible()
    await expect
      .poll(() => mark.evaluate(element => getComputedStyle(element).backgroundImage))
      .toContain(`/brand/traittutor-mark-${theme}.png`)
    await expect(homeIcon).toBeVisible()
    await expect(researchIcon).toBeVisible()
    await expect
      .poll(() => homeIcon.evaluate(element => getComputedStyle(element).backgroundImage))
      .toContain(`/brand/icons/${theme}/home.png`)
    await expect
      .poll(() => researchIcon.evaluate(element => getComputedStyle(element).backgroundImage))
      .toContain(`/brand/icons/${theme}/research.png`)
  }
})

test('the retired settings status route stays missing', async ({ page }) => {
  const response = await page.goto('/settings/status')

  expect(response?.status()).toBe(404)
})

test('the 320px navigation shows the five canonical routes and no retired personal links', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 })
  await installNavigationRoutes(page)
  await page.goto('/home')

  const primaryNavigation = page.getByRole('navigation', { name: /Main navigation|主导航/ })
  for (const href of ['/home', '/assist', '/research', '/learning', '/settings']) {
    await expect(primaryNavigation.locator(`a[href="${href}"]`)).toBeVisible()
  }

  const linkBoxes = await primaryNavigation.locator('a').evaluateAll(links =>
    links.map(link => {
      const box = link.getBoundingClientRect()
      return { left: box.left, right: box.right, width: box.width, height: box.height }
    })
  )
  expect(linkBoxes).toHaveLength(5)
  for (const box of linkBoxes) {
    expect(box.left).toBeGreaterThanOrEqual(0)
    expect(box.right).toBeLessThanOrEqual(320)
    expect(box.width).toBe(40)
    expect(box.height).toBe(40)
  }

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  )
  expect(overflow).toBeLessThanOrEqual(0)
})

test('a long active-path list owns a visible, functional page scrollbar', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 })
  await installNavigationRoutes(page)
  await page.goto('/learning')

  const scrollRoot = page.locator('main.learning-canvas')
  await expect(page.getByRole('heading', { name: /Active paths|进行中的路径/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /Learning goal 12/ })).toBeAttached()

  const dimensions = await scrollRoot.evaluate(element => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }))
  expect(dimensions.scrollHeight).toBeGreaterThan(dimensions.clientHeight)

  await scrollRoot.hover()
  await page.mouse.wheel(0, 1200)
  await expect.poll(() => scrollRoot.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
})
