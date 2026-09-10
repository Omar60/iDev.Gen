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
from typing import Any, Iterable

import db
import resource_import
import resource_readiness
import resource_store


PREVIEW_VERSION = 2
PREVIEW_TTL_SECONDS = 24 * 60 * 60
_ATTESTATION_KEY_NAME = ".resource-preview-key"
_ATTESTATION_DIR_NAME = ".resource-preview-attestations"
_ATTESTATION_TOKEN_MIN_LENGTH = 32
_CANONICAL_NON_NEGATIVE_INT_RE = re.compile(r"^(0|[1-9][0-9]*)$")


def preview_import(selections: Iterable[tuple[str | Path, str]]) -> resource_import.PreviewReport:
    """Preview selected files without writing application state."""
    return resource_import.preview_import(
        (Path(path), library_key) for path, library_key in selections
    )


def commit_import(
    preview: resource_import.PreviewReport | dict[str, Any],
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
            report = resource_import.commit_import(preview)
            _persist_new_revision_coverage(preview)
    except BaseException:
        _restore_attestation(claim, token)
        raise
    return report


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


def _safe_reason(bucket: str, reason: str) -> str:
    if bucket == resource_import.BUCKET_FILE_READ_ERROR:
        return "source file could not be read or parsed"
    return reason


def _safe_file_report(value: resource_import.FileReport) -> dict[str, Any]:
    """Return report data without source paths or source payloads."""
    return {
        "library_key": value.library_key,
        "total_inputs": value.total_inputs,
        "accepted": [_accepted_to_dict(item) for item in value.accepted_outcomes],
        "auxiliary": [_auxiliary_to_dict(item) for item in value.auxiliary_outcomes],
        "duplicates": [
            {
                "source_id": item.source_id,
                "occurrences": item.occurrences,
            }
            for item in value.duplicate_identifiers
        ],
        "unresolved": [
            {
                "bucket": item.bucket,
                "index": item.index,
                "reason": _safe_reason(item.bucket, item.reason),
                "received_type": item.received_type,
                "expected_kind": item.expected_kind,
                "identifier_fields": list(item.identifier_fields),
            }
            for item in value.unresolved
        ],
    }


def safe_report(report: resource_import.PreviewReport | resource_import.CommitReport) -> dict[str, Any]:
    """Create the stable, path-free report shown by the app and CLI."""
    result: dict[str, Any] = {
        "version": 1,
        "phase": "commit" if isinstance(report, resource_import.CommitReport) else "preview",
        "summary": {
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
        },
        "files": [_safe_file_report(item) for item in report.files],
        "missing_source_entries": [
            {
                "library_key": item.library_key,
                "source_id": item.source_id,
                "latest_content_digest": item.latest_content_digest,
            }
            for item in report.missing_source_entries
        ],
    }
    if isinstance(report, resource_import.CommitReport):
        result["summary"].update({
            "recorded": report.total_recorded,
            "new_scene_revisions": report.total_new_scene_revisions,
            "unchanged_scene_revisions": report.total_unchanged_scene_revisions,
            "updated_scene_revisions": report.total_updated_scene_revisions,
            "new_auxiliary_revisions": report.total_new_auxiliary_revisions,
            "unchanged_auxiliary_revisions": report.total_unchanged_auxiliary_revisions,
        })
    return result


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
    "preview_import",
    "commit_import",
    "preview_to_dict",
    "preview_from_dict",
    "serialize_preview",
    "deserialize_preview",
    "safe_report",
    "write_report_artifact",
    "list_resource_libraries",
    "get_resource_library",
    "get_resource_revision",
)
