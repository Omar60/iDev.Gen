## MODIFIED Requirements

### Requirement: Structured preparation has explicit inputs

Preparation SHALL consume the resolved session state, selected resource revisions, declared field mappings and take choices. It SHALL separate creative decisions, descriptive fields and any optional prose synthesis. Required descriptive fields SHALL be consumed strictly from authorized English translations in the translation sidecar; preparation SHALL NOT fall back to the source payload for required descriptive fields even if the payload contains English text. Optional descriptive fields without translations SHALL be omitted if they contain non-English characters. Instructions in source files SHALL be treated as data and SHALL NOT execute commands or change application rules. External application binaries SHALL NOT be required for normal operation.

When automatic resource-session authoring is used, the writer request MAY additionally include a bounded authoritative authoring context derived from the current persisted plan revision. That context SHALL contain only information needed to maintain the session while varying unlocked take choices: the persisted session brief, exact scene anchor and authorized descriptive state, current take ID/ordinal/total, variation policy/resolved fixed values, and deterministic summaries of at most the five immediately preceding finalized takes in stable plan order.

Each prior-take summary SHALL contain only stable take ID plus camera, framing, pose and expression. Generated images, arbitrary conversation, full historical prompts and unrelated session history SHALL NOT be included. When more than five earlier finalized takes exist, truncation SHALL deterministically retain only the five immediately preceding finalized takes in plan order.

The context SHALL be input-only: writer output remains restricted to currently unlocked fields from the closed take-choice set `camera`, `framing`, `pose` and `expression`. The configured OpenAI-compatible assistant transport MAY expose a field-preserving structured-output helper for this writer path. Resource writer output SHALL remain a structured field object through validation and SHALL NOT be flattened into an unrelated historical `{label, prompt}` representation and then reconstructed heuristically.

Because the configured assistant transport is asynchronous while the existing deterministic resource writer interface is synchronous, the implementation SHALL establish an explicit orchestration boundary. That boundary MAY invoke the async assistant transport before passing structured values into existing preparation validation/finalization primitives, but it SHALL NOT duplicate provider configuration/HTTP transport or bypass existing writer-output validation merely to bridge async and sync code.

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
- **THEN** the writer receives that authoritative context plus summaries of at most the five immediately preceding finalized take choices
- **AND** it may return only the currently unlocked camera, framing, pose and expression fields

#### Scenario: More than five prior takes are available
- **WHEN** automatic authoring prepares a take with more than five finalized predecessors
- **THEN** only the five immediately preceding finalized predecessors in stable plan order are included
- **AND** no generated image, arbitrary conversation or full historical prompt is included

#### Scenario: Structured transport returns take fields
- **WHEN** the configured assistant returns structured camera, framing, pose or expression values
- **THEN** those fields reach the existing writer-output validator as fields rather than being flattened into a prose prompt and reconstructed

### Requirement: Preparation is traceable and replayable

The system SHALL save the final prompt, effective state, source revision references, field mappings, preparation version and any writer input/output needed to explain the result. Re-running a finalized take SHALL reuse its saved prompt without another writer call. Unsupported claims of exact AmazingDraw rendering parity SHALL NOT appear in the interface or documentation. When automatic authoring used persisted authoring context, the saved writer input SHALL include the exact bounded context sent to the assistant so the resulting choice can be explained without relying on external conversational state.

Assistant-established fixed variation values SHALL also be traceable before they are consumed by per-take preparation. The authoritative authoring state SHALL preserve the resolved value, `value_origin = assistant`, the exact bounded resolution input/context and the validated assistant output used to establish that fixed value. User-established fixed values SHALL record `value_origin = user` and SHALL NOT fabricate assistant provenance.

#### Scenario: Repeating a prepared photograph
- **WHEN** the user repeats a finalized take with another seed
- **THEN** the saved prompt remains unchanged and no source refresh or writer variation changes it

#### Scenario: Reviewing assistant-authored provenance
- **WHEN** a prepared take was filled by automatic session authoring
- **THEN** its provenance identifies the assistant synthesis and preserves the exact bounded writer input/context and validated writer output used for that take

#### Scenario: Reviewing assistant-established fixed state
- **WHEN** the assistant established a fixed variation value before take preparation
- **THEN** persisted authoring state identifies its assistant origin and exact resolution input/output
- **AND** later takes do not need another assistant call to explain or reconstruct that fixed value
