import React, { useEffect, useRef, useState } from 'react'
import { api, shotImage } from '../api'
import { go } from '../App.jsx'
import ShotsEditor, { blankShot } from './ShotsEditor.jsx'
import AnglePicker from './AnglePicker.jsx'
import ExpressionPicker from './ExpressionPicker.jsx'
import { BaseModelSelect, SamplerSelect } from './Models.jsx'
import SessionLookProgression from './SessionLookProgression.jsx'
import { KINDS, forKind, sessionKind, checkpointProfile, profileSummary,
         RUN_SUBJECTS, missingSubjects } from '../kinds.js'
import { candidatePool, defaultCount, extrasFor, fillCellDefaultCount } from '../compose.js'
import { composed, spread } from '../enhance.js'
import { arcFor, outfits, statesFor } from '../wardrobe.js'
import {
  isResourceSession,
  normalizePlan,
  createTake,
  updateTake,
  removeTake,
  reorderTakes,
  setWardrobeChange,
  removeWardrobeChange,
  resolveEffectiveWardrobes,
  resolveEffectiveWardrobeDetails,
  computePlanChangeImpact,
  WARDROBE_SCOPE_THIS_TAKE,
  WARDROBE_SCOPE_FROM_HERE,
  buildPlanSavePayload,
  loadSessionPlan,
  loadPlanReviews,
  executeSavePlan,
  canPreparePlan,
  canProceedToGeneration,
  canGenerateSession,
  isLegacyControlVisible,
  getTakePreparationState,
  hasGeneratedTakeHistory,
  loadTakeReview,
  approvePlanReview,
  recordTakeAdaptation,
  prepareTake,
  preparePlanTakes,
  submitPreparedTake,
  submitSelectedTakes,
} from '../sessionPlan.js'

const MAX_AUTOMATIC_TAKE_BATCH = 20
const AUTHORING_OPERATION_STATES = new Set([
  'active', 'cancel_requested', 'succeeded', 'failed', 'cancelled', 'expired',
])
const hasStableAuthoringError = (error, code) => (
  Number.isInteger(error?.status) && error.status >= 400 && error.status < 500
  && error?.hasStableErrorBody === true
  && (!code || error?.detail?.code === code)
)

const authoringOperationStorageKey = (sessionId, planRevision) => (
  `idevgen:authoring-operation:${sessionId}:${planRevision}`
)
const authoringOperationPointerKey = (sessionId) => (
  `idevgen:authoring-operation:${sessionId}:latest`
)

const hasOwn = (value, key) => (
  value !== null && typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, key)
)

const errorDetailMessage = (detail) => {
  if (typeof detail === 'string') return detail
  if (detail === undefined || detail === null) return ''
  try { return JSON.stringify(detail, null, 2) } catch { return String(detail) }
}

const isAuthoringEvidenceInvalid = (failure) => {
  const detail = failure?.detail
  return detail?.code === 'authoring_evidence_invalid'
    || (typeof detail === 'string' && detail.trim().startsWith('authoring_evidence_invalid:'))
}

const takeIdSummary = (takeIds) => takeIds.length ? takeIds.join(', ') : 'None'

const readRememberedAuthoringOperation = (sessionId, planRevision) => {
  try {
    const pointer = window.localStorage.getItem(authoringOperationPointerKey(sessionId))
    const pointedRevision = pointer !== null && /^\d+$/.test(pointer) ? Number(pointer) : null
    let storedRevision = Number.isInteger(pointedRevision) ? pointedRevision : planRevision
    let raw = window.localStorage.getItem(authoringOperationStorageKey(sessionId, storedRevision))
    if (!raw && storedRevision !== planRevision) {
      storedRevision = planRevision
      raw = window.localStorage.getItem(authoringOperationStorageKey(sessionId, storedRevision))
    }
    const saved = raw ? JSON.parse(raw) : null
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return null
    if (typeof saved.operation_id === 'string' && saved.operation_id) {
      return { operation_id: saved.operation_id, plan_revision: storedRevision }
    }
    const request = saved.request
    if (!request || typeof request !== 'object' || Array.isArray(request)) return null
    const keys = Object.keys(request).sort()
    const expectedKeys = request.kind === 'prepare_takes'
      ? ['expected_revision', 'kind', 'request_id', 'take_ids']
      : ['expected_revision', 'kind', 'request_id']
    if (
      keys.length !== expectedKeys.length
      || keys.some((key, index) => key !== expectedKeys[index])
      || typeof request.request_id !== 'string'
      || !/^[0-9a-f-]{36}$/i.test(request.request_id)
      || !Number.isInteger(request.expected_revision)
      || request.expected_revision !== storedRevision
      || !['shared_suggestions', 'prepare_takes'].includes(request.kind)
      || (request.kind === 'prepare_takes' && (
        !Array.isArray(request.take_ids)
        || request.take_ids.length < 1
        || request.take_ids.length > MAX_AUTOMATIC_TAKE_BATCH
        || request.take_ids.some((takeId) => typeof takeId !== 'string' || !takeId)
        || new Set(request.take_ids).size !== request.take_ids.length
      ))
    ) return null
    return { request, plan_revision: storedRevision }
  } catch {
    return null
  }
}

const rememberAuthoringOperation = (sessionId, planRevision, record) => {
  try {
    window.localStorage.setItem(
      authoringOperationStorageKey(sessionId, planRevision),
      JSON.stringify(record),
    )
    const pointerKey = authoringOperationPointerKey(sessionId)
    const pointer = window.localStorage.getItem(pointerKey)
    const pointedRevision = pointer !== null && /^\d+$/.test(pointer) ? Number(pointer) : null
    if (!Number.isInteger(pointedRevision) || planRevision >= pointedRevision) {
      window.localStorage.setItem(pointerKey, String(planRevision))
    }
  } catch { /* Browser storage is only a recovery hint; the backend owns operation state. */ }
}

const forgetAuthoringOperation = (sessionId, planRevision) => {
  try {
    window.localStorage.removeItem(authoringOperationStorageKey(sessionId, planRevision))
    const pointerKey = authoringOperationPointerKey(sessionId)
    if (window.localStorage.getItem(pointerKey) === String(planRevision)) {
      window.localStorage.removeItem(pointerKey)
    }
  } catch { /* Optional recovery hint. */ }
}

const isAuthoringOperationView = (view, sessionId) => {
  const viewKeys = [
    'can_cancel', 'can_resume', 'created_at', 'error', 'kind', 'lease_expires_at',
    'operation_id', 'plan_revision', 'progress', 'result', 'session_id', 'state', 'updated_at',
  ].sort()
  if (
    !view || typeof view !== 'object' || Array.isArray(view)
    || Object.keys(view).sort().join('|') !== viewKeys.join('|')
    || typeof view.operation_id !== 'string' || !view.operation_id
    || String(view.session_id) !== String(sessionId)
    || !Number.isInteger(view.plan_revision) || view.plan_revision < 0
    || !['shared_suggestions', 'prepare_takes'].includes(view.kind)
    || !AUTHORING_OPERATION_STATES.has(view.state)
    || typeof view.created_at !== 'string' || typeof view.updated_at !== 'string'
    || !(view.lease_expires_at === null || typeof view.lease_expires_at === 'string')
    || !(view.error === null || typeof view.error === 'string')
    || !(view.result === null || (typeof view.result === 'object' && !Array.isArray(view.result)))
    || typeof view.can_cancel !== 'boolean' || typeof view.can_resume !== 'boolean'
  ) return false

  const progress = view.progress
  const progressKeys = ['completed', 'failed', 'remaining', 'requested']
  if (
    !progress || typeof progress !== 'object' || Array.isArray(progress)
    || Object.keys(progress).sort().join('|') !== progressKeys.join('|')
    || ['requested', 'completed', 'remaining'].some((key) => (
      !Array.isArray(progress[key]) || progress[key].some((takeId) => typeof takeId !== 'string')
    ))
  ) return false
  if (progress.failed === null) return true
  return Boolean(
    progress.failed && typeof progress.failed === 'object' && !Array.isArray(progress.failed)
    && Object.keys(progress.failed).sort().join('|') === 'error|take_id'
    && typeof progress.failed.take_id === 'string'
    && typeof progress.failed.error === 'string'
  )
}

/** The wardrobe the shoot passes through, in order — the arc a composed run is
 *  dealt, one state per photograph after `spread`.
 *
 *  It lives in the session's `settings` blob for the same reason `kind` does: it
 *  is read by this screen and never by the runner, so it needs no column and no
 *  route of its own. Empty (the default, and every session written before the
 *  composer could undress) means the session's one wardrobe, in every
 *  photograph.
 */
/*  Two sources, one of them derived. `settings.outfit` names an outfit in the
 *  wardrobe catalogue and the arc comes out of its garment order — N garments,
 *  N+1 states, each naming only what she is still wearing. `wardrobe_states` is
 *  the hand-typed override, and it wins when it has anything in it: a shoot that
 *  needs a state the catalogue cannot derive should not have to leave the
 *  catalogue to get one.
 */
const wardrobeArc = (session) => {
  const typed = (session?.settings?.wardrobe_states || []).filter((line) => line.trim())
  // A typed state is prose and has no garment list behind it, so it carries no
  // answer about access: `null`, and the run's `bare` checkbox decides it the way
  // it did before the catalogue existed. Guessing from the words would be a
  // second answer to a question `crop.lowest_named` already answers for the
  // stages that come from an outfit.
  return typed.length ? typed.map((text) => ({ text, access: null })) : arcFor(session?.settings?.outfit)
}

const wardrobeStates = (session) => wardrobeArc(session).map((s) => s.text)

/** A checkpoint's name for a session title: no folder, no extension. Three copies
 *  called "shoot (copy)" are three copies you have to open to tell apart. */
const modelStem = (checkpoint) =>
  (checkpoint || '').split(/[\\/]/).pop().replace(/\.[^.]+$/, '') || 'copy'

export default function SessionView({
  id,
  initialSession = null,
  initialPlan = null,
  initialRevision = null,
  initialError = '',
  initialActiveStep = 'character',
  initialReviewedRevision = null,
  initialConflicts = [],
  initialPreparation = null,
  initialSharedSummary = null,
  initialExpandedTakeId = null,
  initialTakeReviewData = {},
}) {
  const [s, setS] = useState(initialSession)
  const [error, setError] = useState(initialError)
  const [filter, setFilter] = useState('all')
  const [zoom, setZoom] = useState(null)
  const [split, setSplit] = useState(50)
  const [adding, setAdding] = useState(null)
  // The wardrobe the add-shots panel is working from. Its own state because it is
  // editable there and only written back on Add.
  const [worn, setWorn] = useState('')
  const [workflows, setWorkflows] = useState([])
  const [baseModels, setBaseModels] = useState({})
  const [settingsOpen, setSettingsOpen] = useState(false)
  // The copy being set up: null when the panel is closed.
  const [clone, setClone] = useState(null)
  // Every session, for the copies-of-this-shoot list, and the one picked to
  // compare against, loaded whole because the comparison needs its shots.
  const [sessions, setSessions] = useState([])
  const [twinId, setTwinId] = useState(0)
  const [twin, setTwin] = useState(null)
  // The whole config, not just `llm_ok`: it also carries the per-checkpoint
  // profiles that picking a base model fills in from.
  const [config, setConfig] = useState({})
  // The tag editor's draft input. A PATCH fires on submit so the network
  // round-trip is one per tag, not one per keystroke, and on remove so each
  // click is its own action with its own undo.
  const [tagDraft, setTagDraft] = useState('')
  // The rooms this planned run would be refused in, asked for before it is
  // sent. Null until asked: an empty report and no report are different
  // answers, and only one of them is worth a line on the screen.
  const [roomReport, setRoomReport] = useState(null)
  // Open when the user starts typing; close on blur once the field is empty
  // again, so a session with no tags does not eat a row of vertical space.
  const [tagsOpen, setTagsOpen] = useState(false)
  // The compose-run control. `mode` defaults to "exploratory" rather than
  // "strict" because the cell table holds 17 rows and two verified trios on
  // the current checkpoint, and a strict default makes the first use of
  // this feature a 422 — which the operator reads as broken. Exploratory
  // draws unknown and verified cells, never dead ones, so the first click
  // queues a real shot; strict is one click away when the operator has
  // measured enough to want it.
  // Opens on the largest run the no-repeat rule allows for this manner
  // (see `defaultCount`), so the first click is not a 422 for the same
  // reason the mode defaults to exploratory. The initialiser runs once,
  // before the session has loaded, so it reads the `directed` fallback —
  // which is the right number for every manner today because the binding
  // slot is the act list and that list is shared. A manner-specific act
  // catalogue would make this stale and it would have to move to an effect.
  const [composeCount, setComposeCount] = useState(() => defaultCount(s?.manner))
  const [composeMode, setComposeMode] = useState('exploratory')
  // Compose the line WITHOUT the session's wardrobe, so a reference can deliver
  // the clothing instead. Measured 2026-08-31: a written garment beats a
  // reference card 0/9 at every strength, and struck out it lands 3/3. One
  // checkbox for both compose controls — the switch is a property of the take,
  // not of which button queued it. Off by default: with no reference attached
  // a silent line renders her undressed.
  const [muteWardrobe, setMuteWardrobe] = useState(false)
  // Whether a second person is in the room for the next composed run. Off is
  // the arc's own default: an act that needs him is not drawn at all, so a
  // photograph dealt a dressed wardrobe state cannot come back as penetration
  // — measured on session 330, three photographs of nine.
  const [withHim, setWithHim] = useState(false)
  // Whether the room has furniture in it. Same shape as `withHim`: a property of
  // the run, so it narrows the pool once. No field of the prompt describes the
  // room at all — the sampler invents one — so an act that names a chair or a bed
  // BUILDS it, and off is the honest default for a look that has no such piece.
  const [withFurniture, setWithFurniture] = useState(false)
  // The three subjects a run can switch on, in the same shape as the two above:
  // off by default, a property of the RUN. They are the writer's rather than the
  // draw's - a tattoo is something the LINE says - so they travel to
  // `sessionFromBrief` instead of onto the compose payload, and each carries the
  // words the operator wants used. The flag alone is not enough: a switch with
  // nothing behind it is an invitation to invent, which is what it was turned on
  // to prevent, so the writer refuses a run that is short of one.
  const [subjects, setSubjects] = useState({
    with_tattoo: false, tattoo: '', with_pet: false, pet: '',
    with_liquids: false, liquids: '',
  })
  const subjectShort = missingSubjects(subjects)
  // Whether she is undressed for the next composed run. Same shape as `withHim`
  // and the same reason: an act that needs her bare (a toy, a hand between her
  // legs) is not drawn at all unless the run says so, because dealing one to a
  // stage still in a sweatshirt is the contradiction `with him` exists to stop.
  // It was on the payload from the day the flag shipped and on no screen, so
  // the seven solo acts of the candid catalogue were undrawable from this
  // button while the endpoint was happy to draw them.
  const [bare, setBare] = useState(false)
  // Shoot the composed takes through the session's reference graph. Its own
  // switch and not a consequence of the one above: guiding the body while the
  // line still writes the clothes is a real take, and one flag carrying two
  // meanings is how use_reference grew three of them.
  const [composeReference, setComposeReference] = useState(false)
  // Read through the session, not off the checkbox alone: clearing the
  // reference workflow after ticking it would otherwise still send the flag,
  // and the row would record a guided take the runner painted from noise.
  const composeGuided = composeReference && !!s?.reference_workflow_id
  // The fill-cell control: pick one trio (camera, act, framing),
  // pick a count, queue N photographs of that trio on
  // THIS session. The picker reads the same catalogue slice the
  // Compose control reads (`candidatePool(manner)`) — no second
  // pool, no second source of truth. Initial state opens on the
  // first camera and first act of the slice and the threshold
  // count (10) a cell needs to reach verified or dead. The
  // `s?.manner` initialiser runs before the session has loaded
  // and falls back to the `directed` slice, which is the right
  // first paint while we wait: the catalogue re-resolves on
  // every press.
  const [fillCellCamera, setFillCellCamera] = useState(() => [candidatePool(s?.manner).camera[0]?.key || 'none'])
  const [fillCellAct, setFillCellAct] = useState(() => [candidatePool(s?.manner).act[0]?.key || 'none'])
  const [fillCellFraming, setFillCellFraming] = useState(() => [candidatePool(s?.manner).framing[0]?.key || 'none'])
  const [fillCellMode, setFillCellMode] = useState('exploratory')
  const [fillCellCount, setFillCellCount] = useState(() => fillCellDefaultCount())
  const llm = !!config.llm_ok

  const [plan, setPlan] = useState(initialPlan)
  const [savedPlan, setSavedPlan] = useState(initialPlan)
  const [planSessionId, setPlanSessionId] = useState(() => (
    initialPlan && String(initialSession?.id) === String(id) ? String(id) : ''
  ))
  const [planRevision, setPlanRevision] = useState(initialRevision)
  const planRevisionRef = useRef(initialRevision)
  planRevisionRef.current = planRevision
  const [planConflicts, setPlanConflicts] = useState(initialConflicts)
  const [planPreparation, setPlanPreparation] = useState(initialPreparation)
  const [sharedSummary, setSharedSummary] = useState(initialSharedSummary)
  const [sharedOperation, setSharedOperation] = useState(null)
  const [sharedProposalDrafts, setSharedProposalDrafts] = useState({})
  const [sharedActionError, setSharedActionError] = useState('')
  const [sharedActionNotice, setSharedActionNotice] = useState('')
  const [sharedStartBusy, setSharedStartBusy] = useState(false)
  const [sharedStartUnknown, setSharedStartUnknown] = useState(false)
  const [sharedAcceptBusy, setSharedAcceptBusy] = useState(false)
  const [sharedAcceptUnknown, setSharedAcceptUnknown] = useState(false)
  const [preparationActionBusy, setPreparationActionBusy] = useState(false)
  const [preparationStartUnknown, setPreparationStartUnknown] = useState(false)
  const [preparationActionError, setPreparationActionError] = useState('')
  const [preparationActionCode, setPreparationActionCode] = useState('')
  const [manualDecisionBusy, setManualDecisionBusy] = useState(false)
  const [lookProgressionBusy, setLookProgressionBusy] = useState(false)
  const [expandedTakeFields, setExpandedTakeFields] = useState({})
  const [planDirty, setPlanDirty] = useState(false)
  const planDirtyRef = useRef(false)
  planDirtyRef.current = planDirty
  const sharedOperationRef = useRef(null)
  sharedOperationRef.current = sharedOperation
  const sharedOperationEpochRef = useRef(0)
  const sharedStartRequestRef = useRef(null)
  const sharedAcceptanceRequestRef = useRef(null)
  const preparationStartRequestRef = useRef(null)
  const authoringActionInFlightRef = useRef(false)
  const authoringOperationRefreshRef = useRef(null)
  const restoredOperationKeyRef = useRef(null)
  const sessionViewMountedRef = useRef(false)
  const sessionViewEpochRef = useRef(0)
  const sessionViewIdRef = useRef(id)
  sessionViewIdRef.current = id
  const [sessionRequestEpoch, setSessionRequestEpoch] = useState(0)
  const [planNotice, setPlanNotice] = useState('')
  const [planSaveImpact, setPlanSaveImpact] = useState(null)
  const [planReviewFailure, setPlanReviewFailure] = useState(null)
  const [resourceRefreshBusy, setResourceRefreshBusy] = useState(false)
  const [resourceRefreshOutcome, setResourceRefreshOutcome] = useState(null)
  const [activeStep, setActiveStep] = useState(initialActiveStep)
  const [reviewedRevision, setReviewedRevision] = useState(initialReviewedRevision)
  const [selectedTakeIds, setSelectedTakeIds] = useState(() => new Set())
  const [submittingTakes, setSubmittingTakes] = useState(false)
  const [expandedTakeId, setExpandedTakeId] = useState(initialExpandedTakeId)
  const [takeReviewData, setTakeReviewData] = useState(initialTakeReviewData)
  const hasKnownStaleAdaptations = Object.values(takeReviewData || {}).some(
    (review) => (review?.stale_adaptations || []).length > 0,
  )
  const planConflictMarkers = Array.isArray(planConflicts) && planConflicts.length > 0
    ? planConflicts
    : (Array.isArray(plan?.conflicts) ? plan.conflicts : [])
  const hasPlanConflictMarkers = planConflictMarkers.length > 0
  const staleAdaptationCount = Object.values(takeReviewData || {}).reduce(
    (count, review) => count + (review?.stale_adaptations || []).length,
    0,
  )
  const [planReviewStatus, setPlanReviewStatus] = useState('idle')
  const [reviewRefreshCounter, setReviewRefreshCounter] = useState(0)
  const [takeReviewLoading, setTakeReviewLoading] = useState({})
  const [takeReviewError, setTakeReviewError] = useState({})
  const [adaptationDrafts, setAdaptationDrafts] = useState({})
  const [preparingTakeId, setPreparingTakeId] = useState(null)
  const [preparingAll, setPreparingAll] = useState(false)

  const markPlanDirty = () => {
    setPlanDirty(true)
    setReviewedRevision(null)
    setSelectedTakeIds(new Set())
  }

  const isResource = isResourceSession(s)
  const planSessionCurrent = planSessionId === String(id)
  const sharedAcceptancePending = sharedAcceptBusy || sharedAcceptUnknown
  const planEditLocked = manualDecisionBusy || sharedAcceptancePending || lookProgressionBusy || !planSessionCurrent
  const isCurrentSessionRequest = (sessionId, requestEpoch) => (
    sessionViewMountedRef.current
    && sessionViewEpochRef.current === requestEpoch
    && String(sessionViewIdRef.current) === String(sessionId)
  )
  const reviewBlocksFinalization = hasKnownStaleAdaptations
    || planReviewStatus === 'error'
    || (isResource && activeStep === 'review' && planReviewStatus !== 'ready')
  const canRefreshResourceDependencies = isResource && !planDirty && planRevision !== null
    && (isAuthoringEvidenceInvalid(planReviewFailure) || hasKnownStaleAdaptations)

  const reload = () => {
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    return api.get(`/api/sessions/${sessionId}`).then((data) => {
    if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
    setS(data)
    if (isResourceSession(data)) {
      loadSessionPlan(sessionId, api).then((res) => {
        if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
        if (res.ok) {
          if (typeof res.planRevision !== 'number' || res.planRevision < 0) {
            setPlanSessionId('')
            setPlan(null)
            setSavedPlan(null)
            setPlanRevision(null)
            setPlanConflicts([])
            setPlanPreparation(null)
            setSharedSummary(null)
            setReviewedRevision(null)
            setSelectedTakeIds(new Set())
            setTakeReviewData({})
            setError('Loaded draft missing valid plan_revision from backend')
            return
          }
          if (!planDirtyRef.current && planRevisionRef.current !== res.planRevision) {
            if (sharedOperationRef.current) {
              setPreparationActionCode('plan_revision_stale')
              setPreparationActionError('The saved plan changed. Reload the saved plan before resuming or starting preparation.')
              setSharedProposalDrafts({})
            }
          }
          setPlan((prev) => (planDirtyRef.current && prev ? prev : res.plan))
          setPlanRevision((prev) => (planDirtyRef.current && prev !== null ? prev : res.planRevision))
          if (!planDirtyRef.current) {
            setSavedPlan(res.plan)
            setPlanSessionId(String(sessionId))
          }
          setPlanConflicts(res.conflicts)
          setPlanPreparation(res.preparation || null)
          if (!planDirtyRef.current) setSharedSummary(res.sharedSummary || null)
          setSelectedTakeIds(new Set())
          setTakeReviewData({})
          setPlanReviewStatus('idle')
          setReviewRefreshCounter((count) => count + 1)
          if (!planDirtyRef.current) {
            const hasResourceMarkers = [res.conflicts, res.plan?.conflicts]
              .some((markers) => Array.isArray(markers) && markers.length > 0)
            setReviewedRevision((prev) => {
              if (typeof res.reviewedRevision === 'number') return res.reviewedRevision
              if (hasResourceMarkers) return null
              return prev && prev === res.planRevision ? prev : null
            })
          }
        } else {
          setPlanSessionId('')
          setPlan(null)
          setSavedPlan(null)
          setPlanRevision(null)
          setPlanConflicts([])
          setPlanPreparation(null)
          setSharedSummary(null)
          setReviewedRevision(null)
          setSelectedTakeIds(new Set())
          setTakeReviewData({})
          setPlanReviewStatus('idle')
          setError(res.error)
        }
      }).catch((e) => {
        if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
        setPlanSessionId('')
        setPlan(null)
        setSavedPlan(null)
        setPlanRevision(null)
        setPlanConflicts([])
        setPlanPreparation(null)
        setSharedSummary(null)
        setReviewedRevision(null)
        setSelectedTakeIds(new Set())
        setTakeReviewData({})
        setPlanReviewStatus('idle')
        setError(e?.message || 'Failed to load plan')
      })
    } else {
      setPlanSessionId('')
    }
    }).catch((e) => {
      if (isCurrentSessionRequest(sessionId, requestEpoch)) {
        setPlanSessionId('')
        setError(e.message)
      }
    })
  }

  const resetSharedOperation = () => {
    sharedOperationEpochRef.current += 1
    sharedOperationRef.current = null
    sharedStartRequestRef.current = null
    sharedAcceptanceRequestRef.current = null
    setSharedOperation(null)
    setSharedProposalDrafts({})
    setSharedStartBusy(false)
    setSharedStartUnknown(false)
    setSharedAcceptBusy(false)
    setSharedAcceptUnknown(false)
  }

  const adoptAuthoritativePlan = (loaded, session) => {
    if (!loaded?.ok || planDirtyRef.current) return false
    if (planRevisionRef.current !== loaded.planRevision && sharedOperationRef.current) {
      setPreparationActionCode('plan_revision_stale')
      setPreparationActionError('The saved plan changed. Reload the saved plan before resuming or starting preparation.')
      setSharedProposalDrafts({})
    }
    setPlan(loaded.plan)
    setSavedPlan(loaded.plan)
    setPlanSessionId(String(id))
    setPlanRevision(loaded.planRevision)
    planRevisionRef.current = loaded.planRevision
    setPlanConflicts(loaded.conflicts || [])
    setPlanPreparation(loaded.preparation || null)
    setSharedSummary(loaded.sharedSummary || null)
    setPlanDirty(false)
    planDirtyRef.current = false
    setReviewedRevision(loaded.reviewedRevision ?? null)
    setSelectedTakeIds(new Set())
    setTakeReviewData({})
    setPlanReviewStatus('idle')
    if (session) setS(session)
    return true
  }

  const reloadAuthoritativePlan = async (
    sessionId,
    minimumRevision = 0,
    expectedSessionEpoch = sessionViewEpochRef.current,
  ) => {
    const [sessionResult, loaded] = await Promise.all([
      api.get(`/api/sessions/${sessionId}`),
      loadSessionPlan(sessionId, api),
    ])
    if (!isCurrentSessionRequest(sessionId, expectedSessionEpoch)) return false
    if (!loaded.ok || loaded.planRevision < minimumRevision) {
      setSharedActionError(loaded.error || 'The saved plan could not be reloaded at its accepted revision.')
      return false
    }
    if (planDirtyRef.current) {
      setSharedActionError('The saved plan changed while this draft has unsaved edits. Reload the saved plan before continuing.')
      return false
    }
    return adoptAuthoritativePlan(loaded, sessionResult) ? loaded : false
  }

  const refreshResourceDependencies = async () => {
    if (!isResource || !planSessionCurrent || planDirtyRef.current || planRevisionRef.current === null || resourceRefreshBusy || lookProgressionBusy) return
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    setResourceRefreshBusy(true)
    setResourceRefreshOutcome(null)
    try {
      const result = await api.post(`/api/sessions/${sessionId}/plan/refresh-resources`, {
        expected_revision: planRevisionRef.current,
      })
      if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
      const outcome = {
        type: result?.refreshed ? 'refreshed' : 'no-drift',
        affectedTakes: Array.isArray(result?.affected_takes) ? result.affected_takes : [],
        requiredPreparation: Array.isArray(result?.required_preparation) ? result.required_preparation : [],
        copiedForwardTakes: Array.isArray(result?.copied_forward_takes) ? result.copied_forward_takes : [],
        diagnostics: Array.isArray(result?.diagnostics) ? result.diagnostics : [],
        planRevision: result?.plan_revision,
      }
      setResourceRefreshOutcome(outcome)
      if (result?.refreshed && Number.isInteger(result.plan_revision)) {
        const loaded = await reloadAuthoritativePlan(sessionId, result.plan_revision, requestEpoch)
        if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
        if (!loaded) {
          setResourceRefreshOutcome((previous) => ({ ...previous, reloadFailed: true }))
        }
      }
      setReviewRefreshCounter((count) => count + 1)
    } catch (error) {
      if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
      setResourceRefreshOutcome({
        type: 'error',
        status: error?.status,
        detail: error?.detail,
        message: error?.message || 'Resource dependency refresh failed.',
      })
    } finally {
      if (isCurrentSessionRequest(sessionId, requestEpoch)) setResourceRefreshBusy(false)
    }
  }

  const refreshAfterAuthoringOperation = async (view) => {
    if (
      view?.kind !== 'prepare_takes'
      || ['active', 'cancel_requested'].includes(view.state)
      || view.plan_revision !== planRevisionRef.current
      || authoringOperationRefreshRef.current === view.operation_id
    ) return
    authoringOperationRefreshRef.current = view.operation_id
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    const loaded = await reloadAuthoritativePlan(sessionId, view.plan_revision, requestEpoch)
    if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
    if (!loaded) {
      setPreparationActionCode('plan_revision_stale')
      setPreparationActionError('Preparation finished, but the saved plan and take reviews could not be refreshed. Reload the saved plan before continuing.')
      return
    }
    setReviewRefreshCounter((count) => count + 1)
  }

  const applySharedOperationView = (view) => {
    sharedOperationRef.current = view
    setSharedOperation(view)
    if (
      view && typeof view.operation_id === 'string'
      && Number.isInteger(view.plan_revision)
      && ['shared_suggestions', 'prepare_takes'].includes(view.kind)
    ) {
      rememberAuthoringOperation(id, view.plan_revision, { operation_id: view.operation_id })
    }
    if (view?.kind === 'shared_suggestions' && view.state === 'succeeded') {
      const requested = Array.isArray(view.progress?.requested) ? view.progress.requested : []
      const items = Array.isArray(view.result?.items) ? view.result.items : []
      const drafts = {}
      let valid = requested.length > 0
        && requested.every((target) => ['look', 'initial_wardrobe'].includes(target))
        && new Set(requested).size === requested.length
        && items.length === requested.length
      for (const item of items) {
        if (
          !item || !['look', 'initial_wardrobe'].includes(item.target)
          || !requested.includes(item.target) || typeof item.result !== 'string'
          || Object.prototype.hasOwnProperty.call(drafts, item.target)
        ) {
          valid = false
          break
        }
        drafts[item.target] = item.result
      }
      if (valid && requested.some((target) => !Object.prototype.hasOwnProperty.call(drafts, target))) {
        valid = false
      }
      if (valid) setSharedProposalDrafts(drafts)
      else {
        setSharedProposalDrafts({})
        setSharedActionError('The saved shared suggestion result is incomplete or invalid. Reload the plan before continuing.')
      }
    } else {
      setSharedProposalDrafts({})
    }
    if (view?.kind === 'prepare_takes' && !['active', 'cancel_requested'].includes(view.state)) {
      void refreshAfterAuthoringOperation(view)
    }
  }

  const savePlan = async ({ sharedDecisions = null, impactConfirmed = false, confirmedPlan = null } = {}) => {
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    const explicitDecision = Boolean(sharedDecisions?.length)
    if (explicitDecision && manualDecisionBusy) return
    if (planEditLocked) {
      setSharedActionError('Wait for the pending shared-choice operation to finish before editing the plan.')
      return
    }
    if (!plan || planRevision === null) {
      setError('Cannot save plan: no valid plan draft loaded')
      return
    }
    if (sharedDecisions?.length && planDirtyRef.current) {
      setSharedActionError('Save or discard the draft before recording an empty shared choice.')
      return
    }
    if (!explicitDecision && planDirtyRef.current) {
      if (!impactConfirmed || confirmedPlan !== plan) {
        setPlanSaveImpact({ ...computePlanChangeImpact(savedPlan, plan, planPreparation), draft: plan })
        return
      }
      setPlanSaveImpact(null)
    }
    const previousRevision = planRevisionRef.current
    if (explicitDecision) setManualDecisionBusy(true)
    try {
      const res = await executeSavePlan(sessionId, plan, planRevision, api, sharedDecisions)
      if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
      if (res.ok) {
        if (!explicitDecision) setSavedPlan(plan)
        setResourceRefreshOutcome(null)
        setPlanRevision(res.planRevision)
        planRevisionRef.current = res.planRevision
        setPlanConflicts(res.conflicts)
        setPlanDirty(false)
        planDirtyRef.current = false
        setReviewedRevision(null)
        setSelectedTakeIds(new Set())
        setTakeReviewData({})
        setPlanReviewStatus('idle')
        setPlanNotice(`Plan saved (revision ${res.planRevision})`)
        setError('')
        setTimeout(() => setPlanNotice(''), 4000)
        if (res.planRevision !== previousRevision) {
          if (sharedOperationRef.current) {
            setPreparationActionCode('plan_revision_stale')
            setPreparationActionError('The saved plan changed. Reload the saved plan before resuming or starting preparation.')
            setSharedProposalDrafts({})
          }
        }
        if (explicitDecision) {
          const refreshed = await reloadAuthoritativePlan(sessionId, res.planRevision, requestEpoch)
          if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
          if (refreshed) setSharedActionNotice('The empty shared choice was saved in the authoritative plan.')
        } else {
          reload()
        }
      } else {
        setError(res.error)
        setSharedActionError(res.error)
        if (res.status === 409 && !planDirtyRef.current) {
          try {
            const loaded = await reloadAuthoritativePlan(sessionId, 0, requestEpoch)
            if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
            if (
              explicitDecision && loaded
              && sharedDecisions.every((field) => (
                loaded.plan?.[field] === ''
                && loaded.plan?.authoring?.shared_state?.[field]?.origin === 'user'
              ))
            ) {
              setSharedActionNotice('The empty shared choice is saved in the authoritative plan.')
              setSharedActionError('')
              setError('')
            }
          } catch { /* Keep the local draft and show the CAS error. */ }
        } else if (explicitDecision && (!res.status || res.status >= 500)) {
          try {
            const refreshed = await reloadAuthoritativePlan(sessionId, 0, requestEpoch)
            if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
            if (
              refreshed && sharedDecisions.every((field) => (
                refreshed.plan?.[field] === ''
                && refreshed.plan?.authoring?.shared_state?.[field]?.origin === 'user'
              ))
            ) {
              setSharedActionNotice('The empty shared choice was saved in the authoritative plan.')
              setSharedActionError('')
              setError('')
            }
          } catch { /* The visible message keeps the unresolved outcome explicit. */ }
        }
      }
    } catch (e) {
      if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
      setError(e?.message || 'Failed to save plan draft')
      setSharedActionError(e?.message || 'Failed to save plan draft')
    } finally {
      if (explicitDecision && isCurrentSessionRequest(sessionId, requestEpoch)) setManualDecisionBusy(false)
    }
  }

  const startSharedSuggestions = async () => {
    const current = sharedStartRequestRef.current
    const retryUnknown = Boolean(sharedStartUnknown && current?.kind === 'shared_suggestions')
    const pendingPreparationRequest = preparationStartUnknown
      && preparationStartRequestRef.current?.kind === 'prepare_takes'
    if (!planSessionCurrent || authoringActionInFlightRef.current || pendingPreparationRequest || lookProgressionBusy) return
    if (!retryUnknown && (
      !plan?.authoring || planDirtyRef.current || typeof planRevisionRef.current !== 'number'
      || (sharedOperationRef.current && ['active', 'cancel_requested'].includes(sharedOperationRef.current.state))
    )) return
    if (!retryUnknown) resetSharedOperation()
    const request = retryUnknown
      ? current
      : {
          request_id: crypto.randomUUID(),
          expected_revision: planRevisionRef.current,
          kind: 'shared_suggestions',
        }
    sharedStartRequestRef.current = request
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    rememberAuthoringOperation(sessionId, request.expected_revision, { request })
    const operationEpoch = ++sharedOperationEpochRef.current
    authoringActionInFlightRef.current = true
    setSharedStartBusy(true)
    setSharedActionError('')
    setSharedActionNotice('')
    try {
      const view = await api.post(`/api/sessions/${sessionId}/plan/authoring/operations`, request)
      if (
        !isCurrentSessionRequest(sessionId, requestEpoch)
        || operationEpoch !== sharedOperationEpochRef.current
      ) return
      if (!isAuthoringOperationView(view, sessionId) || view.plan_revision !== request.expected_revision) {
        setSharedStartUnknown(true)
        setSharedActionError('The suggestion start result is unknown. Retry to check the same operation.')
        return
      }
      sharedStartRequestRef.current = null
      setSharedStartUnknown(false)
      applySharedOperationView(view)
      if (planRevisionRef.current !== request.expected_revision) {
        setPreparationActionCode('plan_revision_stale')
        setSharedActionError('The suggestion operation belongs to an older plan revision. Reload the current plan before continuing.')
      }
    } catch (e) {
      if (
        !isCurrentSessionRequest(sessionId, requestEpoch)
        || operationEpoch !== sharedOperationEpochRef.current
      ) return
      const activeOperation = e?.status === 409
        && e?.detail?.code === 'authoring_active'
        && isAuthoringOperationView(e.detail.operation, sessionId)
        ? e.detail.operation
        : null
      if (activeOperation) {
        sharedStartRequestRef.current = null
        setSharedStartUnknown(false)
        applySharedOperationView(activeOperation)
        setSharedActionError('Another authoring operation is active. Its saved progress is shown above.')
        return
      }
      if (hasStableAuthoringError(e, 'plan_revision_stale')) {
        sharedStartRequestRef.current = null
        setSharedStartUnknown(false)
        forgetAuthoringOperation(sessionId, request.expected_revision)
        setPreparationActionCode('plan_revision_stale')
        setSharedActionError(e.message)
        if (!planDirtyRef.current) {
          try { await reloadAuthoritativePlan(sessionId, 0, requestEpoch) } catch { /* The stale CAS message remains visible. */ }
        }
      } else if (!hasStableAuthoringError(e)) {
        setSharedStartUnknown(true)
        setSharedActionError('The suggestion start result is unknown. Retry to check the same operation.')
      } else {
        setSharedStartUnknown(false)
        forgetAuthoringOperation(sessionId, request.expected_revision)
        setPreparationActionCode(e?.detail?.code || '')
        setSharedActionError(e?.message || 'Shared suggestions could not be started.')
      }
    } finally {
      if (isCurrentSessionRequest(sessionId, requestEpoch)) {
        authoringActionInFlightRef.current = false
        setSharedStartBusy(false)
      }
    }
  }

  const acceptSharedSuggestions = async () => {
    if (!planSessionCurrent || lookProgressionBusy) return
    let pending = sharedAcceptanceRequestRef.current
    if (!pending) {
      const operation = sharedOperationRef.current
      if (
        !operation || operation.kind !== 'shared_suggestions' || operation.state !== 'succeeded'
        || planDirtyRef.current || operation.plan_revision !== planRevisionRef.current
      ) return
      const accepted = {}
      for (const item of operation.result?.items || []) {
        if (
          item && ['look', 'initial_wardrobe'].includes(item.target)
          && Object.prototype.hasOwnProperty.call(sharedProposalDrafts, item.target)
        ) accepted[item.target] = sharedProposalDrafts[item.target]
      }
      if (!Object.keys(accepted).length) {
        setSharedActionError('Edit at least one proposed shared choice before accepting it.')
        return
      }
      pending = {
        operationId: operation.operation_id,
        body: { expected_revision: operation.plan_revision, accepted },
      }
      sharedAcceptanceRequestRef.current = pending
    }

    const operationEpoch = ++sharedOperationEpochRef.current
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    setSharedAcceptBusy(true)
    setSharedAcceptUnknown(false)
    setSharedActionError('')
    setSharedActionNotice('')
    try {
      const result = await api.post(
        `/api/sessions/${sessionId}/plan/authoring/operations/${encodeURIComponent(pending.operationId)}/accept`,
        pending.body,
      )
      if (
        !isCurrentSessionRequest(sessionId, requestEpoch)
        || operationEpoch !== sharedOperationEpochRef.current
      ) return
      if (!Number.isInteger(result?.plan_revision) || result.plan_revision <= pending.body.expected_revision) {
        setSharedAcceptUnknown(true)
        setSharedActionError('The acceptance result is unknown. Retry the same acceptance to check its saved result.')
        return
      }
      sharedAcceptanceRequestRef.current = null
      setSharedAcceptUnknown(false)
      forgetAuthoringOperation(sessionId, pending.body.expected_revision)
      resetSharedOperation()
      const reloaded = await reloadAuthoritativePlan(sessionId, result.plan_revision, requestEpoch)
      if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
      if (reloaded) {
        setSharedActionNotice('Accepted shared choices were reloaded from the saved plan.')
      } else {
        setSharedActionError((current) => current || 'Acceptance succeeded, but the authoritative plan must be reloaded before continuing.')
      }
    } catch (e) {
      if (
        !isCurrentSessionRequest(sessionId, requestEpoch)
        || operationEpoch !== sharedOperationEpochRef.current
      ) return
      if (!e?.status || e.status >= 500) {
        setSharedAcceptUnknown(true)
        setSharedActionError('The acceptance result is unknown. Retry the same acceptance to check its saved result.')
      } else {
        sharedAcceptanceRequestRef.current = null
        setSharedAcceptUnknown(false)
        setSharedActionError(e?.message || 'Shared suggestions were not accepted.')
        if (e?.detail?.code === 'plan_revision_stale' && !planDirtyRef.current) {
          try { await reloadAuthoritativePlan(sessionId, 0, requestEpoch) } catch { /* The stale revision remains visible. */ }
        }
      }
    } finally {
      if (isCurrentSessionRequest(sessionId, requestEpoch)) setSharedAcceptBusy(false)
    }
  }

  const incompletePlanTakeIds = () => (
    !planDirty && Array.isArray(plan?.takes)
      ? plan.takes.filter((take) => !['ready', 'generated'].includes(
          getTakePreparationState(take.take_id, planPreparation, false),
        )).map((take) => take.take_id)
      : []
  )

  const incompleteAuthoringTakeIds = () => incompletePlanTakeIds()

  const startPreparationOperation = async () => {
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    const current = preparationStartRequestRef.current
    const retryUnknown = Boolean(preparationStartUnknown && current?.kind === 'prepare_takes')
    const pendingSharedRequest = sharedStartUnknown
      && sharedStartRequestRef.current?.kind === 'shared_suggestions'
    if (!planSessionCurrent || authoringActionInFlightRef.current || pendingSharedRequest || lookProgressionBusy) return
    if (!retryUnknown && (
      !plan?.authoring || plan.authoring.mode !== 'automatic'
      || planDirtyRef.current || typeof planRevisionRef.current !== 'number'
      || reviewBlocksFinalization
      || (sharedOperationRef.current && ['active', 'cancel_requested'].includes(sharedOperationRef.current.state))
      || sharedOperationRef.current?.can_resume
    )) return
    const takeIds = retryUnknown
      ? current.take_ids
      : incompleteAuthoringTakeIds().slice(0, MAX_AUTOMATIC_TAKE_BATCH)
    if (!takeIds.length) return
    if (!retryUnknown) resetSharedOperation()
    const request = retryUnknown ? current : {
      request_id: crypto.randomUUID(),
      expected_revision: planRevisionRef.current,
      kind: 'prepare_takes',
      take_ids: takeIds,
    }
    preparationStartRequestRef.current = request
    rememberAuthoringOperation(sessionId, request.expected_revision, { request })
    const operationEpoch = ++sharedOperationEpochRef.current
    authoringActionInFlightRef.current = true
    setPreparationActionBusy(true)
    setPreparingAll(true)
    setPreparationActionError('')
    setPreparationActionCode('')
    setSharedActionError('')
    try {
      const view = await api.post(`/api/sessions/${sessionId}/plan/authoring/operations`, request)
      if (
        !isCurrentSessionRequest(sessionId, requestEpoch)
        || operationEpoch !== sharedOperationEpochRef.current
      ) return
      if (!isAuthoringOperationView(view, sessionId)) {
        setPreparationStartUnknown(true)
        setPreparationActionError('The preparation start result could not be verified. Retry the same request to check its saved operation.')
        return
      }
      if (view.plan_revision !== request.expected_revision) {
        applySharedOperationView(view)
        setPreparationStartUnknown(false)
        setPreparationActionCode('plan_revision_stale')
        setPreparationActionError('The saved plan changed before preparation started. Reload the saved plan before continuing.')
        return
      }
      preparationStartRequestRef.current = null
      setPreparationStartUnknown(false)
      applySharedOperationView(view)
    } catch (e) {
      if (
        !isCurrentSessionRequest(sessionId, requestEpoch)
        || operationEpoch !== sharedOperationEpochRef.current
      ) return
      const activeOperation = e?.status === 409
        && e?.detail?.code === 'authoring_active'
        && isAuthoringOperationView(e.detail.operation, sessionId)
        ? e.detail.operation
        : null
      if (activeOperation) {
        preparationStartRequestRef.current = null
        setPreparationStartUnknown(false)
        applySharedOperationView(activeOperation)
        setPreparationActionError('Another authoring operation is active. Its saved progress is shown above; no duplicate assistant work was started.')
      } else if (hasStableAuthoringError(e, 'assistant_unavailable')) {
        preparationStartRequestRef.current = null
        setPreparationStartUnknown(false)
        forgetAuthoringOperation(sessionId, request.expected_revision)
        setPreparationActionCode('assistant_unavailable')
        setPreparationActionError('The prompt assistant is unavailable. Configure it in Setup, then try preparation again.')
      } else if (hasStableAuthoringError(e, 'plan_revision_stale')) {
        preparationStartRequestRef.current = null
        setPreparationStartUnknown(false)
        forgetAuthoringOperation(sessionId, request.expected_revision)
        setPreparationActionCode('plan_revision_stale')
        setPreparationActionError(e?.message || 'The saved plan is stale. Reload it before preparing.')
        if (!planDirtyRef.current) {
          try { await reloadAuthoritativePlan(sessionId, 0, requestEpoch) } catch { /* Keep the stale-revision action visible. */ }
        }
      } else if (!hasStableAuthoringError(e)) {
        setPreparationStartUnknown(true)
        setPreparationActionError('The preparation start result is unknown. Retry the same request to check its saved operation.')
      } else {
        preparationStartRequestRef.current = null
        setPreparationStartUnknown(false)
        forgetAuthoringOperation(sessionId, request.expected_revision)
        setPreparationActionCode(e?.detail?.code || '')
        setPreparationActionError(e?.message || 'Automatic preparation could not be started.')
      }
    } finally {
      if (isCurrentSessionRequest(sessionId, requestEpoch)) {
        authoringActionInFlightRef.current = false
        setPreparationActionBusy(false)
        setPreparingAll(false)
      }
    }
  }

  const resumeAuthoringOperation = async () => {
    const operation = sharedOperationRef.current
    if (
      !operation || !operation.can_resume
      || operation.plan_revision !== planRevisionRef.current
      || authoringActionInFlightRef.current
    ) return
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    const operationEpoch = ++sharedOperationEpochRef.current
    // Resume keeps the operation ID, so a later terminal result must refresh
    // persisted preparation and reviews even if this ID already finished once.
    authoringOperationRefreshRef.current = null
    authoringActionInFlightRef.current = true
    setPreparationActionBusy(true)
    setPreparationActionError('')
    setPreparationActionCode('')
    try {
      const view = await api.post(
        `/api/sessions/${sessionId}/plan/authoring/operations/${encodeURIComponent(operation.operation_id)}/resume`,
        { expected_revision: operation.plan_revision },
      )
      if (!isCurrentSessionRequest(sessionId, requestEpoch) || operationEpoch !== sharedOperationEpochRef.current) return
      if (!isAuthoringOperationView(view, sessionId)) {
        setPreparationActionError('The resume result could not be verified. Retry Resume to check the same operation.')
        return
      }
      applySharedOperationView(view)
    } catch (e) {
      if (!isCurrentSessionRequest(sessionId, requestEpoch) || operationEpoch !== sharedOperationEpochRef.current) return
      const returnedOperation = isAuthoringOperationView(e?.detail?.operation, sessionId)
        ? e.detail.operation
        : null
      if (returnedOperation) applySharedOperationView(returnedOperation)
      if (e?.detail?.code === 'assistant_unavailable') {
        setPreparationActionCode('assistant_unavailable')
        setPreparationActionError('The prompt assistant is unavailable. Configure it in Setup, then resume this operation.')
      } else if (e?.detail?.code === 'plan_revision_stale') {
        setPreparationActionCode('plan_revision_stale')
        setPreparationActionError('This operation belongs to an older plan revision. Reload the saved plan before continuing.')
      } else if (e?.detail?.code === 'authoring_active' && returnedOperation) {
        setPreparationActionError('Another authoring operation is active. Its saved progress is shown above.')
      } else if (!e?.status || e.status >= 500) {
        setPreparationActionError('The resume result is unknown. Retry Resume to check this same operation.')
      } else {
        setPreparationActionCode(e?.detail?.code || '')
        setPreparationActionError(e?.message || 'The operation could not be resumed.')
      }
    } finally {
      if (isCurrentSessionRequest(sessionId, requestEpoch)) {
        authoringActionInFlightRef.current = false
        setPreparationActionBusy(false)
      }
    }
  }

  const cancelAuthoringOperation = async () => {
    const operation = sharedOperationRef.current
    if (!operation?.can_cancel || authoringActionInFlightRef.current) return
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    const operationEpoch = ++sharedOperationEpochRef.current
    authoringActionInFlightRef.current = true
    setPreparationActionBusy(true)
    setPreparationActionError('')
    setPreparationActionCode('')
    try {
      const view = await api.post(
        `/api/sessions/${sessionId}/plan/authoring/operations/${encodeURIComponent(operation.operation_id)}/cancel`,
        { expected_revision: operation.plan_revision },
      )
      if (!isCurrentSessionRequest(sessionId, requestEpoch) || operationEpoch !== sharedOperationEpochRef.current) return
      if (!isAuthoringOperationView(view, sessionId)) {
        setPreparationActionError('The cancellation result could not be verified. Retry Cancel to check this operation.')
        return
      }
      applySharedOperationView(view)
    } catch (e) {
      if (!isCurrentSessionRequest(sessionId, requestEpoch) || operationEpoch !== sharedOperationEpochRef.current) return
      const returnedOperation = isAuthoringOperationView(e?.detail?.operation, sessionId)
        ? e.detail.operation
        : null
      if (returnedOperation) applySharedOperationView(returnedOperation)
      setPreparationActionCode(e?.detail?.code || '')
      setPreparationActionError(e?.message || 'The operation could not be cancelled. Retry Cancel to check its status.')
    } finally {
      if (isCurrentSessionRequest(sessionId, requestEpoch)) {
        authoringActionInFlightRef.current = false
        setPreparationActionBusy(false)
      }
    }
  }

  const reloadSavedPlanForOperation = async () => {
    if (planDirtyRef.current) {
      setPreparationActionCode('plan_revision_stale')
      setPreparationActionError('Save or discard unsaved plan edits before reloading the saved plan.')
      return
    }
    const sessionId = id
    const requestEpoch = sessionViewEpochRef.current
    setPreparationActionBusy(true)
    try {
      const loaded = await reloadAuthoritativePlan(sessionId, 0, requestEpoch)
      if (!isCurrentSessionRequest(sessionId, requestEpoch)) return
      if (loaded) {
        setPreparationActionCode('')
        setPreparationActionError('The saved plan and preparation progress were reloaded. Start a new operation for any remaining takes.')
        setReviewRefreshCounter((count) => count + 1)
      }
    } finally {
      if (isCurrentSessionRequest(sessionId, requestEpoch)) setPreparationActionBusy(false)
    }
  }

  useEffect(() => {
    sessionViewEpochRef.current += 1
    setSessionRequestEpoch(sessionViewEpochRef.current)
    sessionViewMountedRef.current = true
    setPlanSaveImpact(null)
    setPlanReviewFailure(null)
    setResourceRefreshBusy(false)
    setResourceRefreshOutcome(null)
    authoringActionInFlightRef.current = false
    authoringOperationRefreshRef.current = null
    restoredOperationKeyRef.current = null
    resetSharedOperation()
    setManualDecisionBusy(false)
    setLookProgressionBusy(false)
    setSharedActionError('')
    setSharedActionNotice('')
    setPreparationActionBusy(false)
    setPreparationStartUnknown(false)
    setPreparationActionError('')
    setPreparationActionCode('')
    setExpandedTakeFields({})
    return () => {
      sessionViewMountedRef.current = false
      sessionViewEpochRef.current += 1
      sharedOperationEpochRef.current += 1
    }
  }, [id])

  useEffect(() => {
    if (!Number.isInteger(planRevision) || planRevision < 0) return undefined
    const sessionId = id
    const remembered = readRememberedAuthoringOperation(sessionId, planRevision)
    if (!remembered) return undefined
    const restoreKey = `${sessionId}:${remembered.plan_revision}:${remembered.operation_id || remembered.request.request_id}`
    if (restoredOperationKeyRef.current === restoreKey) return undefined
    restoredOperationKeyRef.current = restoreKey
    const requestEpoch = sessionViewEpochRef.current

    if (remembered.request) {
      if (remembered.request.kind === 'prepare_takes') {
        preparationStartRequestRef.current = remembered.request
        setPreparationStartUnknown(true)
        setPreparationActionError('The previous preparation start result is unknown. Retry the same request to recover its saved operation.')
      } else {
        sharedStartRequestRef.current = remembered.request
        setSharedStartUnknown(true)
        setSharedActionError('The suggestion start result is unknown. Retry to check the same operation.')
      }
      return undefined
    }

    const operationId = remembered.operation_id
    const operationEpoch = ++sharedOperationEpochRef.current
    let current = true
    api.get(
      `/api/sessions/${sessionId}/plan/authoring/operations/${encodeURIComponent(operationId)}`,
    ).then((view) => {
      if (
        !current || !isCurrentSessionRequest(sessionId, requestEpoch)
        || operationEpoch !== sharedOperationEpochRef.current
      ) return
      if (!isAuthoringOperationView(view, sessionId)) {
        setPreparationActionCode('operation_status_invalid')
        setPreparationActionError('The saved operation status could not be verified. Reload the plan before continuing.')
        return
      }
      applySharedOperationView(view)
      if (view.plan_revision !== planRevisionRef.current) {
        setPreparationActionCode('plan_revision_stale')
        setPreparationActionError('This operation belongs to an older plan revision. Reload the saved plan before continuing.')
      }
    }).catch((e) => {
      if (!current || !isCurrentSessionRequest(sessionId, requestEpoch)) return
      setPreparationActionCode(e?.detail?.code || 'operation_status_unavailable')
      setPreparationActionError(e?.message || 'Could not restore the saved authoring operation. Reload the plan before continuing.')
    })
    return () => { current = false }
  }, [id, planRevision])

  useEffect(() => {
    const operation = sharedOperation
    if (!operation || !['active', 'cancel_requested'].includes(operation.state)) return undefined
    const sessionId = id
    const operationId = operation.operation_id
    const operationEpoch = sharedOperationEpochRef.current
    let current = true
    let timer = null
    const poll = async () => {
      try {
        const view = await api.get(
          `/api/sessions/${sessionId}/plan/authoring/operations/${encodeURIComponent(operationId)}`,
        )
        if (
          !current || !sessionViewMountedRef.current || String(id) !== String(sessionId)
          || operationEpoch !== sharedOperationEpochRef.current
        ) return
        if (!isAuthoringOperationView(view, sessionId)) {
          setPreparationActionCode('operation_status_invalid')
          setPreparationActionError('The authoring operation returned an invalid status view. Reload the plan before continuing.')
          return
        }
        if (view.plan_revision !== planRevisionRef.current) {
          applySharedOperationView(view)
          setPreparationActionCode('plan_revision_stale')
          setPreparationActionError('This operation belongs to an older plan revision. Reload the saved plan before continuing.')
          return
        }
        setSharedActionError('')
        setPreparationActionError('')
        setPreparationActionCode('')
        applySharedOperationView(view)
        if (['active', 'cancel_requested'].includes(view.state)) timer = window.setTimeout(poll, 1200)
      } catch (e) {
        if (!current || !sessionViewMountedRef.current || operationEpoch !== sharedOperationEpochRef.current) return
        if (e?.detail?.code === 'operation_not_found') {
          setPreparationActionCode('operation_not_found')
          setPreparationActionError('The saved operation status is unavailable. Reload the saved plan before starting new preparation.')
          return
        }
        setPreparationActionError(e?.message || 'Could not read authoring operation status.')
        timer = window.setTimeout(poll, 2000)
      }
    }
    timer = window.setTimeout(poll, 900)
    return () => {
      current = false
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [id, sharedOperation?.operation_id, sharedOperation?.state])

  const handleToggleTakeSelect = (takeId) => {
    setSelectedTakeIds((prev) => {
      const next = new Set(prev)
      if (next.has(takeId)) {
        next.delete(takeId)
      } else {
        next.add(takeId)
      }
      return next
    })
  }

  const handleSelectAllReady = (readyTakeIds) => {
    setSelectedTakeIds((prev) => {
      const allSelected = readyTakeIds.length > 0 && readyTakeIds.every((tid) => prev.has(tid))
      if (allSelected) {
        return new Set()
      }
      return new Set(readyTakeIds)
    })
  }

  const fetchTakeReview = async (takeId) => {
    setTakeReviewLoading((prev) => ({ ...prev, [takeId]: true }))
    setTakeReviewError((prev) => ({ ...prev, [takeId]: '' }))
    try {
      const res = await loadTakeReview(id, takeId, api)
      if (res.ok) {
        setTakeReviewData((prev) => ({ ...prev, [takeId]: res }))
      } else {
        setTakeReviewError((prev) => ({ ...prev, [takeId]: res.error || 'Failed to load take review' }))
      }
    } catch (e) {
      setTakeReviewError((prev) => ({ ...prev, [takeId]: e?.message || 'Failed to load take review' }))
    } finally {
      setTakeReviewLoading((prev) => ({ ...prev, [takeId]: false }))
    }
  }

  const toggleTakeReview = (takeId) => {
    if (expandedTakeId === takeId) {
      setExpandedTakeId(null)
    } else {
      setExpandedTakeId(takeId)
      if (!takeReviewData[takeId]) {
        fetchTakeReview(takeId)
      }
    }
  }

  const handlePrepareTake = async (takeId) => {
    if (planRevision === null || planDirty || planEditLocked || reviewBlocksFinalization) return
    setPreparingTakeId(takeId)
    setError('')
    try {
      const res = await prepareTake(id, takeId, planRevision, api)
      if (res.ok) {
        setPlanNotice(`Take ${takeId} prepared`)
        setTimeout(() => setPlanNotice(''), 3000)
        await fetchTakeReview(takeId)
        await reload()
      } else {
        setError(res.error || `Failed to prepare take ${takeId}`)
      }
    } catch (e) {
      setError(e?.message || `Failed to prepare take ${takeId}`)
    } finally {
      setPreparingTakeId(null)
    }
  }

  const handlePrepareAllIncomplete = async () => {
    if (planRevision === null || planDirty || planEditLocked || reviewBlocksFinalization) return
    const takeIds = incompletePlanTakeIds()
    if (!takeIds.length) return
    setPreparingAll(true)
    setError('')
    try {
      const res = await preparePlanTakes(id, planRevision, takeIds, api)
      if (res.ok) {
        setPlanNotice(`Batch preparation complete: ${res.prepared.length} prepared`)
        setTimeout(() => setPlanNotice(''), 4000)
        await reload()
      } else {
        setError(res.error || 'Batch preparation failed')
      }
    } catch (e) {
      setError(e?.message || 'Batch preparation failed')
    } finally {
      setPreparingAll(false)
    }
  }

  const handleRecordAdaptation = async (takeId, conflict) => {
    if (planRevision === null || planDirty || planEditLocked || reviewBlocksFinalization) return
    const conflictKey = conflict.conflict_key || `${conflict.library_key || ''}:${conflict.source_id || ''}:${conflict.resource_field || ''}`
    const adaptedVal = (adaptationDrafts[takeId]?.[conflictKey] ?? '').trim()
    if (!adaptedVal) {
      setError('Adaptation value cannot be empty')
      return
    }
    setError('')
    try {
      const res = await recordTakeAdaptation(id, takeId, {
        plan_revision: planRevision,
        adaptation: {
          library_key: conflict.library_key,
          source_id: conflict.source_id,
          content_digest: conflict.content_digest,
          resource_field: conflict.resource_field,
          adapted_value: adaptedVal,
        },
      }, api)
      if (res.ok) {
        setPlanNotice(`Adaptation recorded for take ${takeId}`)
        setTimeout(() => setPlanNotice(''), 3000)
        await fetchTakeReview(takeId)
        await reload()
      } else {
        setError(res.error || 'Failed to record adaptation')
      }
    } catch (e) {
      setError(e?.message || 'Failed to record adaptation')
    }
  }

  const handleApproveReview = async () => {
    if (planDirty || planEditLocked || planRevision === null || reviewBlocksFinalization) return
    setError('')
    try {
      const res = await approvePlanReview(id, planRevision, api)
      if (res.ok) {
        setReviewedRevision(planRevision)
        setPlanNotice(`Review approved for revision ${planRevision}`)
        setTimeout(() => setPlanNotice(''), 3000)
      } else {
        setError(res.error || 'Failed to approve review')
      }
    } catch (e) {
      setError(e?.message || 'Failed to approve review')
    }
  }

  const handleSubmitSelectedTakes = async () => {
    if (submittingTakes || planEditLocked) return
    if (planDirty) {
      setError('Cannot submit: plan has unsaved changes')
      return
    }
    if (reviewedRevision !== planRevision) {
      setError('Cannot submit: review must be confirmed for current plan revision')
      return
    }
    const toSubmit = Array.from(selectedTakeIds)
    if (toSubmit.length === 0) {
      setError('No takes selected for submission')
      return
    }
    setSubmittingTakes(true)
    setError('')
    try {
      const res = await submitSelectedTakes(id, toSubmit, planRevision, api)
      if (res.ok) {
        setSelectedTakeIds(new Set())
        setPlanNotice(`Submitted ${res.submitted_count || toSubmit.length} take(s) to queue`)
        setTimeout(() => setPlanNotice(''), 4000)
        await reload()
        navigateStep('generation')
      } else {
        setError(res.error || 'Failed to submit selected takes')
      }
    } catch (e) {
      setError(e?.message || 'Failed to submit selected takes')
    } finally {
      setSubmittingTakes(false)
    }
  }

  const navigateStep = (step) => {
    if (isResource) {
      if (!canPreparePlan({ plan, planRevision })) {
        setError('Cannot navigate steps: plan draft is incomplete or missing')
        return false
      }
      if (step === 'generation') {
        if (activeStep !== 'review' && activeStep !== 'generation') {
          setError('Cannot skip to generation: review step must be completed first')
          return false
        }
        if (planDirty) {
          setError('Cannot proceed to generation: plan has unsaved changes. Save the draft first.')
          return false
        }
        if (hasKnownStaleAdaptations) {
          setError('Cannot proceed to generation: a reviewed adaptation no longer matches the authorized resource description. Save a new plan revision and review it.')
          return false
        }
        if (activeStep === 'review' && planReviewStatus !== 'ready') {
          setError('Cannot proceed to generation: take reviews must load successfully before proceeding.')
          return false
        }
        if (!canProceedToGeneration(s, {
          plan,
          planRevision,
          planDirty,
          conflicts: planConflictMarkers,
          reviewedRevision,
        })) {
          if (hasPlanConflictMarkers && reviewedRevision !== planRevision) {
            setError('Cannot proceed to generation: approve the current plan revision to continue while resource markers remain.')
          } else if (!plan?.takes?.length) {
            setError('Cannot proceed to generation: plan must contain at least one take.')
          } else if (!Boolean(s?.workflow_id || s?.model?.workflow_id || s?.settings?.workflow_id)) {
            setError('Cannot proceed to generation: session has no workflow assigned.')
          } else {
            setError('Cannot proceed to generation: plan is incomplete or has unsaved changes.')
          }
          return false
        }
        if (!hasPlanConflictMarkers) setReviewedRevision(planRevision)
      }
    }
    setActiveStep(step)
    setError('')
    return true
  }
  useEffect(() => {
    reload()
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
    api.get('/api/sessions').then(setSessions).catch(() => {})
    api.get('/api/comfy/models').then(setBaseModels).catch(() => {})
    // Optional: with no endpoint configured the ✨ buttons simply do not appear.
    api.get('/api/config').then(setConfig).catch(() => {})
  }, [id])

  useEffect(() => {
    if (!isResource || activeStep !== 'review' || planDirty || planRevision === null) {
      setPlanReviewStatus('idle')
      setPlanReviewFailure(null)
      return undefined
    }
    let current = true
    setPlanReviewStatus('loading')
    setPlanReviewFailure(null)
    loadPlanReviews(id, planRevision, api).then((result) => {
      if (!current) return
      if (!result.ok || result.planRevision !== planRevision) {
        setPlanReviewFailure({
          error: result.error || `Review response revision ${result.planRevision ?? 'unknown'} does not match ${planRevision}.`,
          status: result.status,
          detail: result.detail,
        })
        setPlanReviewStatus('error')
        return
      }
      const reviewsByTake = Object.fromEntries(
        (result.takes || [])
          .filter((review) => review && typeof review.take_id === 'string')
          .map((review) => [review.take_id, review]),
      )
      setTakeReviewData(reviewsByTake)
      setPlanReviewStatus('ready')
    }).catch(() => {
      if (current) {
        setPlanReviewFailure({ error: 'Failed to load plan reviews.' })
        setPlanReviewStatus('error')
      }
    })
    return () => { current = false }
  }, [id, isResource, activeStep, planDirty, planRevision, reviewRefreshCounter])

  // The session being compared against. Loaded whole and separately: the list
  // route carries counts, not shots, and the pairing needs the shots.
  useEffect(() => {
    if (!twinId) return setTwin(null)
    api.get(`/api/sessions/${twinId}`).then(setTwin).catch(() => setTwin(null))
  }, [twinId])

  // However the panel was opened — "add shots", "more like this", a kind switch —
  // it opens on what the session is currently wearing.
  useEffect(() => { if (adding) setWorn(s?.wardrobe || '') }, [adding !== null])

  // Only poll while it runs: the queue is serial, one photo every few seconds.
  useEffect(() => {
    if (!s || s.status !== 'running') return
    const t = setInterval(reload, 2500)
    return () => clearInterval(t)
  }, [s?.status, id])

  useEffect(() => {
    if (s?.status === 'running') {
      setActiveStep('generation')
    }
  }, [s?.status])

  if (!s) return <p className="muted">{error || 'Loading…'}</p>

  const automaticIncompleteTakeIds = incompleteAuthoringTakeIds()
  const incompleteTakeIds = incompletePlanTakeIds()
  const automaticBatchTakeIds = automaticIncompleteTakeIds.slice(0, MAX_AUTOMATIC_TAKE_BATCH)
  const authoringOperationStale = Boolean(
    sharedOperation && Number.isInteger(planRevision)
    && sharedOperation.plan_revision !== planRevision,
  )
  const authoringOperationActive = Boolean(
    sharedOperation && ['active', 'cancel_requested'].includes(sharedOperation.state),
  )
  const preparationRetryUnknown = Boolean(
    preparationStartUnknown
    && preparationStartRequestRef.current?.kind === 'prepare_takes',
  )

  const done = s.shots.filter((x) => x.status === 'done').length
  const failed = s.shots.filter((x) => ['failed', 'cancelled'].includes(x.status)).length
  const pending = s.shots.filter((x) => x.status === 'pending').length
  const shots = s.shots.filter((x) => (
    filter === 'picks' ? x.rating >= 4 && !x.rejected
      : filter === 'keep' ? !x.rejected
        : true))

  const call = async (fn) => { try { await fn(); reload() } catch (e) { setError(e.message) } }

  // Which rooms this run would be refused in, before anything is queued. The
  // route recomputes nothing: every reason comes from `room_refusal`, the
  // function the run itself calls, so the report and the refusal cannot
  // disagree. Asked here because `with_him` is a property of the RUN and
  // this is where it is set - the picker two screens back does not know it.
  const askRooms = async () => {
    try { setRoomReport(await api.post('/api/rooms/preflight', { with_him: withHim })) }
    catch (e) { setError(e.message) }
  }

  // Compose a run of N photographs from the catalogue. The button is on
  // an EXISTING session (not in the create flow) because the 3.2 rework
  // lifted `manner` and `checkpoint` out of the editor onto the row, and
  // the compose endpoints add shots to a session that already carries
  // them; putting this in the create form would mean duplicating the
  // checkpoint derivation before there is a row to derive it onto. The
  // 422 path is the refusal 3.3 already wrote (`backend/main.py`),
  // surfaced verbatim through the same `setError(e.message)` the other
  // call sites use — the slot, its verified count, the largest fillable
  // count and the word "exploratory" all reach the screen the way the
  // operator's eye expects them.
  // The slip and the defect are dealt HERE and not on a fill-cell: a cell is
  // measured by ten photographs that differ in nothing but the seed, and a
  // clause that changes on every row is the noise the measurement is trying to
  // see through. A run is a shoot, and a shoot carries them.
  const composeRun = (n, mode) => call(async () => {
    const candidates = candidatePool(s.manner)
    const dealt = spread(wardrobeArc(s), n)
    await api.post(`/api/sessions/${id}/compose-run`, {
      count: n, candidates, mode, mute_wardrobe: muteWardrobe, reference: composeGuided,
      with_him: withHim, with_furniture: withFurniture, bare,
      extras: extrasFor(s.manner, n),
      // The arc, spread over the run by the same function that spreads it over
      // written takes: K states, N photographs, the wardrobe holding still
      // between two photographs of one stage. No states is an empty array, and
      // every photograph is then composed in the session's own wardrobe.
      // The arc is spread ONCE and then split, so the sentence a photograph is
      // composed with and the access answer it is judged drawable by come from
      // the same stage. Spreading the two lists separately is two calculations
      // that agree until somebody changes one of them.
      wardrobes: dealt.map((x) => x.text),
      access: dealt.map((x) => x.access),
    })
  })

  // Fill a cell: queue N photographs of the picked trio on this
  // session. The cell check (verified in strict, unknown refused
  // in strict but drawable in exploratory, dead refused in both)
  // runs ONCE on the backend before any insert; a 422 leaves
  // nothing queued, and the response's `detail` reaches the
  // screen verbatim through `setError(e.message)` the way 8.3
  // already pins. The trio is built from the same `candidatePool`
  // the Compose control reads — picking the first wording of
  // each selected concept — so the payload matches the shape
  // `/compose` reads, and the operator sees no second list to
  // learn.
  // The control arm: a cell shot with NO phrase for that slot, so a wording's
  // arrival rate can be read against what the model does when nobody asks. It
  // is not a catalogue row — the component table refuses an empty wording, and
  // a row carrying any text at all is a treatment rather than a control. The
  // empty text is dropped by the same `_sentences` join the writer goes
  // through, and the cell records the slot as `none`.
  const NONE = { key: 'none', wordings: [{ key: 'none', text: '' }] }
  const pick = (list, key) => (key === 'none' ? NONE : list.find((c) => c.key === key) || list[0])

  // Every slot goes as a LIST. The endpoint takes the cross product and
  // checks every cell before inserting anything, so a selection whose
  // ninth combination is dead queues nothing at all — the same rule the
  // count already kept for one cell.
  const fillCells = fillCellCamera.length * fillCellAct.length * fillCellFraming.length

  const fillCell = (camKeys, actKeys, framingKeys, n, mode) => call(async () => {
    const pool = candidatePool(s.manner)
    await api.post(`/api/sessions/${id}/compose`, {
      camera: camKeys.map((k) => pick(pool.camera, k)),
      act: actKeys.map((k) => pick(pool.act, k)),
      framing: framingKeys.map((k) => pick(pool.framing, k)),
      count: n, mode, mute_wardrobe: muteWardrobe, reference: composeGuided,
    })
  })

  // Which photograph guides THIS take. Empty means "follow the session's 📎
  // pick", which is what every take did before takes could choose. Only offered
  // on a pending reference take: a finished one already ran against whatever it
  // ran against, and the column is the record of that.
  const guideWith = (shot, value) => call(async () => {
    await api.patch(`/api/shots/${shot.id}`, {
      reference_shot_ids: value ? [Number(value)] : [],
    })
  })

  const rate = (shot, rating) => call(async () => {
    await api.patch(`/api/shots/${shot.id}`, { rating: shot.rating === rating ? 0 : rating })
  })

  // The stored prompt already carries trigger + base + look, so reshooting from
  // a keeper reuses it whole rather than recomposing and drifting. `reference` is
  // carried over too: a reshoot of an edit that came back as a fresh text2image
  // would silently be a different picture.
  const moreLikeThis = (shot) => setAdding([{
    label: shot.shot_label, prompt: shot.prompt, negative: shot.negative, count: 4,
    verbatim: true, reference: !!shot.use_reference,
    reference_strength: shot.reference_strength, seed: 0,
  }])

  // Same prompt AND same noise: edit one word in the panel and the difference you
  // see is that word, not another seed.
  // For a reference take this is the strength sweep: four rows, one prompt, one
  // seed, a different strength each. Whatever changes is the strength.
  const reshootSameSeed = (shot) => setAdding(
    (shot.use_reference ? [1.0, 1.5, 2.0, 3.0] : [null]).map((strength) => ({
      label: strength ? `${shot.shot_label} @${strength}` : shot.shot_label,
      prompt: shot.prompt, negative: shot.negative, count: 1,
      verbatim: true, reference: !!shot.use_reference,
      reference_strength: strength, seed: shot.seed,
    })))

  // The reference this shot really ran against, not whatever the session points
  // at now. A shot from before the feature existed has none, and gets no slider.
  const before = (shot) => (shot.reference_shot_ids || [])[0]

  // The copies of this shoot, and only those: two sessions are comparable when
  // the takes, the prompts and the seeds are the same, which is exactly what a
  // clone guarantees and nothing else does. A clone of a clone carries the same
  // root, so the family is flat and every member sees every other one.
  const root = s?.settings?.cloned_from || s?.id
  const family = sessions.filter((x) => x.id !== s?.id && (x.settings?.cloned_from || x.id) === root)

  // What makes two photos the same take: the id of the take they were both
  // copied from. A shot of the session that was cloned is its own original, and
  // a clone of a clone carries the same id, so the whole family pairs up.
  //
  // NOT the seed. Reshooting (↺) rolls a new one on purpose — that is the button
  // for a frame that came back wrong — and pairing on the seed loses the twin at
  // exactly the moment you reshot the photo you wanted to compare.
  const takeKey = (x) => `take ${x.origin_shot_id || x.id}`
  // Copies made before the take id existed carry neither, so they keep pairing
  // the way they always did: the row it belongs to and its noise. Not the seed
  // alone — a strength sweep (⚖) pins one seed across four rows; not the row
  // alone — a take with count 4 is four variations under one index.
  const seedKey = (x) => `seed ${x.shot_index}|${x.seed}`
  const twinShots = {}
  for (const x of (twin?.shots || [])) if (x.status === 'done') {
    twinShots[takeKey(x)] = x
    twinShots[seedKey(x)] ??= x
  }
  const twinOf = (shot) => twinShots[takeKey(shot)] || twinShots[seedKey(shot)]
  const shotWith = (session) => `${session.name} · ${session.settings?.checkpoint || "the workflow's own"}`

  // Null for a session created before kinds existed: no badge, no filtering and
  // no guidance beats a wrong guess about what that session was for.
  const kind = sessionKind(s)
  const anchors = s.anchor_shot_ids || []
  // The same two counts the run preflight uses: takes that need a photo to edit,
  // and takes that would produce one.
  const refTakes = s.shots.filter((x) => x.use_reference && x.status === 'pending').length
  const willShoot = s.shots.some((x) => !x.use_reference && x.status === 'pending')
  const running = s.status === 'running' || s.running
  // As many anchors as the reference workflow actually reads. Keeping three
  // regardless made 📎 on a keeper *add* a second reference to a graph with one
  // slot, and the run is then refused for a count mismatch — a guaranteed
  // refusal produced by the button whose whole job is picking the photo to edit.
  // Re-pointing a one-slot session is now one click, not unpin-then-pin. An
  // unknown workflow falls back to three, the most any graph can read.
  const refWf = workflows.find((w) => w.id === s.reference_workflow_id)
  const refSlots = refWf
    ? Math.max(1, ['reference', 'reference2', 'reference3'].filter((r) => refWf.node_map?.[r]).length)
    : 3
  // Which of the session's numbers actually reach ComfyUI. An unmapped slot is
  // not patched at all — the graph's own widget value stands — so printing the
  // session's number next to it is a lie, and the lie is the whole reason
  // "one workflow per model" looks mysterious instead of deliberate. Until the
  // workflows have loaded, assume mapped: the honest state is the quiet one.
  const shootWf = workflows.find((w) => w.id === (s.workflow_id || s.model.workflow_id))
  const maps = (slots) => !shootWf || slots.split(' ').every((x) => !!shootWf.node_map?.[x])
  // Struck through and explained rather than hidden: the number is still what a
  // clone of this session would carry, it just is not what this graph shoots.
  const Sent = ({ slot, children }) => (maps(slot) ? <>{children}</> : (
    <span style={{ textDecoration: 'line-through' }}
          title={`This workflow does not map ${slot.split(' ').join(' or ')} — its own value is what runs`}>
      {children}
    </span>
  ))
  // Same question one row of the Clone panel at a time: a copy shoots through
  // its own graph, so whether its boxes drive anything is that graph's answer,
  // not this session's.
  const rowWfOf = (r) => workflows.find((w) => w.id === Number(r.workflow_id)) || shootWf
  const rowMaps = (r, slot) => { const w = rowWfOf(r); return !w || !!w.node_map?.[slot] }
  // The graph written for a checkpoint is the one whose own loader names it, so
  // picking a base model can pick its workflow and nothing is typed twice. Only
  // the shooting graphs: an editing graph loads its own model by design.
  const wfFor = (checkpoint) => checkpoint
    && workflows.find((w) => w.kind === 't2i' && w.base_model === checkpoint)
  const tunedWf = wfFor(s.settings.checkpoint)
  const profile = checkpointProfile(config, s.settings.checkpoint)
  // Everything the Settings panel offers that this session's graphs ignore.
  // Denoise is the reference graph's dial, not the shooting one's.
  // Sampler and scheduler are opt-in, so they are only worth reporting when the
  // session actually picks one — listed unconditionally, every graph that leaves
  // the pair alone would report two problems it does not have.
  const unmapped = ['steps', 'cfg', 'width', 'height', 'checkpoint', 'lora_strength']
    .concat(['sampler', 'scheduler'].filter((x) => s.settings[x]))
    .filter((x) => !maps(x))
    .concat(refWf && !refWf.node_map?.denoise ? ['denoise (the editing graph)'] : [])
  // Clicking one already picked drops it.
  const toggleAnchor = (shot) => call(() => api.patch(`/api/sessions/${id}`, {
    anchor_shot_ids: anchors.includes(shot.id)
      ? anchors.filter((a) => a !== shot.id)
      : [...anchors, shot.id].slice(-refSlots),
  }))

  // "Now edit this one" is one decision, and it used to be four clicks in three
  // places: the kind chip, the reference workflow, 📎 and + Shots. Into another
  // session it was worse — download the photo and upload it back, because
  // nothing carried a shot across. Every kind that edits a photo is offered.
  const continuations = Object.entries(KINDS).filter(([, spec]) => spec.refKind)

  /** The editing graph for the kind we are switching to. An untagged graph is
   *  offered everywhere so it stays; one tagged for the job we are leaving does
   *  not, or a photoshoot turned camera-angles runs its takes through the edit
   *  graph. 0 clears it, and the panels already say how to pick one. */
  const refWfFor = (k) => {
    // Before the list has loaded every graph looks untagged, and clearing the
    // session's own pick over a race is the one outcome worth guarding against.
    if (!workflows.length) return s.reference_workflow_id || 0
    const cur = workflows.find((w) => w.id === s.reference_workflow_id)
    if (cur && (!cur.kind || cur.kind === KINDS[k].refKind)) return cur.id
    const tagged = workflows.filter((w) => w.kind === KINDS[k].refKind)
    return tagged.length === 1 ? tagged[0].id : 0
  }

  const continueWith = (shot, choice) => {
    const [where, k] = choice.split(':')
    if (where === 'here') {
      return call(async () => {
        await api.patch(`/api/sessions/${id}`, {
          settings: { kind: k }, anchor_shot_ids: [shot.id], reference_workflow_id: refWfFor(k),
        })
        setAdding([blankShot(k)])
      })
    }
    // The new session starts with no look and no takes: the look belongs to the
    // shoot that produced the photo, and an edit take carries none anyway.
    call(async () => {
      const { id: sid } = await api.post('/api/sessions', {
        model_id: s.model_id, name: `${s.name} — ${KINDS[k].label}`,
        workflow_id: s.workflow_id, reference_workflow_id: refWfFor(k) || null,
        settings: { ...s.settings, kind: k },
      })
      const copy = await api.post(`/api/sessions/${sid}/import?from_shot=${shot.id}`)
      await api.patch(`/api/sessions/${sid}`, { anchor_shot_ids: [copy.id] })
      go(`/session/${sid}`)
    })
  }

  const tags = s.tags || []
  const addTag = (raw) => {
    const v = (raw || '').trim()
    if (!v) return
    if (tags.some((t) => t.toLowerCase() === v.toLowerCase())) {
      // Backend would dedupe it anyway; skipping the round-trip keeps the
      // response identical and the list from re-rendering for nothing.
      setTagDraft('')
      return
    }
    call(() => api.patch(`/api/sessions/${id}`, { tags: [...tags, v] })).then(() => setTagDraft(''))
  }
  const removeTag = (t) => call(() => api.patch(`/api/sessions/${id}`, {
    tags: tags.filter((x) => x !== t),
  }))

  const renderSavedSharedSummary = () => sharedSummary && (
    <section
      aria-label="Saved shared session summary"
      style={{
        border: '1px solid var(--line)',
        borderRadius: 8,
        padding: 12,
        marginBottom: 12,
        background: 'var(--panel-2)',
      }}
    >
      <h4 style={{ margin: '0 0 4px' }}>Saved Shared Session Summary</h4>
      <p className="muted" style={{ margin: '0 0 10px', fontSize: 12 }}>
        This is the authoritative saved summary. Pending suggestions remain proposals until you accept them.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
        {[
          ['look', 'Look'],
          ['initial_wardrobe', 'Initial Wardrobe'],
        ].map(([field, label]) => {
          const value = sharedSummary[field]
          return (
            <div key={field}>
              <b>{label}:</b> {value?.value === '' ? 'No additional constraint' : value?.value || 'Unavailable'}
              <div className="muted" style={{ fontSize: 11 }}>Origin: {value?.origin || 'Unavailable'}</div>
            </div>
          )
        })}
      </div>
      {!sharedSummary.available && (
        <p className="muted" style={{ margin: '8px 0 0', fontSize: 12 }}>
          {sharedSummary.message || 'Authorized scene descriptions are unavailable.'}
        </p>
      )}
    </section>
  )

  return (
    <>
      {error && <div className="error" onClick={() => setError('')}>{error}</div>}
      {planSaveImpact && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 1000, display: 'grid', placeItems: 'center',
          padding: 16, background: 'rgba(0, 0, 0, 0.72)',
        }}>
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="plan-save-impact-title"
            className="panel"
            style={{ width: 'min(680px, 100%)', maxHeight: '90vh', overflowY: 'auto' }}
          >
            <h3 id="plan-save-impact-title" style={{ marginTop: 0 }}>Review downstream impact before saving</h3>
            <p className="muted" style={{ fontSize: 13 }}>
              {planSaveImpact.reason === 'plan-wide'
                ? 'Plan-owned constants or automatic authoring inputs changed; existing ungenerated work may need new preparation.'
                : planSaveImpact.reason === 'automatic-downstream'
                  ? 'A take edit, removal, wardrobe change, or reorder changes later automatic authoring context.'
                  : 'This save changes take-level preparation inputs.'}
            </p>
            {!planSaveImpact.baselineAvailable && (
              <p role="alert" className="muted">The saved baseline is unavailable, so current takes are treated conservatively as affected.</p>
            )}
            <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
              <div><b>Affected takes:</b> {takeIdSummary(planSaveImpact.affectedTakeIds)}</div>
              <div><b>Ready takes requiring re-preparation:</b> {takeIdSummary(planSaveImpact.readyTakeIds)}</div>
              <div><b>Pending or incomplete affected takes:</b> {takeIdSummary(planSaveImpact.pendingTakeIds)}</div>
              <div><b>New takes needing first preparation:</b> {takeIdSummary(planSaveImpact.addedTakeIds)}</div>
              <div><b>Removed plan rows:</b> {takeIdSummary(planSaveImpact.removedTakeIds)}</div>
              <div><b>Generated or shot-linked history retained:</b> {takeIdSummary(planSaveImpact.generatedTakeIds)}</div>
            </div>
            <p className="muted" style={{ marginBottom: 0, fontSize: 12 }}>
              Saving does not approve the review, submit takes, or start Run. Generated photos and their history remain intact.
            </p>
            <div className="row" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
              <button type="button" onClick={() => setPlanSaveImpact(null)}>Cancel</button>
              <button
                type="button"
                className="primary"
                onClick={() => {
                  const confirmation = planSaveImpact
                  setPlanSaveImpact(null)
                  void savePlan({ impactConfirmed: true, confirmedPlan: confirmation.draft })
                }}
              >
                Save Plan Changes
              </button>
            </div>
          </section>
        </div>
      )}
      {roomReport && (
        <div className="muted" onClick={() => setRoomReport(null)}>
          {roomReport.refused.length === 0
            ? `All ${roomReport.rooms} rooms are shootable under this run.`
            : `${roomReport.refused.length} of ${roomReport.rooms} rooms are refused under this run:`}
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {roomReport.refused.map((r) => (
              <li key={r.key}>{r.label || r.key}: {r.reason}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div>
          <h1>{s.name}</h1>
          <p className="muted">
            <a href={`#/model/${s.model.id}`}>{s.model.name}</a> ·{' '}
            <Sent slot="width height">{s.settings.width}×{s.settings.height}</Sent> ·{' '}
            <Sent slot="steps">{s.settings.steps} steps</Sent> ·{' '}
            <Sent slot="cfg">cfg {s.settings.cfg}</Sent> ·{' '}
            <Sent slot="lora_strength">LoRA {s.settings.lora_strength}</Sent>
            {s.settings.sampler && <> · <Sent slot="sampler">{s.settings.sampler}</Sent></>}
            {s.settings.scheduler && <> · <Sent slot="scheduler">{s.settings.scheduler}</Sent></>}
          </p>
          {isResource ? (
            s.diagnostic ? (
              <p role="alert" className="muted" style={{ marginTop: -6 }}>
                <b>Resource plan needs attention{s.diagnostic.code ? ` (${s.diagnostic.code})` : ''}:</b> {s.diagnostic.message || 'Plan-owned look and wardrobe are unavailable.'}
              </p>
            ) : (
              <div className="muted" style={{ marginTop: -6, fontSize: 13 }}>
                <p style={{ margin: '0 0 3px' }}>
                  <b>Plan look:</b> {typeof s.look === 'string' ? (s.look || 'No additional look constraint') : 'Unavailable'}
                </p>
                <p style={{ margin: 0 }}>
                  <b>Initial wardrobe:</b> {typeof s.wardrobe === 'string' ? (s.wardrobe || 'No wardrobe description') : 'Unavailable'}
                </p>
              </div>
            )
          ) : s.look && <p className="muted" style={{ marginTop: -6 }}><b>Look:</b> {s.look}</p>}
          {/* Where the look came from, and the way out of saying so. The key
              and not a label: this screen does not load the room library, and
              a session can outlive the library it was filled from. Detaching
              patches the key alone - the look is not a field this route
              accepts, so the words cannot move with it. */}
          {!isResource && s.room_key && (
            <p className="muted" style={{ marginTop: -6 }}>
              Filled from <code>{s.room_key}</code>{' '}
              <button onClick={() => call(() => api.patch(`/api/sessions/${id}`, { room_key: '' }))}
                      title="Stop recording which room this look came from. The look itself is untouched.">
                Detach
              </button>
            </p>
          )}
          {!isResource && s.wardrobe && (
            <p className="muted" style={{ marginTop: -6 }}>
              <b>Wardrobe:</b> {s.wardrobe} <i>— what a take wears unless it says otherwise</i>
            </p>
          )}
          {/* Tag editor. Existing tags as removable badges, plus an input that
              opens on focus and stays open for as long as it has text. The
              Library screen reads the same column, so a tag added here shows
              up in the chip row on the next visit. */}
          <div className="row" style={{ marginTop: -4, marginBottom: 4, gap: 6 }}>
            {tags.map((t) => (
              <span key={t} className="badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                {t}
                <button className="icon" style={{ padding: '0 2px', border: 'none', background: 'transparent',
                                                  color: 'var(--muted)', cursor: 'pointer' }}
                        onClick={() => removeTag(t)} title={`Remove tag "${t}"`}>×</button>
              </span>
            ))}
            {(tagsOpen || tags.length > 0) && (
              <form onSubmit={(e) => { e.preventDefault(); addTag(tagDraft) }}
                    style={{ display: 'flex', gap: 4, flex: '0 1 200px' }}>
                <input value={tagDraft} onChange={(e) => setTagDraft(e.target.value)}
                       onBlur={() => { if (!tagDraft) setTagsOpen(false) }}
                       onFocus={() => setTagsOpen(true)}
                       placeholder="Add a tag" style={{ width: '100%' }} />
                <button type="submit" disabled={!tagDraft.trim()}>Add</button>
              </form>
            )}
            {!tagsOpen && tags.length === 0 && (
              <button onClick={() => setTagsOpen(true)} title="Mark this session with a tag, then find it from Library">+ Tag</button>
            )}
          </div>
          {anchors.length > 0 ? (
            <div className="anchor">
              {anchors.map((a) => <img key={a} src={shotImage(a)} alt="" title={`Reference — shot ${a}`} />)}
              <span className="muted">
                Reference · takes marked <b>ref</b> edit this photo, so their prompt is an
                instruction and carries no look.
              </span>
            </div>
          ) : refTakes > 0 && (
            // The state that used to show nothing at all, which is the one state
            // where you need to be told: takes that edit a photo, and no photo.
            // Left to Run, it is a refusal several clicks after the decision.
            <p className="rule">
              <b>No reference photo yet.</b> {refTakes === 1 ? '1 take edits' : `${refTakes} takes edit`} one.
              {willShoot
                ? ' The first photo this session shoots becomes it, and the edits follow in the same Run.'
                : ' Nothing here shoots one, so Run is refused: Import photo… (it becomes the reference), '
                  + 'or 📎 a finished photo, or add a take with ref unticked.'}
            </p>
          )}
        </div>
        <div className="row">
          {kind && <span className="badge" title={KINDS[kind].blurb}>{KINDS[kind].label}</span>}
          {canGenerateSession(s, {
            plan,
            planRevision,
            planDirty,
            conflicts: planConflictMarkers,
            activeStep,
            reviewedRevision,
            pending,
          }) && (
            <button className="primary" onClick={() => call(() => api.post(`/api/sessions/${id}/run`))}>
              Run ({pending})
            </button>
          )}
          {isLegacyControlVisible(s, 'compose') && (
            <>
              {/* The compose control: a count, a mode, and a button, on the session
                  that's already open. The candidate pool is the whole catalogue slice
                  for the session's manner (see compose.js for the per-manner rule);
                  the framing is fixed and the screen says so, because picking
                  framings is a measurement decision not yet made. Disabled when
                  manner or checkpoint is missing, with the reason on the title —
                  the same refusal the endpoint will give, surfaced before the click
                  so the operator does not pay a round trip to learn what is
                  missing. Mode defaults to "exploratory" (8.4): a strict default
                  makes the first use of this feature a 422 on a 17-row, 2-trios
                  cell table, and reads as broken. */}
              <span className="muted" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <label title="Compose the line without the session's wardrobe, so a reference image can deliver the clothing instead. With no reference attached the line renders her undressed."
                       style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                  <input type="checkbox" checked={muteWardrobe}
                         disabled={!s.manner || !s.checkpoint || s.running}
                         onChange={(e) => setMuteWardrobe(e.target.checked)} />
                  no wardrobe
                </label>
                <label title="He is in the room for this run. Off: an act with a second person in it is not drawn at all, which is what keeps a dressed photograph from coming back as penetration. On does not mean only him - the whole act list is drawable again."
                       style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                  <input type="checkbox" checked={withHim}
                         disabled={!s.manner || !s.checkpoint || s.running}
                         onChange={(e) => setWithHim(e.target.checked)} />
                  with him
                </label>
                <label title="The room has furniture in it for this run. Off: an act that sits her on a chair, a bed, a counter or a stair is not drawn at all, because nothing in the prompt describes the room and naming a piece builds it - measured seven times in eight. On does not mean only furniture - the whole act list is drawable again."
                       style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                  <input type="checkbox" checked={withFurniture}
                         disabled={!s.manner || !s.checkpoint || s.running}
                         onChange={(e) => setWithFurniture(e.target.checked)} />
                  furniture
                </label>
                {/* The three run subjects. A checkbox alone would be the failure they
                    exist to prevent - the writer would invent one - so the words
                    come with the switch, and the run is refused while a box is on
                    and its words are empty. */}
                {RUN_SUBJECTS.map((subject) => (
                  <label key={subject.flag}
                         title={`This shoot has one, written in your words and carried into the \`${subject.field}\` field of every photograph that does not change it. Off: nothing about it reaches the writer at all, and a line that describes one anyway is flagged.`}
                         style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                    <input type="checkbox" checked={subjects[subject.flag]}
                           disabled={s.running}
                           onChange={(e) => setSubjects({ ...subjects,
                                                          [subject.flag]: e.target.checked })} />
                    {subject.input}
                    {subjects[subject.flag] && (
                      <input type="text" value={subjects[subject.input]} disabled={s.running}
                             placeholder={`what the ${subject.input} is, in your words`}
                             style={{ width: 220 }}
                             onChange={(e) => setSubjects({ ...subjects,
                                                            [subject.input]: e.target.value })} />
                    )}
                  </label>
                ))}
                {/* The gates report before the run, not during it: an operator finds
                    out a room is unshootable today by sending the run and reading the
                    422, which is fine for one room and useless for a picker with
                    hundreds in it. Nothing is queued by asking. */}
                <button className="icon" onClick={askRooms}
                        title="Which rooms this run would be refused in, and why. Queues nothing.">
                  rooms?
                </button>
                <label title="The fallback answer for a photograph the arc says nothing about. An act that needs access - a toy, a hand between her legs - is drawn where the dealt wardrobe gives access: nothing covering her below the waist, or the garment pulled aside. This decides the photographs an outfit does not: a hand-typed arc, or a session with no arc at all."
                       style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                  <input type="checkbox" checked={bare}
                         disabled={!s.manner || !s.checkpoint || s.running}
                         onChange={(e) => setBare(e.target.checked)} />
                  access
                </label>
                {/* Only offered when the session has a reference graph to send them
                    through: without one the runner falls back to the text2image
                    workflow and the flag is a lie the row still records. */}
                <label title="Shoot these takes through the session's reference workflow, so the anchor photograph can carry what the line leaves out."
                       style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                  <input type="checkbox" checked={composeGuided}
                         disabled={!s.reference_workflow_id || !s.manner || !s.checkpoint || s.running}
                         onChange={(e) => setComposeReference(e.target.checked)} />
                  guided
                </label>
                <input type="number" min={1} max={50} value={composeCount}
                       disabled={!s.manner || !s.checkpoint || s.running}
                       onChange={(e) => setComposeCount(Math.max(1, Number(e.target.value) || 1))}
                       style={{ width: 60 }}
                       title="How many photographs to compose and queue" />
                <select value={composeMode}
                        disabled={!s.manner || !s.checkpoint || s.running}
                        onChange={(e) => setComposeMode(e.target.value)}
                        title="exploratory draws unknown and verified cells (never dead); strict draws verified only">
                  <option value="exploratory">exploratory</option>
                  <option value="strict">strict</option>
                </select>
                <button disabled={!s.manner || !s.checkpoint || s.running || composeCount < 1}
                        onClick={() => composeRun(composeCount, composeMode)}
                        title={!s.manner || !s.checkpoint
                          ? `Compose needs manner and checkpoint (manner="${s.manner || ''}", checkpoint="${s.checkpoint || ''}")`
                          : `Compose ${composeCount} ${composeMode} photograph${composeCount === 1 ? '' : 's'} from the catalogue`}>
                  Compose
                </button>
              </span>
              {/* The fill-cell control: pick one trio, queue N photographs of it on
                  this session so an operator can take a single cell to its
                  `judged=10` threshold without a script. The camera and act
                  are <select>s of the catalogue slice the Compose control also
                  reads (`candidatePool(manner)`), one per slot. Default count is 10 — the
                  threshold `db.cell_state` reads — so a single press queues
                  the batch that pushes a cell to verified or dead. Strict
                  mode refuses unknowns (the cell check 3.2 already pinned);
                  exploratory draws them too. The 422 path is the same
                  `setError(e.message)` the Compose control uses. */}
              <span className="muted" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <select multiple size={4} value={fillCellCamera}
                        disabled={!s.manner || !s.checkpoint || s.running}
                        onChange={(e) => setFillCellCamera([...e.target.selectedOptions].map((o) => o.value))}
                        title="Camera concepts for the fill-cell compose — pick several and every combination becomes its own cell">
                  {candidatePool(s.manner).camera.map((x) => (
                    <option key={x.key} value={x.key}>{x.key}</option>
                  ))}
                  <option value="none">none (control)</option>
                </select>
                <select multiple size={4} value={fillCellAct}
                        disabled={!s.manner || !s.checkpoint || s.running}
                        onChange={(e) => setFillCellAct([...e.target.selectedOptions].map((o) => o.value))}
                        title="Act concepts for the fill-cell compose — pick several and every combination becomes its own cell">
                  {candidatePool(s.manner).act.map((x) => (
                    <option key={x.key} value={x.key}>{x.key}</option>
                  ))}
                  <option value="none">none (control)</option>
                </select>
                <select multiple size={4} value={fillCellFraming}
                        disabled={!s.manner || !s.checkpoint || s.running}
                        onChange={(e) => setFillCellFraming([...e.target.selectedOptions].map((o) => o.value))}
                        title="Framing concepts for the fill-cell compose — pick several and every combination becomes its own cell">
                  {candidatePool(s.manner).framing.map((x) => (
                    <option key={x.key} value={x.key}>{x.key}</option>
                  ))}
                  <option value="none">none (control)</option>
                </select>
                <input type="number" min={1} max={50} value={fillCellCount}
                       disabled={!s.manner || !s.checkpoint || s.running}
                       onChange={(e) => setFillCellCount(Math.max(1, Number(e.target.value) || 1))}
                       style={{ width: 50 }}
                       title="How many photographs of this trio to queue" />
                <select value={fillCellMode}
                        disabled={!s.manner || !s.checkpoint || s.running}
                        onChange={(e) => setFillCellMode(e.target.value)}
                        title="Mode for the fill-cell compose (strict refuses unknown cells; exploratory draws them)">
                  <option value="exploratory">exploratory</option>
                  <option value="strict">strict</option>
                </select>
                <button disabled={!s.manner || !s.checkpoint || s.running || fillCellCount < 1
                                  || !fillCellCamera.length || !fillCellAct.length || !fillCellFraming.length}
                        onClick={() => fillCell(fillCellCamera, fillCellAct, fillCellFraming, fillCellCount, fillCellMode)}
                        title={!s.manner || !s.checkpoint
                          ? `Fill cell needs manner and checkpoint (manner="${s.manner || ''}", checkpoint="${s.checkpoint || ''}")`
                          : `${fillCells} cell${fillCells === 1 ? '' : 's'} × ${fillCellCount} = ${fillCells * fillCellCount} ${fillCellMode} photographs. Every cell is checked before any of them is queued.`}>
                  Fill {fillCells * fillCellCount} photo{fillCells * fillCellCount === 1 ? '' : 's'}
                </button>
              </span>
            </>
          )}
          {s.status === 'running' &&
            <button onClick={() => call(() => api.post(`/api/sessions/${id}/cancel`))}>Cancel</button>}
          {failed > 0 && s.status !== 'running' &&
            <button onClick={() => call(() => api.post(`/api/sessions/${id}/retry`))}>Retry {failed}</button>}
          <button onClick={() => setSettingsOpen(!settingsOpen)}
                  title="The workflows and the base model this session shoots with">⚙ Settings</button>
          <button onClick={() => setClone(clone ? null : {
            name: s.name,
            steps: s.settings.steps ?? '',
            // It opens on what this session shoots with, so pressing Create
            // straight away is the plain copy. Every other model is one more row.
            rows: [{ checkpoint: s.settings.checkpoint || '', steps: s.settings.steps ?? '',
                     cfg: s.settings.cfg ?? '', sampler: s.settings.sampler ?? '',
                     scheduler: s.settings.scheduler ?? '' }],
          })} title="Shoot this whole session again on other base models — same takes, same seeds">
            ⧉ Clone
          </button>
          {isLegacyControlVisible(s, 'add_shots') && (
            <button onClick={() => setAdding(adding ? null : [blankShot(kind)])}>+ Shots</button>
          )}
          {/* The native file input renders its label in the browser's locale, so
              it is hidden behind our own, the same way Workflows does it. */}
          <label className="filebtn" title="Bring in a photo from outside — it lands as a finished shot, so it can be marked as a reference like any other">
            Import photo…
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden
                   onChange={(e) => {
                     const file = e.target.files[0]
                     e.target.value = ''   // same file twice in a row still fires
                     if (file) call(() => api.upload(`/api/sessions/${id}/import`, file))
                   }} />
          </label>
          <button className="danger" onClick={() => {
            if (confirm('Delete this session and its images?')) call(async () => {
              const r = await api.del(`/api/sessions/${id}`)
              if (r?.warning) alert(r.warning)
              go(`/model/${s.model.id}`)
            })
          }}>Delete</button>
        </div>
      </div>

      {isResource && (
        <div className="panel" style={{ marginBottom: 14 }}>
          {sharedOperation && (
            <section
              aria-label="Authoring operation"
              style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 10, marginBottom: 14 }}
            >
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <div>
                  <b>Authoring operation</b>{' '}
                  <code>{sharedOperation.operation_id}</code>{' '}
                  <span className={`badge ${sharedOperation.state}`}>{sharedOperation.state}</span>
                  <span className="muted"> · {sharedOperation.kind} · plan revision {sharedOperation.plan_revision}</span>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  {sharedOperation.can_cancel && (
                    <button
                      type="button"
                      onClick={cancelAuthoringOperation}
                      disabled={preparationActionBusy}
                    >
                      {preparationActionBusy ? 'Working…' : 'Cancel operation'}
                    </button>
                  )}
                  {!authoringOperationStale && sharedOperation.can_resume && (
                    <button
                      type="button"
                      onClick={resumeAuthoringOperation}
                      disabled={preparationActionBusy}
                    >
                      {preparationActionBusy ? 'Working…' : 'Resume operation'}
                    </button>
                  )}
                </div>
              </div>
              <div
                style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 6, marginTop: 8, fontSize: 12 }}
              >
                <span><b>Requested:</b> {Array.isArray(sharedOperation.progress?.requested) ? (sharedOperation.progress.requested.join(', ') || 'None') : 'None'}</span>
                <span><b>Completed:</b> {Array.isArray(sharedOperation.progress?.completed) ? (sharedOperation.progress.completed.join(', ') || 'None') : 'None'}</span>
                <span><b>Failed:</b> {sharedOperation.progress?.failed
                  ? `${sharedOperation.progress.failed.take_id}: ${sharedOperation.progress.failed.error}`
                  : 'None'}</span>
                <span><b>Remaining:</b> {Array.isArray(sharedOperation.progress?.remaining) ? (sharedOperation.progress.remaining.join(', ') || 'None') : 'None'}</span>
              </div>
              {sharedOperation.error && (
                <p className="muted" style={{ margin: '8px 0 0', fontSize: 12 }}>{sharedOperation.error}</p>
              )}
              {authoringOperationStale && (
                <p className="error" role="alert" style={{ margin: '8px 0 0', fontSize: 12 }}>
                  This operation belongs to an older plan revision. Reload the saved plan before continuing.
                </p>
              )}
              {!authoringOperationStale && !sharedOperation.can_resume
                && ['failed', 'cancelled', 'expired'].includes(sharedOperation.state)
                && (preparationActionCode === 'assistant_unavailable' || !llm) && (
                  <p className="muted" style={{ margin: '8px 0 0', fontSize: 12 }}>
                    The prompt assistant is unavailable. <a href="#/setup">Configure assistant</a>, then resume this operation.
                  </p>
                )}
              {!authoringOperationStale && !sharedOperation.can_resume
                && ['failed', 'cancelled', 'expired'].includes(sharedOperation.state)
                && preparationActionCode !== 'assistant_unavailable' && llm && (
                  <div style={{ marginTop: 8 }}>
                    <p className="muted" style={{ margin: '0 0 6px', fontSize: 12 }}>
                      Resume is unavailable because the saved inputs need to be checked. Reload the saved plan before starting new preparation.
                    </p>
                    <button
                      type="button"
                      onClick={reloadSavedPlanForOperation}
                      disabled={preparationActionBusy || planDirty}
                    >
                      Reload saved plan
                    </button>
                  </div>
                )}
              {authoringOperationStale && (
                <button
                  type="button"
                  onClick={reloadSavedPlanForOperation}
                  disabled={preparationActionBusy || planDirty}
                  style={{ marginTop: 8 }}
                >
                  Reload saved plan
                </button>
              )}
            </section>
          )}
          {preparationActionError && (
            <div className="error" role="alert" style={{ marginBottom: 12, padding: 8 }}>
              {preparationActionError}{' '}
              {preparationActionCode === 'assistant_unavailable' && <a href="#/setup">Configure assistant</a>}
              {['plan_revision_stale', 'operation_status_invalid', 'operation_status_unavailable', 'operation_not_found'].includes(preparationActionCode)
                && !authoringOperationStale && (
                  <button type="button" onClick={reloadSavedPlanForOperation} disabled={preparationActionBusy || planDirty}>
                    Reload saved plan
                  </button>
                )}
            </div>
          )}
          {!canPreparePlan({ plan, planRevision }) ? (
            <div>
              <h3>Resource Session Plan Incomplete</h3>
              <p className="muted" style={{ margin: '0 0 10px' }}>
                {error || 'No valid plan draft could be loaded for this session.'} Preparation and generation are blocked until a valid draft is available.
              </p>
            </div>
          ) : (
            <>
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div className="row" style={{ gap: 6 }}>
              <button
                type="button"
                className={'chip' + (activeStep === 'character' ? ' on' : '')}
                onClick={() => navigateStep('character')}
              >
                1. Character
              </button>
              <button
                type="button"
                className={'chip' + (activeStep === 'constants' ? ' on' : '')}
                onClick={() => navigateStep('constants')}
              >
                2. Scene / Constants
              </button>
              <button
                type="button"
                className={'chip' + (activeStep === 'takes' ? ' on' : '')}
                onClick={() => navigateStep('takes')}
              >
                3. Takes {plan?.takes ? `(${plan.takes.length})` : ''}
              </button>
              <button
                type="button"
                className={'chip' + (activeStep === 'review' ? ' on' : '')}
                onClick={() => navigateStep('review')}
              >
                4. Review
              </button>
              <button
                type="button"
                className={'chip' + (activeStep === 'generation' ? ' on' : '')}
                onClick={() => navigateStep('generation')}
                disabled={activeStep !== 'generation' && (
                  activeStep !== 'review' ||
                  reviewBlocksFinalization ||
                  !canProceedToGeneration(s, { plan, planRevision, planDirty, conflicts: planConflictMarkers, reviewedRevision })
                )}
                title={
                  activeStep !== 'generation' && activeStep !== 'review'
                    ? 'Review step must be completed before generation'
                    : planDirty
                      ? 'Plan has unsaved changes'
                      : hasKnownStaleAdaptations
                        ? 'A reviewed adaptation is stale; save a new plan revision before proceeding'
                        : (activeStep === 'review' && planReviewStatus !== 'ready')
                          ? 'All take reviews must load successfully before proceeding'
                        : (hasPlanConflictMarkers && reviewedRevision !== planRevision)
                          ? 'Approve Review for the current plan revision before proceeding with resource markers'
                        : !plan?.takes?.length
                          ? 'Plan must contain at least one take'
                          : !Boolean(s?.workflow_id || s?.model?.workflow_id || s?.settings?.workflow_id)
                            ? 'Session has no workflow assigned'
                          : hasPlanConflictMarkers
                            ? 'Resource markers remain visible; this plan revision is approved'
                            : ''
                }
              >
                5. Generation {s.shots?.length ? `(${s.shots.length})` : ''}
              </button>
            </div>
            <div className="row" style={{ gap: 8 }}>
              {planRevision !== null && (
                <span className="badge" title="Current plan revision">Rev {planRevision}</span>
              )}
              {planDirty && (
                <span className="badge pending" title="Unsaved changes in draft plan">Unsaved changes</span>
              )}
              {planNotice && (
                <span className="badge ready">{planNotice}</span>
              )}
            </div>
          </div>

          {activeStep === 'character' && (
            <div>
              <h3>1. Character</h3>
              <p className="muted" style={{ margin: '0 0 10px' }}>
                Character identity is fixed for this session. Base model and LoRA parameters apply to all takes.
              </p>
              <div className="grid-form" style={{ marginBottom: 12 }}>
                <div>
                  <label>Model</label>
                  <input readOnly disabled value={s.model?.name || s.model?.id || ''} />
                </div>
                <div>
                  <label>Trigger / Prefix</label>
                  <input readOnly disabled value={s.model?.trigger || '(none)'} />
                </div>
                <div>
                  <label>Base Model / Checkpoint</label>
                  <input readOnly disabled value={s.checkpoint || s.settings?.checkpoint || 'Default'} />
                </div>
                <div>
                  <label>LoRA Strength</label>
                  <input readOnly disabled value={s.settings?.lora_strength ?? '1.0'} />
                </div>
              </div>
              <p className="muted" style={{ fontSize: 12 }}>
                To adjust checkpoint profiles or LoRA strength, use the ⚙ Settings panel above.
              </p>
              <div className="row" style={{ marginTop: 12 }}>
                <span className="spacer" style={{ flex: 1 }} />
                <button className="primary" onClick={() => navigateStep('constants')}>
                  Next: Scene / Constants →
                </button>
              </div>
            </div>
          )}

          {activeStep === 'constants' && (
            <div>
              <h3>2. Scene / Constants</h3>
              <p className="muted" style={{ margin: '0 0 10px' }}>
                Set constants shared across the session. The look sets appearance, location, and lighting.
              </p>
              {plan?.authoring && (
                <>
                  {renderSavedSharedSummary()}
                  <section aria-label="Shared choices" style={{ marginBottom: 14 }}>
                    <h4 style={{ margin: '0 0 4px' }}>Shared Choices</h4>
                    <p className="muted" style={{ margin: '0 0 8px', fontSize: 12 }}>
                      Edit the saved values directly, record an empty choice, or review an assistant proposal before accepting it.
                    </p>
                    <SessionLookProgression
                      key={`session-look-${id}`}
                      sessionId={id}
                      plan={plan}
                      revision={planRevision}
                      disabled={
                        planDirty || planRevision === null || planEditLocked
                        || sharedStartBusy || sharedStartUnknown
                        || preparationActionBusy || preparationStartUnknown
                        || resourceRefreshBusy
                        || ['active', 'cancel_requested'].includes(sharedOperation?.state)
                      }
                      onBusyChange={(busy) => {
                        if (
                          sessionViewMountedRef.current
                          && sessionViewEpochRef.current === sessionRequestEpoch
                          && String(sessionViewIdRef.current) === String(id)
                        ) setLookProgressionBusy(busy)
                      }}
                      onReload={(minimumRevision) => (
                        reloadAuthoritativePlan(id, minimumRevision, sessionRequestEpoch)
                      )}
                    />
                    <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                      {[
                        ['look', 'Choose no additional look constraint'],
                        ['initial_wardrobe', 'Choose no initial wardrobe constraint'],
                      ].filter(([field]) => (
                        (plan[field] ?? '') === ''
                        && plan.authoring.shared_state?.[field]?.origin !== 'user'
                      )).map(([field, label]) => (
                        <button
                          key={field}
                          type="button"
                          onClick={() => savePlan({ sharedDecisions: [field] })}
                          disabled={planDirty || planEditLocked || planRevision === null}
                          title={planDirty ? 'Save or discard unsaved plan edits first' : 'Record the empty choice through the plan revision CAS'}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    {plan?.authoring?.mode === 'automatic' && (
                      <div style={{ borderTop: '1px solid var(--line)', paddingTop: 10, marginTop: 8 }}>
                        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                          <div>
                            <b>Shared suggestions</b>
                            <div className="muted" style={{ fontSize: 12 }}>
                              Suggestions do not affect the saved plan until you accept the edited values.
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={startSharedSuggestions}
                            disabled={
                              sharedStartBusy || sharedAcceptancePending || manualDecisionBusy
                              || lookProgressionBusy || !planSessionCurrent
                              || preparationStartUnknown || preparationActionBusy
                              || (!sharedStartUnknown && (
                                planDirty || planRevision === null
                                || ['active', 'cancel_requested'].includes(sharedOperation?.state)
                              ))
                            }
                          >
                            {sharedStartBusy
                              ? 'Starting…'
                              : sharedStartUnknown
                                ? 'Retry suggestion request'
                                : 'Generate shared suggestions'}
                          </button>
                        </div>
                        {sharedOperation?.kind === 'shared_suggestions' && sharedOperation.state === 'succeeded' && (
                          <div style={{ marginTop: 10 }}>
                            {Object.entries(sharedProposalDrafts).map(([field, value]) => (
                              <div key={field} style={{ marginBottom: 8 }}>
                                <label htmlFor={`shared-proposal-${field}`}>
                                  Proposed {field === 'look' ? 'look' : 'initial wardrobe'}
                                </label>
                                <textarea
                                  id={`shared-proposal-${field}`}
                                  rows={field === 'look' ? 3 : 2}
                                  value={value}
                                  disabled={manualDecisionBusy || sharedAcceptBusy || sharedAcceptUnknown}
                                  onChange={(event) => setSharedProposalDrafts((previous) => ({
                                    ...previous,
                                    [field]: event.target.value,
                                  }))}
                                />
                              </div>
                            ))}
                            <button
                              type="button"
                              className="primary"
                              onClick={acceptSharedSuggestions}
                              disabled={
                                manualDecisionBusy || sharedAcceptBusy || lookProgressionBusy || !planSessionCurrent || planDirty
                                || sharedOperation.plan_revision !== planRevision
                                || !Object.keys(sharedProposalDrafts).length
                              }
                            >
                              {sharedAcceptBusy
                                ? 'Accepting…'
                                : sharedAcceptUnknown
                                  ? 'Retry acceptance'
                                  : 'Accept edited suggestions'}
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                    {sharedActionError && <p className="error" role="alert" style={{ margin: '8px 0 0' }}>{sharedActionError}</p>}
                    {sharedActionNotice && <p className="muted" role="status" style={{ margin: '8px 0 0' }}>{sharedActionNotice}</p>}
                  </section>
                </>
              )}
              <div style={{ marginBottom: 12 }}>
                <label>Look (appearance, place, light)</label>
                <textarea
                  rows={3}
                  value={plan?.look ?? ''}
                  disabled={planEditLocked}
                  placeholder="e.g. natural soft daylight in an open loft, detailed skin texture"
                  onChange={(e) => {
                    if (!plan) return
                    setPlan({ ...plan, look: e.target.value })
                    setPlanDirty(true)
                    setReviewedRevision(null)
                  }}
                />
              </div>
              <div style={{ marginBottom: 12 }}>
                <label>Initial Wardrobe</label>
                <textarea
                  rows={2}
                  value={plan?.initial_wardrobe ?? ''}
                  disabled={planEditLocked}
                  placeholder="e.g. wearing a charcoal wool overcoat and white silk shirt"
                  onChange={(e) => {
                    if (!plan) return
                    setPlan({ ...plan, initial_wardrobe: e.target.value })
                    setPlanDirty(true)
                    setReviewedRevision(null)
                  }}
                />
              </div>
              <div style={{ marginBottom: 12 }}>
                <label>Selected Resources (Pinned Revisions)</label>
                {(plan?.selected_resources && plan.selected_resources.length > 0) ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
                    {plan.selected_resources.map((res, idx) => (
                      <div key={idx} className="row" style={{ padding: '6px 8px', background: 'var(--panel-2)', borderRadius: 6, fontSize: 12 }}>
                        <span className="badge">{res.library_key}</span>
                        <span><b>{res.source_id}</b></span>
                        <span className="muted" style={{ fontFamily: 'monospace', fontSize: 11 }}>
                          digest: {res.content_digest?.slice(0, 16)}…
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="muted" style={{ margin: '4px 0', fontSize: 12 }}>
                    No pinned resource revisions attached to this plan.
                  </p>
                )}
              </div>
              <div className="row" style={{ marginTop: 12 }}>
                <button onClick={() => navigateStep('character')}>← Back: Character</button>
                <button onClick={savePlan} disabled={!planDirty || planEditLocked}>Save Draft</button>
                <span className="spacer" style={{ flex: 1 }} />
                <button className="primary" onClick={() => navigateStep('takes')}>Next: Takes →</button>
              </div>
            </div>
          )}

          {activeStep === 'takes' && (() => {
            const effectiveDetails = plan ? resolveEffectiveWardrobeDetails(plan) : {}
            const rePrepCount = incompleteTakeIds.filter((takeId) => (
              (planPreparation?.history || []).some((item) => item.take_id === takeId)
              && !hasGeneratedTakeHistory(takeId, planPreparation)
            )).length

            return (
              <div>
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <div>
                    <h3 style={{ margin: 0 }}>3. Takes ({plan?.takes?.length || 0})</h3>
                    <p className="muted" style={{ margin: '2px 0 0' }}>
                      Configure takes. Creative choices (camera, framing, pose, expression) may repeat across takes. Stable take IDs are preserved.
                    </p>
                  </div>
                  <button
                    disabled={planEditLocked}
                    onClick={() => {
                      if (!plan) return
                      setPlan({
                        ...plan,
                        takes: createTake(plan.takes || []),
                      })
                      setPlanDirty(true)
                      setReviewedRevision(null)
                    }}
                  >
                    + Add Take
                  </button>
                </div>

                {planDirty && (
                  <div style={{ padding: '6px 10px', background: 'var(--panel-2)', border: '1px solid var(--warn)', borderRadius: 6, marginBottom: 10, fontSize: 12, color: 'var(--warn)' }}>
                    Plan has unsaved modifications. Saving a new revision will require re-preparation of affected takes.
                  </div>
                )}

                {!planDirty && incompleteTakeIds.length > 0 && (
                  <div style={{ padding: '6px 10px', background: 'var(--panel-2)', border: '1px solid var(--line)', borderRadius: 6, marginBottom: 10, fontSize: 12 }}>
                    {rePrepCount > 0 ? (
                      <span style={{ color: 'var(--warn)' }}>
                        ⚠️ {rePrepCount} take(s) require re-preparation following plan revision changes.
                      </span>
                    ) : (
                      <span className="muted">
                        {incompleteTakeIds.length} take(s) require preparation before generation.
                      </span>
                    )}
                  </div>
                )}

                <fieldset disabled={planEditLocked} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
                  {(plan?.takes || []).map((take, index) => {
                    const detail = effectiveDetails[take.take_id] || { wardrobe: plan?.initial_wardrobe || '', source: 'initial' }
                    const existingChange = (plan?.wardrobe_changes || []).find((c) => c.take_id === take.take_id)
                    const takeState = getTakePreparationState(take.take_id, planPreparation, planDirty)
                    const isGenerated = takeState === 'generated'
                    const isCompleted = takeState === 'ready'
                    const isInvalidated = takeState === 'invalidated'
                    const isIncomplete = incompleteTakeIds.includes(take.take_id)
                    const generatedItem = (planPreparation?.completed || []).find((item) => (
                      item.take_id === take.take_id && (item.status === 'generated' || item.linked_shot_id != null)
                    )) || (planPreparation?.history || []).find((item) => (
                      item.take_id === take.take_id && (item.status === 'generated' || item.linked_shot_id != null)
                    ))
                    const takeFieldsOpen = Object.prototype.hasOwnProperty.call(expandedTakeFields, take.take_id)
                      ? expandedTakeFields[take.take_id]
                      : plan?.authoring?.mode !== 'automatic'

                    return (
                      <div
                        key={take.take_id}
                        style={{
                          border: '1px solid var(--line)',
                          borderRadius: 8,
                          padding: 10,
                          background: 'var(--panel-2)',
                        }}
                      >
                        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
                          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                            <span className="badge" style={{ fontWeight: 600 }}>{take.take_id}</span>
                            <span className="muted">Take {index + 1}</span>
                            {isGenerated ? (
                              <span className="badge ready">✓ Generated [Shot #{generatedItem?.linked_shot_id || '—'}]</span>
                            ) : planDirty ? (
                              <span className="muted" style={{ fontSize: 11, color: 'var(--warn)' }}>⚠️ Unsaved edits</span>
                            ) : isCompleted ? (
                              <span className="badge ready">✓ Ready</span>
                            ) : isInvalidated ? (
                              <span className="badge warn">⚠️ Requires re-preparation</span>
                            ) : isIncomplete ? (
                              <span className="badge pending">Preparation required</span>
                            ) : null}
                          </div>
                          <div className="row" style={{ gap: 4 }}>
                            <button
                              className="icon"
                              title="Move take up"
                              disabled={planEditLocked || index === 0}
                              onClick={() => {
                                setPlan({
                                  ...plan,
                                  takes: reorderTakes(plan.takes || [], index, index - 1),
                                })
                                setPlanDirty(true)
                                setReviewedRevision(null)
                              }}
                            >
                              ↑
                            </button>
                            <button
                              className="icon"
                              title="Move take down"
                              disabled={planEditLocked || index === (plan?.takes?.length || 1) - 1}
                              onClick={() => {
                                setPlan({
                                  ...plan,
                                  takes: reorderTakes(plan.takes || [], index, index + 1),
                                })
                                setPlanDirty(true)
                                setReviewedRevision(null)
                              }}
                            >
                              ↓
                            </button>
                            <button
                              className="icon danger"
                              title="Remove take"
                              disabled={planEditLocked || (plan?.takes || []).length <= 1}
                              onClick={() => {
                                setPlan({
                                  ...plan,
                                  takes: removeTake(plan.takes, take.take_id),
                                  wardrobe_changes: removeWardrobeChange(plan.wardrobe_changes || [], take.take_id),
                                })
                                setPlanDirty(true)
                                setReviewedRevision(null)
                              }}
                            >
                              🗑
                            </button>
                          </div>
                        </div>
                        <div style={{ marginBottom: 8 }}>
                          <label>Label</label>
                          <input
                            value={take.label || ''}
                            disabled={planEditLocked}
                            placeholder="e.g. Wide shot at entrance"
                            onChange={(e) => {
                              setPlan({
                                ...plan,
                                takes: updateTake(plan.takes, take.take_id, { label: e.target.value }),
                              })
                              setPlanDirty(true)
                              setReviewedRevision(null)
                            }}
                          />
                        </div>
                        <details
                          open={takeFieldsOpen}
                          onToggle={(event) => {
                            const isOpen = event.currentTarget.open
                            setExpandedTakeFields((previous) => ({
                              ...previous,
                              [take.take_id]: isOpen,
                            }))
                          }}
                          style={{ marginBottom: 8 }}
                        >
                          <summary>Advanced take fields (camera, framing, pose, expression)</summary>
                          <div className="grid-form" style={{ marginTop: 8 }}>
                          <div>
                            <label>Camera</label>
                            <input
                              value={take.camera || ''}
                              disabled={planEditLocked}
                              placeholder="e.g. eye-level, 50mm"
                              onChange={(e) => {
                                setPlan({
                                  ...plan,
                                  takes: updateTake(plan.takes, take.take_id, { camera: e.target.value }),
                                })
                                setPlanDirty(true)
                                setReviewedRevision(null)
                              }}
                            />
                          </div>
                          <div>
                            <label>Framing</label>
                            <input
                              value={take.framing || ''}
                              disabled={planEditLocked}
                              placeholder="e.g. medium full shot"
                              onChange={(e) => {
                                setPlan({
                                  ...plan,
                                  takes: updateTake(plan.takes, take.take_id, { framing: e.target.value }),
                                })
                                setPlanDirty(true)
                                setReviewedRevision(null)
                              }}
                            />
                          </div>
                          <div>
                            <label>Pose</label>
                            <input
                              value={take.pose || ''}
                              disabled={planEditLocked}
                              placeholder="e.g. standing leaning against doorway"
                              onChange={(e) => {
                                setPlan({
                                  ...plan,
                                  takes: updateTake(plan.takes, take.take_id, { pose: e.target.value }),
                                })
                                setPlanDirty(true)
                                setReviewedRevision(null)
                              }}
                            />
                          </div>
                          <div style={{ gridColumn: 'span 2' }}>
                            <label>Expression</label>
                            <input
                              value={take.expression || ''}
                              disabled={planEditLocked}
                              placeholder="e.g. calm neutral expression, direct eye contact"
                              onChange={(e) => {
                                setPlan({
                                  ...plan,
                                  takes: updateTake(plan.takes, take.take_id, { expression: e.target.value }),
                                })
                                setPlanDirty(true)
                                setReviewedRevision(null)
                              }}
                            />
                          </div>
                          </div>
                        </details>

                        {/* Wardrobe Display and Scope Controls */}
                        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--line)' }}>
                          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                            <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                              <span style={{ fontSize: 12, fontWeight: 600 }}>Effective Wardrobe:</span>
                              {detail.source === 'initial' && (
                                <span className="badge">Inherited (initial wardrobe)</span>
                              )}
                              {detail.source === 'from_here' && (
                                <span className="badge warn">Persistent change (from here onward)</span>
                              )}
                              {detail.source === 'inherited_from_here' && (
                                <span className="badge">Inherited from {detail.inheritedFrom} (from here)</span>
                              )}
                              {detail.source === 'this_take' && (
                                <span className="badge warn">Override (this take only)</span>
                              )}
                            </div>
                            {!existingChange ? (
                              <button
                                className="icon"
                                style={{ fontSize: 11, padding: '2px 8px' }}
                                disabled={planEditLocked}
                                onClick={() => {
                                  setPlan({
                                    ...plan,
                                    wardrobe_changes: setWardrobeChange(plan.wardrobe_changes || [], take.take_id, {
                                      scope: WARDROBE_SCOPE_THIS_TAKE,
                                      wardrobe: detail.wardrobe || '',
                                    }),
                                  })
                                  setPlanDirty(true)
                                  setReviewedRevision(null)
                                }}
                              >
                                + Change wardrobe
                              </button>
                            ) : (
                              <button
                                className="icon danger"
                                style={{ fontSize: 11, padding: '2px 8px' }}
                                title="Remove wardrobe change and restore inherited state"
                                disabled={planEditLocked}
                                onClick={() => {
                                  setPlan({
                                    ...plan,
                                    wardrobe_changes: removeWardrobeChange(plan.wardrobe_changes || [], take.take_id),
                                  })
                                  setPlanDirty(true)
                                  setReviewedRevision(null)
                                }}
                              >
                                Remove change
                              </button>
                            )}
                          </div>

                          <div style={{ fontSize: 13, padding: '4px 8px', background: 'var(--panel)', borderRadius: 4, marginBottom: existingChange ? 8 : 0 }}>
                            {detail.wardrobe ? detail.wardrobe : <span className="muted">(none)</span>}
                          </div>

                          {existingChange && (
                            <div style={{ marginTop: 6, padding: 8, background: 'var(--panel)', borderRadius: 6, border: '1px solid var(--line)' }}>
                              <div className="row" style={{ gap: 16, marginBottom: 6 }}>
                                <label style={{ margin: 0, fontWeight: 600 }}>Scope:</label>
                                <label className="chk" style={{ padding: 0 }}>
                                  <input
                                    type="radio"
                                    name={`scope-${take.take_id}`}
                                    checked={existingChange.scope === WARDROBE_SCOPE_THIS_TAKE}
                                    disabled={planEditLocked}
                                    onChange={() => {
                                      setPlan({
                                        ...plan,
                                        wardrobe_changes: setWardrobeChange(plan.wardrobe_changes || [], take.take_id, {
                                          scope: WARDROBE_SCOPE_THIS_TAKE,
                                          wardrobe: existingChange.wardrobe,
                                        }),
                                      })
                                      setPlanDirty(true)
                                      setReviewedRevision(null)
                                    }}
                                  />
                                  This take only
                                </label>
                                <label className="chk" style={{ padding: 0 }}>
                                  <input
                                    type="radio"
                                    name={`scope-${take.take_id}`}
                                    checked={existingChange.scope === WARDROBE_SCOPE_FROM_HERE}
                                    disabled={planEditLocked}
                                    onChange={() => {
                                      setPlan({
                                        ...plan,
                                        wardrobe_changes: setWardrobeChange(plan.wardrobe_changes || [], take.take_id, {
                                          scope: WARDROBE_SCOPE_FROM_HERE,
                                          wardrobe: existingChange.wardrobe,
                                        }),
                                      })
                                      setPlanDirty(true)
                                      setReviewedRevision(null)
                                    }}
                                  />
                                  From here onward
                                </label>
                              </div>
                              <div>
                                <label>Wardrobe Description</label>
                                <textarea
                                  rows={2}
                                  value={existingChange.wardrobe}
                                  disabled={planEditLocked}
                                  placeholder="Describe clothing for this take..."
                                  onChange={(e) => {
                                    setPlan({
                                      ...plan,
                                      wardrobe_changes: setWardrobeChange(plan.wardrobe_changes || [], take.take_id, {
                                        scope: existingChange.scope,
                                        wardrobe: e.target.value,
                                      }),
                                    })
                                    setPlanDirty(true)
                                    setReviewedRevision(null)
                                  }}
                                />
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
                </fieldset>

                <div className="row" style={{ marginTop: 12 }}>
                  <button onClick={() => navigateStep('constants')}>← Back: Scene / Constants</button>
                  <button onClick={savePlan} disabled={!planDirty || planEditLocked}>Save Draft</button>
                  <span className="spacer" style={{ flex: 1 }} />
                  <button className="primary" onClick={() => navigateStep('review')}>Next: Review →</button>
                </div>
              </div>
            )
          })()}

          {activeStep === 'review' && (() => {
            const effectiveDetails = plan ? resolveEffectiveWardrobeDetails(plan) : {}
            const rePrepCount = incompleteTakeIds.filter((takeId) => (
              (planPreparation?.history || []).some((item) => item.take_id === takeId)
              && !hasGeneratedTakeHistory(takeId, planPreparation)
            )).length
            const incompleteCount = incompleteTakeIds.length

            const readyTakeIds = (plan?.takes || []).filter((t) => {
              const st = getTakePreparationState(t.take_id, planPreparation, planDirty)
              return st === 'ready'
            }).map((t) => t.take_id)

            const allReadySelected = readyTakeIds.length > 0 && readyTakeIds.every((tid) => selectedTakeIds.has(tid))

            return (
              <div>
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <div>
                    <h3 style={{ margin: 0 }}>4. Review</h3>
                    <p className="muted" style={{ margin: '2px 0 0' }}>
                      Review session configuration, effective prompts, provenance, and conflicts before generating.
                    </p>
                  </div>
                  <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                    {reviewedRevision === planRevision && planRevision !== null ? (
                      <span className="badge ready" title="Current plan revision reviewed and approved">
                        ✓ Review Approved (Rev {planRevision})
                      </span>
                    ) : (
                      <button
                        className="secondary"
                        onClick={handleApproveReview}
                        disabled={planDirty || planEditLocked || planRevision === null || reviewBlocksFinalization}
                        title={
                          planDirty
                            ? 'Save plan changes before approving review'
                            : hasKnownStaleAdaptations
                                ? 'Save a new plan revision and review the authorized resource description before approving'
                                : planReviewStatus !== 'ready'
                                  ? 'All take reviews must load successfully before approval'
                                  : hasPlanConflictMarkers
                                    ? 'Approve the current plan revision while keeping these neutral resource markers visible'
                                    : 'Approve review for the current plan revision'
                        }
                      >
                        Approve Review (Rev {planRevision ?? '—'})
                      </button>
                    )}
                  </div>
                </div>

                {planDirty && (
                  <div style={{
                    background: '#2a2214',
                    border: '1px solid #785a28',
                    borderRadius: 8,
                    padding: 10,
                    marginBottom: 14,
                  }}>
                    <div style={{ fontWeight: 600, color: 'var(--warn)', marginBottom: 4 }}>
                      Unsaved Plan Changes
                    </div>
                    <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                      You have unsaved changes to takes or wardrobe. Save the plan draft to update revisions before proceeding to generation.
                    </p>
                  </div>
                )}

                {!planDirty && rePrepCount > 0 && (
                  <div style={{
                    background: '#2a2214',
                    border: '1px solid #785a28',
                    borderRadius: 8,
                    padding: 10,
                    marginBottom: 14,
                  }}>
                    <div style={{ fontWeight: 600, color: 'var(--warn)', marginBottom: 4 }}>
                      Re-preparation Required ({rePrepCount} take{rePrepCount > 1 ? 's' : ''})
                    </div>
                    <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                      Previous take preparations were invalidated by plan revisions. Re-preparation is required before shooting.
                    </p>
                  </div>
                )}

                {hasPlanConflictMarkers && (
                  <div role="note" style={{
                    background: '#2a2214',
                    border: '1px solid #785a28',
                    borderRadius: 8,
                    padding: 10,
                    marginBottom: 14,
                  }}>
                    <div style={{ fontWeight: 600, color: 'var(--warn)', marginBottom: 4 }}>
                      Resource Field Markers ({planConflictMarkers.length})
                    </div>
                    <p className="muted" style={{ margin: '0 0 8px', fontSize: 13 }}>
                      These neutral markers identify descriptive resource inputs used during preparation; they do not establish a semantic conflict. Review take-level conflicts and prompts below. Approve the current plan revision after take reviews load to continue while these markers remain visible.
                    </p>
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                      {planConflictMarkers.map((c, i) => (
                        <li key={i} style={{ margin: '2px 0' }}>
                          <b>{c.resource_field || c.source_id || 'Resource'}</b>: {c.message || 'Descriptive input is mapped for preparation.'}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12, marginBottom: 14 }}>
                  <div style={{ background: 'var(--panel-2)', padding: 10, borderRadius: 8 }}>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>Character Identity</div>
                    <div className="muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                      <div><b>Model:</b> {s.model?.name || s.model?.id}</div>
                      <div><b>Checkpoint:</b> {s.checkpoint || s.settings?.checkpoint || 'Default'}</div>
                      <div><b>LoRA Strength:</b> {s.settings?.lora_strength ?? '1.0'}</div>
                    </div>
                  </div>
                  <div style={{ background: 'var(--panel-2)', padding: 10, borderRadius: 8 }}>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>Resources</div>
                    <div className="muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                      <div><b>Pinned Resources:</b> {plan?.selected_resources?.length || 0}</div>
                      {isResource && (s.diagnostic ? (
                        <div role="alert" style={{ marginTop: 6 }}>
                          <b>Resource plan needs attention{s.diagnostic.code ? ` (${s.diagnostic.code})` : ''}:</b> {s.diagnostic.message || 'Plan-owned look and wardrobe are unavailable.'}
                        </div>
                      ) : (
                        <>
                          <div><b>Plan look:</b> {typeof plan?.look === 'string' ? (plan.look || 'No additional look constraint') : 'Unavailable'}</div>
                          <div><b>Initial wardrobe:</b> {typeof plan?.initial_wardrobe === 'string' ? (plan.initial_wardrobe || 'No wardrobe description') : 'Unavailable'}</div>
                        </>
                      ))}
                    </div>
                  </div>
                </div>

                {sharedSummary && (
                  <section
                    aria-label="Shared session summary"
                    style={{
                      border: '1px solid var(--line)',
                      borderRadius: 8,
                      padding: 12,
                      marginBottom: 14,
                      background: 'var(--panel-2)',
                    }}
                  >
                    <h4 style={{ margin: '0 0 4px' }}>Shared Session Summary</h4>
                    <p className="muted" style={{ margin: '0 0 10px', fontSize: 12 }}>
                      Saved look and wardrobe values are additional constraints. Empty values leave the selected scene descriptions intact.
                    </p>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10, marginBottom: 12 }}>
                      {[
                        ['Look', sharedSummary.look],
                        ['Initial Wardrobe', sharedSummary.initial_wardrobe],
                      ].map(([label, value]) => (
                        <div key={label}>
                          <b>{label}:</b> {value?.value === '' ? 'No additional constraint' : value?.value || 'Unavailable'}
                          <div className="muted" style={{ fontSize: 11 }}>Origin: {value?.origin || 'Unavailable'}</div>
                        </div>
                      ))}
                    </div>
                    {!sharedSummary.available && (
                      <p className="muted" style={{ margin: '0 0 10px' }}>
                        {sharedSummary.message || 'Authorized scene descriptions are unavailable.'}
                      </p>
                    )}
                    {sharedSummary.available && (
                      <>
                        <div style={{ fontWeight: 600, marginBottom: 6 }}>Authorized Scene Descriptions</div>
                        {sharedSummary.scene_descriptions?.length ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                            {sharedSummary.scene_descriptions.map((scene) => (
                              <div
                                key={`${scene.library_key}:${scene.source_id}:${scene.content_digest}`}
                                style={{ borderTop: '1px solid var(--line)', paddingTop: 8 }}
                              >
                                <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
                                  {scene.kind}: {scene.library_key} / {scene.source_id} / {scene.content_digest}
                                </div>
                                {Object.entries(scene.descriptive_inputs || {}).map(([field, value]) => (
                                  <div key={field} style={{ whiteSpace: 'pre-wrap', marginBottom: 4 }}>
                                    <b>{field}:</b> {Array.isArray(value) ? value.join(', ') : value}
                                  </div>
                                ))}
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="muted" style={{ margin: 0 }}>No selected scene descriptions are available.</p>
                        )}
                      </>
                    )}
                  </section>
                )}

                {isResource && !planDirty && planReviewStatus === 'idle' && (
                  <p className="muted" style={{ margin: '0 0 10px', fontSize: 12 }}>
                    Take reviews must load before this plan can be approved or prepared.
                  </p>
                )}
                {isResource && !planDirty && planReviewStatus === 'loading' && (
                  <p className="muted" style={{ margin: '0 0 10px', fontSize: 12 }}>
                    Loading complete take reviews before approval and preparation…
                  </p>
                )}
                {planReviewStatus === 'error' && (
                  <div role="alert" style={{ background: '#2a2214', border: '1px solid #785a28', borderRadius: 8, padding: 10, marginBottom: 14 }}>
                    <div style={{ fontWeight: 600, color: 'var(--warn)', marginBottom: 4 }}>Take Review Unavailable</div>
                    <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                      {isAuthoringEvidenceInvalid(planReviewFailure)
                        ? 'The backend rejected prepared authoring evidence. Resource drift is not confirmed; use Refresh Resources to check dependencies before deciding what to do next.'
                        : planReviewFailure?.error || 'Take reviews could not be verified. Reload before approving or preparing.'}
                    </p>
                    {planReviewFailure?.status && (
                      <p className="muted" style={{ margin: '4px 0 0', fontSize: 11 }}>Backend status: {planReviewFailure.status}</p>
                    )}
                    {planReviewFailure?.detail !== undefined && (
                      <details style={{ marginTop: 6, fontSize: 11 }}>
                        <summary>Backend review detail</summary>
                        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{errorDetailMessage(planReviewFailure.detail)}</pre>
                      </details>
                    )}
                    {canRefreshResourceDependencies && (
                      <button
                        type="button"
                        style={{ marginTop: 8 }}
                        onClick={refreshResourceDependencies}
                        disabled={resourceRefreshBusy}
                        title="Check pinned resource dependencies; this does not approve, submit, prepare, or run takes."
                      >
                        {resourceRefreshBusy ? 'Checking resources…' : 'Refresh Resources'}
                      </button>
                    )}
                  </div>
                )}
                {hasKnownStaleAdaptations && (
                  <div role="alert" style={{ background: '#2a2214', border: '1px solid #785a28', borderRadius: 8, padding: 10, marginBottom: 14 }}>
                    <div style={{ fontWeight: 600, color: 'var(--warn)', marginBottom: 4 }}>
                      Stale Reviewed Adaptations ({staleAdaptationCount})
                    </div>
                    <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                      An authorized resource description changed after approval. Save a new plan revision and review its current description before approving or preparing takes.
                    </p>
                  </div>
                )}

                {canRefreshResourceDependencies && planReviewStatus !== 'error' && (
                  <div style={{ marginBottom: 14 }}>
                    <button
                      type="button"
                      onClick={refreshResourceDependencies}
                      disabled={resourceRefreshBusy}
                      title="Check pinned resource dependencies; this does not approve, submit, prepare, or run takes."
                    >
                      {resourceRefreshBusy ? 'Checking resources…' : 'Refresh Resources'}
                    </button>
                  </div>
                )}

                {resourceRefreshOutcome && (
                  <div
                    role={resourceRefreshOutcome.type === 'error' ? 'alert' : 'status'}
                    style={{
                      background: resourceRefreshOutcome.type === 'error' ? '#2a2214' : 'var(--panel-2)',
                      border: `1px solid ${resourceRefreshOutcome.type === 'error' ? '#785a28' : 'var(--line)'}`,
                      borderRadius: 8, padding: 10, marginBottom: 14,
                    }}
                  >
                    {resourceRefreshOutcome.type === 'refreshed' ? (
                      <>
                        <div style={{ fontWeight: 600 }}>Resource drift confirmed; plan advanced to revision {resourceRefreshOutcome.planRevision}.</div>
                        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                          Affected takes: {takeIdSummary(resourceRefreshOutcome.affectedTakes)}. Required preparation: {takeIdSummary(resourceRefreshOutcome.requiredPreparation)}. Copied forward: {takeIdSummary(resourceRefreshOutcome.copiedForwardTakes)}.
                        </div>
                        {resourceRefreshOutcome.diagnostics.length > 0 && (
                          <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12 }}>
                            {resourceRefreshOutcome.diagnostics.map((item, index) => (
                              <li key={`${item?.take_id || 'take'}:${item?.code || 'diagnostic'}:${index}`}>
                                Take {item?.take_id || 'unknown'}: {item?.code || 'resource dependency changed'}
                              </li>
                            ))}
                          </ul>
                        )}
                        {resourceRefreshOutcome.reloadFailed && (
                          <p className="muted" style={{ margin: '6px 0 0', fontSize: 12 }}>The plan revision changed, but its new authoritative state could not be loaded. Reload before continuing.</p>
                        )}
                      </>
                    ) : resourceRefreshOutcome.type === 'no-drift' ? (
                      <>
                        <div style={{ fontWeight: 600 }}>No resource drift found.</div>
                        <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>
                          The check did not change the plan. Take review remains subject to the backend result shown above.
                        </p>
                      </>
                    ) : (
                      <>
                        <div style={{ fontWeight: 600 }}>Resource dependency check failed; drift was not determined.</div>
                        <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>{resourceRefreshOutcome.message}</p>
                        {resourceRefreshOutcome.status && <div className="muted" style={{ fontSize: 11 }}>Backend status: {resourceRefreshOutcome.status}</div>}
                        {resourceRefreshOutcome.detail !== undefined && (
                          <details style={{ marginTop: 6, fontSize: 11 }}>
                            <summary>Backend refresh detail</summary>
                            <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{errorDetailMessage(resourceRefreshOutcome.detail)}</pre>
                          </details>
                        )}
                      </>
                    )}
                  </div>
                )}

                {/* Step 4 Toolbar: Batch Preparation & Selected Test Submission */}
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, padding: '8px 12px', background: 'var(--panel-2)', borderRadius: 8 }}>
                  <div className="row" style={{ gap: 10, alignItems: 'center' }}>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>Actions:</span>
                    {plan?.authoring?.mode === 'automatic' ? (
                      <>
                        <button
                          type="button"
                          onClick={startPreparationOperation}
                          disabled={
                            manualDecisionBusy || preparationActionBusy
                            || (!preparationRetryUnknown && (
                              planDirty || reviewBlocksFinalization || authoringOperationActive
                              || sharedOperation?.can_resume || sharedStartBusy || sharedStartUnknown
                              || !llm || automaticBatchTakeIds.length === 0
                            ))
                          }
                          title={
                            preparationRetryUnknown
                              ? 'Retry the same request ID and body to recover its operation'
                              : planDirty
                                ? 'Save draft before preparing'
                                : reviewBlocksFinalization
                                  ? 'Load all take reviews before preparing'
                                  : !llm
                                    ? 'Configure a text assistant before automatic preparation'
                                    : `Prepare the next ${automaticBatchTakeIds.length} incomplete take(s), in plan order; each operation is limited to 20`
                          }
                        >
                          {preparationActionBusy
                            ? 'Starting preparation…'
                            : preparationRetryUnknown
                              ? 'Retry preparation'
                              : automaticIncompleteTakeIds.length > 0 && (
                                planPreparation?.completed?.length > 0
                                || sharedOperation?.kind === 'prepare_takes'
                              )
                                ? `Continue preparation (${automaticBatchTakeIds.length})`
                                : `Prepare ${automaticBatchTakeIds.length} take(s)`}
                        </button>
                        {automaticIncompleteTakeIds.length > 0 && !llm && (
                          <span className="muted" role="status" style={{ fontSize: 12 }}>
                            Automatic preparation needs a text assistant. <a href="#/setup">Configure assistant</a>.
                          </span>
                        )}
                        {automaticIncompleteTakeIds.length > MAX_AUTOMATIC_TAKE_BATCH && (
                          <span className="muted" role="status" style={{ fontSize: 12 }}>
                            {automaticIncompleteTakeIds.length} takes remain; each batch prepares at most {MAX_AUTOMATIC_TAKE_BATCH}.
                          </span>
                        )}
                      </>
                    ) : (
                      <button
                        onClick={handlePrepareAllIncomplete}
                        disabled={planDirty || manualDecisionBusy || preparingAll || incompleteCount === 0 || reviewBlocksFinalization}
                        title={
                          planDirty
                            ? 'Save draft before preparing'
                            : hasKnownStaleAdaptations
                              ? 'Save a new plan revision and review the authorized resource description before preparing'
                              : reviewBlocksFinalization
                                ? 'Load all take reviews before preparing'
                              : incompleteCount === 0
                                ? 'All takes already prepared'
                                : `Prepare ${incompleteCount} take(s)`
                        }
                      >
                        {preparingAll ? 'Preparing…' : `Prepare Incomplete Takes (${incompleteCount})`}
                      </button>
                    )}
                  </div>
                  <div className="row" style={{ gap: 10, alignItems: 'center' }}>
                    <button
                      className="primary"
                      onClick={handleSubmitSelectedTakes}
                      disabled={selectedTakeIds.size === 0 || submittingTakes || planDirty || planEditLocked || reviewedRevision !== planRevision}
                      title={
                        planDirty
                          ? 'Save draft before submitting test selection'
                          : reviewedRevision !== planRevision
                            ? 'Review must be approved for the current revision before submission'
                            : selectedTakeIds.size === 0
                              ? 'Select at least one ready take to submit for test generation'
                              : `Submit ${selectedTakeIds.size} selected take(s) to the queue; use Run separately to generate`
                      }
                    >
                      {submittingTakes ? 'Submitting…' : `Submit Test Selection (${selectedTakeIds.size})`}
                    </button>
                  </div>
                </div>

                <div style={{ marginBottom: 14 }}>
                  <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                    <div style={{ fontWeight: 600 }}>Planned Takes ({plan?.takes?.length || 0})</div>
                    {selectedTakeIds.size > 0 && (
                      <span className="muted" style={{ fontSize: 12 }}>
                        {selectedTakeIds.size} take{selectedTakeIds.size === 1 ? '' : 's'} selected for test submission
                      </span>
                    )}
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table>
                      <thead>
                        <tr>
                          <th style={{ width: 36, textAlign: 'center' }}>
                            <input
                              type="checkbox"
                              checked={allReadySelected}
                              onChange={() => handleSelectAllReady(readyTakeIds)}
                              disabled={readyTakeIds.length === 0 || planDirty || submittingTakes}
                              title={readyTakeIds.length === 0 ? 'No ready takes available to select' : 'Select all ready takes'}
                            />
                          </th>
                          <th>Take ID</th>
                          <th>Label</th>
                          <th>Effective Camera</th>
                          <th>Effective Framing</th>
                          <th>Effective Pose</th>
                          <th>Effective Expression</th>
                          <th>Effective Wardrobe</th>
                          <th>Status</th>
                          <th style={{ textAlign: 'right' }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(plan?.takes || []).map((t) => {
                          const d = effectiveDetails[t.take_id] || { wardrobe: plan?.initial_wardrobe || '', source: 'initial' }
                          const prepState = getTakePreparationState(t.take_id, planPreparation, planDirty)
                          const completedItem = (planPreparation?.completed || []).find((c) => c.take_id === t.take_id)
                          const generatedItem = completedItem && (completedItem.status === 'generated' || completedItem.linked_shot_id != null)
                            ? completedItem
                            : (planPreparation?.history || []).find((item) => (
                              item.take_id === t.take_id && (item.status === 'generated' || item.linked_shot_id != null)
                            ))
                          const isSelectable = !planDirty && prepState === 'ready' && !submittingTakes
                          const isExpanded = expandedTakeId === t.take_id
                          const rev = takeReviewData[t.take_id]
                          const snap = rev?.snapshot || generatedItem || completedItem
                          const choiceSnapshot = snap?.effective_state?.take_choices
                          const displayChoice = (field) => {
                            if (choiceSnapshot && typeof choiceSnapshot === 'object') {
                              return hasOwn(choiceSnapshot, field)
                                ? (choiceSnapshot[field] === '' ? '(empty in prepared snapshot)' : choiceSnapshot[field])
                                : 'Not recorded in prepared snapshot'
                            }
                            if (snap?.effective_state) return 'Not recorded in prepared snapshot'
                            return `Planned: ${t[field] || '—'}`
                          }
                          const displaySnapshotField = (field, plannedValue, emptyLabel) => {
                            const effectiveState = snap?.effective_state
                            if (hasOwn(effectiveState, field)) {
                              return effectiveState[field] === ''
                                ? `(empty in prepared snapshot: ${emptyLabel})`
                                : effectiveState[field]
                            }
                            if (effectiveState) return 'Not recorded in prepared snapshot'
                            return `Planned: ${plannedValue === '' ? emptyLabel : (plannedValue || '(none)')}`
                          }
                          const duplicateFlags = Array.isArray(
                            (snap?.provenance || rev?.provenance)?.authoring_evidence?.duplicate_flags?.flags,
                          )
                            ? (snap?.provenance || rev?.provenance).authoring_evidence.duplicate_flags.flags
                            : []
                          const snapshotProvenance = snap?.provenance || rev?.provenance || {}
                          const authoringEvidence = snapshotProvenance?.authoring_evidence || {}
                          const duplicateEvidence = authoringEvidence?.duplicate_flags || {}
                          const writerSynthesis = authoringEvidence?.writer_synthesis || snapshotProvenance?.writer_synthesis
                          const finalPrompt = snap?.final_prompt || rev?.final_prompt
                          const conflicts = rev?.conflicts || []
                          const resolvedConflicts = rev?.adaptations || rev?.resolved_conflicts || []
                          const unresolvedPlaceholders = rev?.unresolved_placeholders || []
                          const fusedDescriptions = rev?.fused_descriptions || []
                          const staleAdaptations = rev?.stale_adaptations || []
                          const hasStaleAdaptations = staleAdaptations.length > 0
                          const hasPlaceholders = unresolvedPlaceholders.length > 0 || rev?.has_standing_placeholders || false
                          const pinnedResources = Array.isArray(snap?.provenance?.selected_resource_revisions)
                              ? snap.provenance.selected_resource_revisions
                              : Array.isArray(rev?.selected_resource_revisions)
                                ? rev.selected_resource_revisions
                              : snap
                                ? []
                                : (plan?.selected_resources || [])

                          return (
                            <React.Fragment key={t.take_id}>
                              <tr>
                                <td style={{ textAlign: 'center' }}>
                                  <input
                                    type="checkbox"
                                    checked={selectedTakeIds.has(t.take_id)}
                                    onChange={() => handleToggleTakeSelect(t.take_id)}
                                    disabled={!isSelectable}
                                    title={
                                      prepState === 'generated'
                                        ? 'Take already generated as a shot'
                                        : planDirty
                                          ? 'Save draft first'
                                          : prepState !== 'ready'
                                            ? 'Take must be prepared before test generation'
                                            : `Select take ${t.take_id} for test generation`
                                    }
                                  />
                                </td>
                                <td><span className="badge">{t.take_id}</span></td>
                                <td>{t.label || '—'}</td>
                                <td>{displayChoice('camera')}</td>
                                <td>{displayChoice('framing')}</td>
                                <td>{displayChoice('pose')}</td>
                                <td>{displayChoice('expression')}</td>
                                <td>
                                  <div style={{ fontSize: 12 }}>{displaySnapshotField('wardrobe', d.wardrobe, 'no wardrobe description')}</div>
                                  <span className="badge" style={{ fontSize: 10 }}>
                                    {hasOwn(snap?.effective_state, 'scope')
                                      ? `snapshot: ${snap.effective_state.scope || 'initial'}`
                                      : d.source === 'initial' && 'initial'}
                                    {!hasOwn(snap?.effective_state, 'scope') && d.source === 'from_here' && 'from_here'}
                                    {!hasOwn(snap?.effective_state, 'scope') && d.source === 'inherited_from_here' && `from ${d.inheritedFrom}`}
                                    {!hasOwn(snap?.effective_state, 'scope') && d.source === 'this_take' && 'this_take'}
                                  </span>
                                </td>
                                <td>
                                  {prepState === 'generated' ? (
                                    <span className="badge ready">✓ Generated [Shot #{generatedItem?.linked_shot_id || '—'}]</span>
                                  ) : planDirty || prepState === 'unsaved' ? (
                                    <span className="badge warn">Unsaved edits</span>
                                  ) : prepState === 'ready' ? (
                                    <span className="badge ready">Ready</span>
                                  ) : prepState === 'invalidated' ? (
                                    <span className="badge warn">Requires re-prep</span>
                                  ) : (
                                    <span className="badge pending">Pending prep</span>
                                  )}
                                </td>
                                <td style={{ textAlign: 'right' }}>
                                  <div className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                                    <button
                                      className="icon"
                                      onClick={() => toggleTakeReview(t.take_id)}
                                      title="Inspect effective prompt, provenance, and conflicts"
                                    >
                                      {isExpanded ? '▲ Close' : '▼ Review'}
                                    </button>
                                    {plan?.authoring?.mode !== 'automatic' && (
                                      <button
                                        style={{ fontSize: 11, padding: '2px 8px' }}
                                        onClick={() => handlePrepareTake(t.take_id)}
                                        disabled={planDirty || prepState === 'ready' || prepState === 'generated' || preparingTakeId === t.take_id || preparingAll || reviewBlocksFinalization}
                                        title={
                                          planDirty
                                            ? 'Save draft before preparing'
                                            : prepState === 'generated'
                                              ? 'This take is already linked to a generated shot'
                                            : hasStaleAdaptations
                                              ? 'Save a new plan revision and review the authorized resource description before preparing'
                                              : reviewBlocksFinalization
                                                ? 'Load all take reviews before preparing'
                                              : 'Compile authoritative preparation snapshot'
                                        }
                                      >
                                        {preparingTakeId === t.take_id ? 'Preparing…' : prepState === 'generated' ? 'Already Generated' : 'Prepare'}
                                      </button>
                                    )}
                                  </div>
                                </td>
                              </tr>
                              {isExpanded && (
                                <tr>
                                  <td colSpan={10} style={{ padding: 12, background: 'var(--panel)' }}>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                                      {takeReviewLoading[t.take_id] && <p className="muted" style={{ margin: 0 }}>Loading take review…</p>}
                                      {takeReviewError[t.take_id] && <p style={{ margin: 0, color: 'var(--warn)' }}>{takeReviewError[t.take_id]}</p>}

                                      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                                        <div style={{ fontWeight: 600 }}>Inspector: Take {t.take_id} {t.label ? `(${t.label})` : ''}</div>
                                        <div className="row" style={{ gap: 6 }}>
                                          <button
                                            className="icon"
                                            onClick={() => fetchTakeReview(t.take_id)}
                                            title="Reload review data from backend"
                                          >
                                            ↻ Refresh
                                          </button>
                                          {plan?.authoring?.mode !== 'automatic' && (
                                            <button
                                              onClick={() => handlePrepareTake(t.take_id)}
                                            disabled={planDirty || prepState === 'generated' || preparingTakeId === t.take_id || preparingAll || reviewBlocksFinalization}
                                              title={
                                                planDirty
                                                  ? 'Save draft before preparing'
                                                : prepState === 'generated'
                                                  ? 'This take is already linked to a generated shot'
                                                  : hasStaleAdaptations
                                                    ? 'Save a new plan revision and review the authorized resource description before preparing'
                                                    : reviewBlocksFinalization
                                                      ? 'Load all take reviews before preparing'
                                                    : 'Compile authoritative preparation snapshot'
                                              }
                                            >
                                            {preparingTakeId === t.take_id ? 'Preparing…' : prepState === 'ready' ? 'Re-prepare Take' : prepState === 'generated' ? 'Already Generated' : 'Prepare Take'}
                                            </button>
                                          )}
                                        </div>
                                      </div>

                                      {/* Effective Wardrobe & Look */}
                                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
                                        <div style={{ background: 'var(--panel-2)', padding: 8, borderRadius: 6 }}>
                                          <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>Effective Wardrobe</div>
                                          <div style={{ fontSize: 12 }}>{displaySnapshotField('wardrobe', d.wardrobe, 'no wardrobe description')}</div>
                                          <div style={{ marginTop: 4 }}>
                                            <span className="badge" style={{ fontSize: 10 }}>
                                              {hasOwn(snap?.effective_state, 'scope')
                                                ? `Prepared snapshot scope: ${snap.effective_state.scope || 'initial wardrobe'}`
                                                : `Plan source: ${d.source === 'initial' ? 'Inherited from initial wardrobe' : d.source === 'from_here' ? 'Persistent change (from here onward)' : d.source === 'inherited_from_here' ? `Inherited from ${d.inheritedFrom}` : 'This take only override'}`}
                                            </span>
                                          </div>
                                        </div>
                                        <div style={{ background: 'var(--panel-2)', padding: 8, borderRadius: 6 }}>
                                          <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>Effective Look</div>
                                          <div style={{ fontSize: 12 }}>{displaySnapshotField('look', plan?.look, 'no additional look constraint')}</div>
                                        </div>
                                      </div>

                                      {duplicateFlags.length > 0 && (
                                        <div role="alert" style={{ padding: '8px 10px', background: '#2a2214', border: '1px solid #785a28', borderRadius: 6 }}>
                                          <div style={{ fontWeight: 600, color: 'var(--warn)', marginBottom: 4 }}>
                                            Exact Duplicate Choices ({duplicateFlags.length})
                                          </div>
                                          <p className="muted" style={{ margin: '0 0 6px', fontSize: 12 }}>
                                            Each listed take matches all four normalized choices: camera, framing, pose, and expression. Review before approving; deliberate repeats are allowed after approval. A shared camera alone is not a duplicate.
                                          </p>
                                          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                                            {duplicateFlags.map((flag, index) => (
                                              <li key={`${flag?.take_id || 'take'}:${flag?.plan_revision ?? 'revision'}:${index}`}>
                                                Take <code>{flag?.take_id || 'unknown'}</code> (plan revision <code>{flag?.plan_revision ?? 'unknown'}</code>)
                                              </li>
                                            ))}
                                          </ul>
                                        </div>
                                      )}
                                      {duplicateFlags.length === 0 && duplicateEvidence.status && (
                                        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                                          Duplicate check: {duplicateEvidence.status.replaceAll('_', ' ')}.
                                        </p>
                                      )}

                                      {/* Authoritative Final Prompt */}
                                      <div style={{ background: 'var(--panel-2)', padding: 8, borderRadius: 6 }}>
                                        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 4 }}>
                                          <div style={{ fontWeight: 600, fontSize: 12 }}>Authoritative Final Prompt</div>
                                          {finalPrompt && <span className="badge ready" style={{ fontSize: 10 }}>Backend Compiled</span>}
                                        </div>
                                        {finalPrompt ? (
                                          <pre style={{
                                            margin: 0,
                                            padding: 8,
                                            background: 'var(--bg)',
                                            borderRadius: 4,
                                            fontSize: 11,
                                            whiteSpace: 'pre-wrap',
                                            wordBreak: 'break-word',
                                            maxHeight: 120,
                                            overflowY: 'auto',
                                          }}>
                                            {finalPrompt}
                                          </pre>
                                        ) : (
                                          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                                            Prompt has not yet been compiled by the backend compiler. Click &ldquo;Prepare Take&rdquo; to compile authoritative prompt.
                                          </p>
                                        )}
                                      </div>

                                      {fusedDescriptions.length > 0 && (
                                        <div style={{ background: 'var(--panel-2)', padding: 8, borderRadius: 6 }}>
                                          <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>Authorized Fused Scene Descriptions</div>
                                          {fusedDescriptions.map((resource, resourceIndex) => (
                                            <div key={`${resource.library_key}:${resource.source_id}:${resource.content_digest}:${resourceIndex}`} style={{ marginTop: 6 }}>
                                              <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
                                                {resource.library_key}: {resource.source_id}
                                              </div>
                                              {Object.entries(resource.descriptive_inputs || {}).map(([field, value]) => (
                                                <div key={field} style={{ whiteSpace: 'pre-wrap', marginBottom: 4, fontSize: 12 }}>
                                                  <b>{field}:</b> {Array.isArray(value) ? value.join('\n') : value}
                                                </div>
                                              ))}
                                            </div>
                                          ))}
                                        </div>
                                      )}

                                      {/* Provenance & Versions */}
                                      <div style={{ background: 'var(--panel-2)', padding: 8, borderRadius: 6 }}>
                                        <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>Provenance & Compilation Metadata</div>
                                        <div className="row" style={{ gap: 16, fontSize: 11 }}>
                                          <span><b>Compiler:</b> {rev?.compiler_version || snap?.compiler_version || 'resource-v1'}</span>
                                          <span><b>Mapping:</b> {rev?.mapping_version || snap?.mapping_version || '1.0'}</span>
                                          <span><b>Prepared At:</b> {(() => {
                                             const ts = snap?.updated_at || snap?.created_at || snap?.prepared_at
                                             if (!ts) return '—'
                                             try {
                                               const d = typeof ts === 'number' ? new Date(ts > 1e11 ? ts : ts * 1000) : new Date(ts)
                                               return isNaN(d.getTime()) ? String(ts) : d.toLocaleString()
                                             } catch (_) {
                                               return String(ts)
                                             }
                                           })()}</span>
                                        </div>
                                        {pinnedResources.length > 0 && (
                                          <div style={{ marginTop: 6, fontSize: 11 }}>
                                            <div style={{ fontWeight: 600 }}>Pinned Resource Revisions:</div>
                                            <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                                              {pinnedResources.map((res, idx) => {
                                                if (typeof res === 'string') {
                                                  return <li key={idx}><b>{res}</b></li>
                                                }
                                                const key = res.library_key || res.key || ''
                                                const sourceId = res.source_id || res.id || ''
                                                const digest = res.content_digest
                                                return (
                                                  <li key={idx}>
                                                    <b>{key && sourceId ? `${key}:${sourceId}` : key || sourceId || JSON.stringify(res)}</b>
                                                    {digest ? (
                                                      <span className="muted" style={{ fontFamily: 'monospace' }}> ({digest.slice(0, 16)}…)</span>
                                                    ) : null}
                                                  </li>
                                                )
                                              })}
                                            </ul>
                                          </div>
                                        )}
                                        <details style={{ marginTop: 8, fontSize: 11 }}>
                                          <summary>Inspect authoring evidence and writer request/output</summary>
                                          <div className="muted" style={{ margin: '6px 0' }}>
                                            <b>Mode:</b> {authoringEvidence.mode || 'Unavailable'} ·{' '}
                                            <b>Source:</b> {authoringEvidence.source || 'Unavailable'} ·{' '}
                                            <b>Operation:</b> {authoringEvidence.operation_id || 'None'}
                                          </div>
                                          <pre style={{
                                            margin: 0, padding: 8, background: 'var(--bg)', borderRadius: 4,
                                            whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 260, overflowY: 'auto',
                                          }}>
                                            {JSON.stringify({
                                              writer_synthesis: writerSynthesis || null,
                                              writer_context: authoringEvidence.writer_context ?? null,
                                              predecessor_projection: authoringEvidence.predecessor_projection ?? null,
                                              manual_completion: authoringEvidence.manual_completion ?? null,
                                              duplicate_flags: duplicateEvidence,
                                              effective_resource_input_digest: authoringEvidence.effective_resource_input_digest ?? null,
                                            }, null, 2)}
                                          </pre>
                                        </details>
                                      </div>

                                      {/* Conflicts and Adaptations */}
                                      <div style={{ background: 'var(--panel-2)', padding: 8, borderRadius: 6 }}>
                                        <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>
                                          Conflicts & Adaptations {conflicts.length > 0 ? `(${conflicts.length} open)` : ''}
                                        </div>
                                        {hasPlaceholders && (
                                          <div style={{ padding: '6px 8px', background: '#3a2010', border: '1px solid var(--warn)', borderRadius: 4, marginBottom: 8, fontSize: 11, color: 'var(--warn)' }}>
                                            ⚠️ Standing placeholders detected in resource inputs. An adapted value must be provided before preparation can succeed.
                                            {unresolvedPlaceholders.length > 0 && (
                                              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                                                {unresolvedPlaceholders.map((up, idx) => (
                                                  <li key={idx}>
                                                    <code>{typeof up === 'string' ? up : up.placeholder || JSON.stringify(up)}</code>
                                                    {up?.resource_field ? ` in ${up.resource_field}` : ''}
                                                  </li>
                                                ))}
                                              </ul>
                                            )}
                                          </div>
                                        )}
                                        {hasStaleAdaptations && (
                                          <div role="alert" style={{ padding: '6px 8px', background: '#3a2010', border: '1px solid var(--warn)', borderRadius: 4, marginBottom: 8, fontSize: 11, color: 'var(--warn)' }}>
                                            ⚠️ This reviewed adaptation no longer matches the authorized resource description. Save a new plan revision and review the resource before preparing this take.
                                            <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                                              {staleAdaptations.map((item, idx) => (
                                                <li key={`${item.library_key}:${item.source_id}:${item.resource_field}:${idx}`}>
                                                  {item.resource_field || 'Resource field'}: {item.message}
                                                </li>
                                              ))}
                                            </ul>
                                          </div>
                                        )}
                                        {conflicts.length === 0 && resolvedConflicts.length === 0 && !hasPlaceholders && !hasStaleAdaptations ? (
                                          <p className="muted" style={{ margin: 0, fontSize: 12 }}>No structural resource conflicts detected. Review free-text descriptions for semantic fit.</p>
                                        ) : null}

                                        {conflicts.length > 0 && (
                                          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 8 }}>
                                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--warn)' }}>Open Conflicts:</div>
                                            {conflicts.map((c, idx) => {
                                              const conflictKey = c.conflict_key || `${c.library_key || ''}:${c.source_id || ''}:${c.resource_field || ''}`
                                              const draftVal = adaptationDrafts[t.take_id]?.[conflictKey] ?? ''
                                              return (
                                                <div key={idx} style={{ padding: 6, background: 'var(--bg)', borderRadius: 4, border: '1px solid var(--warn)' }}>
                                                  <div style={{ fontSize: 11, marginBottom: 4 }}>
                                                    <b>{c.resource_field || 'Resource'} conflict:</b> {c.message || 'Descriptive input conflicts with take settings.'}
                                                  </div>
                                                  <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                                                    <input
                                                      type="text"
                                                      style={{ flex: 1, fontSize: 11 }}
                                                      placeholder={`Enter adapted ${c.resource_field || 'value'}...`}
                                                      value={draftVal}
                                                      onChange={(e) => {
                                                        const val = e.target.value
                                                        setAdaptationDrafts((prev) => ({
                                                          ...prev,
                                                          [t.take_id]: {
                                                            ...(prev[t.take_id] || {}),
                                                            [conflictKey]: val,
                                                          },
                                                        }))
                                                      }}
                                                    />
                                                    <button
                                                      style={{ fontSize: 11, padding: '3px 8px' }}
                                                      onClick={() => handleRecordAdaptation(t.take_id, { ...c, conflict_key: conflictKey })}
                                                      disabled={planDirty || !draftVal.trim() || reviewBlocksFinalization}
                                                      title={
                                                        planDirty
                                                          ? 'Save draft before adapting'
                                                          : hasStaleAdaptations
                                                            ? 'Save a new plan revision before recording another adaptation'
                                                            : reviewBlocksFinalization
                                                              ? 'Load all take reviews before adapting'
                                                              : 'Record adaptation bound to plan revision'
                                                      }
                                                    >
                                                      Adapt
                                                    </button>
                                                  </div>
                                                </div>
                                              )
                                            })}
                                          </div>
                                        )}

                                        {resolvedConflicts.length > 0 && (
                                          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--ok)' }}>Resolved Adaptations:</div>
                                            {resolvedConflicts.map((rc, idx) => (
                                              <div key={idx} className="row" style={{ fontSize: 11, padding: '4px 6px', background: 'var(--bg)', borderRadius: 4 }}>
                                                <span className="badge ready" style={{ fontSize: 9 }}>✓ Adapted</span>
                                                <span>
                                                  {typeof rc.source_value === 'string' && (
                                                    <span><b>{rc.resource_field} authorized description:</b> {rc.source_value}. </span>
                                                  )}
                                                  <b>{rc.resource_field} approved adaptation:</b> {rc.adapted_value}
                                                </span>
                                                <span className="muted" style={{ fontSize: 10 }}>({rc.library_key}:{rc.source_id})</span>
                                              </div>
                                            ))}
                                          </div>
                                        )}
                                      </div>
                                    </div>
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="row" style={{ marginTop: 14, paddingTop: 10, borderTop: '1px solid var(--line)' }}>
                  <button onClick={() => navigateStep('takes')}>← Back: Takes</button>
                  <button className={planDirty ? 'primary' : ''} onClick={savePlan} disabled={!planDirty || planEditLocked}>
                    Save Plan
                  </button>
                  <span className="spacer" style={{ flex: 1 }} />
                  <button
                    className="primary"
                    onClick={() => navigateStep('generation')}
                    disabled={planEditLocked || reviewBlocksFinalization || !canProceedToGeneration(s, { plan, planRevision, planDirty, conflicts: planConflictMarkers, reviewedRevision })}
                    title={
                      planDirty
                        ? 'Save plan before proceeding to generation'
                        : hasKnownStaleAdaptations
                            ? 'A reviewed adaptation is stale; save a new plan revision before proceeding'
                            : reviewBlocksFinalization
                              ? 'All take reviews must load successfully before proceeding'
                              : (hasPlanConflictMarkers && reviewedRevision !== planRevision)
                                ? 'Approve Review for the current plan revision before proceeding with resource markers'
                          : !plan?.takes?.length
                            ? 'Plan must contain at least one take'
                            : !Boolean(s?.workflow_id || s?.model?.workflow_id || s?.settings?.workflow_id)
                              ? 'Session has no workflow assigned'
                              : hasPlanConflictMarkers
                                ? 'Resource markers remain visible; this plan revision is approved'
                                : 'Proceed to Generation'
                    }
                  >
                    Proceed to Generation →
                  </button>
                </div>
              </div>
            )
          })()}

          {activeStep === 'generation' && (
            <div style={{ marginBottom: 4 }}>
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <h3 style={{ margin: 0 }}>5. Generation</h3>
                  <p className="muted" style={{ margin: '2px 0 0' }}>
                    {s.shots.length > 0
                      ? `Session gallery (${done}/${s.shots.length} completed).`
                      : pending > 0
                        ? `Session plan reviewed (${pending} pending shots ready to run). Click Run in the top bar to shoot.`
                        : 'Session plan reviewed. Materialized takes will appear here ready to run.'}
                  </p>
                </div>
                <button onClick={() => navigateStep('review')}>← Back to Review</button>
              </div>
            </div>
          )}
            </>
          )}
        </div>
      )}

      {(!isResource || (canPreparePlan({ plan, planRevision }) && activeStep === 'generation')) && (
        <div className="row" style={{ margin: '10px 0' }}>
          <div className="progress"><div style={{ width: `${(done / Math.max(1, s.shots.length)) * 100}%` }} /></div>
          <span className="muted">{done}/{s.shots.length} done{failed ? ` · ${failed} failed` : ''}</span>
          <span className="spacer" style={{ flex: 1 }} />
          {/* Only the copies of this shoot are offered: comparing two photos means
              the same take on the same seed, and no other pair of sessions has
              that. Picking one turns every photo that has a twin into a
              before/after wipe in the lightbox. */}
          {family.length > 0 && (
            <select style={{ width: 'auto' }} value={twinId}
                    title="Compare with a copy of this session — same takes, same seeds, other base model"
                    onChange={(e) => setTwinId(Number(e.target.value))}>
              <option value={0}>Compare with…</option>
              {family.map((f) => (
                <option key={f.id} value={f.id}>{shotWith(f)} · {f.done_count}/{f.shot_count}</option>
              ))}
            </select>
          )}
          <select style={{ width: 'auto' }} value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">All</option>
            <option value="keep">Without rejects</option>
            <option value="picks">Picks only (4★+)</option>
          </select>
          {(() => {
            const minRating = filter === 'picks' ? 4 : 1
            const exportCount = s.shots.filter((x) => x.status === 'done' && !x.rejected && x.rating >= minRating).length
            const url = `/api/sessions/${id}/export?min_rating=${minRating}`
            return (
              <a href={exportCount > 0 ? url : undefined} download
                 className={exportCount > 0 ? 'button' : 'button disabled'}>
                Download picks ({exportCount})
              </a>
            )
          })()}
          {(() => {
            const minRating = filter === 'picks' ? 4 : 1
            const count = s.shots.filter((x) => x.status === 'done' && !x.rejected && x.rating >= minRating).length
            const url = `/api/sessions/${id}/contact-sheet?min_rating=${minRating}`
            return (
              <a href={count > 0 ? url : undefined} download
                 className={count > 0 ? 'button' : 'button disabled'}>
                Contact sheet ({count})
              </a>
            )
          })()}
          {(() => {
            // Same threshold the export uses, read the other way: "below X" is the
            // complement of "X and up". Picks filter -> reshoot everything that
            // isn't a pick; otherwise just the unrated. The anchor stays put for
            // the same reason the per-shot ↺ refuses it.
            const minRating = filter === 'picks' ? 4 : 1
            const reshootCount = s.shots.filter((x) =>
              x.status === 'done' && x.rating < minRating && !anchors.includes(x.id)
            ).length
            return (
              <button disabled={reshootCount === 0}
                      title={reshootCount > 0
                        ? `Delete ${reshootCount} photo${reshootCount === 1 ? '' : 's'} and put ${reshootCount === 1 ? 'it' : 'them'} back in the queue on a new seed`
                        : 'No finished shots are below the current threshold'}
                      onClick={() => {
                        if (confirm(`Reshoot ${reshootCount} shot${reshootCount === 1 ? '' : 's'} below ${minRating}★? Their photos will be deleted and the takes go back in the queue.`)) {
                          call(() => api.post(`/api/sessions/${id}/reshoot-below?min_rating=${minRating}`))
                        }
                      }}>
                Reshoot below {minRating}★ ({reshootCount})
              </button>
            )
          })()}
        </div>
      )}

      {/* The three choices every refused Run is about. Each saves on change, like
          the reference workflow selector below: a Save button here would be one
          more thing to forget between the error message and the retry. */}
      {settingsOpen && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Settings</h3>
          {/* A shoot changes job halfway on purpose: edit the pose, keep the one
              that worked, then turn the camera on it. That is one session with
              two graphs in turn, so the kind moves with it — otherwise the
              selector below filters away the very graph the next batch needs. */}
          {/* The runner re-reads the session before every take, so a graph swapped
              mid-queue silently sends the rest of the shoot somewhere else. The
              queue is serial and short-lived; waiting is the whole fix. */}
          {running && (
            <p className="rule">
              This session is running. The remaining takes read these values as they
              come up, so changing one now would send the rest of the queue through a
              different graph. Wait for it to finish, or Cancel.
            </p>
          )}
          <div className="row" style={{ marginBottom: 10 }}>
            <label style={{ width: 'auto', margin: 0 }}>Kind</label>
            {Object.entries(KINDS).map(([k, spec]) => (
              <button key={k} className={'chip' + (kind === k ? ' on' : '')} title={spec.blurb}
                      disabled={running}
                      onClick={() => call(() => api.patch(`/api/sessions/${id}`, { settings: { kind: k } }))}>
                {spec.label}
              </button>
            ))}
          </div>
          <div className="grid-form">
            <div>
              <label title="The graph for takes with ref unticked — the ones painted from noise. An editing or camera-angle graph does not go here.">
                Workflow (new photos)
              </label>
              <select value={s.workflow_id ?? ''} disabled={running}
                      onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                        { workflow_id: Number(e.target.value) || 0 }))}>
                <option value="">— the model's —</option>
                {forKind(workflows, 't2i').map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </div>
            <div>
              <label title="The graph for takes marked ref — the ones that edit the reference photo.">
                Reference workflow (edits)
              </label>
              <select value={s.reference_workflow_id ?? ''} disabled={running}
                      onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                        { reference_workflow_id: Number(e.target.value) || 0 }))}>
                <option value="">— none, text to image only —</option>
                {forKind(workflows, kind && KINDS[kind].refKind).map((w) => (
                  <option key={w.id} value={w.id}>{w.name}</option>
                ))}
              </select>
            </div>
            <div style={{ gridColumn: 'span 2' }}>
              <label title="Only applied to the workflow above, and only if it maps the slot. An editing graph loads its own model.">
                Base model
              </label>
              {/* The profile rides along with the choice. Overwriting rather than
                  filling blanks is deliberate: steps and cfg always hold a value,
                  so a fill-the-blanks rule would never fire and picking a model
                  would keep shooting it at the last model's settings. What makes
                  it safe is that it is not silent — the line below says what
                  arrived, and every value stays editable. */}
              <BaseModelSelect value={s.settings.checkpoint} models={baseModels} disabled={running}
                               onChange={(v) => call(() => api.patch(`/api/sessions/${id}`,
                                 { settings: { checkpoint: v, ...(checkpointProfile(config, v) || {}) } }))} />
              {profile && (
                <p className="muted" style={{ margin: '4px 0 0' }}>
                  This model's profile: <b>{profileSummary(profile)}</b> — filled in when you pick it.
                </p>
              )}
            </div>
            {/* The two dials an identity pass is made of: how far the edit may
                travel from the photo, and how hard the character LoRA pulls. They
                were only settable when the session was created, which is before
                you have the photo whose face drifted. */}
            <div>
              <label title="How far an img2img edit may travel from the reference. Low keeps the frame and repaints detail — 0.2 to 0.35 is an identity pass. High repaints the outfit and moves the pose.">
                Denoise
              </label>
              <input type="number" step="0.05" min="0" max="1" disabled={running}
                     value={s.settings.denoise ?? ''} placeholder="workflow's own"
                     onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                       { settings: { denoise: e.target.value === '' ? null : parseFloat(e.target.value) } }))} />
            </div>
            <div>
              <label title="Only applied if the workflow maps it.">LoRA strength</label>
              <input type="number" step="0.05" min="0" disabled={running}
                     value={s.settings.lora_strength ?? ''} placeholder="workflow's own"
                     onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                       { settings: { lora_strength: e.target.value === '' ? null : parseFloat(e.target.value) } }))} />
            </div>
            <div>
              <label title="Steps">Steps</label>
              <input type="number" min="1" disabled={running}
                     value={s.settings.steps ?? ''} placeholder="8"
                     onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                       { settings: { steps: e.target.value === '' ? null : parseInt(e.target.value, 10) } }))} />
            </div>
            <div>
              <label title="CFG Scale">CFG</label>
              <input type="number" step="0.1" min="0" disabled={running}
                     value={s.settings.cfg ?? ''} placeholder="1.0"
                     onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                       { settings: { cfg: e.target.value === '' ? null : parseFloat(e.target.value) } }))} />
            </div>
            <div>
              <label title="Sampler">Sampler</label>
              <SamplerSelect value={s.settings.sampler} options={baseModels.samplers} disabled={running}
                             onChange={(v) => call(() => api.patch(`/api/sessions/${id}`,
                               { settings: { sampler: v } }))} />
            </div>
            <div>
              <label title="Scheduler">Scheduler</label>
              <SamplerSelect value={s.settings.scheduler} options={baseModels.schedulers} disabled={running}
                             onChange={(v) => call(() => api.patch(`/api/sessions/${id}`,
                               { settings: { scheduler: v } }))} />
            </div>
          </div>
          {/* Offered, not applied: swapping the graph out from under a session
              because a dropdown moved is exactly the silent change the panel
              above warns about. One click, and it says what it will do. */}
          {tunedWf && tunedWf.id !== shootWf?.id && (
            <p className="rule">
              <b>{tunedWf.name}</b> is written for this base model — it loads it itself, with
              the sampler, steps and cfg that model wants.{' '}
              <button disabled={running}
                      onClick={() => call(() => api.patch(`/api/sessions/${id}`,
                        { workflow_id: tunedWf.id }))}>Shoot with it</button>
            </p>
          )}
          {/* A graph tuned for one checkpoint carries its own sampler, steps and
              cfg, and the sane way to use one is to leave those unmapped. Then
              this panel and the header are quietly describing a session that is
              not the one being shot — unless they say so. */}
          {unmapped.length > 0 && (
            <p className="rule">
              Not mapped by the graphs above: <b>{unmapped.join(', ')}</b> — whatever the session
              says, the workflow's own value is what runs. That is how a graph tuned for one base
              model is meant to work; map the slot in <a href="#/workflows">Workflows</a> if you
              want the session to drive it instead.
            </p>
          )}
          {!isResource && (
            <>
              {/* The arc, typed once: one wardrobe per line, in the order the shoot
                  passes through them. A composed run spreads them over its
                  photographs (`spread`), so three states over twelve photographs is
                  four photographs a state — and the wardrobe holds still inside a
                  stage, which is what makes two photographs of one stage two
                  photographs of one stage.
                  Written on blur and not on every keystroke: a PATCH per character
                  is a session row rewritten forty times while somebody types a
                  sentence. Empty is the session's one wardrobe in every
                  photograph. */}
              {/* The outfit, and the arc it derives. Picking one writes the session's
                  own wardrobe too: a fill-cell sends no states and composes in
                  `session.wardrobe`, so leaving that pointing at an older outfit
                  would put two shoots in one session and file them under one cell. */}
              <div style={{ marginTop: 10 }}>
                <label title="An outfit from the wardrobe catalogue. Its garments come off in the order the outfit lists them, one per state, and the last state is bare.">
                  Outfit
                </label>
                <select value={s.settings?.outfit || ''} disabled={running}
                        onChange={(e) => call(() => api.patch(`/api/sessions/${id}`, {
                          settings: { outfit: e.target.value },
                          wardrobe: statesFor(e.target.value)[0] || s.wardrobe,
                        }))}>
                  <option value="">the session's own wardrobe</option>
                  {outfits().map((o) => (
                    <option key={o.key} value={o.key}>{o.label || o.key}</option>
                  ))}
                </select>
              </div>
              <div style={{ marginTop: 10 }}>
                <label title="One wardrobe per line, in order. A composed run spreads them over its photographs, so the shoot undresses without a writer. Empty: the session's wardrobe in every photograph.">
                  Wardrobe states ({wardrobeStates(s).length || "the session's"}
                  {!s.settings?.wardrobe_states?.length && s.settings?.outfit ? ', from the outfit' : ''})
                </label>
                <textarea rows={4} disabled={running} defaultValue={wardrobeStates(s).join('\n')}
                          key={wardrobeStates(s).join('\n')}
                          placeholder="She wears a black wool coat, and a grey jumper.&#10;She wears a grey jumper.&#10;She wears nothing at all."
                          onBlur={(e) => call(() => api.patch(`/api/sessions/${id}`, {
                            settings: { wardrobe_states: e.target.value.split('\n').filter((l) => l.trim()) },
                          }))} />
              </div>
            </>
          )}
          <p className="muted" style={{ marginBottom: 0 }}>
            Photos already shot keep the settings they were shot with. These apply to what runs next.
          </p>
        </div>
      )}

      {/* Two base models on the same shoot is the only way to tell them apart:
          one frame is luck, twenty is the model. So the copy carries the takes,
          the composed prompts and the seeds unchanged, and the dials here are the
          four things a different checkpoint asks for: the model, its graph, its
          steps and its sampler pair. */}
      {clone && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Clone this session</h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            Same look, wardrobe, takes and seeds — so what changes in the photos is what
            you change here. Nothing is queued: the copy lands as a draft with every take
            pending. Photos brought in from outside are copied as they are; everything else
            is shot again.
          </p>
          <div className="grid-form">
            <div style={{ gridColumn: 'span 2' }}>
              <label>Name</label>
              <input value={clone.name} onChange={(e) => setClone({ ...clone, name: e.target.value })} />
            </div>
            <div style={{ gridColumn: 'span 2' }}>
              <label title="Only applied if the workflow maps the slot — the same rule the run preflight checks. Pick as many as you want to try: each one is a copy of its own.">
                Add a base model
              </label>
              {/* The single-model select, used as an *add* control: picking one
                  appends a row below. Three models is three copies from one
                  press — the alternative is opening this panel three times and
                  retyping the name each go. */}
              <BaseModelSelect value="" models={baseModels}
                               onChange={(v) => v && !clone.rows.some((r) => r.checkpoint === v)
                                 && setClone({
                                   ...clone,
                                   rows: [...clone.rows, {
                                     checkpoint: v, steps: clone.steps, cfg: s.settings.cfg ?? '',
                                     // Inherited from the source, then overwritten
                                     // by whatever this checkpoint's profile names.
                                     // A sweep is worth running at each model's own
                                     // settings; holding one sampler across four
                                     // checkpoints compares the sampler, not them.
                                     sampler: s.settings.sampler ?? '',
                                     scheduler: s.settings.scheduler ?? '',
                                     ...(checkpointProfile(config, v) || {}),
                                     // Its own graph if one exists, the source's otherwise.
                                     workflow_id: wfFor(v)?.id || '',
                                   }],
                                 })} />
            </div>
          </div>
          {/* Steps per row, not one for all: mixing a distilled model with a full
              one is the normal case, and 8 steps on the full one wastes the whole
              copy — twenty-odd photos before it is visible. */}
          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>Base model</th><th>Workflow</th>
                <th style={{ width: 80 }}>Steps</th>
                <th style={{ width: 70 }}>CFG</th>
                <th style={{ width: 150 }}>Sampler</th>
                <th style={{ width: 140 }}>Scheduler</th>
                <th style={{ width: 40 }} />
              </tr>
            </thead>
            <tbody>
              {clone.rows.map((r, i) => {
                // A row's graph decides whether its boxes are anything but
                // decoration — the same rule the header line strikes through.
                const rowSteps = rowMaps(r, 'steps')
                const edit = (patch) => setClone({
                  ...clone,
                  rows: clone.rows.map((x, j) => (j === i ? { ...x, ...patch } : x)),
                })
                return (
                <tr key={r.checkpoint || i}>
                  <td>{r.checkpoint || "the workflow's own"}</td>
                  <td>
                    {/* Prefilled with the graph that names this checkpoint, and
                        still a dropdown: the whole point of a sweep is shooting
                        one model through another's graph on purpose. */}
                    <select value={r.workflow_id || ''} style={{ width: '100%' }}
                            onChange={(e) => edit({ workflow_id: e.target.value })}>
                      <option value="">— this session's —</option>
                      {forKind(workflows, 't2i').map((w) => (
                        <option key={w.id} value={w.id}>
                          {w.name}{w.base_model === r.checkpoint ? ' — written for this model' : ''}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input type="number" min="1" value={rowSteps ? r.steps : ''}
                           disabled={!rowSteps} placeholder="the graph's own"
                           title={rowSteps ? '' : "This graph does not map steps — its own value is what runs"}
                           onChange={(e) => edit({ steps: e.target.value })} />
                  </td>
                  <td>
                    {/* Carried because a profile can set it — muse wants 2 where
                        every other Krea 2 finetune wants 1 — and shown because a
                        value that arrives on its own has to be visible. */}
                    {rowMaps(r, 'cfg') ? (
                      <input type="number" step="0.1" min="0" value={r.cfg ?? ''}
                             placeholder="the graph's own"
                             onChange={(e) => edit({ cfg: e.target.value })} />
                    ) : (
                      <span className="muted" title="This graph does not map cfg — its own value is what runs">—</span>
                    )}
                  </td>
                  {/* The two dials this whole table was missing: across seven Krea 2
                      finetunes no two ask for the same pair, so sweeping checkpoints
                      while holding one sampler compares the sampler, not the models. */}
                  {[['sampler', baseModels.samplers], ['scheduler', baseModels.schedulers]].map(([slot, options]) => (
                    <td key={slot}>
                      {rowMaps(r, slot) ? (
                        <SamplerSelect value={r[slot]} options={options}
                                       onChange={(v) => edit({ [slot]: v })} />
                      ) : (
                        <span className="muted" title={`This graph does not map ${slot} — its own value is what runs`}>
                          the graph's own
                        </span>
                      )}
                    </td>
                  ))}
                  <td>
                    <button className="icon" title="Drop this copy"
                            onClick={() => setClone({ ...clone, rows: clone.rows.filter((_, j) => j !== i) })}>✕</button>
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
          {clone.rows.length > 1 && (
            <p className="muted" style={{ margin: '6px 0 0' }}>
              {clone.rows.length} copies, each named after its model. They are drafts: the
              queue is serial, so you still run them one at a time.
            </p>
          )}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" disabled={!clone.rows.length} onClick={() => call(async () => {
              // One POST per copy. The route takes a name and the settings to
              // override, which is all a second model is, so nothing on the
              // server had to learn about batches.
              const made = []
              for (const r of clone.rows) {
                made.push(await api.post(`/api/sessions/${id}/clone`, {
                  name: `${clone.name} — ${modelStem(r.checkpoint)}`,
                  // A row whose graph does not map the pair carries none: the run
                  // preflight refuses a chosen sampler the graph would ignore, and
                  // that refusal would land on a copy nobody chose one for.
                  settings: { checkpoint: r.checkpoint, steps: Number(r.steps) || s.settings.steps,
                              cfg: r.cfg === '' || r.cfg == null ? s.settings.cfg : Number(r.cfg),
                              sampler: rowMaps(r, 'sampler') ? (r.sampler || '') : '',
                              scheduler: rowMaps(r, 'scheduler') ? (r.scheduler || '') : '' },
                  workflow_id: Number(r.workflow_id) || null,
                }))
              }
              setClone(null)
              // One copy is the shoot you are about to look at. Several are a
              // batch to launch from the list, and jumping into an arbitrary one
              // of them hides the other two.
              if (made.length === 1) go(`/session/${made[0].id}`)
              else api.get('/api/sessions').then(setSessions).catch(() => {})
            })}>
              {clone.rows.length > 1 ? `Create ${clone.rows.length} copies` : 'Create the copy'}
            </button>
            <button onClick={() => setClone(null)}>Cancel</button>
          </div>
        </div>
      )}

      {adding && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Add shots to this session</h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            Same look ({s.look || 'none set'}) — that part does not change. A take marked
            <b> ref</b> skips it entirely and edits the reference photo instead.
          </p>
          {/* The wardrobe is the half that moves, so it is editable here: twenty
              takes in, the shoot is rarely still wearing what it started in, and
              the takes added next should start from where it got to. Saved with
              the shots, so Cancel changes nothing. */}
          <label style={{ marginTop: 8 }}>
            Wardrobe the next takes start from — each row below can still set its own
          </label>
          <textarea rows={2} value={worn} placeholder="none set"
                    onChange={(e) => setWorn(e.target.value)} />
          {kind && KINDS[kind].rule && <p className="rule">{KINDS[kind].rule}</p>}
          {kind === 'angles' && (
            <AnglePicker llm={llm}
                         onAdd={(takes) => setAdding([...adding.filter((x) => x.prompt.trim()), ...takes])} />
          )}
          {/* Here and not on the new-session panel: an expression is an edit of a
              photograph that exists, and a session being created has none. */}
          {kind === 'edit' && (
            <ExpressionPicker
              onAdd={(takes) => setAdding([...adding.filter((x) => x.prompt.trim()), ...takes])} />
          )}
          {/* No `onLook` here: the look belongs to the session, and `add_shots`
              re-reads it from the server anyway. A shoot whose hair, place and
              light changed halfway is two sessions. */}
          <ShotsEditor kind={kind} shots={adding} onChange={setAdding} llm={llm}
                       context={composed(s.model, '')} look={s.look} wardrobe={worn}
                       runSubjects={subjects} />

          {/* Deciding to edit a keeper happens mid-shoot, looking at the gallery —
              not when the session was created. So the reference workflow is picked
              here, the moment a take is marked ref, or the run would be refused
              with no way to satisfy it. */}
          {adding.some((x) => x.reference) && (
            <div className="row" style={{ marginTop: 10 }}>
              <label style={{ width: 'auto', whiteSpace: 'nowrap' }}>Reference workflow</label>
              <select style={{ width: 'auto' }} value={s.reference_workflow_id ?? ''}
                      onChange={(e) => call(() => api.patch(`/api/sessions/${id}`, {
                        reference_workflow_id: e.target.value ? Number(e.target.value) : 0,
                      }))}>
                <option value="">— pick the graph that edits —</option>
                {forKind(workflows, kind && KINDS[kind].refKind).map((w) => (
                  <option key={w.id} value={w.id}>{w.name}</option>
                ))}
              </select>
              <span className="muted">
                {anchors.length ? 'an img2img or instruction-editing graph, with its reference image slot mapped'
                  : 'and mark a finished photo as the reference with 📎 — the ref takes have nothing to edit yet'}
              </span>
            </div>
          )}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" onClick={() => call(async () => {
              // The session's wardrobe first: `add_shots` reads it from the row,
              // and a take that left its own box empty is asking for this one.
              if (worn !== s.wardrobe) await api.patch(`/api/sessions/${id}`, { wardrobe: worn })
              await api.post(`/api/sessions/${id}/shots`, { shots: adding, seed_mode: 'random' })
              setAdding(null)
            })}>Add</button>
            <button onClick={() => setAdding(null)}>Cancel</button>
          </div>
        </div>
      )}

      {(!isResource || (canPreparePlan({ plan, planRevision }) && activeStep === 'generation')) && (
        <>
          {shots.length > 0 ? (
            <div className="shots">
              {shots.map((shot) => (
                <div className={'shot' + (shot.rejected ? ' rejected' : '')
                                + (anchors.includes(shot.id) ? ' is-anchor' : '')} key={shot.id}>
                  {shot.status === 'done'
                    ? <img src={shotImage(shot.id)} alt={shot.shot_label} loading="lazy" onClick={() => setZoom(shot)} />
                    : <div className="ph">
                        {shot.status === 'running' ? '⏳ generating…'
                          : shot.status === 'pending' ? '· queued'
                            : `⚠ ${shot.error || shot.status}`}
                        {shot.status === 'pending' && !!shot.use_reference && (
                          <select className="guide" disabled={running}
                                  value={(shot.reference_shot_ids || [])[0] || ''}
                                  title="The photograph that guides this take. Follows the session's 📎 pick unless you name one here."
                                  onChange={(e) => guideWith(shot, e.target.value)}>
                            <option value="">📎 session's pick</option>
                            {s.shots.filter((x) => x.status === 'done').map((x) => (
                              <option key={x.id} value={x.id}>{x.shot_label || `shot ${x.id}`}</option>
                            ))}
                          </select>
                        )}
                      </div>}
                  <div className="bar">
                    <div className="stars">
                      {[1, 2, 3, 4, 5].map((n) => (
                        <span key={n} className={'star' + (shot.rating >= n ? ' on' : '')} onClick={() => rate(shot, n)}>★</span>
                      ))}
                    </div>
                    <span className="spacer" style={{ flex: 1 }} />
                    {shot.status === 'done' && (
                      <>
                        <button className="icon" onClick={() => toggleAnchor(shot)}
                                title={anchors.includes(shot.id)
                                  ? 'Stop using this photo as the reference'
                                  : 'Use as the reference — takes marked ref will edit this photo'}>
                          {anchors.includes(shot.id) ? '📌' : '📎'}
                        </button>
                        {/* A native menu on purpose: a popover would need its own
                            dismiss, focus and z-index for six items the browser
                            already knows how to show. */}
                        <select className="continue" value="" disabled={running}
                                title="Continue with this photo — as the reference of this session, or of a new one"
                                onChange={(e) => continueWith(shot, e.target.value)}>
                          <option value="">→</option>
                          <optgroup label="Continue here">
                            {continuations.map(([k, spec]) => (
                              <option key={k} value={`here:${k}`}>{spec.label}</option>
                            ))}
                          </optgroup>
                          <optgroup label="In a new session">
                            {continuations.map(([k, spec]) => (
                              <option key={k} value={`new:${k}`}>{spec.label}…</option>
                            ))}
                          </optgroup>
                        </select>
                      </>
                    )}
                    <button className="icon" title="More like this — same prompt, new seeds"
                            onClick={() => moreLikeThis(shot)}>⟳</button>
                    <button className="icon"
                            title={shot.use_reference
                              ? 'Strength sweep — this prompt and seed at 1.0 / 1.5 / 2.0 / 3.0, so the only difference you see is the dial'
                              : 'Tweak on this same seed — edit the prompt, compare the change'}
                            onClick={() => reshootSameSeed(shot)}>⚖</button>
                    {shot.status === 'done' && (
                      <button className="icon"
                              title="Reshoot — this photo is deleted and the take goes back in the queue with a new seed"
                              onClick={() => {
                                if (confirm(`Delete this photo and shoot "${shot.shot_label}" again?`)) {
                                  call(() => api.post(`/api/shots/${shot.id}/reshoot`))
                                }
                              }}>↺</button>
                    )}
                    <button className="icon" title={shot.rejected ? 'Restore' : 'Reject'}
                            onClick={() => call(() => api.patch(`/api/shots/${shot.id}`, { rejected: !shot.rejected }))}>
                      {shot.rejected ? '↩' : '✕'}
                    </button>
                    {/* Delete, as opposed to Reject: the row and the file go, and
                        nothing takes their place. The cell counts are NOT touched -
                        a judged photograph stays counted after its row is gone, so
                        the confirm says so rather than the button quietly corrupting
                        a measurement. Reject is the one that takes a photograph out
                        of a judging pass and leaves the evidence where it is. */}
                    <button className="icon" title="Delete this photo"
                            onClick={() => {
                              const judged = !!shot.verdicts
                              if (confirm(judged
                                ? 'This photo has already been judged and its answer stays counted in the cell. Delete it anyway?'
                                : 'Delete this photo?')) {
                                call(() => api.del(`/api/shots/${shot.id}`))
                              }
                            }}>🗑</button>
                  </div>
                  <div className="muted" style={{ padding: '0 6px 6px', fontSize: 11 }} title={shot.prompt}>
                    {shot.shot_label} · seed {shot.seed}
                    {/* Which photos the picked copy actually has a twin for: one that
                        has not been shot there yet, or was reshot on a new seed, is
                        not comparable and says so instead of opening a plain photo. */}
                    {twin && (twinOf(shot)
                      ? <span title={`Compares with ${shotWith(twin)}`}> · ⇄</span>
                      : <span title="No twin in the session being compared — not shot yet, or reshot on another seed"> · —</span>)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className={isResource ? 'panel' : ''} style={isResource ? { textAlign: 'center', padding: '32px 16px', margin: '14px 0' } : undefined}>
              <p className="muted" style={{ margin: isResource ? '0 0 6px' : undefined }}>
                {isResource && s.shots.length === 0
                  ? 'No photos have been generated for this session yet.'
                  : 'Nothing to show with this filter.'}
              </p>
              {isResource && s.shots.length === 0 && (
                <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                  {pending > 0
                    ? 'Click Run in the top bar to start shooting.'
                    : 'Complete preparation and review. Takes must be materialized as pending shots before running.'}
                </p>
              )}
            </div>
          )}
        </>
      )}

      {zoom && (
        <div className="lightbox" onClick={() => setZoom(null)}>
          <div>
            {/* The same wipe the reference comparison uses, on the same frame,
                for the same reason: two models rarely differ by more than a face
                and a fabric, and that difference is invisible when the eye has
                to travel between two pictures. A picked twin wins over the
                reference view — it is the comparison that was asked for. */}
            {twin && twinOf(zoom)
              ? <div onClick={(e) => e.stopPropagation()}>
                  <div className="compare" style={{ '--split': `${split}%` }}>
                    <img src={shotImage(zoom.id)} alt="" />
                    <img className="before" src={shotImage(twinOf(zoom).id)} alt="" />
                    <span className="handle" />
                  </div>
                  <input type="range" min="0" max="100" value={split}
                         onChange={(e) => setSplit(Number(e.target.value))} />
                  <div className="meta">
                    ← {shotWith(twin)} · {shotWith(s)} →
                    {/* One of the two was reshot, so the noise differs as well
                        as the model. Still worth comparing — same take, same
                        prompt — but it is no longer the model alone, and a wipe
                        that does not say so reads as if it were. */}
                    {twinOf(zoom).seed !== zoom.seed && (
                      <><br />seed {twinOf(zoom).seed} vs {zoom.seed} — one side was reshot,
                        so the noise differs too, not only the model</>
                    )}
                  </div>
                </div>
              : before(zoom)
              // Before/after on one image rather than two side by side: an edit
              // that only moves a collar is invisible when the eye has to travel
              // between two frames.
              ? <div onClick={(e) => e.stopPropagation()}>
                  <div className="compare" style={{ '--split': `${split}%` }}>
                    <img src={shotImage(zoom.id)} alt="" />
                    <img className="before" src={shotImage(before(zoom))} alt="" />
                    <span className="handle" />
                  </div>
                  <input type="range" min="0" max="100" value={split}
                         onChange={(e) => setSplit(Number(e.target.value))} />
                  <div className="meta">
                    ← reference (shot {before(zoom)}) · this edit →
                  </div>
                </div>
              : <img src={shotImage(zoom.id)} alt="" />}
            <div className="meta">{zoom.prompt}<br />seed {zoom.seed} · {zoom.filename}</div>
          </div>
        </div>
      )}
    </>
  )
}
