## MODIFIED Requirements

### Requirement: Structured preparation has explicit inputs

Preparation SHALL consume the resolved session state, selected resource revisions, declared field mappings and take choices. It SHALL separate creative decisions, descriptive fields and any optional prose synthesis. Required descriptive fields SHALL be consumed strictly from authorized English translations in the translation sidecar; preparation SHALL NOT fall back to the source payload for required descriptive fields even if the payload contains English text. Optional descriptive fields without translations SHALL be omitted if they contain non-English characters. Instructions in source files SHALL be treated as data and SHALL NOT execute commands or change application rules. External application binaries SHALL NOT be required for normal operation.

When automatic resource-session authoring is used, the writer request MAY additionally include a bounded authoritative authoring context derived from the current persisted plan revision. That context SHALL contain only information needed to maintain the session while varying unlocked take choices: the persisted session brief, exact scene anchor and authorized descriptive state, current take ID/ordinal/total, variation policy/resolved fixed values, and deterministic summaries of already finalized earlier takes' camera, framing, pose and expression choices. The context SHALL be input-only: writer output remains restricted to currently unlocked fields from the closed take-choice set `camera`, `framing`, `pose` and `expression`.

The configured OpenAI-compatible assistant transport MAY expose a field-preserving structured-output helper for this writer path. Resource writer output SHALL remain a structured field object through validation and SHALL NOT be flattened into an unrelated historical `{label, prompt}` representation and then reconstructed heuristically.

#### Scenario: Guidance includes an instruction
- **WHEN** a resource contains authoring guidance
- **THEN** it is passed only as bounded reference data where its mapping permits and is not blindly appended to the image description

#### Scenario: Required descriptive field lacks translation sidecar
- **WHEN** a resource take is prepared but a required descriptive field has no authorized translation in the sidecar
- **THEN** preparation is refused with a PreparationFieldError even if the source payload contains English text

#### Scenario: Optional field contains non-English characters
- **WHEN** an untranslated optional field contains non-English text
- **THEN** it is omitted from prompt clauses to avoid non-English script pollution

#### Scenario: Automatic authoring receives continuity context
- **WHEN** an incomplete take is automatically authored under a persisted session brief and scene anchor
- **THEN** the writer receives that authoritative context plus bounded prior finalized take-choice summaries
- **AND** it may return only the currently unlocked camera, framing, pose and expression fields

#### Scenario: Structured transport returns take fields
- **WHEN** the configured assistant returns structured camera, framing, pose or expression values
- **THEN** those fields reach the existing writer-output validator as fields rather than being flattened into a prose prompt and reconstructed

### Requirement: Preparation is traceable and replayable

The system SHALL save the final prompt, effective state, source revision references, field mappings, preparation version and any writer input/output needed to explain the result. Re-running a finalized take SHALL reuse its saved prompt without another writer call. Unsupported claims of exact AmazingDraw rendering parity SHALL NOT appear in the interface or documentation. When automatic authoring used persisted authoring context, the saved writer input SHALL include the exact bounded context sent to the assistant so the resulting choice can be explained without relying on external conversational state.

#### Scenario: Repeating a prepared photograph
- **WHEN** the user repeats a finalized take with another seed
- **THEN** the saved prompt remains unchanged and no source refresh or writer variation changes it

#### Scenario: Reviewing assistant-authored provenance
- **WHEN** a prepared take was filled by automatic session authoring
- **THEN** its provenance identifies the assistant synthesis and preserves the exact bounded writer input/context and validated writer output used for that take
