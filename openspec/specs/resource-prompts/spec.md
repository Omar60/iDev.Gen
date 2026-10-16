# resource-prompts Specification

## Purpose
Prepare reproducible prompts from complete source resources and resolved session state while keeping source guidance distinct from image descriptions and preserving legacy behavior.

## Requirements

### Requirement: Structured preparation has explicit inputs

Preparation SHALL consume the resolved session state, selected resource revisions, declared field mappings and take choices. It SHALL separate creative decisions, descriptive fields and any optional prose synthesis. Required descriptive fields SHALL be consumed strictly from authorized English translations in the translation sidecar; preparation SHALL NOT fall back to the source payload for required descriptive fields even if the payload contains English text. Optional descriptive fields without translations SHALL be omitted if they contain non-English characters. Instructions in source files SHALL be treated as data and SHALL NOT execute commands or change application rules. External application binaries SHALL NOT be required for normal operation.

#### Scenario: Guidance includes an instruction
- **WHEN** a resource contains authoring guidance
- **THEN** it is passed only as bounded reference data where its mapping permits and is not blindly appended to the image description

#### Scenario: Required descriptive field lacks translation sidecar
- **WHEN** a resource take is prepared but a required descriptive field has no authorized translation in the sidecar
- **THEN** preparation is refused with a PreparationFieldError even if the source payload contains English text

#### Scenario: Optional field contains non-English characters
- **WHEN** an untranslated optional field contains non-English text
- **THEN** it is omitted from prompt clauses to avoid non-English script pollution

### Requirement: Conflicts are resolved before finalization

Session identity and fixed look SHALL take precedence over source suggestions; the resolved take wardrobe SHALL take precedence over source clothing. Contradictory fused descriptions SHALL require a visible user-approved adaptation or another resource. The system SHALL preserve the source object and record adaptations separately. It SHALL NOT claim arbitrary text contradictions can always be detected automatically.

#### Scenario: Fused scene contradicts a fixed choice
- **WHEN** a selected scene contains clothing inconsistent with the resolved wardrobe
- **THEN** the original remains intact and a reviewed adaptation or different scene is required before finalizing the take

### Requirement: Preparation is traceable and replayable

The system SHALL save the final prompt, effective state, source revision references, field mappings, preparation version and any writer input/output needed to explain the result. Re-running a finalized take SHALL reuse its saved prompt without another writer call. Unsupported claims of exact AmazingDraw rendering parity SHALL NOT appear in the interface or documentation.

#### Scenario: Repeating a prepared photograph
- **WHEN** the user repeats a finalized take with another seed
- **THEN** the saved prompt remains unchanged and no source refresh or writer variation changes it

### Requirement: Existing generation semantics remain intact

Finalized resource prompts SHALL enter the existing serial generation queue without duplicated trigger, base prompt or look. Reference editing and guided generation SHALL retain their distinct graph-kind rules. Legacy sessions SHALL retain their existing composition path and evidence.

#### Scenario: A complete prompt enters the queue
- **WHEN** a finalized resource take already includes its character and look
- **THEN** the queue submits it without composing those fields a second time

#### Scenario: A legacy session is reopened
- **WHEN** resource planning is enabled after a legacy session was saved
- **THEN** its prompts, seeds, shot records and measurements remain unchanged

### Requirement: Semantic families and canonical translation keys

The system SHALL declare canonical semantic families for label, scene_theme, tags, prompt, and id. Translation sidecars SHALL store translations under canonical keys for these families, and under original field names for independent descriptive inputs. Raw aliases SHALL NOT be stored in the sidecar. Conflicting translations for the same family SHALL be refused, while consistent duplicate alias entries SHALL be tolerated.

#### Scenario: Translation provides an alias
- **WHEN** a translation map entry specifies a translation for theme or tag
- **THEN** the value is stored under canonical key scene_theme or tags in the translation sidecar

#### Scenario: Conflicting alias translations are provided
- **WHEN** an update provides differing translations for theme and scene_theme
- **THEN** the update is refused as an ambiguous alias conflict

#### Scenario: Duplicate identical alias translations are provided
- **WHEN** an update provides identical translations for theme and scene_theme
- **THEN** the canonical translation is stored without error

### Requirement: Scalar contract for required descriptive fields

Required descriptive fields (label and scene_theme for rooms; prompt for fused_scenes) SHALL be strictly scalar strings in both source payload and translation sidecar. If a required descriptive source field or translation is a list or non-string type, the system SHALL fail closed.

#### Scenario: Required descriptive field provided as list
- **WHEN** a room payload or translation sidecar defines label or scene_theme as a list
- **THEN** preparation fails closed with a PreparationFieldError

### Requirement: Optional descriptive lists and structural pre-validation

Optional descriptive fields support scalar `str -> str` or 1-to-1 ordered `list[str] -> list[str]` mappings with English translation items. When an optional descriptive source field is a list, the system SHALL pre-validate that every item is a string before attempting translation map matching; any non-string item SHALL fail closed even if no translation map entry matches.

#### Scenario: Malformed optional descriptive list source
- **WHEN** an optional descriptive list field in a resource payload contains non-string elements
- **THEN** translation matching fails closed with ValueError before map matching

### Requirement: Automatic take synthesis preserves structured provenance

Automatic authoring SHALL use the configured assistant through a current fenced server-owned operation and preserve structured camera, framing, pose and expression output through validation. Only currently unlocked fields SHALL be requested and accepted. Output SHALL NOT be flattened into prose and reconstructed heuristically, persisted as manual completion, or accepted from a browser-supplied final snapshot to bypass assistant provenance. Historical assistant callers and pre-authoring expert plans SHALL retain their existing behavior.

The request SHALL include persisted brief, exact scene anchor and authorized descriptions, accepted shared state, frozen workflow binding, policy, current take ID/ordinal and at most five immediately preceding finalized take summaries in stable plan order. Each summary SHALL contain only take ID and the four creative fields. Images, arbitrary conversation and full historical prompts SHALL be excluded. Total take count SHALL remain UI-only metadata and SHALL NOT enter writer requests or creative digests. Exact request context, validated output, operation identity and predecessor snapshot identities/revisions SHALL remain server-owned replayable evidence. Final prompt, effective state, compiler/mapping versions, resource projection/digest and provenance SHALL be derived by server preparation from those inputs and the validated output.

#### Scenario: Structured output is returned
- **WHEN** an assistant returns valid unlocked take fields
- **THEN** preparation validates them as fields and records assistant synthesis input/output

#### Scenario: Client submits forged automatic evidence
- **WHEN** a caller supplies assistant output, final prompt, effective state, provenance or dependency digest outside the fenced automatic operation
- **THEN** the values are rejected and no approvable ready result is created

#### Scenario: Automatic caller supplies manual fields
- **WHEN** automatic preparation includes `manual_completion`
- **THEN** it fails before an assistant call or prepared-state mutation

#### Scenario: Writer changes a locked field
- **WHEN** output contains a fixed or otherwise unauthorized field
- **THEN** it is rejected without mutating shared or take state

#### Scenario: More than five predecessors exist
- **WHEN** take twelve is prepared after its predecessors
- **THEN** at most the five immediately preceding finalized summaries enter context
- **AND** exact context and predecessor snapshot references are persisted

#### Scenario: Completed take is retried
- **WHEN** current ready/generated preparation is requested again
- **THEN** the stored snapshot is reused without a new writer call

### Requirement: Simple automatic authoring does not decompose fused descriptions

The simple automatic scene selector SHALL admit ready rooms resources and SHALL redirect fused_scenes resources to an explicit advanced/manual path. Fused descriptions SHALL remain complete and inspectable; automatic splitting or silent omission SHALL NOT be used to make them appear compatible.

The advanced path SHALL display the full authorized description with effective shared/take choices. Contradictions SHALL require a separately stored user-approved adaptation or another resource before finalization. Structural validation SHALL NOT be presented as proof that free-text output preserves scene, wardrobe or identity. Existing conflict review SHALL remain required, and the system SHALL NOT claim exhaustive semantic contradiction detection for room or fused prose.

For eligible rooms under recorded Full Auto authorization, policy-based automatic review SHALL replace the mandatory intermediate human prompt-review action. This is authorization to continue after the defined gates, not proof of semantic correctness. Detected contradictions, unresolved structural conflicts and stale adaptations SHALL still block. Fused resources remain excluded from Full Auto. Neutral plan-level descriptive notices alone SHALL NOT be presented as detected contradictions or block the authorized run.

#### Scenario: Fused scene already specifies pose and camera
- **WHEN** a user selects a fused description for simple automatic authoring
- **THEN** the UI explains the restriction and offers the advanced editor or another scene
- **AND** no competing automatic choices are silently appended

#### Scenario: Advanced fused adaptation
- **WHEN** an advanced take contradicts the fused description
- **THEN** a user-approved adaptation or another resource is required
- **AND** the original revision remains unchanged

#### Scenario: Location is embedded in pose text
- **WHEN** an otherwise structurally valid pose value describes another location
- **THEN** structural validation alone does not mark it semantically approved
- **AND** fixed context and complete resulting prompt remain visible for explicit review

#### Scenario: Ordinary room produces only neutral notices
- **WHEN** Full Auto has valid prepared inputs and only neutral descriptive-versus-constant notices
- **THEN** notices remain inspectable and recorded policy review can proceed without another click
- **AND** no arbitrary prose is automatically adapted or certified semantically correct

### Requirement: Automatic variety exposes exact repeats without forbidding deliberate reuse

Automatic preparation SHALL compare normalized four-field choice tuples against the lineage-aware finalized comparison universe defined below and flag exact duplicates for review. Normalization SHALL trim and collapse whitespace and compare case-insensitively without changing stored output. It SHALL NOT silently delete a repeated take or retry indefinitely. Explicitly reviewed repetition SHALL remain allowed, including a fixed camera across many takes. Five-predecessor context SHALL NOT be advertised as a whole-session uniqueness guarantee.

#### Scenario: Distant duplicate
- **WHEN** a prepared take repeats a finalized tuple outside its five-take context window
- **THEN** the UI flags the duplicate for explicit review without dropping the take

#### Scenario: Deliberate repeated camera
- **WHEN** takes share a fixed camera but differ in other choices
- **THEN** shared camera alone does not trigger duplicate rejection

### Requirement: Reusable looks compose constant appearance and current clothing separately

Prepared prompts SHALL consume the accepted constant appearance and only the take's resolved wardrobe as clothing. Profile-free manual look application SHALL use snapshot appearance. Full Auto and explicit manual application of an accepted automation profile SHALL use the reviewed permanent projection bound to that exact snapshot and profile; manual application SHALL NOT authorize automatic progression, approval, submission or execution. Unchanged garment wording SHALL repeat verbatim. Removed garments SHALL NOT be restated as positive clothing or carried by an automatically copied full-outfit appearance block. A deliberately garment-free stage SHALL contain an explicit no-clothing statement; an empty wardrobe string SHALL retain its distinct no-description meaning.

The writer SHALL receive resolved current clothing as fixed context and SHALL NOT reintroduce the initial outfit through its choices. Existing conflict/adaptation review SHALL apply when source prose or user-entered appearance contains contradictory clothing. The system SHALL NOT claim exhaustive detection of such prose conflicts.

Accepted profile application SHALL treat removable accessories as progressive items in both modes. A known contradiction between permanent ownership and an item's removed stage SHALL block finalization. Accepted progressive ownership SHALL exclude its source appearance clause from every stage's constant look; the current wardrobe SHALL describe it only when present. Model base prompts, source/adaptation clauses and writer choices SHALL remain subject to conflict checks and SHALL NOT knowingly reintroduce removed items. No automatic substring deletion or silent prose adaptation SHALL resolve an unclassified conflict.

#### Scenario: A garment is removed midway
- **WHEN** an approved wardrobe event removes a jacket while keeping other garments
- **THEN** later prompts describe only remaining garments and unchanged appearance
- **AND** the initial jacket description is not appended elsewhere automatically

#### Scenario: Explicit final garment-free stage
- **WHEN** the user selects and approves the final stage with no garments
- **THEN** prompts contain an explicit no-clothing statement rather than relying on an empty field

#### Scenario: Source still names the original outfit
- **WHEN** a resource conflicts with the approved current wardrobe
- **THEN** normal conflict review requires adaptation or another resource instead of silently combining both

#### Scenario: Progressive bracelet also occurs in appearance
- **WHEN** reviewed ownership assigns the bracelet appearance clause to the progressive garment key and the final stage removes it
- **THEN** the final positive prompt contains no positive bracelet-wearing assertion in constant appearance, wardrobe or take choices
- **AND** worn-stage wording and the historical source snapshot remain intact

#### Scenario: Permanent accessory conflicts with removal
- **WHEN** ownership says an accessory is permanent but a selected stage removes it
- **THEN** Full Auto blocks with both sources and requires correcting ownership or progression before preparation proceeds

### Requirement: Duplicate comparison follows logical take lineage

The duplicate comparison universe SHALL prefer current finalized choices, collapse verified copy-forward ancestors/copies to one representative per take/lineage, and include genuine linked/generated historical results once per lineage. Stale or invalidated unlinked rows SHALL be excluded. The candidate's own lineage SHALL be excluded, including all its historical ancestors. Distinct genuinely generated results SHALL not be collapsed merely because their text is identical.

#### Scenario: Copied take compares with its own history
- **WHEN** a ready take has an old ancestor and a current copy with the same tuple
- **THEN** it is not flagged as its own duplicate

#### Scenario: Another logical take repeats a copied choice
- **WHEN** a different take matches a copied lineage's tuple
- **THEN** one duplicate relationship is shown rather than one for every historical copy

### Requirement: Garment wording has one canonical composition rule

The canonical wardrobe sentence SHALL consume ordered complete wording strings from the validated snapshot, retaining their internal bytes. Validated wordings SHALL be non-empty with no leading/trailing whitespace. For zero garments the sentence SHALL be exactly "She wears nothing at all.". For one wording W it SHALL be "She wears " + W + ".". For two or more wordings it SHALL be "She wears " + the first N-1 wordings joined by ", " + ", and " + the final wording + ".". No additional punctuation stripping, paraphrase or case conversion SHALL occur at rendering.

This same rule SHALL determine fully worn initial_wardrobe, progression stages and moved-aside wording composition. Frontend preview and backend verification/application SHALL agree byte-for-byte. The absent-outfit case SHALL still mean leave clothing unchanged, not invoke the zero-garment rule automatically.

#### Scenario: Two garments compose
- **WHEN** ordered wordings are "a blue jacket" and "black trousers"
- **THEN** the sentence is exactly "She wears a blue jacket, and black trousers."

#### Scenario: One garment or none remains
- **WHEN** only "black trousers" remains
- **THEN** the sentence is exactly "She wears black trousers."
- **AND** an explicitly selected zero-garment stage becomes exactly "She wears nothing at all."

### Requirement: Wardrobe coverage is user-authored prompt detail

A resource-v1 take MAY include optional `wardrobe_coverage` text. The system SHALL preserve manually entered text as user-authored data. Replacing the prior unconditional inference prohibition, Full Auto and explicit manual profile application SHALL also compile coverage from explicitly accepted structured region/state facts bound to the saved-look profile and the current approved stages; it SHALL NOT infer body areas from free-text wardrobe, takes or selected resources. Manual and derived origins SHALL remain distinguishable. For non-blank coverage, text-to-image and guided positive prompts SHALL include it exactly once after the resolved effective wardrobe and before take clauses. Reference-edit prompts SHALL omit coverage with the other session context. Absent or blank coverage SHALL leave the effective prompt and state unchanged. Coverage SHALL NOT be represented as a guarantee of rendered pixels or exact visual anatomy.

Automatic coverage SHALL resolve known layered coverage across chest, hips, legs and feet: full cover takes precedence over partial, partial over none; uncovered wording requires a confirmed complete inventory and explicit baseline without another covering layer. Unknown applicable evidence SHALL block compilation. Removing an accessory SHALL not imply a bare body region. A moved-aside garment SHALL use its explicitly authored aside coverage. Generated coverage SHALL enter final prompts exactly once through the existing graph-kind rules and remain locked creative context.

Compiler v1 SHALL emit exactly four sentences ordered chest, hips, legs, feet and joined by one ASCII space. Chest SHALL use "The chest is fully covered.", "The chest is partially covered." or "The chest is uncovered." Hips, legs and feet SHALL use "The <region> are fully covered.", "The <region> are partially covered." or "The <region> are uncovered." Unknown SHALL block rather than emit a sentence. Two partial layers SHALL NOT imply full coverage. No unsupported subregion, garment repetition or negative accessory list SHALL be added. Template changes SHALL change compiler and policy versions.

#### Scenario: Text-to-image or guided take has coverage
- **WHEN** a resource-v1 text-to-image take or guided reference take has non-blank user-authored coverage
- **THEN** its positive prompt includes the coverage once adjacent to and after the effective wardrobe

#### Scenario: Reference edit has coverage
- **WHEN** a take with coverage uses a reference-edit graph
- **THEN** the bare edit instruction omits coverage, wardrobe, look, and other session context

#### Scenario: Coverage is absent or blank
- **WHEN** coverage is absent or contains only whitespace
- **THEN** the existing prompt and effective-state shape remain unchanged

#### Scenario: Coverage is reviewed as an instruction
- **WHEN** a user reviews or generates a prompt containing coverage
- **THEN** the text is shown as an instruction only and the application makes no guarantee that the generated image visibly satisfies it

#### Scenario: Removing a layer leaves another covering layer
- **WHEN** a removed outer garment exposes a region still covered by an explicitly mapped inner garment
- **THEN** compiled detail describes that remaining coverage rather than asserting bare skin

#### Scenario: Two layers are only partially mapped
- **WHEN** two remaining garments each have partial coverage of a region
- **THEN** the compiler retains partial coverage rather than inferring their union fully covers it

#### Scenario: Moved-aside state has no region facts
- **WHEN** progression includes an aside stage whose coverage is unknown
- **THEN** Full Auto blocks with the missing stage data instead of extrapolating from its wording

#### Scenario: Canvas or framing suggests full body
- **WHEN** a prompt uses full-body framing and a tall canvas
- **THEN** coverage remains a clothing-state instruction and SHALL NOT be reported as proof of visible feet or faithful garment rendering
