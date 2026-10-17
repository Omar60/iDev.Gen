# Spec Delta

## ADDED Requirements

### Requirement: Full Auto removes the complete confirmed outfit progressively

For an outfit-based look, Full Auto SHALL use the confirmed ordered inventory for its complete canonical removal arc. It SHALL start with every listed item worn, remove successive items in that order, retain the canonical final-item aside stage when present, and end with every listed garment and accessory absent. The accepted permanent appearance SHALL not retain removed-item clauses.

#### Scenario: Look save produces a profile for an ordered outfit
- **WHEN** the user authorizes Full Auto for that exact look and its eligible generated profile
- **THEN** the first take uses the complete outfit, intermediate takes follow the canonical removal stages, and the last take has no listed garment or accessory remaining
- **AND** generation does not invent a different removal order or a replacement outfit

#### Scenario: The last remaining item has an aside state
- **WHEN** that exact saved item defines moved-aside wording
- **THEN** its moved-aside stage is represented before its complete removal
- **AND** the moved-aside stage is not treated as the garment-free endpoint

#### Scenario: An accessory was described in appearance
- **WHEN** accepted ownership assigns its source clauses to that progressive accessory
- **THEN** Full Auto uses the permanent appearance projection without those clauses
- **AND** the final wardrobe and prepared prompt do not assert that accessory is still worn

#### Scenario: The baseline contradicts the empty final inventory
- **WHEN** the selected profile's baseline asserts residual worn coverage after all listed items are removed
- **THEN** existing readiness and preflight checks block creation with the actionable baseline conflict
- **AND** no session, run, generation or creation receipt is allocated

### Requirement: All removal stages fit the authorized take interval

Full Auto SHALL materialize every canonical wardrobe stage over the complete authorized take interval without skipping a removal. It SHALL reject insufficient take count before allocation. When more takes than stages exist, repeated stages SHALL preserve the same canonical remaining-inventory wording. An appearance-only look SHALL retain its existing single-stage semantics and explicit baseline review.

#### Scenario: Take count equals the stage count
- **WHEN** an eligible request contains exactly enough takes for all stages
- **THEN** each stage is represented once, from the complete outfit to the all-items-absent final stage

#### Scenario: Too few takes are requested
- **WHEN** the count is below the canonical stage count including an applicable aside stage
- **THEN** preflight returns the existing insufficient-stage-count error and required minimum without allocating work
- **AND** no stage is dropped or multiple removals compressed into one transition to make the request pass

#### Scenario: A longer session spans the same inventory
- **WHEN** the take count exceeds the stage count
- **THEN** the existing deterministic distribution preserves every ordered stage across the full interval and the last take reaches the empty-inventory endpoint

#### Scenario: A profile is saved for an appearance-only look
- **WHEN** there are no listed outfit items and baseline and inventory facts have been explicitly accepted
- **THEN** the existing constant stage is retained without invented garments, removal events or an inferred uncovered assertion

### Requirement: Removal acceptance distinguishes prompt state from image fidelity

Acceptance SHALL connect combined look/profile save to Full Auto preflight, creation, materialized wardrobes, prepared prompts and submitted prompt bytes using isolated fake providers. It SHALL verify initial, intermediate and final states, including accessory ownership. Output production SHALL not be represented as certification that rendered pixels show every removal correctly.

#### Scenario: Combined save reaches an authorized generated session
- **WHEN** an isolated journey saves a reviewed layered look with a removable accessory and runs Full Auto
- **THEN** persisted identities, all ordered wardrobe stages and submitted prompt bytes agree with the saved version and profile
- **AND** the final prompt asserts the empty inventory without a conflicting permanent garment/accessory assertion

#### Scenario: A rendered output disagrees with its final wardrobe
- **WHEN** an output retains an item contrary to its verified instructions
- **THEN** the mismatch remains visible without a visual-compliance claim or an automatic reroll
