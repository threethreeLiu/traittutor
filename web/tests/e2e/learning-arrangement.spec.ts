import { expect, test, type Page, type Route } from '@playwright/test'

const createdAt = '2026-08-14T00:00:00+00:00'

type ComponentType = 'goal_map' | 'diagnostic_check' | 'concept_explanation' | 'visual_map'

function component(
  componentId: string,
  componentType: ComponentType,
  labelZh: string,
  labelEn: string,
  reason: string
) {
  return {
    component_id: componentId,
    component_type: componentType,
    executor: componentType === 'diagnostic_check' ? 'quiz' : 'lesson',
    label_zh: labelZh,
    label_en: labelEn,
    concept_refs: ['kc-1'],
    support_dimensions: componentType === 'goal_map' ? ['goal_planning'] : [],
    bkt_stage: 'unobserved',
    modality: 'text',
    dependencies: [],
    required: true,
    reason,
    evidence_refs: [],
    completion_event: 'complete',
    status: 'pending',
    output_ref: null as string | null,
  }
}

function plan(arrangement: 'pending' | 'llm' | 'deterministic_fallback', version = 1) {
  const llm = arrangement === 'llm'
  return {
    plan_id: `plan-${arrangement}-${version}`,
    pack_id: 'pack-arrangement',
    version,
    goal: 'Understand fractions',
    subject_ref: { subject_id: 'math', label: 'Mathematics', confidence: 0.9 },
    support_state_snapshot: { source: 'default', dimensions: {}, boundary: 'Teaching support.' },
    components: llm
      ? [
          component(
            `goal-${version}`,
            'goal_map',
            'LLM 推荐目标地图',
            'LLM goal map',
            '先展示推荐路径。'
          ),
          component(
            `visual-${version}`,
            'visual_map',
            'LLM 推荐概念图',
            'LLM visual map',
            '再用关系图连接分数概念。'
          ),
        ]
      : [
          component(
            `diagnostic-${version}`,
            'diagnostic_check',
            '初始确定性诊断',
            'Initial deterministic diagnostic',
            '基础计划的起点占位。'
          ),
          component(
            `concept-${version}`,
            'concept_explanation',
            '初始概念讲解',
            'Initial concept explanation',
            '基础计划的概念讲解。'
          ),
        ],
    status: 'active',
    arrangement,
    arrangement_rationale: llm ? 'LLM 建议先看目标，再连接分数概念。' : null,
    created_at: createdAt,
    updated_at: createdAt,
    start_url: '/learning/pack-arrangement',
  }
}

function pack(activePlan: ReturnType<typeof plan>) {
  return {
    schema_version: 2,
    pack_id: 'pack-arrangement',
    title: 'Fractions',
    goal: { text: activePlan.goal, status: 'active' },
    material: {
      material_id: 'source-1',
      content_hash: 'hash-1',
      source_type: 'upload',
      title: 'fractions.txt',
      text: 'One half is represented as 1/2.',
      metadata: {},
    },
    materials: [
      {
        material_id: 'source-1',
        content_hash: 'hash-1',
        source_type: 'upload',
        title: 'fractions.txt',
        text: 'One half is represented as 1/2.',
      },
    ],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    flashcard_progress: {},
    quiz_attempts: [],
    component_plans: [activePlan],
    active_plan_id: activePlan.plan_id,
    component_progress: {},
    arrangement_preference: null as null | 'auto' | 'basic',
    pre_assessment: null as null | Record<string, unknown>,
    due_review_count: 0,
    repairs: [],
    created_at: createdAt,
    updated_at: createdAt,
  }
}

async function routeShell(page: Page, handler: (route: Route) => Promise<boolean>) {
  await page.addInitScript(() => {
    window.localStorage.setItem('traittutor:onboarding-profile-dismissed', 'true')
  })
  await page.route('**/api/v1/**', async route => {
    if (await handler(route)) return
    const url = route.request().url()
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
}

async function fulfillHomeUpload(
  route: Route,
  currentPack: ReturnType<typeof pack>,
  initialPlan: ReturnType<typeof plan>,
  mutations: string[]
): Promise<boolean> {
  const request = route.request()
  const path = new URL(request.url()).pathname
  if (path.endsWith('/traittutor/generate/materials/prepare')) {
    await route.fulfill({
      json: {
        source_type: 'upload',
        source_id: 'source-1',
        title: 'fractions.txt',
        text: 'One half is represented as 1/2.',
        metadata: { filename: 'fractions.txt', mime_type: 'text/plain' },
      },
    })
    return true
  }
  if (path.endsWith('/learning/intent')) {
    await route.fulfill({
      json: {
        mode: 'learning_path',
        confidence: 0.9,
        rationale: 'Safe learning material.',
        fallback_required: false,
        safety_action: 'allow',
      },
    })
    return true
  }
  if (path.endsWith('/sessions') && request.method() === 'POST') {
    await route.fulfill({
      json: {
        session: {
          id: 'arrangement-session',
          session_id: 'arrangement-session',
          title: 'Fractions',
          messages: [],
        },
      },
    })
    return true
  }
  if (path.endsWith('/traittutor/generate/materials/analyze')) {
    await route.fulfill({
      json: {
        analysis_id: 'analysis-1',
        title: 'Fractions',
        subject: 'Mathematics',
        summary: 'Fraction basics.',
        concepts: ['Fractions'],
        learning_objectives: ['Understand one half'],
      },
    })
    return true
  }
  if (path.endsWith('/learning-packs/with-plan') && request.method() === 'POST') {
    mutations.push('with-plan')
    await route.fulfill({ json: { pack: currentPack, plan: initialPlan } })
    return true
  }
  if (path === '/api/v1/assistant/route') {
    mutations.push('assistant-route')
    await route.fulfill({ status: 500, json: { detail: 'Learn must not call Assistant routing' } })
    return true
  }
  if (path === '/api/v1/learning-packs' && request.method() === 'POST') {
    mutations.push('legacy-pack-create')
    await route.fulfill({ status: 500, json: { detail: 'Legacy create must not be used' } })
    return true
  }
  if (/\/learning-packs\/[^/]+\/plans$/.test(path) && request.method() === 'POST') {
    mutations.push('legacy-plan-create')
    await route.fulfill({ status: 500, json: { detail: 'Legacy plan create must not be used' } })
    return true
  }
  return false
}

async function uploadAndBuildPath(page: Page) {
  await page.goto('/home')
  await page.locator('input[type=file]').setInputFiles({
    name: 'fractions.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('One half is represented as 1/2.'),
  })
  await page.getByRole('button', { name: /建立学习路径|Build learning path/ }).click()
  await expect(page).toHaveURL(/\/home(?:\/arrangement-session)?$/)
  await expect(page.getByTestId('learning-path-launch')).toBeVisible()
}

test('upload stays on the intermediate page, answers a probe, previews the LLM plan, then enters the canvas', async ({
  page,
}) => {
  const initialPlan = plan('pending')
  const currentPack = pack(initialPlan)
  const mutations: string[] = []

  await routeShell(page, async route => {
    if (await fulfillHomeUpload(route, currentPack, initialPlan, mutations)) return true
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/pre-assessment') && request.method() === 'POST') {
      await new Promise(resolve => setTimeout(resolve, 150))
      currentPack.pre_assessment = {
        assessment_id: 'pre-1',
        status: 'pending',
        questions: [
          {
            question_id: 'q1',
            concept_id: 'kc-1',
            concept_label: 'Fractions',
            question: 'Which value is one half?',
            options: ['1/3', '1/2', '2/3'],
          },
        ],
      }
      await route.fulfill({
        json: {
          needed: true,
          assessment_id: 'pre-1',
          status: 'pending',
          questions: [
            {
              question_id: 'q1',
              concept_id: 'kc-1',
              concept_label: 'Fractions',
              question: 'Which value is one half?',
              options: ['1/3', '1/2', '2/3'],
              confidence_scale: ['低', '中', '高'],
            },
          ],
        },
      })
      return true
    }
    if (path.endsWith('/pre-assessment/pre-1/submit')) {
      currentPack.pre_assessment = { ...currentPack.pre_assessment, status: 'answered' }
      await route.fulfill({
        json: {
          assessment_id: 'pre-1',
          idempotent_replay: false,
          results: [
            {
              question_id: 'q1',
              correct: true,
              confidence: '中',
              rationale: 'One divided by two is one half.',
            },
          ],
        },
      })
      return true
    }
    if (path.endsWith('/plans/arrange')) {
      await new Promise(resolve => setTimeout(resolve, 150))
      const arranged = plan('llm', 2)
      currentPack.component_plans = [arranged]
      currentPack.active_plan_id = arranged.plan_id
      currentPack.pre_assessment = { ...currentPack.pre_assessment, status: 'consumed' }
      await route.fulfill({ json: { ...arranged, fallback: false, idempotent_replay: false } })
      return true
    }
    if (path === '/api/v1/learning-packs/pack-arrangement') {
      await route.fulfill({ json: currentPack })
      return true
    }
    if (path.includes('/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return true
    }
    return false
  })

  await uploadAndBuildPath(page)
  // The first intermediate-page step asks whether the LLM may auto-select
  // the components; the judge → arrange pipeline must not run before the
  // learner chooses.
  await expect(page.getByTestId('personalization-choice-card')).toBeVisible()
  await expect(page.getByTestId('arrangement-judging')).toHaveCount(0)
  await page.getByTestId('personalization-choice-auto').click()
  await expect(page.getByTestId('arrangement-judging')).toBeVisible()
  // While judging whether probes are needed and while answering them, the
  // component area must stay hidden: components are not ready yet.
  await expect(page.getByTestId('learning-path-components-skeleton')).toHaveCount(0)
  await expect(page.getByText('初始确定性诊断')).toHaveCount(0)
  await expect(page.getByTestId('pre-assessment-card')).toBeVisible()
  await page.getByLabel('1/2').check()
  // Confidence was removed from the pre-assessment interaction (it is no
  // longer collected or displayed), so submit directly after answering.
  await page.getByRole('button', { name: /提交并查看结果|Submit and view results/ }).click()
  await expect(page.getByText('One divided by two is one half.')).toBeVisible()
  await page.getByRole('button', { name: /继续生成学习路径|Continue to build the path/ }).click()
  await expect(page.getByTestId('arrangement-arranging')).toBeVisible()
  await expect(page.getByTestId('learning-path-components-llm')).toContainText('LLM 推荐概念图')
  await expect(page.getByTestId('learning-path-components-llm')).toContainText(
    'LLM 建议先看目标，再连接分数概念。'
  )
  await expect(page.getByText('初始确定性诊断')).toHaveCount(0)
  await page.getByRole('button', { name: /开始学习|Start learning/ }).click()
  await expect(page).toHaveURL(/\/learning\/pack-arrangement$/)
  await expect(page.getByTestId('learning-path-panel')).toBeVisible()
  await expect(page.getByTestId('arrangement-judging')).toHaveCount(0)
  await expect(page.getByTestId('pre-assessment-card')).toHaveCount(0)
  expect(mutations).toEqual(['with-plan'])
})

test('opting out of LLM selection uses the basic path without any LLM calls', async ({ page }) => {
  const initialPlan = plan('pending')
  const currentPack = pack(initialPlan)
  const mutations: string[] = []
  let judgeCalls = 0
  let arrangeCalls = 0
  const preferencePatches: string[] = []

  await routeShell(page, async route => {
    if (await fulfillHomeUpload(route, currentPack, initialPlan, mutations)) return true
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/pre-assessment')) {
      judgeCalls += 1
      await route.fulfill({ json: { needed: false } })
      return true
    }
    if (path.endsWith('/plans/arrange')) {
      arrangeCalls += 1
      await route.fulfill({ json: { ...plan('llm', 2), fallback: false } })
      return true
    }
    if (path === '/api/v1/learning-packs/pack-arrangement' && route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON() as { arrangement_preference?: string }
      if (body.arrangement_preference) {
        preferencePatches.push(body.arrangement_preference)
        currentPack.arrangement_preference = body.arrangement_preference as 'auto' | 'basic'
      }
      await route.fulfill({ json: currentPack })
      return true
    }
    if (path === '/api/v1/learning-packs/pack-arrangement') {
      await route.fulfill({ json: currentPack })
      return true
    }
    if (path.includes('/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return true
    }
    return false
  })

  await uploadAndBuildPath(page)
  await expect(page.getByTestId('personalization-choice-card')).toBeVisible()
  await page.getByTestId('personalization-choice-basic').click()
  // The basic path is immediately available: the LLM pipeline never runs.
  await expect(page.getByTestId('personalization-choice-card')).toHaveCount(0)
  await expect(page.getByTestId('arrangement-fallback')).toBeVisible()
  await expect(page.getByTestId('learning-path-components-fallback')).toContainText(
    '初始确定性诊断'
  )
  await expect(page.getByRole('button', { name: /开始学习|Start learning/ })).toBeVisible()
  await page.getByRole('button', { name: /开始学习|Start learning/ }).click()
  await expect(page).toHaveURL(/\/learning\/pack-arrangement$/)
  // A deliberate opt-out is not a failure: no "尚未完成智能排列" banner and
  // no retry affordance may appear on the canvas.
  await expect(page.getByTestId('arrangement-canvas-notice')).toHaveCount(0)
  expect(preferencePatches).toEqual(['basic'])
  expect(judgeCalls).toBe(0)
  expect(arrangeCalls).toBe(0)
  expect(mutations).toEqual(['with-plan'])
})

test('no pre-assessment needed goes straight to the LLM recommendation without showing v1', async ({
  page,
}) => {
  const initialPlan = plan('pending')
  const currentPack = pack(initialPlan)
  const mutations: string[] = []

  await routeShell(page, async route => {
    if (await fulfillHomeUpload(route, currentPack, initialPlan, mutations)) return true
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/pre-assessment')) {
      currentPack.pre_assessment = { status: 'not_needed' }
      await route.fulfill({ json: { needed: false } })
      return true
    }
    if (path.endsWith('/plans/arrange')) {
      await new Promise(resolve => setTimeout(resolve, 250))
      const arranged = plan('llm', 2)
      currentPack.component_plans = [arranged]
      currentPack.active_plan_id = arranged.plan_id
      await route.fulfill({ json: { ...arranged, fallback: false, idempotent_replay: false } })
      return true
    }
    return false
  })

  await uploadAndBuildPath(page)
  await expect(page.getByTestId('personalization-choice-card')).toBeVisible()
  await page.getByTestId('personalization-choice-auto').click()
  await expect(page.getByTestId('arrangement-arranging')).toBeVisible()
  await expect(page.getByTestId('pre-assessment-card')).toHaveCount(0)
  await expect(page.getByTestId('learning-path-components-skeleton')).toBeVisible()
  await expect(page.getByText('初始确定性诊断')).toHaveCount(0)
  await expect(page.getByTestId('learning-path-components-llm')).toContainText('LLM 推荐目标地图')
  await expect(page.getByTestId('learning-path-components-llm')).toContainText('LLM 推荐概念图')
  await expect(page.getByText('初始确定性诊断')).toHaveCount(0)
  expect(mutations).toEqual(['with-plan'])
})

test('arrangement fallback can retry, accept the basic path, and start without a dead end', async ({
  page,
}) => {
  const initialPlan = plan('pending')
  const currentPack = pack(initialPlan)
  const mutations: string[] = []
  let arrangeCalls = 0

  await routeShell(page, async route => {
    if (await fulfillHomeUpload(route, currentPack, initialPlan, mutations)) return true
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/pre-assessment')) {
      currentPack.pre_assessment = { status: 'not_needed' }
      await route.fulfill({ json: { needed: false } })
      return true
    }
    if (path.endsWith('/plans/arrange')) {
      arrangeCalls += 1
      const fallback = plan('deterministic_fallback', 1)
      currentPack.component_plans = [fallback]
      currentPack.active_plan_id = fallback.plan_id
      await route.fulfill({
        json: {
          ...fallback,
          fallback: true,
          fallback_message: 'Use the deterministic plan.',
          idempotent_replay: false,
        },
      })
      return true
    }
    if (path === '/api/v1/learning-packs/pack-arrangement') {
      await route.fulfill({ json: currentPack })
      return true
    }
    if (path.includes('/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return true
    }
    return false
  })

  await uploadAndBuildPath(page)
  await expect(page.getByTestId('personalization-choice-card')).toBeVisible()
  await page.getByTestId('personalization-choice-auto').click()
  await expect(page.getByTestId('arrangement-fallback')).toBeVisible()
  await expect(page.getByTestId('learning-path-components-fallback')).toContainText(
    '初始确定性诊断'
  )
  await page.getByRole('button', { name: /^重试$|^Retry$/ }).click()
  await expect.poll(() => arrangeCalls).toBe(2)
  await expect(page.getByTestId('arrangement-fallback')).toBeVisible()
  await page.getByRole('button', { name: /直接使用基础路径|Use the basic path/ }).click()
  await expect(page.getByRole('button', { name: /开始学习|Start learning/ })).toBeVisible()
  await page.getByRole('button', { name: /开始学习|Start learning/ }).click()
  await expect(page).toHaveURL(/\/learning\/pack-arrangement$/)
  await expect(page.getByTestId('arrangement-canvas-notice')).toBeVisible()
  await expect(page.getByTestId('arrangement-judging')).toHaveCount(0)
  expect(mutations).toEqual(['with-plan'])
})

test('canvas never auto-runs arrangement and offers only an explicit manual retry', async ({
  page,
}) => {
  const pendingPlan = plan('pending')
  const currentPack = pack(pendingPlan)
  let judgeCalls = 0
  let arrangeCalls = 0

  await routeShell(page, async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/learning-packs/pack-arrangement') {
      await route.fulfill({ json: currentPack })
      return true
    }
    if (path.includes('/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return true
    }
    if (path.endsWith('/pre-assessment')) {
      judgeCalls += 1
      await route.fulfill({ json: { needed: false } })
      return true
    }
    if (path.endsWith('/plans/arrange')) {
      arrangeCalls += 1
      const arranged = plan('llm', 2)
      currentPack.component_plans = [arranged]
      currentPack.active_plan_id = arranged.plan_id
      await route.fulfill({ json: { ...arranged, fallback: false, idempotent_replay: false } })
      return true
    }
    return false
  })

  await page.goto('/learning/pack-arrangement')
  await expect(page.getByTestId('learning-path-panel')).toBeVisible()
  await expect(page.getByTestId('arrangement-canvas-notice')).toBeVisible()
  await expect(page.getByTestId('arrangement-judging')).toHaveCount(0)
  await expect(page.getByTestId('pre-assessment-card')).toHaveCount(0)
  expect(judgeCalls).toBe(0)
  expect(arrangeCalls).toBe(0)
  await page.getByRole('button', { name: /重试排列|Retry arrangement/ }).click()
  await expect.poll(() => arrangeCalls).toBe(1)
  await expect(page.getByTestId('arrangement-canvas-notice')).toHaveCount(0)
  expect(judgeCalls).toBe(0)
})

test('arranged components still start independent generation requests', async ({ page }) => {
  const activePlan = plan('llm', 2)
  const currentPack = pack(activePlan)
  const generatedComponentIds: string[] = []

  await routeShell(page, async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/learning-packs/pack-arrangement' && request.method() === 'GET') {
      await route.fulfill({ json: currentPack })
      return true
    }
    if (path.includes('/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return true
    }
    if (path.includes('/components/') && path.endsWith('/events')) {
      const componentId = path.split('/components/')[1].split('/')[0]
      const body = request.postDataJSON() as { action: string; output_ref?: string }
      const target = activePlan.components.find(item => item.component_id === componentId)!
      target.status = 'active'
      if (body.output_ref) target.output_ref = body.output_ref
      await route.fulfill({
        json: {
          component: target,
          event: body,
          plan_status: 'active',
          replanned_plan: null,
          calibration: null,
        },
      })
      return true
    }
    if (path.endsWith('/traittutor/generate/tasks') && request.method() === 'POST') {
      const body = request.postDataJSON() as {
        options: { learning_component: { component_id: string } }
      }
      generatedComponentIds.push(body.options.learning_component.component_id)
      await route.fulfill({
        json: {
          generation_id: `generation-${generatedComponentIds.length}`,
          status: 'queued',
          events_url: '/unused',
        },
      })
      return true
    }
    if (path.includes('/traittutor/generate/tasks/generation-') && request.method() === 'GET') {
      const generationId = path.split('/').at(-1)!
      await route.fulfill({
        json: {
          generation_id: generationId,
          status: 'completed',
          result: { kind: 'courseware', title: 'Generated component', markdown: 'Ready.' },
          events: [],
          created_at: createdAt,
          updated_at: createdAt,
        },
      })
      return true
    }
    if (path === '/api/v1/learning-packs/pack-arrangement' && request.method() === 'PATCH') {
      await route.fulfill({ json: currentPack })
      return true
    }
    return false
  })

  await page.goto('/learning/pack-arrangement')
  await page.getByRole('button', { name: /^01 (?:LLM 推荐目标地图|LLM goal map)$/ }).click()
  await page.getByRole('button', { name: /开始学习|Start learning/ }).click()
  await expect.poll(() => generatedComponentIds.length).toBe(1)
  await page.getByRole('button', { name: /^02 (?:LLM 推荐概念图|LLM visual map)$/ }).click()
  await page.getByRole('button', { name: /开始学习|Start learning/ }).click()
  await expect.poll(() => generatedComponentIds.length).toBe(2)
  expect(generatedComponentIds).toEqual(['goal-2', 'visual-2'])
})

test('goal_map completes on open without advancing to the next component', async ({ page }) => {
  const activePlan = plan('llm', 2)
  const currentPack = pack(activePlan)
  const recordedEvents: Array<{ action: string; output_ref?: string }> = []

  await routeShell(page, async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/learning-packs/pack-arrangement' && request.method() === 'GET') {
      await route.fulfill({ json: currentPack })
      return true
    }
    if (path.includes('/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return true
    }
    if (path.includes('/components/') && path.endsWith('/events')) {
      const componentId = path.split('/components/')[1].split('/')[0]
      const body = request.postDataJSON() as { action: string; output_ref?: string }
      recordedEvents.push(body)
      const target = activePlan.components.find(item => item.component_id === componentId)!
      target.status = body.action === 'complete' ? 'completed' : 'active'
      if (body.output_ref) target.output_ref = body.output_ref
      await route.fulfill({
        json: {
          component: target,
          event: body,
          plan_status: 'active',
          replanned_plan: null,
          calibration: null,
        },
      })
      return true
    }
    if (path.endsWith('/traittutor/generate/tasks') && request.method() === 'POST') {
      await route.fulfill({
        json: {
          generation_id: 'generation-goal',
          status: 'queued',
          events_url: '/unused',
        },
      })
      return true
    }
    if (path.includes('/traittutor/generate/tasks/generation-goal') && request.method() === 'GET') {
      await route.fulfill({
        json: {
          generation_id: 'generation-goal',
          status: 'completed',
          events: [],
          result: {
            kind: 'courseware',
            title: 'Understand fractions',
            markdown: '',
            sections: [],
            orchestration: { status: 'succeeded', agents: [] },
          },
          learner_profile: {},
          page_schema_id: 'generation-goal:page',
          page_schema: {
            page_schema_id: 'generation-goal:page',
            generation_run_id: 'generation-goal',
            version: 'v1',
            published: false,
            created_at: createdAt,
            regions: [
              {
                region_id: 'region-goal',
                component: {
                  instance_id: 'srl-1',
                  component_type: 'goal_map',
                  version: 'v1',
                  props: {
                    title: 'Understand fractions',
                    milestones: ['Milestone one', 'Milestone two'],
                  },
                  modality_hint: 'text',
                },
              },
            ],
          },
          created_at: createdAt,
          updated_at: createdAt,
        },
      })
      return true
    }
    if (path === '/api/v1/learning-packs/pack-arrangement' && request.method() === 'PATCH') {
      await route.fulfill({ json: currentPack })
      return true
    }
    return false
  })

  await page.goto('/learning/pack-arrangement')
  await page.getByRole('button', { name: /^01 (?:LLM 推荐目标地图|LLM goal map)$/ }).click()
  await page.getByRole('button', { name: /开始学习|Start learning/ }).click()

  // The goal map renders and auto-completes (start → feedback → complete)…
  await expect(page.getByText('Milestone one')).toBeVisible()
  await expect.poll(() => recordedEvents.map(event => event.action)).toEqual([
    'start',
    'feedback',
    'complete',
  ])
  // …but the learner stays on the goal map: it must not auto-advance to the
  // visual map (which has no output yet and would blank the main area).
  await expect(page.getByText('Milestone two')).toBeVisible()
  await expect(page.getByRole('button', { name: /^02 (?:LLM 推荐概念图|LLM visual map)$/ })).toBeVisible()
})
