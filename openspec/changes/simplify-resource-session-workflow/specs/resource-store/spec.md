## ADDED Requirements

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

### Requirement: Photo look extraction is an explicitly reviewed proposal

The system SHALL accept one browser-selected JPEG, PNG or WebP look image per operation, up to 10 MiB file bytes and 25 megapixels decoded. It SHALL validate actual image content, stage it privately behind opaque identifiers, and require an explicit extraction action before sending it to the configured vision assistant. It SHALL NOT infer person identity, unseen garments, background, camera or pose as accepted look content.

Extraction SHALL produce editable visible appearance/garment proposals with unresolved details identified. Users SHALL confirm removal order and review or edit content before saving. Failed extraction or invalid output SHALL save no look. Without vision support, manual entry SHALL remain available and extraction unavailability SHALL be explained.

Saved provenance SHALL retain the source digest, assistant request/output and user edits without embedding image bytes in ordinary metadata or portable JSON. Temporary images SHALL expire after 24 hours and be cleaned after save/cancel when no longer needed. Saved looks SHALL remain usable without the original image or another assistant call. Look images SHALL NOT automatically become generation references.

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
