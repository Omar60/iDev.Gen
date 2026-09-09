/** Pure utility functions for resource-v1 session planning (Task 5.2).
 *
 *  Kept strictly pure and dependency-free so the planning logic,
 *  take immutability, and CAS payload formatting can be verified
 *  independently of the React DOM.
 */

export const MODE_RESOURCE_V1 = 'resource-v1'

/** Check whether a session is configured for resource-v1 composition.
 *
 *  A session is in resource-v1 mode when its top-level composition_mode or its
 *  persisted settings.composition_mode explicitly matches 'resource-v1'.
 *  Absence or any other value defaults to legacy behavior.
 */
export function isResourceSession(session) {
  if (!session) return false
  if (session.composition_mode === MODE_RESOURCE_V1) return true
  if (session.settings && session.settings.composition_mode === MODE_RESOURCE_V1) return true
  return false
}
/** Normalize an incoming plan draft from the API or creation payload.
 *
 *  Preserves exact resource revision triples, stable take IDs, creative choices,
 *  and any existing wardrobe changes without data loss.
 */
export function normalizePlan(rawPlan) {
  const plan = rawPlan?.plan || rawPlan || {}

  const look = typeof plan.look === 'string' ? plan.look : ''
  const initialWardrobe = typeof plan.initial_wardrobe === 'string'
    ? plan.initial_wardrobe
    : (typeof plan.initialWardrobe === 'string' ? plan.initialWardrobe : '')

  // Normalize selected resources ensuring library_key, source_id, content_digest are retained
  const selectedResources = (plan.selected_resources || plan.selectedResources || []).map((res) => ({
    library_key: String(res.library_key || '').trim(),
    source_id: String(res.source_id || '').trim(),
    content_digest: String(res.content_digest || '').trim(),
  }))

  // Normalize takes preserving take_id and creative choices
  const rawTakes = Array.isArray(plan.takes) ? plan.takes : []
  const takes = rawTakes.length > 0
    ? rawTakes.map((t, idx) => ({
        ...t,
        take_id: String(t.take_id || `take-${String(idx + 1).padStart(3, '0')}`).trim(),
        camera: typeof t.camera === 'string' ? t.camera : '',
        framing: typeof t.framing === 'string' ? t.framing : '',
        pose: typeof t.pose === 'string' ? t.pose : '',
        expression: typeof t.expression === 'string' ? t.expression : '',
      }))
    : [{ take_id: 'take-001', camera: '', framing: '', pose: '', expression: '' }]

  // Normalize wardrobe changes
  const wardrobeChanges = (plan.wardrobe_changes || plan.wardrobeChanges || []).map((ch) => ({
    take_id: String(ch.take_id || '').trim(),
    scope: ch.scope === 'from_here' ? 'from_here' : 'this_take',
    wardrobe: typeof ch.wardrobe === 'string' ? ch.wardrobe : '',
  }))

  const normalized = {
    version: MODE_RESOURCE_V1,
    look,
    initial_wardrobe: initialWardrobe,
    takes,
    selected_resources: selectedResources,
    wardrobe_changes: wardrobeChanges,
  }

  if (Array.isArray(plan.conflicts)) {
    normalized.conflicts = plan.conflicts
  }

  return normalized
}

/** Generate the next stable unique take_id (e.g. take-002, take-003). */
export function nextTakeId(existingTakes = []) {
  const seenNumbers = []
  for (const t of existingTakes) {
    const match = String(t.take_id || '').match(/^take-(\d+)$/)
    if (match) {
      seenNumbers.push(parseInt(match[1], 10))
    }
  }
  const max = seenNumbers.length > 0 ? Math.max(...seenNumbers) : 0
  let nextNum = max + 1
  let candidate = `take-${String(nextNum).padStart(3, '0')}`
  const existingIds = new Set(existingTakes.map((t) => t.take_id))
  while (existingIds.has(candidate)) {
    nextNum += 1
    candidate = `take-${String(nextNum).padStart(3, '0')}`
  }
  return candidate
}

/** Create and append a new take with a stable take_id while preserving existing takes. */
export function createTake(existingTakes = [], defaults = {}) {
  const newId = nextTakeId(existingTakes)
  const newTake = {
    take_id: newId,
    camera: '',
    framing: '',
    pose: '',
    expression: '',
    ...defaults,
  }
  // take_id must remain the newly generated ID
  newTake.take_id = newId
  return [...existingTakes, newTake]
}

/** Immutably update creative choices for a take while strictly locking its take_id. */
export function updateTake(existingTakes = [], takeId, changes = {}) {
  return existingTakes.map((take) => {
    if (take.take_id === takeId) {
      return {
        ...take,
        ...changes,
        take_id: takeId, // lock take_id
      }
    }
    return take
  })
}

/** Immutably remove a take by take_id, preserving at least one take. */
export function removeTake(existingTakes = [], takeId) {
  if (existingTakes.length <= 1) {
    return existingTakes
  }
  return existingTakes.filter((take) => take.take_id !== takeId)
}

/** Build the CAS save payload matching the backend PlanDraftIn contract. */
export function buildPlanSavePayload(plan, expectedRevision) {
  if (typeof expectedRevision !== 'number' || Number.isNaN(expectedRevision) || expectedRevision < 0) {
    throw new Error(`expected_revision must be a non-negative integer, got ${expectedRevision}`)
  }

  const normalized = normalizePlan(plan)
  return {
    plan: {
      version: MODE_RESOURCE_V1,
      look: normalized.look,
      initial_wardrobe: normalized.initial_wardrobe,
      takes: normalized.takes,
      selected_resources: normalized.selected_resources,
      wardrobe_changes: normalized.wardrobe_changes,
    },
    expected_revision: expectedRevision,
  }
}

/** Load session plan from backend API.
 *
 *  Does NOT fabricate a fallback plan or revision 0 if missing.
 *  Returns an object representing the load outcome:
 *  - ok: true/false
 *  - plan: normalized plan object, or null on failure
 *  - planRevision: integer revision from backend, or null on failure
 *  - conflicts: list of conflict markers
 *  - error: string message if failed, or null on success
 */
export async function loadSessionPlan(sessionId, api) {
  try {
    const data = await api.get(`/api/sessions/${sessionId}/plan`)
    const rawPlan = data.plan || data
    const planRevision = typeof data.plan_revision === 'number'
      ? data.plan_revision
      : (typeof rawPlan.plan_revision === 'number' ? rawPlan.plan_revision : null)

    if (planRevision === null || planRevision < 0) {
      return {
        ok: false,
        plan: null,
        planRevision: null,
        conflicts: [],
        error: 'Loaded draft missing valid plan_revision from backend',
      }
    }

    return {
      ok: true,
      plan: normalizePlan(rawPlan),
      planRevision,
      conflicts: data.conflicts || rawPlan.conflicts || [],
      error: null,
    }
  } catch (err) {
    return {
      ok: false,
      plan: null,
      planRevision: null,
      conflicts: [],
      error: err?.message || 'Failed to load session plan',
    }
  }
}

/** Save session plan to backend API with compare-and-swap expected_revision.
 *
 *  Requires a valid plan and non-null expectedRevision.
 *  Returns { ok: true, planRevision, conflicts, error: null } on success,
 *  or { ok: false, error, planRevision: expectedRevision } on CAS conflict or failure.
 *  Strictly requires a valid plan_revision returned by backend; never invents expectedRevision + 1.
 */
export async function executeSavePlan(sessionId, plan, expectedRevision, api) {
  if (!plan || typeof expectedRevision !== 'number' || expectedRevision < 0) {
    return {
      ok: false,
      error: 'Cannot save plan: no valid plan draft loaded',
      planRevision: typeof expectedRevision === 'number' ? expectedRevision : null,
      conflicts: [],
    }
  }

  try {
    const payload = buildPlanSavePayload(plan, expectedRevision)
    const res = await api.post(`/api/sessions/${sessionId}/plan`, payload)
    if (!res || typeof res.plan_revision !== 'number' || res.plan_revision < 0) {
      return {
        ok: false,
        error: 'Save failed: backend did not provide a valid plan_revision',
        planRevision: expectedRevision,
        conflicts: [],
      }
    }
    return {
      ok: true,
      planRevision: res.plan_revision,
      conflicts: res.conflicts || [],
      error: null,
    }
  } catch (err) {
    return {
      ok: false,
      error: err?.message || 'Failed to save plan draft',
      planRevision: expectedRevision,
      conflicts: [],
    }
  }
}

/** Guard: check whether the resource plan is in a valid loaded state ready for preparation. */
export function canPreparePlan(planState) {
  return Boolean(
    planState &&
    planState.plan &&
    typeof planState.planRevision === 'number' &&
    planState.planRevision >= 0
  )
}

/** Guard: check whether the resource session plan is authorized to proceed from Review to Generation.
 *
 *  For legacy sessions: returns true.
 *  For resource-v1 sessions:
 *  Requires:
 *  1. A valid draft loaded with a non-negative integer planRevision (canPreparePlan).
 *  2. No unsaved local changes (planDirty must be false).
 *  3. Zero unresolved resource/constant conflicts.
 *  4. At least one planned take.
 *  5. A valid workflow assigned (on the session, model, or settings).
 */
export function canProceedToGeneration(session, planState = {}) {
  if (!isResourceSession(session)) return true
  if (!canPreparePlan(planState)) return false
  if (planState.planDirty) return false

  const conflicts = planState.conflicts || planState.plan?.conflicts || []
  if (conflicts.length > 0) return false

  if (!planState.plan?.takes || planState.plan.takes.length === 0) return false

  const hasWorkflow = Boolean(
    session?.workflow_id ||
    session?.model?.workflow_id ||
    session?.settings?.workflow_id
  )
  if (!hasWorkflow) return false

  return true
}

/** Guard: check whether the session has materialized shots ready and is permitted to execute via Run (POST /run).
 *
 *  A saved or reviewed plan by itself is NEVER permission to generate; POST /api/sessions/{id}/run
 *  only starts the runner when there are pending shots. With zero pending shots, /run is rejected.
 *
 *  For legacy sessions:
 *  Requires session not running, and pending > 0.
 *
 *  For resource-v1 sessions:
 *  Requires:
 *  1. Session is not running (session.status !== 'running').
 *  2. Real materialized pending shots exist (pending > 0).
 *  3. canProceedToGeneration is satisfied (!planDirty, 0 conflicts, takes > 0, workflow assigned).
 *  4. Review step was completed for the current revision (reviewedRevision === planRevision).
 *  5. Active in generation step (activeStep === 'generation').
 */
export function canGenerateSession(session, planState = {}) {
  if (session?.status === 'running') return false

  const pending = typeof planState.pending === 'number'
    ? planState.pending
    : (Array.isArray(session?.shots) ? session.shots.filter((x) => x.status === 'pending').length : 0)

  if (pending <= 0) return false

  if (!isResourceSession(session)) return true

  if (!canProceedToGeneration(session, planState)) return false

  const isReviewed = typeof planState.reviewedRevision === 'number' &&
    planState.reviewedRevision === planState.planRevision &&
    planState.activeStep === 'generation'

  return isReviewed
}

/** Guard: check whether a legacy control should be visible. */
export function isLegacyControlVisible(session, controlName) {
  if (isResourceSession(session)) return false
  return true
}

/** Guard: check whether the session path requires measured Catalogue or Judge. */
export function requiresCatalogueOrJudge(session) {
  return !isResourceSession(session)
}

/** Extract accessible advanced settings according to design.md.
 *  Reuses existing workflow, checkpoint profiles, and sampler/scheduler settings.
 */
export function getAdvancedSettings(session, config = {}, workflows = []) {
  const settings = session?.settings || {}
  const checkpoint = settings.checkpoint || session?.checkpoint || ''
  return {
    workflow_id: session?.workflow_id ?? null,
    reference_workflow_id: session?.reference_workflow_id ?? null,
    checkpoint,
    lora_strength: settings.lora_strength ?? 1.0,
    steps: settings.steps ?? null,
    cfg: settings.cfg ?? null,
    sampler: settings.sampler || '',
    scheduler: settings.scheduler || '',
    denoise: settings.denoise ?? null,
    width: settings.width ?? 1024,
    height: settings.height ?? 1024,
  }
}

/** Screen controller modeling the SessionView contract and state machine.
 *
 *  Coordinates session loading, mode resolution, plan draft CAS persistence,
 *  step transitions, error handling, and control visibility.
 */
export function createSessionViewController(sessionId, { api, config = {} } = {}) {
  let session = null
  let plan = null
  let planRevision = null
  let planConflicts = []
  let planDirty = false
  let planNotice = ''
  let activeStep = 'character'
  let reviewedRevision = null
  let error = ''
  let settingsOpen = false
  const listeners = new Set()

  function notify() {
    const st = getState()
    for (const listener of listeners) {
      listener(st)
    }
  }

  function getState() {
    const isResource = isResourceSession(session)
    const pending = Array.isArray(session?.shots) ? session.shots.filter((x) => x.status === 'pending').length : 0
    const planState = {
      plan,
      planRevision,
      planDirty,
      conflicts: planConflicts,
      activeStep,
      reviewedRevision,
      pending,
    }
    return {
      session,
      isResource,
      plan,
      planRevision,
      planConflicts,
      planDirty,
      planNotice,
      activeStep,
      reviewedRevision,
      error,
      settingsOpen,
      canPrepare: isResource ? canPreparePlan(planState) : true,
      canProceedToGeneration: canProceedToGeneration(session, planState),
      canGenerate: canGenerateSession(session, planState),
    }
  }

  async function reload() {
    try {
      const data = await api.get(`/api/sessions/${sessionId}`)
      session = data
      if (isResourceSession(data)) {
        const planRes = await loadSessionPlan(sessionId, api)
        if (planRes.ok) {
          plan = planDirty && plan ? plan : planRes.plan
          planRevision = planDirty && planRevision !== null ? planRevision : planRes.planRevision
          planConflicts = planRes.conflicts
          if (!planDirty) {
            reviewedRevision = null
          }
          error = ''
        } else {
          plan = null
          planRevision = null
          planConflicts = []
          reviewedRevision = null
          error = planRes.error
        }
      } else {
        plan = null
        planRevision = null
        planConflicts = []
        reviewedRevision = null
        error = ''
      }
      notify()
      return getState()
    } catch (err) {
      error = err?.message || 'Failed to load session'
      notify()
      return getState()
    }
  }

  async function savePlan() {
    if (!plan || planRevision === null) {
      error = 'Cannot save plan: no valid plan draft loaded'
      notify()
      return { ok: false, error }
    }
    const res = await executeSavePlan(sessionId, plan, planRevision, api)
    if (res.ok) {
      planRevision = res.planRevision
      planConflicts = res.conflicts
      planDirty = false
      reviewedRevision = null
      planNotice = `Plan saved (revision ${res.planRevision})`
      notify()
      return res
    } else {
      error = res.error
      notify()
      return res
    }
  }

  function navigateStep(step) {
    const isResource = isResourceSession(session)
    if (isResource) {
      if (!canPreparePlan({ plan, planRevision })) {
        error = 'Cannot navigate steps: plan draft is incomplete or missing'
        notify()
        return false
      }
      if (step === 'generation') {
        if (activeStep !== 'review' && activeStep !== 'generation') {
          error = 'Cannot skip to generation: review step must be completed first'
          notify()
          return false
        }
        if (planDirty) {
          error = 'Cannot proceed to generation: plan has unsaved changes'
          notify()
          return false
        }
        const conflicts = (planConflicts && planConflicts.length > 0) || (plan?.conflicts && plan.conflicts.length > 0)
        if (conflicts) {
          error = 'Cannot proceed to generation: unresolved resource conflicts require review'
          notify()
          return false
        }
        if (!plan?.takes || plan.takes.length === 0) {
          error = 'Cannot proceed to generation: plan must contain at least one take'
          notify()
          return false
        }
        const hasWorkflow = Boolean(session?.workflow_id || session?.model?.workflow_id || session?.settings?.workflow_id)
        if (!hasWorkflow) {
          error = 'Cannot proceed to generation: session has no workflow assigned'
          notify()
          return false
        }
        reviewedRevision = planRevision
      }
    }
    activeStep = step
    error = ''
    notify()
    return true
  }

  function editConstants(newLook, newInitialWardrobe) {
    if (!canPreparePlan({ plan, planRevision })) {
      error = 'Cannot edit constants: plan draft is incomplete or missing'
      notify()
      return false
    }
    plan = {
      ...plan,
      look: typeof newLook === 'string' ? newLook : plan.look,
      initial_wardrobe: typeof newInitialWardrobe === 'string' ? newInitialWardrobe : plan.initial_wardrobe,
    }
    planDirty = true
    reviewedRevision = null
    notify()
    return true
  }

  function addTake(defaults = {}) {
    if (!canPreparePlan({ plan, planRevision })) {
      error = 'Cannot add take: plan draft is incomplete or missing'
      notify()
      return false
    }
    plan = {
      ...plan,
      takes: createTake(plan.takes || [], defaults),
    }
    planDirty = true
    reviewedRevision = null
    notify()
    return true
  }

  function editTake(takeId, changes = {}) {
    if (!canPreparePlan({ plan, planRevision })) {
      error = 'Cannot edit take: plan draft is incomplete or missing'
      notify()
      return false
    }
    plan = {
      ...plan,
      takes: updateTake(plan.takes || [], takeId, changes),
    }
    planDirty = true
    reviewedRevision = null
    notify()
    return true
  }

  function deleteTake(takeId) {
    if (!canPreparePlan({ plan, planRevision })) {
      error = 'Cannot delete take: plan draft is incomplete or missing'
      notify()
      return false
    }
    plan = {
      ...plan,
      takes: removeTake(plan.takes || [], takeId),
    }
    planDirty = true
    reviewedRevision = null
    notify()
    return true
  }

  function isControlVisible(controlName) {
    const isResource = isResourceSession(session)
    const pending = Array.isArray(session?.shots) ? session.shots.filter((x) => x.status === 'pending').length : 0
    const planState = {
      plan,
      planRevision,
      planDirty,
      conflicts: planConflicts,
      activeStep,
      reviewedRevision,
      pending,
    }
    if (isResource) {
      if (['compose', 'fill_cell', 'add_shots', 'catalogue_outfit', 'wardrobe_states_textarea'].includes(controlName)) {
        return false
      }
      if (['character_step', 'constants_step', 'takes_step', 'review_step', 'generation_step'].includes(controlName)) {
        return canPreparePlan({ plan, planRevision })
      }
      if (controlName === 'incomplete_plan_notice') {
        return !canPreparePlan({ plan, planRevision })
      }
      if (controlName === 'run_button') {
        return canGenerateSession(session, planState)
      }
      if (controlName === 'proceed_to_generation_button') {
        return canProceedToGeneration(session, planState)
      }
      if (['settings_button', 'workflow_select', 'reference_workflow_select', 'base_model_select', 'sampler_select', 'scheduler_select'].includes(controlName)) {
        return true
      }
    } else {
      if (['compose', 'fill_cell', 'add_shots', 'catalogue_outfit', 'wardrobe_states_textarea'].includes(controlName)) {
        return true
      }
      if (['character_step', 'constants_step', 'takes_step', 'review_step', 'generation_step', 'incomplete_plan_notice'].includes(controlName)) {
        return false
      }
      if (controlName === 'run_button') {
        return canGenerateSession(session, planState)
      }
      if (['settings_button', 'workflow_select', 'reference_workflow_select', 'base_model_select', 'sampler_select', 'scheduler_select'].includes(controlName)) {
        return true
      }
    }
    return true
  }

  return {
    getState,
    reload,
    savePlan,
    navigateStep,
    editConstants,
    addTake,
    editTake,
    deleteTake,
    isControlVisible,
    toggleSettings: () => { settingsOpen = !settingsOpen; notify() },
    subscribe(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
  }
}
