## ADDED Requirements

### Requirement: Wardrobe coverage is user-authored prompt detail

A resource-v1 take MAY include optional `wardrobe_coverage` text. The system SHALL preserve it as user-authored data and SHALL NOT infer body areas from the wardrobe, take, or selected resources. For non-blank coverage, text-to-image and guided positive prompts SHALL include it exactly once after the resolved effective wardrobe and before take clauses. Reference-edit prompts SHALL omit coverage with the other session context. Absent or blank coverage SHALL leave the effective prompt and state unchanged. Coverage SHALL NOT be represented as a guarantee of rendered pixels or exact visual anatomy.

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
