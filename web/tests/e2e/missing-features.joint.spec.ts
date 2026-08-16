import { expect, test, type Page, type Route } from '@playwright/test'

const NOW = '2026-08-10T08:00:00+00:00'

test('formal routes preserve research, memory, persona, mastery, and chat boundaries', async ({
  page,
}) => {
  let memoryDeleted = false
  let persona = personaProfile('Guide', 1)
  let researchReads = 0

  await installJointRoutes(page, {
    get memoryDeleted() {
      return memoryDeleted
    },
    deleteMemory() {
      memoryDeleted = true
    },
    get persona() {
      return persona
    },
    savePersona(next) {
      persona = next
    },
    readResearch() {
      researchReads += 1
    },
  })

  await page.goto('/research/rw-joint')
  await expect(page.getByText(/^Running$|^研究中$/)).toBeVisible()
  await page.reload()
  await expect(page.getByText(/^Running$|^研究中$/)).toBeVisible()
  expect(researchReads).toBeGreaterThanOrEqual(2)

  await page.goto('/settings/memory')
  await expect(page.getByText('Keep explanations concise')).toBeVisible()
  await page
    .getByRole('button', { name: /^Delete$|^删除$/ })
    .first()
    .click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: /^Delete$|^确认删除$/ }).click()
  await expect(
    page
      .getByRole('status')
      .filter({ hasText: /removed from recall immediately|召回资格已立即移除/ })
  ).toBeVisible()
  await expect(page.getByText('Keep explanations concise')).toHaveCount(0)

  await page.goto('/settings/tutor')
  const displayName = page.getByLabel(/Display name|显示名称/)
  await expect(displayName).toHaveValue('Guide')
  await displayName.fill('Study Guide')
  await page.getByRole('button', { name: /Save Tutor Persona|保存 Tutor Persona/ }).click()
  await expect(page.getByRole('status')).toContainText(/saved as version 2|已保存为版本 2/)
  await page.reload()
  await expect(page.getByLabel(/Display name|显示名称/)).toHaveValue('Study Guide')

  await page.goto('/settings/learning-model/subject-joint')
  const learnerMain = page.locator('main')
  await expect(learnerMain.getByRole('heading', { name: 'Evidence-safe subject' })).toBeVisible()
  await expect(learnerMain.getByText(/Insufficient evidence|证据不足/)).toBeVisible()
  await expect(learnerMain.locator('[data-mastery-state="estimated"]')).toHaveCount(0)
  await expect(learnerMain.getByText('0%', { exact: true })).toHaveCount(0)

  const retiredChatRoute = await page.goto('/chat/thread-joint')
  expect(retiredChatRoute?.status()).toBe(404)
})

interface JointState {
  readonly memoryDeleted: boolean
  deleteMemory: () => void
  readonly persona: ReturnType<typeof personaProfile>
  savePersona: (next: ReturnType<typeof personaProfile>) => void
  readResearch: () => void
}

async function installJointRoutes(page: Page, state: JointState) {
  await page.addInitScript(() => {
    window.localStorage.setItem('traittutor:onboarding-profile-dismissed', 'true')
  })
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname

    if (path.endsWith('/auth/status'))
      return fulfill(route, { enabled: false, authenticated: true, username: 'joint' })
    if (path.endsWith('/settings')) return fulfill(route, { catalog: {} })
    if (path.endsWith('/memory/learner/overview')) return fulfill(route, { subjects: [] })
    if (path.endsWith('/sessions/thread-joint')) return fulfill(route, session())
    if (path.includes('/sessions')) return fulfill(route, { sessions: [] })

    if (path.endsWith('/research/workspaces/rw-joint')) {
      state.readResearch()
      return fulfill(route, researchWorkspace())
    }
    if (path.endsWith('/research/workspaces/rw-joint/briefs'))
      return fulfill(route, [researchBrief()])
    if (path.endsWith('/research/workspaces/rw-joint/runs')) return fulfill(route, [researchRun()])
    if (path.endsWith('/research/workspaces/rw-joint/sources')) return fulfill(route, [])
    if (path.endsWith('/research/workspaces/rw-joint/notes')) return fulfill(route, [])

    if (path.endsWith('/memories/candidates')) return fulfill(route, [])
    if (path.endsWith('/memories/conflicts')) return fulfill(route, [])
    if (path.endsWith('/memories/access-records')) return fulfill(route, [])
    if (path.endsWith('/memories/index/status'))
      return fulfill(route, { generation: state.memoryDeleted ? 2 : 1, entries: [] })
    if (path.endsWith('/memories/items') && request.method() === 'GET') {
      return fulfill(route, [memoryItem(state.memoryDeleted)])
    }
    if (path.endsWith('/memories/items/mem-joint') && request.method() === 'DELETE') {
      const body = request.postDataJSON() as Record<string, unknown>
      expect(body.operation_id).toMatch(/^memory-ui-delete-/)
      state.deleteMemory()
      return fulfill(route, { item: memoryItem(true), invalidated_index_generation: 2 })
    }

    if (path.endsWith('/tutor-personas') && request.method() === 'GET')
      return fulfill(route, state.persona)
    if (path.endsWith('/tutor-personas/preview') && request.method() === 'POST') {
      const body = request.postDataJSON() as { settings: ReturnType<typeof personaSettings> }
      return fulfill(route, personaContract(body.settings, state.persona.version))
    }
    if (path.endsWith('/tutor-personas') && request.method() === 'PUT') {
      const body = request.postDataJSON() as {
        settings: ReturnType<typeof personaSettings>
        expected_version: number
      }
      expect(body.expected_version).toBe(state.persona.version)
      expect(body).not.toHaveProperty('prompt')
      const next = personaProfile(body.settings.name, state.persona.version + 1, body.settings)
      state.savePersona(next)
      return fulfill(route, next)
    }

    if (path.endsWith('/memory/learner/subjects/subject-joint'))
      return fulfill(route, learnerProfile())
    if (path.endsWith('/memory/learner/subjects/subject-joint/knowledge-graph'))
      return fulfill(route, knowledgeGraph())
    if (path.endsWith('/memory/learner/evidence')) return fulfill(route, { evidence: [] })
    if (path.endsWith('/memory/learner/reflections'))
      return fulfill(route, { reflections: [], summary: {} })
    if (path.endsWith('/memory/learner/context/preview'))
      return fulfill(route, personalizationContext())
    // The detail page treats an unavailable canonical projection as
    // insufficient evidence. Returning an empty 200 object would violate the
    // typed API contract and crash while reading `knowledge`.
    if (path.endsWith('/learning-state'))
      return fulfill(route, { detail: 'projection unavailable' }, 503)
    if (
      ['/errors', '/repairs', '/misconceptions', '/reviews'].some(suffix => path.endsWith(suffix))
    )
      return fulfill(route, [])

    return fulfill(route, {})
  })
}

function researchWorkspace() {
  return {
    workspace_id: 'rw-joint',
    title: 'Recovered research',
    subject_id: null,
    status: 'active',
    revision: 2,
    active_brief_id: 'rb-joint',
    created_at: NOW,
    updated_at: NOW,
  }
}

function researchBrief() {
  return {
    brief_id: 'rb-joint',
    workspace_id: 'rw-joint',
    version: 1,
    question: 'Can the run recover?',
    objectives: [],
    constraints: [],
    source_policy: 'web',
    created_at: NOW,
  }
}

function researchRun() {
  return {
    run_id: 'rr-joint',
    workspace_id: 'rw-joint',
    brief_id: 'rb-joint',
    brief_version: 1,
    status: 'running',
    revision: 2,
    fencing_epoch: 1,
    failure_reason: null,
    created_at: NOW,
    updated_at: NOW,
  }
}

function memoryItem(deleted: boolean) {
  return {
    memory_id: 'mem-joint',
    scope: 'global',
    subject_id: null,
    kc_id: null,
    key: 'explanation.preference',
    value: deleted ? null : 'Keep explanations concise',
    redacted: deleted,
    provenance: 'explicit',
    status: deleted ? 'deleted' : 'active',
    confidence: 1,
    sensitivity: 'personal',
    valid_from: NOW,
    valid_until: null,
    supersedes_id: null,
    evidence_refs: ['profile:joint'],
    source_ref: 'profile:joint',
    created_at: NOW,
    updated_at: NOW,
  }
}

function personaSettings(name = 'Guide') {
  return {
    name,
    address_terms: ['you'] as const,
    avatar_ref: 'guide' as const,
    voice_id: 'steady' as const,
    speech_rate: 1,
    tone: 'warm' as const,
    directness: 'medium' as const,
    humor_level: 'low' as const,
    encouragement_level: 'medium' as const,
    feedback_format: 'balanced' as const,
    proactivity: 'reminders_only' as const,
    reminder_consent: false,
    emoji_policy: 'minimal' as const,
    quiet_hours: { enabled: false, start_local: '22:00', end_local: '07:00', timezone: 'UTC' },
    accessibility: {
      captions: true,
      reduced_motion: false,
      screen_reader_optimized: false,
      text_scale: 'standard' as const,
    },
    safety_version: 'persona-safety-v1' as const,
  }
}

function personaProfile(name: string, version: number, settings = personaSettings(name)) {
  return { persona_id: 'persona-joint', version, settings, created_at: NOW, updated_at: NOW }
}

function personaContract(settings: ReturnType<typeof personaSettings>, version: number) {
  return {
    contract_version: 'tutor-persona-contract.v1',
    persona_id: 'persona-joint',
    profile_version: version,
    identity: {
      display_name: settings.name,
      address_terms: settings.address_terms,
      avatar_ref: settings.avatar_ref,
    },
    expression: {
      tone: settings.tone,
      directness: settings.directness,
      humor_level: settings.humor_level,
      encouragement_level: settings.encouragement_level,
      feedback_format: settings.feedback_format,
      proactivity: settings.proactivity,
      emoji_policy: settings.emoji_policy,
    },
    modality: {
      voice_id: settings.voice_id,
      speech_rate: settings.speech_rate,
      accessibility: settings.accessibility,
    },
    quiet_hours: settings.quiet_hours,
    safety_version: 'persona-safety-v1',
  }
}

function subject() {
  return {
    subject_id: 'subject-joint',
    label: 'Evidence-safe subject',
    path: ['Evidence-safe subject'],
    confidence: 1,
    source: 'explicit',
    confirmed: true,
  }
}

function learnerProfile() {
  return {
    scope: 'subject',
    subject: subject(),
    inference_enabled: false,
    preferences: [],
    concept_signals: [
      {
        concept_id: 'kc-unknown',
        label: 'Unknown concept',
        support_level: 'unobserved',
        confidence: 0,
        attempt_count: 0,
        misconception_tags: [],
        evidence_refs: [],
        evidence_state: 'insufficient_evidence',
        model_version: 'v1-uncalibrated',
        stage_policy_version: 'bkt-stage-policy-v1',
        observation_count: 0,
        verified_observation_count: 0,
      },
    ],
    strategy_evidence: [],
    understanding: {
      status: 'starting',
      concept_count: 1,
      observed_concept_count: 0,
      coverage: 0,
      confidence: 0,
      review_load: 0,
    },
    updated_at: NOW,
    needs_rebuild: false,
  }
}

function knowledgeGraph() {
  return {
    subject: subject(),
    nodes: [
      {
        concept_id: 'kc-unknown',
        label: 'Unknown concept',
        module_id: 'module-1',
        module_label: 'Module 1',
        evidence_chunk_ids: [],
        confidence: 0,
      },
    ],
    edges: [],
    source_refs: [],
  }
}

function personalizationContext() {
  return {
    purpose: 'courseware',
    subject: subject(),
    plan: { rationale: [] },
    memory_snapshot: null,
    relevant_concept_signals: [],
    constraints: [],
    evidence_refs: [],
    degraded: false,
    degradation_reason: null,
  }
}

function session() {
  return {
    id: 'thread-joint',
    session_id: 'thread-joint',
    title: 'Joint compatibility',
    created_at: 1,
    updated_at: 1,
    preferences: {},
    messages: [],
  }
}

async function fulfill(route: Route, json: unknown, status = 200) {
  await route.fulfill({ status, json })
}
