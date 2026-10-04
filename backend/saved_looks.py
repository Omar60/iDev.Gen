"""Versioned, self-contained personal looks backed by the wardrobe catalogue."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
import sys
import time
import unicodedata
from uuid import uuid4

import db
from backend import resource_store


class SavedLookError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _invalid() -> None:
    raise SavedLookError(422, "invalid_look", "Look data is invalid.")


def _write_conflict() -> None:
    raise SavedLookError(409, "look_write_conflict", "Saved look could not be saved.")


def _stored_data_invalid() -> None:
    raise SavedLookError(500, "look_data_invalid", "Saved look data is invalid.")


def _receipt_invalid() -> None:
    raise SavedLookError(500, "look_import_receipt_invalid", "Portable import receipt is inconsistent.")


_PORTABLE_JSON_INTEGER_DIGITS = 4300
_PORTABLE_SOURCES = {"manual", "photo", "assistant", "import"}
_SQLITE_INTEGER_MAX = (1 << 63) - 1
_PORTABLE_IMPORT_PREVIEW_TTL_SECONDS = 15 * 60
_PORTABLE_IMPORT_PREVIEW_PURPOSE = "portable-look-import-v1"
_LEGACY_IMPORT_PREVIEW_PURPOSE = "legacy-wardrobe-import-v1"


def _portable_digest(value: object) -> str:
    try:
        return resource_store.canonical_digest(value)
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        _invalid()


def _portable_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _portable_json_integer(value: str) -> int:
    configured_limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    digit_limit = min(_PORTABLE_JSON_INTEGER_DIGITS, configured_limit) if configured_limit else _PORTABLE_JSON_INTEGER_DIGITS
    if len(value.lstrip("-")) > digit_limit:
        raise ValueError("integer is too large")
    return int(value)


def _reject_json_constant(_value: str):
    raise ValueError("non-JSON numeric constant")


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _canonicalize_portable_annotation(value: object) -> dict | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"source", "image_sha256"}:
        _invalid()
    source = value["source"]
    image_sha256 = value["image_sha256"]
    if (
        not isinstance(source, str)
        or source not in _PORTABLE_SOURCES
        or (image_sha256 is not None and not _valid_sha256(image_sha256))
        or (image_sha256 is not None and source != "photo")
    ):
        _invalid()
    return {"source": source, "image_sha256": image_sha256}


def _canonicalize_portable_look(value: object) -> tuple[dict, str]:
    if type(value) is not dict or set(value) != {
        "schema_version", "look", "garments", "provenance",
    }:
        _invalid()
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _invalid()

    look = value["look"]
    if type(look) is not dict or set(look) != {
        "key", "version", "name", "appearance", "outfit",
    }:
        _invalid()
    if (
        not _valid_key(look["key"])
        or type(look["version"]) is not int
        or look["version"] < 1
        or not _valid_name(look["name"])
        or not isinstance(look["appearance"], str)
    ):
        _invalid()

    outfit = look["outfit"]
    garment_keys = []
    if outfit is not None:
        if type(outfit) is not dict or set(outfit) != {"key", "garment_keys"}:
            _invalid()
        garment_keys = outfit["garment_keys"]
        if (
            not _valid_key(outfit["key"])
            or type(garment_keys) is not list
            or not garment_keys
            or any(not _valid_key(key) for key in garment_keys)
            or len(garment_keys) != len(set(garment_keys))
        ):
            _invalid()

    raw_garments = value["garments"]
    if type(raw_garments) is not list:
        _invalid()
    by_key = {}
    for garment in raw_garments:
        if type(garment) is not dict or set(garment) != {"key", "wording", "aside"}:
            _invalid()
        if (
            not _valid_key(garment["key"])
            or garment["key"] in by_key
            or not _valid_wording(garment["wording"])
            or not _valid_wording(garment["aside"], allow_empty=True)
        ):
            _invalid()
        by_key[garment["key"]] = {
            "key": garment["key"],
            "wording": garment["wording"],
            "aside": garment["aside"],
        }
    if set(by_key) != set(garment_keys):
        _invalid()

    provenance = _canonicalize_portable_annotation(value["provenance"])

    canonical = {
        "schema_version": 1,
        "look": {
            "key": look["key"],
            "version": look["version"],
            "name": look["name"],
            "appearance": look["appearance"],
            "outfit": None if outfit is None else {
                "key": outfit["key"],
                "garment_keys": list(garment_keys),
            },
        },
        "garments": [by_key[key] for key in garment_keys],
        "provenance": provenance,
    }
    return canonical, _portable_digest(canonical)


def canonicalize_portable_look(value: object) -> dict:
    """Validate portable-look-v1 and order complete garments by reference."""
    return _canonicalize_portable_look(value)[0]


def parse_portable_look_json(raw: str | bytes) -> dict:
    """Parse closed portable-look-v1 JSON, rejecting duplicate and invalid values."""
    if type(raw) not in (str, bytes):
        _invalid()
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_portable_json_object,
            parse_int=_portable_json_integer,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        _invalid()
    return canonicalize_portable_look(value)


def portable_content_digest(value: object) -> str:
    """Digest the complete canonical pre-remap envelope, including annotation."""
    return _canonicalize_portable_look(value)[1]


def _parse_import_json(raw: str | bytes) -> object:
    if type(raw) not in (str, bytes):
        _invalid()
    try:
        return json.loads(
            raw,
            object_pairs_hook=_portable_json_object,
            parse_int=_portable_json_integer,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        _invalid()


def _canonicalize_legacy_wardrobe(value: object) -> dict:
    if (
        type(value) is not dict
        or not value
        or set(value) - {"garments", "outfits"}
    ):
        _invalid()

    garments = []
    seen_garments: set[str] = set()
    if "garments" in value:
        if type(value["garments"]) is not list:
            _invalid()
        for item in value["garments"]:
            if type(item) is not dict or set(item) - {"key", "wording", "aside"} or not {"key", "wording"}.issubset(item):
                _invalid()
            key, wording = item["key"], item["wording"]
            aside = item.get("aside", "")
            if not isinstance(key, str) or not isinstance(wording, str) or not isinstance(aside, str):
                _invalid()
            key, wording, aside = key.strip(), wording.strip(), aside.strip()
            if (
                not _valid_key(key)
                or key in seen_garments
                or not _valid_wording(wording)
                or not _valid_wording(aside, allow_empty=True)
            ):
                _invalid()
            seen_garments.add(key)
            garments.append({"key": key, "wording": wording, "aside": aside})

    outfits = []
    seen_outfits: set[str] = set()
    if "outfits" in value:
        if type(value["outfits"]) is not list:
            _invalid()
        for item in value["outfits"]:
            if type(item) is not dict or set(item) - {"key", "label", "garments"} or not {"key", "garments"}.issubset(item):
                _invalid()
            key, references = item["key"], item["garments"]
            label = item["label"] if "label" in item else None
            if not isinstance(key, str) or ("label" in item and not isinstance(label, str)):
                _invalid()
            key = key.strip()
            label = key if "label" not in item else label.strip()
            if not label:
                label = key
            if not _valid_key(key) or key in seen_outfits or not _valid_name(label):
                _invalid()
            if isinstance(references, str):
                references = references.split(",")
            if type(references) is not list or not references:
                _invalid()
            ordered_keys = []
            seen_references: set[str] = set()
            for reference in references:
                if not isinstance(reference, str):
                    _invalid()
                reference = reference.strip()
                if not _valid_key(reference) or reference in seen_references:
                    _invalid()
                seen_references.add(reference)
                ordered_keys.append(reference)
            seen_outfits.add(key)
            outfits.append({"key": key, "label": label, "garments": ordered_keys})

    if not garments and not outfits:
        _invalid()
    return {
        "garments": sorted(garments, key=lambda item: item["key"]),
        "outfits": sorted(outfits, key=lambda item: item["key"]),
    }


def canonicalize_legacy_wardrobe(value: object) -> dict:
    """Validate and normalize legacy garment/outfit JSON for reviewed import."""
    return _canonicalize_legacy_wardrobe(value)


def _canonicalize_look_import_document(value: object) -> tuple[str, dict]:
    if type(value) is not dict:
        _invalid()
    if "schema_version" in value:
        return "portable", canonicalize_portable_look(value)
    return "legacy", canonicalize_legacy_wardrobe(value)


def parse_look_import_preview_json(raw: str | bytes) -> tuple[str, dict]:
    return _canonicalize_look_import_document(_parse_import_json(raw))


def parse_look_import_commit_json(raw: str | bytes) -> tuple[str, dict, str, str, str]:
    request = _parse_import_json(raw)
    if type(request) is not dict or set(request) != {
        "envelope", "preview_token", "review_digest", "choice",
    }:
        _invalid()
    kind, document = _canonicalize_look_import_document(request["envelope"])
    token, review_digest, choice = request["preview_token"], request["review_digest"], request["choice"]
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 65536
        or not _valid_sha256(review_digest)
        or not isinstance(choice, str)
        or choice not in {"import", "new_version", "save_copy"}
    ):
        _invalid()
    return kind, document, token, review_digest, choice


def _validate_import_storage_version(envelope: dict) -> None:
    """Keep schema-valid portable integers inside SQLite's signed INTEGER range."""
    version = envelope["look"]["version"]
    if type(version) is not int or not 1 <= version <= _SQLITE_INTEGER_MAX:
        _invalid()


def portable_look_to_snapshot(value: object) -> dict:
    """Convert a validated envelope into a complete, ordered session snapshot."""
    envelope, _ = _canonicalize_portable_look(value)
    look = envelope["look"]
    outfit = look["outfit"]
    snapshot_outfit = None if outfit is None else {
        "outfit_key": outfit["key"],
        "garments": envelope["garments"],
    }
    return {
        "look_id": look["key"],
        "version": look["version"],
        "content_digest": _portable_digest({
            "appearance": look["appearance"],
            "outfit": snapshot_outfit,
        }),
        "appearance": look["appearance"],
        "outfit": snapshot_outfit,
    }


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _valid_key(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and value == value.strip()
        and not _has_control(value)
    )


def _valid_name(value: object) -> bool:
    return _valid_key(value)


def _valid_wording(value: object, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and value == value.strip()
    )


def _validate_snapshot(snapshot: object, row: dict) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "look_id", "version", "content_digest", "appearance", "outfit",
    }:
        _stored_data_invalid()
    if (
        not _valid_key(row["look_key"])
        or not _valid_name(row["name"])
        or type(row["version"]) is not int
        or row["version"] < 1
        or snapshot["look_id"] != row["look_key"]
        or type(snapshot["version"]) is not int
        or snapshot["version"] != row["version"]
        or not isinstance(snapshot["appearance"], str)
    ):
        _stored_data_invalid()

    outfit = snapshot["outfit"]
    if outfit is not None:
        if (
            not isinstance(outfit, dict)
            or set(outfit) != {"outfit_key", "garments"}
            or not _valid_key(outfit["outfit_key"])
            or not isinstance(outfit["garments"], list)
            or not outfit["garments"]
        ):
            _stored_data_invalid()
        seen: set[str] = set()
        for garment in outfit["garments"]:
            if (
                not isinstance(garment, dict)
                or set(garment) != {"key", "wording", "aside"}
                or not _valid_key(garment["key"])
                or garment["key"] in seen
                or not _valid_wording(garment["wording"])
                or not _valid_wording(garment["aside"], allow_empty=True)
            ):
                _stored_data_invalid()
            seen.add(garment["key"])

    digest = snapshot["content_digest"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or digest != resource_store.canonical_digest({
            "appearance": snapshot["appearance"],
            "outfit": outfit,
        })
    ):
        _stored_data_invalid()
    return snapshot


def _read_row(row: dict) -> dict:
    try:
        snapshot = _validate_snapshot(json.loads(row["snapshot_json"]), row)
    except SavedLookError:
        raise
    except (TypeError, ValueError, RecursionError, json.JSONDecodeError):
        _stored_data_invalid()
    return {
        "key": row["look_key"],
        "version": row["version"],
        "name": row["name"],
        "content_digest": snapshot["content_digest"],
        "appearance": snapshot["appearance"],
        "outfit": snapshot["outfit"],
    }


def list_latest() -> list[dict]:
    rows = db.q(
        """SELECT v.* FROM saved_look_version AS v
           JOIN (SELECT look_key, MAX(version) AS version
                 FROM saved_look_version GROUP BY look_key) AS latest
             ON latest.look_key = v.look_key AND latest.version = v.version
           ORDER BY v.look_key"""
    )
    return [
        {
            "key": view["key"],
            "version": view["version"],
            "name": view["name"],
            "content_digest": view["content_digest"],
        }
        for view in (_read_row(row) for row in rows)
    ]


def get_version(look_key: str, version: int) -> dict:
    if not _valid_key(look_key) or type(version) is not int or version < 1:
        _invalid()
    row = db.one(
        "SELECT * FROM saved_look_version WHERE look_key = ? AND version = ?",
        look_key, version,
    )
    if row is None:
        raise SavedLookError(404, "look_not_found", "Saved look was not found.")
    return _read_row(row)


def export_version(look_key: str, version: int) -> dict:
    """Export only the closed portable envelope from the immutable snapshot."""
    saved = get_version(look_key, version)
    provenance = _portable_annotation_for_destination(look_key, version)
    outfit = saved["outfit"]
    envelope = {
        "schema_version": 1,
        "look": {
            "key": saved["key"],
            "version": saved["version"],
            "name": saved["name"],
            "appearance": saved["appearance"],
            "outfit": None if outfit is None else {
                "key": outfit["outfit_key"],
                "garment_keys": [garment["key"] for garment in outfit["garments"]],
            },
        },
        "garments": [] if outfit is None else [
            {
                "key": garment["key"],
                "wording": garment["wording"],
                "aside": garment["aside"],
            }
            for garment in outfit["garments"]
        ],
        "provenance": provenance,
    }
    return canonicalize_portable_look(envelope)


def _history(look_key: str) -> list[dict]:
    rows = db.q(
        "SELECT * FROM saved_look_version WHERE look_key = ? ORDER BY version DESC",
        look_key,
    )
    return [_read_row(row) for row in rows]


def _portable_envelope(saved: dict, provenance: dict | None) -> dict:
    outfit = saved["outfit"]
    return canonicalize_portable_look({
        "schema_version": 1,
        "look": {
            "key": saved["key"],
            "version": saved["version"],
            "name": saved["name"],
            "appearance": saved["appearance"],
            "outfit": None if outfit is None else {
                "key": outfit["outfit_key"],
                "garment_keys": [item["key"] for item in outfit["garments"]],
            },
        },
        "garments": [] if outfit is None else outfit["garments"],
        "provenance": provenance,
    })


def _receipt_mapping(row: dict) -> tuple[dict, dict]:
    try:
        garments = json.loads(row["garment_mapping_json"], object_pairs_hook=_portable_json_object)
        outfits = json.loads(row["outfit_mapping_json"], object_pairs_hook=_portable_json_object)
    except (KeyError, TypeError, ValueError, RecursionError, UnicodeError):
        _receipt_invalid()
    if type(garments) is not dict or type(outfits) is not dict:
        _receipt_invalid()
    for mapping in (garments, outfits):
        if (
            any(not _valid_key(source) or not _valid_key(target) for source, target in mapping.items())
            or len(set(mapping.values())) != len(mapping)
        ):
            _receipt_invalid()
    return garments, outfits


def _receipt_annotation(row: dict) -> dict | None:
    raw = row.get("portable_annotation_json")
    if raw is None:
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_portable_json_object)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        _receipt_invalid()
    try:
        annotation = _canonicalize_portable_annotation(value)
    except SavedLookError:
        _receipt_invalid()
    if json.dumps(annotation, ensure_ascii=True, sort_keys=True, separators=(",", ":")) != raw:
        _receipt_invalid()
    return annotation


def _receipt_destination_digest(
    row: dict,
    saved: dict,
    envelope: dict,
    garment_mapping: dict,
    outfit_mapping: dict,
) -> str:
    return _portable_digest({
        "original": {
            "key": row["original_look_key"],
            "version": row["original_version"],
            "portable_content_digest": row["portable_content_digest"],
        },
        "destination": {
            "key": saved["key"],
            "version": saved["version"],
            "name": saved["name"],
            "snapshot": {
                "content_digest": saved["content_digest"],
                "appearance": saved["appearance"],
                "outfit": saved["outfit"],
            },
        },
        "local_origin": row["local_origin"],
        "portable_annotation": envelope["provenance"],
        "mapping": {"garments": garment_mapping, "outfits": outfit_mapping},
    })


def _verify_import_receipt(row: dict) -> dict:
    if (
        not _valid_key(row.get("original_look_key"))
        or type(row.get("original_version")) is not int
        or row["original_version"] < 1
        or not _valid_sha256(row.get("portable_content_digest"))
        or not _valid_key(row.get("destination_look_key"))
        or type(row.get("destination_version")) is not int
        or row["destination_version"] < 1
        or row.get("local_origin") != "import"
        or not _valid_sha256(row.get("destination_digest"))
    ):
        _receipt_invalid()
    saved_row = db.one(
        "SELECT * FROM saved_look_version WHERE look_key = ? AND version = ?",
        row["destination_look_key"], row["destination_version"],
    )
    if saved_row is None:
        _receipt_invalid()
    try:
        saved = _read_row(saved_row)
    except SavedLookError:
        _receipt_invalid()
    garment_mapping, outfit_mapping = _receipt_mapping(row)
    annotation = _receipt_annotation(row)
    local_garments = set() if saved["outfit"] is None else {
        item["key"] for item in saved["outfit"]["garments"]
    }
    if set(garment_mapping.values()) != local_garments:
        _receipt_invalid()
    local_outfits = set() if saved["outfit"] is None else {saved["outfit"]["outfit_key"]}
    if set(outfit_mapping.values()) != local_outfits or len(outfit_mapping) != len(local_outfits):
        _receipt_invalid()
    try:
        envelope = _portable_envelope(saved, annotation)
        actual_digest = _receipt_destination_digest(row, saved, envelope, garment_mapping, outfit_mapping)
    except SavedLookError:
        _receipt_invalid()
    if actual_digest != row["destination_digest"]:
        _receipt_invalid()
    return {
        "original_identity": {"key": row["original_look_key"], "version": row["original_version"]},
        "portable_content_digest": row["portable_content_digest"],
        "look": saved,
        "portable_annotation": annotation,
        "mapping": {"garments": garment_mapping, "outfits": outfit_mapping},
        "envelope": envelope,
    }


def _portable_annotation_for_destination(look_key: str, version: int) -> dict | None:
    photo = db.one(
        "SELECT image_sha256 FROM saved_look_photo_evidence WHERE look_key = ? AND version = ?",
        look_key,
        version,
    )
    if photo is not None:
        return {"source": "photo", "image_sha256": photo["image_sha256"]}
    verified = _import_receipts_for_destination(look_key, version)
    if not verified:
        return None
    annotations = [item["portable_annotation"] for item in verified]
    if any(value != annotations[0] for value in annotations[1:]):
        _stored_data_invalid()
    return annotations[0]


def save_photo_evidence(
    saved: dict,
    *,
    metadata: dict,
    request_projection: dict,
    output: dict,
    corrections: dict,
) -> None:
    """Persist safe local evidence inside the saved-look transaction."""
    if not getattr(db, "_tx_depth", 0):
        raise RuntimeError("photo evidence must share the saved-look transaction")
    if set(metadata) != {"sha256", "media_type", "byte_count", "width", "height"}:
        raise SavedLookError(422, "photo_evidence_invalid", "Photo extraction evidence is invalid.")
    digest = metadata["sha256"]
    if (
        not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or metadata["media_type"] not in {"image/jpeg", "image/png", "image/webp"}
        or type(metadata["byte_count"]) is not int
        or not 0 < metadata["byte_count"] <= 10 * 1024 * 1024
        or type(metadata["width"]) is not int
        or type(metadata["height"]) is not int
        or metadata["width"] < 1
        or metadata["height"] < 1
        or metadata["width"] * metadata["height"] > 25_000_000
        or not isinstance(request_projection, dict)
        or set(request_projection) != {"messages", "model", "parameters"}
        or not isinstance(output, dict)
        or set(output) != {"appearance", "garments", "unresolved"}
        or not isinstance(corrections, dict)
    ):
        raise SavedLookError(422, "photo_evidence_invalid", "Photo extraction evidence is invalid.")
    try:
        values = [
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            for value in (request_projection, output, corrections)
        ]
    except (TypeError, ValueError):
        raise SavedLookError(422, "photo_evidence_invalid", "Photo extraction evidence is invalid.") from None
    serialized = "\n".join(values).casefold()
    if any(marker in serialized for marker in ("data:", "https://", "http://", "authorization:", "bearer ")):
        raise SavedLookError(422, "photo_evidence_invalid", "Photo extraction evidence is invalid.")
    db.run(
        """INSERT INTO saved_look_photo_evidence
           (look_key, version, image_sha256, media_type, byte_count, width, height,
            request_projection_json, output_json, corrections_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        saved["key"], saved["version"], digest, metadata["media_type"],
        metadata["byte_count"], metadata["width"], metadata["height"],
        *values, db.now(),
    )


def get_photo_evidence(look_key: str, version: int) -> dict:
    saved = get_version(look_key, version)
    row = db.one(
        "SELECT * FROM saved_look_photo_evidence WHERE look_key = ? AND version = ?",
        saved["key"],
        saved["version"],
    )
    if row is None:
        raise SavedLookError(404, "photo_evidence_not_found", "This saved look has no photo extraction evidence.")
    try:
        return {
            "source": {
                "sha256": row["image_sha256"],
                "media_type": row["media_type"],
                "byte_count": row["byte_count"],
                "width": row["width"],
                "height": row["height"],
            },
            "request": json.loads(row["request_projection_json"]),
            "output": json.loads(row["output_json"]),
            "corrections": json.loads(row["corrections_json"]),
        }
    except (TypeError, ValueError):
        raise SavedLookError(500, "photo_evidence_invalid", "Saved photo evidence is invalid.") from None


def _import_receipts_for_destination(look_key: str, version: int) -> list[dict]:
    rows = db.q(
        """SELECT * FROM saved_look_import_receipt
           WHERE destination_look_key = ? AND destination_version = ? ORDER BY id""",
        look_key, version,
    )
    return [_verify_import_receipt(row) for row in rows]


def parse_portable_look_commit_json(raw: str | bytes) -> tuple[dict, str, str, str]:
    """Parse the strict commit wrapper while rejecting duplicate JSON keys."""
    if type(raw) not in (str, bytes):
        _invalid()
    try:
        request = json.loads(
            raw,
            object_pairs_hook=_portable_json_object,
            parse_int=_portable_json_integer,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        _invalid()
    if type(request) is not dict or set(request) != {
        "envelope", "preview_token", "review_digest", "choice",
    }:
        _invalid()
    envelope = canonicalize_portable_look(request["envelope"])
    token = request["preview_token"]
    review_digest = request["review_digest"]
    choice = request["choice"]
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 65536
        or not _valid_sha256(review_digest)
        or not isinstance(choice, str)
        or choice not in {"import", "new_version", "save_copy"}
    ):
        _invalid()
    return envelope, token, review_digest, choice


def _portable_review_digest(plan: dict, state_digest: str, content_digest: str) -> str:
    return _portable_digest({
        "purpose": _PORTABLE_IMPORT_PREVIEW_PURPOSE,
        "plan": plan,
        "store_state_digest": state_digest,
        "portable_content_digest": content_digest,
    })


def _portable_preview_key(*, create: bool) -> bytes:
    from backend import resource_service
    try:
        return resource_service._attestation_key(create=create)
    except (OSError, ValueError):
        raise SavedLookError(500, "look_import_preview_unavailable", "Import preview could not be created.") from None


def _preview_token(plan: dict, state_digest: str, content_digest: str, key: bytes) -> tuple[str, str]:
    review_digest = _portable_review_digest(plan, state_digest, content_digest)
    payload = {
        "version": 1,
        "purpose": _PORTABLE_IMPORT_PREVIEW_PURPOSE,
        "expires_at": int(time.time()) + _PORTABLE_IMPORT_PREVIEW_TTL_SECONDS,
        "store_state_digest": state_digest,
        "portable_content_digest": content_digest,
        "review_digest": review_digest,
        "mode": plan["mode"],
        "destination": plan["destination"],
        "choices": plan["choices"],
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(key, b"portable-look-import-v1\0" + encoded, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=") + "." + signature
    return token, review_digest


def _read_preview_token(token: str) -> dict:
    try:
        encoded_text, signature = token.split(".", 1)
        if (
            not encoded_text
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in encoded_text)
            or len(signature) != 64
            or any(char not in "0123456789abcdef" for char in signature)
        ):
            raise ValueError("invalid token")
        padded = encoded_text + "=" * ((4 - len(encoded_text) % 4) % 4)
        encoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        expected = hmac.new(
            _portable_preview_key(create=False),
            b"portable-look-import-v1\0" + encoded,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid token")
        payload = json.loads(encoded, object_pairs_hook=_portable_json_object)
        if (
            type(payload) is not dict
            or set(payload) != {
                "version", "purpose", "expires_at", "store_state_digest",
                "portable_content_digest", "review_digest", "mode",
                "destination", "choices",
            }
            or type(payload["version"]) is not int
            or payload["version"] != 1
            or payload["purpose"] != _PORTABLE_IMPORT_PREVIEW_PURPOSE
            or type(payload["expires_at"]) is not int
            or payload["expires_at"] <= int(time.time())
            or not _valid_sha256(payload["store_state_digest"])
            or not _valid_sha256(payload["portable_content_digest"])
            or not _valid_sha256(payload["review_digest"])
            or payload["mode"] not in {"receipt_replay", "local_equality", "choice_required", "import"}
            or not _valid_review_destination(payload["destination"])
            or not _valid_review_choices(payload["mode"], payload["choices"])
        ):
            raise ValueError("invalid token")
        return payload
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError, UnicodeError):
        raise SavedLookError(409, "look_import_preview_invalid", "Import preview is invalid or expired.") from None


def _valid_review_destination(value: object) -> bool:
    return value is None or (
        type(value) is dict
        and set(value) == {"key", "version"}
        and _valid_key(value["key"])
        and type(value["version"]) is int
        and 1 <= value["version"] <= _SQLITE_INTEGER_MAX
    )


def _valid_review_choices(mode: str, value: object) -> bool:
    if type(value) is not list or not value:
        return False
    if mode == "choice_required":
        if not all(
            type(option) is dict
            and set(option) == {"choice", "destination"}
            and option["choice"] in {"new_version", "save_copy"}
            and _valid_review_destination(option["destination"])
            and option["destination"] is not None
            for option in value
        ):
            return False
        reviewed_choices = [option["choice"] for option in value]
        return reviewed_choices in (["new_version", "save_copy"], ["save_copy"])
    return value == ["import"]


def _portable_store_state_digest() -> str:
    state = {
        "looks": db.q(
            "SELECT look_key, version, name, snapshot_json, created_at FROM saved_look_version ORDER BY look_key, version"
        ),
        "garments": db.q(
            "SELECT key, wording, aside, retired_at, created_at FROM garment ORDER BY key"
        ),
        "outfits": db.q(
            "SELECT key, label, garments, retired_at, created_at FROM outfit ORDER BY key"
        ),
        "receipts": db.q("SELECT * FROM saved_look_import_receipt ORDER BY id"),
    }
    return _portable_digest(state)


def _catalogue_index() -> dict:
    garment_rows = db.q("SELECT key, wording, aside FROM garment ORDER BY key")
    outfit_rows = db.q("SELECT key, garments FROM outfit ORDER BY key")
    look_rows = db.q("SELECT * FROM saved_look_version ORDER BY look_key, version")
    garments: dict[str, set[tuple[str, str]]] = {}
    outfits: dict[str, set[str]] = {}
    look_keys: set[str] = set()
    live_garments: dict[str, dict] = {}
    for row in garment_rows:
        if (
            not _valid_key(row["key"])
            or not _valid_wording(row["wording"])
            or not _valid_wording(row["aside"], allow_empty=True)
        ):
            _stored_data_invalid()
        definition = {"key": row["key"], "wording": row["wording"], "aside": row["aside"]}
        live_garments[row["key"]] = definition
        garments.setdefault(row["key"], set()).add((row["wording"], row["aside"]))

    stored_looks = [_read_row(row) for row in look_rows]
    for look in stored_looks:
        look_keys.add(look["key"])
        outfit = look["outfit"]
        if outfit is None:
            continue
        for garment in outfit["garments"]:
            garments.setdefault(garment["key"], set()).add((garment["wording"], garment["aside"]))
        outfits.setdefault(outfit["outfit_key"], set()).add(
            _portable_outfit_fingerprint(outfit["garments"])
        )

    for row in outfit_rows:
        if not _valid_key(row["key"]):
            _stored_data_invalid()
        keys = row["garments"].split(",") if row["garments"] else []
        if not keys or any(not _valid_key(key) for key in keys) or len(set(keys)) != len(keys):
            outfits.setdefault(row["key"], set()).add("unresolved")
            continue
        if any(key not in live_garments for key in keys):
            outfits.setdefault(row["key"], set()).add("unresolved")
            continue
        outfits.setdefault(row["key"], set()).add(
            _portable_outfit_fingerprint([live_garments[key] for key in keys])
        )
    return {
        "garments": garments,
        "outfits": outfits,
        "look_keys": look_keys,
        "garment_rows": live_garments,
        "outfit_rows": {row["key"]: row for row in outfit_rows},
        "looks": stored_looks,
    }


def _portable_outfit_fingerprint(garments: list[dict]) -> str:
    return _portable_digest({"garments": garments})


def _generated_key(
    prefix: str, reserved: set[str], key: bytes, seed: dict,
    *, purpose: str = _PORTABLE_IMPORT_PREVIEW_PURPOSE,
) -> str:
    attempt = 0
    while True:
        seed_bytes = json.dumps(
            {"purpose": purpose, "seed": seed, "attempt": attempt},
            ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        suffix = hmac.new(key, seed_bytes, hashlib.sha256).hexdigest()[:32]
        candidate = f"{prefix}-{suffix}"
        if candidate not in reserved:
            reserved.add(candidate)
            return candidate
        attempt += 1


def _portable_mapping(
    envelope: dict, index: dict, content_digest: str, state_digest: str, key: bytes,
) -> tuple[dict, list[dict], list[dict]]:
    garment_mapping: dict[str, str] = {}
    outfit_mapping: dict[str, str] = {}
    remapped: list[dict] = []
    reserved_garments = set(index["garments"])
    local_garments = []
    for garment in envelope["garments"]:
        source_key = garment["key"]
        candidates = index["garments"].get(source_key, set())
        reason = "unrepresentable_key" if "," in source_key else (
            "key_conflict" if candidates and candidates != {(garment["wording"], garment["aside"])} else ""
        )
        target_key = _generated_key(
            "garment-import", reserved_garments, key,
            {"kind": "garment", "source_key": source_key, "content_digest": content_digest, "state_digest": state_digest},
        ) if reason else source_key
        garment_mapping[source_key] = target_key
        if reason:
            remapped.append({"kind": "garment", "from": source_key, "to": target_key, "reason": reason})
        local = {"key": target_key, "wording": garment["wording"], "aside": garment["aside"]}
        local_garments.append(local)
        index["garments"].setdefault(target_key, set()).add((local["wording"], local["aside"]))

    outfit = envelope["look"]["outfit"]
    if outfit is not None:
        desired = [dict(item) for item in local_garments]
        desired_fingerprint = _portable_outfit_fingerprint(desired)
        candidates = index["outfits"].get(outfit["key"], set())
        if candidates and candidates != {desired_fingerprint}:
            target_key = _generated_key(
                "outfit-import", set(index["outfits"]), key,
                {"kind": "outfit", "source_key": outfit["key"], "content_digest": content_digest, "state_digest": state_digest},
            )
            outfit_mapping[outfit["key"]] = target_key
            remapped.append({"kind": "outfit", "from": outfit["key"], "to": target_key, "reason": "key_conflict"})
        else:
            outfit_mapping[outfit["key"]] = outfit["key"]
            target_key = outfit["key"]
        index["outfits"].setdefault(target_key, set()).add(desired_fingerprint)
    return {"garments": garment_mapping, "outfits": outfit_mapping}, local_garments, remapped


def _find_import_receipt(key: str, version: int, digest: str) -> dict | None:
    row = db.one(
        """SELECT * FROM saved_look_import_receipt
           WHERE original_look_key = ? AND original_version = ? AND portable_content_digest = ?""",
        key, version, digest,
    )
    return None if row is None else _verify_import_receipt(row)


def _source_import_receipts(key: str, version: int) -> list[dict]:
    rows = db.q(
        """SELECT * FROM saved_look_import_receipt
           WHERE original_look_key = ? AND original_version = ? ORDER BY id""",
        key, version,
    )
    return [_verify_import_receipt(row) for row in rows]


def _local_equal_destination(envelope: dict) -> dict | None:
    look = envelope["look"]
    row = db.one(
        "SELECT * FROM saved_look_version WHERE look_key = ? AND version = ?",
        look["key"], look["version"],
    )
    if row is None:
        return None
    saved = _read_row(row)
    exported = _portable_envelope(saved, _portable_annotation_for_destination(look["key"], look["version"]))
    return saved if _portable_digest(exported) == _portable_digest(envelope) else None


def _build_portable_import_plan(envelope: dict, content_digest: str, state_digest: str, key: bytes) -> dict:
    original = envelope["look"]
    source_receipts = _source_import_receipts(original["key"], original["version"])
    receipt = next((item for item in source_receipts if item["portable_content_digest"] == content_digest), None)
    if receipt is not None:
        plan = {
            "mode": "receipt_replay",
            "status": "already_imported",
            "original_identity": {"key": original["key"], "version": original["version"]},
            "destination": {"key": receipt["look"]["key"], "version": receipt["look"]["version"]},
            "choices": ["import"],
            "mapping": receipt["mapping"],
            "remapped": [],
            "portable_annotation": receipt["portable_annotation"],
            "no_op": True,
        }
    else:
        local_equal = _local_equal_destination(envelope)
        if local_equal is not None:
            outfit = envelope["look"]["outfit"]
            identity_mapping = {
                "garments": {item["key"]: item["key"] for item in envelope["garments"]},
                "outfits": {} if outfit is None else {outfit["key"]: outfit["key"]},
            }
            plan = {
                "mode": "local_equality",
                "status": "already_equal",
                "original_identity": {"key": original["key"], "version": original["version"]},
                "destination": {"key": local_equal["key"], "version": local_equal["version"]},
                "choices": ["import"],
                "mapping": identity_mapping,
                "remapped": [],
                "portable_annotation": envelope["provenance"],
                "no_op": True,
            }
        else:
            index = _catalogue_index()
            mapping, _local_garments, remapped = _portable_mapping(
                envelope, index, content_digest, state_digest, key,
            )
            history = [item for item in index["looks"] if item["key"] == original["key"]]
            latest = max((item["version"] for item in history), default=0)
            occupied = any(item["version"] == original["version"] for item in history)
            conflict = occupied or (latest > 0 and original["version"] <= latest) or bool(source_receipts)
            if conflict:
                reserved_look_keys = set(index["look_keys"])
                copy_key = _generated_key(
                    "look-copy", reserved_look_keys, key,
                    {"kind": "look", "source_key": original["key"], "content_digest": content_digest, "state_digest": state_digest},
                )
                choices = []
                if latest < _SQLITE_INTEGER_MAX:
                    choices.append({
                        "choice": "new_version",
                        "destination": {"key": original["key"], "version": latest + 1},
                    })
                choices.append({
                    "choice": "save_copy",
                    "destination": {"key": copy_key, "version": 1},
                })
                plan = {
                    "mode": "choice_required",
                    "status": "choice_required",
                    "original_identity": {"key": original["key"], "version": original["version"]},
                    "destination": None,
                    "choices": choices,
                    "mapping": mapping,
                    "remapped": remapped,
                    "portable_annotation": envelope["provenance"],
                    "no_op": False,
                }
            else:
                plan = {
                    "mode": "import",
                    "status": "ready",
                    "original_identity": {"key": original["key"], "version": original["version"]},
                    "destination": {"key": original["key"], "version": original["version"]},
                    "choices": ["import"],
                    "mapping": mapping,
                    "remapped": remapped,
                    "portable_annotation": envelope["provenance"],
                    "no_op": False,
                }
    return plan


def preview_portable_import(value: object) -> dict:
    envelope, content_digest = _canonicalize_portable_look(value)
    _validate_import_storage_version(envelope)
    with db.transaction():
        state_digest = _portable_store_state_digest()
        key = _portable_preview_key(create=True)
        plan = _build_portable_import_plan(envelope, content_digest, state_digest, key)
        token, review_digest = _preview_token(plan, state_digest, content_digest, key)
        return {**plan, "portable_content_digest": content_digest, "review_digest": review_digest, "preview_token": token}


def _commit_destination(plan: dict, choice: str) -> dict:
    if plan["mode"] in {"receipt_replay", "local_equality"}:
        if choice != "import":
            raise SavedLookError(409, "look_import_choice_invalid", "The reviewed import choice is no longer available.")
        return _checked_import_destination(plan["destination"])
    if plan["mode"] == "import":
        if choice != "import":
            raise SavedLookError(409, "look_import_choice_invalid", "The reviewed import choice is no longer available.")
        return _checked_import_destination(plan["destination"])
    for option in plan["choices"]:
        if isinstance(option, dict) and option.get("choice") == choice:
            return _checked_import_destination(option["destination"])
    raise SavedLookError(409, "look_import_choice_invalid", "The reviewed import choice is no longer available.")


def _checked_import_destination(destination: object) -> dict:
    if not _valid_review_destination(destination) or destination is None:
        _write_conflict()
    return destination


def _require_reviewed_destination(reviewed: dict, actual: dict) -> None:
    if reviewed != actual:
        raise SavedLookError(409, "look_import_choice_invalid", "The reviewed choice does not match the imported destination.")


def _commit_portable_rows(envelope: dict, plan: dict, destination: dict, content_digest: str) -> dict:
    _checked_import_destination(destination)
    mapping = plan["mapping"]
    garment_mapping = mapping["garments"]
    outfit_mapping = mapping["outfits"]
    source_look = envelope["look"]
    local_garments = [
        {"key": garment_mapping[item["key"]], "wording": item["wording"], "aside": item["aside"]}
        for item in envelope["garments"]
    ]
    for garment in local_garments:
        row = db.one("SELECT wording, aside FROM garment WHERE key = ?", garment["key"])
        if row is None:
            db.run(
                "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
                garment["key"], garment["wording"], garment["aside"], db.now(),
            )
        elif row["wording"] != garment["wording"] or row["aside"] != garment["aside"]:
            raise SavedLookError(409, "look_import_preview_stale", "The catalogue changed; preview the import again.")

    local_outfit = None
    outfit = source_look["outfit"]
    if outfit is not None:
        outfit_key = outfit_mapping[outfit["key"]]
        local_outfit = {"outfit_key": outfit_key, "garments": local_garments}
        row = db.one("SELECT garments FROM outfit WHERE key = ?", outfit_key)
        joined = ",".join(item["key"] for item in local_garments)
        if row is None:
            db.run(
                "INSERT INTO outfit (key, label, garments, created_at) VALUES (?, ?, ?, ?)",
                outfit_key, source_look["name"], joined, db.now(),
            )
        elif row["garments"] != joined:
            raise SavedLookError(409, "look_import_preview_stale", "The catalogue changed; preview the import again.")

    snapshot_content = {"appearance": source_look["appearance"], "outfit": local_outfit}
    snapshot = {
        "look_id": destination["key"],
        "version": destination["version"],
        "content_digest": _portable_digest(snapshot_content),
        **snapshot_content,
    }
    created_at = db.now()
    db.run(
        """INSERT INTO saved_look_version
           (look_key, version, name, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?)""",
        destination["key"], destination["version"], source_look["name"],
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), created_at,
    )
    saved = {
        "key": destination["key"],
        "version": destination["version"],
        "name": source_look["name"],
        "content_digest": snapshot["content_digest"],
        "appearance": snapshot["appearance"],
        "outfit": snapshot["outfit"],
    }
    stored_envelope = _portable_envelope(saved, envelope["provenance"])
    row_for_digest = {
        "original_look_key": source_look["key"],
        "original_version": source_look["version"],
        "portable_content_digest": content_digest,
        "destination_look_key": destination["key"],
        "destination_version": destination["version"],
        "local_origin": "import",
    }
    receipt_digest = _receipt_destination_digest(
        row_for_digest, saved, stored_envelope, garment_mapping, outfit_mapping,
    )
    annotation_json = None if envelope["provenance"] is None else json.dumps(
        envelope["provenance"], ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    db.run(
        """INSERT INTO saved_look_import_receipt
           (original_look_key, original_version, portable_content_digest,
            destination_look_key, destination_version, local_origin,
            portable_annotation_json, garment_mapping_json, outfit_mapping_json,
            destination_digest, created_at)
           VALUES (?, ?, ?, ?, ?, 'import', ?, ?, ?, ?, ?)""",
        source_look["key"], source_look["version"], content_digest,
        destination["key"], destination["version"], annotation_json,
        json.dumps(garment_mapping, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        json.dumps(outfit_mapping, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        receipt_digest, created_at,
    )
    return saved


def commit_portable_import(value: object, preview_token: str, review_digest: str, choice: str) -> dict:
    envelope, content_digest = _canonicalize_portable_look(value)
    _validate_import_storage_version(envelope)
    payload = _read_preview_token(preview_token)
    if (
        payload["portable_content_digest"] != content_digest
        or payload["review_digest"] != review_digest
    ):
        raise SavedLookError(409, "look_import_preview_mismatch", "Import content or review changed; preview the import again.")
    try:
        with db.transaction():
            reviewed_plan = {
                "mode": payload["mode"],
                "destination": payload["destination"],
                "choices": payload["choices"],
            }
            reviewed_destination = _commit_destination(reviewed_plan, choice)
            original = envelope["look"]
            receipt = _find_import_receipt(original["key"], original["version"], content_digest)
            if receipt is not None:
                _require_reviewed_destination(reviewed_destination, {
                    "key": receipt["look"]["key"],
                    "version": receipt["look"]["version"],
                })
                return {
                    "look": receipt["look"],
                    "local_origin": "import",
                    "portable_annotation": receipt["portable_annotation"],
                    "mapping": receipt["mapping"],
                    "no_op": True,
                }
            equal = _local_equal_destination(envelope)
            if equal is not None:
                _require_reviewed_destination(reviewed_destination, {
                    "key": equal["key"],
                    "version": equal["version"],
                })
                existing_receipts = _import_receipts_for_destination(equal["key"], equal["version"])
                outfit = envelope["look"]["outfit"]
                identity_mapping = {
                    "garments": {item["key"]: item["key"] for item in envelope["garments"]},
                    "outfits": {} if outfit is None else {outfit["key"]: outfit["key"]},
                }
                return {
                    "look": equal,
                    "local_origin": "import" if existing_receipts else "existing",
                    "portable_annotation": envelope["provenance"],
                    "mapping": identity_mapping,
                    "no_op": True,
                }
            state_digest = _portable_store_state_digest()
            if state_digest != payload["store_state_digest"]:
                raise SavedLookError(409, "look_import_preview_stale", "The look store changed; preview the import again.")
            plan = _build_portable_import_plan(
                envelope,
                content_digest,
                state_digest,
                _portable_preview_key(create=False),
            )
            if _portable_review_digest(plan, state_digest, content_digest) != review_digest:
                raise SavedLookError(409, "look_import_preview_stale", "The reviewed import mapping changed; preview the import again.")
            destination = _commit_destination(plan, choice)
            saved = _commit_portable_rows(envelope, plan, destination, content_digest)
            receipt = _find_import_receipt(original["key"], original["version"], content_digest)
            if receipt is None:
                _stored_data_invalid()
            return {
                "look": saved,
                "local_origin": "import",
                "portable_annotation": receipt["portable_annotation"],
                "mapping": receipt["mapping"],
                "no_op": False,
            }
    except SavedLookError:
        raise
    except sqlite3.IntegrityError:
        _write_conflict()
    except sqlite3.Error:
        _write_conflict()


def _legacy_import_invalid_receipt() -> None:
    raise SavedLookError(500, "look_import_receipt_invalid", "Legacy import receipt is inconsistent.")


def _legacy_import_stale() -> None:
    raise SavedLookError(409, "look_import_preview_stale", "The look store changed; preview the import again.")


def _legacy_import_choice_invalid() -> None:
    raise SavedLookError(409, "look_import_choice_invalid", "The reviewed import choice is no longer available.")


def _legacy_store_state_digest() -> str:
    return _portable_digest({
        "portable_store": _portable_store_state_digest(),
        "legacy_receipts": db.q(
            "SELECT * FROM saved_look_legacy_import_receipt ORDER BY legacy_content_digest"
        ),
    })


def _legacy_review_digest(plan: dict, state_digest: str, content_digest: str) -> str:
    return _portable_digest({
        "purpose": _LEGACY_IMPORT_PREVIEW_PURPOSE,
        "plan": plan,
        "store_state_digest": state_digest,
        "legacy_content_digest": content_digest,
    })


def _legacy_preview_token(
    plan: dict, state_digest: str, content_digest: str, key: bytes,
) -> tuple[str, str]:
    review_digest = _legacy_review_digest(plan, state_digest, content_digest)
    payload = {
        "version": 1,
        "purpose": _LEGACY_IMPORT_PREVIEW_PURPOSE,
        "expires_at": int(time.time()) + _PORTABLE_IMPORT_PREVIEW_TTL_SECONDS,
        "store_state_digest": state_digest,
        "legacy_content_digest": content_digest,
        "review_digest": review_digest,
        "mode": plan["mode"],
        "choices": plan["choices"],
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        key, _LEGACY_IMPORT_PREVIEW_PURPOSE.encode("ascii") + b"\0" + encoded, hashlib.sha256,
    ).hexdigest()
    token = base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=") + "." + signature
    return token, review_digest


def _read_legacy_preview_token(token: str) -> dict:
    try:
        encoded_text, signature = token.split(".", 1)
        if (
            not encoded_text
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in encoded_text)
            or len(signature) != 64
            or any(char not in "0123456789abcdef" for char in signature)
        ):
            raise ValueError("invalid token")
        encoded = base64.urlsafe_b64decode(
            (encoded_text + "=" * ((4 - len(encoded_text) % 4) % 4)).encode("ascii")
        )
        expected = hmac.new(
            _portable_preview_key(create=False),
            _LEGACY_IMPORT_PREVIEW_PURPOSE.encode("ascii") + b"\0" + encoded,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid token")
        payload = json.loads(encoded, object_pairs_hook=_portable_json_object)
        if (
            type(payload) is not dict
            or set(payload) != {
                "version", "purpose", "expires_at", "store_state_digest",
                "legacy_content_digest", "review_digest", "mode", "choices",
            }
            or type(payload["version"]) is not int
            or payload["version"] != 1
            or payload["purpose"] != _LEGACY_IMPORT_PREVIEW_PURPOSE
            or type(payload["expires_at"]) is not int
            or payload["expires_at"] <= int(time.time())
            or not _valid_sha256(payload["store_state_digest"])
            or not _valid_sha256(payload["legacy_content_digest"])
            or not _valid_sha256(payload["review_digest"])
            or payload["mode"] not in {"receipt_replay", "choice_required", "import", "already_equal"}
            or type(payload["choices"]) is not list
            or payload["choices"] != (["save_copy"] if payload["mode"] == "choice_required" else ["import"])
        ):
            raise ValueError("invalid token")
        return payload
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError, UnicodeError):
        raise SavedLookError(409, "look_import_preview_invalid", "Import preview is invalid or expired.") from None


def _legacy_remapped(mapping: dict) -> list[dict]:
    remapped = []
    for kind in ("garments", "outfits"):
        for source_key, destination_key in sorted(mapping[kind].items()):
            if source_key != destination_key:
                reason = "unrepresentable_key" if kind == "garments" and "," in source_key else "key_conflict"
                remapped.append({
                    "kind": kind[:-1], "from": source_key, "to": destination_key, "reason": reason,
                })
    return remapped


def _legacy_mapping_from_receipt(receipt: dict, document: dict) -> dict:
    try:
        mapping = {
            "garments": json.loads(receipt["garment_mapping_json"], object_pairs_hook=_portable_json_object),
            "outfits": json.loads(receipt["outfit_mapping_json"], object_pairs_hook=_portable_json_object),
        }
    except (KeyError, TypeError, ValueError, RecursionError, UnicodeError):
        _legacy_import_invalid_receipt()
    source_garments = {item["key"] for item in document["garments"]}
    source_garments.update(key for outfit in document["outfits"] for key in outfit["garments"])
    source_outfits = {item["key"] for item in document["outfits"]}
    if (
        type(mapping["garments"]) is not dict
        or type(mapping["outfits"]) is not dict
        or set(mapping["garments"]) != source_garments
        or set(mapping["outfits"]) != source_outfits
        or any(not _valid_key(key) or not _valid_key(value) for key, value in mapping["garments"].items())
        or any(not _valid_key(key) or not _valid_key(value) for key, value in mapping["outfits"].items())
        or len(set(mapping["garments"].values())) != len(mapping["garments"])
        or len(set(mapping["outfits"].values())) != len(mapping["outfits"])
    ):
        _legacy_import_invalid_receipt()
    return mapping


def _legacy_destination_projection(
    document: dict, mapping: dict, *, receipt: bool = False,
) -> dict:
    def invalid():
        if receipt:
            _legacy_import_invalid_receipt()
        _legacy_import_stale()

    source_definitions = {item["key"]: item for item in document["garments"]}
    source_outfits = {item["key"]: item for item in document["outfits"]}
    resolved_garments = []
    for source_key, destination_key in sorted(mapping["garments"].items()):
        row = db.one(
            "SELECT key, wording, aside FROM garment WHERE key = ?", destination_key,
        )
        if row is None or not _valid_key(row["key"]) or not _valid_wording(row["wording"]):
            invalid()
        if not _valid_wording(row["aside"], allow_empty=True):
            invalid()
        definition = {
            "source_key": source_key,
            "key": row["key"],
            "wording": row["wording"],
            "aside": row["aside"],
        }
        expected = source_definitions.get(source_key)
        if expected is not None and (
            definition["wording"] != expected["wording"]
            or definition["aside"] != expected["aside"]
        ):
            invalid()
        resolved_garments.append(definition)

    resolved_outfits = []
    for source_key, destination_key in sorted(mapping["outfits"].items()):
        source = source_outfits[source_key]
        garment_keys = [mapping["garments"][key] for key in source["garments"]]
        row = db.one(
            "SELECT key, label, garments FROM outfit WHERE key = ?", destination_key,
        )
        if (
            row is None
            or not _valid_key(row["key"])
            or not isinstance(row["label"], str)
            or row["label"] != source["label"]
            or row["garments"] != ",".join(garment_keys)
        ):
            invalid()
        resolved_outfits.append({
            "source_key": source_key,
            "key": row["key"],
            "label": row["label"],
            "garments": row["garments"].split(","),
        })

    return {
        "mapping": mapping,
        "garments": resolved_garments,
        "outfits": resolved_outfits,
    }


def _verify_legacy_import_receipt(receipt: dict, document: dict) -> dict:
    content_digest = _portable_digest(document)
    if (
        receipt.get("legacy_content_digest") != content_digest
        or not _valid_sha256(receipt.get("review_digest"))
        or receipt.get("choice") not in {"import", "save_copy"}
        or not _valid_sha256(receipt.get("destination_digest"))
    ):
        _legacy_import_invalid_receipt()
    mapping = _legacy_mapping_from_receipt(receipt, document)
    has_remapping = bool(_legacy_remapped(mapping))
    if receipt["choice"] != ("save_copy" if has_remapping else "import"):
        _legacy_import_invalid_receipt()
    projection = _legacy_destination_projection(document, mapping, receipt=True)
    if _portable_digest(projection) != receipt["destination_digest"]:
        _legacy_import_invalid_receipt()
    return {
        "mapping": mapping,
        "remapped": _legacy_remapped(mapping),
        "resolved_garments": projection["garments"],
        "resolved_outfits": projection["outfits"],
    }


def _find_legacy_import_receipt(content_digest: str, document: dict) -> tuple[dict, dict] | None:
    receipt = db.one(
        "SELECT * FROM saved_look_legacy_import_receipt WHERE legacy_content_digest = ?",
        content_digest,
    )
    return None if receipt is None else (receipt, _verify_legacy_import_receipt(receipt, document))


def _legacy_fresh_plan(
    document: dict, content_digest: str, state_digest: str, key: bytes,
) -> dict:
    index = _catalogue_index()
    definitions = {item["key"]: item for item in document["garments"]}
    source_keys = set(definitions)
    source_keys.update(key for outfit in document["outfits"] for key in outfit["garments"])
    resolved_by_source = {}
    for source_key in sorted(source_keys):
        definition = definitions.get(source_key) or index["garment_rows"].get(source_key)
        if definition is None:
            raise SavedLookError(422, "look_import_unknown_garment", "An outfit references an unknown garment.")
        resolved_by_source[source_key] = {
            "key": source_key,
            "wording": definition["wording"],
            "aside": definition["aside"],
        }

    reserved_garments = set(index["garments"]) | source_keys
    garment_mapping = {}
    remapped = []
    for source_key in sorted(source_keys):
        definition = resolved_by_source[source_key]
        candidates = index["garments"].get(source_key, set())
        reason = "unrepresentable_key" if "," in source_key else (
            "key_conflict"
            if source_key in definitions and candidates
            and candidates != {(definition["wording"], definition["aside"])}
            else ""
        )
        destination_key = source_key
        if reason:
            destination_key = _generated_key(
                "garment-import", reserved_garments, key,
                {
                    "kind": "garment", "source_key": source_key,
                    "legacy_content_digest": content_digest, "state_digest": state_digest,
                },
                purpose=_LEGACY_IMPORT_PREVIEW_PURPOSE,
            )
            remapped.append({
                "kind": "garment", "from": source_key, "to": destination_key, "reason": reason,
            })
        garment_mapping[source_key] = destination_key

    resolved_by_local = {
        garment_mapping[source_key]: {
            "key": garment_mapping[source_key],
            "wording": definition["wording"],
            "aside": definition["aside"],
        }
        for source_key, definition in resolved_by_source.items()
    }
    outfit_mapping = {}
    reserved_outfits = set(index["outfits"])
    reserved_outfits.update(outfit["key"] for outfit in document["outfits"])
    resolved_outfits = []
    for source in document["outfits"]:
        source_key = source["key"]
        garment_keys = [garment_mapping[item] for item in source["garments"]]
        desired_garments = [resolved_by_local[item] for item in garment_keys]
        desired_fingerprint = _portable_outfit_fingerprint(desired_garments)
        candidates = index["outfits"].get(source_key, set())
        existing = db.one("SELECT label, garments FROM outfit WHERE key = ?", source_key)
        conflicts = bool(candidates and candidates != {desired_fingerprint})
        if existing is not None and (
            existing["label"] != source["label"]
            or existing["garments"] != ",".join(garment_keys)
        ):
            conflicts = True
        destination_key = source_key
        if conflicts:
            destination_key = _generated_key(
                "outfit-import", reserved_outfits, key,
                {
                    "kind": "outfit", "source_key": source_key,
                    "legacy_content_digest": content_digest, "state_digest": state_digest,
                },
                purpose=_LEGACY_IMPORT_PREVIEW_PURPOSE,
            )
            remapped.append({
                "kind": "outfit", "from": source_key, "to": destination_key, "reason": "key_conflict",
            })
        outfit_mapping[source_key] = destination_key
        resolved_outfits.append({
            "source_key": source_key,
            "key": destination_key,
            "label": source["label"],
            "garments": garment_keys,
        })
        index["outfits"].setdefault(destination_key, set()).add(desired_fingerprint)

    mapping = {"garments": garment_mapping, "outfits": outfit_mapping}
    imported_garments = {}
    for source_key, definition in definitions.items():
        local_key = garment_mapping[source_key]
        imported_garments[local_key] = {
            "key": local_key, "wording": definition["wording"], "aside": definition["aside"],
        }
    for source_key, local_key in garment_mapping.items():
        if source_key not in definitions and source_key != local_key:
            definition = resolved_by_source[source_key]
            imported_garments[local_key] = {
                "key": local_key, "wording": definition["wording"], "aside": definition["aside"],
            }

    resolved_garments = [
        {
            "source_key": source_key,
            "key": garment_mapping[source_key],
            "wording": resolved_by_source[source_key]["wording"],
            "aside": resolved_by_source[source_key]["aside"],
        }
        for source_key in sorted(source_keys)
    ]
    write_garments = [imported_garments[name] for name in sorted(imported_garments)]
    write_outfits = [
        {"key": item["key"], "label": item["label"], "garments": item["garments"]}
        for item in resolved_outfits
    ]
    write_garments = [
        item for item in write_garments
        if db.one("SELECT 1 FROM garment WHERE key = ?", item["key"]) is None
    ]
    write_outfits = [
        item for item in write_outfits
        if db.one("SELECT 1 FROM outfit WHERE key = ?", item["key"]) is None
    ]
    mode = "choice_required" if remapped else ("import" if write_garments or write_outfits else "already_equal")
    return {
        "mode": mode,
        "status": "choice_required" if remapped else ("ready" if mode == "import" else "already_equal"),
        "choices": ["save_copy"] if remapped else ["import"],
        "mapping": mapping,
        "remapped": remapped,
        "resolved_garments": resolved_garments,
        "resolved_outfits": resolved_outfits,
        "write_garments": write_garments,
        "write_outfits": write_outfits,
        "no_op": mode == "already_equal",
    }


def _legacy_replay_plan(verified: dict) -> dict:
    return {
        "mode": "receipt_replay",
        "status": "already_imported",
        "choices": ["import"],
        "mapping": verified["mapping"],
        "remapped": verified["remapped"],
        "resolved_garments": verified["resolved_garments"],
        "resolved_outfits": verified["resolved_outfits"],
        "write_garments": [],
        "write_outfits": [],
        "no_op": True,
    }


def _build_legacy_import_plan(
    document: dict, content_digest: str, state_digest: str, key: bytes,
) -> dict:
    receipt = _find_legacy_import_receipt(content_digest, document)
    if receipt is not None:
        return _legacy_replay_plan(receipt[1])
    return _legacy_fresh_plan(document, content_digest, state_digest, key)


def preview_legacy_import(value: object) -> dict:
    document = _canonicalize_legacy_wardrobe(value)
    content_digest = _portable_digest(document)
    with db.transaction():
        state_digest = _legacy_store_state_digest()
        key = _portable_preview_key(create=True)
        plan = _build_legacy_import_plan(document, content_digest, state_digest, key)
        token, review_digest = _legacy_preview_token(plan, state_digest, content_digest, key)
        return {
            **plan,
            "legacy_content_digest": content_digest,
            "review_digest": review_digest,
            "preview_token": token,
        }


def _commit_legacy_rows(document: dict, plan: dict) -> str:
    created_at = db.now()
    for garment in plan["write_garments"]:
        existing = db.one(
            "SELECT wording, aside FROM garment WHERE key = ?", garment["key"],
        )
        if existing is None:
            db.run(
                "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
                garment["key"], garment["wording"], garment["aside"], created_at,
            )
        elif existing["wording"] != garment["wording"] or existing["aside"] != garment["aside"]:
            _legacy_import_stale()
    for outfit in plan["write_outfits"]:
        garments = ",".join(outfit["garments"])
        existing = db.one(
            "SELECT label, garments FROM outfit WHERE key = ?", outfit["key"],
        )
        if existing is None:
            db.run(
                "INSERT INTO outfit (key, label, garments, created_at) VALUES (?, ?, ?, ?)",
                outfit["key"], outfit["label"], garments, created_at,
            )
        elif existing["label"] != outfit["label"] or existing["garments"] != garments:
            _legacy_import_stale()
    projection = _legacy_destination_projection(document, plan["mapping"])
    return _portable_digest(projection)


def _legacy_import_result(plan: dict, *, no_op: bool) -> dict:
    return {
        "mapping": plan["mapping"],
        "remapped": plan["remapped"],
        "garments": plan["resolved_garments"],
        "outfits": plan["resolved_outfits"],
        "no_op": no_op,
    }


def commit_legacy_import(
    value: object, preview_token: str, review_digest: str, choice: str,
) -> dict:
    document = _canonicalize_legacy_wardrobe(value)
    content_digest = _portable_digest(document)
    payload = _read_legacy_preview_token(preview_token)
    if (
        payload["legacy_content_digest"] != content_digest
        or payload["review_digest"] != review_digest
    ):
        raise SavedLookError(409, "look_import_preview_mismatch", "Import content or review changed; preview the import again.")
    try:
        with db.transaction():
            receipt_pair = _find_legacy_import_receipt(content_digest, document)
            if receipt_pair is not None:
                receipt, verified = receipt_pair
                if payload["mode"] == "receipt_replay":
                    plan = _legacy_replay_plan(verified)
                    if (
                        payload["choices"] != plan["choices"]
                        or _legacy_review_digest(plan, payload["store_state_digest"], content_digest) != review_digest
                        or choice != "import"
                    ):
                        _legacy_import_choice_invalid()
                elif (
                    payload["mode"] not in {"choice_required", "import"}
                    or receipt["review_digest"] != review_digest
                    or receipt["choice"] != choice
                ):
                    _legacy_import_stale()
                return _legacy_import_result(_legacy_replay_plan(verified), no_op=True)

            state_digest = _legacy_store_state_digest()
            if state_digest != payload["store_state_digest"]:
                _legacy_import_stale()
            plan = _build_legacy_import_plan(document, content_digest, state_digest, _portable_preview_key(create=False))
            if (
                plan["mode"] != payload["mode"]
                or plan["choices"] != payload["choices"]
                or _legacy_review_digest(plan, state_digest, content_digest) != review_digest
            ):
                _legacy_import_stale()
            if choice not in plan["choices"]:
                _legacy_import_choice_invalid()

            destination_digest = _commit_legacy_rows(document, plan)
            if plan["mode"] != "already_equal":
                db.run(
                    """INSERT INTO saved_look_legacy_import_receipt
                       (legacy_content_digest, review_digest, choice,
                        garment_mapping_json, outfit_mapping_json,
                        destination_digest, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    content_digest,
                    review_digest,
                    choice,
                    json.dumps(plan["mapping"]["garments"], ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                    json.dumps(plan["mapping"]["outfits"], ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                    destination_digest,
                    db.now(),
                )
            return _legacy_import_result(plan, no_op=plan["no_op"])
    except SavedLookError:
        raise
    except sqlite3.IntegrityError:
        _legacy_import_stale()
    except sqlite3.Error:
        _legacy_import_stale()


def _unique_key(prefix: str, table: str, reserved: set[str]) -> str:
    column = "look_key" if table == "saved_look_version" else "key"
    while True:
        key = f"{prefix}-{uuid4().hex}"
        if key not in reserved and db.one(f"SELECT 1 FROM {table} WHERE {column} = ?", key) is None:
            reserved.add(key)
            return key


def _known_garment(key: str, history: list[dict]) -> dict | None:
    for look in history:
        outfit = look["outfit"]
        if outfit is not None:
            for garment in outfit["garments"]:
                if garment["key"] == key:
                    return garment
    row = db.one("SELECT key, wording, aside FROM garment WHERE key = ?", key)
    if row is None:
        return None
    garment = {"key": row["key"], "wording": row["wording"], "aside": row["aside"]}
    if not _valid_wording(garment["wording"]) or not _valid_wording(garment["aside"], allow_empty=True):
        _stored_data_invalid()
    return garment


def _resolve_outfit_key(outfit_key: object, history: list[dict]) -> dict:
    if not _valid_key(outfit_key):
        _invalid()
    for look in history:
        outfit = look["outfit"]
        if outfit is not None and outfit["outfit_key"] == outfit_key:
            return outfit

    row = db.one("SELECT key, garments FROM outfit WHERE key = ?", outfit_key)
    if row is None:
        _invalid()
    keys = row["garments"].split(",")
    if not keys or any(not _valid_key(key) for key in keys) or len(set(keys)) != len(keys):
        _stored_data_invalid()
    garments = []
    for key in keys:
        garment = _known_garment(key, history)
        if garment is None:
            _invalid()
        garments.append(garment)
    return {"outfit_key": row["key"], "garments": garments}


def _resolve_garments(
    definitions: object,
    history: list[dict],
    pending: list[dict],
    reserved: set[str],
) -> list[dict]:
    if not isinstance(definitions, list):
        _invalid()
    garments = []
    seen: set[str] = set()
    for definition in definitions:
        if not isinstance(definition, dict) or set(definition) - {"key", "wording", "aside"}:
            _invalid()
        key = definition.get("key")
        has_wording = "wording" in definition
        has_aside = "aside" in definition
        if key is not None and not _valid_key(key):
            _invalid()
        if key is None:
            if not has_wording:
                _invalid()
            wording = definition["wording"]
            aside = definition.get("aside", "")
            if not _valid_wording(wording) or not _valid_wording(aside, allow_empty=True):
                _invalid()
            garment = {"key": None, "wording": wording, "aside": aside}
            pending.append(garment)
        else:
            original = _known_garment(key, history)
            if original is None:
                _invalid()
            if not has_wording and not has_aside:
                garment = original
            else:
                wording = definition["wording"] if has_wording else original["wording"]
                aside = definition["aside"] if has_aside else original["aside"]
                if not _valid_wording(wording) or not _valid_wording(aside, allow_empty=True):
                    _invalid()
                if wording == original["wording"] and aside == original["aside"]:
                    garment = original
                else:
                    garment = {"key": None, "wording": wording, "aside": aside}
                    pending.append(garment)
        if garment["key"] is not None and garment["key"] in seen:
            _invalid()
        if garment["key"] is not None:
            seen.add(garment["key"])
        garments.append(garment)
    for garment in pending:
        garment["key"] = _unique_key("garment", "garment", reserved)
        if garment["key"] in seen:
            _invalid()
        seen.add(garment["key"])
    return garments


def _reuse_outfit(garments: list[dict], history: list[dict]) -> dict | None:
    for look in history:
        outfit = look["outfit"]
        if outfit is not None and outfit["garments"] == garments:
            return outfit
    joined = ",".join(garment["key"] for garment in garments)
    row = db.one("SELECT key FROM outfit WHERE garments = ? ORDER BY key LIMIT 1", joined)
    if row is not None:
        if not _valid_key(row["key"]):
            _stored_data_invalid()
        return {"outfit_key": row["key"], "garments": garments}
    return None


def _save(
    payload: dict,
    *,
    look_key: str | None,
    version: int,
    history: list[dict],
    prior: dict | None,
) -> dict:
    name = payload.get("name")
    if not _valid_name(name):
        _invalid()
    appearance = payload.get("appearance", prior["appearance"] if prior else "")
    if not isinstance(appearance, str):
        _invalid()

    if "outfit_key" in payload and "garments" in payload:
        _invalid()
    pending_garments: list[dict] = []
    pending_outfit: dict | None = None
    reserved: set[str] = set()
    if "outfit_key" in payload:
        outfit_key = payload["outfit_key"]
        outfit = None if outfit_key is None else _resolve_outfit_key(outfit_key, history)
    elif "garments" in payload:
        definitions = payload["garments"]
        garments = _resolve_garments([] if definitions is None else definitions, history, pending_garments, reserved)
        if not garments:
            outfit = None
        else:
            outfit = _reuse_outfit(garments, history)
            if outfit is None:
                outfit_key = _unique_key("outfit", "outfit", reserved)
                outfit = {"outfit_key": outfit_key, "garments": garments}
                pending_outfit = {
                    "key": outfit_key,
                    "label": name,
                    "garments": ",".join(garment["key"] for garment in garments),
                }
    else:
        outfit = prior["outfit"] if prior is not None else None

    if look_key is None:
        look_key = _unique_key("look", "saved_look_version", reserved)
    digest = resource_store.canonical_digest({"appearance": appearance, "outfit": outfit})
    snapshot = {
        "look_id": look_key,
        "version": version,
        "content_digest": digest,
        "appearance": appearance,
        "outfit": outfit,
    }
    created_at = db.now()
    for garment in pending_garments:
        db.run(
            "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
            garment["key"], garment["wording"], garment["aside"], created_at,
        )
    if pending_outfit is not None:
        db.run(
            "INSERT INTO outfit (key, label, garments, created_at) VALUES (?, ?, ?, ?)",
            pending_outfit["key"], pending_outfit["label"], pending_outfit["garments"], created_at,
        )
    db.run(
        """INSERT INTO saved_look_version
           (look_key, version, name, snapshot_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        look_key, version, name,
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        created_at,
    )
    return {
        "key": look_key,
        "version": version,
        "name": name,
        "content_digest": digest,
        "appearance": appearance,
        "outfit": outfit,
    }


def create(payload: dict) -> dict:
    try:
        with db.transaction():
            return _save(payload, look_key=None, version=1, history=[], prior=None)
    except sqlite3.IntegrityError:
        _write_conflict()


def create_version(look_key: str, expected_version: int, payload: dict) -> dict:
    if (
        not _valid_key(look_key)
        or type(expected_version) is not int
        or expected_version < 1
    ):
        _invalid()
    try:
        with db.transaction():
            history = _history(look_key)
            if not history:
                raise SavedLookError(404, "look_not_found", "Saved look was not found.")
            prior = history[0]
            if prior["version"] != expected_version:
                raise SavedLookError(409, "look_version_stale", "Saved look changed; reload before saving.")
            return _save(
                payload,
                look_key=look_key,
                version=prior["version"] + 1,
                history=history,
                prior=prior,
            )
    except sqlite3.IntegrityError:
        _write_conflict()
