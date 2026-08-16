import { expect, test } from '@playwright/test'

test('learning components are independently selectable except local calibration', async ({
  page,
}) => {
  const component = (
    componentId: string,
    componentType: string,
    executor: string,
    labelZh: string,
    labelEn: string,
    dependencies: string[] = []
  ) => ({
    component_id: componentId,
    component_type: componentType,
    executor,
    label_zh: labelZh,
    label_en: labelEn,
    concept_refs: [],
    support_dimensions: [],
    bkt_stage: 'unobserved',
    modality: executor === 'assessment' ? 'interactive' : 'text',
    dependencies,
    required: componentType !== 'diagnostic_check',
    reason: 'Independent component regression.',
    evidence_refs: [],
    completion_event: 'complete',
    status: 'pending',
  })
  const practiceId = 'practice-1'
  const components = [
    component('goal-1', 'goal_map', 'lesson', '目标地图', 'Goal map'),
    component('concept-1', 'concept_explanation', 'lesson', '核心概念讲解', 'Concept explanation'),
    component('diagnostic-1', 'diagnostic_check', 'assessment', '起点诊断', 'Diagnostic check'),
    component('visual-1', 'visual_map', 'image', '知识图解', 'Visual map'),
    component(practiceId, 'guided_practice', 'assessment', '引导练习', 'Guided practice'),
    component(
      'calibration-1',
      'calibration_checkpoint',
      'deterministic',
      '校准复盘',
      'Calibration checkpoint',
      [practiceId]
    ),
    component('retrieval-1', 'retrieval_card', 'retrieval', '主动回忆', 'Active recall'),
  ]
  const plan = {
    plan_id: 'plan-components',
    pack_id: 'pack-components',
    version: 1,
    goal: 'Understand derivatives',
    support_state_snapshot: { source: 'default', dimensions: {} },
    components,
    status: 'active',
    created_at: '2026-08-14T00:00:00+00:00',
    updated_at: '2026-08-14T00:00:00+00:00',
  }
  const pack = {
    pack_id: 'pack-components',
    title: 'Derivatives',
    goal: { text: plan.goal, status: 'active' },
    materials: [{ material_id: 'source-1', source_type: 'paste', text: 'Derivative source.' }],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    component_plans: [plan],
    active_plan_id: plan.plan_id,
    due_review_count: 0,
    repairs: [],
    created_at: '2026-08-14T00:00:00+00:00',
    updated_at: '2026-08-14T00:00:00+00:00',
  }
  let generationRequests = 0

  await page.addInitScript(() => {
    window.localStorage.setItem('traittutor:onboarding-profile-dismissed', 'true')
  })
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = request.url()
    if (url.endsWith('/auth/status')) {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: 'smoke' } })
      return
    }
    if (url.endsWith('/settings')) {
      await route.fulfill({ json: { catalog: {} } })
      return
    }
    if (url.endsWith('/research/workspaces')) {
      await route.fulfill({ json: [] })
      return
    }
    if (url.endsWith('/learning-packs/pack-components')) {
      await route.fulfill({ json: pack })
      return
    }
    if (url.includes('/plans/plan-components/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return
    }
    if (url.endsWith('/traittutor/generate/tasks') && request.method() === 'POST') {
      generationRequests += 1
      await route.fulfill({ status: 500, json: { detail: 'Generation must not run' } })
      return
    }
    if (url.includes('/sessions')) {
      await route.fulfill({ json: { sessions: [] } })
      return
    }
    if (url.includes('/learner/overview')) {
      await route.fulfill({ json: { subjects: [] } })
      return
    }
    await route.fulfill({ status: 200, json: {} })
  })

  await page.goto('/learning/pack-components')

  for (const name of [
    /^01 (?:目标地图|Goal map)$/,
    /^02 (?:核心概念讲解|Concept explanation)$/,
    /^03 (?:起点诊断|Diagnostic check)$/,
    /^04 (?:知识图解|Visual map)$/,
    /^05 (?:引导练习|Guided practice)$/,
    /^07 (?:主动回忆|Active recall)$/,
  ]) {
    await expect(page.getByRole('button', { name })).toBeEnabled()
  }
  await expect(
    page.getByRole('button', { name: /^06 (?:校准复盘|Calibration checkpoint)$/ })
  ).toBeDisabled()

  await page.getByRole('button', { name: /^03 (?:起点诊断|Diagnostic check)$/ }).click()
  await expect(page.getByRole('heading', { name: /起点诊断|Diagnostic check/ })).toBeVisible()
  await page.getByRole('button', { name: /^07 (?:主动回忆|Active recall)$/ }).click()
  await expect(page.getByRole('heading', { name: /主动回忆|Active recall/ })).toBeVisible()
  expect(generationRequests).toBe(0)
})

test('review queue reads, reveals, and records the canonical due item without generating cards', async ({
  page,
}) => {
  const component = {
    component_id: 'review-1',
    component_type: 'review_queue',
    executor: 'retrieval',
    label_zh: '待复习队列',
    label_en: 'Review queue',
    concept_refs: ['concept-1'],
    support_dimensions: [],
    bkt_stage: 'developing',
    modality: 'interactive',
    dependencies: [],
    required: true,
    reason: 'Review the canonical due item.',
    evidence_refs: [],
    completion_event: 'flashcard_review',
    status: 'active',
  }
  const plan = {
    plan_id: 'plan-review',
    pack_id: 'pack-review',
    version: 1,
    goal: 'Retain the concept',
    support_state_snapshot: { source: 'default', dimensions: {} },
    components: [component],
    status: 'active',
    created_at: '2026-08-14T00:00:00+00:00',
    updated_at: '2026-08-14T00:00:00+00:00',
  }
  const pack = {
    pack_id: 'pack-review',
    title: 'Review',
    goal: { text: plan.goal, status: 'active' },
    materials: [{ material_id: 'source-1', source_type: 'paste', text: 'Source.' }],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    component_plans: [plan],
    active_plan_id: plan.plan_id,
    due_review_count: 1,
    repairs: [],
    created_at: '2026-08-14T00:00:00+00:00',
    updated_at: '2026-08-14T00:00:00+00:00',
  }
  let generationRequests = 0
  const submitted: Array<Record<string, unknown>> = []
  const componentEvents: Array<Record<string, unknown>> = []

  await page.addInitScript(() => {
    window.localStorage.setItem('traittutor:onboarding-profile-dismissed', 'true')
  })
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = request.url()
    if (url.endsWith('/auth/status')) {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: 'smoke' } })
      return
    }
    if (url.endsWith('/settings')) {
      await route.fulfill({ json: { catalog: {} } })
      return
    }
    if (url.endsWith('/research/workspaces')) {
      await route.fulfill({ json: [] })
      return
    }
    if (url.endsWith('/learning-packs/pack-review')) {
      await route.fulfill({ json: pack })
      return
    }
    if (url.includes('/plans/plan-review/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return
    }
    if (url.endsWith('/learning-packs/pack-review/reviews/due')) {
      await route.fulfill({
        json: {
          items: [
            {
              review_id: 'due-1',
              pack_id: 'pack-review',
              concept_id: 'concept-1',
              knowledge_type: 'concept',
              source: 'retrieval',
              due_at: '2020-01-01T00:00:00+00:00',
              priority: 1,
              interval_index: 0,
              consecutive_correct: 0,
              consecutive_wrong: 0,
              prompt: 'What is the rule?',
            },
          ],
          total: 1,
          estimated_minutes: 1,
        },
      })
      return
    }
    if (url.endsWith('/learning-packs/pack-review/reviews/due-1/reveal')) {
      await route.fulfill({ json: { review_id: 'due-1', answer: 'Server-held answer' } })
      return
    }
    if (url.endsWith('/learning-packs/pack-review/reviews/due-1/result')) {
      submitted.push(request.postDataJSON())
      await route.fulfill({
        json: {
          accepted: true,
          verified: false,
          correct: null,
          review: { due_at: '2026-08-15T00:00:00+00:00' },
          next_review_at: '2026-08-15T00:00:00+00:00',
        },
      })
      return
    }
    if (url.includes('/components/review-1/events')) {
      componentEvents.push(request.postDataJSON())
      await route.fulfill({
        json: { component: { ...component, status: 'completed' }, learner_state_updated: false },
      })
      return
    }
    if (url.endsWith('/traittutor/generate/tasks') && request.method() === 'POST') {
      generationRequests += 1
      await route.fulfill({ status: 500, json: { detail: 'Generation must not run' } })
      return
    }
    if (url.includes('/sessions')) {
      await route.fulfill({ json: { sessions: [] } })
      return
    }
    if (url.includes('/learner/overview')) {
      await route.fulfill({ json: { subjects: [] } })
      return
    }
    await route.fulfill({ status: 200, json: {} })
  })

  await page.goto('/learning/pack-review')
  await expect(page.getByText('What is the rule?')).toBeVisible()
  await expect(page.getByText('Server-held answer')).toHaveCount(0)
  await page.getByRole('button', { name: /翻面核对|Reveal answer/ }).click()
  await expect(page.getByText('Server-held answer')).toBeVisible()
  await page.getByRole('button', { name: /记得清楚|Known/ }).click()
  await expect(page.getByRole('heading', { name: /当前没有到期复习|Nothing is due/ })).toBeVisible()

  expect(submitted).toHaveLength(1)
  expect(submitted[0].rating).toBe('known')
  expect(componentEvents.some(event => event.action === 'complete')).toBe(true)
  expect(generationRequests).toBe(0)
})

test('guided practice after a completed lesson sends no stale analysis id', async ({
  page,
}) => {
  const component = (
    componentId: string,
    componentType: string,
    executor: string,
    labelZh: string,
    labelEn: string,
    extra: Record<string, unknown> = {}
  ) => ({
    component_id: componentId,
    component_type: componentType,
    executor,
    label_zh: labelZh,
    label_en: labelEn,
    concept_refs: [],
    support_dimensions: [],
    bkt_stage: 'unobserved',
    modality: executor === 'assessment' ? 'interactive' : 'text',
    dependencies: [],
    required: true,
    reason: 'Regression.',
    evidence_refs: [],
    completion_event: 'complete',
    ...extra,
  })
  const practiceId = 'practice-1'
  const components = [
    component('goal-1', 'goal_map', 'lesson', '目标地图', 'Goal map', { status: 'completed' }),
    component('concept-1', 'concept_explanation', 'lesson', '核心概念讲解', 'Concept explanation', {
      status: 'completed',
      output_ref: 'gen-concept-1',
    }),
    component(practiceId, 'guided_practice', 'assessment', '引导练习', 'Guided practice', {
      status: 'pending',
    }),
  ]
  const plan = {
    plan_id: 'plan-lesson-quiz',
    pack_id: 'pack-lesson-quiz',
    version: 1,
    goal: 'Understand derivatives',
    support_state_snapshot: { source: 'default', dimensions: {} },
    components,
    status: 'active',
    created_at: '2026-08-14T00:00:00+00:00',
    updated_at: '2026-08-14T00:00:00+00:00',
  }
  const pack = {
    pack_id: 'pack-lesson-quiz',
    title: 'Derivatives',
    goal: { text: plan.goal, status: 'active' },
    materials: [
      {
        material_id: 'source-1',
        source_type: 'paste',
        text: 'Derivative source.',
        metadata: {
          learning_session_id: 'session-1',
          learner_analyses: [{ analysis_id: 'analysis-1' }],
        },
      },
    ],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    component_plans: [plan],
    active_plan_id: plan.plan_id,
    due_review_count: 0,
    repairs: [],
    created_at: '2026-08-14T00:00:00+00:00',
    updated_at: '2026-08-14T00:00:00+00:00',
  }
  const generationBodies: Array<Record<string, unknown>> = []

  await page.addInitScript(() => {
    window.localStorage.setItem('traittutor:onboarding-profile-dismissed', 'true')
  })
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = request.url()
    if (url.endsWith('/auth/status')) {
      await route.fulfill({ json: { enabled: false, authenticated: true, username: 'smoke' } })
      return
    }
    if (url.endsWith('/settings')) {
      await route.fulfill({ json: { catalog: {} } })
      return
    }
    if (url.endsWith('/research/workspaces')) {
      await route.fulfill({ json: [] })
      return
    }
    if (url.endsWith('/learning-packs/pack-lesson-quiz')) {
      await route.fulfill({ json: pack })
      return
    }
    if (url.includes('/plans/plan-lesson-quiz/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return
    }
    if (url.includes('/traittutor/generate/tasks/gen-concept-1')) {
      // The completed concept explanation rehydrates from its durable output.
      await route.fulfill({
        json: {
          generation_id: 'gen-concept-1',
          generation_type: 'courseware',
          status: 'completed',
          result: {
            kind: 'courseware',
            title: 'Concept',
            sections: [],
            markdown: 'Derivatives measure how a function changes as its input changes.',
          },
        },
      })
      return
    }
    if (url.endsWith('/traittutor/generate/tasks') && request.method() === 'POST') {
      generationBodies.push(request.postDataJSON())
      await route.fulfill({ status: 500, json: { detail: 'captured' } })
      return
    }
    if (url.includes(`/components/${practiceId}/events`)) {
      await route.fulfill({
        json: {
          component: { ...components[2], status: 'active' },
          learner_state_updated: false,
        },
      })
      return
    }
    if (url.includes('/sessions')) {
      await route.fulfill({ json: { sessions: [] } })
      return
    }
    if (url.includes('/learner/overview')) {
      await route.fulfill({ json: { subjects: [] } })
      return
    }
    await route.fulfill({ status: 200, json: {} })
  })

  await page.goto('/learning/pack-lesson-quiz')
  await page.getByRole('button', { name: /^03 (?:引导练习|Guided practice)$/ }).click()
  await page.getByRole('button', { name: /开始学习/ }).first().click()
  await expect
    .poll(() => generationBodies.length, { timeout: 15_000 })
    .toBeGreaterThanOrEqual(1)

  const body = generationBodies[0]
  const options = (body?.options ?? {}) as Record<string, unknown>
  const material = (body?.material ?? {}) as Record<string, unknown>
  const metadata = (material?.metadata ?? {}) as Record<string, unknown>

  // The pack material still carries the raw-PDF analysis record, but the
  // quiz is grounded in the generated lesson — the stale id must not ride
  // along or the server rejects it as "not this material".
  expect(options.analysis_id).toBeUndefined()
  expect(options.session_id).toBeUndefined()
  expect(material.source_type).toBe('paste')
  expect(metadata.source_kind).toBe('generated_lesson')
})
