## Purpose

Prepare reproducible prompts from complete source resources and resolved session state while keeping source guidance distinct from image descriptions and preserving legacy behavior.

## ADDED Requirements

### Requirement: Structured preparation has explicit inputs

Preparation SHALL consume the resolved session state, selected resource revisions, declared field mappings and take choices. It SHALL separate creative decisions, descriptive fields and any optional prose synthesis. Instructions in source files SHALL be treated as data and SHALL NOT execute commands or change application rules. External application binaries SHALL NOT be required for normal operation.

#### Scenario: Guidance includes an instruction
- **WHEN** a resource contains authoring guidance
- **THEN** it is passed only as bounded reference data where its mapping permits and is not blindly appended to the image description

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
