"""Translation service for resource planning (task 2.2).

Provides two-phase preview -> apply bulk translation mapping with HMAC-SHA256
attestation against TOCTOU race conditions, validated merge updates, canonical
sidecar persistence in asset_revision.translation, and single revision updates.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
import time
from typing import Any, Mapping

import db
import resource_prompts
import resource_readiness
import resource_store
import translation_map

ATTESTATION_SALT: str = "idevgen-resource-translation-attestation-v1"


class TranslationConflictError(Exception):
    """Raised when library state or translation map drifted between preview and apply."""


def normalize_translation_map_input(data: Any) -> dict[str, dict[str, Any]]:
    """Normalize and validate translation map data from a file path, dict, or list.

    Returns a dict mapping source_string -> validated_entry_dict.
    Raises ValueError or TypeError if the map is invalid, empty, or contains non-English translations.
    """
    if isinstance(data, (str, Path)):
        p = Path(data)
        if p.is_file():
            raw_text = p.read_text(encoding="utf-8")
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in translation map file {p}: {exc}") from exc
            data = parsed
        else:
            # Try parsing as JSON string directly
            try:
                data = json.loads(str(data))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Translation map file not found or invalid JSON: {p}") from exc

    if not isinstance(data, (dict, list)):
        raise TypeError(f"Translation map must be a dict or list, got {type(data).__name__}")

    # Handle envelope formats: {"translation_map": ...}, {"items": ...}
    if isinstance(data, dict):
        if "translation_map" in data and isinstance(data["translation_map"], (dict, list)):
            data = data["translation_map"]
        elif "items" in data and isinstance(data["items"], (dict, list)):
            data = data["items"]

    # Convert list of entry dicts to dict keyed by source
    if isinstance(data, list):
        converted: dict[str, Any] = {}
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                raise TypeError(f"Translation map entry at index {idx} must be a dict, got {type(item).__name__}")
            src = item.get("source")
            if not isinstance(src, str) or not src.strip():
                raise ValueError(f"Translation map entry at index {idx} missing 'source' string")
            converted[src] = item
        data = converted

    return translation_map.validate_translation_map(data)


def compute_library_fingerprint(library_id: int) -> str:
    """Compute a deterministic SHA-256 fingerprint of all revisions in a library."""
    rows = db.q(
        "SELECT id, content_digest, translation FROM asset_revision WHERE library_id = ? ORDER BY id ASC",
        library_id,
    )
    hasher = hashlib.sha256()
    for row in rows:
        rev_id = str(row["id"])
        digest = str(row["content_digest"])
        trans = str(row["translation"] or "{}")
        try:
            parsed = json.loads(trans)
            canonical_trans = json.dumps(parsed, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        except Exception:
            canonical_trans = trans
        chunk = f"{rev_id}:{digest}:{canonical_trans}\n"
        hasher.update(chunk.encode("utf-8"))
    return hasher.hexdigest()


def compute_map_digest(validated_map: dict[str, dict[str, Any]]) -> str:
    """Compute a deterministic SHA-256 hash over validated translation map entries."""
    canonical_json = json.dumps(validated_map, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _create_attestation_token(
    library_key: str,
    map_digest: str,
    library_fingerprint: str,
    ttl_seconds: int = 3600,
) -> tuple[str, int]:
    """Create a signed HMAC-SHA256 attestation token."""
    expires_at = int(time.time()) + ttl_seconds
    payload_data = {
        "library_key": library_key,
        "map_digest": map_digest,
        "library_fingerprint": library_fingerprint,
        "exp": expires_at,
    }
    payload_bytes = json.dumps(payload_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    sig = hmac.new(
        ATTESTATION_SALT.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{sig}", expires_at


def _verify_attestation_token(token: str, expected_library_key: str) -> dict[str, Any]:
    """Verify HMAC signature, expiry, and library binding of an attestation token."""
    if not isinstance(token, str) or "." not in token:
        raise ValueError("Invalid attestation token format")
    payload_b64, sig = token.split(".", 1)
    rem = len(payload_b64) % 4
    padded_b64 = payload_b64 + ("=" * (4 - rem) if rem else "")
    expected_sig = hmac.new(
        ATTESTATION_SALT.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("Attestation token signature verification failed")
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded_b64.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Corrupt attestation token payload: {exc}") from exc

    if payload.get("library_key") != expected_library_key:
        raise ValueError(
            f"Attestation token library mismatch: expected {expected_library_key!r}, got {payload.get('library_key')!r}"
        )
    if int(time.time()) > payload.get("exp", 0):
        raise ValueError("Attestation token has expired; generate a fresh preview before applying")
    return payload


def match_translation_map(
    payload: dict[str, Any],
    validated_map: dict[str, dict[str, Any]],
    kind: str,
) -> tuple[dict[str, Any], set[str]]:
    """Match payload strings against validated translation map entries.

    Returns a tuple of (canonical_translations, matched_source_strings).
    Canonicalizes alias fields (name -> label, theme -> scene_theme, tag -> tags)
    and retains independent field names.
    Raises ValueError if conflicting translations are generated for the same canonical family.
    """
    if not isinstance(payload, dict):
        return {}, set()

    matches: dict[str, Any] = {}
    matched_sources: set[str] = set()

    for field_name, val in payload.items():
        if isinstance(val, str):
            if val in validated_map:
                entry = validated_map[val]
                canonical_field = resource_prompts.canonical_field_name(field_name)
                allowed_canonical_fields = {
                    resource_prompts.canonical_field_name(f) for f in entry["fields"]
                }
                if canonical_field in allowed_canonical_fields or field_name in entry["fields"]:
                    trans_val = entry["translation"]
                    if canonical_field in matches and matches[canonical_field] != trans_val:
                        raise ValueError(
                            f"Conflicting translations matched for canonical field {canonical_field!r}: "
                            f"{matches[canonical_field]!r} vs {trans_val!r}"
                        )
                    matches[canonical_field] = trans_val
                    matched_sources.add(val)
        elif isinstance(val, list):
            canonical_field = resource_prompts.canonical_field_name(field_name)
            translated_items = []
            has_match = False
            for item in val:
                if isinstance(item, str) and item in validated_map:
                    entry = validated_map[item]
                    allowed_canonical_fields = {
                        resource_prompts.canonical_field_name(f) for f in entry["fields"]
                    }
                    if canonical_field in allowed_canonical_fields or field_name in entry["fields"]:
                        translated_items.append(entry["translation"])
                        matched_sources.add(item)
                        has_match = True
                    else:
                        translated_items.append(item)
                else:
                    translated_items.append(item)
            if has_match:
                matches[canonical_field] = translated_items

    return matches, matched_sources


def preview_translation_map(
    library_key: str,
    translation_map_input: Any,
) -> dict[str, Any]:
    """Preview translation map application without modifying database.

    Strictly read-only. Validates translation map, matches against revisions,
    computes candidate readiness, and issues an HMAC attestation token.
    """
    library = db.one("SELECT id, kind FROM resource_library WHERE library_key = ?", library_key)
    if library is None:
        raise ValueError(f"Resource library {library_key!r} not found")

    library_id = int(library["id"])
    kind = str(library["kind"])

    # 100% validate input map up front; raises ValueError on invalid schema or non-English text
    validated_map = normalize_translation_map_input(translation_map_input)
    map_digest = compute_map_digest(validated_map)
    library_fingerprint = compute_library_fingerprint(library_id)

    attestation_token, expires_at = _create_attestation_token(
        library_key, map_digest, library_fingerprint
    )

    revisions = resource_store.list_revisions(library_id)
    all_matched_sources: set[str] = set()
    matched_revisions_count = 0
    would_update = 0
    unchanged = 0
    would_be_ready = 0
    would_remain_pending = 0

    for rev in revisions:
        payload = rev["payload"]
        existing_trans = rev["translation"] or {}
        updates, matched_srcs = match_translation_map(payload, validated_map, kind)
        if matched_srcs:
            matched_revisions_count += 1
            all_matched_sources.update(matched_srcs)

        merged = {**existing_trans, **updates}
        if merged != existing_trans:
            would_update += 1
        else:
            unchanged += 1

        report = resource_readiness.evaluate_readiness(kind, payload, merged)
        if report.is_ready:
            would_be_ready += 1
        else:
            would_remain_pending += 1

    unmatched_entries = len(set(validated_map.keys()) - all_matched_sources)

    return {
        "library_key": library_key,
        "total_revisions": len(revisions),
        "matched_revisions": matched_revisions_count,
        "unmatched_map_entries": unmatched_entries,
        "would_update": would_update,
        "unchanged": unchanged,
        "would_be_ready": would_be_ready,
        "would_remain_pending": would_remain_pending,
        "attestation_token": attestation_token,
        "expires_at": expires_at,
    }


def apply_translation_map(
    library_key: str,
    translation_map_input: Any,
    attestation_token: str,
) -> dict[str, Any]:
    """Atomically apply a translation map to a library with TOCTOU verification."""
    library = db.one("SELECT id, kind FROM resource_library WHERE library_key = ?", library_key)
    if library is None:
        raise ValueError(f"Resource library {library_key!r} not found")

    library_id = int(library["id"])
    kind = str(library["kind"])

    token_payload = _verify_attestation_token(attestation_token, library_key)
    validated_map = normalize_translation_map_input(translation_map_input)

    current_map_digest = compute_map_digest(validated_map)
    current_fingerprint = compute_library_fingerprint(library_id)

    if (
        current_map_digest != token_payload["map_digest"]
        or current_fingerprint != token_payload["library_fingerprint"]
    ):
        raise TranslationConflictError(
            "Resource library state or translation map changed since preview; generate a fresh preview before applying."
        )

    updated = 0
    unchanged = 0
    ready = 0
    pending = 0

    revisions = resource_store.list_revisions(library_id)
    with db.transaction():
        for rev in revisions:
            rev_id = int(rev["id"])
            payload = rev["payload"]
            existing_trans = rev["translation"] or {}
            updates, _ = match_translation_map(payload, validated_map, kind)
            merged = {**existing_trans, **updates}

            if merged != existing_trans:
                resource_store.update_translation(rev_id, merged)
                report = resource_readiness.evaluate_readiness(kind, payload, merged)
                resource_readiness.set_revision_readiness(rev_id, report.coverage)
                updated += 1
            else:
                report = resource_readiness.evaluate_readiness(kind, payload, merged)
                unchanged += 1

            if report.is_ready:
                ready += 1
            else:
                pending += 1

    return {
        "library_key": library_key,
        "updated": updated,
        "unchanged": unchanged,
        "ready": ready,
        "pending": pending,
    }


def apply_revision_translation(
    library_key: str,
    source_id: str,
    content_digest: str,
    translation_updates: dict[str, Any],
) -> dict[str, Any]:
    """Update the translation sidecar for a single revision with validated merge."""
    library = db.one("SELECT id, kind FROM resource_library WHERE library_key = ?", library_key)
    if library is None:
        raise ValueError(f"Resource library {library_key!r} not found")

    library_id = int(library["id"])
    kind = str(library["kind"])

    rev = resource_store.get_revision(
        library_id=library_id,
        source_id=source_id,
        content_digest=content_digest,
    )
    if rev is None:
        raise ValueError(
            f"Revision not found for library_key={library_key!r}, "
            f"source_id={source_id!r}, content_digest={content_digest!r}"
        )

    canonical_updates = resource_prompts.canonicalize_translation_dict(translation_updates)
    for field_name, val in canonical_updates.items():
        if not resource_readiness._is_valid_english_translation(val):
            raise ValueError(
                f"Translation for field {field_name!r} is not valid English: {val!r}"
            )

    existing_trans = rev["translation"] or {}
    merged = {**existing_trans, **canonical_updates}

    rev_id = int(rev["id"])
    with db.transaction():
        resource_store.update_translation(rev_id, merged)
        report = resource_readiness.evaluate_readiness(kind, rev["payload"], merged)
        resource_readiness.set_revision_readiness(rev_id, report.coverage)

    return {
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": content_digest,
        "translation": merged,
        "status": report.status,
        "pending_fields": report.pending_fields,
        "is_ready": report.is_ready,
    }
