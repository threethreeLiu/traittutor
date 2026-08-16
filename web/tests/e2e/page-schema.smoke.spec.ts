import { expect, test } from '@playwright/test'

test('LearningCanvas renders a served PageSchema safely and emits a stable action id', async ({
  page,
}) => {
  const recordedEvents: Record<string, unknown>[] = []
  const component = {
    component_id: 'component-lesson',
    // worked_example keeps the manual complete/continue action bar: the plan
    // component type drives the action bar, while the PageSchema region below
    // still renders a concept_explanation for the content assertions.
    component_type: 'worked_example',
    executor: 'lesson',
    label_zh: '极限解释',
    label_en: 'Limit explanation',
    concept_refs: ['limits'],
    support_dimensions: [],
    bkt_stage: 'unobserved',
    modality: 'text',
    dependencies: [],
    required: true,
    reason: 'Build a source-grounded explanation.',
    evidence_refs: ['source:c1'],
    completion_event: 'complete',
    status: 'pending',
    output_ref: undefined as string | undefined,
  }
  const plan = {
    plan_id: 'plan-page-schema',
    pack_id: 'pack-page-schema',
    version: 1,
    goal: 'Understand limits',
    subject_ref: { subject_id: 'math', label: 'Mathematics' },
    support_state_snapshot: {
      subject_id: 'math',
      source: 'default',
      dimensions: {},
      boundary: 'Support changes presentation only.',
    },
    components: [component],
    status: 'active',
    created_at: '2026-08-09T08:00:00+00:00',
    updated_at: '2026-08-09T08:00:00+00:00',
  }
  const pack = {
    pack_id: 'pack-page-schema',
    title: 'Limits',
    goal: { text: 'Understand limits', status: 'active' },
    materials: [
      {
        material_id: 'material-limits',
        source_type: 'paste',
        title: 'Limits',
        text: 'Limits source',
      },
    ],
    material_revisions: [],
    artifacts: { courseware: [], flashcards: [], quiz: [] },
    flashcard_progress: {},
    quiz_attempts: [],
    component_plans: [plan],
    active_plan_id: plan.plan_id,
    due_review_count: 0,
    repairs: [],
    created_at: '2026-08-09T08:00:00+00:00',
    updated_at: '2026-08-09T08:00:00+00:00',
  }
  const generation = {
    generation_id: 'generation-page-schema',
    generation_type: 'courseware',
    status: 'completed',
    events: [],
    result: {
      kind: 'courseware',
      title: 'Legacy title must not be the renderer source',
      sections: [{ section_title: 'Legacy section', core_content: 'Legacy body' }],
      save_target: 'notebook',
    },
    learner_profile: {},
    page_schema_id: 'generation-page-schema:page',
    page_schema: {
      page_schema_id: 'generation-page-schema:page',
      generation_run_id: 'generation-page-schema',
      version: 'v1',
      published: false,
      created_at: '2026-08-09T08:00:00+00:00',
      regions: [
        {
          region_id: 'region-concept',
          component: {
            instance_id: 'instruction-1',
            component_type: 'concept_explanation',
            version: 'v1',
            props: {
              title: 'Limits from PageSchema',
              body_markdown:
                'A limit describes the value a function approaches from nearby inputs.',
              figure: {
                type: 'concept_map',
                title: 'Limits as a concept map',
                nodes: [
                  { id: 'n1', label: 'Function', detail: 'Maps inputs to outputs' },
                  { id: 'n2', label: 'Limit value' },
                  { id: 'n3', label: 'Nearby inputs' },
                ],
                edges: [
                  { from: 'n1', to: 'n2', label: 'approaches' },
                  { from: 'n3', to: 'n2', label: 'converge to' },
                ],
              },
            },
            modality_hint: 'text',
          },
        },
        {
          region_id: 'region-unsafe-media',
          component: {
            instance_id: 'visual-1',
            component_type: 'visual_map',
            version: 'v1',
            props: {
              title: 'Safe media fallback',
              media_url: 'javascript:alert(1)',
              a11y_label: 'Unsafe image was blocked',
            },
            modality_hint: 'visual',
          },
        },
        {
          region_id: 'region-unknown',
          component: {
            instance_id: 'unknown-1',
            component_type: 'remote_script_widget',
            version: 'v1',
            props: {
              body_markdown: '<script>window.__traittutor_xss = true</script>',
            },
          },
        },
      ],
    },
  }
  let traceMode: 'error' | 'unavailable' | 'ready' = 'error'
  let releaseInitialTrace = () => {}
  const initialTraceGate = new Promise<void>(resolve => {
    releaseInitialTrace = resolve
  })
  const unavailableTrace = {
    run_id: 'run-page-schema',
    generation_run_id: 'generation-page-schema',
    graph_id: 'graph-page-schema',
    status: 'degraded',
    graph_status: 'unavailable',
    graph_version: null,
    created_at: null,
    page_schema_id: 'generation-page-schema:page',
    nodes: [],
    budget: {
      total_planned_budget_ms: null,
      total_timeout_ms: null,
      total_retry_limit: null,
      elapsed_ms: null,
      timing_status: 'unavailable',
    },
    validation: {
      status: 'unavailable',
      finding_count: 0,
      category_codes: [],
      offending_task_ids: [],
    },
    degradation_codes: [],
  }
  const runTrace = {
    ...unavailableTrace,
    graph_status: 'available',
    graph_version: 'v1',
    created_at: '2026-08-09T08:00:00+00:00',
    nodes: [
      {
        task_id: 'material',
        task_type: 'material',
        status: 'succeeded',
        depends_on: [],
        input_refs: [{ kind: 'grounding', ref_id: 'chunk-safe' }],
        redacted_input_ref_count: 0,
        failure_code: null,
        degradation_codes: [],
      },
      {
        task_id: 'instruction',
        task_type: 'instruction',
        status: 'degraded',
        depends_on: ['material'],
        input_refs: [{ kind: 'context_snapshot', ref_id: 'snapshot-safe' }],
        redacted_input_ref_count: 1,
        failure_code: null,
        degradation_codes: ['task_degraded'],
      },
    ],
    budget: {
      total_planned_budget_ms: 300,
      total_timeout_ms: 400,
      total_retry_limit: 1,
      elapsed_ms: 245,
      timing_status: 'available',
    },
    validation: {
      status: 'repair',
      finding_count: 1,
      category_codes: ['component_schema', 'SECRET PRIVATE VALIDATION'],
      offending_task_ids: ['instruction'],
    },
    degradation_codes: [
      'run_degraded',
      'task_degraded',
      'validation_not_passed',
      'SECRET PRIVATE DEGRADATION',
    ],
    prompt: 'SECRET PRIVATE PROMPT',
    answer: 'SECRET ANSWER',
    rubric: 'SECRET RUBRIC',
    tool_args: { secret: 'SECRET TOOL PARAMS' },
    reasoning: 'SECRET PRIVATE REASONING',
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
    if (url.endsWith('/generation-runs/generation-page-schema/trace')) {
      if (traceMode === 'error') {
        await initialTraceGate
        await route.fulfill({ status: 503, json: { detail: 'Generation run trace unavailable' } })
      } else if (traceMode === 'unavailable') {
        await route.fulfill({ json: unavailableTrace })
      } else {
        await route.fulfill({ json: runTrace })
      }
      return
    }
    if (url.endsWith('/traittutor/generate/tasks/generation-page-schema')) {
      await route.fulfill({ json: generation })
      return
    }
    if (url.endsWith('/traittutor/generate/tasks') && request.method() === 'POST') {
      await route.fulfill({
        json: {
          generation_id: 'generation-page-schema',
          status: 'queued',
          events_url: '/api/v1/traittutor/generate/tasks/generation-page-schema/events',
          result_url: '/api/v1/traittutor/generate/tasks/generation-page-schema',
        },
      })
      return
    }
    if (url.endsWith('/learning-packs/pack-page-schema')) {
      await route.fulfill({ json: pack })
      return
    }
    if (url.includes('/plans/plan-page-schema/components/component-lesson/events')) {
      const event = request.postDataJSON() as Record<string, unknown>
      recordedEvents.push(event)
      const action = String(event.action || '')
      component.status = action === 'complete' ? 'completed' : 'active'
      if (event.output_ref) component.output_ref = String(event.output_ref)
      await route.fulfill({
        json: {
          component: { ...component },
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

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/learning/pack-page-schema')

  const mobilePathPanel = page.getByTestId('learning-path-panel')
  await expect(mobilePathPanel).toBeVisible()
  const mobilePathBox = await mobilePathPanel.boundingBox()
  expect(mobilePathBox).not.toBeNull()
  // Chromium may report quarter-pixel text rounding at this viewport; keep
  // the compact-panel contract while allowing one CSS pixel of tolerance.
  expect(mobilePathBox!.height).toBeLessThanOrEqual(176)

  const mobileStartButton = page.getByRole('button', { name: /Start learning|开始学习/ })
  await expect(mobileStartButton).toBeVisible()
  const mobileStartBox = await mobileStartButton.boundingBox()
  expect(mobileStartBox).not.toBeNull()
  expect(mobileStartBox!.y + mobileStartBox!.height).toBeLessThanOrEqual(844)

  await page.setViewportSize({ width: 1280, height: 800 })
  await expect(page.getByRole('heading', { name: /Learning assistant|问学习内容/ })).toBeVisible()
  await expect(page.getByText(/Learning rationale|学习依据/)).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Add tutor|添加导师/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /Voice input|语音输入/ })).toBeVisible()
  const assistantComposer = page.getByRole('textbox', {
    name: /Ask about the current learning content|输入关于当前学习内容的问题/,
  })
  await assistantComposer.fill('Keep this question while minimized')
  await page.getByRole('button', { name: /Minimize learning assistant|最小化学习助手/ }).click()
  const assistantBubble = page.getByRole('button', {
    name: /Open learning Q&A assistant|打开学习问答助手/,
  })
  await expect(assistantBubble).toBeVisible()
  await assistantBubble.click()
  await expect(page.getByRole('heading', { name: /Learning assistant|问学习内容/ })).toBeVisible()
  await expect(assistantComposer).toHaveValue('Keep this question while minimized')
  await page.getByRole('button', { name: /Start learning|开始学习/ }).click()
  const trace = page.getByTestId('generation-run-trace')
  await expect(trace).toHaveAttribute('aria-busy', 'true')
  releaseInitialTrace()
  await expect(trace.getByRole('alert')).toContainText(
    /Generation trace unavailable|生成过程暂时无法读取/
  )
  traceMode = 'unavailable'
  // The error card offers regeneration only (no read-retry). Clicking it
  // regenerates the component AND re-fetches the run record, which now
  // resolves to the task-graph-unavailable state.
  await trace.getByRole('button', { name: /Regenerate|重新生成/ }).click()
  await expect(trace).toContainText(/Task graph unavailable|任务图暂不可用/)
  traceMode = 'ready'
  await trace.getByRole('button', { name: /Try again|重新读取/ }).click()
  await expect(trace.getByRole('heading', { name: /Generation trace|生成过程/ })).toBeVisible()
  await expect(trace).toContainText(/Instruction|讲解/)
  await expect(trace).toContainText(/Depends on: Material|依赖：材料/)
  await expect(trace).toContainText('300 ms')
  await expect(trace).toContainText('245 ms')
  await expect(trace).toContainText(/Component safety and schema|组件安全与结构/)
  await expect(trace).toContainText(/Task degraded|部分任务已降级/)
  await expect(trace).toContainText(/1 hidden|1 条已隐藏/)
  await expect(trace).not.toContainText(
    /SECRET PRIVATE PROMPT|SECRET ANSWER|SECRET RUBRIC|SECRET TOOL PARAMS|SECRET PRIVATE REASONING|SECRET PRIVATE VALIDATION|SECRET PRIVATE DEGRADATION/
  )
  await expect(page.getByRole('heading', { name: 'Limits from PageSchema' })).toBeVisible()
  await expect(
    page.getByText('A limit describes the value a function approaches from nearby inputs.')
  ).toBeVisible()
  // structured figure renders as a concept map card with its SVG node labels
  await expect(page.getByText('Concept map')).toBeVisible()
  await expect(page.getByText('Limits as a concept map')).toBeVisible()
  // the SVG text node and its <title> tooltip both carry the label; assert the
  // visible text node specifically
  await expect(page.getByText('Limit value').first()).toBeVisible()
  await expect(page.getByText('Function').first()).toBeVisible()
  await expect(page.getByText('Unsafe image was blocked')).toBeVisible()
  await expect(
    page.getByText(/Unsupported component type; showing text only|该组件类型未注册，已降级为文字/)
  ).toBeVisible()
  await expect(page.getByText('<script>window.__traittutor_xss = true</script>')).toBeVisible()
  await expect(page.getByText('Legacy body')).toHaveCount(0)
  expect(
    await page.evaluate(
      () => (window as typeof window & { __traittutor_xss?: boolean }).__traittutor_xss
    )
  ).toBeUndefined()
  // Generation ran twice: the initial start/feedback pair, then the
  // regenerate click persisted another feedback for the same artifact.
  await expect.poll(() => recordedEvents.length).toBe(3)
  expect(recordedEvents.slice(0, 2).map(event => event.action)).toEqual(['start', 'feedback'])
  expect(recordedEvents[1]).toMatchObject({
    action: 'feedback',
    output_ref: 'generation-page-schema',
    replan: false,
  })
  expect(recordedEvents[2]).toMatchObject({
    action: 'feedback',
    output_ref: 'generation-page-schema',
    replan: false,
  })

  await page.getByRole('button', { name: /Complete and continue|完成并继续/ }).click()
  await expect.poll(() => recordedEvents.length).toBe(4)
  expect(recordedEvents[3]).toMatchObject({
    event_id: 'generation-page-schema:component-lesson:complete',
    action: 'complete',
    replan: false,
  })
})
