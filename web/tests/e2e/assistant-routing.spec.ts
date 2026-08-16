import { expect, test, type Page } from '@playwright/test'

type SeenRequest = {
  pathname: string
  body: Record<string, unknown>
}

async function installAssistantConversationRuntime(
  page: Page,
  seen: SeenRequest[],
  sessionOverrides: Record<string, unknown> = {}
) {
  await page.addInitScript(() => {
    window.localStorage.setItem('traittutor:onboarding-profile-dismissed', 'true')
    window.localStorage.setItem('traittutor-language', 'en')
  })

  await page.routeWebSocket('**/api/v1/ws', socket => {
    socket.onMessage(message => {
      try {
        const body = JSON.parse(String(message)) as Record<string, unknown>
        if (body.type === 'start_turn' || body.type === 'submit_user_reply') {
          seen.push({ pathname: `ws:${String(body.type)}`, body })
        }
      } catch {
        // Ignore non-protocol frames.
      }
    })
  })

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const body = request.postDataJSON?.() as Record<string, unknown> | undefined

    if (path === '/api/v1/auth/status') {
      await route.fulfill({
        json: { enabled: false, authenticated: true, username: 'assistant-e2e' },
      })
      return
    }
    if (path === '/api/v1/sessions/assistant-session') {
      await route.fulfill({
        json: {
          id: 'assistant-session',
          session_id: 'assistant-session',
          title: 'Assistant conversation',
          created_at: 1,
          updated_at: 2,
          messages: [
            {
              id: 1,
              session_id: 'assistant-session',
              role: 'assistant',
              content: 'How can I help?',
              events: [],
              attachments: [],
              created_at: 1,
            },
          ],
          ...sessionOverrides,
        },
      })
      return
    }
    if (path === '/api/v1/tutor-personas' && request.method() === 'GET') {
      await route.fulfill({
        json: {
          persona_id: 'persona-e2e',
          version: 1,
          settings: {
            name: 'E2E Tutor',
            address_terms: ['learner'],
            avatar_ref: 'default',
            voice_id: 'default',
            speech_rate: 1,
            tone: 'warm',
            directness: 'low',
            humor_level: 'low',
            encouragement_level: 'low',
            feedback_format: 'balanced',
            proactivity: 'off',
            reminder_consent: false,
            emoji_policy: 'minimal',
            text_scale: 'standard',
            quiet_hours: {
              enabled: false,
              start_local: '22:00',
              end_local: '07:00',
              timezone: 'UTC',
            },
            accessibility_preferences: {
              captions: false,
              reduced_motion: false,
              screen_reader_optimized: false,
              text_scale: 'standard',
            },
          },
          created_at: '2026-08-13T00:00:00+00:00',
          updated_at: '2026-08-13T00:00:00+00:00',
        },
      })
      return
    }

    if (request.method() !== 'GET') {
      seen.push({ pathname: path, body: body ?? {} })
    }
    if (path.includes('/sessions')) {
      await route.fulfill({ json: { sessions: [] } })
      return
    }
    if (path === '/api/v1/settings') {
      await route.fulfill({ json: { catalog: {} } })
      return
    }
    if (path.includes('/learner/')) {
      await route.fulfill({ json: { subjects: [], evidence: [], reflections: [], summary: {} } })
      return
    }
    await route.fulfill({ status: 200, json: {} })
  })
}

test('mobile Assistant composer keeps every action inside its card', async ({ page }) => {
  const seen: SeenRequest[] = []
  await page.setViewportSize({ width: 390, height: 844 })
  await installAssistantConversationRuntime(page, seen)
  await page.goto('/assist/assistant-session')

  const card = page.getByTestId('chat-composer-card')
  await expect(card).toBeVisible()
  await expect
    .poll(() =>
      card.evaluate(element => {
        const cardRect = element.getBoundingClientRect()
        const controls = Array.from(element.querySelectorAll<HTMLElement>('a, button')).filter(
          control => control.getClientRects().length > 0
        )
        return controls.every(control => {
          const rect = control.getBoundingClientRect()
          return rect.left >= cardRect.left && rect.right <= cardRect.right
        })
      })
    )
    .toBe(true)
})

test('ask_user confirmation stays inline and makes skipped answers explicit', async ({ page }) => {
  const seen: SeenRequest[] = []
  await installAssistantConversationRuntime(page, seen, {
    status: 'running',
    active_turns: [
      {
        id: 'turn-ask-user',
        turn_id: 'turn-ask-user',
        session_id: 'assistant-session',
        capability: 'chat',
        status: 'running',
        error: '',
        created_at: 1,
        updated_at: 1,
        last_seq: 1,
      },
    ],
    messages: [
      {
        id: 1,
        session_id: 'assistant-session',
        role: 'assistant',
        content: 'I need two choices before continuing.',
        attachments: [],
        created_at: 1,
        events: [
          {
            event_id: 'event-ask-user',
            request_id: 'request-ask-user',
            type: 'tool_result',
            source: 'ask_user',
            content: '',
            stage: 'waiting_for_user',
            turn_id: 'turn-ask-user',
            timestamp: 1,
            metadata: {
              tool_call_id: 'ask-1',
              tool_metadata: {
                ask_user: {
                  intro: 'Confirm how you want to continue.',
                  questions: [
                    {
                      id: 'pace',
                      header: 'Pace',
                      prompt: 'Which pace should I use?',
                      multi_select: false,
                      allow_free_text: true,
                      options: [
                        { label: 'Steady pace', description: 'Keep each step manageable.' },
                        { label: 'Fast pace', description: 'Move through familiar material quickly.' },
                      ],
                    },
                    {
                      id: 'format',
                      header: 'Format',
                      prompt: 'Which format do you prefer?',
                      multi_select: false,
                      allow_free_text: true,
                      options: [
                        { label: 'Worked examples', description: null },
                        { label: 'Short explanations', description: null },
                      ],
                    },
                  ],
                },
              },
            },
          },
        ],
      },
    ],
  })

  await page.goto('/assist/assistant-session')
  await expect(page.getByRole('heading', { name: 'Confirm how you want to continue.' })).toBeVisible()
  await expect(page.getByText('0 of 2 answered — choose a question to continue.')).toBeVisible()

  const firstOption = page.getByRole('button', { name: /Steady pace/ })
  await firstOption.click()
  await expect(page.getByText('1 of 2 answered — choose a question to continue.')).toBeVisible()
  await expect(page.getByRole('button', { name: /Pace — Answered/ })).toBeVisible()
  await expect(page.getByText('Which format do you prefer?')).toBeVisible()

  await page.getByRole('button', { name: 'Submit and skip 1' }).click()
  await expect
    .poll(() => seen.find(entry => entry.pathname === 'ws:submit_user_reply')?.body)
    .toMatchObject({
      type: 'submit_user_reply',
      turn_id: 'turn-ask-user',
      answers: [
        { questionId: 'pace', text: 'Steady pace' },
        { questionId: 'format', text: '' },
      ],
    })
})

test('Assistant sends learning-like text as conversation without Learn hand-off', async ({
  page,
}) => {
  const seen: SeenRequest[] = []
  await installAssistantConversationRuntime(page, seen)
  await page.goto('/assist/assistant-session')
  await expect(page.getByText('How can I help?')).toBeVisible()

  const message = '请帮我制定一个微积分学习计划'
  await page.locator('textarea').last().fill(message)
  await page.getByRole('button', { name: /^(Send|发送)$/ }).click()

  await expect.poll(() => seen.filter(entry => entry.pathname === 'ws:start_turn').length).toBe(1)
  expect(seen.find(entry => entry.pathname === 'ws:start_turn')?.body).toMatchObject({
    type: 'start_turn',
    content: message,
    session_id: 'assistant-session',
  })
  expect(seen.filter(entry => entry.pathname === '/api/v1/assistant/route')).toHaveLength(0)
  expect(seen.filter(entry => entry.pathname === '/api/v1/learning/intent')).toHaveLength(0)
  expect(
    seen.filter(
      entry =>
        entry.pathname.includes('/learning-packs') ||
        entry.pathname.includes('/component-plans') ||
        entry.pathname.endsWith('/confirm')
    )
  ).toHaveLength(0)
  await expect(
    page.getByRole('alertdialog', {
      name: /Confirm next step|确认下一步|Confirm courseware task|确认课件任务/,
    })
  ).toHaveCount(0)
})

for (const shortcut of [
  { label: /Solver|智能解题/, mode: 'solve' },
  { label: /学习探索|Learning Exploration/, mode: 'learning_exploration' },
  { label: /知识图解|Knowledge Diagram/, mode: 'knowledge_diagram' },
  { label: /Humanizer|自然改写/, mode: 'humanizer' },
]) {
  test(`${shortcut.mode} sends only visible user text and typed mode config`, async ({ page }) => {
    const seen: SeenRequest[] = []
    await installAssistantConversationRuntime(page, seen)
    await page.goto('/assist/assistant-session')
    await expect(page.getByText('How can I help?')).toBeVisible()

    await page.getByRole('button', { name: /Add files & context|添加文件和上下文/ }).click()
    await page.getByRole('button', { name: shortcut.label }).click()

    const message = `visible request for ${shortcut.mode}`
    await page.locator('textarea').last().fill(message)
    await page.getByRole('button', { name: /^(Send|发送)$/ }).click()

    await expect.poll(() => seen.filter(entry => entry.pathname === 'ws:start_turn').length).toBe(1)
    const startTurn = seen.find(entry => entry.pathname === 'ws:start_turn')?.body
    expect(startTurn?.content).toBe(message)
    expect(startTurn?.content).not.toMatch(/TRAITTUTOR_/i)
    expect(startTurn?.config).toMatchObject({
      product_mode: 'assist',
      traittutor_mode: shortcut.mode,
    })
  })
}

test('clicking Learning Tools from an Assistant history opens a fresh conversation', async ({
  page,
}) => {
  const seen: SeenRequest[] = []
  await installAssistantConversationRuntime(page, seen)
  await page.goto('/assist/assistant-session')
  await expect(page.getByText('How can I help?')).toBeVisible()

  await page.locator('a[data-chat-entry="/assist"]').click()

  await expect(page).toHaveURL(/\/assist$/)
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as Window & { __traittutorSuppressedHistoricalSessionId?: string })
            .__traittutorSuppressedHistoricalSessionId
      )
    )
    .toBe('assistant-session')
  await expect(page.getByText('How can I help?')).toHaveCount(0)
  await expect(page.locator('textarea').last()).toBeVisible()
  await page.waitForTimeout(750)
  await expect(page).toHaveURL(/\/assist$/)
  await expect(page.getByText('How can I help?')).toHaveCount(0)
})

test('mobile Learning Tools entry also resets an Assistant history', async ({ page }) => {
  const seen: SeenRequest[] = []
  await page.setViewportSize({ width: 390, height: 844 })
  await installAssistantConversationRuntime(page, seen)
  await page.goto('/assist/assistant-session')
  await expect(page.getByText('How can I help?')).toBeVisible()

  await page.getByRole('link', { name: /Learning Tools|学习工具/, exact: true }).click()

  await expect(page).toHaveURL(/\/assist$/)
  await expect(page.getByText('How can I help?')).toHaveCount(0)
  await expect(page.locator('textarea').last()).toBeVisible()
})
