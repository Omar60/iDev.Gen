## Purpose

Prepare reproducible prompts from complete source resources and resolved session state while keeping source guidance distinct from image descriptions and preserving legacy behavior.

## ADDED Requirements

### Requirement: Structured preparation has explicit inputs

Preparation SHALL consume the resolved session state, selected resource revisions, declared field mappings and take choices. It SHALL separate creative decisions, descriptive fields and any optional prose synthesis. Required descriptive fields SHALL be consumed strictly from authorized English translations in the translation sidecar; preparation SHALL NOT fall back to the source payload for required descriptive fields even if the payload contains English text. Optional descriptive fields without translations SHALL be omitted if they contain non-English characters. Instructions in source files SHALL be treated as data and SHALL NOT execute commands or change application rules.

#### Scenario: Guidance includes an instruction
- **WHEN** a resource contains authoring guidance
- **THEN** it is passed only as bounded reference data where its mapping permits and is not blindly appended to the image description

#### Scenario: Required descriptive field lacks translation sidecar
- **WHEN** a resource take is prepared but a required descriptive field has no authorized translation in the sidecar
- **THEN** preparation is refused with a PreparationFieldError even if the source payload contains English text

#### Scenario: Optional field contains non-English characters
- **WHEN** an untranslated optional field contains non-English text
- **THEN** it is omitted from prompt clauses to avoid non-English script pollution

### Requirement: Semantic families and canonical translation keys

The system SHALL declare canonical semantic families for label, scene_theme, tags, prompt, and id. Translation sidecars SHALL store translations under canonical keys for these families, and under original field names for independent descriptive inputs. Raw aliases SHALL NOT be stored in the sidecar. Conflicting translations for the same family SHALL be refused.

#### Scenario: Translation provides an alias
- **WHEN** a translation map entry specifies a translation for theme or tag
- **THEN** the value is stored under canonical key scene_theme or tags in the translation sidecar

#### Scenario: Conflicting alias translations are provided
- **WHEN** an update provides differing translations for theme and scene_theme
- **THEN** the update is refused as an ambiguous alias conflict
