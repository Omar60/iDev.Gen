# session-plan Specification

## Purpose
Let a user prepare and resume a photographic session whose shared creative choices persist across ordered takes and change only through explicit decisions.

## Requirements

### Requirement: Session choices precede take variations

A resource session SHALL bind its character to the selected model and store a constant look, initial wardrobe and source selections. Source identity suggestions SHALL NOT replace the selected character. Each take SHALL expose its camera, framing, pose and expression choices and its effective wardrobe. Shared appearance, place and light SHALL remain constant within the session; changing those constants SHALL require a new session or an explicit revision before any take is generated.

#### Scenario: A resource suggests another character or outfit
- **WHEN** a selected source contradicts session identity or clothing
- **THEN** the conflict is shown before preparation and neither choice is silently overwritten

### Requirement: Wardrobe changes have explicit scope

The default SHALL keep the initial wardrobe constant for every take. An explicit wardrobe change SHALL declare either this take only or this take and following takes. Persistent changes SHALL apply in take order until superseded; a one-take override SHALL NOT modify the inherited state of following takes. A resource selection or assistant suggestion SHALL NOT create an implicit change.

#### Scenario: Adding a jacket midway
- **WHEN** a user adds a jacket at take seven with following-takes scope
- **THEN** takes one through six retain their previous wardrobe and take seven onward inherits the jacket until another explicit change

#### Scenario: One-take override
- **WHEN** take four has a one-take wardrobe override
- **THEN** take five inherits the persistent wardrobe state rather than take four's override

### Requirement: Draft preparation survives interruption

The system SHALL persist the draft before long preparation begins and save completed take preparation incrementally. Reopening a draft SHALL recover completed work and identify incomplete work without regenerating completed takes automatically. Saving failures SHALL be visible.

#### Scenario: Browser closes during preparation
- **WHEN** the browser closes after three of twelve takes have been prepared
- **THEN** reopening restores those three results and offers preparation of the remaining takes

### Requirement: Review binds generation to a plan revision

The system SHALL show effective state, selected resource revisions, conflicts and final prompt for every prepared take. Editing or reordering a draft SHALL invalidate affected prepared takes. Generated or queued takes SHALL retain their snapshots; changing their plan SHALL require an explicit new revision. Submission SHALL refuse stale preparation and duplicate submission of the same take revision.

#### Scenario: Reordering across a wardrobe change
- **WHEN** an ungenerated take moves across a persistent wardrobe change
- **THEN** its effective state is recalculated and affected prompts require preparation and review again

#### Scenario: Retrying submission
- **WHEN** the same prepared take revision is submitted twice after a connection interruption
- **THEN** it produces one shot row rather than two generations

### Requirement: Creative variety is independent of measurement

Resource sessions SHALL allow a repeated camera or pose when deliberately selected. They SHALL not require measured cells or catalogue imports to save a draft or prepare independently specified takes. The interface SHALL offer a small test selection before generating all reviewed takes.

#### Scenario: Twelve portraits with one camera
- **WHEN** twelve takes intentionally share one camera and vary expressions or poses
- **THEN** preparation is allowed without a measured-catalogue uniqueness refusal
