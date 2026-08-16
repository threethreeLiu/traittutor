import { expect, test, type Page } from '@playwright/test'

type CanonicalOptions = {
  candidateGate?: Promise<void>
  failItems?: boolean
  conflict409Once?: boolean
}

const candidate = (id: string, key: string, value: string) => ({
  candidate_id: id,
  scope: 'subject',
  subject_id: 'mathematics',
  kc_id: null,
  key,
  value,
  provenance: 'inferred',
  status: 'candidate',
  confidence: 0.82,
  sensitivity: 'personal',
  evidence_refs: [`event-${id}`],
  source_ref: null,
  proposed_supersedes_id: null,
  conflict_memory_ids: [],
  valid_from: null,
  valid_until: null,
  created_at: '2026-08-10T01:00:00+00:00',
})

const activeItem = {
  memory_id: 'memory-active',
  scope: 'global',
  subject_id: null,
  kc_id: null,
  key: 'feedback_style',
  value: 'Uses concise feedback',
  redacted: false,
  provenance: 'explicit',
  status: 'active',
  confidence: 1,
  sensitivity: 'personal',
  valid_from: '2026-08-10T01:00:00+00:00',
  valid_until: null,
  supersedes_id: null,
  evidence_refs: ['turn-explicit'],
  source_ref: null,
  created_at: '2026-08-10T01:00:00+00:00',
  updated_at: '2026-08-10T01:00:00+00:00',
}

async function installCanonicalRoutes(page: Page, options: CanonicalOptions = {}) {
  let candidates = [
    candidate('candidate-activate', 'example_style', 'Show a worked example first'),
    candidate('candidate-reject', 'pace', 'Always move very quickly'),
  ]
  let items: Array<Record<string, unknown>> = [activeItem]
  let conflicts = [
    {
      scope: 'subject',
      subject_id: 'mathematics',
      kc_id: null,
      key: 'goal',
      candidate_id: 'candidate-conflict',
      candidate_value: 'Study calculus',
      memory_ids: ['memory-goal'],
      values: ['Study algebra'],
    },
  ]
  let index = {
    generation: 3,
    entries: [
      {
        entry_id: 'profile',
        index_version: 1,
        generation: 3,
        content_hash: 'hash-index',
        claim_count: 1,
        assertion_states: ['verified'],
        updated_at: '2026-08-10T01:00:00+00:00',
      },
    ],
  }
  let conflictAttempt = 0

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/v1/auth/status') {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: 'e2e' } })
      return
    }
    if (path === '/api/v1/memories/candidates' && request.method() === 'GET') {
      await options.candidateGate
      await route.fulfill({ json: candidates })
      return
    }
    if (path === '/api/v1/memories/items' && request.method() === 'GET') {
      if (options.failItems) {
        await route.fulfill({ status: 503, json: { detail: 'Memory service unavailable' } })
      } else {
        await route.fulfill({ json: items })
      }
      return
    }
    if (path === '/api/v1/memories/conflicts' && request.method() === 'GET') {
      await route.fulfill({ json: conflicts })
      return
    }
    if (path === '/api/v1/memories/index/status') {
      await route.fulfill({ json: index })
      return
    }
    if (path === '/api/v1/memories/access-records') {
      await route.fulfill({
        json: [
          {
            record_id: 'access-1',
            snapshot_id: 'snapshot-courseware',
            created_at: '2026-08-10T02:00:00+00:00',
            scope: 'subject_state',
            key: 'private-kc-key-must-not-render',
            version_read: 'v1',
            purpose: 'courseware_generation',
            user_authorized: true,
          },
          {
            record_id: 'access-2',
            snapshot_id: 'snapshot-courseware',
            created_at: '2026-08-10T02:00:00+00:00',
            scope: 'subject_state',
            key: 'second-private-key-must-not-render',
            version_read: 'v3',
            purpose: 'courseware_generation',
            user_authorized: true,
          },
        ],
      })
      return
    }

    if (/\/api\/v1\/memories\/candidates\/[^/]+\/activate$/.test(path)) {
      const body = request.postDataJSON() as Record<string, unknown>
      expect(body).not.toHaveProperty('owner_id')
      expect(body).not.toHaveProperty('user_id')
      expect(body.confirmed).toBe(true)
      const id = decodeURIComponent(path.split('/').at(-2) ?? '')
      const activated = candidates.find(entry => entry.candidate_id === id)!
      candidates = candidates.filter(entry => entry.candidate_id !== id)
      const item = {
        ...activeItem,
        memory_id: `memory-${id}`,
        key: activated.key,
        value: activated.value,
      }
      items = [...items, item]
      await route.fulfill({ json: item })
      return
    }
    if (/\/api\/v1\/memories\/candidates\/[^/]+\/reject$/.test(path)) {
      const body = request.postDataJSON() as Record<string, unknown>
      expect(body).not.toHaveProperty('owner_id')
      const id = decodeURIComponent(path.split('/').at(-2) ?? '')
      const rejected = candidates.find(entry => entry.candidate_id === id)!
      candidates = candidates.filter(entry => entry.candidate_id !== id)
      await route.fulfill({ json: { ...rejected, status: 'rejected' } })
      return
    }
    if (/\/api\/v1\/memories\/conflicts\/[^/]+\/supersede$/.test(path)) {
      conflictAttempt += 1
      const body = request.postDataJSON() as Record<string, unknown>
      expect(body.confirmed).toBe(true)
      expect(body).not.toHaveProperty('owner_id')
      if (options.conflict409Once && conflictAttempt === 1) {
        await route.fulfill({ status: 409, json: { detail: 'The active value changed' } })
        return
      }
      conflicts = []
      const item = {
        ...activeItem,
        memory_id: 'memory-new-goal',
        key: 'goal',
        value: 'Study calculus',
      }
      items = [...items, item]
      await route.fulfill({ json: item })
      return
    }
    if (/\/api\/v1\/memories\/items\/[^/]+$/.test(path) && request.method() === 'DELETE') {
      const id = decodeURIComponent(path.split('/').at(-1) ?? '')
      const existing = items.find(entry => entry.memory_id === id)!
      const redacted = {
        ...existing,
        value: null,
        redacted: true,
        status: 'deleted',
        evidence_refs: [],
        source_ref: null,
      }
      items = items.map(entry => (entry.memory_id === id ? redacted : entry))
      await route.fulfill({ json: { item: redacted, invalidated_index_generation: 4 } })
      return
    }
    if (path === '/api/v1/memories/index/rebuild' && request.method() === 'POST') {
      index = {
        generation: 4,
        entries: [
          { ...index.entries[0], generation: 4, claim_count: 2, assertion_states: ['verified'] },
        ],
      }
      await route.fulfill({ json: index })
      return
    }

    await route.fulfill({ status: 200, json: {} })
  })
}

test('shows a deterministic loading state while canonical memory is pending', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>(resolve => {
    release = resolve
  })
  await installCanonicalRoutes(page, { candidateGate: gate })

  await page.goto('/settings/memory')
  await expect(page.getByLabel(/Loading memory|正在加载记忆/)).toBeVisible()
  release()
  await expect(page.getByRole('heading', { name: /My memory|我的记忆/ })).toBeVisible()
})

test('keeps API failure visible without inventing memory state', async ({ page }) => {
  await installCanonicalRoutes(page, { failItems: true })
  await page.goto('/settings/memory')

  await expect(
    page.locator('[role="alert"]').filter({ hasText: 'Memory service unavailable' })
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: /My memory|我的记忆/ })).toBeVisible()
  await expect(page.getByText('Uses concise feedback')).toHaveCount(0)
})

test('reviews, deletes, and rebuilds canonical memory at 320px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 })
  await installCanonicalRoutes(page)
  await page.goto('/settings/memory')
  await expect(page.getByRole('heading', { name: /My memory|我的记忆/ })).toBeVisible()

  const accessSection = page
    .getByRole('heading', { name: /Recent memory use|最近的记忆使用/ })
    .locator('xpath=ancestor::section')
  const accessSummary = accessSection.getByTestId('memory-access-summary')
  await expect(accessSummary).toContainText(/subject state/)
  await expect(accessSummary).toContainText(/courseware generation/)
  await expect(accessSummary.getByText('2', { exact: true })).toBeVisible()
  await expect(accessSection).not.toContainText('private-kc-key-must-not-render')
  await expect(accessSection).not.toContainText('second-private-key-must-not-render')

  await page
    .getByRole('button', { name: /Confirm|确认记住/ })
    .first()
    .click()
  await expect(page.getByRole('status')).toContainText(/active memory|生效记忆/)
  await page.getByRole('button', { name: /Reject|拒绝/ }).click()
  await expect(page.getByText('Always move very quickly')).toHaveCount(0)

  const item = page.getByText('Uses concise feedback').locator('xpath=ancestor::article')
  await item.getByRole('button', { name: /Delete|删除/ }).click()
  await expect(
    page.getByRole('dialog', { name: /Delete this memory\?|删除这条记忆/ })
  ).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: /Delete this memory\?|删除这条记忆/ })).toHaveCount(
    0
  )
  await item.getByRole('button', { name: /Delete|删除/ }).click()
  await page
    .getByRole('dialog')
    .getByRole('button', { name: /Delete|确认删除/ })
    .click()
  await expect(page.getByText('Uses concise feedback')).toHaveCount(0)

  const indexSection = page
    .getByRole('heading', { name: /Long-term memory index|长期记忆索引/ })
    .locator('xpath=ancestor::section')
  await expect(
    indexSection.locator('span').filter({ hasText: /^(Verified|已验证)$/ })
  ).toBeVisible()
  await indexSection.getByRole('button', { name: /Rebuild|重建/ }).click()
  await expect(indexSection).toContainText(/2 claims|2 条主张/)

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  )
  expect(overflow).toBeLessThanOrEqual(0)
})

test('focus stays in the conflict dialog and a 409 never silently overwrites', async ({ page }) => {
  await installCanonicalRoutes(page, { conflict409Once: true })
  await page.goto('/settings/memory')

  await page.getByRole('button', { name: /Compare and resolve|比较并处理/ }).click()
  const dialog = page.getByRole('dialog', { name: /Confirm memory replacement|确认替换冲突记忆/ })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: /Cancel|取消/ })).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  expect(await dialog.evaluate(node => node.contains(document.activeElement))).toBe(true)

  await dialog.getByRole('checkbox').check()
  await dialog.getByRole('button', { name: /Replace old memory|替换旧记忆/ }).click()
  await expect(
    page.locator('[role="alert"]').filter({ hasText: 'The active value changed' })
  ).toBeVisible()
  await expect(dialog).toBeVisible()

  await dialog.getByRole('button', { name: /Replace old memory|替换旧记忆/ }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('Study calculus')).toBeVisible()
})
