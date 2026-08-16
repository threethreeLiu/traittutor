import { expect, test, type Page } from '@playwright/test'

const subject = {
  subject_id: 'math',
  label: 'Mathematics',
  path: ['STEM', 'Mathematics'],
  confidence: 1,
  source: 'explicit',
  confirmed: true,
}

const learnerProfile = {
  scope: 'subject',
  subject,
  inference_enabled: true,
  preferences: [],
  concept_signals: [
    {
      concept_id: 'algebra',
      label: 'Algebra',
      support_level: 'starting',
      confidence: 0,
      attempt_count: 0,
      misconception_tags: [],
      evidence_refs: [],
      evidence_state: 'insufficient_evidence',
      model_version: 'v1-uncalibrated',
      stage_policy_version: 'bkt-stage-policy-v1',
      observation_count: 0,
      verified_observation_count: 0,
      module_id: 'module-1',
    },
  ],
  strategy_evidence: [],
  understanding: {
    status: 'starting',
    concept_count: 1,
    observed_concept_count: 0,
    coverage: 0,
    confidence: 0,
    recent_activity_at: null,
    review_load: 0,
  },
  updated_at: '2026-08-10T01:00:00+00:00',
  needs_rebuild: false,
}

const learnerOverview = {
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
  subjects: [learnerProfile],
  inference_enabled: true,
  pending_subjects: [],
}

const learningModelOverview = {
  generated_at: '2026-08-10T01:00:00+00:00',
  today: {
    meta: { status: 'ready', updated_at: null, source_refs: [], unavailable_sources: [] },
    active_subject_count: 1,
    due_review_count: 0,
    open_error_count: 1,
    attribution_pending_count: 1,
    latest_activity_at: null,
  },
  confirmed_subjects: {
    meta: { status: 'ready', updated_at: null, source_refs: [], unavailable_sources: [] },
    items: [
      {
        subject_id: 'math',
        label: 'Mathematics',
        last_activity_at: null,
        covered_kc_count: 1,
        strong_evidence_count: 0,
        open_error_count: 1,
        due_review_count: 0,
        source_refs: [],
      },
    ],
  },
  pending_subjects: {
    meta: { status: 'empty', updated_at: null, source_refs: [], unavailable_sources: [] },
    items: [],
  },
  task_queue: {
    meta: { status: 'empty', updated_at: null, source_refs: [], unavailable_sources: [] },
    items: [],
  },
  support: {
    meta: { status: 'ready', updated_at: null, source_refs: [], unavailable_sources: [] },
    inference_enabled: true,
    confirmed_preference_count: 0,
    confirmed_reflection_count: 0,
    compass_signal_count: 0,
  },
}

const learningModelDetail = {
  generated_at: '2026-08-10T01:00:00+00:00',
  header: {
    subject_id: 'math',
    label: 'Mathematics',
    confirmed: true,
    updated_at: '2026-08-10T01:00:00+00:00',
    data_status: 'ready',
  },
  tabs: Object.fromEntries(
    ['overview', 'knowledge', 'errors', 'reviews', 'misconceptions', 'support', 'governance'].map(
      tab => [
        tab,
        {
          meta: { status: 'ready', updated_at: null, source_refs: [], unavailable_sources: [] },
          item_count: 0,
          actionable_count: 0,
          ...(tab === 'knowledge'
            ? { mastery_items: [], model_version: 'v1-uncalibrated', mapping_version: null }
            : {}),
        },
      ]
    )
  ),
  allowed_actions: ['continue_learning', 'correct_subject', 'view_evidence'],
}

const supportProfile = {
  profile_id: 'profile-1',
  scores: { O: 8, C: 7, E: 5, A: 7, N: 4 },
  levels: { O: 'high', C: 'high', E: 'mid', A: 'high', N: 'low' },
  dominant_traits: ['O', 'C'],
  summary: 'Exploratory and structured learning support.',
  answers: {},
  created_at: '2026-08-10T01:00:00+00:00',
  metadata: {
    slr_support: {
      version: 'v1',
      source: 'big_five_initial',
      status: 'initial',
      dimensions: Object.fromEntries(
        ['goal_planning', 'monitoring_regulation', 'reflection_transfer', 'motivation_emotion'].map(
          key => [
            key,
            {
              label: key.replaceAll('_', ' '),
              detail: `Support for ${key}`,
              actions: [],
              emphasis: 'standard',
              evidence_count: 1,
            },
          ]
        )
      ),
      boundary: 'Support signals are not ability judgments.',
    },
  },
}

async function installLearningRoutes(
  page: Page,
  options: { governanceGate?: Promise<void>; failGovernance?: boolean } = {}
) {
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path === '/api/v1/auth/status') {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: 'e2e' } })
      return
    }
    if (path === '/api/v1/memory/learner/subjects/math') {
      await route.fulfill({ json: learnerProfile })
      return
    }
    if (path === '/api/v1/learning-model/subjects/math') {
      await route.fulfill({ json: learningModelDetail })
      return
    }
    if (path === '/api/v1/learning-model/overview') {
      await route.fulfill({ json: learningModelOverview })
      return
    }
    if (path === '/api/v1/memory/learner/overview') {
      await route.fulfill({ json: learnerOverview })
      return
    }
    if (path === '/api/v1/traittutor/profile/profiles') {
      await route.fulfill({ json: { profiles: [supportProfile] } })
      return
    }
    if (path === '/api/v1/learning-state') {
      await route.fulfill({
        json: {
          subject_id: 'math',
          source_revision: 'a'.repeat(64),
          param_version: 'v1-uncalibrated',
          calibrated: false,
          strong_event_count: 0,
          knowledge: [],
        },
      })
      return
    }
    if (path === '/api/v1/memory/learner/subjects/math/knowledge-graph') {
      await route.fulfill({
        json: {
          subject,
          nodes: [
            {
              concept_id: 'algebra',
              label: 'Algebra',
              module_id: 'module-1',
              module_label: 'Foundations',
              evidence_chunk_ids: ['chunk-1'],
              confidence: 0.9,
            },
          ],
          edges: [],
          source_refs: ['chunk-1'],
        },
      })
      return
    }
    if (path === '/api/v1/memory/learner/evidence') {
      await route.fulfill({ json: { evidence: [] } })
      return
    }
    if (path === '/api/v1/memory/learner/reflections') {
      await route.fulfill({
        json: {
          reflections: [],
          summary: {
            candidate: 0,
            confirmed: 0,
            rejected: 0,
            stale: 0,
            needs_rebuild: 0,
            applies_to_compass: 0,
          },
        },
      })
      return
    }
    if (path === '/api/v1/memory/learner/context/preview') {
      await route.fulfill({
        json: {
          purpose: 'courseware',
          subject,
          active_goal: null,
          plan: { rationale: [], srl_support: [] },
          memory_snapshot: null,
          relevant_concept_signals: [],
          constraints: [],
          evidence_refs: [],
          degraded: false,
          degradation_reason: null,
        },
      })
      return
    }

    if (
      ['/api/v1/errors', '/api/v1/repairs', '/api/v1/misconceptions', '/api/v1/reviews'].includes(
        path
      )
    ) {
      if (path === '/api/v1/errors') await options.governanceGate
      if (options.failGovernance && path === '/api/v1/errors') {
        await route.fulfill({ status: 503, json: { detail: 'Governance store unavailable' } })
        return
      }
      const data =
        path === '/api/v1/errors'
          ? [
              {
                error_id: 'error-1',
                question_id: 'question-1',
                subject_id: 'math',
                kc_id: 'algebra',
                module_id: 'module-1',
                error_type: 'application',
                status: 'open',
                attribution_status: 'attribution_pending',
                source_event_ids: ['event-1'],
                created_at: 1_786_300_000,
                repaired_at: null,
                relapsed_at: null,
                last_seen_at: null,
              },
            ]
          : []
      await route.fulfill({ json: data })
      return
    }
    await route.fulfill({ status: 200, json: {} })
  })
}

test('unknown mastery stays non-numeric while governance loads and renders at 320px', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 })
  let release!: () => void
  const gate = new Promise<void>(resolve => {
    release = resolve
  })
  await installLearningRoutes(page, { governanceGate: gate })
  await page.goto('/settings/learning-model/math')

  await expect(page.getByRole('heading', { name: 'Mathematics' })).toBeVisible()
  await page.getByRole('tab', { name: /Knowledge and KC|Knowledge & KCs|知识与 KC/i }).click()
  await expect(
    page.getByRole('heading', { name: /Knowledge state and graded evidence|知识状态与可判分证据/ })
  ).toBeVisible()
  const insufficient = page.locator('[data-mastery-state="insufficient_evidence"]')
  await expect(insufficient.first()).toContainText(/Insufficient evidence|证据不足/)
  expect(await insufficient.count()).toBeGreaterThanOrEqual(2)

  await page.getByRole('tab', { name: /Data and governance|Data & governance|数据与治理/i }).click()
  await expect(
    page.getByLabel(/Loading learning governance records|正在加载学习治理记录/)
  ).toBeVisible()
  release()
  await expect(
    page.getByRole('heading', {
      name: /Errors, repairs, misconceptions, and reviews|错误、修复、误概念与复习/,
    })
  ).toBeVisible()
  await expect(page.getByText(/Attribution pending|归因待确认/)).toBeVisible()

  await expect(page.getByRole('progressbar')).toHaveCount(0)
  await expect(page.getByText(/\b0%\b/)).toHaveCount(0)

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  )
  expect(overflow).toBeLessThanOrEqual(0)
})

test('governance failure is isolated from the learner profile', async ({ page }) => {
  await installLearningRoutes(page, { failGovernance: true })
  await page.goto('/settings/learning-model/math')

  await expect(page.getByRole('heading', { name: 'Mathematics' })).toBeVisible()
  await page.getByRole('tab', { name: /Data and governance|Data & governance|数据与治理/i }).click()
  await expect(
    page.locator('[role="alert"]').filter({ hasText: 'Governance store unavailable' })
  ).toBeVisible()
  await page.getByRole('tab', { name: /Knowledge and KC|Knowledge & KCs|知识与 KC/i }).click()
  await expect(page.getByText(/Insufficient evidence|证据不足/).first()).toBeVisible()
  await expect(page.getByRole('progressbar')).toHaveCount(0)
})

test('the personality profile opens with Big Five and SRL without a learner-profile link', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 })
  await installLearningRoutes(page)
  await page.goto('/settings/personality')

  await expect(
    page.getByRole('heading', { name: /Personality Profile|性格画像/ })
  ).toBeVisible()
  await expect(page.getByRole('img', { name: /Big Five radar chart|大五画像雷达图/ })).toBeVisible()
  await expect(page.getByText(/SLR · Learning support network|SLR · 学习支持网络/)).toBeVisible()
  await expect(page.getByText(/BKT knowledge tracking|BKT 知识追踪/)).toHaveCount(0)

  await expect(
    page.getByRole('link', { name: /Open learner profile|查看学习者画像/ })
  ).toHaveCount(0)

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  )
  expect(overflow).toBeLessThanOrEqual(0)
})
