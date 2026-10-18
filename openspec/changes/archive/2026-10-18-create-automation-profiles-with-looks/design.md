# Design

## Context

See `proposal.md` for motivation and the three delta specs for acceptance.

The shared browser save dispatch is `Looks.jsx::saveLook`: new look, new version,
manual photo description and reviewed photo extraction use four existing routes.
Legacy outfit conversion enters this same form; portable JSON import does not.
`saved_looks.create` resolves garment keys before returning the exact snapshot
digest. Automation profiles require those final identities and are currently
published separately by `AutomationProfileEditor`.

`photo_extraction.save_review` already accepts a look-creation callback inside
the photo-stage save transaction. `db.transaction()` nests under the outermost
transaction and cleanup uses post-commit callbacks. Profile publication and
readiness validation are already reusable. The canonical wardrobe stage builder
removes each ordered item, adds an aside stage for the final item when defined,
and ends with no remaining items. No replacement progression algorithm is needed.

## Goals / Non-Goals

**Goals:** Close the gap between reviewed look saving and usable Full Auto
profiles; reuse one review surface, exact existing identity/provenance rules and
the existing all-stage progression; preserve response-loss and photo cleanup
behavior at the combined persistence boundary.

**Non-Goals:** Change generation consent, redefine the runner/coordinator,
automatically certify free text, infer unseen garments, migrate historical looks,
add automation to portable-v1, start live generation, or guarantee image fidelity.

## Decisions

### 1. Extend existing saves with an explicit reviewed automation companion

Add an optional closed `automation` block and client UUID `request_id` to the
new-look, new-version, manual-photo and reviewed-photo save models. Require the
pair together for a combined save. Existing requests without the pair keep
their current result shape and look-only behavior. Extend only combined results
with an `automation_profile` identity/readiness object alongside the existing
saved-look fields. Use the same feature flag, request byte limits, safe errors
and exact integer transport as the surrounding routes.

The primary browser Save includes the companion when the integrated review is
complete. A visibly labeled normal-only save remains available for incomplete
facts and assistant failure; the backend never silently falls back after a
combined request fails. Portable import/export continues using its existing
closed schema and endpoints.

This adds one common service branch to the existing form dispatch instead of a
parallel look resource or a second browser POST that could leave half a result.

### 2. Represent prospective garments by ordered indices until keys exist

The prospective draft contains exact appearance, resolved garment definitions
in current removal order, ownership segments, worn/aside region states, baseline
and inventory completeness. Profile owners use `permanent` or a strict garment
index; coverage entries use those same indices. The UI maintains row identity
while editing and constructs the current ordered indices when requesting review
or saving. Users do not type keys, indices or Unicode offsets.

Canonicalize a separate draft-content digest over exact appearance and ordered
garment definitions, including source keys when present and exact worn/aside
wording. Resolve catalogue/outfit references server-side and recheck them inside
save. This is not the final saved-look content digest. After look creation,
translate indices to actual snapshot garment keys, verify the corresponding
definitions, and build the closed v1 profile with the final key/version/digest.
Reject duplicate, foreign, out-of-range or incorrectly typed references.

Do not reserve temporary catalogue keys or publish an incomplete identity merely
to make the existing post-save profile editor run earlier.

### 3. Propose structured facts before save, then accept them in one review

Add a bounded `POST /api/looks/automation-proposals` operation for the current
manual form using `enhance.run_structured`. It proposes exact-source ownership
and coverage as editable values, never accepted facts. Keep uncertain values
unknown and unresolved ownership visibly unassigned. No provider call occurs
inside a database transaction or inside the durable combined save.

The integrated photo client explicitly requests an automation companion on the
existing extraction operation. Preserve the historical empty-object extraction
request and its response shape for existing callers; opt-in requests receive
the existing look proposal plus an automation candidate/status. Reuse that
vision request's validated source and redacted evidence. Invalid automation
content must not discard an otherwise valid ordinary look proposal: show an
automation-specific failure and retain manual review. Invalid ordinary look
output retains its existing extraction failure behavior. Neither provider path
decides or accepts removal order, inventory completeness or the final baseline.

Store text-assistant proposal evidence in a small additive proposal table with
opaque identity, exact source/draft digest, validated output, non-secret request
projection and a 24-hour expiry. Photo proposal evidence remains owned by its
existing staged proposal and its expiry. Manual structured input needs no
assistant proposal. On accepted save copy relevant proposal evidence into durable
review evidence so later proposal expiry or photo cleanup cannot remove it.

This keeps assistant output independently inspectable without trusting a
client-echoed request as proof that the provider produced it. Reuse existing
sanitization and do not retain endpoint URLs, credentials, image bytes or data URIs.

### 4. Separate original suggestion identity from final reviewed content

The automation companion identifies the exact final draft-content digest and
explicit profile review, inventory, baseline and removal-order confirmations.
When suggestions were used, retain their server-owned source identity separately.
Original suggestions and final user corrections are distinct evidence.

Any appearance, garment/state, order or source change clears the current review
confirmation and invalidates in-flight proposal responses. Revised facts can be
accepted again against the final draft; an original suggestion is then historical
input to that correction, not automatic approval of changed content. Preserve
normal photo correction of appearance and garment text. A name-only edit does
not shift ownership spans, but is still part of save-request replay identity.

The backend recomputes the final draft digest and rejects mismatched review,
stale expected look versions and obsolete/wrong photo proposals before writes.
Validate exact Unicode scalar partitions with the existing ownership rules.
Keep malformed persisted evidence and unsafe assistant output as visible errors.

### 5. Publish reviewed facts with existing accepted provenance semantics

Construct profile provenance server-side as explicit `origin: user` acceptance
with `source_identity: looks-integrated-review`, the final saved-look digest and
accepted revision 1. Store actual suggestions/corrections in the separate review
evidence. Do not relabel a new assistant proposal as `reviewed_structured`:
that origin currently authenticates equivalent facts copied from an existing
immutable source profile and its digest. Keep that verifier unchanged.

Reuse `saved_looks.publish_automation_profile(..., expected_profile_revision=0)`
and `get_automation_profile_readiness` after mapping. The integrated combined
action requires an eligible profile; unknown coverage, unassigned appearance,
unconfirmed inventory or a covering baseline for the final empty outfit remains
an actionable failure. Incomplete data uses the explicit normal-only save, not
an empty revision advertised as ready.

### 6. Commit once and replay the complete response

Add a small durable combined-save receipt keyed by client request UUID, normalized
operation/source/target/content digest and stored result. Bind all reviewed facts,
confirmations, relevant proposal/photo IDs and expected target version. Retain
accepted evidence under this local operation and exact destination identities.

Under one outer `db.transaction()`, resolve an exact committed replay first;
otherwise verify current enablement, target/archive state and final review,
create the look with the existing service, map and publish profile revision 1,
evaluate readiness, persist accepted evidence and store the full response.
For photo paths invoke the existing stage/review service with a creation callback
that creates both objects. Include the automation companion in the stage save
digest so it cannot replay as another reviewed payload. Existing photo receipt
and global combined receipt participate in the same transaction.

Identical races converge on the stored result; changed UUID reuse returns
`409 idempotency_conflict`. Resolve successful photo retries before opening
cleaned image bytes. Preserve historical read-only receipt replay after archive;
refuse every new write to an archived identity. Feature disablement still gates
the request before any new work. Do not use two independently committed saves.

If any write/readiness validation fails, roll back catalogue, outfit, look,
profile, evidence and receipt writes together. Existing post-commit photo cleanup
must remain deferred until this outer commit succeeds. A cleanup warning after
commit does not undo the accepted pair.

### 7. Reuse the profile review controls inside the look form

Extract only the editable ownership/coverage/baseline/inventory controls needed
from `AutomationProfileEditor` into a shared controlled review component. Keep
existing publish, copy and exact-profile history actions in the existing saved
profile editor. The integrated form uses pending garment rows and receives
review state rather than pretending an unsaved draft has a persisted identity.

Photo extraction populates that same review. Manual looks and imported-outfit
conversion use the same controls, with optional text suggestions. Editing and
new-version saving review the new exact content; accepted old state can be
reused only through the existing verified-copy rules, not by cloning provenance
or stale scalar spans. Each new look version starts its own profile revision 1.

Freeze a combined save's request and UUID while pending/unknown. Known no-write
refusals retain edits. Unknown outcomes allow retry/readback only with the same
frozen request, not a new allocation. Decode both version and profile revision
losslessly through `api.js` just as existing exact-profile calls do.

### 8. Auto-select an exact sole ready profile only in new guided creation

Extend `AutomationProfileSelector` with an opt-in unambiguous selection behavior
used by Resources. After discovery for the current exact look identity, choose
the sole eligible revision only when no user selection exists, then load and
verify its exact profile digest through the normal loader. Show that selection
before preflight; do not send `latest` or infer revision 1 without discovery.

Keep explicit selection when multiple ready revisions exist; show blockers when
none are ready. An existing manual choice is not replaced by refresh/publication.
Other consumers, including saved-profile editing and existing session application,
retain their current explicit behavior. Fence A-to-B-to-A responses, unmounts
and superseded requests. Selection changes invalidate the old consent summary.

### 9. Reuse and test the complete removal arc

Use `session_plan._saved_look_wardrobe_stages`,
`build_full_auto_wardrobe_progression_candidate` and the existing coverage state
compiler. Every inventoried removable garment/accessory participates in order.
The initial take is fully dressed; successive canonical stages remove the next
item; the final item's aside state is intermediate; the last take reaches the
all-items-absent stage. Repeat canonical wording when the session has extra takes.
Keep existing `insufficient_stage_count` refusal instead of compressing stages.

The integrated review must expose progressive accessory/garment ownership so
fixed appearance does not reintroduce removed items. Require an explicitly
accepted no-residual-coverage baseline for the outfit-based endpoint; never
default it from an empty inventory or assume arbitrary free text is harmless.
Appearance-only looks retain their existing constant-stage and baseline rules.
No additional undressing policy, timing mode or runner mutation is introduced.

## Risks / Trade-offs

- Ambiguous descriptions or unseen items cannot establish complete inventory
  from a photo alone -> keep unknown facts visible and require one integrated
  acceptance rather than a second setup workflow or fabricated certainty.
- Garment keys appear only during save -> bind prospective ordered definitions
  and map inside the transaction, with failure injection after each write.
- Accepted source content can change while the assistant responds -> exact draft
  binding, current editor epochs, renewed confirmation and target-version checks.
- A successful first POST can lose its response -> retain a durable combined
  receipt and immutable frozen request for retries.
- Reusing a post-save editor prematurely could expand coupling -> share its
  controlled review surface only, keeping persistence/history actions separate.
- Automatic selection can accidentally choose latest -> scope the behavior to
  new guided creation and the unique ready identity verified by discovery.
- Correct prompt stages do not prove garment fidelity in generated pixels ->
  test protocol and prompt bytes with fakes, retain the existing visual limits.

## Migration Plan

1. Add proposal/evidence and combined-save receipt persistence through the
   existing idempotent database initialization, with tests for an existing DB.
2. Add companion-aware services/routes and the integrated review UI; leave old
   unprofiled calls and portable look-v1 compatible.
3. Verify manual, photo and versioned saves plus a complete isolated Full Auto
   journey with layered clothing and a removable accessory through the last take.
4. Update README and canonical look/session guides with verified behavior and
   exact correction paths, then run the required repository gates.

No historical backfill runs on startup. Reverting the feature code may leave
unused additive tables, but must preserve all saved looks and existing profile
rows and must not modify historical sessions. This is not permission to run an
old binary against unrelated upgraded Full Auto state or perform a live downgrade.
