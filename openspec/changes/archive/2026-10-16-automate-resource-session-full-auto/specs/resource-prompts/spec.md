## MODIFIED Requirements

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
