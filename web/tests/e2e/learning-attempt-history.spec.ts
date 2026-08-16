import { expect, test } from '@playwright/test'

test('completed Pack assessment restores a read-only answer and ends naturally', async ({
  page,
}) => {
  const component = {
    component_id: 'assessment-history',
    component_type: 'guided_practice',
    executor: 'assessment',
    label_zh: '历史练习',
    label_en: 'Historical practice',
    concept_refs: ['fractions'],
    support_dimensions: [],
    bkt_stage: 'learning',
    modality: 'interactive',
    dependencies: [],
    required: true,
    reason: 'Verify fraction understanding.',
    evidence_refs: ['source:fractions'],
    completion_event: 'complete',
    status: 'completed',
    output_ref: 'quiz-history',
  }
  const plan = {
    plan_id: 'plan-history',
    pack_id: 'pack-history',
    version: 1,
    goal: 'Understand fractions',
    subject_ref: { subject_id: 'math', label: 'Mathematics' },
    support_state_snapshot: {
      subject_id: 'math',
      source: 'default',
      dimensions: {},
      boundary: 'Support changes presentation only.',
    },
    components: [component],
    status: 'completed',
    created_at: '2026-08-13T08:00:00+00:00',
    updated_at: '2026-08-13T08:05:00+00:00',
  }
  const pack = {
    pack_id: 'pack-history',
    title: 'Fractions',
    goal: { text: 'Understand fractions', status: 'active' },
    materials: [
      {
        material_id: 'material-fractions',
        source_type: 'paste',
        title: 'Fractions',
        text: 'Fraction source',
      },
    ],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    flashcard_progress: {},
    quiz_attempts: [],
    component_plans: [plan],
    active_plan_id: plan.plan_id,
    due_review_count: 0,
    repairs: [],
    created_at: '2026-08-13T08:00:00+00:00',
    updated_at: '2026-08-13T08:05:00+00:00',
  }
  const generation = {
    generation_id: 'quiz-history',
    generation_type: 'quiz',
    status: 'completed',
    events: [],
    result: {
      kind: 'quiz',
      title: 'Fraction check',
      items: [
        {
          question_id: 'question-fraction-1',
          node_id: 'fractions',
          question: 'Which fraction equals one half?',
          question_type: 'choice',
          options: [
            { key: 'A', text: '1/3' },
            { key: 'B', text: '2/4' },
          ],
        },
      ],
      save_target: 'notebook',
    },
    learner_profile: {},
  }
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
    if (url.endsWith('/learning-packs/pack-history')) {
      await route.fulfill({ json: pack })
      return
    }
    if (url.includes('/plans/plan-history/attempts')) {
      await route.fulfill({
        json: {
          items: [
            {
              attempt_id: 'attempt-fraction-1',
              component_id: component.component_id,
              question_id: 'question-fraction-1',
              generated_result_id: 'quiz-history',
              user_answer: 'B',
              confidence: 0.9,
              correct: true,
              reference_answer: 'B',
              explanation: 'Two quarters simplify to one half.',
              submitted_at: '2026-08-13T08:05:00+00:00',
              read_only: true,
              historical_explanation_available: true,
            },
          ],
          total: 1,
          limit: 200,
          offset: 0,
        },
      })
      return
    }
    if (url.endsWith('/traittutor/generate/tasks/quiz-history')) {
      await route.fulfill({ json: generation })
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

  await page.goto('/learning/pack-history')
  await expect(page.getByLabel('2/4')).toBeChecked()
  await expect(page.getByLabel('2/4')).toBeDisabled()
  await expect(page.getByText(/Reference answer: B|参考答案：B/)).toBeVisible()
  await expect(page.getByText('Two quarters simplify to one half.')).toBeVisible()
  await expect(page.getByText(/Read-only historical attempt|只读历史作答/)).toBeVisible()

  await expect(
    page.getByRole('button', { name: /Practice a new similar item|练习一道相似新题/ })
  ).toHaveCount(0)
})

test('partially submitted assessment exposes every remaining server question', async ({ page }) => {
  const component = {
    component_id: 'assessment-partial',
    component_type: 'guided_practice',
    executor: 'assessment',
    label_zh: '未完成练习',
    label_en: 'Partial practice',
    concept_refs: ['concept'],
    support_dimensions: [],
    bkt_stage: 'learning',
    modality: 'interactive',
    dependencies: [],
    required: true,
    reason: 'Complete every generated question.',
    evidence_refs: ['source:concept'],
    completion_event: 'complete',
    status: 'active',
    output_ref: 'quiz-partial',
  }
  const plan = {
    plan_id: 'plan-partial',
    pack_id: 'pack-partial',
    version: 1,
    goal: 'Finish the assessment',
    subject_ref: { subject_id: 'subject', label: 'Subject' },
    support_state_snapshot: {
      subject_id: 'subject',
      source: 'default',
      dimensions: {},
      boundary: 'Support changes presentation only.',
    },
    components: [component],
    status: 'active',
    created_at: '2026-08-14T02:00:00+00:00',
    updated_at: '2026-08-14T02:00:00+00:00',
  }
  const pack = {
    pack_id: 'pack-partial',
    title: 'Partial assessment',
    goal: { text: 'Finish the assessment', status: 'active' },
    materials: [
      {
        material_id: 'material-partial',
        source_type: 'paste',
        title: 'Material',
        text: 'Source material',
      },
    ],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    flashcard_progress: {},
    quiz_attempts: [],
    component_plans: [plan],
    active_plan_id: plan.plan_id,
    due_review_count: 0,
    repairs: [],
    created_at: '2026-08-14T02:00:00+00:00',
    updated_at: '2026-08-14T02:00:00+00:00',
  }
  const items = Array.from({ length: 8 }, (_, index) => ({
    question_id: `question-${index + 1}`,
    node_id: `concept-${index + 1}`,
    node_name: `Concept ${index + 1}`,
    question: `Partial question ${index + 1}`,
    question_type: 'SHORT_ANSWER',
    options: [],
  }))
  const attempts = items.slice(0, 5).map((item, index) => ({
    attempt_id: `attempt-${index + 1}`,
    component_id: component.component_id,
    question_id: item.question_id,
    generated_result_id: 'quiz-partial',
    user_answer: `saved answer ${index + 1}`,
    confidence: 0.35,
    correct: index === 2,
    reference_answer: `reference ${index + 1}`,
    explanation: `feedback ${index + 1}`,
    submitted_at: '2026-08-14T02:05:00+00:00',
    read_only: true,
    historical_explanation_available: true,
  }))
  const submittedEvents: Array<{ action?: string; question_id?: string }> = []

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
    if (url.endsWith('/learning-packs/pack-partial')) {
      await route.fulfill({ json: pack })
      return
    }
    if (url.includes('/plans/plan-partial/attempts')) {
      await route.fulfill({ json: { items: attempts, total: attempts.length, limit: 200, offset: 0 } })
      return
    }
    if (url.endsWith('/traittutor/generate/tasks/quiz-partial')) {
      await route.fulfill({
        json: {
          generation_id: 'quiz-partial',
          generation_type: 'quiz',
          status: 'completed',
          events: [],
          result: { kind: 'quiz', title: 'Eight questions', items, save_target: 'question_bank' },
          learner_profile: {},
        },
      })
      return
    }
    if (url.includes('/components/assessment-partial/events')) {
      const event = request.postDataJSON() as { action?: string; question_id?: string }
      submittedEvents.push(event)
      await route.fulfill({
        json: {
          component: {
            ...component,
            status: event.action === 'complete' ? 'completed' : 'active',
          },
          learner_state_updated: true,
          verified_observation: 'correct',
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

  await page.goto('/learning/pack-partial')
  await expect(page.getByText('Partial question 8')).toBeVisible()
  await expect(page.getByText(/只读历史作答|Read-only historical attempt/).first()).toBeVisible()

  for (const number of [6, 7, 8]) {
    const question = page.getByRole('group', { name: new RegExp(`${number}\\. Partial question`) })
    await question.getByRole('textbox').fill(`new answer ${number}`)
    await question.getByLabel(/把握不大|Not very/).check()
  }
  await page.getByRole('button', { name: /提交答案|Submit answers/ }).click()

  await expect.poll(() => submittedEvents.length).toBe(3)
  expect(submittedEvents.map(event => event.question_id)).toEqual([
    'question-6',
    'question-7',
    'question-8',
  ])
  expect(submittedEvents.at(-1)).toMatchObject({
    action: 'complete',
    question_id: 'question-8',
  })
})

test('retrieval component presents every generated flashcard before completion', async ({ page }) => {
  const component = {
    component_id: 'retrieval-many',
    component_type: 'retrieval_card',
    executor: 'retrieval',
    label_zh: '多张闪卡',
    label_en: 'Multiple flashcards',
    concept_refs: ['concept'],
    support_dimensions: [],
    bkt_stage: 'learning',
    modality: 'interactive',
    dependencies: [],
    required: true,
    reason: 'Practice every generated card.',
    evidence_refs: ['source:concept'],
    completion_event: 'complete',
    status: 'active',
    output_ref: 'flashcards-many',
  }
  const plan = {
    plan_id: 'plan-flashcards',
    pack_id: 'pack-flashcards',
    version: 1,
    goal: 'Review every card',
    subject_ref: { subject_id: 'subject', label: 'Subject' },
    support_state_snapshot: {
      subject_id: 'subject',
      source: 'default',
      dimensions: {},
      boundary: 'Self-ratings do not update mastery.',
    },
    components: [component],
    status: 'active',
    created_at: '2026-08-14T02:00:00+00:00',
    updated_at: '2026-08-14T02:00:00+00:00',
  }
  const pack = {
    pack_id: 'pack-flashcards',
    title: 'Flashcards',
    goal: { text: 'Review every card', status: 'active' },
    materials: [
      {
        material_id: 'material-flashcards',
        source_type: 'paste',
        title: 'Material',
        text: 'Source material',
      },
    ],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    flashcard_progress: {},
    quiz_attempts: [],
    component_plans: [plan],
    active_plan_id: plan.plan_id,
    due_review_count: 0,
    repairs: [],
    created_at: '2026-08-14T02:00:00+00:00',
    updated_at: '2026-08-14T02:00:00+00:00',
  }
  const items = Array.from({ length: 3 }, (_, index) => ({
    node_id: `concept-${index + 1}`,
    node_name: `Concept ${index + 1}`,
    front: `Flashcard front ${index + 1}`,
    back: `Flashcard back ${index + 1}`,
  }))
  const ratings: Array<{ action?: string; observation?: string; question_id?: string }> = []

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
    if (url.endsWith('/learning-packs/pack-flashcards')) {
      await route.fulfill({ json: pack })
      return
    }
    if (url.endsWith('/traittutor/generate/tasks/flashcards-many')) {
      await route.fulfill({
        json: {
          generation_id: 'flashcards-many',
          generation_type: 'flashcards',
          status: 'completed',
          events: [],
          result: { kind: 'flashcards', title: 'Three cards', items, save_target: 'notebook' },
          learner_profile: {},
        },
      })
      return
    }
    const revealMatch = url.match(/\/traittutor\/generate\/tasks\/flashcards-many\/flashcards\/(concept-\d+)\/reveal$/)
    if (revealMatch) {
      const item = items.find(card => card.node_id === revealMatch[1])
      await route.fulfill({ json: { card_id: revealMatch[1], answer: item?.back ?? '' } })
      return
    }
    if (url.includes('/components/retrieval-many/events')) {
      const event = request.postDataJSON() as {
        action?: string
        observation?: string
        question_id?: string
      }
      ratings.push(event)
      await route.fulfill({
        json: {
          component: {
            ...component,
            status: event.action === 'complete' ? 'completed' : 'active',
          },
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

  await page.goto('/learning/pack-flashcards')
  await expect(page.getByText('Flashcard front 1')).toBeVisible()
  await expect(page.getByText('1 / 3')).toBeVisible()

  await page.getByRole('button', { name: /翻面核对|Reveal answer/ }).click()
  await page.getByRole('button', { name: /掌握了|Known/ }).click()
  await expect(page.getByText('Flashcard front 2')).toBeVisible()
  await expect(page.getByText('2 / 3')).toBeVisible()

  await page.getByRole('button', { name: /翻面核对|Reveal answer/ }).click()
  await page.getByRole('button', { name: /有点模糊|Uncertain/ }).click()
  await expect(page.getByText('Flashcard front 3')).toBeVisible()
  await expect(page.getByText('3 / 3')).toBeVisible()

  await page.getByRole('button', { name: /翻面核对|Reveal answer/ }).click()
  await page.getByRole('button', { name: /还不熟|Not yet/ }).click()

  await expect.poll(() => ratings.length).toBe(3)
  expect(ratings.map(item => item.action)).toEqual(['feedback', 'feedback', 'complete'])
  expect(ratings.map(item => item.question_id)).toEqual(['concept-1', 'concept-2', 'concept-3'])
})

test('repair defers after two failures and returns after another assessment', async ({ page }) => {
  const repairComponent = {
    component_id: 'assessment-repair',
    component_type: 'guided_practice',
    executor: 'assessment',
    label_zh: '分数修复',
    label_en: 'Fraction repair',
    concept_refs: ['fractions'],
    support_dimensions: [],
    bkt_stage: 'learning',
    modality: 'interactive',
    dependencies: [],
    required: true,
    reason: 'Repair a fraction misconception.',
    evidence_refs: [],
    completion_event: 'complete',
    status: 'active',
    output_ref: 'quiz-repair',
  }
  const otherComponent = {
    ...repairComponent,
    component_id: 'assessment-other',
    label_zh: '比例练习',
    label_en: 'Ratio practice',
    concept_refs: ['ratios'],
    reason: 'Try a different objective.',
    output_ref: 'quiz-other',
  }
  const plan = {
    plan_id: 'plan-recovery',
    pack_id: 'pack-recovery',
    version: 1,
    goal: 'Learn fractions and ratios',
    subject_ref: { subject_id: 'math', label: 'Mathematics' },
    support_state_snapshot: {
      subject_id: 'math',
      source: 'default',
      dimensions: {},
      boundary: 'Support changes presentation only.',
    },
    components: [repairComponent, otherComponent],
    status: 'active',
    created_at: '2026-08-13T08:00:00+00:00',
    updated_at: '2026-08-13T08:00:00+00:00',
  }
  const repair = {
    repair_id: 'repair-fractions',
    action_id: repairComponent.component_id,
    question_id: 'question-repair',
    concept_id: 'fractions',
    artifact_ref: 'quiz-repair',
    user_answer: '1/3',
    correct_rule: 'Equivalent fractions represent the same value.',
    error_type: 'deviation',
    status: 'identified',
    retry_count: 0,
    next_review_at: null,
    created_at: '2026-08-13T08:00:00+00:00',
    retry_prompt: 'Which fraction equals one half?',
    retry_question_type: 'short',
    retry_evidence_strength: 'strong',
    suggested_next_component_id: null as string | null,
  }
  const pack = {
    pack_id: 'pack-recovery',
    title: 'Fractions and ratios',
    goal: { text: plan.goal, status: 'active' },
    materials: [{ material_id: 'math', source_type: 'paste', title: 'Math', text: 'Source' }],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    flashcard_progress: {},
    quiz_attempts: [],
    component_plans: [plan],
    active_plan_id: plan.plan_id,
    due_review_count: 0,
    repairs: [repair],
    created_at: '2026-08-13T08:00:00+00:00',
    updated_at: '2026-08-13T08:00:00+00:00',
  }
  const generation = (generationId: string, questionId: string, nodeId: string) => ({
    generation_id: generationId,
    generation_type: 'quiz',
    status: 'completed',
    events: [],
    learner_profile: {},
    result: {
      kind: 'quiz',
      title: 'Check',
      items: [
        {
          question_id: questionId,
          node_id: nodeId,
          question: 'Give a short answer.',
          question_type: 'short',
          options: [],
        },
      ],
      save_target: 'notebook',
    },
  })

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
    if (url.endsWith('/learning-packs/pack-recovery')) {
      await route.fulfill({ json: pack })
      return
    }
    if (url.endsWith('/learning-packs/pack-recovery/repairs/repair-fractions')) {
      await route.fulfill({ json: repair })
      return
    }
    if (url.includes('/plans/plan-recovery/attempts')) {
      await route.fulfill({ json: { items: [], total: 0, limit: 200, offset: 0 } })
      return
    }
    if (url.endsWith('/traittutor/generate/tasks/quiz-repair')) {
      await route.fulfill({ json: generation('quiz-repair', 'question-repair', 'fractions') })
      return
    }
    if (url.endsWith('/traittutor/generate/tasks/quiz-other')) {
      await route.fulfill({ json: generation('quiz-other', 'question-other', 'ratios') })
      return
    }
    if (url.includes('/repairs/repair-fractions/retry')) {
      repair.retry_count += 1
      repair.status = repair.retry_count >= 2 ? 'deferred' : 'retrying'
      repair.suggested_next_component_id =
        repair.status === 'deferred' ? otherComponent.component_id : null
      await route.fulfill({
        json: {
          accepted: true,
          verified_correct: false,
          repair: { ...repair },
          recovery: {
            deferred: repair.status === 'deferred',
            suggested_next_component_id: repair.suggested_next_component_id,
          },
          evidence_strength: 'strong',
        },
      })
      return
    }
    if (url.includes('/components/assessment-other/events')) {
      const event = request.postDataJSON() as { action?: string }
      if (event.action === 'complete') {
        otherComponent.status = 'completed'
        repair.status = 'retrying'
      }
      await route.fulfill({
        json: {
          component: { ...otherComponent },
          learner_state_updated: true,
          verified_observation: 'correct',
          verified_feedback: 'Verified on the different objective.',
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

  await page.goto('/learning/pack-recovery')
  const repairAnswer = page
    .getByRole('heading', { name: /先修复这个理解缺口|Repair this gap/ })
    .locator('..')
    .getByRole('textbox')
  await repairAnswer.fill('wrong')
  await page.getByRole('button', { name: /提交重试并安排复习|Submit retry/ }).click()
  await page.getByRole('button', { name: /提交重试并安排复习|Submit retry/ }).click()

  await expect(page.getByRole('heading', { name: /比例练习|Ratio practice/ })).toBeVisible()
  await page.getByLabel(/写下你的答案与思路|Write your answer/).fill('different answer')
  await page.getByLabel(/很有把握|Very/).check()
  await page.getByRole('button', { name: /提交答案|Submit answers/ }).click()

  await page.getByRole('button', { name: /进入对应修复项|Open repair item/ }).click()
  await expect(page.getByRole('heading', { name: /先修复这个理解缺口|Repair this gap/ })).toBeVisible()
})
