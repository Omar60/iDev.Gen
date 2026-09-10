## Context

See proposal.md for motivation. Current room imports project selected fields into seed files; fused entries require curated decomposition. Session creation and composition expose measured-catalogue prerequisites. Existing session looks are constant and take wardrobes override a session default, but there is no general persistent ordered draft of resource selections and explicit changes.

The inspected source distribution exposes a library resolver and card preparation commands. The resolver supports exact IDs and weighted filtered selection. Preparation exposes creative decisions, descriptive fields and optional narrative synthesis. Rendering, routing and some validation are compiled extensions. This is evidence for adopting structured preparation, not for promising exact prompt parity or session continuity in that application. Source filenames and relative code references suffice in tracked documentation; private installation details do not belong here.

## Goals / Non-Goals

**Goals:** preserve complete accepted resources, make their use inspectable, provide stable session choices and explicit wardrobe continuity, and reuse the existing generation infrastructure.

**Non-Goals:** reproduce unavailable compiled algorithms; depend on the source runtime; replace the runner, workflow mapping or judge; migrate old sessions automatically; build a generic plugin framework; guarantee pixel-identical clothing from text. Neutral scene restrictions imposed solely by storage shape, compulsory decomposition or catalogue readiness are in scope for removal. The import-side refusal of prohibited content is no longer in force.

## Decisions

### 1. Complete source records plus declared consumers

Use the existing SQLite layer. Add an `asset_library` table and an immutable `asset_revision` table keyed by library ID, source ID and content digest. Store the full accepted object as JSON, keeping original strings intact; store translation and field-coverage JSON separately. Use explicit unique keys to make reimport idempotent. Persist an import report containing counts and safe identifiers, including the full original payload of accepted entries. Registry declarations select adapters rather than authorize execution of source code.

An adapter identifies entries and classifies fields. Known scene libraries, perspective scenes and auxiliary maps receive explicit handling. Accepted unfamiliar fields survive unchanged. Unknown library shapes appear in inventory as unresolved; do not guess their identity or silently omit them. Establish a coverage ledger for every discovered library before calling adoption complete. A metadata field need not become a prompt clause to count as supported, but its role must be documented. The final acceptance report distinguishes stored, usable, auxiliary and pending entries.

Alternative rejected: columns for every source property, which lose evolving structures; a single text field, which loses meaning; raw archival without consumers, which repeats the original usability failure. JSON storage with a small explicit mapping is sufficient.

Private original-language storage is a deliberate change authorized by complete resource preservation. Code, tests, UI labels and documentation remain English. Source payloads and translations never enter tracked files. Required English fields gate readiness, not initial accepted-data storage. Existing legacy import endpoints keep their all-translations-first contract.

### 2. Preview and commit are separate operations

Add resource inventory, preview and commit operations beside the existing room import route. The preview fingerprints source files, validates shape and identity, screens content, checks translation readiness and computes revision changes. Commit rechecks fingerprints and applies accepted entries in one database transaction. Malformed/ambiguous files remain unresolved and are not partially guessed. An explicit report accounts for all entries and files, including auxiliary files and failures. Shared screening remains before any write; database failure rolls back the accepted set.

No automatic import on page load. No workflow/script execution from the external folder. Operator-selected directories stay in local configuration. File and request size limits use existing conventions and report refusal rather than truncating payloads.

### 3. Add an explicit resource-plan mode

Use a versioned `composition_mode` in session settings: absent means legacy; `resource-v1` selects the new path. Store one current draft plan with a monotonically increasing revision in a `session_plan` table linked to the existing session. The plan JSON contains stable take IDs, order, constant look, initial wardrobe, selected asset revisions and scoped wardrobe changes. Store prepared take snapshots in `prepared_take`, keyed by session, plan revision and take ID, with final prompt, effective state, mapping/compiler versions, provenance, status and an optional linked shot ID.

Stable IDs prevent reorder from attaching a preparation to the wrong take. A compare-and-swap revision check prevents stale browser saves from overwriting newer edits. A wardrobe change is attached to a take ID with `this_take` or `from_here` scope. Calculate effective state by walking the ordered draft from its initial wardrobe: persistent changes update inherited state; one-take overrides affect only that take. Never infer a progression from an outfit's garment order in this mode.

Keep appearance, place and lighting constant. Before generation, changing a constant revises and invalidates the entire draft. Once any take is queued or generated, offer a new session for a new look. Wardrobe changes remain possible through new reviewed take revisions. Finished shots and old prepared snapshots are immutable history; they are never rewritten to pretend they used the new state.

Alternative rejected: globally changing legacy wardrobe inheritance, which would change existing sessions. The new mode contains the behavior change while old tests keep their meaning.

### 4. Explicit preparation followed by a stable final prompt

Implement source mapping and effective-state resolution as deterministic backend functions. A preparation input contains only selected resources, declared guidance and effective session/take choices. It does not concatenate every source field. Optional writer synthesis fills unlocked descriptive choices; fixed identity, look and wardrobe are supplied from authoritative state, not requested anew for every take. Save writer input/output with the preparation when it is used.

For a fused scene, retain the original. If its prose conflicts with a fixed choice, show both and require a reviewed adaptation stored separately, or another resource. Do not silently split or rewrite the original and do not promise exhaustive semantic contradiction detection. Structured conflicts can be checked mechanically; free prose requires review. Unresolved placeholders block finalization. Without an assistant endpoint, manually completed descriptive fields still support deterministic preparation; assistant buttons explain their prerequisite.

Save the exact final prompt and its source revision references. Repeating a take uses that prompt, not another writer call. Review approval is tied to a plan revision. Reorder or relevant edits invalidate affected ungenerated preparations. The first implementation can conservatively invalidate the changed take and following takes for wardrobe/order edits, and all takes for changed constants; correctness precedes finer invalidation.

Use the existing verbatim mechanism for finalized text-to-image prompts so trigger, base prompt and look are not appended twice. Preserve graph-kind behavior for editing and guided workflows; an editing instruction is not a complete text-to-image prompt. The resource planner must not invent a second interpretation of reference flags.

### 5. Persist first and submit once

Save a draft before any lengthy writing operation and persist each completed take. Recovery marks interrupted work as incomplete and resumes only incomplete work. Use a uniqueness constraint linking a prepared revision to its shot so HTTP retries cannot duplicate queue entries. Reuse shot creation before enqueue and the serial runner. A saved plan is not permission to generate; generation starts from reviewed takes through the existing explicit action.

### 6. Put resource choices inside session creation

Integrate a resource browser with text/category filters, library/readiness indicators, field details and a start-session action. No image gallery is required where resources have no previews. The session editor orders decisions as character, scene and constants, take variations, review, generation. Sampler and workflow details remain accessible as advanced settings and reuse current profiles.

Each take displays inherited versus overridden clothing and the scope of changes. Offer one-take or selected-take tests before the whole session. Keep manual editing and the existing assistant. Leave measured Compose/Fill/Judge tools available under the legacy workflow, not as prerequisites for resource sessions. A missing catalogue must not block resource drafts, but missing mapped resources, unresolved conflicts or invalid workflows must remain visible before generation.

## Risks / Trade-offs

- Incomplete source internals -> declare supported field mappings from readable evidence, use invented fixtures and representative non-explicit manual examples, and label unverified behavior rather than claiming parity.
- Source refresh changes wording -> immutable revisions and prepared snapshots prevent drift; removed entries are reported without deleting history.
- Text preserves instructions, not exact garment geometry -> keep visual reference workflows available and document the distinction without promising automatic visual consistency.
- Fused prose contradicts shared choices -> review adaptations separately; never silently discard fields or prepend contradictory descriptions.
- Two preparation modes add complexity -> confine dispatch to the session mode and share persistence, queue and reference infrastructure; no rewrite of the legacy engine.
- Original private payloads contain non-English material -> keep payloads untracked and separate from English translations and UI; add isolation checks against public artifact leakage.

## Migration Plan

1. Record field coverage and source API evidence before implementing adapters; leave unsupported shapes explicitly pending. The import-side refusal of prohibited content is not retained.
2. Back up the configured database using a consistent SQLite backup. Add schema without changing existing rows; missing mode remains legacy.
3. Import accepted complete resources through preview/commit. Existing seeds remain usable by legacy sessions and are not fabricated into complete source objects.
4. Deliver resource selection, draft continuity and manual preparation end to end before optional writer synthesis. Then add synthesis and recovery without changing snapshot semantics.
5. Verify reference paths, old sessions, source refresh and retry behavior. Make resource planning the default for new sessions only after acceptance checks; keep explicit legacy entry available.
6. Rollback disables creation/preparation in the new mode while retaining new tables and source revisions. Finish or cancel active work through the existing queue. Restore a database backup only as an explicit recovery operation with awareness of post-backup work; do not run a destructive down-migration automatically.

Acceptance demonstration: import an accepted invented resource set, prepare twelve portraits of one fictional adult character with constant outfit and varied cameras/poses, close and reopen after three prepared takes, then add a jacket from take seven. Verify inherited state, prompt provenance, unchanged completed preparations where valid, explicit invalidation where needed, retry idempotency and unchanged legacy prompts. Automated tests use no GPU or network. A separate optional manual render checks visual results without treating them as guaranteed by text equality.
