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
import os
from pathlib import Path
import secrets
import time
from typing import Any

import db
import resource_prompts
import resource_readiness
import resource_store
import translation_map

_ATTESTATION_KEY_NAME: str = ".resource-translation-preview-key"


class TranslationConflictError(Exception):
    """Raised when library state or translation map drifted between preview and apply."""


def _database_directory() -> Path:
    """Return the directory containing the active SQLite database.

    Mirrors the database location semantics used by the persistence layer,
    falling back to IDEVGEN_DATA_DIR or 'data'.
    """
    try:
        row = db.one("PRAGMA database_list")
        database_file = row.get("file") if row else None
        if database_file and database_file != ":memory:":
            return Path(database_file).resolve().parent
    except Exception:
        pass
    configured = os.environ.get("IDEVGEN_DATA_DIR")
    return Path(configured or "data").resolve()


def _attestation_key(create: bool) -> bytes:
    """Read or create the private HMAC attestation key.

    Stored as .resource-translation-preview-key beside the active SQLite database.
    If create is False and the key does not exist, raises ValueError.
    """
    target_dir = _database_directory()
    path = target_dir / _ATTESTATION_KEY_NAME
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        if not create:
            raise ValueError("Attestation key does not exist; generate a fresh preview before applying")
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
        raise ValueError("Corrupt resource translation attestation key")
    return key


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


def _validate_map_fields_for_kind(
    validated_map: dict[str, dict[str, Any]],
    kind: str,
) -> None:
    """Strict upfront validation that all fields in translation map entries are authorized for kind.

    Only fields with ROLE_DESCRIPTIVE_INPUT are permitted.
    If any field in any entry is unauthorized (or unmapped), raises ValueError with details.
    """
    for src, entry in validated_map.items():
        for f in entry.get("fields", []):
            info = resource_prompts.classify_field(kind, str(f))
            role = info.get("role")
            if role != resource_prompts.ROLE_DESCRIPTIVE_INPUT:
                raise ValueError(
                    f"Translation map entry for {src!r} specifies field {f!r} with role {role!r}; "
                    f"only descriptive input fields (role {resource_prompts.ROLE_DESCRIPTIVE_INPUT!r}) "
                    f"may be translated in library of kind {kind!r}"
                )


def compute_library_fingerprint(library_id: int) -> str:
    """Compute a deterministic SHA-256 fingerprint of library metadata and revisions.

    Excludes coverage so that Apply can repair stale coverage without invalidating
    a valid preview token.
    """
    lib = db.one(
        "SELECT id, library_key, kind, created_at FROM resource_library WHERE id = ?",
        library_id,
    )
    if lib is None:
        raise ValueError(f"Resource library {library_id!r} not found")

    hasher = hashlib.sha256()
    header = f"lib:{lib['id']}:{lib['library_key']}:{lib['kind']}:{lib['created_at']}\n"
    hasher.update(header.encode("utf-8"))

    rows = db.q(
        "SELECT id, content_digest, translation FROM asset_revision WHERE library_id = ? ORDER BY id ASC",
        library_id,
    )
    for row in rows:
        rev_id = str(row["id"])
        digest = str(row["content_digest"])
        trans = str(row["translation"] or "{}")
        try:
            parsed = json.loads(trans)
            canonical_trans = json.dumps(parsed, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        except Exception:
            canonical_trans = trans
        chunk = f"rev:{rev_id}:{digest}:{canonical_trans}\n"
        hasher.update(chunk.encode("utf-8"))
    return hasher.hexdigest()


def compute_map_digest(validated_map: dict[str, dict[str, Any]]) -> str:
    """Compute a deterministic SHA-256 hash over validated translation map entries."""
    canonical_json = json.dumps(validated_map, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _create_attestation_token(
    library_key: str,
    library_id: int,
    library_kind: str,
    library_created_at: str,
    map_digest: str,
    library_fingerprint: str,
    ttl_seconds: int = 3600,
) -> tuple[str, int]:
    """Create a signed HMAC-SHA256 attestation token."""
    expires_at = int(time.time()) + ttl_seconds
    payload_data = {
        "library_key": library_key,
        "library_id": library_id,
        "library_kind": library_kind,
        "library_created_at": library_created_at,
        "map_digest": map_digest,
        "library_fingerprint": library_fingerprint,
        "exp": expires_at,
    }
    payload_bytes = json.dumps(payload_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    key = _attestation_key(create=True)
    sig = hmac.new(
        key,
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
    try:
        key = _attestation_key(create=False)
    except ValueError:
        raise ValueError("Attestation token signature verification failed")
    expected_sig = hmac.new(
        key,
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
    Canonicalizes alias fields (name -> label, theme -> scene_theme, tag -> tags).
    Fails closed (raising ValueError) on contractual/structural violations.
    Omit candidate list translation only if at least one item matched an authorized entry
    and other non-English items remain untranslated; does not generate candidate list
    if no map entry matched.
    """
    if not isinstance(payload, dict):
        return {}, set()

    matches: dict[str, Any] = {}
    matched_sources: set[str] = set()

    for field_name, val in payload.items():
        info = resource_prompts.classify_field(kind, field_name)
        if info.get("role") != resource_prompts.ROLE_DESCRIPTIVE_INPUT:
            continue
        canonical_field = resource_prompts.canonical_field_name(field_name)

        if isinstance(val, str):
            if val in validated_map:
                entry = validated_map[val]
                allowed_canonical_fields = {
                    resource_prompts.canonical_field_name(f) for f in entry.get("fields", [])
                }
                if canonical_field in allowed_canonical_fields or field_name in entry.get("fields", []):
                    trans_val = entry["translation"]
                    if canonical_field in matches and matches[canonical_field] != trans_val:
                        raise ValueError(
                            f"Conflicting translations matched for canonical field {canonical_field!r}: "
                            f"{matches[canonical_field]!r} vs {trans_val!r}"
                        )
                    resource_readiness.validate_translation_value_for_source(
                        kind, payload, canonical_field, trans_val
                    )
                    matches[canonical_field] = trans_val
                    matched_sources.add(val)
        elif isinstance(val, list):
            candidate_items: list[str] = []
            matched_for_this_field: list[str] = []
            has_map_match = False
            has_untranslated_non_english = False

            for item in val:
                if not isinstance(item, str):
                    candidate_items.append(item)  # type: ignore
                    continue
                if item in validated_map:
                    entry = validated_map[item]
                    allowed_canonical_fields = {
                        resource_prompts.canonical_field_name(f) for f in entry.get("fields", [])
                    }
                    if canonical_field in allowed_canonical_fields or field_name in entry.get("fields", []):
                        candidate_items.append(entry["translation"])
                        matched_for_this_field.append(item)
                        has_map_match = True
                    else:
                        if resource_readiness.is_valid_english_translation_scalar(item):
                            candidate_items.append(item)
                        else:
                            has_untranslated_non_english = True
                            candidate_items.append(item)
                else:
                    if resource_readiness.is_valid_english_translation_scalar(item):
                        candidate_items.append(item)
                    else:
                        has_untranslated_non_english = True
                        candidate_items.append(item)

            if has_map_match:
                matched_sources.update(matched_for_this_field)
                if not has_untranslated_non_english:
                    resource_readiness.validate_translation_value_for_source(
                        kind, payload, canonical_field, candidate_items
                    )
                    matches[canonical_field] = candidate_items

    return matches, matched_sources


def preview_translation_map(
    library_key: str,
    translation_map_input: Any,
) -> dict[str, Any]:
    """Preview translation map application without modifying database.

    Strictly read-only. Validates translation map, matches against revisions,
    computes candidate readiness, and issues an HMAC attestation token.
    """
    library = db.one(
        "SELECT id, library_key, kind, created_at FROM resource_library WHERE library_key = ?",
        library_key,
    )
    if library is None:
        raise ValueError(f"Resource library {library_key!r} not found")

    library_id = int(library["id"])
    kind = str(library["kind"])
    created_at = str(library["created_at"])

    # 100% validate input map up front; raises ValueError on invalid schema or non-English text
    validated_map = normalize_translation_map_input(translation_map_input)
    _validate_map_fields_for_kind(validated_map, kind)

    map_digest = compute_map_digest(validated_map)
    library_fingerprint = compute_library_fingerprint(library_id)

    attestation_token, expires_at = _create_attestation_token(
        library_key=library_key,
        library_id=library_id,
        library_kind=kind,
        library_created_at=created_at,
        map_digest=map_digest,
        library_fingerprint=library_fingerprint,
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
        canonical_existing = resource_readiness.validate_and_canonicalize_existing_translation(
            kind, payload, existing_trans
        )
        updates, matched_srcs = match_translation_map(payload, validated_map, kind)
        if matched_srcs:
            matched_revisions_count += 1
            all_matched_sources.update(matched_srcs)

        merged = {**canonical_existing, **updates}
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
    token_payload = _verify_attestation_token(attestation_token, library_key)
    validated_map = normalize_translation_map_input(translation_map_input)

    updated = 0
    unchanged = 0
    ready = 0
    pending = 0

    with db.transaction():
        library = db.one(
            "SELECT id, library_key, kind, created_at FROM resource_library WHERE library_key = ?",
            library_key,
        )
        if library is None:
            raise ValueError(f"Resource library {library_key!r} not found")

        library_id = int(library["id"])
        kind = str(library["kind"])
        created_at = str(library["created_at"])

        # 1. Check metadata drift (409 Conflict)
        if (
            library_id != int(token_payload.get("library_id", 0))
            or kind != str(token_payload.get("library_kind", ""))
            or created_at != str(token_payload.get("library_created_at", ""))
        ):
            raise TranslationConflictError(
                "Resource library metadata changed since preview; generate a fresh preview before applying."
            )

        # 2. Check fingerprint and map digest drift (409 Conflict)
        current_map_digest = compute_map_digest(validated_map)
        current_fingerprint = compute_library_fingerprint(library_id)

        if (
            current_map_digest != token_payload["map_digest"]
            or current_fingerprint != token_payload["library_fingerprint"]
        ):
            raise TranslationConflictError(
                "Resource library state or translation map changed since preview; generate a fresh preview before applying."
            )

        # 3. Validate map fields for current kind (422 Unprocessable Entity)
        _validate_map_fields_for_kind(validated_map, kind)

        revisions = resource_store.list_revisions(library_id)
        for rev in revisions:
            rev_id = int(rev["id"])
            payload = rev["payload"]
            existing_trans = rev["translation"] or {}
            canonical_existing = resource_readiness.validate_and_canonicalize_existing_translation(
                kind, payload, existing_trans
            )
            updates, _ = match_translation_map(payload, validated_map, kind)
            merged = {**canonical_existing, **updates}

            report = resource_readiness.evaluate_readiness(kind, payload, merged)
            stored_coverage = rev.get("coverage") or {}
            cov_needs_update = (report.coverage != stored_coverage)
            trans_needs_update = (merged != existing_trans)

            if trans_needs_update:
                resource_store.update_translation(rev_id, merged)
                resource_readiness.set_revision_readiness(rev_id, report.coverage)
                updated += 1
            elif cov_needs_update:
                resource_readiness.set_revision_readiness(rev_id, report.coverage)
                unchanged += 1
            else:
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
    if not isinstance(translation_updates, dict):
        raise TypeError(f"translation_updates must be a dict, got {type(translation_updates).__name__}")

    with db.transaction():
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

        payload = rev["payload"]
        existing_trans = rev["translation"] or {}

        # 1. Validate existing sidecar
        canonical_existing = resource_readiness.validate_and_canonicalize_existing_translation(
            kind, payload, existing_trans
        )

        # 2. Validate incoming updates
        canonical_updates: dict[str, Any] = {}
        updates_by_family: dict[str, list[tuple[str, Any]]] = {}
        for raw_k, val in translation_updates.items():
            c_k = resource_prompts.canonical_field_name(str(raw_k))
            updates_by_family.setdefault(c_k, []).append((str(raw_k), val))

        for c_k, entries in sorted(updates_by_family.items()):
            info = resource_prompts.classify_field(kind, c_k)
            role = info.get("role")
            if role != resource_prompts.ROLE_DESCRIPTIVE_INPUT:
                for raw_k, _ in entries:
                    raise ValueError(
                        f"Disallowed field in translation updates: {raw_k!r} has role {role!r}; "
                        "only descriptive input fields may have translations"
                    )

            if len(entries) == 1:
                val = entries[0][1]
            else:
                first_val = entries[0][1]
                if all(v == first_val for _, v in entries[1:]):
                    val = first_val
                else:
                    alias_dict = {k: v for k, v in entries}
                    raise ValueError(
                        f"Conflicting alias entries in translation updates for canonical field {c_k!r}: {alias_dict!r}"
                    )

            resource_readiness.validate_translation_value_for_source(kind, payload, c_k, val)
            canonical_updates[c_k] = val

        merged = {**canonical_existing, **canonical_updates}

        # Validate merged result strictly
        resource_readiness.validate_and_canonicalize_existing_translation(kind, payload, merged)

        rev_id = int(rev["id"])
        resource_store.update_translation(rev_id, merged)
        report = resource_readiness.evaluate_readiness(kind, payload, merged)
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
