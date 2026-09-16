"""Application boundary for resource inspection and import operations.

The parser, importer, store and readiness modules remain the single source of
truth for their respective rules. This module only composes them for HTTP and
CLI callers, and gives the import report a JSON-safe representation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any, Callable, Iterable

import db
import resource_import
import resource_parser
import resource_readiness
import resource_store
import sys

if __name__ == "backend.resource_service" and "resource_service" not in sys.modules:
    sys.modules["resource_service"] = sys.modules[__name__]
elif __name__ == "resource_service" and "backend.resource_service" not in sys.modules:
    sys.modules["backend.resource_service"] = sys.modules[__name__]


PREVIEW_VERSION = 2
PREVIEW_TTL_SECONDS = 24 * 60 * 60
_ATTESTATION_KEY_NAME = ".resource-preview-key"
_ATTESTATION_DIR_NAME = ".resource-preview-attestations"
_ATTESTATION_TOKEN_MIN_LENGTH = 32
_CANONICAL_NON_NEGATIVE_INT_RE = re.compile(r"^(0|[1-9][0-9]*)$")

PURPOSE_PATH_IMPORT = "path_import"
PURPOSE_BROWSER_SELECTION = "browser_selection"


def preview_import(selections: Iterable[tuple[str | Path, str]]) -> resource_import.PreviewReport:
    """Preview selected files without writing application state."""
    return resource_import.preview_import(
        (Path(path), library_key) for path, library_key in selections
    )


def _canonical_commit_core(
    preview: resource_import.PreviewReport,
    failure_injector: Callable[[int], None] | None = None,
) -> resource_import.CommitReport:
    """Execute canonical import and persist coverage in the active transaction."""
    report = resource_import.commit_import(
        preview, failure_injector=failure_injector
    )
    _persist_new_revision_coverage(preview)
    return report


def commit_import(
    preview: resource_import.PreviewReport | dict[str, Any],
    failure_injector: Callable[[int], None] | None = None,
) -> resource_import.CommitReport:
    """Commit an exact preview, rehydrating its typed state when needed."""
    if isinstance(preview, dict):
        preview = preview_from_dict(preview)
    if not isinstance(preview, resource_import.PreviewReport):
        raise TypeError("preview must be a PreviewReport or serialized preview")

    body = _preview_body(preview)
    token = getattr(preview, "_resource_attestation", None)
    if token is None:
        serialized = preview_to_dict(preview)
        token = serialized["attestation"]
    else:
        _verify_attestation(body, token)

    claim = _claim_attestation(body, token)
    try:
        with db.transaction():
            report = _canonical_commit_core(
                preview, failure_injector=failure_injector
            )
    except BaseException:
        _restore_attestation(claim, token)
        raise
    return report


def commit_selection_import(
    preview: resource_import.PreviewReport,
    preview_token: str,
    selection_id: str,
    selection_revision: int,
    manifest_digest: str,
    failure_injector: Callable[[int], None] | None = None,
) -> resource_import.CommitReport:
    """Selection-only commit boundary.

    - Verifies the immutable HMAC attestation and exact server-owned selection context.
    - Never calls _claim_attestation().
    - Requires an already-active caller-owned SQLite transaction.
    - Calls the same canonical core.
    - Returns the same typed CommitReport.
    """
    if not isinstance(preview, resource_import.PreviewReport):
        raise TypeError("preview must be a PreviewReport")
    if not isinstance(preview_token, str) or not preview_token:
        raise ValueError("preview_token is invalid")

    # Requires an already-active caller-owned SQLite transaction
    if not (db.conn().in_transaction or getattr(db, "_tx_depth", 0) > 0):
        raise RuntimeError("commit_selection_import requires an active caller-owned transaction")

    body = _preview_body(preview)
    verify_browser_attestation(
        body=body,
        token=preview_token,
        selection_id=selection_id,
        selection_revision=selection_revision,
        manifest_digest=manifest_digest,
    )

    return _canonical_commit_core(preview, failure_injector=failure_injector)


def _persist_new_revision_coverage(preview: resource_import.PreviewReport) -> None:
    """Store derived coverage only for revisions created by this commit."""
    for file_report in preview.files:
        for outcome in file_report.accepted_outcomes:
            if outcome.classification == resource_import.CLASSIFICATION_UNCHANGED:
                continue
            library = _library(outcome.library_key)
            if library is None:
                raise RuntimeError("committed resource library could not be found")
            revision = resource_store.get_revision(
                library_id=library["id"],
                source_id=outcome.source_id,
                content_digest=outcome.new_content_digest,
            )
            if revision is None:
                raise RuntimeError("committed resource revision could not be found")
            readiness = resource_readiness.evaluate_revision_readiness(
                library["id"],
                outcome.source_id,
                content_digest=outcome.new_content_digest,
            )
            resource_readiness.set_revision_readiness_from_report(
                revision["id"], readiness,
            )


def _fingerprint_to_dict(fingerprint: resource_import.FileFingerprint | None) -> dict[str, Any] | None:
    if fingerprint is None:
        return None
    if (
        not isinstance(fingerprint.mtime_ns, int)
        or isinstance(fingerprint.mtime_ns, bool)
        or fingerprint.mtime_ns < 0
    ):
        raise ValueError("fingerprint mtime_ns must be a non-negative integer")
    return {
        "path": fingerprint.path,
        "size": fingerprint.size,
        "mtime_ns": str(fingerprint.mtime_ns),
        "content_sha256": fingerprint.content_sha256,
    }


def _fingerprint_from_dict(value: Any) -> resource_import.FileFingerprint | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("preview fingerprint must be an object or null")
    try:
        raw_path = value["path"]
        raw_size = value["size"]
        raw_mtime = value["mtime_ns"]
        raw_sha = value["content_sha256"]
    except KeyError as exc:
        raise ValueError("preview fingerprint is incomplete") from exc

    if (
        not isinstance(raw_mtime, str)
        or not _CANONICAL_NON_NEGATIVE_INT_RE.fullmatch(raw_mtime)
    ):
        raise ValueError("preview fingerprint mtime_ns must be a canonical decimal integer string")

    try:
        return resource_import.FileFingerprint(
            path=str(raw_path),
            size=int(raw_size),
            mtime_ns=int(raw_mtime),
            content_sha256=str(raw_sha),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("preview fingerprint is incomplete") from exc


def _accepted_to_dict(value: resource_import.AcceptedEntryOutcome) -> dict[str, Any]:
    return {
        "source_id": value.source_id,
        "library_key": value.library_key,
        "kind": value.kind,
        "classification": value.classification,
        "new_content_digest": value.new_content_digest,
        "previous_content_digest": value.previous_content_digest,
    }


def _accepted_from_dict(value: Any) -> resource_import.AcceptedEntryOutcome:
    if not isinstance(value, dict):
        raise ValueError("accepted preview outcome must be an object")
    try:
        return resource_import.AcceptedEntryOutcome(
            source_id=str(value["source_id"]),
            library_key=str(value["library_key"]),
            kind=str(value["kind"]),
            classification=str(value["classification"]),
            new_content_digest=str(value["new_content_digest"]),
            previous_content_digest=(
                None if value.get("previous_content_digest") is None
                else str(value["previous_content_digest"])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("accepted preview outcome is incomplete") from exc


def _auxiliary_to_dict(value: resource_import.AuxiliaryOutcome) -> dict[str, Any]:
    return {
        "library_key": value.library_key,
        "kind": value.kind,
        "classification": value.classification,
        "new_content_digest": value.new_content_digest,
        "previous_content_digest": value.previous_content_digest,
    }


def _auxiliary_from_dict(value: Any) -> resource_import.AuxiliaryOutcome:
    if not isinstance(value, dict):
        raise ValueError("auxiliary preview outcome must be an object")
    try:
        return resource_import.AuxiliaryOutcome(
            library_key=str(value["library_key"]),
            kind=str(value["kind"]),
            classification=str(value["classification"]),
            new_content_digest=str(value["new_content_digest"]),
            previous_content_digest=(
                None if value.get("previous_content_digest") is None
                else str(value["previous_content_digest"])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("auxiliary preview outcome is incomplete") from exc


def _duplicate_to_dict(value: resource_import.DuplicateIdentifier) -> dict[str, Any]:
    return {
        "source_id": value.source_id,
        "occurrences": value.occurrences,
        "reason": value.reason,
    }


def _duplicate_from_dict(value: Any) -> resource_import.DuplicateIdentifier:
    if not isinstance(value, dict):
        raise ValueError("duplicate preview outcome must be an object")
    try:
        return resource_import.DuplicateIdentifier(
            source_id=str(value["source_id"]),
            occurrences=int(value["occurrences"]),
            reason=str(value["reason"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("duplicate preview outcome is incomplete") from exc


def _unresolved_to_dict(value: resource_import.UnresolvedItem) -> dict[str, Any]:
    return {
        "bucket": value.bucket,
        "index": value.index,
        "reason": value.reason,
        "received_type": value.received_type,
        "expected_kind": value.expected_kind,
        "identifier_fields": list(value.identifier_fields),
    }


def _unresolved_from_dict(value: Any) -> resource_import.UnresolvedItem:
    if not isinstance(value, dict):
        raise ValueError("unresolved preview outcome must be an object")
    try:
        return resource_import.UnresolvedItem(
            bucket=str(value["bucket"]),
            index=int(value["index"]),
            reason=str(value["reason"]),
            received_type=str(value.get("received_type", "")),
            expected_kind=str(value.get("expected_kind", "")),
            identifier_fields=[str(item) for item in value.get("identifier_fields", [])],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("unresolved preview outcome is incomplete") from exc


def _missing_to_dict(value: resource_import.MissingSourceEntry) -> dict[str, Any]:
    return {
        "library_key": value.library_key,
        "source_id": value.source_id,
        "latest_content_digest": value.latest_content_digest,
        "reason": value.reason,
    }


def _missing_from_dict(value: Any) -> resource_import.MissingSourceEntry:
    if not isinstance(value, dict):
        raise ValueError("missing-source preview outcome must be an object")
    try:
        return resource_import.MissingSourceEntry(
            library_key=str(value["library_key"]),
            source_id=str(value["source_id"]),
            latest_content_digest=str(value["latest_content_digest"]),
            reason=str(value.get("reason", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("missing-source preview outcome is incomplete") from exc


def _file_to_dict(value: resource_import.FileReport) -> dict[str, Any]:
    return {
        "file_path": value.file_path,
        "library_key": value.library_key,
        "total_inputs": value.total_inputs,
        "fingerprint": _fingerprint_to_dict(value.fingerprint),
        "accepted_outcomes": [_accepted_to_dict(item) for item in value.accepted_outcomes],
        "auxiliary_outcomes": [_auxiliary_to_dict(item) for item in value.auxiliary_outcomes],
        "duplicate_identifiers": [_duplicate_to_dict(item) for item in value.duplicate_identifiers],
        "unresolved": [_unresolved_to_dict(item) for item in value.unresolved],
    }


def _file_from_dict(value: Any) -> resource_import.FileReport:
    if not isinstance(value, dict):
        raise ValueError("preview file must be an object")
    try:
        return resource_import.FileReport(
            file_path=str(value["file_path"]),
            library_key=str(value["library_key"]),
            total_inputs=int(value["total_inputs"]),
            fingerprint=_fingerprint_from_dict(value.get("fingerprint")),
            accepted_outcomes=[_accepted_from_dict(item) for item in value.get("accepted_outcomes", [])],
            auxiliary_outcomes=[_auxiliary_from_dict(item) for item in value.get("auxiliary_outcomes", [])],
            duplicate_identifiers=[_duplicate_from_dict(item) for item in value.get("duplicate_identifiers", [])],
            unresolved=[_unresolved_from_dict(item) for item in value.get("unresolved", [])],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("preview file is incomplete") from exc


def _preview_body(preview: resource_import.PreviewReport) -> dict[str, Any]:
    return {
        "version": PREVIEW_VERSION,
        "files": [_file_to_dict(item) for item in preview.files],
        "missing_source_entries": [_missing_to_dict(item) for item in preview.missing_source_entries],
    }


def _canonical_preview_body(body: dict[str, Any]) -> bytes:
    return json.dumps(
        body,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _preview_binding(body: dict[str, Any]) -> str:
    """Keep the old public marker for compatibility; never trust it."""
    return hashlib.sha256(_canonical_preview_body(body)).hexdigest()


def _database_directory() -> Path:
    row = db.one("PRAGMA database_list")
    database_file = row.get("file") if row else None
    if database_file and database_file != ":memory:":
        return Path(database_file).resolve().parent
    configured = os.environ.get("IDEVGEN_DATA_DIR")
    return Path(configured or "data").resolve()


def _attestation_directory() -> Path:
    return _database_directory() / _ATTESTATION_DIR_NAME


def _attestation_key(create: bool) -> bytes:
    path = _database_directory() / _ATTESTATION_KEY_NAME
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        if not create:
            raise ValueError("resource preview attestation key is unavailable")
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(key)
        except FileExistsError:
            key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("resource preview attestation key is invalid")
    return key


def _attestation_path(token: str) -> Path:
    if (
        not isinstance(token, str)
        or len(token) < _ATTESTATION_TOKEN_MIN_LENGTH
        or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in token)
    ):
        raise ValueError("serialized resource preview attestation is invalid")
    return _attestation_directory() / f"{token}.json"


def _claim_path(token: str) -> Path:
    return _attestation_directory() / f"{token}.claimed"


def _has_claim(token: str) -> bool:
    return _claim_path(token).is_file()


def _write_new_attestation(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(state, ensure_ascii=True, sort_keys=True) + "\n"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(encoded)


def _canonical_browser_payload(
    body: dict[str, Any],
    selection_id: str,
    selection_revision: int,
    manifest_digest: str,
) -> bytes:
    data = {
        "body": body,
        "context": {
            "manifest_digest": manifest_digest,
            "purpose": PURPOSE_BROWSER_SELECTION,
            "selection_id": selection_id,
            "selection_revision": selection_revision,
        },
    }
    return json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _new_attestation(body: dict[str, Any]) -> str:
    key = _attestation_key(create=True)
    mac = hmac.new(key, _canonical_preview_body(body), hashlib.sha256).hexdigest()
    for _ in range(3):
        token = secrets.token_urlsafe(32)
        try:
            _write_new_attestation(
                _attestation_path(token),
                {
                    "version": PREVIEW_VERSION,
                    "created_at": int(time.time()),
                    "expires_at": int(time.time()) + PREVIEW_TTL_SECONDS,
                    "mac": mac,
                    "purpose": PURPOSE_PATH_IMPORT,
                },
            )
            return token
        except FileExistsError:
            continue
    raise ValueError("could not create resource preview attestation")


def create_browser_attestation(
    preview: resource_import.PreviewReport,
    selection_id: str,
    selection_revision: int,
    manifest_digest: str,
) -> str:
    """Create an immutable browser-scoped attestation bound to selection context."""
    if not isinstance(preview, resource_import.PreviewReport):
        raise TypeError("preview must be a PreviewReport")
    if not isinstance(selection_id, str) or not selection_id:
        raise ValueError("selection_id is invalid")
    if not isinstance(selection_revision, int) or isinstance(selection_revision, bool) or selection_revision < 0:
        raise ValueError("selection_revision is invalid")
    if not isinstance(manifest_digest, str) or len(manifest_digest) != 64:
        raise ValueError("manifest_digest is invalid")

    body = _preview_body(preview)
    payload_bytes = _canonical_browser_payload(
        body=body,
        selection_id=selection_id,
        selection_revision=selection_revision,
        manifest_digest=manifest_digest,
    )
    key = _attestation_key(create=True)
    mac = hmac.new(key, payload_bytes, hashlib.sha256).hexdigest()
    for _ in range(3):
        token = secrets.token_urlsafe(32)
        try:
            _write_new_attestation(
                _attestation_path(token),
                {
                    "version": PREVIEW_VERSION,
                    "created_at": int(time.time()),
                    "expires_at": int(time.time()) + PREVIEW_TTL_SECONDS,
                    "mac": mac,
                    "purpose": PURPOSE_BROWSER_SELECTION,
                    "selection_id": selection_id,
                    "selection_revision": selection_revision,
                    "manifest_digest": manifest_digest,
                },
            )
            return token
        except FileExistsError:
            continue
    raise ValueError("could not create resource preview attestation")


def _load_attestation(body: dict[str, Any], token: str) -> dict[str, Any]:
    path = _attestation_path(token)
    if _has_claim(token):
        raise ValueError("serialized resource preview has already been consumed")
    try:
        state = json.loads(path.read_text(encoding="ascii"))
    except FileNotFoundError as exc:
        raise ValueError("serialized resource preview attestation is unavailable") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("serialized resource preview attestation is invalid") from exc
    if not isinstance(state, dict):
        raise ValueError("serialized resource preview attestation is invalid")
    if state.get("version") != PREVIEW_VERSION:
        raise ValueError("serialized resource preview attestation is invalid")
    if state.get("consumed_at") is not None:
        raise ValueError("serialized resource preview has already been consumed")
    if state.get("purpose") == PURPOSE_BROWSER_SELECTION:
        raise ValueError(
            "serialized resource preview attestation is scoped for browser selection and cannot be consumed via path import"
        )
    try:
        expires_at = int(state["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("serialized resource preview attestation is invalid") from exc
    if expires_at <= int(time.time()):
        raise ValueError("serialized resource preview attestation has expired")
    expected = hmac.new(
        _attestation_key(create=False),
        _canonical_preview_body(body),
        hashlib.sha256,
    ).hexdigest()
    if not isinstance(state.get("mac"), str) or not hmac.compare_digest(state["mac"], expected):
        raise ValueError("serialized resource preview attestation is invalid")
    return state


def verify_browser_attestation(
    body: dict[str, Any],
    token: str,
    selection_id: str,
    selection_revision: int,
    manifest_digest: str,
) -> dict[str, Any]:
    """Verify an immutable browser-scoped attestation against selection context.

    Does NOT check or create a filesystem claim (.claimed file).
    """
    path = _attestation_path(token)
    try:
        state = json.loads(path.read_text(encoding="ascii"))
    except FileNotFoundError as exc:
        raise ValueError("serialized resource preview attestation is unavailable") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("serialized resource preview attestation is invalid") from exc

    if not isinstance(state, dict):
        raise ValueError("serialized resource preview attestation is invalid")
    if state.get("version") != PREVIEW_VERSION:
        raise ValueError("serialized resource preview attestation is invalid")
    if state.get("purpose") != PURPOSE_BROWSER_SELECTION:
        raise ValueError("serialized resource preview attestation is not scoped for browser selection")
    if state.get("selection_id") != selection_id:
        raise ValueError("attestation selection_id mismatch")
    if state.get("selection_revision") != selection_revision:
        raise ValueError("attestation selection_revision mismatch")
    if state.get("manifest_digest") != manifest_digest:
        raise ValueError("attestation manifest_digest mismatch")

    try:
        expires_at = int(state["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("serialized resource preview attestation is invalid") from exc
    if expires_at <= int(time.time()):
        raise ValueError("serialized resource preview attestation has expired")

    payload_bytes = _canonical_browser_payload(
        body=body,
        selection_id=selection_id,
        selection_revision=selection_revision,
        manifest_digest=manifest_digest,
    )
    expected = hmac.new(
        _attestation_key(create=False),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    if not isinstance(state.get("mac"), str) or not hmac.compare_digest(state["mac"], expected):
        raise ValueError("serialized resource preview attestation is invalid")
    return state


_attestation_cleanup_hook: Callable[[str], bool | None] | None = None


def set_attestation_cleanup_hook(hook: Callable[[str], bool | None] | None) -> None:
    global _attestation_cleanup_hook
    _attestation_cleanup_hook = hook


def delete_attestation(token: str) -> tuple[bool, str]:
    """Safely delete an attestation file during cleanup.

    Returns (success, safe_warning).
    Never leaks physical paths in the warning message.
    """
    if not isinstance(token, str) or len(token) < _ATTESTATION_TOKEN_MIN_LENGTH:
        return True, ""

    if _attestation_cleanup_hook is not None:
        try:
            res = _attestation_cleanup_hook(token)
            if res is False:
                return False, "Attestation file cleanup failed: InjectedFailure. Retrying on next recovery sweep."
        except Exception as exc:
            return False, f"Attestation file cleanup failed: {type(exc).__name__}. Retrying on next recovery sweep."

    path = _attestation_directory() / f"{token}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"Attestation file cleanup failed: {type(exc).__name__}. Retrying on next recovery sweep."

    claim = _attestation_directory() / f"{token}.claimed"
    try:
        claim.unlink(missing_ok=True)
    except OSError:
        pass

    return True, ""


def _verify_attestation(body: dict[str, Any], token: str) -> None:
    _load_attestation(body, token)


def _claim_attestation(body: dict[str, Any], token: str) -> tuple[Path, str]:
    """Atomically create the one caller-owned claim file with ``O_EXCL``."""
    _load_attestation(body, token)
    claim_path = _claim_path(token)
    claim_id = secrets.token_hex(16)
    try:
        _write_new_attestation(
            claim_path,
            {"version": PREVIEW_VERSION, "claim_id": claim_id},
        )
    except FileExistsError as exc:
        raise ValueError("serialized resource preview has already been consumed") from exc
    except PermissionError as exc:
        # A concurrent Windows create can briefly report sharing instead of
        # FileExistsError. Fail closed with the same client-facing result.
        if _has_claim(token):
            raise ValueError("serialized resource preview has already been consumed") from exc
        raise ValueError("serialized resource preview has already been consumed") from exc
    return claim_path, claim_id


def _restore_attestation(claim: tuple[Path, str], token: str) -> None:
    """Remove only the claim file this caller atomically owns."""
    claim_path, claim_id = claim
    try:
        state = json.loads(claim_path.read_text(encoding="ascii"))
        if state.get("claim_id") == claim_id:
            claim_path.unlink()
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, PermissionError):
        # Another process owns or completed the token; never resurrect it.
        pass


def _attestation_for_preview(preview: resource_import.PreviewReport, body: dict[str, Any]) -> str:
    existing = getattr(preview, "_resource_attestation", None)
    if existing is not None:
        _verify_attestation(body, existing)
        return existing
    token = _new_attestation(body)
    setattr(preview, "_resource_attestation", token)
    return token


def _preview_attestation(body: dict[str, Any], token: str) -> dict[str, Any]:
    """Return serialized metadata without exposing the local MAC secret."""
    return {
        "binding": _preview_binding(body),
        "attestation": token,
    }


def preview_to_dict(preview: resource_import.PreviewReport) -> dict[str, Any]:
    """Serialize commit state with an opaque local attestation."""
    if not isinstance(preview, resource_import.PreviewReport):
        raise TypeError("preview must be a PreviewReport")
    body = _preview_body(preview)
    token = _attestation_for_preview(preview, body)
    return {**body, **_preview_attestation(body, token)}


def preview_from_dict(value: dict[str, Any]) -> resource_import.PreviewReport:
    """Verify and rehydrate a preview without trusting public hashes."""
    if not isinstance(value, dict) or value.get("version") != PREVIEW_VERSION:
        raise ValueError(f"unsupported resource preview version: {value.get('version') if isinstance(value, dict) else None}")
    body = {
        "version": value["version"],
        "files": value.get("files"),
        "missing_source_entries": value.get("missing_source_entries", []),
    }
    _verify_attestation(body, value.get("attestation"))
    try:
        preview = resource_import.PreviewReport(
            files=[_file_from_dict(item) for item in body["files"]],
            missing_source_entries=[_missing_from_dict(item) for item in body["missing_source_entries"]],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("serialized resource preview is incomplete") from exc
    setattr(preview, "_resource_attestation", value["attestation"])
    return preview


serialize_preview = preview_to_dict
deserialize_preview = preview_from_dict


MAX_SAFE_INTEGER: int = 9007199254740991

_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "version", "phase", "summary", "files", "missing_source_entries"
})
_PREVIEW_SUMMARY_KEYS: frozenset[str] = frozenset({
    "files", "inputs", "accepted", "auxiliary", "duplicates",
    "unresolved", "new", "unchanged", "updated", "missing"
})
_COMMIT_SUMMARY_KEYS: frozenset[str] = frozenset({
    "files", "inputs", "accepted", "auxiliary", "duplicates",
    "unresolved", "new", "unchanged", "updated", "missing",
    "recorded", "new_scene_revisions", "unchanged_scene_revisions",
    "updated_scene_revisions", "new_auxiliary_revisions",
    "unchanged_auxiliary_revisions"
})
_FILE_REPORT_KEYS: frozenset[str] = frozenset({
    "library_key", "total_inputs", "accepted", "auxiliary",
    "duplicates", "unresolved"
})
_ACCEPTED_KEYS: frozenset[str] = frozenset({
    "source_id", "library_key", "kind", "classification",
    "new_content_digest", "previous_content_digest"
})
_AUXILIARY_KEYS: frozenset[str] = frozenset({
    "library_key", "kind", "classification",
    "new_content_digest", "previous_content_digest"
})
_DUPLICATE_KEYS: frozenset[str] = frozenset({
    "source_id", "occurrences"
})
_UNRESOLVED_KEYS: frozenset[str] = frozenset({
    "bucket", "index", "reason", "received_type",
    "expected_kind", "identifier_fields"
})
_MISSING_SOURCE_KEYS: frozenset[str] = frozenset({
    "library_key", "source_id", "latest_content_digest"
})

_ACCEPTED_SCENE_KINDS: frozenset[str] = frozenset((
    resource_parser.KIND_ROOMS,
    resource_parser.KIND_FUSED_SCENES,
))
_AUXILIARY_KINDS: frozenset[str] = frozenset(resource_parser.ALL_AUXILIARY_KINDS)
_CLASSIFICATIONS: frozenset[str] = frozenset(resource_import.ALL_CLASSIFICATIONS)
_UNRESOLVED_BUCKETS: frozenset[str] = frozenset(resource_import.ALL_UNRESOLVED_BUCKETS)
_EXPECTED_KINDS: frozenset[str] = frozenset((
    "",
    resource_parser.KIND_ROOMS,
    resource_parser.KIND_FUSED_SCENES,
))


def _validate_safe_int(v: Any, min_val: int = 0) -> int:
    if type(v) is not int:
        raise ValueError(f"integer required, got {type(v).__name__}")
    if v < min_val or v > MAX_SAFE_INTEGER:
        raise ValueError(f"integer {v} out of range {min_val}..{MAX_SAFE_INTEGER}")
    return v


def _validate_canonical_sha256(v: Any) -> str:
    if type(v) is not str:
        raise ValueError("content digest must be an exact string")
    if len(v) != 64:
        raise ValueError(f"content digest length must be exactly 64, got {len(v)}")
    if not all(c in "0123456789abcdef" for c in v):
        raise ValueError("content digest must be canonical lowercase hexadecimal")
    return v


def _validate_library_key(key: Any) -> str:
    if type(key) is not str:
        raise ValueError("library_key must be an exact string")
    if not (1 <= len(key) <= 128):
        raise ValueError("library_key must be between 1 and 128 characters")
    if key != key.strip():
        raise ValueError("library_key cannot contain leading or trailing whitespace")
    if key in (".", ".."):
        raise ValueError("library_key cannot be '.' or '..'")
    if "/" in key or "\\" in key:
        raise ValueError("library_key cannot contain '/' or '\\'")
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise ValueError("library_key cannot contain ASCII control characters")
    return key


def _validate_source_id(source_id: Any) -> str:
    if type(source_id) is not str:
        raise ValueError("source_id must be an exact string")
    if not source_id:
        raise ValueError("source_id cannot be empty")
    if any(ord(c) < 32 or ord(c) == 127 for c in source_id):
        raise ValueError("source_id cannot contain ASCII control characters")
    return source_id


def _validate_identifier_fields(raw: Any) -> list[str]:
    if type(raw) is not list:
        raise ValueError("identifier_fields must be an exact list")
    canon = list(resource_parser.IDENTIFIER_FIELDS)
    curr_idx = -1
    result: list[str] = []
    for item in raw:
        if type(item) is not str:
            raise ValueError("identifier_field item must be an exact string")
        try:
            pos = canon.index(item)
        except ValueError:
            raise ValueError(f"unknown identifier_field: {item!r}")
        if pos <= curr_idx:
            raise ValueError(f"identifier_fields out of order or duplicated: {item!r}")
        curr_idx = pos
        result.append(item)
    return result


def _validate_classification_relationship(
    classification: str,
    new_content_digest: str,
    previous_content_digest: str | None,
) -> None:
    if classification == resource_import.CLASSIFICATION_NEW:
        if previous_content_digest is not None:
            raise ValueError("classification 'new' requires previous_content_digest to be None")
    elif classification == resource_import.CLASSIFICATION_UNCHANGED:
        if previous_content_digest is None:
            raise ValueError("classification 'unchanged' requires previous_content_digest to be non-None")
        if previous_content_digest != new_content_digest:
            raise ValueError("classification 'unchanged' requires previous_content_digest to equal new_content_digest")
    elif classification == resource_import.CLASSIFICATION_UPDATED:
        if previous_content_digest is None:
            raise ValueError("classification 'updated' requires previous_content_digest to be non-None")
        if previous_content_digest == new_content_digest:
            raise ValueError("classification 'updated' requires previous_content_digest to differ from new_content_digest")
    else:
        raise ValueError(f"invalid classification: {classification!r}")


def _canonicalize_safe_report(value: Any, expected_phase: str | None = None) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("report must be a dictionary")

    if set(value.keys()) != _TOP_LEVEL_KEYS:
        raise ValueError(f"invalid report top-level keys: {set(value.keys())}")

    version = value["version"]
    if type(version) is not int or version != 1:
        raise ValueError("report version must be integer 1")

    phase = value["phase"]
    if type(phase) is not str or phase not in ("preview", "commit"):
        raise ValueError("phase must be 'preview' or 'commit'")
    if expected_phase is not None and phase != expected_phase:
        raise ValueError(f"expected phase {expected_phase!r}, got {phase!r}")

    raw_summary = value["summary"]
    if type(raw_summary) is not dict:
        raise ValueError("summary must be a dictionary")

    expected_summary_keys = _PREVIEW_SUMMARY_KEYS if phase == "preview" else _COMMIT_SUMMARY_KEYS
    if set(raw_summary.keys()) != expected_summary_keys:
        raise ValueError(f"invalid summary keys: {set(raw_summary.keys())}")

    summary_copy: dict[str, int] = {}
    for k in (
        "files", "inputs", "accepted", "auxiliary", "duplicates",
        "unresolved", "new", "unchanged", "updated", "missing"
    ):
        summary_copy[k] = _validate_safe_int(raw_summary[k])

    if phase == "commit":
        for k in (
            "recorded", "new_scene_revisions", "unchanged_scene_revisions",
            "updated_scene_revisions", "new_auxiliary_revisions",
            "unchanged_auxiliary_revisions"
        ):
            summary_copy[k] = _validate_safe_int(raw_summary[k])

    raw_files = value["files"]
    if type(raw_files) is not list:
        raise ValueError("files must be a list")

    files_copy: list[dict[str, Any]] = []
    for f in raw_files:
        if type(f) is not dict:
            raise ValueError("file report item must be a dictionary")
        if set(f.keys()) != _FILE_REPORT_KEYS:
            raise ValueError(f"invalid file report keys: {set(f.keys())}")

        lib_key = _validate_library_key(f["library_key"])
        tot_inputs = _validate_safe_int(f["total_inputs"])

        raw_accepted = f["accepted"]
        if type(raw_accepted) is not list:
            raise ValueError("accepted must be a list")
        accepted_copy: list[dict[str, Any]] = []
        for acc in raw_accepted:
            if type(acc) is not dict:
                raise ValueError("accepted item must be a dictionary")
            if set(acc.keys()) != _ACCEPTED_KEYS:
                raise ValueError(f"invalid accepted item keys: {set(acc.keys())}")
            s_id = _validate_source_id(acc["source_id"])
            a_lib = _validate_library_key(acc["library_key"])
            kind = acc["kind"]
            if type(kind) is not str or kind not in _ACCEPTED_SCENE_KINDS:
                raise ValueError(f"invalid accepted scene kind: {kind!r}")
            clsf = acc["classification"]
            if type(clsf) is not str or clsf not in _CLASSIFICATIONS:
                raise ValueError(f"invalid classification: {clsf!r}")
            new_dig = _validate_canonical_sha256(acc["new_content_digest"])
            prev = acc["previous_content_digest"]
            prev_dig = _validate_canonical_sha256(prev) if prev is not None else None
            _validate_classification_relationship(clsf, new_dig, prev_dig)
            accepted_copy.append({
                "source_id": s_id,
                "library_key": a_lib,
                "kind": kind,
                "classification": clsf,
                "new_content_digest": new_dig,
                "previous_content_digest": prev_dig,
            })

        raw_aux = f["auxiliary"]
        if type(raw_aux) is not list:
            raise ValueError("auxiliary must be a list")
        aux_copy: list[dict[str, Any]] = []
        for aux in raw_aux:
            if type(aux) is not dict:
                raise ValueError("auxiliary item must be a dictionary")
            if set(aux.keys()) != _AUXILIARY_KEYS:
                raise ValueError(f"invalid auxiliary item keys: {set(aux.keys())}")
            aux_lib = _validate_library_key(aux["library_key"])
            kind = aux["kind"]
            if type(kind) is not str or kind not in _AUXILIARY_KINDS:
                raise ValueError(f"invalid auxiliary kind: {kind!r}")
            clsf = aux["classification"]
            if type(clsf) is not str or clsf not in _CLASSIFICATIONS:
                raise ValueError(f"invalid classification: {clsf!r}")
            new_dig = _validate_canonical_sha256(aux["new_content_digest"])
            prev = aux["previous_content_digest"]
            prev_dig = _validate_canonical_sha256(prev) if prev is not None else None
            _validate_classification_relationship(clsf, new_dig, prev_dig)
            aux_copy.append({
                "library_key": aux_lib,
                "kind": kind,
                "classification": clsf,
                "new_content_digest": new_dig,
                "previous_content_digest": prev_dig,
            })

        raw_dups = f["duplicates"]
        if type(raw_dups) is not list:
            raise ValueError("duplicates must be a list")
        dups_copy: list[dict[str, Any]] = []
        for dup in raw_dups:
            if type(dup) is not dict:
                raise ValueError("duplicate item must be a dictionary")
            if set(dup.keys()) != _DUPLICATE_KEYS:
                raise ValueError(f"invalid duplicate item keys: {set(dup.keys())}")
            s_id = _validate_source_id(dup["source_id"])
            occ = _validate_safe_int(dup["occurrences"], min_val=2)
            dups_copy.append({
                "source_id": s_id,
                "occurrences": occ,
            })

        raw_unres = f["unresolved"]
        if type(raw_unres) is not list:
            raise ValueError("unresolved must be a list")
        unres_copy: list[dict[str, Any]] = []
        for unres in raw_unres:
            if type(unres) is not dict:
                raise ValueError("unresolved item must be a dictionary")
            if set(unres.keys()) != _UNRESOLVED_KEYS:
                raise ValueError(f"invalid unresolved item keys: {set(unres.keys())}")
            bkt = unres["bucket"]
            if type(bkt) is not str or bkt not in _UNRESOLVED_BUCKETS:
                raise ValueError(f"invalid unresolved bucket: {bkt!r}")
            idx = _validate_safe_int(unres["index"], min_val=0)
            rsn = unres["reason"]
            if type(rsn) is not str or not rsn:
                raise ValueError("unresolved reason must be a non-empty string")
            if any(ord(c) < 32 or ord(c) == 127 for c in rsn):
                raise ValueError("unresolved reason cannot contain ASCII control characters")
            if bkt == resource_import.BUCKET_FILE_READ_ERROR and rsn != "source file could not be read or parsed":
                raise ValueError("file_read_error bucket must have sanitized reason 'source file could not be read or parsed'")

            rec_type = unres["received_type"]
            if type(rec_type) is not str:
                raise ValueError("unresolved received_type must be a string")
            if any(ord(c) < 32 or ord(c) == 127 for c in rec_type):
                raise ValueError("unresolved received_type cannot contain ASCII control characters")

            exp_kind = unres["expected_kind"]
            if type(exp_kind) is not str or exp_kind not in _EXPECTED_KINDS:
                raise ValueError(f"invalid expected_kind: {exp_kind!r}")

            idf_copy = _validate_identifier_fields(unres["identifier_fields"])
            unres_copy.append({
                "bucket": bkt,
                "index": idx,
                "reason": rsn,
                "received_type": rec_type,
                "expected_kind": exp_kind,
                "identifier_fields": idf_copy,
            })

        file_expected_inputs = len(accepted_copy) + len(aux_copy) + len(dups_copy) + len(unres_copy)
        if tot_inputs != file_expected_inputs:
            raise ValueError(
                f"file report total_inputs ({tot_inputs}) does not reconcile with item counts ({file_expected_inputs})"
            )

        files_copy.append({
            "library_key": lib_key,
            "total_inputs": tot_inputs,
            "accepted": accepted_copy,
            "auxiliary": aux_copy,
            "duplicates": dups_copy,
            "unresolved": unres_copy,
        })

    raw_missing = value["missing_source_entries"]
    if type(raw_missing) is not list:
        raise ValueError("missing_source_entries must be a list")
    missing_copy: list[dict[str, Any]] = []
    for m in raw_missing:
        if type(m) is not dict:
            raise ValueError("missing source entry item must be a dictionary")
        if set(m.keys()) != _MISSING_SOURCE_KEYS:
            raise ValueError(f"invalid missing source entry keys: {set(m.keys())}")
        m_lib = _validate_library_key(m["library_key"])
        s_id = _validate_source_id(m["source_id"])
        lat_dig = _validate_canonical_sha256(m["latest_content_digest"])
        missing_copy.append({
            "library_key": m_lib,
            "source_id": s_id,
            "latest_content_digest": lat_dig,
        })

    if summary_copy["files"] != len(files_copy):
        raise ValueError(f"summary files ({summary_copy['files']}) != len(files) ({len(files_copy)})")
    expected_inputs = sum(f["total_inputs"] for f in files_copy)
    if summary_copy["inputs"] != expected_inputs:
        raise ValueError(f"summary inputs ({summary_copy['inputs']}) != sum of file inputs ({expected_inputs})")

    total_acc = sum(len(f["accepted"]) for f in files_copy)
    total_aux = sum(len(f["auxiliary"]) for f in files_copy)
    total_dup = sum(len(f["duplicates"]) for f in files_copy)
    total_unres = sum(len(f["unresolved"]) for f in files_copy)
    total_missing = len(missing_copy)

    if summary_copy["accepted"] != total_acc:
        raise ValueError(f"summary accepted ({summary_copy['accepted']}) != total accepted items ({total_acc})")
    if summary_copy["auxiliary"] != total_aux:
        raise ValueError(f"summary auxiliary ({summary_copy['auxiliary']}) != total auxiliary items ({total_aux})")
    if summary_copy["duplicates"] != total_dup:
        raise ValueError(f"summary duplicates ({summary_copy['duplicates']}) != total duplicate items ({total_dup})")
    if summary_copy["unresolved"] != total_unres:
        raise ValueError(f"summary unresolved ({summary_copy['unresolved']}) != total unresolved items ({total_unres})")
    if summary_copy["missing"] != total_missing:
        raise ValueError(f"summary missing ({summary_copy['missing']}) != total missing entries ({total_missing})")

    acc_new = sum(1 for f in files_copy for a in f["accepted"] if a["classification"] == resource_import.CLASSIFICATION_NEW)
    acc_unchanged = sum(1 for f in files_copy for a in f["accepted"] if a["classification"] == resource_import.CLASSIFICATION_UNCHANGED)
    acc_updated = sum(1 for f in files_copy for a in f["accepted"] if a["classification"] == resource_import.CLASSIFICATION_UPDATED)

    if summary_copy["new"] != acc_new:
        raise ValueError(f"summary new ({summary_copy['new']}) != accepted new count ({acc_new})")
    if summary_copy["unchanged"] != acc_unchanged:
        raise ValueError(f"summary unchanged ({summary_copy['unchanged']}) != accepted unchanged count ({acc_unchanged})")
    if summary_copy["updated"] != acc_updated:
        raise ValueError(f"summary updated ({summary_copy['updated']}) != accepted updated count ({acc_updated})")
    if summary_copy["new"] + summary_copy["unchanged"] + summary_copy["updated"] != summary_copy["accepted"]:
        raise ValueError("summary new + unchanged + updated != summary accepted")

    if phase == "commit":
        if summary_copy["recorded"] != total_acc + total_aux:
            raise ValueError(f"commit recorded ({summary_copy['recorded']}) != accepted + auxiliary ({total_acc + total_aux})")
        if summary_copy["new_scene_revisions"] != acc_new + acc_updated:
            raise ValueError(f"commit new_scene_revisions ({summary_copy['new_scene_revisions']}) != accepted new + updated ({acc_new + acc_updated})")
        if summary_copy["unchanged_scene_revisions"] != acc_unchanged:
            raise ValueError(f"commit unchanged_scene_revisions ({summary_copy['unchanged_scene_revisions']}) != accepted unchanged ({acc_unchanged})")
        if summary_copy["updated_scene_revisions"] != acc_updated:
            raise ValueError(f"commit updated_scene_revisions ({summary_copy['updated_scene_revisions']}) != accepted updated ({acc_updated})")
        if summary_copy["new_auxiliary_revisions"] + summary_copy["unchanged_auxiliary_revisions"] != total_aux:
            raise ValueError(f"commit new_auxiliary_revisions + unchanged_auxiliary_revisions != total auxiliary ({total_aux})")

    return {
        "version": 1,
        "phase": phase,
        "summary": summary_copy,
        "files": files_copy,
        "missing_source_entries": missing_copy,
    }


def safe_report(report: resource_import.PreviewReport | resource_import.CommitReport) -> dict[str, Any]:
    """Create the stable, path-free report shown by the app and CLI."""
    if not isinstance(report, (resource_import.PreviewReport, resource_import.CommitReport)):
        raise TypeError("report must be a PreviewReport or CommitReport")

    phase = "commit" if isinstance(report, resource_import.CommitReport) else "preview"
    summary: dict[str, int] = {
        "files": report.total_files,
        "inputs": report.total_inputs,
        "accepted": report.total_accepted,
        "auxiliary": report.total_auxiliary,
        "duplicates": report.total_duplicate,
        "unresolved": report.total_unresolved,
        "new": report.total_new,
        "unchanged": report.total_unchanged,
        "updated": report.total_updated,
        "missing": report.total_missing,
    }
    if isinstance(report, resource_import.CommitReport):
        summary.update({
            "recorded": report.total_recorded,
            "new_scene_revisions": report.total_new_scene_revisions,
            "unchanged_scene_revisions": report.total_unchanged_scene_revisions,
            "updated_scene_revisions": report.total_updated_scene_revisions,
            "new_auxiliary_revisions": report.total_new_auxiliary_revisions,
            "unchanged_auxiliary_revisions": report.total_unchanged_auxiliary_revisions,
        })

    files: list[dict[str, Any]] = [
        {
            "library_key": item.library_key,
            "total_inputs": item.total_inputs,
            "accepted": [
                {
                    "source_id": a.source_id,
                    "library_key": a.library_key,
                    "kind": a.kind,
                    "classification": a.classification,
                    "new_content_digest": a.new_content_digest,
                    "previous_content_digest": a.previous_content_digest,
                }
                for a in item.accepted_outcomes
            ],
            "auxiliary": [
                {
                    "library_key": aux.library_key,
                    "kind": aux.kind,
                    "classification": aux.classification,
                    "new_content_digest": aux.new_content_digest,
                    "previous_content_digest": aux.previous_content_digest,
                }
                for aux in item.auxiliary_outcomes
            ],
            "duplicates": [
                {
                    "source_id": d.source_id,
                    "occurrences": d.occurrences,
                }
                for d in item.duplicate_identifiers
            ],
            "unresolved": [
                {
                    "bucket": u.bucket,
                    "index": u.index,
                    "reason": "source file could not be read or parsed" if u.bucket == resource_import.BUCKET_FILE_READ_ERROR else u.reason,
                    "received_type": u.received_type,
                    "expected_kind": u.expected_kind,
                    "identifier_fields": list(u.identifier_fields),
                }
                for u in item.unresolved
            ],
        }
        for item in report.files
    ]

    missing_source_entries: list[dict[str, Any]] = [
        {
            "library_key": m.library_key,
            "source_id": m.source_id,
            "latest_content_digest": m.latest_content_digest,
        }
        for m in report.missing_source_entries
    ]

    projected: dict[str, Any] = {
        "version": 1,
        "phase": phase,
        "summary": summary,
        "files": files,
        "missing_source_entries": missing_source_entries,
    }
    return _canonicalize_safe_report(projected, expected_phase=phase)


def validate_safe_report(value: Any, expected_phase: str | None = None) -> dict[str, Any]:
    """Validate and return a canonical copy of a safe report.

    Accepts only the exact structure emitted by safe_report for the expected phase.
    Rejects unknown fields, wrong types, out-of-range integers, booleans as integers,
    malformed JSON, non-canonical digests, invalid enums, classification mismatches,
    or unreconciled counters.
    """
    if isinstance(value, str):
        if type(value) is not str:
            raise ValueError("persisted report JSON must be a built-in string")
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("malformed JSON in report") from exc
    return _canonicalize_safe_report(value, expected_phase=expected_phase)


def write_report_artifact(
    report: resource_import.PreviewReport | resource_import.CommitReport,
    path: str | Path,
) -> Path:
    """Write only the safe report representation, never source payloads."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(safe_report(report), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _library(library_key: str) -> dict[str, Any] | None:
    return next(
        (item for item in resource_store.list_libraries() if item["library_key"] == library_key),
        None,
    )


def _readiness_view(library_id: int, revision: dict[str, Any]) -> dict[str, Any]:
    report = resource_readiness.evaluate_revision_readiness(
        library_id,
        revision["source_id"],
        content_digest=revision["content_digest"],
    )
    return {
        "status": report.status,
        "pending_fields": dict(report.pending_fields),
        "field_readiness": [
            {
                "name": item.name,
                "role": item.role,
                "translated": item.translated,
                "reason": item.reason,
            }
            for item in report.field_readiness
        ],
        "coverage": report.coverage,
    }


def _revision_view(library: dict[str, Any], revision: dict[str, Any], *, include_payload: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "revision_id": revision["id"],
        "library_key": library["library_key"],
        "library_id": revision["library_id"],
        "source_id": revision["source_id"],
        "content_digest": revision["content_digest"],
        "created_at": revision["created_at"],
        "translation": revision["translation"],
        "coverage": revision["coverage"],
        "readiness": _readiness_view(library["id"], revision),
    }
    if include_payload:
        result["payload"] = revision["payload"]
    return result


def list_resource_libraries() -> list[dict[str, Any]]:
    """List libraries and revision metadata without selecting a latest revision."""
    result: list[dict[str, Any]] = []
    for library in resource_store.list_libraries():
        revisions = resource_store.list_revisions(library["id"])
        auxiliary = resource_store.list_auxiliary(library["id"])
        result.append({
            **library,
            "revision_count": len(revisions),
            "auxiliary_count": len(auxiliary),
            "revisions": [_revision_view(library, item, include_payload=False) for item in revisions],
            "auxiliary": [
                {
                    "auxiliary_id": item["id"],
                    "library_key": library["library_key"],
                    "kind": item["kind"],
                    "content_digest": item["content_digest"],
                    "created_at": item["created_at"],
                }
                for item in auxiliary
            ],
        })
    return result


def get_resource_library(library_key: str) -> dict[str, Any] | None:
    library = _library(library_key)
    if library is None:
        return None
    revisions = resource_store.list_revisions(library["id"])
    auxiliary = resource_store.list_auxiliary(library["id"])
    return {
        **library,
        "revision_count": len(revisions),
        "auxiliary_count": len(auxiliary),
        "revisions": [_revision_view(library, item, include_payload=False) for item in revisions],
        "auxiliary": [
            {
                "auxiliary_id": item["id"],
                "library_key": library["library_key"],
                "kind": item["kind"],
                "content_digest": item["content_digest"],
                "created_at": item["created_at"],
            }
            for item in auxiliary
        ],
    }


def get_resource_revision(
    library_key: str,
    source_id: str,
    content_digest: str,
) -> dict[str, Any] | None:
    """Read one exact immutable revision by its complete natural identity."""
    library = _library(library_key)
    if library is None:
        return None
    revision = resource_store.get_revision(
        library_id=library["id"],
        source_id=source_id,
        content_digest=content_digest,
    )
    if revision is None:
        return None
    return _revision_view(library, revision, include_payload=True)


__all__ = (
    "PREVIEW_VERSION",
    "PURPOSE_PATH_IMPORT",
    "PURPOSE_BROWSER_SELECTION",
    "preview_import",
    "commit_import",
    "commit_selection_import",
    "create_browser_attestation",
    "verify_browser_attestation",
    "delete_attestation",
    "set_attestation_cleanup_hook",
    "preview_to_dict",
    "preview_from_dict",
    "serialize_preview",
    "deserialize_preview",
    "safe_report",
    "validate_safe_report",
    "write_report_artifact",
    "list_resource_libraries",
    "get_resource_library",
    "get_resource_revision",
)
