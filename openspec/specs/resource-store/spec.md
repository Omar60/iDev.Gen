# resource-store Specification

## Purpose
Preserve complete accepted source resources locally and expose their readiness and provenance so sessions can use them without reducing them to the legacy room schema.

## Requirements

### Requirement: Complete accepted entries are retained

The system SHALL retain every field, nested value, original string and source identifier of an accepted entry in its local database. It SHALL keep the original payload separate from translations and derived values. Auxiliary maps SHALL be stored as auxiliary resources, not interpreted as scenes. Refused content SHALL be persisted as imported and SHALL appear in the import report.

#### Scenario: A nested field has no current consumer
- **WHEN** an accepted resource includes an unfamiliar nested field
- **THEN** its value survives a database round trip and is reported as retained but unused

#### Scenario: A fused resource is accepted
- **WHEN** an accepted entry combines scene and camera descriptions
- **THEN** its complete original object is retained without requiring decomposition

### Requirement: Import accounts for every input

The system SHALL preview each discovered file and account for its entries as new, unchanged, updated or unresolved. Readiness SHALL be reported separately from import outcome. It SHALL identify duplicate identifiers, unsupported shapes, malformed input and missing translations before committing. A commit SHALL bind to the previewed source fingerprints and be atomic for its accepted set.

#### Scenario: Source changes after preview
- **WHEN** a source file differs from its previewed fingerprint
- **THEN** commit is refused and requires a fresh preview without changing stored resources

#### Scenario: A file cannot be interpreted safely
- **WHEN** a file has an unsupported shape or ambiguous identifiers
- **THEN** it appears as unresolved with a reason and is not silently ignored or guessed into a scene

### Requirement: Resource revisions are reproducible

The system SHALL use library identity and source entry identity together and retain immutable accepted revisions. Reimporting identical content SHALL create no duplicate revision. A changed source SHALL create a new revision without changing existing session snapshots or measured evidence. Missing source entries SHALL be reported and SHALL NOT silently delete existing data.

#### Scenario: A source entry changes
- **WHEN** the same library and identifier are reimported with different content
- **THEN** new selections can use the new revision while previously prepared takes retain their original inputs

### Requirement: Field coverage and readiness are inspectable

The system SHALL classify source fields as descriptive input, selection metadata, writer guidance, auxiliary data or unused data. It SHALL distinguish importable resources from resources ready for preparation. An unmapped or untranslated field required for preparation SHALL block that preparation with a field-specific reason. Keys in pending_fields SHALL be canonical field names, with any source alias recorded in reason and field metadata. Optional unused fields SHALL remain visible without entering the prompt automatically. Stored revisions with invalid persisted translation sidecars SHALL remain inspectable with pending status and diagnostic sidecar_error metadata.

#### Scenario: Translation is incomplete
- **WHEN** an accepted source is stored but its required display or preparation fields lack English translations in the translation sidecar
- **THEN** it remains inspectable by identifier with a pending status and cannot be silently treated as generation-ready

#### Scenario: An alias is used in the source payload
- **WHEN** an accepted source payload uses an alias such as theme or name without an authorized translation
- **THEN** pending_fields records the canonical key scene_theme or label and the reason specifies source_field

#### Scenario: Invalid persisted translation sidecar remains inspectable
- **WHEN** a stored revision has a malformed or unauthorized translation sidecar
- **THEN** readiness evaluation returns pending status with coverage.sidecar_error diagnostic information without throwing an exception

#### Scenario: Invalid optional translation entry forces pending
- **WHEN** a stored revision has an invalid translation entry for an optional field
- **THEN** readiness evaluation returns pending status due to contract violation

### Requirement: Translation map input format and role authorization

The system SHALL accept translation maps as direct dictionaries or lists of translation entries, rejecting duplicate source strings in list form without requiring internal envelopes. Literal source keys "items" and "translation_map" SHALL remain valid source strings. Translation maps SHALL be authorized strictly against source-backed fields with role ROLE_DESCRIPTIVE_INPUT for the target kind; unauthorized target fields SHALL be rejected upfront.

#### Scenario: List-form translation map contains duplicate sources
- **WHEN** a translation map provided as a list contains duplicate source strings
- **THEN** normalization is rejected upfront with a ValueError

#### Scenario: Translation map targets non-descriptive field
- **WHEN** a translation map targets a field whose role is not ROLE_DESCRIPTIVE_INPUT
- **THEN** preview is rejected with a client error naming the disallowed field

### Requirement: Translation map application, attestation, and TOCTOU protection

The system SHALL support previewing and applying translation maps to stored resource libraries. Preview SHALL be strictly read-only and SHALL produce an HMAC-SHA256 attestation token signed by a database-directory-scoped private key (.resource-translation-preview-key), binding library metadata, map digest, and raw sidecar fingerprints (excluding coverage). Apply SHALL verify the token, library existence, and library fingerprint against TOCTOU drift inside a transaction under BEGIN IMMEDIATE, execute validated merge updates into the translation sidecar, repair stale coverage without mutating unchanged translation counts, and suppress redundant SQL writes. If the target library is deleted or modified after preview, apply SHALL fail with HTTP 409 Conflict.

#### Scenario: Applying a valid translation map
- **WHEN** an operator applies a translation map matching source strings in a library
- **THEN** canonical translations are merged into asset_revision.translation and affected revisions become ready

#### Scenario: Target library is deleted after preview
- **WHEN** a target library is deleted between preview token issuance and apply execution
- **THEN** apply is rejected with an HTTP 409 Conflict error

#### Scenario: Source library state drifts between preview and apply
- **WHEN** library revisions are modified after preview issuance
- **THEN** apply is rejected with a conflict error requiring a fresh preview

#### Scenario: Translation map is invalid
- **WHEN** a translation map contains non-English translations or empty fields
- **THEN** the entire operation is rejected with zero writes to stored translations or coverage

### Requirement: Browser import binds selected bytes and explicit identity

Browser import SHALL preserve exact selected source bytes and use the canonical import accounting, fingerprint, attestation and atomic accepted-set semantics. It SHALL expose opaque identifiers instead of private staging paths. It SHALL enforce 20 files per selection, 10 MiB per file, 50 MiB aggregate per selection and 24-hour staging expiry. A changed selection, target or expired/missing/mismatched staged input SHALL require a fresh preview.

A valid source library declaration SHALL provide the default target with its exact spelling. A declared key SHALL contain 1-128 characters without surrounding whitespace, ASCII controls, slash or backslash and SHALL NOT equal dot or dot-dot. Filenames SHALL NOT determine identity. Supported non-envelope inputs and invalid/missing declarations SHALL offer explicit compatibility targeting of an existing library or a valid new key instead of requiring source editing.

An existing exact target key SHALL remain authoritative. If no exact-key target exists but an exact source-ID/content-digest pair occurs in another library, preview SHALL require an explicit existing-target or separate-library choice. The system SHALL NOT silently merge, rename or duplicate that candidate. Declared and effective identities SHALL be shown and bound to preview. Existing path-based API/CLI semantics SHALL remain unchanged.

#### Scenario: Selected bytes change after preview
- **WHEN** staged bytes or target identity differ from a preview
- **THEN** commit refuses without changing accepted resources

#### Scenario: File or aggregate limits are exceeded
- **WHEN** individual uploads together exceed any selection limit
- **THEN** the selection is refused before expensive parsing or accepted-resource persistence

#### Scenario: Staged input expires
- **WHEN** a commit references missing or expired staging
- **THEN** the user must select or preview fresh content
- **AND** private server paths are not disclosed

#### Scenario: Source declares a valid new library
- **WHEN** a source declares a valid key with no exact-key target or historical exact-revision overlap
- **THEN** that declared key is the normal target without typed identity input

#### Scenario: Historical import used another key
- **WHEN** the declared target is new but source identity and digest overlap a differently keyed stored library
- **THEN** preview requests an explicit target choice
- **AND** selecting the historical library preserves its identity and immutable revisions

#### Scenario: Supported source lacks an envelope
- **WHEN** a supported list, individual entry or auxiliary map has no usable declaration
- **THEN** selected-byte import offers explicit compatibility targeting without editing JSON
- **AND** auxiliary data is not interpreted as a scene

#### Scenario: Two files target one library
- **WHEN** multiple files resolve to the same target key
- **THEN** canonical duplicate and content accounting applies without invented suffixes

### Requirement: Translation proposals do not bypass reviewed application

The system SHALL offer editable source-backed translation rows and optional assistant proposals for authorized descriptive fields. Proposals SHALL preserve canonical keys, scalar/list contracts and list order, and SHALL NOT write sidecars. Already-English required source strings SHALL still require explicitly accepted sidecar entries. Users SHALL preview and explicitly apply selected map content or reviewed rows through existing authorization, map-digest and library-fingerprint checks.

Direct-content translation requests SHALL be limited to 10 MiB; assistant proposal actions SHALL cover at most twenty source entries. Unsolicited or malformed proposals SHALL be refused. A library or selection change SHALL invalidate pending proposal/preview results. Unsupported mappings and invalid source structures SHALL remain correction issues, not be guessed into translations. No assistant SHALL be required for manual rows or selected maps.

#### Scenario: Imported English source lacks required sidecars
- **WHEN** required English source strings have no authorized translations
- **THEN** the resource remains pending until explicit identity translations or other valid translations are reviewed and applied

#### Scenario: Assistant proposes translations
- **WHEN** valid assistant suggestions are returned
- **THEN** users can edit and preview them without any sidecar write
- **AND** only explicit application persists authorized translations

#### Scenario: No translation map or assistant is available
- **WHEN** a user opens an entry with missing required translations
- **THEN** editable source-backed rows provide a complete manual preview/apply path

#### Scenario: Proposal or map becomes stale
- **WHEN** source/library state or selected content changes after a proposal or preview
- **THEN** stale results cannot be applied as current

#### Scenario: Unsolicited translation field
- **WHEN** a proposal targets a field not authorized for the source
- **THEN** preview/application rejects it with no translation writes

#### Scenario: Translation input exceeds bounds
- **WHEN** direct content exceeds 10 MiB or a proposal action exceeds twenty entries
- **THEN** the request is refused with a readable limit error

### Requirement: Personal looks preserve reusable appearance and garment definitions

The system SHALL let users create named looks through forms, import/export versioned JSON, and save reviewed photo-derived proposals. A look SHALL separate constant appearance from an optional ordered garment outfit. Garments SHALL have stable identity, worn wording and optional moved-aside wording. Removal order SHALL be explicitly authored, not inferred from a photograph or assistant output. One-piece garments, layers and removable accessories SHALL be supported without mandatory rigid body-part slots.

Personal looks SHALL remain separate from immutable mined resources. Editing a saved look SHALL create a new immutable version or record and SHALL NOT reword existing legacy garment/outfit keys. Imported references and duplicate keys SHALL be prevalidated before atomic persistence. Identical JSON re-import SHALL be a no-op; conflicting content SHALL require explicit new-version or save-copy resolution. Existing garment/outfit JSON SHALL be accepted as an outfit-only input. JSON requests SHALL be limited to 10 MiB and exported content SHALL exclude photos, private paths, credentials and session data.

#### Scenario: Look is created manually
- **WHEN** a user names a look, enters appearance and adds garments in an explicit order
- **THEN** it is reusable without entering technical keys or editing external JSON

#### Scenario: Look is imported twice
- **WHEN** identical portable JSON is imported twice
- **THEN** the second import creates no duplicate
- **AND** changed content under the same identity requires explicit conflict resolution

#### Scenario: Imported outfit has an unknown garment
- **WHEN** a reference is absent from both the import and existing store
- **THEN** the whole import is refused before writes

#### Scenario: Existing look is edited
- **WHEN** a used look is saved with different garment wording
- **THEN** a new version or record is created without mutating prior versions or legacy keys

### Requirement: Saved look archival preserves stored history

The system SHALL archive a saved-look identity without deleting its immutable versions or associated records. Archived looks SHALL be omitted from active saved-look listings.

#### Scenario: Archive a look with multiple versions
- **WHEN** a user archives a saved look
- **THEN** the look no longer appears in active look listings
- **AND** all versions, profiles, photo evidence, import receipts, and session snapshots remain unchanged

### Requirement: Archived look history remains readable

Exact reads and exports of archived versions SHALL remain available. Read-only receipt replays SHALL remain valid when they perform no writes.

#### Scenario: Read archived history
- **WHEN** a client requests an exact archived version or its export
- **THEN** the same immutable historical content remains available

### Requirement: Archived look writes are refused

Version creation, profile publication or copy targets, and imports writing to an archived identity SHALL fail with `409 look_archived` and SHALL NOT write look, profile, catalogue, or receipt data.

#### Scenario: Write to an archived look
- **WHEN** a request attempts a new version, profile write, or import under an archived identity
- **THEN** it returns `409 look_archived` without writes

### Requirement: Saved look archive requests are stable

Repeated archive requests for an existing look SHALL succeed. Archiving an unknown identity SHALL return `404 look_not_found`.

#### Scenario: Retry archiving an existing look
- **WHEN** a user archives the same existing look more than once
- **THEN** each request succeeds without creating duplicate archive records

#### Scenario: Archive an unknown look
- **WHEN** a user archives a key with no saved versions
- **THEN** the request returns `404 look_not_found` and stores no archive record

### Requirement: Photo look extraction is an explicitly reviewed proposal

The system SHALL accept one browser-selected JPEG, PNG or WebP look image per operation, up to 10 MiB file bytes and 25 megapixels decoded. It SHALL validate actual image content, stage it privately behind opaque identifiers, and require an explicit extraction action before sending it to the configured vision assistant. Extraction SHALL be considered available only when the normal assistant endpoint/model are configured and a non-empty `llm_vision_model` has been selected from verified discovery or explicitly declared by the local operator. A detected visual text model SHALL be stored explicitly as that vision model. Extraction SHALL never fall back from an empty vision-model setting to the text-only model. It SHALL NOT infer person identity, unseen garments, background, camera or pose as accepted look content.

Extraction SHALL produce editable visible appearance/garment proposals with unresolved details identified. Users SHALL confirm removal order and review or edit content before saving. Failed extraction or invalid output SHALL save no look. An empty vision-model setting SHALL return `409 vision_unavailable` before image transmission and offer Configure vision or manual entry. Provider image refusal or invalid visual output SHALL return `502 vision_request_failed`, save no look and retain preview until cancel/expiry for retry.

Saved provenance SHALL retain the source digest, assistant request/output and user edits without embedding image bytes in ordinary metadata or portable JSON. Temporary images SHALL expire after 24 hours and be cleaned after save/cancel when no longer needed. Saved looks SHALL remain usable without the original image or another assistant call. Look images SHALL NOT automatically become generation references.

Startup and each photo-stage access SHALL expire overdue records and retry eligible cleanup. Save/cancel SHALL remove bytes only after saved redacted provenance or cancellation is durable. Cleanup failure SHALL remain a visible retryable warning and SHALL NOT undo a saved look.

#### Scenario: Image is selected
- **WHEN** a photo is selected but Extract look has not been invoked
- **THEN** no assistant request or saved-look mutation occurs

#### Scenario: Image proposal needs correction
- **WHEN** extraction returns uncertain garments or incomplete details
- **THEN** the user can correct or explicitly omit them and confirm removal order before saving

#### Scenario: Vision assistant is unavailable
- **WHEN** the configured assistant cannot process the image
- **THEN** extraction fails visibly without saving
- **AND** photo preview and manual creation remain available

#### Scenario: Only a text model is configured
- **WHEN** the assistant has a text model but no explicit verified/declarative vision model
- **THEN** extraction returns vision unavailable before sending image bytes
- **AND** manual look creation and Configure vision remain available

#### Scenario: Saved look outlives the photo
- **WHEN** temporary image content has been cleaned
- **THEN** saved descriptions and provenance remain reusable
- **AND** the photo is not silently attached to image generation

#### Scenario: Image exceeds limits or has invalid content
- **WHEN** a selected image is oversized, exceeds decoded dimensions or is not a supported valid image
- **THEN** it is refused before inference or saved-look persistence

### Requirement: Browser upload selections are server-owned batches

Before source uploads, the server SHALL issue a selection_id owning an ordered file manifest, selection_revision, aggregate byte/file accounting, effective targets and expires_at. File IDs SHALL belong to exactly that selection. Concurrent uploads SHALL reserve and enforce the same aggregate limits atomically using actual bytes; failed/incomplete uploads SHALL release reservations and SHALL NOT enter a valid manifest.

Every file/target mutation SHALL increment selection_revision and invalidate previews. Preview and commit SHALL bind selection ID, revision, complete manifest digests and targets. Cross-selection IDs SHALL be rejected. Cancelled or expired selections SHALL refuse further preview/commit; expiry SHALL be fixed at 24 hours from selection creation. Successful commit SHALL consume the selection and repeated commit requests SHALL return its recorded result without duplicate persistence. Browser responses for superseded selection/revision pairs SHALL be ignored.

#### Scenario: Concurrent uploads exceed aggregate capacity
- **WHEN** two uploads would jointly exceed a selection's byte or file limit
- **THEN** the backend refuses excess capacity atomically even if each file individually fits

#### Scenario: File belongs to another selection
- **WHEN** preview or commit mixes file IDs from different selections
- **THEN** it is refused without importing resources

#### Scenario: Old preview response arrives
- **WHEN** a response names an earlier selection_revision
- **THEN** the UI cannot use it to enable current Import

#### Scenario: Commit is retried after success
- **WHEN** the same consumed selection commit is retried
- **THEN** its recorded result is returned without another import

### Requirement: Browser selection HTTP exposes only safe revisioned state

The browser selection API SHALL use `POST /api/resources/import-selections` to create, `POST .../{selection_id}/files` for one multipart file plus upload request ID, `DELETE .../files/{file_id}` to remove, `PATCH .../files/{file_id}` to choose effective library/auxiliary kind, `POST .../preview`, `POST .../commit`, `POST .../cancel` and `GET .../{selection_id}` for status. Selection-creation request IDs SHALL be globally unique; upload request IDs SHALL be unique within their selection. Either ID SHALL replay the same selection/file when normalized metadata and exact byte digest match and SHALL return `409 idempotency_conflict` when reused for different content. Remove, choice, preview, commit and cancel SHALL require exact expected selection revision. New create/upload SHALL return `201`, ordinary state reads/mutations and terminal replay `200`, and an already-owned valid commit `202` without launching a second import.

The public selection view SHALL contain opaque IDs, a JavaScript-safe integer revision, state (`open`, `committing`, `committed`, `cancelled` or `expired`), fixed expiry, ordered safe file metadata, nullable preview, nullable commit result and nullable `cleanup_warning`. Preview SHALL include opaque preview token, lowercase-hex manifest digest, committable status and readable canonical report projection. Files SHALL expose only ID, display filename, bounded byte count, declared/effective library, structurally matched/effective auxiliary kind and status. `cleanup_warning` SHALL be `null` unless cleanup has failed and will be retried; when present it SHALL be a stable sanitized public message such as `Temporary cleanup is incomplete and will be retried automatically.` It SHALL be derived from internal cleanup state without projecting `cleanup_state`, paths, physical names, arbitrary exception text, tokens, fingerprints or internal timestamps. Physical paths, device/inode values, nanosecond timestamps, raw fingerprints, attestation secrets, staged bytes and `cleanup_state` SHALL remain server-owned. All exposed integers SHALL be no greater than `Number.MAX_SAFE_INTEGER`; digests/tokens SHALL be strings and `mtime_ns` SHALL never be round-tripped through JavaScript.

Stale revision or preview/manifest mismatch SHALL return `409`; invalid target/kind or unresolved commit `422`; file/body/aggregate excess `413`; cancelled/expired mutation `410`; missing selection or cross-selection file ID `404`; disabled mutation `503`. Error detail SHALL include a stable machine code, readable message and current safe view when relevant. Commit while another owner holds the same tuple SHALL return active state; another tuple SHALL conflict. Cancel while the canonical commit transaction is active SHALL return `409 commit_active`.

Startup and every selection access SHALL recover expired/stale commit ownership and perform bounded cleanup. A claim with no atomic import result SHALL return to open before fixed expiry; a committed result SHALL never be inferred. Commit/cancel SHALL remove bytes only after terminal state is durable. Expiry SHALL remove bytes and retain status/result tombstone for 24 additional hours; cleanup failure SHALL be visible and retryable without changing committed resources.

#### Scenario: Filesystem fingerprint exceeds JavaScript precision
- **WHEN** canonical staging metadata contains an `mtime_ns` or another integer greater than `Number.MAX_SAFE_INTEGER`
- **THEN** it remains server-side while the browser receives only bounded counts and string digests/tokens
- **AND** preview/commit attestation remains exact

#### Scenario: Upload response arrives out of order
- **WHEN** concurrent upload responses carry different monotonically increasing revisions
- **THEN** the browser retains the highest revision and cannot re-enable preview/commit from the lower response

#### Scenario: Selection mutation uses a stale revision
- **WHEN** remove, target choice, preview, commit or cancel uses an older revision
- **THEN** it returns a revision conflict with the current safe view and performs no requested mutation

#### Scenario: Another commit owner exists
- **WHEN** a second commit names the same valid selection/revision/preview tuple
- **THEN** it receives committing status or the recorded result without a second canonical import

### Requirement: Manual translation rows use attested bulk application

Manual and assistant-edited rows SHALL produce a direct translation_map and use the library bulk preview/apply contract with canonical authorization, map digest, attestation and library fingerprint. They SHALL NOT use the direct single-revision translation mutation as a shortcut. Duplicate source strings with incompatible translations SHALL be diagnosed before preview rather than silently collapsed.

The 10 MiB limit SHALL apply to actual total HTTP request body bytes before JSON/model parsing. Declared content length SHALL NOT be the only check; missing or misleading lengths SHALL not bypass streaming limits. Oversized bodies SHALL fail with HTTP 413 before parsing or persistence.

#### Scenario: Manual row is applied
- **WHEN** a user accepts edited translation rows
- **THEN** they undergo bulk preview and explicit attested apply
- **AND** direct single-revision mutation is not called

#### Scenario: Body streams without Content-Length
- **WHEN** streamed translation content exceeds 10 MiB
- **THEN** the request stops with 413 before JSON parsing or translation writes

### Requirement: Look import and derivation preserve snapshot integrity

Looks import SHALL validate every garment/outfit/preset and all identity conflicts before writing and SHALL recheck relevant store state inside the same transaction as all persistence. Matching identity alone SHALL NOT count as identical content. A late invalid garment or conflict SHALL leave zero writes. Existing legacy wardrobe import semantics SHALL remain unchanged.

Progression SHALL derive exclusively from the complete supplied session look snapshot, not live catalogue globals. Missing, duplicate or inconsistent garment identities SHALL fail visibly rather than be filtered out. Changes or retirement in the live catalogue SHALL not alter snapshot-derived stages.

#### Scenario: Later imported garment is invalid
- **WHEN** a payload contains valid early garments followed by an invalid one
- **THEN** no garments, outfits or looks from that payload persist

#### Scenario: Store changes after look preview
- **WHEN** a concurrent write introduces an identity conflict before commit
- **THEN** transactional revalidation rejects stale application with zero writes

#### Scenario: Live catalogue changes
- **WHEN** a selected snapshot's source garments change or retire in the catalogue
- **THEN** progression still derives identical text from the session snapshot

#### Scenario: Snapshot contains a malformed garment
- **WHEN** derivation encounters incomplete or duplicate garment identity
- **THEN** it refuses rather than silently omitting the garment

### Requirement: Photo evidence substitutes image digests for binary request content

Persisted photo extraction evidence SHALL contain exact textual request structure, selected model identifier and non-secret generation parameters, validated output and user corrections. Each image request part SHALL be replaced in that persisted projection by an object with sha256, media_type, byte_count, width and height for the actual submitted image. SHA-256 SHALL be a lowercase 64-character hex digest. Binary bytes, data URIs, secret headers, credentials and private endpoint paths SHALL NOT be retained in that evidence or portable export. This projection SHALL NOT be described as the complete wire request.

Image validation SHALL verify actual supported format and complete decode, not only extension or data-URI syntax. The system SHALL reject corrupt/truncated content or dimensions exceeding 25 megapixels before inference, as well as the existing 10 MiB byte limit.

#### Scenario: Request contains an image data URI
- **WHEN** photo extraction evidence is persisted
- **THEN** the data URI is replaced with the digest and verified image metadata
- **AND** text, non-secret model parameters, validated output and edits remain inspectable

#### Scenario: Correct data URI contains invalid image bytes
- **WHEN** a syntactically valid supported data URI contains corrupt or truncated bytes
- **THEN** complete decoding fails and no inference or look save occurs

### Requirement: Portable look version one has a closed schema

Portable export/import SHALL use exactly the following envelope and members; unknown keys or versions SHALL be refused before writes. Strings shown as example values below do not prescribe particular garments.

```json
{
  "schema_version": 1,
  "look": {
    "key": "look-1",
    "version": 1,
    "name": "Blue outfit",
    "appearance": "soft makeup",
    "outfit": {"key": "outfit-1", "garment_keys": ["jacket-1"]}
  },
  "garments": [
    {"key": "jacket-1", "wording": "a blue jacket", "aside": ""}
  ],
  "provenance": null
}
```

look.key SHALL be the stable logical identity used as look_id in session snapshots; look.version SHALL be a positive integer; key/name SHALL be non-empty strings of at most 128 characters without surrounding whitespace or control characters. appearance SHALL be a string. outfit SHALL be null or exactly key (same key constraints) and garment_keys (non-empty ordered unique string keys). garments SHALL contain exactly key, wording and aside per record, with unique keys and full definitions for exactly the referenced garment set; null outfit SHALL require an empty garments array. Wording SHALL be non-empty without surrounding whitespace; aside SHALL be empty or non-empty without surrounding whitespace. Rendering SHALL follow the canonical wording rule.

provenance SHALL be null or exactly {"source":"manual|photo|assistant|import","image_sha256":null}, where source is one of the four named values and image_sha256 is null or a 64-character lowercase hex digest (non-null only for photo). This is portable, untrusted origin annotation, never attestation of an assistant call or user approval. Full assistant evidence, images, credentials, private paths and sessions SHALL NOT be exported. Import SHALL record its local origin as import and preserve any portable annotation separately without promoting it to trusted evidence.

The input garment array SHALL be ordered by outfit.garment_keys for canonical export/equality. Conversion to the session snapshot SHALL create outfit_key plus complete garments in that order; snapshot content_digest SHALL use the existing specified appearance/outfit digest, excluding display name, logical key/version and portable annotations. All referenced content SHALL be self-contained in the new envelope; legacy garment/outfit JSON compatibility remains a separate preflight adapter.

Identity SHALL be (look.key, look.version). Re-import of canonically equal envelope content for that identity SHALL be a no-op. A new unused key MAY retain the declared version. For an existing key, a free version strictly above its latest version MAY be imported after preview; any occupied-different or older-unused version SHALL require explicit new-version or save-copy choice. new-version SHALL allocate latest version + 1 under the same key inside transactional revalidation. save-copy SHALL allocate a new unused logical key and version 1 while preserving reviewed appearance/outfit content. A concurrent identity/latest-version change SHALL invalidate preview instead of silently selecting another version or key. Neither operation SHALL mutate existing versions or reword legacy garment/outfit records; preview SHALL show any necessary garment/outfit key remapping before explicit application.

#### Scenario: Export round trip
- **WHEN** a look is exported and imported into an empty store
- **THEN** normalized logical identity/version and full appearance/outfit content are preserved
- **AND** a second identical import is a no-op

#### Scenario: Same version has different content
- **WHEN** imported content differs under an occupied key/version
- **THEN** no write occurs until the user explicitly selects new-version or save-copy

#### Scenario: Latest version changes after preview
- **WHEN** another write changes the target identity/version allocation
- **THEN** commit requires a fresh preview without overwriting stored versions

### Requirement: Browser adapters resolve incomplete envelopes and ambiguous auxiliary kinds

With an explicit effective target, browser import SHALL recognize an envelope-like object containing an items list even when library is absent or invalid. Expose has_entry_content_markers(data) as the public shared predicate for the existing entry-content marker set; is_source_envelope and the browser adapter SHALL reuse it without changing legacy classification. This relaxed recognition SHALL use that same boundary and additionally allow only the top-level keys items and library (the latter optional). Any other top-level key, including random_payload, SHALL remain unresolved rather than be classified arbitrarily as collection metadata. This restriction applies to the new absent/invalid-library adapter; already-supported canonical envelopes retain their existing parser behavior. It SHALL parse that list while preserving original staged bytes/fingerprint and full accounting. Objects also carrying conflicting entry-content fields SHALL remain unresolved. This adaptation SHALL apply identically during preview and commit revalidation; it SHALL NOT change legacy parser/API behavior or rewrite staged JSON.

For auxiliary inputs with multiple structural kind candidates, the browser SHALL require explicit Advanced selection from those candidates. No first-match or filename rule SHALL silently decide between mined_families and mined_labels. The selected kind SHALL be bound to the selection manifest/revision and attestation, revalidated against the same bytes at commit, and used for canonical auxiliary persistence. Unsupported kind choices SHALL fail. Legacy callers without explicit browser adapter context SHALL retain existing behavior.

#### Scenario: Collection lacks library
- **WHEN** selected JSON contains an items list without library and the user chooses an effective target
- **THEN** the browser adapter parses the collection with original-byte integrity preserved

#### Scenario: Auxiliary shape has two meanings
- **WHEN** a payload matches both mined_families and mined_labels
- **THEN** preview requests an explicit kind choice
- **AND** commit persists that verified kind rather than the parser's first match

#### Scenario: Auxiliary kind changes after preview
- **WHEN** the user changes the chosen kind
- **THEN** the selection revision changes and old preview/commit authorization is invalidated

#### Scenario: Unknown top-level content in a relaxed envelope
- **WHEN** an object has items, no usable library and a random_payload field, or any entry-content marker key
- **THEN** the browser adapter leaves the object unresolved in both preview and commit revalidation
- **AND** no top-level content is silently discarded

### Requirement: Concurrent selection commits have one owner

A selection/revision SHALL be claimed atomically before entering canonical import. Concurrent commit requests SHALL cause exactly one successful canonical import transaction; other callers SHALL receive the active operation or the same final recorded result. Ownership, import outcome and consumed-result recovery SHALL prevent a crash after successful import from causing another canonical commit. A failed rolled-back attempt MAY be retried after ownership release, never concurrently with a live owner.

#### Scenario: Two commits arrive simultaneously
- **WHEN** two requests commit the same current selection before either has completed
- **THEN** only one enters canonical import
- **AND** the other receives progress or the same final result without duplicate writes

### Requirement: Portable look imports enforce a bounded HTTP body

The 10 MiB portable-look-v1 JSON import limit SHALL count actual total streamed HTTP request body bytes before JSON/Pydantic deserialization at every preview/import/commit boundary receiving the JSON content. Content-Length MAY support early rejection but SHALL NOT replace actual byte counting. Missing or misleading Content-Length SHALL NOT bypass the limit. Oversized requests SHALL return HTTP 413 before deserialization or writes; the same boundary SHALL cover legacy garment/outfit JSON accepted through the new look import flow.

#### Scenario: Oversized look body without a reliable declared length
- **WHEN** actual streamed look-import body bytes exceed 10 MiB with missing or falsely small Content-Length
- **THEN** the server returns HTTP 413 before invoking JSON/Pydantic deserialization
- **AND** no look, garment or outfit is written

### Requirement: Portable import equality survives local key remapping

Before local garment/outfit or logical identity allocation, compute portable_content_digest from the complete validated portable envelope using the existing canonical SHA-256 algorithm and the specified garment-array ordering. Persist a server-owned import receipt atomically with the imported version: original look key/version, portable_content_digest, destination look key/version and the exact original-to-local garment/outfit key mapping. Include portable annotation in envelope equality but never promote it to trusted evidence. Keep this digest distinct from the session look snapshot content_digest. The receipt SHALL be local import evidence, not added to the closed portable-look-v1 envelope.

Preview and commit SHALL check an existing receipt for the original identity and canonical pre-remap content before allocating records or declaring a conflict. An exact match with its intact immutable destination SHALL return that existing version as a no-op, including when the original operation selected new-version/save-copy. Differing original content SHALL still require the defined conflict resolution. Receipt creation and uniqueness for the original identity/digest SHALL be enforced in the same transaction as version and garment/outfit writes, preventing concurrent duplicate imports. A missing/inconsistent receipt destination SHALL be diagnosed rather than silently creating replacement records. Export SHALL remain the closed self-contained envelope of the stored version with its local keys; canonical equality with that stored export SHALL also remain a no-op. Neither comparison SHALL mutate previous versions or receipts.

#### Scenario: Identical import after garment and outfit key remapping
- **WHEN** an envelope defining jacket-1 as blue required remapping because local jacket-1 is red, and the exact original envelope is imported again
- **THEN** its pre-remap digest resolves to the same stored look version
- **AND** no additional garment, outfit, look version or import receipt is created

### Requirement: Profiles retain exact immutable identity

A saved-look version SHALL support separately stored immutable automation-profile revisions bound to its exact key, version and content digest. A profile SHALL have its own canonical digest, schema version and accepted provenance. Editing profile facts SHALL create a new revision; existing session evidence SHALL not follow latest. Before a shot is linked, an explicit session profile application SHALL verify exact identity and plan CAS, fence the run and invalidate affected derived preparation.

#### Scenario: Profiles retain exact immutable identity
- **WHEN** a profile revision is edited after a session selected it
- **THEN** a new revision is created and the session keeps its original binding

#### Scenario: Profile changes after session creation
- **WHEN** a new profile revision is saved for an existing look version
- **THEN** an already created session retains its exact accepted profile and digest
- **AND** new sessions can select the newer revision explicitly

### Requirement: Linked profiles freeze without breaking manual portability

Once any shot is linked, the complete session profile SHALL be frozen with its look snapshot and a different profile SHALL require a new session. Absence of a profile SHALL not invalidate ordinary manual look use. The existing closed look snapshot and portable look-v1 schema SHALL remain unchanged; portable export/import SHALL disclose that separate automation readiness is not transferred.

#### Scenario: Linked profiles freeze without breaking manual portability
- **WHEN** a different profile is applied after any shot is linked
- **THEN** application requires a new session while portable-v1 remains unchanged

#### Scenario: Portable look lacks a profile
- **WHEN** a portable-v1 look is imported or exported
- **THEN** its existing compatibility and digest rules remain valid
- **AND** the UI explains that Full Auto profile setup is separate

### Requirement: Profile coverage uses supported exact state facts

Profile v1 SHALL record explicit appearance-clause ownership, ordered garment identity references, worn/aside region coverage, baseline coverage, inventory completeness and the provenance of accepted facts. Coverage regions SHALL be chest, hips, legs and feet with values full, partial, none or unknown. Unknown SHALL remain representable but SHALL prevent Full Auto when applicable to a selected stage. Every garment reference SHALL belong to the bound snapshot.

#### Scenario: Profile coverage uses supported exact state facts
- **WHEN** a garment reference is absent from the bound snapshot
- **THEN** profile validation rejects the reference

### Requirement: Uncovered and aside states require explicit evidence

A selected moved-aside state SHALL require its own authored coverage. Complete inventory and baseline SHALL be explicitly confirmed before uncovered-region claims are allowed. A selected garment-free final stage SHALL reject baseline facts asserting additional worn covering items.

#### Scenario: Uncovered and aside states require explicit evidence
- **WHEN** the selected aside state lacks its own coverage facts
- **THEN** the profile is ineligible without extrapolating from worn-state wording

### Requirement: Appearance clauses have exactly one owner

Appearance ownership SHALL cover the complete source text without overlapping or omitted spans. Each segment SHALL belong to permanent appearance or an identified progressive garment/accessory. Accepted progressive clauses SHALL be represented by canonical wardrobe wording rather than repeated in fixed appearance. Ambiguous wording SHALL require an explicit profile correction or a new corrected look version, not inferred removal.

#### Scenario: Appearance clauses have exactly one owner
- **WHEN** accepted spans overlap or omit part of the appearance
- **THEN** profile validation rejects the ownership partition

#### Scenario: Accessory appears in two competing sources
- **WHEN** the same accessory is marked permanent in appearance and removed by progression
- **THEN** profile eligibility fails with both sources and a correction action

### Requirement: Accessory removal does not imply uncovered anatomy

Accessories SHALL be able to declare no body coverage without implying that their removal exposes a body region.

#### Scenario: Accessory removal does not imply uncovered anatomy
- **WHEN** an accessory explicitly declares none in all supported regions and is removed
- **THEN** its removal contributes no inferred body-region exposure

### Requirement: Prose alone cannot establish profile facts

Accepted facts SHALL come from user input or reviewed structured evidence identifying the exact source and meaning. Free-text garment names, arbitrary prose and unreviewed assistant/photo proposals SHALL NOT establish region coverage or ownership automatically. Full Auto eligibility SHALL identify missing fields and contradictions, not ask users to duplicate the same detail across every take.

#### Scenario: Prose alone cannot establish profile facts
- **WHEN** an assistant proposes coverage without accepted structured evidence
- **THEN** the facts remain non-authoritative and Full Auto readiness is not granted

#### Scenario: Legacy wording supplies no coverage facts
- **WHEN** a look contains only appearance and garment wording
- **THEN** it remains manually usable but is not automatically certified Full Auto ready
- **AND** the user can complete a reusable profile once

#### Scenario: Previously reviewed structured facts are available
- **WHEN** exact source-bound evidence already declares all required region/state facts
- **THEN** those facts can populate a versioned profile without per-take entry or free-text inference
- **AND** their provenance remains inspectable

### Requirement: Appearance spans use exact Unicode scalar indices

Appearance offsets SHALL be zero-based half-open Unicode scalar-value indices into the exact immutable snapshot text, with no normalization or trimming before indexing/digest verification. Unpaired surrogates SHALL be rejected. Browser and backend SHALL agree for non-BMP characters and combining sequences; users SHALL select text visually, not enter numeric offsets.

#### Scenario: Appearance spans use exact Unicode scalar indices
- **WHEN** appearance includes an emoji and a decomposed accent
- **THEN** browser and backend select identical spans without normalization or digest changes

#### Scenario: Appearance contains emoji and combining marks
- **WHEN** ownership spans cross non-BMP or decomposed Unicode text
- **THEN** browser and backend validate identical source spans and digest without rewriting the snapshot

### Requirement: Profile shortcuts require explicit acceptance

The editor SHALL offer explicit whole-appearance-permanent and no-supported-region-coverage shortcuts, and explicit copying of already accepted coverage to a selected equivalent state. Each SHALL produce a previewed user assertion with provenance, not an automatic default derived from a garment name. Existing ownership and inventory checks SHALL still apply.

#### Scenario: Profile shortcuts require explicit acceptance
- **WHEN** the user previews then confirms no-supported-region coverage for selected states
- **THEN** only those states receive the assertion and its provenance

#### Scenario: Accessory shortcut is chosen
- **WHEN** a user explicitly confirms no coverage in supported regions for selected accessory states
- **THEN** the profile records that user assertion for those states and does not infer it for other garments or regions

#### Scenario: A corrected version reuses unchanged facts
- **WHEN** the user explicitly copies accepted coverage from another profile to states whose garment key, canonical wording and state wording are unchanged
- **THEN** the new profile records reviewed provenance to the source profile digest without requiring re-entry of those facts
- **AND** changed states remain unknown and changed appearance requires new ownership spans

### Requirement: Partial coverage does not certify subregions

V1 partial coverage SHALL mean some but not all of the named region without implying side or subregion. V1 SHALL NOT certify arms, hands, back or more precise subregions; canonical garment wording SHALL remain intact and unsupported precise detail SHALL use manual authoring rather than fabricated structured facts.

#### Scenario: Partial coverage does not certify subregions
- **WHEN** a garment has partial chest coverage
- **THEN** the profile asserts neither full coverage nor a side or exact subregion

### Requirement: Look bindings use the snapshot content digest

The saved_look content_digest SHALL use the existing canonical digest of `{appearance, outfit}` produced by saved-look snapshot validation; it SHALL NOT use portable_content_digest or hash the export envelope. Profile bindings SHALL use that same verified snapshot digest.

#### Scenario: Look bindings use the snapshot content digest
- **WHEN** portable_content_digest is supplied instead of the snapshot digest
- **THEN** exact-version verification rejects the substituted hash

#### Scenario: Export digest is submitted as snapshot identity
- **WHEN** a request supplies portable_content_digest instead of the saved version content_digest
- **THEN** the exact-version verification refuses it without substituting either digest

### Requirement: Baseline describes inventory-absent coverage

Baseline SHALL describe supported-region coverage remaining when every listed outfit item is absent, not the initial fully worn outfit. No baseline value SHALL be inferred from an empty inventory. An appearance-only look SHALL have one constant stage, no progression events and empty wardrobe description; Full Auto eligibility SHALL require explicit baseline and inventory confirmation, and consent SHALL show its compiled coverage.

#### Scenario: Baseline describes inventory-absent coverage
- **WHEN** an appearance-only look has explicit confirmed baseline facts
- **THEN** its single stage has no progression events and the consent panel shows compiled coverage

### Requirement: Uncovered assertions are distinct from empty wardrobe

Explicit none means an accepted uncovered-region assertion, whereas absent or unknown facts SHALL block. It SHALL NOT create an implicit no-clothing wardrobe sentence. A selected explicit garment-free final stage SHALL reject a baseline asserting residual worn covering items.

#### Scenario: Uncovered assertions are distinct from empty wardrobe
- **WHEN** an appearance-only look has unknown baseline or an explicit none baseline
- **THEN** unknown blocks eligibility while accepted none compiles coverage without adding a no-clothing wardrobe sentence

#### Scenario: Appearance-only look has no baseline facts
- **WHEN** Full Auto selects an appearance-only look whose baseline is missing or unknown
- **THEN** preflight requests explicit reusable facts rather than equating empty wardrobe with uncovered regions
