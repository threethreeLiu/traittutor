import { expect, test, type Page, type Route } from '@playwright/test'

const NOW = '2026-08-10T08:00:00+00:00'

interface ResearchFixture {
  workspace: Record<string, unknown> | null
  briefs: Array<Record<string, unknown>>
  runs: Array<Record<string, unknown>>
  runReads: number
  unknownAfterRunReads?: number
  briefConflict?: boolean
  settlePauseOnRead?: boolean
  sources?: Array<Record<string, unknown>>
  reports?: Array<Record<string, unknown>>
  lastBriefBody?: Record<string, unknown>
  lastFollowUpBody?: Record<string, unknown>
}

test.use({ viewport: { width: 320, height: 900 } })

test.describe('research sidebar recents', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('syncs a newly created workspace into recents without a reload', async ({ page }) => {
    const fixture = emptyFixture()
    await installResearchRoutes(page, fixture)

    await page.goto('/research')
    await page.getByLabel(/Workspace name|工作区名称/).fill('Durable evidence review')
    await page.getByRole('button', { name: /^Create$|^创建$/ }).click()

    await expect(page).toHaveURL(/\/research\/rw-e2e$/)
    await expect(
      page.locator('aside').getByRole('button', { name: /Durable evidence review/ })
    ).toBeVisible()
  })
})

test('creates a workspace and recovers the brief and run lifecycle from REST', async ({ page }) => {
  const fixture = emptyFixture()
  await installResearchRoutes(page, fixture)

  await page.goto('/research')
  await expect(
    page.getByRole('heading', { level: 1, name: /Research workspaces|研究工作区/ })
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { level: 3, name: /No research workspaces yet|还没有研究工作区/ })
  ).toBeVisible()

  const title = page.getByLabel(/Workspace name|工作区名称/)
  await title.focus()
  await title.fill('Durable evidence review')
  await expect(page.getByLabel(/Subject ID|学科 ID/)).toHaveCount(0)
  await page.getByRole('button', { name: /^Create$|^创建$/ }).click()
  await expect(page).toHaveURL(/\/research\/rw-e2e$/)

  await page.getByRole('button', { name: /Research follow-up|研究追问/ }).click()
  await expect(
    page.getByRole('region', { name: /Research follow-up assistant|研究追问助手/ })
  ).toBeVisible()
  await page.getByRole('button', { name: /Minimize research assistant|最小化研究助手/ }).click()

  await page
    .getByLabel(/Research question|研究问题/)
    .fill('How does durable recovery protect a research run?')
  await page.getByLabel(/Objectives|研究目标/).fill('Trace state\nVerify evidence')
  await page.getByRole('button', { name: /Save research brief|保存研究简报/ }).click()
  await expect(page.getByText(/Version 1|版本 1/)).toBeVisible()

  await page.getByRole('button', { name: /Start research|启动研究/ }).click()
  await expect(page.getByText(/^Queued$|^排队中$/)).toBeVisible()

  // Simulate a process restart: the browser reloads and recovers current truth
  // only from the durable REST representation, not an in-memory progress event.
  fixture.runs[0] = { ...fixture.runs[0], status: 'running', revision: 2, updated_at: NOW }
  await page.reload()
  await expect(page.getByText(/^Running$|^研究中$/)).toBeVisible()
  await expect(page.getByTestId('generation-run-trace')).toHaveCount(0)

  fixture.settlePauseOnRead = true
  await page.getByRole('button', { name: /^Pause$|^暂停$/ }).click()
  await expect(page.getByText(/^Paused$|^已暂停$/)).toBeVisible()
  await page.getByRole('button', { name: /^Resume$|^继续$/ }).click()
  await expect(page.getByText(/^Queued$|^排队中$/)).toBeVisible()
  await page.getByRole('button', { name: /^Cancel$|^取消$/ }).click()
  await expect(page.getByText(/^Cancelled$|^已取消$/)).toBeVisible()

  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth
    )
  ).toBe(true)
})

test('surfaces a 409 conflict, keeps the draft, and moves focus to the alert', async ({ page }) => {
  const fixture = populatedFixture()
  fixture.briefConflict = true
  await installResearchRoutes(page, fixture)

  await page.goto('/research/rw-e2e')
  const question = page.getByLabel(/Research question|研究问题/)
  await question.fill('A locally edited question that must not overwrite version 2')
  await page.getByRole('button', { name: /Save research brief|保存研究简报/ }).click()

  const alert = page
    .getByRole('alert')
    .filter({ hasText: /changed in another window|已在其他窗口更新/ })
  await expect(alert).toContainText(/changed in another window|已在其他窗口更新/)
  await expect(alert).toBeFocused()
  await expect(question).toHaveValue('A locally edited question that must not overwrite version 2')
})

test('binds an accessible KB when saving a KB-only frozen brief', async ({ page }) => {
  const fixture = emptyFixture()
  await installResearchRoutes(page, fixture)

  await page.goto('/research')
  await page.getByLabel(/Workspace name|工作区名称/).fill('Durable evidence review')
  await page.getByRole('button', { name: /^Create$|^创建$/ }).click()
  await page.getByLabel(/Research question|研究问题/).fill('What does the local evidence show?')
  await page.getByLabel(/Source scope|来源范围/).selectOption('knowledge_base')
  await page.getByLabel(/Knowledge base|知识库/).selectOption('user:kb:method-notes')
  await page.getByRole('button', { name: /Save research brief|保存研究简报/ }).click()

  expect(fixture.lastBriefBody).toMatchObject({
    source_policy: 'knowledge_base',
    knowledge_base_ref: 'user:kb:method-notes',
  })
})

test('fails closed when polling returns an unknown run state', async ({ page }) => {
  const fixture = populatedFixture()
  fixture.runs = [run('running', 2)]
  await installResearchRoutes(page, fixture)

  await page.goto('/research/rw-e2e')
  const pause = page.getByRole('button', { name: /^Pause$|^暂停$/ })
  await expect(pause).toBeEnabled()
  fixture.unknownAfterRunReads = fixture.runReads
  await expect(page.getByText(/unrecognized research state|无法识别的研究状态/)).toBeVisible({
    timeout: 7_000,
  })
  await expect
    .poll(async () => (await pause.count()) === 0 || (await pause.isDisabled()))
    .toBe(true)
})

test('retries a failed frozen run through its explicit CAS lifecycle action', async ({ page }) => {
  const fixture = populatedFixture()
  fixture.runs = [{ ...run('failed', 4), failure_reason: 'executor_failed' }]
  await installResearchRoutes(page, fixture)

  await page.goto('/research/rw-e2e')
  const retry = page.getByRole('button', { name: /^Retry$|^重试$/ })
  await expect(retry).toBeEnabled()
  await retry.click()
  await expect(page.getByText(/^Queued$|^排队中$/)).toBeVisible()
  expect(fixture.runs[0]).toMatchObject({ status: 'queued', revision: 5 })
})

test('invalidates a source without hiding the report and surfaces review status', async ({
  page,
}) => {
  const fixture = populatedFixture()
  fixture.runs = [run('completed', 2)]
  fixture.sources = [
    {
      source_id: 'rs-e2e',
      workspace_id: 'rw-e2e',
      url: 'https://evidence.example/primary',
      title: 'Primary evidence',
      excerpt: 'An auditable source.',
      retrieved_at: NOW,
      revision: 1,
      status: 'active',
      invalidated_at: null,
      invalidation_reason: null,
    },
  ]
  fixture.reports = [
    {
      report_id: 'rpt-e2e',
      workspace_id: 'rw-e2e',
      run_id: 'rr-e2e',
      body: 'The report body remains visible for audit.',
      claims: [
        {
          claim_id: 'rc-e2e',
          workspace_id: 'rw-e2e',
          run_id: 'rr-e2e',
          text: 'The primary source supports this claim.',
          kind: 'grounded',
          source_ids: ['rs-e2e'],
          created_at: NOW,
          revision: 1,
          evidence_status: 'active',
          review_required_source_ids: [],
        },
      ],
      created_at: NOW,
      revision: 1,
      evidence_status: 'active',
      review_required_source_ids: [],
    },
  ]
  await installResearchRoutes(page, fixture)

  await page.goto('/research/rw-e2e')
  await page.getByRole('button', { name: /Invalidate|标记失效/ }).click()
  await expect(page.getByText(/^(Source invalidated|来源已失效)$/)).toBeVisible()
  await expect(page.getByText(/Evidence review required|证据待复核/).first()).toBeVisible()
  await expect(
    page
      .getByLabel(/Sources, claims, and notes|来源、主张与笔记/)
      .getByText('The report body remains visible for audit.')
  ).toBeVisible()
  expect(fixture.sources?.[0]).toMatchObject({ status: 'invalidated', revision: 2 })
  expect(fixture.reports?.[0]).toMatchObject({ evidence_status: 'needs_review' })
})

test.describe('research follow-up assistant', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('starts a source-bound follow-up from the migrated research composer', async ({ page }) => {
    const fixture = populatedFixture()
    fixture.runs = [run('completed', 2)]
    fixture.sources = [researchSource()]
    fixture.reports = [researchReport()]
    await installResearchRoutes(page, fixture)

    await page.goto('/research/rw-e2e')
    const followUp = page.getByLabel(/Enter a research follow-up|输入研究追问/)
    await followUp.fill('Which evidence gap should be investigated next?')
    await followUp.press('Enter')

    await expect(page.getByText(/^Queued$|^排队中$/)).toBeVisible()
    expect(fixture.lastFollowUpBody).toMatchObject({
      question: 'Which evidence gap should be investigated next?',
      source_policy: 'web',
      parent_report_revision: 1,
      expected_workspace_revision: 2,
    })
    expect(fixture.lastFollowUpBody).not.toHaveProperty('user_id')
  })
})

function emptyFixture(): ResearchFixture {
  return { workspace: null, briefs: [], runs: [], runReads: 0 }
}

function populatedFixture(): ResearchFixture {
  return {
    workspace: workspace(),
    briefs: [brief()],
    runs: [],
    runReads: 0,
  }
}

function workspace(): Record<string, unknown> {
  return {
    workspace_id: 'rw-e2e',
    title: 'Durable evidence review',
    subject_id: 'research-methods',
    status: 'active',
    revision: 2,
    active_brief_id: 'rb-e2e',
    created_at: NOW,
    updated_at: NOW,
  }
}

function brief(): Record<string, unknown> {
  return {
    brief_id: 'rb-e2e',
    workspace_id: 'rw-e2e',
    version: 1,
    question: 'How does durable recovery protect a research run?',
    objectives: ['Trace state', 'Verify evidence'],
    constraints: [],
    source_policy: 'web',
    created_at: NOW,
  }
}

function run(status: string, revision: number): Record<string, unknown> {
  return {
    run_id: 'rr-e2e',
    workspace_id: 'rw-e2e',
    brief_id: 'rb-e2e',
    brief_version: 1,
    status,
    revision,
    fencing_epoch: 0,
    failure_reason: null,
    created_at: NOW,
    updated_at: NOW,
  }
}

function researchSource(): Record<string, unknown> {
  return {
    source_id: 'rs-e2e',
    workspace_id: 'rw-e2e',
    url: 'https://evidence.example/primary',
    title: 'Primary evidence',
    excerpt: 'An auditable source.',
    retrieved_at: NOW,
    revision: 1,
    status: 'active',
    invalidated_at: null,
    invalidation_reason: null,
  }
}

function researchReport(): Record<string, unknown> {
  return {
    report_id: 'rpt-e2e',
    workspace_id: 'rw-e2e',
    run_id: 'rr-e2e',
    body: 'The current report is grounded in the linked evidence.',
    claims: [],
    created_at: NOW,
    revision: 1,
    evidence_status: 'active',
    review_required_source_ids: [],
  }
}

async function installResearchRoutes(page: Page, fixture: ResearchFixture) {
  await page.addInitScript(() => {
    window.localStorage.setItem('traittutor:onboarding-profile-dismissed', 'true')
  })
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname

    if (path.endsWith('/auth/status'))
      return fulfill(route, { enabled: false, authenticated: true, username: 'e2e' })
    if (path.endsWith('/settings')) return fulfill(route, { catalog: {} })
    if (path.includes('/sessions')) return fulfill(route, { sessions: [] })
    if (path.endsWith('/memory/learner/overview')) return fulfill(route, { subjects: [] })
    if (path.endsWith('/knowledge/list')) {
      return fulfill(route, [{ id: 'user:kb:method-notes', name: 'method-notes', source: 'user' }])
    }

    if (path.endsWith('/research/workspaces') && request.method() === 'GET') {
      return fulfill(route, fixture.workspace ? [fixture.workspace] : [])
    }
    if (path.endsWith('/research/workspaces') && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>
      expect(body).toMatchObject({
        title: 'Durable evidence review',
      })
      expect(body).not.toHaveProperty('subject_id')
      expect(body).not.toHaveProperty('user_id')
      expect(body).not.toHaveProperty('owner_id')
      fixture.workspace = { ...workspace(), revision: 1, active_brief_id: null }
      return fulfill(route, fixture.workspace, 201)
    }
    if (path.endsWith('/research/workspaces/rw-e2e') && request.method() === 'GET') {
      return fulfill(route, fixture.workspace ?? workspace())
    }
    if (path.endsWith('/research/workspaces/rw-e2e/briefs') && request.method() === 'GET') {
      return fulfill(route, fixture.briefs)
    }
    if (
      path.includes('/research/workspaces/rw-e2e/briefs') &&
      ['POST', 'PUT'].includes(request.method())
    ) {
      if (fixture.briefConflict) {
        return fulfill(
          route,
          { detail: { code: 'revision_conflict', expected_revision: 1, actual_revision: 2 } },
          409
        )
      }
      const body = request.postDataJSON() as Record<string, unknown>
      expect(body).toHaveProperty('expected_workspace_revision')
      expect(body).not.toHaveProperty('user_id')
      fixture.lastBriefBody = body
      const next = {
        ...brief(),
        question: body.question,
        objectives: body.objectives,
        constraints: body.constraints,
        source_policy: body.source_policy,
        knowledge_base: body.knowledge_base_ref
          ? { resource_id: body.knowledge_base_ref, display_name: 'method-notes', source: 'user' }
          : null,
      }
      fixture.briefs = [next]
      fixture.workspace = {
        ...(fixture.workspace ?? workspace()),
        revision: 2,
        active_brief_id: 'rb-e2e',
      }
      return fulfill(route, next, request.method() === 'POST' ? 201 : 200)
    }
    if (path.endsWith('/research/workspaces/rw-e2e/runs') && request.method() === 'POST') {
      fixture.runs = [run('queued', 1)]
      return fulfill(route, fixture.runs[0], 202)
    }
    if (path.endsWith('/research/workspaces/rw-e2e/runs') && request.method() === 'GET') {
      fixture.runReads += 1
      if (fixture.unknownAfterRunReads && fixture.runReads > fixture.unknownAfterRunReads) {
        return fulfill(route, [{ ...fixture.runs[0], status: 'future_state' }])
      }
      if (fixture.settlePauseOnRead && fixture.runs[0]?.status === 'pausing') {
        fixture.runs[0] = {
          ...fixture.runs[0],
          status: 'paused',
          revision: Number(fixture.runs[0].revision) + 1,
        }
        fixture.settlePauseOnRead = false
      }
      return fulfill(route, fixture.runs)
    }
    const action = path.match(/\/runs\/rr-e2e\/(pause|resume|cancel|retry)$/)?.[1]
    if (action && request.method() === 'POST') {
      const current = fixture.runs[0]
      const body = request.postDataJSON() as Record<string, unknown>
      expect(body).toMatchObject({
        expected_revision: current.revision,
        expected_status: current.status,
      })
      const nextStatus =
        action === 'pause' ? 'pausing' : action === 'cancel' ? 'cancelled' : 'queued'
      fixture.runs[0] = { ...current, status: nextStatus, revision: Number(current.revision) + 1 }
      return fulfill(route, fixture.runs[0])
    }
    if (
      path.endsWith('/research/workspaces/rw-e2e/runs/rr-e2e/follow-up') &&
      request.method() === 'POST'
    ) {
      const body = request.postDataJSON() as Record<string, unknown>
      fixture.lastFollowUpBody = body
      fixture.runs = [
        {
          ...run('queued', 1),
          run_id: 'rr-follow-up',
          brief_id: 'rb-follow-up',
          brief_version: 2,
        },
        ...fixture.runs,
      ]
      return fulfill(route, fixture.runs[0], 202)
    }
    if (
      path.endsWith('/research/workspaces/rw-e2e/sources/rs-e2e') &&
      request.method() === 'DELETE'
    ) {
      const current = fixture.sources?.[0]
      const body = request.postDataJSON() as Record<string, unknown>
      expect(current).toBeTruthy()
      expect(body).toMatchObject({
        expected_revision: current?.revision,
        expected_status: 'active',
      })
      fixture.sources = [
        {
          ...current,
          status: 'invalidated',
          revision: Number(current?.revision) + 1,
          invalidated_at: NOW,
        },
      ]
      fixture.reports = fixture.reports?.map(report => ({
        ...report,
        evidence_status: 'needs_review',
        review_required_source_ids: ['rs-e2e'],
        revision: Number(report.revision) + 1,
        claims: (report.claims as Array<Record<string, unknown>>).map(claim => ({
          ...claim,
          evidence_status: 'needs_review',
          review_required_source_ids: ['rs-e2e'],
          revision: Number(claim.revision) + 1,
        })),
      }))
      return fulfill(route, fixture.sources[0])
    }
    if (path.endsWith('/research/workspaces/rw-e2e/sources'))
      return fulfill(route, fixture.sources ?? [])
    if (path.endsWith('/research/workspaces/rw-e2e/notes')) return fulfill(route, [])
    if (path.endsWith('/report'))
      return fixture.reports?.[0]
        ? fulfill(route, fixture.reports[0])
        : fulfill(route, { detail: 'Research object not found' }, 404)
    return fulfill(route, {})
  })
}

async function fulfill(route: Route, json: unknown, status = 200) {
  await route.fulfill({ status, json })
}
