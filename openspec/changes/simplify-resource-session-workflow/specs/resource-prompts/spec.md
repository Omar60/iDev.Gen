## ADDED Requirements

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

### Requirement: Automatic variety exposes exact repeats without forbidding deliberate reuse

Automatic preparation SHALL compare normalized four-field choice tuples against the lineage-aware finalized comparison universe defined below and flag exact duplicates for review. Normalization SHALL trim and collapse whitespace and compare case-insensitively without changing stored output. It SHALL NOT silently delete a repeated take or retry indefinitely. Explicitly reviewed repetition SHALL remain allowed, including a fixed camera across many takes. Five-predecessor context SHALL NOT be advertised as a whole-session uniqueness guarantee.

#### Scenario: Distant duplicate
- **WHEN** a prepared take repeats a finalized tuple outside its five-take context window
- **THEN** the UI flags the duplicate for explicit review without dropping the take

#### Scenario: Deliberate repeated camera
- **WHEN** takes share a fixed camera but differ in other choices
- **THEN** shared camera alone does not trigger duplicate rejection

### Requirement: Reusable looks compose constant appearance and current clothing separately

Prepared prompts SHALL consume snapshotted appearance as constant look and only the take's resolved wardrobe as clothing. Unchanged garment wording SHALL repeat verbatim. Removed garments SHALL NOT be restated as positive clothing or carried by an automatically copied full-outfit appearance block. A deliberately garment-free stage SHALL contain an explicit no-clothing statement; an empty wardrobe string SHALL retain its distinct no-description meaning.

The writer SHALL receive resolved current clothing as fixed context and SHALL NOT reintroduce the initial outfit through its choices. Existing conflict/adaptation review SHALL apply when source prose or user-entered appearance contains contradictory clothing. The system SHALL NOT claim exhaustive detection of such prose conflicts.

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
