# Spec Delta

## ADDED Requirements

### Requirement: Principal look review includes automation preparation

Looks SHALL integrate automation-profile preparation into the principal look editor for new manual looks, photo descriptions, outfit-to-look conversion and new look versions. Users SHALL review appearance ownership, ordered garments, coverage, baseline and inventory in that surface. A complete reviewed Save SHALL create the look and its profile without a second editor or Publish action.

#### Scenario: A reviewed photo look is saved for Full Auto
- **WHEN** extraction and the integrated automation review are complete
- **THEN** one Save action creates the principal look and profile revision 1
- **AND** the success view shows the saved look version and Full Auto readiness

#### Scenario: A manually entered look is saved with automation facts
- **WHEN** a user enters the required structured facts manually in the same look editor
- **THEN** the same combined save is available without an assistant or a separate profile publication

#### Scenario: An imported outfit becomes a named look
- **WHEN** the user starts a look from an imported outfit
- **THEN** its ordered items enter the integrated review before the named look and profile are saved
- **AND** importing the outfit itself creates neither a named look nor an accepted automation profile

### Requirement: Incomplete automation retains an explicit normal save

The integrated editor SHALL show missing automation facts and preserve an explicit normal-only save when assistance fails or review is incomplete. It SHALL distinguish saved-but-ineligible looks from automation-ready results. It SHALL not mark unknown facts accepted, silently drop an invalid combined request into a normal save, or require automation setup for ordinary look use.

#### Scenario: A profile proposal fails
- **WHEN** an assistant request fails after the look description is available
- **THEN** the current look edits remain and the user can complete facts manually or save only the ordinary look

#### Scenario: Combined save is rejected
- **WHEN** the server rejects the requested look/profile save
- **THEN** the form retains its edits and shows the actual error
- **AND** no success notice claims a saved ordinary look or an eligible profile

### Requirement: Exact unambiguous profiles are selected visibly

For a newly selected exact look version during guided creation, the UI SHALL automatically select its sole ready profile revision when no choice exists yet, showing the exact identity and readiness before consent. Multiple ready revisions SHALL require a visible choice. Refresh SHALL not replace an existing choice with latest, change a historical session binding or silently accept changed consent.

#### Scenario: A newly saved look has one ready profile
- **WHEN** the user selects its exact version for Full Auto
- **THEN** the exact ready revision is selected and shown without requiring a redundant empty-selector choice
- **AND** preflight and creation send that verified revision and digest

#### Scenario: Multiple ready revisions exist
- **WHEN** the selected version has more than one ready profile and no retained selection
- **THEN** the UI asks which exact revision to use and does not choose the latest silently

#### Scenario: No ready revision exists
- **WHEN** profile discovery finds no eligible revision
- **THEN** the UI displays the missing-profile or readiness diagnostics and a correction action
- **AND** normal creation remains available

#### Scenario: An earlier discovery response arrives
- **WHEN** the user changes the selected look version before a profile-list response arrives
- **THEN** that response cannot select a profile for the newer selection or enable its consent action

#### Scenario: The selected profile changes before authorization
- **WHEN** a visible selection or its verified identity changes after preflight
- **THEN** the old consent summary is invalidated and the next generation requires the current exact summary and explicit authorization

### Requirement: The removal destination is visible before generation

Full Auto SHALL show the confirmed ordered removal stages, intermediate states, final empty inventory and minimum take count before authorization. The user SHALL see that clothing and accessories are progressively removed through the complete inventory. A profile save SHALL not start a session or generate images. Output fidelity SHALL remain an explicitly stated limitation.

#### Scenario: Ordered clothing includes a final moved-aside state
- **WHEN** the user inspects the Full Auto progression for that look
- **THEN** the timeline shows all canonical stages, including the moved-aside state and the later all-items-absent final stage
- **AND** insufficient take count has a readable correction instead of skipping stages

#### Scenario: A look and ready profile are saved
- **WHEN** combined save succeeds
- **THEN** neither preparation nor generation starts until the existing session creation and Generate without pauses authorization occur
