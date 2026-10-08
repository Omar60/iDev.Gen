## ADDED Requirements

### Requirement: Guided canvas override extends the closed creation request

Superseding the exact field list in `simplify-resource-session-workflow/design.md` §7, `POST /api/sessions/guided` SHALL retain all established accepted keys and add only optional paired `width` and `height` dimensions; all other unknown keys remain rejected. If supplied, both SHALL be strict JSON integers, at least 8 and divisible by 8; a partial pair, null, coercible value, zero, or negative value SHALL be rejected before writes. If omitted, the new session SHALL inherit the character's current effective dimensions. Supplied values SHALL be stored only in the new session settings and SHALL NOT mutate character settings.

Explicitly supplied dimensions SHALL participate in the normalized request digest used by guided `request_id` replay. When both are omitted, normalization SHALL preserve the established pre-override digest shape. The same request ID and normalized body SHALL return the stored response; reuse with different normalized content, including different dimensions, SHALL return `409 idempotency_conflict` without writes.

The guided UI SHALL leave the override disabled by default. When enabled, its initial custom dimensions SHALL be 768×1360. These are input defaults, not a server canvas default or a rendering guarantee.

#### Scenario: Canvas dimensions are omitted
- **WHEN** guided creation omits both canvas dimensions
- **THEN** the session inherits the character's current effective dimensions without changing the character

#### Scenario: Canvas override is valid
- **WHEN** guided creation supplies width and height as positive integer multiples of 8
- **THEN** the new session stores that pair without changing character settings
- **AND** the supplied pair participates in request-ID replay identity

#### Scenario: Canvas override is partial or invalid
- **WHEN** guided creation supplies only one dimension or either value is not a positive integer multiple of 8
- **THEN** it returns a validation error before creating a request record or session

#### Scenario: Canvas retry changes dimensions
- **WHEN** an existing request ID is retried with different width or height
- **THEN** creation returns `409 idempotency_conflict` and writes nothing

### Requirement: Per-take wardrobe coverage is user-authored plan state

A resource-v1 take MAY store optional `wardrobe_coverage` as a string of at most 2,000 characters without disallowed control characters. The value SHALL remain attached to its stable take ID through reorder and save normalization. The application SHALL NOT infer body areas from the wardrobe, take, or selected resources, and automatic writers SHALL NOT request or replace this user-owned field. Missing or blank coverage SHALL preserve the existing effective-state shape.

Plan edits SHALL use the existing expected-revision compare-and-swap. Changing coverage SHALL invalidate preparation for the affected ungenerated take and SHALL invalidate downstream automatic work when its dependencies require it. Unaffected verified snapshots MAY copy forward. Generated snapshots and linked shots SHALL remain unchanged as historical records.

#### Scenario: Coverage follows its take
- **WHEN** a take with coverage is reordered and the plan is saved
- **THEN** the coverage remains attached to that take's stable ID

#### Scenario: Coverage changes after preparation
- **WHEN** coverage changes in a new plan revision
- **THEN** affected ungenerated preparation is invalidated and unrelated verified snapshots may copy forward
- **AND** existing generated snapshots and linked shots remain unchanged

#### Scenario: Coverage is absent or blank
- **WHEN** a take has no coverage or only whitespace coverage
- **THEN** no coverage value is added to effective state
