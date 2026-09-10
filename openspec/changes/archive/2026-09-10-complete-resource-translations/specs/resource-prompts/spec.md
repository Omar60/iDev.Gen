## Purpose

Prepare reproducible prompts from complete source resources and resolved session state while keeping source guidance distinct from image descriptions and preserving legacy behavior.

## MODIFIED Requirements

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

## ADDED Requirements

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
