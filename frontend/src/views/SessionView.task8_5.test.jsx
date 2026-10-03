// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, describe, expect, it, vi } from 'vitest'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import SessionView from './SessionView.jsx'
import { api } from '../api.js'

let root
let container

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

const session = {
  id: 850,
  name: 'Invented resource session',
  status: 'draft',
  model_id: 4,
  model: { id: 4, name: 'Model A', trigger: 'invented trigger' },
  workflow_id: 12,
  shots: [],
  tags: [],
  look: 'Legacy look must not replace the plan',
  wardrobe: 'Legacy wardrobe must not replace the plan',
  settings: {
    composition_mode: 'resource-v1',
    width: 1024,
    height: 1024,
    steps: 20,
    cfg: 7,
    lora_strength: 1,
    checkpoint: 'invented-model.safetensors',
  },
}

const plan = {
  version: 'resource-v1',
  look: 'Plan-owned look',
  initial_wardrobe: 'Plan-owned wardrobe',
  selected_resources: [],
  wardrobe_changes: [],
  takes: [{
    take_id: 'take-001',
    label: 'One take',
    camera: 'draft camera',
    framing: 'draft framing',
    pose: 'draft pose',
    expression: 'draft expression',
  }],
}

describe('Task 8.5 resource review presentation', () => {
  it('uses prepared snapshot choices and preserves intentional empty snapshot strings', () => {
    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: session.id,
        initialSession: session,
        initialPlan: plan,
        initialRevision: 3,
        initialActiveStep: 'review',
        initialExpandedTakeId: 'take-001',
        initialTakeReviewData: {
          'take-001': {
            snapshot: {
              final_prompt: 'Invented authoritative final prompt',
              effective_state: {
                take_choices: {
                  camera: 'prepared camera',
                  framing: '',
                  pose: 'prepared pose',
                  expression: 'prepared expression',
                },
                look: '',
                wardrobe: '',
                scope: '',
              },
              provenance: {
                selected_resource_revisions: [],
                authoring_evidence: {
                  mode: 'automatic',
                  source: 'assistant',
                  operation_id: 'invented-operation',
                  duplicate_flags: { status: 'complete', flags: [] },
                  writer_synthesis: {
                    kind: 'assistant',
                    writer_input: { request: 'Invented writer request' },
                    writer_output: 'Invented writer output',
                  },
                  writer_context: { take_id: 'take-001' },
                  predecessor_projection: { status: 'first_take' },
                  manual_completion: { descriptive_inputs: {} },
                  effective_resource_input_digest: { sha256: 'd'.repeat(64) },
                },
              },
            },
          },
        },
      }),
    )

    expect(html).toContain('Effective Camera')
    expect(html).toContain('prepared camera')
    expect(html).toContain('prepared pose')
    expect(html).toContain('(empty in prepared snapshot)')
    expect(html).not.toContain('Planned: draft camera')
    expect(html).toContain('(empty in prepared snapshot: no additional look constraint)')
    expect(html).toContain('(empty in prepared snapshot: no wardrobe description)')
    expect(html).toContain('Invented authoritative final prompt')
    expect(html).toContain('Inspect authoring evidence and writer request/output')
    expect(html).toContain('Invented writer request')
    expect(html).toContain('Invented writer output')
    expect(html).toContain('Duplicate check: complete.')
    expect(html).toContain('Approve Review (Rev 3)')
    expect(html).toContain('Submit Test Selection (0)')
    expect(html).not.toContain('Test Generate Selected')
  })

  it('shows linked generated history and its saved choices when the current revision is missing', () => {
    const historySnapshot = {
      take_id: 'take-001',
      plan_revision: 2,
      status: 'generated',
      linked_shot_id: 884,
      final_prompt: 'Historical prompt remains immutable.',
      effective_state: {
        take_choices: {
          camera: 'historical camera',
          framing: 'historical framing',
          pose: 'historical pose',
          expression: 'historical expression',
        },
        look: 'historical effective look',
        wardrobe: 'historical effective wardrobe',
        scope: 'initial',
      },
      provenance: { selected_resource_revisions: [] },
    }
    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: session.id,
        initialSession: session,
        initialPlan: plan,
        initialRevision: 4,
        initialActiveStep: 'review',
        initialExpandedTakeId: 'take-001',
        initialPreparation: {
          completed: [],
          incomplete: plan.takes.map((take) => ({ take_id: take.take_id, status: 'missing' })),
          history: [historySnapshot],
        },
      }),
    )

    expect(html).toContain('Generated [Shot #884]')
    expect(html).toContain('historical camera')
    expect(html).toContain('historical effective look')
    expect(html).toContain('historical effective wardrobe')
    expect(html).toContain('Historical prompt remains immutable.')
    expect(html).not.toContain('Planned: draft camera')
    expect(html).not.toContain('Pending prep')
    expect(html).toContain('Prepare Incomplete Takes (0)')
  })

  it('shows a missing plan diagnostic instead of legacy session look and wardrobe', () => {
    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: session.id,
        initialSession: {
          ...session,
          diagnostic: {
            code: 'resource_plan_missing',
            message: 'Resource-v1 plan is missing; look and wardrobe are unavailable.',
          },
        },
        initialPlan: null,
        initialRevision: null,
      }),
    )

    expect(html).toContain('Resource plan needs attention')
    expect(html).toContain('Resource-v1 plan is missing')
    expect(html).not.toContain('Legacy look must not replace the plan')
    expect(html).not.toContain('Legacy wardrobe must not replace the plan')
  })

  it('shows affected work before saving and sends the CAS only after confirmation', async () => {
    let revision = 3
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === `/api/sessions/${session.id}`) return session
      if (path === `/api/sessions/${session.id}/plan`) {
        return { plan_revision: revision, plan }
      }
      if (path === '/api/config' || path === '/api/comfy/models') return {}
      return []
    })
    const post = vi.spyOn(api, 'post').mockImplementation(async (path) => {
      if (path === `/api/sessions/${session.id}/plan`) {
        revision = 4
        return { plan_revision: revision, conflicts: [] }
      }
      throw new Error(`Unexpected POST ${path}`)
    })
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root.render(React.createElement(SessionView, {
        id: session.id,
        initialSession: session,
        initialPlan: plan,
        initialRevision: revision,
        initialActiveStep: 'constants',
      }))
      await Promise.resolve()
    })

    const look = container.querySelector('textarea')
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
      setter.call(look, 'Edited plan look')
      look.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const saveDraft = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Save Draft'))
    await act(async () => saveDraft.click())

    expect(post).not.toHaveBeenCalled()
    expect(container.textContent).toContain('Review downstream impact before saving')
    expect(container.textContent).toContain('Affected takes: take-001')
    expect(container.textContent).toContain('Ready takes requiring re-preparation: None')

    const confirmSave = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Save Plan Changes'))
    await act(async () => confirmSave.click())
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][1].expected_revision).toBe(3)
    expect(post.mock.calls[0][1].plan.look).toBe('Edited plan look')
  })

  it('checks dependencies only for confirmed invalid authoring evidence and keeps review gates closed', async () => {
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === `/api/sessions/${session.id}`) return session
      if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
      if (path === `/api/sessions/${session.id}/plan/review?plan_revision=3`) {
        throw Object.assign(new Error('authoring_evidence_invalid: prepared evidence is invalid'), {
          status: 422,
          detail: 'authoring_evidence_invalid: prepared evidence is invalid',
        })
      }
      if (path === '/api/config' || path === '/api/comfy/models') return {}
      return []
    })
    const post = vi.spyOn(api, 'post').mockResolvedValue({
      plan_revision: 3,
      refreshed: false,
      affected_takes: [],
      required_preparation: [],
      copied_forward_takes: [],
      diagnostics: [],
    })
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root.render(React.createElement(SessionView, {
        id: session.id,
        initialSession: session,
        initialPlan: plan,
        initialRevision: 3,
        initialActiveStep: 'review',
      }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('Resource drift is not confirmed')
    expect(container.textContent).toContain('Refresh Resources')
    const refresh = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Refresh Resources'))
    await act(async () => {
      refresh.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][0]).toBe(`/api/sessions/${session.id}/plan/refresh-resources`)
    expect(post.mock.calls[0][1]).toEqual({ expected_revision: 3 })
    expect(container.textContent).toContain('No resource drift found.')
    expect(container.textContent).not.toContain('Resource drift confirmed;')
    const approve = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Approve Review'))
    expect(approve.disabled).toBe(true)
  })

  it('does not label a generic review failure as drift or offer a drift refresh', async () => {
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === `/api/sessions/${session.id}`) return session
      if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
      if (path === `/api/sessions/${session.id}/plan/review?plan_revision=3`) {
        throw Object.assign(new Error('review service unavailable'), { status: 503, detail: 'review service unavailable' })
      }
      if (path === '/api/config' || path === '/api/comfy/models') return {}
      return []
    })
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root.render(React.createElement(SessionView, {
        id: session.id,
        initialSession: session,
        initialPlan: plan,
        initialRevision: 3,
        initialActiveStep: 'review',
      }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('review service unavailable')
    expect(container.textContent).not.toContain('Resource drift is not confirmed')
    expect([...container.querySelectorAll('button')].some((button) => button.textContent.includes('Refresh Resources'))).toBe(false)
  })
})
