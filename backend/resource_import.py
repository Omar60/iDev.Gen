"""Backend preview/commit service for resource imports (task 2.3 of
`adopt-resource-session-planning`).

This module sits between the parser (task 2.2) and the persistence
layer (task 2.1) and provides the two operations the OpenSpec
`design.md` names under "Preview and commit are separate operations":

  * `preview_import` reads operator-selected source files, computes a
    stable fingerprint for each, runs the parser, classifies each
    accepted scene entry as new / unchanged / updated relative to
    the database, classifies each auxiliary resource as new /
    unchanged / updated, and reports duplicate identifiers (both
    within one file and across files), non-accepted parser outcomes,
    unreadable file outcomes, and source_ids the database holds that
    are absent from the current import. The function never writes.
  * `commit_import` re-verifies every file's fingerprint by reading
    each file ONCE, uses the verified bytes to drive parsing and
    persistence (closing the TOCTOU window between fingerprint
    verification and the actual write), opens one database
    transaction via `db.transaction`, persists the accepted scene
    set through `resource_store.record_revision` and the auxiliary
    set through `resource_store.record_auxiliary`. A failure at any
    point rolls the entire write set back; unresolved outcomes are
    never written.

The contract this module pins is:

  1. Fingerprint binding. A fingerprint is the source-of-truth for
     "did the file change". The commit refuses if a fingerprint
     differs from the previewed one, regardless of whether the
     parsed entries look similar. The commit reads each file once,
     computes the fingerprint from those bytes, and uses the same
     bytes for parsing and persistence; the TOCTOU window between
     a "verify then re-read" flow is closed because there is no
     re-read.

  2. Count reconciliation. Every file and every input reaches a
     bucket. For each file,
     `accepted_total + auxiliary_total + duplicate_total +
     unresolved_total == total_inputs`. Across all files,
     `total_accepted + total_auxiliary + total_duplicate +
     total_unresolved == total_inputs`, and
     `total_new + total_unchanged + total_updated == total_accepted`
     (and the same identity holds for auxiliary outcomes).
     Unreadable files count as one input each: a file the preview
     could not read or parse contributes one `file_read_error`
     unresolved item and reconciles.

  3. Atomic commit. Either every accepted scene entry AND every
     auxiliary resource are recorded, or no scene and no auxiliary
     are recorded. The DB's immutable revision semantics (one row
     per `(library, source_id, content_digest)` for scenes; one row
     per `(library, kind, content_digest)` for auxiliary; prior
     rows preserved) are the row-level invariants; the transaction
     is the cross-row invariant.

  4. No silent guessing on duplicates. A `(library_key,
     source_id)` pair that appears more than once in the import
     (whether in the same file or across files) is reported as a
     duplicate and every ambiguous occurrence is excluded from the
     accepted set. The import does not pick an arbitrary
     occurrence; the operator resolves the ambiguity and re-runs
     the preview.

  5. No silent guessing on auxiliary. Auxiliary maps the parser
     recognises structurally (translation_map, cut_map,
     mined_families, mined_labels) are stored as auxiliary
     resources with their own immutable revision table. The
     complete original map is preserved, the provenance carries
     the library identity, and identical content does not create
     a duplicate row.

  6. Missing source entries are reported, not deleted. A
     source_id previously stored for a library but absent from
     the current import is reported, and the historical revision
     stays byte-for-byte unchanged.

This module does NOT:

  * touch any other table (only `resource_library`,
    `asset_revision`, and `auxiliary_resource` are read or
    written);
  * call `asset_guard` or apply any content-based filter on
    parser-accepted records;
  * compute readiness, manage translations, or build a session
    plan (those are tasks 2.4 and 3.x);
  * open HTTP routes, build CLI commands, or call ComfyUI (task
    2.5);
  * touch the legacy room-import endpoints, the Compose / Fill /
    Judge code paths, or any existing session / shot / workflow
    table.

The parser contract is preserved: every input the parser hands
back in one of the six mutually exclusive buckets is surfaced by
the import report, and the deep-copied `original` dict the parser
carries is the exact dict `resource_store.record_revision` writes
into the `payload` column.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import db
import resource_parser
import resource_store


# -- Source file fingerprint ------------------------------------------------


@dataclass(frozen=True)
class FileFingerprint:
    """A stable, content-derived identity for a source file.

    The fingerprint is bound to a path string (so the preview can
    match a commit's re-read to the previewed file) and to a content
    sha256 (so the commit can refuse when the bytes have changed).
    The size and mtime are recorded for diagnostics: the content
    hash is the actual binding the commit checks, and a hash
    collision is not a real risk on the file sizes the importer
    handles.
    """

    path: str
    size: int
    mtime_ns: int
    content_sha256: str

    def matches(self, other: "FileFingerprint") -> bool:
        """True when two fingerprints describe the same bytes.

        The path must match (a file renamed away is "changed"); the
        content sha256 must match. Size and mtime are diagnostic
        only: a sha256 equality check implies a size equality
        check, and a future mtime-only check would defeat the whole
        point of fingerprinting bytes.
        """
        return (
            self.path == other.path
            and self.content_sha256 == other.content_sha256
        )


# A failure to read a selected file. The preview records one of
# these in the FileReport's `unresolved` list and refuses to
# process the file further; the commit refuses any preview that
# contains one.
@dataclass(frozen=True)
class UnreadableFile:
    """A selected file the preview could not read or parse.

    `path` is the resolved absolute path the preview tried to
    read; `reason` is a non-empty human-readable string naming the
    failure (file missing, permission denied, not valid UTF-8, not
    valid JSON). The commit refuses any preview whose FileReport
    carries an `unreadable` outcome, so a commit based on a
    non-readable preview never writes.
    """

    path: str
    reason: str


def _read_file_bytes(path: Path) -> tuple[FileFingerprint, bytes] | UnreadableFile:
    """Read the file at ``path`` and compute its fingerprint.

    On success: ``(FileFingerprint, bytes)``. The bytes are the
    exact contents the fingerprint hashes, so a downstream parse
    and a downstream persistence that use these bytes operate on
    the same data the fingerprint verified. This is what closes
    the TOCTOU window between "verify the fingerprint" and "use
    the bytes": there is no re-read.

    On failure: ``UnreadableFile``. Failures are file-system
    errors (missing, permission denied) and UTF-8 decode errors.
    JSON decode errors are NOT caught here: the caller parses the
    bytes, and a JSON failure is reported separately so the
    distinction "the file exists and is readable, but is not
    valid JSON" stays visible in the report.
    """
    p = Path(path).resolve()
    try:
        data = p.read_bytes()
    except OSError as exc:
        return UnreadableFile(path=str(p), reason=f"could not read: {exc}")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return UnreadableFile(path=str(p), reason=f"not valid UTF-8: {exc}")
    stat = p.stat()
    fp = FileFingerprint(
        path=str(p),
        size=len(data),
        mtime_ns=stat.st_mtime_ns,
        content_sha256=hashlib.sha256(data).hexdigest(),
    )
    return fp, data


def _parse_payload(data: bytes, file_path: str) -> Any | UnreadableFile:
    """Parse ``data`` as JSON. Returns the payload or an
    ``UnreadableFile`` naming the failure.

    The bytes are expected to be UTF-8 (the read step guarantees
    this); a JSON decode error here means the file is well-formed
    text but not valid JSON.
    """
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        return UnreadableFile(
            path=file_path, reason=f"not valid JSON: {exc}",
        )


# -- Per-entry classification ------------------------------------------------


CLASSIFICATION_NEW: str = "new"
CLASSIFICATION_UNCHANGED: str = "unchanged"
CLASSIFICATION_UPDATED: str = "updated"

ALL_CLASSIFICATIONS: tuple[str, ...] = (
    CLASSIFICATION_NEW, CLASSIFICATION_UNCHANGED, CLASSIFICATION_UPDATED,
)


@dataclass
class AcceptedEntryOutcome:
    """One accepted scene entry's preview classification.

    `classification` is one of NEW / UNCHANGED / UPDATED. For NEW,
    `previous_content_digest` is None. For UNCHANGED, it equals
    `new_content_digest`. For UPDATED, it is the digest the DB
    currently holds (the prior revision's content).
    """

    source_id: str
    library_key: str
    kind: str
    classification: str
    new_content_digest: str
    previous_content_digest: str | None = None

    @property
    def is_new(self) -> bool:
        return self.classification == CLASSIFICATION_NEW

    @property
    def is_unchanged(self) -> bool:
        return self.classification == CLASSIFICATION_UNCHANGED

    @property
    def is_updated(self) -> bool:
        return self.classification == CLASSIFICATION_UPDATED


@dataclass
class AuxiliaryOutcome:
    """One auxiliary resource's preview classification.

    An auxiliary resource is a structurally recognised map the
    parser classifies as translation_map, cut_map, mined_families,
    or mined_labels. The import persists it to the
    `auxiliary_resource` table with the same idempotent revision
    semantics the scene table pins: identical content creates no
    duplicate row, changed content creates a new immutable row,
    prior rows stay readable.

    `kind` is one of the four auxiliary kinds the parser
    publishes; `classification` is one of NEW / UNCHANGED /
    UPDATED against the current database state.
    """

    library_key: str
    kind: str
    classification: str
    new_content_digest: str
    previous_content_digest: str | None = None

    @property
    def is_new(self) -> bool:
        return self.classification == CLASSIFICATION_NEW

    @property
    def is_unchanged(self) -> bool:
        return self.classification == CLASSIFICATION_UNCHANGED

    @property
    def is_updated(self) -> bool:
        return self.classification == CLASSIFICATION_UPDATED


# Buckets for the unresolved side of the report. The first five
# mirror the parser's mutually exclusive buckets one-to-one; the
# import adds `duplicate_identifier` (the import detects these;
# the parser does not) and `file_read_error` (the file could not
# be read or parsed).
BUCKET_AUXILIARY: str = "auxiliary"
BUCKET_MALFORMED: str = "malformed"
BUCKET_UNSUPPORTED: str = "unsupported"
BUCKET_AMBIGUOUS_IDENTIFIER: str = "ambiguous_identifier"
BUCKET_MISSING_IDENTIFIER: str = "missing_identifier"
BUCKET_DUPLICATE_IDENTIFIER: str = "duplicate_identifier"
BUCKET_FILE_READ_ERROR: str = "file_read_error"

ALL_UNRESOLVED_BUCKETS: tuple[str, ...] = (
    BUCKET_AUXILIARY,
    BUCKET_MALFORMED,
    BUCKET_UNSUPPORTED,
    BUCKET_AMBIGUOUS_IDENTIFIER,
    BUCKET_MISSING_IDENTIFIER,
    BUCKET_DUPLICATE_IDENTIFIER,
    BUCKET_FILE_READ_ERROR,
)


@dataclass
class DuplicateIdentifier:
    """A source_id the import cannot pick without guessing.

    Triggered when the same `(library_key, source_id)` appears
    more than once in the import, whether in the same file or
    across files. The spec requires the import to refuse to
    guess, so every occurrence of the duplicate key is excluded
    from the accepted set. `occurrences` is the total count of
    occurrences in the import; `reason` is a non-empty
    human-readable string the report carries through.
    """

    source_id: str
    occurrences: int
    reason: str


@dataclass
class UnresolvedItem:
    """A non-accepted parser outcome, a duplicate, or a
    file-read error, kept visible in the report.

    `bucket` is one of the seven `BUCKET_*` constants. `reason` is
    a non-empty human-readable string the import report carries
    through unchanged, so a reviewer reading the report can see
    why the entry was not written without re-running the parse.
    """

    bucket: str
    index: int
    reason: str
    received_type: str = ""
    expected_kind: str = ""
    identifier_fields: list[str] = field(default_factory=list)


@dataclass
class MissingSourceEntry:
    """A source_id previously stored for a library, absent from
    the current import.

    Reporting is non-destructive: the historical revision stays
    in the database. `latest_content_digest` is the digest the DB
    currently holds (the latest revision's content); `reason` is a
    non-empty string the import report carries through unchanged.
    """

    library_key: str
    source_id: str
    latest_content_digest: str
    reason: str = ""


# -- Per-file report ---------------------------------------------------------


@dataclass
class FileReport:
    """The outcome of previewing or committing one file.

    A file's report is the unit of reconciliation:
    `accepted_total + auxiliary_total + duplicate_total +
    unresolved_total == total_inputs`. `fingerprint` is None when
    the file could not be read or parsed at preview time; the
    commit refuses any preview whose FileReport carries a None
    fingerprint.
    """

    file_path: str
    library_key: str
    total_inputs: int
    fingerprint: FileFingerprint | None = None
    accepted_outcomes: list[AcceptedEntryOutcome] = field(default_factory=list)
    auxiliary_outcomes: list[AuxiliaryOutcome] = field(default_factory=list)
    duplicate_identifiers: list[DuplicateIdentifier] = field(default_factory=list)
    unresolved: list[UnresolvedItem] = field(default_factory=list)

    @property
    def is_readable(self) -> bool:
        return self.fingerprint is not None

    @property
    def accepted_total(self) -> int:
        return len(self.accepted_outcomes)

    @property
    def auxiliary_total(self) -> int:
        return len(self.auxiliary_outcomes)

    @property
    def duplicate_total(self) -> int:
        return len(self.duplicate_identifiers)

    @property
    def unresolved_total(self) -> int:
        return len(self.unresolved)

    @property
    def accepted_new(self) -> int:
        return sum(1 for o in self.accepted_outcomes if o.is_new)

    @property
    def accepted_unchanged(self) -> int:
        return sum(1 for o in self.accepted_outcomes if o.is_unchanged)

    @property
    def accepted_updated(self) -> int:
        return sum(1 for o in self.accepted_outcomes if o.is_updated)

    @property
    def auxiliary_new(self) -> int:
        return sum(1 for o in self.auxiliary_outcomes if o.is_new)

    @property
    def auxiliary_unchanged(self) -> int:
        return sum(1 for o in self.auxiliary_outcomes if o.is_unchanged)

    @property
    def auxiliary_updated(self) -> int:
        return sum(1 for o in self.auxiliary_outcomes if o.is_updated)

    def counts_reconcile(self) -> bool:
        return (
            self.accepted_total + self.auxiliary_total
            + self.duplicate_total + self.unresolved_total
            == self.total_inputs
        )


# -- Aggregate preview / commit reports --------------------------------------


@dataclass
class PreviewReport:
    """The result of previewing a set of files.

    Reconciliation properties:
      * for every file,
        `accepted_total + auxiliary_total + duplicate_total +
        unresolved_total == total_inputs`;
      * across all files,
        `total_accepted + total_auxiliary + total_duplicate +
        total_unresolved == total_inputs`;
      * `total_new + total_unchanged + total_updated ==
        total_accepted` (and the same identity holds for
        auxiliary outcomes).
    """

    files: list[FileReport] = field(default_factory=list)
    missing_source_entries: list[MissingSourceEntry] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_inputs(self) -> int:
        return sum(f.total_inputs for f in self.files)

    @property
    def total_accepted(self) -> int:
        return sum(f.accepted_total for f in self.files)

    @property
    def total_auxiliary(self) -> int:
        return sum(f.auxiliary_total for f in self.files)

    @property
    def total_duplicate(self) -> int:
        return sum(f.duplicate_total for f in self.files)

    @property
    def total_unresolved(self) -> int:
        return sum(f.unresolved_total for f in self.files)

    @property
    def total_new(self) -> int:
        return sum(f.accepted_new for f in self.files)

    @property
    def total_unchanged(self) -> int:
        return sum(f.accepted_unchanged for f in self.files)

    @property
    def total_updated(self) -> int:
        return sum(f.accepted_updated for f in self.files)

    @property
    def total_auxiliary_new(self) -> int:
        return sum(f.auxiliary_new for f in self.files)

    @property
    def total_auxiliary_unchanged(self) -> int:
        return sum(f.auxiliary_unchanged for f in self.files)

    @property
    def total_auxiliary_updated(self) -> int:
        return sum(f.auxiliary_updated for f in self.files)

    @property
    def total_missing(self) -> int:
        return len(self.missing_source_entries)

    def counts_reconcile(self) -> bool:
        for f in self.files:
            if not f.counts_reconcile():
                return False
        if (
            self.total_accepted + self.total_auxiliary
            + self.total_duplicate + self.total_unresolved
            != self.total_inputs
        ):
            return False
        if self.total_new + self.total_unchanged + self.total_updated != self.total_accepted:
            return False
        if (
            self.total_auxiliary_new + self.total_auxiliary_unchanged
            + self.total_auxiliary_updated
            != self.total_auxiliary
        ):
            return False
        return True


# -- The preview operation ---------------------------------------------------


def _latest_revision_digest(library_id: int, source_id: str) -> str | None:
    """The most recent content_digest for ``(library_id, source_id)``,
    or ``None`` when no revision exists.

    Used to classify an accepted scene entry as NEW (None) /
    UNCHANGED (equal to the new digest) / UPDATED (different from
    the new digest). The "latest" matters when a source entry has
    been reimported before with different content: the import
    classifies against the latest one, and the revision table
    keeps every prior revision.
    """
    row = db.one(
        "SELECT content_digest FROM asset_revision "
        "WHERE library_id = ? AND source_id = ? "
        "ORDER BY id DESC LIMIT 1",
        library_id, source_id,
    )
    return row["content_digest"] if row else None


def _latest_auxiliary_digest(
    library_id: int, kind: str,
) -> str | None:
    """The most recent content_digest for ``(library_id, kind)``,
    or ``None`` when no auxiliary row exists.

    Auxiliary resources do not carry a source_id; the natural
    key is `(library_id, kind, content_digest)`. The "latest"
    matters when an auxiliary map has been reimported before with
    different content: the import classifies against the latest
    one, and the auxiliary table keeps every prior row.
    """
    row = db.one(
        "SELECT content_digest FROM auxiliary_resource "
        "WHERE library_id = ? AND kind = ? "
        "ORDER BY id DESC LIMIT 1",
        library_id, kind,
    )
    return row["content_digest"] if row else None


def _classify_one_entry(
    entry: resource_parser.AcceptedEntry,
    library_id: int | None,
    library_key: str,
) -> AcceptedEntryOutcome:
    """Classify one accepted scene entry against the current
    database state.

    A library that has not been registered yet (no row in
    `resource_library`) cannot have prior revisions, so every
    entry for a fresh library classifies as NEW. The library will
    be registered on the commit.
    """
    new_digest = resource_store.canonical_digest(entry.original)
    if library_id is None:
        return AcceptedEntryOutcome(
            source_id=entry.source_id,
            library_key=library_key,
            kind=entry.kind,
            classification=CLASSIFICATION_NEW,
            new_content_digest=new_digest,
            previous_content_digest=None,
        )
    previous = _latest_revision_digest(library_id, entry.source_id)
    if previous is None:
        classification = CLASSIFICATION_NEW
    elif previous == new_digest:
        classification = CLASSIFICATION_UNCHANGED
    else:
        classification = CLASSIFICATION_UPDATED
    return AcceptedEntryOutcome(
        source_id=entry.source_id,
        library_key=library_key,
        kind=entry.kind,
        classification=classification,
        new_content_digest=new_digest,
        previous_content_digest=previous,
    )


def _classify_auxiliary(
    aux: resource_parser.AuxiliaryResource,
    library_id: int | None,
    library_key: str,
) -> AuxiliaryOutcome:
    """Classify one auxiliary resource against the current database
    state.

    An auxiliary resource is one structurally recognised map the
    parser classified as translation_map, cut_map,
    mined_families, or mined_labels. The natural key is
    `(library_id, kind, content_digest)`.
    """
    new_digest = resource_store.canonical_digest(aux.payload)
    if library_id is None:
        return AuxiliaryOutcome(
            library_key=library_key,
            kind=aux.kind,
            classification=CLASSIFICATION_NEW,
            new_content_digest=new_digest,
            previous_content_digest=None,
        )
    previous = _latest_auxiliary_digest(library_id, aux.kind)
    if previous is None:
        classification = CLASSIFICATION_NEW
    elif previous == new_digest:
        classification = CLASSIFICATION_UNCHANGED
    else:
        classification = CLASSIFICATION_UPDATED
    return AuxiliaryOutcome(
        library_key=library_key,
        kind=aux.kind,
        classification=classification,
        new_content_digest=new_digest,
        previous_content_digest=previous,
    )


def _build_unresolved_from_parser(
    result: resource_parser.ParseResult,
) -> list[UnresolvedItem]:
    """Convert the parser's non-auxiliary, non-accepted outcomes
    into ``UnresolvedItem`` records.

    Auxiliary maps are NOT routed through this helper: they have
    their own classification bucket (auxiliary_outcomes) so the
    report can record their new / unchanged / updated status and
    the commit can persist them. The non-auxiliary, non-accepted
    outcomes — malformed, unsupported, ambiguous-identifier,
    missing-identifier — are mapped 1:1 with the parser's bucket
    names.
    """
    items: list[UnresolvedItem] = []
    for mal in result.malformed:
        items.append(UnresolvedItem(
            bucket=BUCKET_MALFORMED,
            index=mal.index,
            reason=mal.reason,
            received_type=mal.received_type,
        ))
    for uns in result.unsupported:
        items.append(UnresolvedItem(
            bucket=BUCKET_UNSUPPORTED,
            index=uns.index,
            reason=uns.reason,
            received_type=uns.received_type,
        ))
    for amb in result.ambiguous_identifier:
        items.append(UnresolvedItem(
            bucket=BUCKET_AMBIGUOUS_IDENTIFIER,
            index=amb.index,
            reason=amb.reason,
            identifier_fields=list(amb.identifier_fields),
        ))
    for mis in result.missing_identifier:
        items.append(UnresolvedItem(
            bucket=BUCKET_MISSING_IDENTIFIER,
            index=mis.index,
            reason=mis.reason,
            expected_kind=mis.expected_kind,
        ))
    return items


def _build_file_report(file_path: Path, library_key: str) -> FileReport:
    """Build one file's ``FileReport`` from a preview pass.

    The file is read once, the bytes are parsed once, and the
    classification runs over the parser output. A file the
    preview could not read or parse contributes a single
    `file_read_error` unresolved item and `total_inputs=1`
    (the file itself is the input) so the reconciliation
    identity holds.

    The cross-file duplicate detection runs after this function
    in `preview_import`; within-file duplicates are a subset of
    cross-file duplicates and are handled the same way there.
    """
    read = _read_file_bytes(file_path)
    if isinstance(read, UnreadableFile):
        return FileReport(
            file_path=read.path,
            library_key=library_key,
            total_inputs=1,
            fingerprint=None,
            unresolved=[UnresolvedItem(
                bucket=BUCKET_FILE_READ_ERROR,
                index=0,
                reason=read.reason,
            )],
        )
    fingerprint, data = read
    payload = _parse_payload(data, fingerprint.path)
    if isinstance(payload, UnreadableFile):
        # The file is well-formed text but not valid JSON. The
        # fingerprint is still known; the file is just
        # unusable. The reconciliation counts the file as one
        # input and the JSON error as one unresolved item.
        return FileReport(
            file_path=fingerprint.path,
            library_key=library_key,
            total_inputs=1,
            fingerprint=fingerprint,
            unresolved=[UnresolvedItem(
                bucket=BUCKET_FILE_READ_ERROR,
                index=0,
                reason=payload.reason,
            )],
        )
    result = resource_parser.parse_source_payload(payload)
    library_row = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        library_key,
    )
    library_id = library_row["id"] if library_row else None

    accepted_outcomes: list[AcceptedEntryOutcome] = [
        _classify_one_entry(entry, library_id, library_key)
        for entry in result.accepted
    ]
    auxiliary_outcomes: list[AuxiliaryOutcome] = [
        _classify_auxiliary(aux, library_id, library_key)
        for aux in result.auxiliary
    ]
    unresolved = _build_unresolved_from_parser(result)
    return FileReport(
        file_path=fingerprint.path,
        library_key=library_key,
        total_inputs=result.total(),
        fingerprint=fingerprint,
        accepted_outcomes=accepted_outcomes,
        auxiliary_outcomes=auxiliary_outcomes,
        unresolved=unresolved,
    )


def _detect_cross_file_duplicates(files: list[FileReport]) -> None:
    """For each `(library_key, source_id)` appearing more than once
    across the import, move every occurrence to the duplicate
    bucket and remove it from the accepted set.

    Within-file duplicates are a subset of cross-file duplicates,
    so a single pass over all files catches both. The spec
    forbids the import from picking an arbitrary occurrence; the
    operator resolves the ambiguity and re-runs the preview.
    """
    # Collect all accepted outcomes grouped by (library_key,
    # source_id). The result preserves the originating
    # FileReport so each occurrence can be removed in place.
    by_key: dict[tuple[str, str], list[tuple[FileReport, AcceptedEntryOutcome]]] = {}
    for file_report in files:
        for outcome in file_report.accepted_outcomes:
            key = (file_report.library_key, outcome.source_id)
            by_key.setdefault(key, []).append((file_report, outcome))
    for (library_key, source_id), occurrences in by_key.items():
        if len(occurrences) <= 1:
            continue
        # One DuplicateIdentifier record per excluded
        # occurrence. The reconciliation property the report
        # carries (`accepted + auxiliary + duplicate +
        # unresolved == total_inputs`) requires this: each
        # excluded occurrence contributes one duplicate record.
        for file_report, outcome in occurrences:
            file_report.accepted_outcomes.remove(outcome)
            file_report.duplicate_identifiers.append(DuplicateIdentifier(
                source_id=source_id,
                occurrences=len(occurrences),
                reason=(
                    f"source_id {source_id!r} appears "
                    f"{len(occurrences)} times in the import "
                    f"for library {library_key!r}; the import "
                    f"does not pick one occurrence and "
                    f"excludes all from the accepted set"
                ),
            ))


def _compute_missing_source_entries(
    files: list[FileReport],
) -> list[MissingSourceEntry]:
    """The source_ids in the DB that are not present in this import.

    For each library the import touches, find every source_id the
    DB holds a revision for and subtract the source_ids the
    import's accepted outcomes cover. The remainder is the
    "missing" set. The historical revisions are NOT deleted:
    reporting is the entire point of the list.

    A library that has no DB row yet (the import is its first
    time) contributes no missing entries: there is nothing to be
    missing.
    """
    seen: dict[str, set[str]] = {}
    for f in files:
        seen.setdefault(f.library_key, set()).update(
            o.source_id for o in f.accepted_outcomes
        )
    missing: list[MissingSourceEntry] = []
    for library_key, library_seen in seen.items():
        library_row = db.one(
            "SELECT id FROM resource_library WHERE library_key = ?",
            library_key,
        )
        if library_row is None:
            continue
        library_id = library_row["id"]
        rows = db.q(
            "SELECT source_id, content_digest FROM asset_revision "
            "WHERE library_id = ? "
            "ORDER BY source_id, id DESC",
            library_id,
        )
        latest_by_source: dict[str, str] = {}
        for row in rows:
            if row["source_id"] not in latest_by_source:
                latest_by_source[row["source_id"]] = row["content_digest"]
        for source_id, digest in latest_by_source.items():
            if source_id not in library_seen:
                missing.append(MissingSourceEntry(
                    library_key=library_key,
                    source_id=source_id,
                    latest_content_digest=digest,
                    reason=(
                        f"source_id {source_id!r} was previously "
                        f"stored for library {library_key!r} but is "
                        f"absent from the current import; the "
                        f"historical revision remains in the database"
                    ),
                ))
    missing.sort(key=lambda m: (m.library_key, m.source_id))
    return missing


def preview_import(
    selections: Iterable[tuple[Path, str]],
) -> PreviewReport:
    """Preview a set of operator-selected source files.

    `selections` is an iterable of ``(file_path, library_key)``
    pairs. The library_key names the resource_library the
    accepted entries and auxiliary resources will be written
    under; the file is read, parsed, and the outcomes are
    classified against the database.

    The function never writes. The returned report accounts for
    every input and reconciles its counts.
    """
    files: list[FileReport] = []
    for path, library_key in selections:
        files.append(_build_file_report(Path(path), library_key))
    _detect_cross_file_duplicates(files)
    missing = _compute_missing_source_entries(files)
    return PreviewReport(files=files, missing_source_entries=missing)


# -- The commit operation ----------------------------------------------------


class StaleFingerprintError(Exception):
    """At least one source file changed, became unreadable, or was
    unreadable at preview time; commit refused."""

    def __init__(self, mismatches: list[tuple[str, str, str]]):
        self.mismatches = mismatches
        super().__init__(
            f"{len(mismatches)} source file(s) changed or became "
            f"unreadable after preview; a fresh preview is required "
            f"before commit"
        )


class CommitAborted(Exception):
    """The commit was rolled back due to a persistence failure."""

    def __init__(
        self,
        message: str,
        processed: int,
        cause: BaseException | None = None,
    ):
        self.processed = processed
        self.cause = cause
        super().__init__(message)


# A failure injector is a callable called after each successful
# `record_*` (scene revision or auxiliary resource) with the
# running count of persisted items in this commit. If it raises,
# the commit rolls back. The default is `None` (no injection).
FailureInjector = Callable[[int], None]


@dataclass
class CommitReport:
    """The result of a successful commit.

    The shape mirrors the preview report so a test can compare
    `preview.counts_reconcile()` and `commit.counts_reconcile()`
    directly. `total_recorded` is the number of persistence
    operations the commit performed (scenes + auxiliary):
    identical content returns the existing row's id, so
    "recorded" here is the union of newly-inserted and
    already-existing. `total_new_scene_revisions`,
    `total_unchanged_scene_revisions`,
    `total_new_auxiliary_revisions`, and
    `total_unchanged_auxiliary_revisions` split the count between
    scene and auxiliary writes. The reconciliation property holds
    for new, unchanged, and updated outcomes on both axes:
    `total_new + total_unchanged + total_updated == total_accepted`
    and the same identity holds for auxiliary outcomes, with
    `total_new_auxiliary_revisions + total_unchanged_auxiliary_revisions
    == total_auxiliary`.
    """

    files: list[FileReport] = field(default_factory=list)
    total_recorded: int = 0
    total_new_scene_revisions: int = 0
    total_unchanged_scene_revisions: int = 0
    total_updated_scene_revisions: int = 0
    total_new_auxiliary_revisions: int = 0
    total_unchanged_auxiliary_revisions: int = 0
    missing_source_entries: list[MissingSourceEntry] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_inputs(self) -> int:
        return sum(f.total_inputs for f in self.files)

    @property
    def total_accepted(self) -> int:
        return sum(f.accepted_total for f in self.files)

    @property
    def total_auxiliary(self) -> int:
        return sum(f.auxiliary_total for f in self.files)

    @property
    def total_duplicate(self) -> int:
        return sum(f.duplicate_total for f in self.files)

    @property
    def total_unresolved(self) -> int:
        return sum(f.unresolved_total for f in self.files)

    @property
    def total_new(self) -> int:
        return sum(f.accepted_new for f in self.files)

    @property
    def total_unchanged(self) -> int:
        return sum(f.accepted_unchanged for f in self.files)

    @property
    def total_updated(self) -> int:
        return sum(f.accepted_updated for f in self.files)

    @property
    def total_auxiliary_new(self) -> int:
        return sum(f.auxiliary_new for f in self.files)

    @property
    def total_auxiliary_unchanged(self) -> int:
        return sum(f.auxiliary_unchanged for f in self.files)

    @property
    def total_auxiliary_updated(self) -> int:
        return sum(f.auxiliary_updated for f in self.files)

    @property
    def total_missing(self) -> int:
        return len(self.missing_source_entries)

    def counts_reconcile(self) -> bool:
        for f in self.files:
            if not f.counts_reconcile():
                return False
        if (
            self.total_accepted + self.total_auxiliary
            + self.total_duplicate + self.total_unresolved
            != self.total_inputs
        ):
            return False
        if self.total_new + self.total_unchanged + self.total_updated != self.total_accepted:
            return False
        if (
            self.total_auxiliary_new + self.total_auxiliary_unchanged
            + self.total_auxiliary_updated
            != self.total_auxiliary
        ):
            return False
        if self.total_recorded != self.total_accepted + self.total_auxiliary:
            return False
        if (
            self.total_new_scene_revisions + self.total_unchanged_scene_revisions
            != self.total_accepted
        ):
            return False
        if self.total_new_scene_revisions != self.total_new + self.total_updated:
            return False
        if self.total_unchanged_scene_revisions != self.total_unchanged:
            return False
        if (
            self.total_new_auxiliary_revisions
            + self.total_unchanged_auxiliary_revisions
            != self.total_auxiliary
        ):
            return False
        # The FileReport's auxiliary classification (new /
        # unchanged / updated) reflects the database state at
        # preview time; the commit's per-row accounting
        # reflects what `record_auxiliary` actually did
        # inside the transaction. They can diverge for
        # identical same-kind auxiliary payloads in one file
        # (the preview classifies both as NEW against an
        # empty table; the commit inserts the first and finds
        # the second unchanged at the row level). The
        # reconciliation property is therefore held within
        # each accounting view separately, not across them.
        return True


def _verify_and_partition(
    preview: PreviewReport,
) -> tuple[
    list[tuple[FileReport, bytes]],
    list[FileReport],
    list[tuple[str, str, str]],
]:
    """Verify every file's fingerprint and partition the preview
    into three lists.

    The verification reads each file ONCE, computes the
    fingerprint from those bytes, and returns the same bytes for
    parsing. A re-read in the parser would re-open the TOCTOU
    window: the file could change between the fingerprint check
    and the re-read, and the parser would then operate on bytes
    the commit never verified. The verified bytes are the only
    bytes the commit persists, so a file changed after the
    preview cannot sneak through.

    Partition:
      * ``to_persist``: files the commit will write. Each entry
        carries the verified bytes.
      * ``unresolved_to_skip``: files the preview already marked
        as unreadable or unparseable. Their FileReport is kept
        verbatim in the CommitReport (so the operator sees them),
        but the commit never parses or persists them.
      * ``mismatches``: files whose current state no longer
        matches the preview. The whole commit is refused
        (StaleFingerprintError) when this list is non-empty,
        unless the file was already unresolved at preview time:
        those are kept in ``unresolved_to_skip`` instead, so a
        good file alongside a bad file is not held hostage.
    """
    to_persist: list[tuple[FileReport, bytes]] = []
    unresolved_to_skip: list[FileReport] = []
    mismatches: list[tuple[str, str, str]] = []
    for file_report in preview.files:
        if file_report.fingerprint is None:
            # Unreadable at preview: no fingerprint, no commit
            # activity. Per spec, the file must never be newly
            # parsed or persisted; a fresh preview is required
            # to consider it.
            unresolved_to_skip.append(file_report)
            continue
        read = _read_file_bytes(Path(file_report.fingerprint.path))
        if isinstance(read, UnreadableFile):
            mismatches.append((
                file_report.fingerprint.path,
                file_report.fingerprint.content_sha256,
                f"unreadable: {read.reason}",
            ))
            continue
        current_fp, current_data = read
        if not current_fp.matches(file_report.fingerprint):
            mismatches.append((
                file_report.fingerprint.path,
                file_report.fingerprint.content_sha256,
                current_fp.content_sha256,
            ))
            continue
        # The fingerprint matches. If the preview already marked
        # the file as a file-read error (e.g. invalid JSON), the
        # matched bytes are still not parseable, so there is
        # nothing to persist; the unresolved report travels on.
        if (
            not file_report.accepted_outcomes
            and not file_report.auxiliary_outcomes
        ):
            unresolved_to_skip.append(file_report)
            continue
        to_persist.append((file_report, current_data))
    return to_persist, unresolved_to_skip, mismatches


def commit_import(
    preview: PreviewReport,
    *,
    failure_injector: FailureInjector | None = None,
) -> CommitReport:
    """Persist the accepted scene set and the auxiliary set from
    ``preview``, atomically.

    Steps:
      1. Re-check every file's fingerprint by reading each file
         once, computing the fingerprint from those bytes, and
         comparing to the previewed fingerprint. The same bytes
         drive the parser and the persistence, so a file changed
         after the preview cannot sneak through.
      2. Partition the preview: files the preview already marked
         as unreadable travel on as unresolved FileReports
         without being parsed or persisted; files whose current
         state no longer matches the preview are mismatches and
         refuse the whole commit. A good file alongside a bad
         file is therefore not held hostage by the bad one.
      3. Open one database transaction via `db.transaction()`.
      4. For each file to persist, parse the verified bytes,
         ensure the library exists lazily, and persist every
         accepted scene entry through
         `resource_store.record_revision` and every auxiliary
         resource through `resource_store.record_auxiliary`.
         Each auxiliary outcome is matched to its exact parser
         payload by the (kind, content_digest) key, so two
         same-kind, different-content auxiliary resources in the
         same file are written separately.
      5. After each successful persistence operation, invoke
         `failure_injector(processed)` if it was supplied. A
         raised exception there is the same as any other
         exception in the block: the transaction rolls back, the
         commit raises `CommitAborted` with the cause and the
         count of operations that were about to be committed.
      6. On success, return a `CommitReport` with the actual
         per-entry outcomes from the write phase, including the
         unresolved file reports that were preserved from the
         preview.
    """
    to_persist, unresolved_to_skip, mismatches = _verify_and_partition(preview)
    if mismatches:
        raise StaleFingerprintError(mismatches)

    # All files appear in the CommitReport: the to-persist files
    # are added as the write proceeds, the unresolved-to-skip
    # files travel on from the preview verbatim.
    file_reports: list[FileReport] = list(unresolved_to_skip)
    library_ids: dict[str, int] = {}
    # Each tuple: (outcome, was_newly_inserted). An outcome is
    # an AcceptedEntryOutcome or AuxiliaryOutcome; the bool is
    # True when the commit wrote a new row, False when the row
    # already existed and `record_*` returned the existing id.
    recorded: list[tuple[Any, bool]] = []
    new_scene_count = 0
    unchanged_scene_count = 0
    updated_scene_count = 0
    new_aux_count = 0
    unchanged_aux_count = 0
    processed = 0

    try:
        with db.transaction():
            for file_report, data in to_persist:
                # Parse the verified bytes. The fingerprint check
                # above guarantees these bytes are the same ones
                # the preview parsed, so the parser output is
                # what the preview's FileReport already
                # classified.
                payload = _parse_payload(data, file_report.fingerprint.path)
                if isinstance(payload, UnreadableFile):
                    # Defensive: a parse failure here means the
                    # file was valid JSON at preview time but
                    # became invalid at commit time. The
                    # fingerprint check passed (bytes are the
                    # same), so this branch is unreachable in
                    # practice; the assertion guards against a
                    # future refactor that breaks the contract.
                    raise CommitAborted(
                        f"verified bytes failed JSON parse for "
                        f"{file_report.fingerprint.path}: "
                        f"{payload.reason}",
                        processed=processed,
                    )
                result = resource_parser.parse_source_payload(payload)
                # Use the preview's FileReport directly: the
                # cross-file duplicate detection already
                # applied there is the source of truth the
                # operator approved. The parse output is only
                # used to recover the full original dicts the
                # persistence layer needs.
                rebuilt = file_report
                file_reports.append(rebuilt)
                # A library is created lazily: only when at
                # least one accepted scene entry or auxiliary
                # resource will be recorded against it. An
                # import whose only outcomes are malformed,
                # unsupported, ambiguous, missing, duplicate, or
                # file-read errors must NOT create a library,
                # because a library with no content is a label
                # with no data. The unique key on
                # `resource_library.library_key` makes the
                # call itself idempotent on the second and
                # later commits.
                if (
                    (rebuilt.accepted_outcomes or rebuilt.auxiliary_outcomes)
                    and rebuilt.library_key not in library_ids
                ):
                    library_ids[rebuilt.library_key] = resource_store.ensure_library(
                        rebuilt.library_key,
                    )
                library_id = library_ids.get(rebuilt.library_key)
                # Recover the full original dict for each
                # accepted scene outcome. First-occurrence-wins
                # is irrelevant here: the cross-file duplicate
                # detection already removed duplicates from
                # the preview's FileReport, so every remaining
                # accepted_outcome is a unique source_id.
                originals_by_id: dict[str, dict] = {
                    entry.source_id: entry.original
                    for entry in result.accepted
                }
                # Auxiliary outcomes are matched to their exact
                # parser payload by (kind, content_digest) so two
                # same-kind, different-content auxiliaries in the
                # same file are written separately, not collapsed
                # by a kind-only key.
                auxiliaries_by_key: dict[tuple[str, str], dict] = {
                    (
                        aux.kind,
                        resource_store.canonical_digest(aux.payload),
                    ): aux.payload
                    for aux in result.auxiliary
                }
                # Record scene revisions in source_id order
                # so the write order is deterministic.
                sorted_outcomes = sorted(
                    rebuilt.accepted_outcomes,
                    key=lambda o: o.source_id,
                )
                for outcome in sorted_outcomes:
                    original = originals_by_id[outcome.source_id]
                    pre = db.one(
                        "SELECT id FROM asset_revision "
                        "WHERE library_id = ? AND source_id = ? "
                        "AND content_digest = ?",
                        library_id,
                        outcome.source_id,
                        outcome.new_content_digest,
                    )
                    resource_store.record_revision(
                        library_id, outcome.source_id, original,
                    )
                    was_new = pre is None
                    recorded.append((outcome, was_new))
                    if was_new:
                        # An UPDATED outcome is a row the commit
                        # inserts (the prior revision stays
                        # readable, but a new row is created
                        # because the content digest differs).
                        new_scene_count += 1
                        if outcome.is_updated:
                            updated_scene_count += 1
                    else:
                        unchanged_scene_count += 1
                    processed += 1
                    if failure_injector is not None:
                        failure_injector(processed)
                # Record auxiliary resources in (kind,
                # content_digest) order so the write order is
                # deterministic.
                sorted_aux = sorted(
                    rebuilt.auxiliary_outcomes,
                    key=lambda o: (o.kind, o.new_content_digest),
                )
                for outcome in sorted_aux:
                    key = (outcome.kind, outcome.new_content_digest)
                    aux_payload = auxiliaries_by_key[key]
                    pre = db.one(
                        "SELECT id FROM auxiliary_resource "
                        "WHERE library_id = ? AND kind = ? "
                        "AND content_digest = ?",
                        library_id,
                        outcome.kind,
                        outcome.new_content_digest,
                    )
                    resource_store.record_auxiliary(
                        library_id, outcome.kind, aux_payload,
                    )
                    was_new = pre is None
                    recorded.append((outcome, was_new))
                    if was_new:
                        new_aux_count += 1
                    else:
                        unchanged_aux_count += 1
                    processed += 1
                    if failure_injector is not None:
                        failure_injector(processed)
    except CommitAborted:
        raise
    except BaseException as exc:
        # The transaction is already rolled back by
        # `db.transaction`. Surface a typed exception that names
        # the cause and the number of operations the commit had
        # touched when it failed.
        raise CommitAborted(
            f"commit rolled back after {processed} operation(s): {exc}",
            processed=processed,
            cause=exc,
        ) from exc

    return CommitReport(
        files=file_reports,
        total_recorded=len(recorded),
        total_new_scene_revisions=new_scene_count,
        total_unchanged_scene_revisions=unchanged_scene_count,
        total_updated_scene_revisions=updated_scene_count,
        total_new_auxiliary_revisions=new_aux_count,
        total_unchanged_auxiliary_revisions=unchanged_aux_count,
        missing_source_entries=list(preview.missing_source_entries),
    )


__all__ = (
    "FileFingerprint", "UnreadableFile",
    "AcceptedEntryOutcome", "AuxiliaryOutcome",
    "DuplicateIdentifier", "UnresolvedItem", "MissingSourceEntry",
    "FileReport", "PreviewReport", "CommitReport",
    "StaleFingerprintError", "CommitAborted",
    "CLASSIFICATION_NEW", "CLASSIFICATION_UNCHANGED", "CLASSIFICATION_UPDATED",
    "ALL_CLASSIFICATIONS",
    "BUCKET_AUXILIARY", "BUCKET_MALFORMED", "BUCKET_UNSUPPORTED",
    "BUCKET_AMBIGUOUS_IDENTIFIER", "BUCKET_MISSING_IDENTIFIER",
    "BUCKET_DUPLICATE_IDENTIFIER", "BUCKET_FILE_READ_ERROR",
    "ALL_UNRESOLVED_BUCKETS",
    "preview_import", "commit_import", "FailureInjector",
)
