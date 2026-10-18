# Spec Delta

## ADDED Requirements

### Requirement: Look automation proposals describe exact pending content

The system SHALL prepare editable automation-profile proposals for a pending principal look without saving a look or publishing a profile. Proposals SHALL identify exact appearance, ordered garment definitions and worn/aside states, with unresolved facts represented explicitly. Assistant suggestions SHALL remain unaccepted until the user reviews them.

#### Scenario: Photo extraction supplies an automation candidate
- **WHEN** a user explicitly extracts a look from a staged photo through the integrated editor
- **THEN** its appearance and garment proposal is accompanied by editable proposed automation facts
- **AND** no look version, accepted profile or session is created by extraction

#### Scenario: A manually authored look requests assistance
- **WHEN** the user requests automation suggestions for the current look form
- **THEN** suggestions bind that form's exact appearance and ordered garment definitions
- **AND** the assistant cannot reorder the confirmed removal sequence or accept its own suggestions

#### Scenario: Provider cannot establish a fact
- **WHEN** coverage or ownership is uncertain or the provider returns invalid automation output
- **THEN** the editor retains the ordinary look content and identifies missing automation facts
- **AND** it does not substitute an accepted uncovered state, complete inventory or permanent ownership

### Requirement: Integrated review accepts automation facts once

The look review SHALL allow correction and explicit acceptance of appearance ownership, worn/aside coverage, inventory completeness and inventory-absent baseline alongside the garment removal order. Full Auto readiness SHALL use these accepted facts and existing validation. Unknown or contradictory facts SHALL remain visible blockers rather than becoming automatic assertions.

#### Scenario: Complete look and profile are reviewed together
- **WHEN** the user confirms all required structured facts and the ordered inventory
- **THEN** one reviewed save can create the principal look and an eligible profile without a separate profile-editor publication

#### Scenario: Inventory contains a removable accessory
- **WHEN** an accessory appears in the appearance description and the removal inventory
- **THEN** review allows assigning its exact appearance clauses to that accessory
- **AND** acceptance excludes those clauses from permanent appearance while retaining their original source text

#### Scenario: Complete removal is confirmed
- **WHEN** a user accepts an outfit-based automation profile
- **THEN** review identifies every removable garment and accessory in the confirmed inventory and explicitly confirms the baseline after all listed items are absent
- **AND** a covering baseline or conflicting permanent garment clause prevents Full Auto readiness

#### Scenario: Facts remain incomplete
- **WHEN** ownership or applicable coverage remains unknown or inventory completeness is unconfirmed
- **THEN** the editor identifies the missing reusable facts and keeps manual-only saving available
- **AND** saving the ordinary look does not claim Full Auto readiness

### Requirement: Reviewed look and profile save atomically

A combined reviewed save SHALL commit the principal look version, its first immutable automation-profile revision, accepted evidence and save receipt as one operation. The profile SHALL bind the actual saved look key, version, content digest and garment identities in their confirmed order. Validation or persistence failure SHALL leave no partial combined-save records.

#### Scenario: New garments receive persistent keys
- **WHEN** a reviewed combined save allocates previously unsaved garment identities
- **THEN** the profile references those exact saved identities in the confirmed order and revision 1 belongs to that exact look version
- **AND** the response identifies both stored versions and their authoritative readiness

#### Scenario: Profile publication fails after look allocation
- **WHEN** any profile, evidence or receipt write fails during the combined operation
- **THEN** all look, garment, outfit, profile, evidence and receipt writes from that operation roll back
- **AND** a staged source photo remains available for the existing retry lifecycle

#### Scenario: A later look version is saved
- **WHEN** a reviewed combined save creates a new immutable version of an existing look
- **THEN** that new version receives its own profile revision 1
- **AND** previous versions, profiles and session bindings remain unchanged

### Requirement: Combined look saves replay their exact result

A combined save SHALL bind a client request identity to its complete normalized source, reviewed content, target and acceptance. Identical retries SHALL return the exact stored look/profile result without another allocation. Reuse for changed content SHALL fail with an idempotency conflict. Concurrent identical requests SHALL commit one result.

#### Scenario: The browser loses the successful response
- **WHEN** a client repeats an identical combined save after commit
- **THEN** it receives the stored look/profile identities and response without another garment, outfit, look version or profile revision

#### Scenario: Two tabs submit one save identity
- **WHEN** identical combined requests race
- **THEN** one result is committed and both callers resolve to that same result

#### Scenario: A request identity is reused after an edit
- **WHEN** the same request identity carries different appearance, name, garment order, target or accepted profile facts
- **THEN** the save returns an idempotency conflict without writes

#### Scenario: A saved photo has already been cleaned
- **WHEN** an identical committed photo-derived save is retried after staged bytes have been deleted
- **THEN** its durable receipt returns the original combined result without reopening the image

### Requirement: Look edits invalidate stale automation acceptance

Automation acceptance SHALL bind exact appearance and ordered garment wording/states. Changes to those inputs SHALL invalidate pending proposals and confirmations. Combined save SHALL reject stale content, target versions or photo proposals before persistence. Late responses from superseded editor states SHALL not restore stale acceptance.

#### Scenario: Clothing order changes after review
- **WHEN** the user reorders, adds or removes an item after accepting automation facts
- **THEN** the integrated review requires fresh acceptance for the current order and identity mapping
- **AND** a stale combined save writes no records

#### Scenario: An appearance edit shifts Unicode spans
- **WHEN** source appearance changes after ownership review
- **THEN** exact Unicode-scalar ownership must be revalidated against the new text without normalization

#### Scenario: A newer photo proposal supersedes the reviewed one
- **WHEN** combined save references a superseded or different photo-stage proposal
- **THEN** it is refused without allocating a look or profile

### Requirement: Integrated profile evidence preserves accepted meaning

Combined-save evidence SHALL retain the exact reviewed content, structured suggestions when used, user corrections and acceptance bound to the saved identities. The server SHALL construct accepted provenance from the reviewed facts rather than trusting provider approval or arbitrary client provenance. Photo evidence SHALL remain redacted, and portable look-v1 SHALL remain unchanged.

#### Scenario: A suggestion is corrected before saving
- **WHEN** a user edits an assistant coverage suggestion and accepts the reviewed profile
- **THEN** the accepted fact matches the correction, its destination look digest and profile revision
- **AND** suggestion and reviewed content remain distinguishable in local evidence

#### Scenario: An unreviewed provider labels output accepted
- **WHEN** provider output or request content claims approval without the required reviewed acceptance
- **THEN** it cannot publish an accepted automation profile or establish Full Auto readiness

#### Scenario: A photo-derived look is exported
- **WHEN** the saved combined result is exported as portable look-v1
- **THEN** the existing closed export excludes the automation profile, review evidence, image bytes, credentials and private paths

### Requirement: Legacy look operations retain ordinary compatibility

Existing look-only API requests, portable imports and historical versions SHALL retain their current behavior. Integrated automation preparation SHALL offer manual input without an assistant. Missing automation facts SHALL not invalidate ordinary look use. Existing stored looks SHALL not receive inferred profiles or change historical session bindings automatically.

#### Scenario: Assistance is not configured
- **WHEN** a user authors a principal look without an assistant
- **THEN** manual look saving and manual structured automation review remain available
- **AND** missing facts are not guessed to make the look eligible

#### Scenario: An old look or portable import has no profile
- **WHEN** its existing version is read, imported or used manually
- **THEN** it remains compatible without implicit profile creation or a historical rewrite

#### Scenario: Resource planning is disabled
- **WHEN** a combined save or automation suggestion is requested while the feature is disabled
- **THEN** the request is refused before provider dispatch or look/profile writes
