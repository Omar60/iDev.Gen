"""Atomic look, automation-profile, evidence, and retry-receipt saves."""
from __future__ import annotations

import json
import re
import sqlite3
from uuid import UUID

import db
from backend import resource_store, saved_looks


_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PROPOSAL_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONFIRMATIONS = (
    "profile_review_confirmed",
    "inventory_complete_confirmed",
    "baseline_coverage_confirmed",
    "removal_order_confirmed",
)


def _error(status: int, code: str, message: str) -> None:
    raise saved_looks.SavedLookError(status, code, message)


def _request_uuid(value: object) -> str:
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        _error(422, "invalid_look", "Combined look request identity is invalid.")
    try:
        return str(UUID(value))
    except ValueError:
        _error(422, "invalid_look", "Combined look request identity is invalid.")


def _require_enabled(enabled) -> None:
    if enabled is not None:
        if not enabled():
            _error(503, "resource_planning_disabled", "Saved-look writing is disabled.")
    else:
        saved_looks._require_resource_planning_enabled()


def _validate_source_context(source: object) -> dict:
    if type(source) is not dict or not isinstance(source.get("kind"), str):
        _error(422, "invalid_look", "Combined look source is invalid.")
    kind = source["kind"]
    if kind == "manual" and set(source) == {"kind"}:
        return {"kind": kind}
    if kind == "photo-manual" and set(source) == {"kind", "photo_id"}:
        if not isinstance(source["photo_id"], str) or not source["photo_id"]:
            _error(422, "invalid_look", "Combined look source is invalid.")
        return {"kind": kind, "photo_id": source["photo_id"]}
    if kind == "photo-extracted" and set(source) == {
        "kind", "photo_id", "proposal_id", "unresolved_decisions",
        "review_confirmed", "removal_order_confirmed",
    }:
        if (
            not isinstance(source["photo_id"], str)
            or not source["photo_id"]
            or not isinstance(source["proposal_id"], str)
            or not _PROPOSAL_ID.fullmatch(source["proposal_id"])
            or type(source["unresolved_decisions"]) is not list
            or source["review_confirmed"] is not True
            or source["removal_order_confirmed"] is not True
        ):
            _error(422, "photo_review_required", "Review the photo proposal before saving.")
        return {
            "kind": kind,
            "photo_id": source["photo_id"],
            "proposal_id": source["proposal_id"],
            "unresolved_decisions": source["unresolved_decisions"],
            "review_confirmed": True,
            "removal_order_confirmed": True,
        }
    _error(422, "invalid_look", "Combined look source is invalid.")


def _automation_input(value: object) -> dict:
    required = {"profile", *_CONFIRMATIONS}
    allowed = required | {"suggestion"}
    if type(value) is not dict or set(value) - allowed or not required.issubset(value):
        _error(422, "invalid_look", "Combined automation review is invalid.")
    if any(type(value[field]) is not bool or value[field] is not True for field in _CONFIRMATIONS):
        _error(422, "automation_review_required", "Confirm the complete automation review before saving.")
    suggestion = value.get("suggestion")
    if suggestion is not None and (
        type(suggestion) is not dict
        or set(suggestion) != {"proposal_id", "draft_content_digest"}
        or not isinstance(suggestion["proposal_id"], str)
        or not _PROPOSAL_ID.fullmatch(suggestion["proposal_id"])
        or not isinstance(suggestion["draft_content_digest"], str)
        or not _SHA256.fullmatch(suggestion["draft_content_digest"])
    ):
        _error(422, "invalid_look", "Automation suggestion identity is invalid.")
    return {
        "profile": value["profile"],
        **{field: True for field in _CONFIRMATIONS},
        "suggestion": suggestion,
    }


def _request_input(
    request_id: str,
    operation: str,
    target: dict | None,
    source: dict,
    look_payload: dict,
    automation: dict,
) -> dict:
    return {
        "request_id": request_id,
        "operation": operation,
        "target": target,
        "source": source,
        "look": look_payload,
        "automation": automation,
    }


def _normalize_transport_look(look_payload: dict, operation: str) -> dict:
    """Collapse only transport spellings with identical save semantics."""
    normalized = dict(look_payload)
    if operation == "create":
        normalized.setdefault("appearance", "")
    if "garments" in normalized and normalized["garments"] is None:
        normalized["garments"] = []
    garments = normalized.get("garments")
    if type(garments) is list:
        normalized_garments = []
        for garment in garments:
            if type(garment) is not dict:
                normalized_garments.append(garment)
                continue
            item = dict(garment)
            # A missing key and an explicit null both describe a newly
            # allocated garment. For those rows, omitted aside has the same
            # saved value as an empty aside. Existing-key rows retain omission
            # because it means "inherit the immutable historical wording".
            if item.get("key") is None:
                item.pop("key", None)
                item.setdefault("aside", "")
            normalized_garments.append(item)
        normalized["garments"] = normalized_garments
    return normalized


def _target_and_history(operation: str, target: dict | None) -> tuple[dict | None, list[dict], dict | None]:
    if operation == "create" and target is None:
        return None, [], None
    if operation != "new_version" or type(target) is not dict or set(target) != {"look_key", "expected_version"}:
        _error(422, "invalid_look", "Combined look target is invalid.")
    look_key, expected = target["look_key"], target["expected_version"]
    if (
        not saved_looks._valid_key(look_key)
        or type(expected) is not int
        or not 1 <= expected <= saved_looks._SQLITE_INTEGER_MAX
    ):
        _error(422, "invalid_look", "Combined look target is invalid.")
    prior = saved_looks.get_version(look_key, expected)
    history = saved_looks._history(look_key)
    return {"look_key": look_key, "expected_version": expected}, history, prior


def _source_garments(
    look_payload: dict,
    history: list[dict],
    prior: dict | None,
) -> tuple[str, list[dict], dict, list[dict | None]]:
    appearance = look_payload.get("appearance", prior["appearance"] if prior else "")
    if type(appearance) is not str:
        _error(422, "invalid_look", "Look data is invalid.")

    if "outfit_key" in look_payload:
        outfit_key = look_payload["outfit_key"]
        outfit = None if outfit_key is None else saved_looks._resolve_outfit_key(outfit_key, history)
        garments = [] if outfit is None else outfit["garments"]
        reference_definitions = [dict(garment) for garment in garments]
        source_mode = {"kind": "outfit_key", "outfit_key": outfit_key}
    elif "garments" in look_payload:
        definitions = look_payload["garments"]
        if definitions is None:
            definitions = []
        if type(definitions) is not list:
            _error(422, "invalid_look", "Look data is invalid.")
        garments = []
        reference_definitions = []
        for definition in definitions:
            if type(definition) is not dict or set(definition) - {"key", "wording", "aside"}:
                _error(422, "invalid_look", "Look data is invalid.")
            key = definition.get("key")
            if key is None:
                if "wording" not in definition:
                    _error(422, "invalid_look", "Look data is invalid.")
                wording = definition["wording"]
                aside = definition.get("aside", "")
                reference_definitions.append(None)
            else:
                known = saved_looks._known_garment(key, history)
                if known is None:
                    _error(422, "invalid_look", "Look data is invalid.")
                wording = definition.get("wording", known["wording"])
                aside = definition.get("aside", known["aside"])
                reference_definitions.append(dict(known))
            garments.append({"key": key, "wording": wording, "aside": aside})
        source_mode = {"kind": "garments"}
    else:
        outfit = None if prior is None else prior["outfit"]
        garments = [] if outfit is None else outfit["garments"]
        reference_definitions = [dict(garment) for garment in garments]
        source_mode = {
            "kind": "inherited" if prior is not None else "none",
            "base": None if prior is None else {
                "look_key": prior["key"],
                "version": prior["version"],
                "content_digest": prior["content_digest"],
            },
        }

    source_args = [
        {"key": item.get("key"), "wording": item["wording"], "aside": item["aside"]}
        for item in garments
    ]
    source = saved_looks.canonicalize_prospective_automation_source(appearance, source_args)
    return appearance, source_args, {**source, "mode": source_mode}, reference_definitions


def _load_suggestion(source: dict, reference: dict | None) -> dict | None:
    if reference is None:
        return None
    if source["kind"] == "photo-extracted":
        if reference["proposal_id"] != source["proposal_id"]:
            _error(409, "automation_proposal_stale", "Automation proposal is stale or belongs to a different photo review.")
        from backend import photo_extraction

        proposal = photo_extraction.get_automation_proposal(
            source["photo_id"], reference["proposal_id"], reference["draft_content_digest"],
        )
        if proposal["status"] != "ready" or "profile" not in proposal:
            _error(409, "automation_proposal_stale", "The photo automation proposal is unavailable; review its facts manually.")
        ordinary_row = db.one(
            "SELECT output_json FROM photo_look_proposal WHERE photo_id = ? AND proposal_id = ?",
            source["photo_id"], reference["proposal_id"],
        )
        if ordinary_row is None:
            _error(409, "automation_proposal_stale", "The photo automation proposal is unavailable; review its facts manually.")
        ordinary = json.loads(ordinary_row["output_json"])
        original_source = saved_looks.canonicalize_prospective_automation_source(
            ordinary["appearance"],
            [{"key": None, "wording": wording, "aside": ""} for wording in ordinary["garments"]],
        )
        return {
            "kind": "photo",
            "proposal_id": reference["proposal_id"],
            "draft_content_digest": reference["draft_content_digest"],
            "source": original_source,
            "profile": proposal["profile"],
            "request_projection": proposal["request_projection"],
        }
    from backend import look_automation

    proposal = look_automation.get_manual_proposal(
        reference["proposal_id"], reference["draft_content_digest"],
    )
    source_row = db.one(
        "SELECT source_json FROM saved_look_automation_proposal WHERE proposal_id = ?",
        reference["proposal_id"],
    )
    if source_row is None:
        _error(409, "automation_proposal_stale", "The automation proposal is unavailable; review its facts manually.")
    original_source = json.loads(source_row["source_json"])
    return {
        "kind": "manual",
        "proposal_id": reference["proposal_id"],
        "draft_content_digest": reference["draft_content_digest"],
        "source": original_source,
        "profile": proposal["profile"],
        "request_projection": proposal["request_projection"],
    }


def _photo_stage_evidence(source: dict) -> dict | None:
    if not source["kind"].startswith("photo-"):
        return None
    row = db.one(
        """SELECT source_sha256, media_type, byte_count, width, height
           FROM look_photo_stage WHERE photo_id = ?""",
        source["photo_id"],
    )
    if row is None:
        from backend.photo_staging import PhotoStageError

        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    return {
        "sha256": row["source_sha256"],
        "media_type": row["media_type"],
        "byte_count": row["byte_count"],
        "width": row["width"],
        "height": row["height"],
    }


def _user_profile(mapped: dict, saved: dict) -> dict:
    revision = 1
    provenance = {
        "origin": "user",
        "source_identity": "looks-integrated-review",
        "source_digest": saved["content_digest"],
        "accepted_revision": revision,
    }
    if any(segment["owner"] is None for segment in mapped["appearance_segments"]):
        _error(422, "automation_profile_not_ready", "Assign every appearance fact before the combined save.")
    return {
        "schema_version": 1,
        "look": mapped["look"],
        "appearance_segments": [
            {**segment, "provenance": dict(provenance)}
            for segment in mapped["appearance_segments"]
        ],
        "garment_coverage": {
            key: {
                state: {**fact, "provenance": dict(provenance)}
                for state, fact in states.items()
            }
            for key, states in mapped["garment_coverage"].items()
        },
        "baseline_coverage": (
            None if mapped["baseline_coverage"] is None
            else {**mapped["baseline_coverage"], "provenance": dict(provenance)}
        ),
        "inventory_complete": {
            "value": mapped["inventory_complete"],
            "provenance": dict(provenance),
        },
        "authored_provenance": dict(provenance),
    }


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        _error(422, "invalid_look", "Combined look request is invalid.")


def _validate_receipt_source(source: object, look: dict) -> dict:
    canonical = _validate_source_context(source)
    if canonical != source:
        raise ValueError("receipt source is not canonical")
    if canonical["kind"] == "photo-extracted":
        decisions = canonical["unresolved_decisions"]
        if len(decisions) > 32:
            raise ValueError("receipt review is invalid")
        seen: set[str] = set()
        for decision in decisions:
            if (
                type(decision) is not dict
                or set(decision) - {"id", "action", "correction"}
                or not isinstance(decision.get("id"), str)
                or not re.fullmatch(r"u[1-9][0-9]{0,2}", decision["id"])
                or decision["id"] in seen
                or decision.get("action") not in {"correct", "omit"}
            ):
                raise ValueError("receipt review is invalid")
            seen.add(decision["id"])
            correction = decision.get("correction")
            if decision["action"] == "correct":
                if not isinstance(correction, str) or not correction.strip() or len(correction) > 1000:
                    raise ValueError("receipt correction is invalid")
                final_text = [look.get("appearance", "")]
                if type(look.get("garments")) is list:
                    for garment in look["garments"]:
                        if type(garment) is dict:
                            final_text.extend((garment.get("wording", ""), garment.get("aside", "")))
                if not any(correction in text for text in final_text if isinstance(text, str)):
                    raise ValueError("receipt correction is absent from the accepted look")
            elif correction is not None:
                raise ValueError("receipt omission is invalid")
    return canonical


def _validate_receipt_look_resolution(
    look: dict,
    operation: str,
    target: dict | None,
    resolved: dict,
    source_args: list[dict],
    reference_definitions: list[dict | None],
) -> None:
    """Bind submitted look fields to the accepted, server-resolved source."""
    if "appearance" in look:
        if look["appearance"] != resolved["appearance"]:
            raise ValueError("receipt appearance differs from its resolved source")
    elif operation == "new_version":
        base = saved_looks.get_version(target["look_key"], target["expected_version"])
        if base["appearance"] != resolved["appearance"]:
            raise ValueError("receipt inherited appearance differs from its source version")
    elif resolved["appearance"] != "":
        raise ValueError("receipt create appearance has no submitted value")

    if "garments" in look:
        submitted_garments = look["garments"]
        if len(submitted_garments) != len(source_args):
            raise ValueError("receipt garment count differs from its resolved source")
        for submitted, resolved_garment, original in zip(
            submitted_garments, source_args, reference_definitions,
        ):
            key = submitted.get("key")
            if key is None:
                if (
                    resolved_garment.get("key") is not None
                    or original is not None
                    or "wording" not in submitted
                    or submitted["wording"] != resolved_garment["wording"]
                    or submitted.get("aside", "") != resolved_garment["aside"]
                ):
                    raise ValueError("receipt new garment differs from its resolved source")
                continue
            if (
                original is None
                or original["key"] != key
                or resolved_garment.get("key") != key
                or resolved_garment["wording"] != submitted.get("wording", original["wording"])
                or resolved_garment["aside"] != submitted.get("aside", original["aside"])
            ):
                raise ValueError("receipt referenced garment differs from its resolved source")
        return

    if "outfit_key" in look:
        if source_args != reference_definitions:
            raise ValueError("receipt outfit differs from its resolved definitions")
        return

    if operation == "new_version":
        base = saved_looks.get_version(target["look_key"], target["expected_version"])
        if resolved["mode"].get("kind") == "inherited" and (
            resolved["mode"]["base"]["content_digest"] != base["content_digest"]
        ):
            raise ValueError("receipt inherited source version is inconsistent")
        base_garments = [] if base["outfit"] is None else base["outfit"]["garments"]
        if source_args != base_garments or reference_definitions != base_garments:
            raise ValueError("receipt inherited garments differ from their source version")
    elif source_args or reference_definitions:
        raise ValueError("receipt create has an unsubmitted garment source")


def _validate_receipt_identity(
    identity: object,
    suggestion_evidence: object,
    request_id: str,
    input_digest: str,
) -> dict:
    expected_fields = {
        "request_id", "operation", "target", "source", "look", "automation",
        "input_digest", "resolved_source",
    }
    if type(identity) is not dict or set(identity) != expected_fields:
        raise ValueError("receipt identity has an invalid shape")
    if identity["request_id"] != request_id or identity["input_digest"] != input_digest:
        raise ValueError("receipt identity does not match its key")

    operation = identity["operation"]
    target = identity["target"]
    if operation == "create":
        if target is not None:
            raise ValueError("receipt create target is invalid")
    elif operation == "new_version":
        if (
            type(target) is not dict
            or set(target) != {"look_key", "expected_version"}
            or not saved_looks._valid_key(target["look_key"])
            or type(target["expected_version"]) is not int
            or not 1 <= target["expected_version"] <= saved_looks._SQLITE_INTEGER_MAX
        ):
            raise ValueError("receipt version target is invalid")
    else:
        raise ValueError("receipt operation is invalid")

    look = identity["look"]
    if type(look) is not dict:
        raise ValueError("receipt look input is invalid")
    source = _validate_receipt_source(identity["source"], look)
    if (
        type(look) is not dict
        or set(look) - {"name", "appearance", "garments", "outfit_key"}
        or "name" not in look
        or not isinstance(look["name"], str)
        or len(look["name"]) > 128
        or ("appearance" in look and (
            not isinstance(look["appearance"], str) or len(look["appearance"]) > 4000
        ))
        or ({"garments", "outfit_key"}.issubset(look))
        or _normalize_transport_look(look, operation) != look
        or ("outfit_key" in look and look["outfit_key"] is not None and not saved_looks._valid_key(look["outfit_key"]))
    ):
        raise ValueError("receipt look input is invalid")
    if "garments" in look:
        if type(look["garments"]) is not list or len(look["garments"]) > 64:
            raise ValueError("receipt garment input is invalid")
        for garment in look["garments"]:
            if (
                type(garment) is not dict
                or set(garment) - {"key", "wording", "aside"}
                or ("key" in garment and garment["key"] is not None and not saved_looks._valid_key(garment["key"]))
                or ("wording" in garment and not isinstance(garment["wording"], str))
                or ("aside" in garment and not isinstance(garment["aside"], str))
            ):
                raise ValueError("receipt garment input is invalid")
    if operation == "new_version" and type(target) is not dict:
        raise ValueError("receipt version target is invalid")

    automation = identity["automation"]
    normalized_automation = _automation_input(automation)
    if normalized_automation != automation:
        raise ValueError("receipt review is not canonical")
    if (
        source["kind"] == "photo-extracted"
        and automation["suggestion"] is not None
        and automation["suggestion"]["proposal_id"] != source["proposal_id"]
    ):
        raise ValueError("receipt photo proposal identity differs")

    resolved = identity["resolved_source"]
    if type(resolved) is not dict or set(resolved) != {
        "appearance", "garments", "draft_content_digest", "mode", "photo",
        "reference_definitions",
    }:
        raise ValueError("receipt resolved source is invalid")
    resolved_garments = resolved["garments"]
    if type(resolved_garments) is not list or len(resolved_garments) > 64:
        raise ValueError("receipt resolved garments are invalid")
    source_args = []
    for garment in resolved_garments:
        if (
            type(garment) is not dict
            or set(garment) - {"key", "wording", "aside"}
            or not {"wording", "aside"}.issubset(garment)
        ):
            raise ValueError("receipt resolved garment is invalid")
        source_args.append({
            "key": garment.get("key"),
            "wording": garment["wording"],
            "aside": garment["aside"],
        })
    canonical_source = saved_looks.canonicalize_prospective_automation_source(
        resolved["appearance"], source_args,
    )
    if (
        canonical_source["appearance"] != resolved["appearance"]
        or canonical_source["garments"] != resolved_garments
        or canonical_source["draft_content_digest"] != resolved["draft_content_digest"]
    ):
        raise ValueError("receipt resolved source digest is invalid")
    reference_definitions = resolved["reference_definitions"]
    if type(reference_definitions) is not list or len(reference_definitions) != len(source_args):
        raise ValueError("receipt source reference definitions are invalid")
    for source_garment, original in zip(source_args, reference_definitions):
        source_key = source_garment["key"]
        if source_key is None:
            if original is not None:
                raise ValueError("receipt has a reference for an unkeyed garment")
            continue
        if (
            type(original) is not dict
            or set(original) != {"key", "wording", "aside"}
            or original.get("key") != source_key
        ):
            raise ValueError("receipt source reference identity is invalid")
        canonical_original = saved_looks.canonicalize_prospective_automation_source("", [original])
        if canonical_original["garments"] != [original]:
            raise ValueError("receipt source reference definition is invalid")

    mode = resolved["mode"]
    if type(mode) is not dict:
        raise ValueError("receipt source mode is invalid")
    if "garments" in look:
        if mode != {"kind": "garments"}:
            raise ValueError("receipt garment source mode is invalid")
    elif "outfit_key" in look:
        if mode != {"kind": "outfit_key", "outfit_key": look["outfit_key"]}:
            raise ValueError("receipt outfit source mode is invalid")
    elif operation == "new_version":
        expected_base = {
            "look_key": target["look_key"],
            "version": target["expected_version"],
        }
        if (
            set(mode) != {"kind", "base"}
            or mode["kind"] != "inherited"
            or type(mode["base"]) is not dict
            or set(mode["base"]) != {"look_key", "version", "content_digest"}
            or {key: mode["base"][key] for key in expected_base} != expected_base
            or not saved_looks._valid_sha256(mode["base"]["content_digest"])
        ):
            raise ValueError("receipt inherited source mode is invalid")
    elif mode != {"kind": "none"}:
        raise ValueError("receipt empty source mode is invalid")

    _validate_receipt_look_resolution(
        look, operation, target, resolved, source_args, reference_definitions,
    )

    photo = resolved["photo"]
    if source["kind"].startswith("photo-"):
        if (
            type(photo) is not dict
            or set(photo) != {"sha256", "media_type", "byte_count", "width", "height"}
            or not saved_looks._valid_sha256(photo["sha256"])
            or photo["media_type"] not in {"image/jpeg", "image/png", "image/webp"}
            or type(photo["byte_count"]) is not int
            or not 1 <= photo["byte_count"] <= 10 * 1024 * 1024
            or type(photo["width"]) is not int
            or type(photo["height"]) is not int
            or photo["width"] <= 0
            or photo["height"] <= 0
            or photo["width"] * photo["height"] > 25_000_000
        ):
            raise ValueError("receipt photo evidence is invalid")
    elif photo is not None:
        raise ValueError("receipt has unexpected photo evidence")

    suggestion = automation["suggestion"]
    evidence = suggestion_evidence
    if suggestion is None:
        if evidence is not None:
            raise ValueError("receipt has unbound suggestion evidence")
    else:
        expected_kind = "photo" if source["kind"] == "photo-extracted" else "manual"
        if (
            type(evidence) is not dict
            or set(evidence) != {
                "kind", "proposal_id", "draft_content_digest", "source", "profile", "request_projection",
            }
            or evidence["kind"] != expected_kind
            or evidence["proposal_id"] != suggestion["proposal_id"]
            or evidence["draft_content_digest"] != suggestion["draft_content_digest"]
        ):
            raise ValueError("receipt suggestion evidence is unbound")
        original_source = evidence["source"]
        if type(original_source) is not dict or set(original_source) != {
            "appearance", "garments", "draft_content_digest",
        }:
            raise ValueError("receipt suggestion source is invalid")
        original_args = [
            {"key": item.get("key"), "wording": item["wording"], "aside": item["aside"]}
            for item in original_source["garments"]
        ]
        canonical_original = saved_looks.canonicalize_prospective_automation_source(
            original_source["appearance"], original_args,
        )
        if canonical_original != original_source or canonical_original["draft_content_digest"] != suggestion["draft_content_digest"]:
            raise ValueError("receipt suggestion digest is invalid")
        candidate = saved_looks.canonicalize_prospective_automation_profile(
            evidence["profile"], original_source["appearance"], original_args,
        )
        if (
            candidate != evidence["profile"]
            or candidate["baseline_coverage"] is not None
            or candidate["inventory_complete"] is not None
        ):
            raise ValueError("receipt suggestion candidate is invalid")
        projection = evidence["request_projection"]
        from backend import look_automation, photo_extraction
        if (
            type(projection) is not dict
            or set(projection) != {"model", "parameters", "messages_digest"}
            or not isinstance(projection["model"], str)
            or type(projection["parameters"]) is not dict
            or set(projection["parameters"]) - look_automation._REQUEST_PARAMETER_KEYS
            or not saved_looks._valid_sha256(projection["messages_digest"])
        ):
            raise ValueError("receipt suggestion request evidence is invalid")
        photo_extraction._check_safe_strings(projection, {})

    return {
        "source": source,
        "source_args": source_args,
        "reference_definitions": reference_definitions,
        "resolved": resolved,
    }


def _read_receipt(row: dict, request_id: str, input_digest: str) -> dict:
    if row["input_digest"] != input_digest:
        _error(409, "idempotency_conflict", "This request identity was already used for different look content.")
    try:
        request = json.loads(row["request_json"])
        result = json.loads(row["result_json"])
        if (
            type(request) is not dict
            or set(request) != {"identity", "suggestion_evidence", "input_projection"}
            or type(request["identity"]) is not dict
            or resource_store.canonical_digest(request) != row["request_digest"]
            or type(result) is not dict
            or set(result) != {
                "key", "version", "name", "content_digest", "appearance", "outfit", "automation_profile",
            }
        ):
            raise ValueError("receipt identity is invalid")
        validated = _validate_receipt_identity(
            request["identity"], request["suggestion_evidence"], request_id, input_digest,
        )
        input_projection = request["input_projection"]
        if type(input_projection) is not dict or set(input_projection) != {"automation_profile"}:
            raise ValueError("receipt input projection has an invalid shape")
        input_profile = input_projection["automation_profile"]
        canonical_input_profile = saved_looks.canonicalize_prospective_automation_profile(
            input_profile,
            validated["resolved"]["appearance"],
            validated["source_args"],
        )
        identity = request["identity"]
        if canonical_input_profile != identity["automation"]["profile"]:
            raise ValueError("receipt input profile differs from its reviewed profile")
        input_body = _request_input(
            identity["request_id"],
            identity["operation"],
            identity["target"],
            identity["source"],
            identity["look"],
            {
                "profile": input_profile,
                **{field: True for field in _CONFIRMATIONS},
                "suggestion": identity["automation"]["suggestion"],
            },
        )
        if resource_store.canonical_digest(input_body) != input_digest:
            raise ValueError("receipt input projection does not match its digest")
        # Suggestion evidence is deliberately part of the closed durable
        # request envelope, not a replay-time dependency on expiring rows.
        if (
            type(result["key"]) is not str
            or type(result["version"]) is not int
            or type(result["name"]) is not str
            or not saved_looks._valid_sha256(result["content_digest"])
            or type(result["appearance"]) is not str
        ):
            raise ValueError("receipt result identity is invalid")
        saved = saved_looks.get_version(result["key"], result["version"])
        if saved != {key: result[key] for key in saved}:
            raise ValueError("receipt look is invalid")
        profile_identity = result["automation_profile"]
        if type(profile_identity) is not dict or set(profile_identity) != {
            "look_key", "version", "content_digest", "profile_revision", "profile_digest", "readiness",
        }:
            raise ValueError("receipt profile identity has an invalid shape")
        readiness_identity = profile_identity["readiness"]
        if type(readiness_identity) is not dict or set(readiness_identity) != {
            "profile_ready", "manual_available", "diagnostics",
        }:
            raise ValueError("receipt readiness has an invalid shape")
        profile = saved_looks.get_automation_profile(
            profile_identity["look_key"], profile_identity["version"],
            profile_identity["profile_revision"], profile_identity["content_digest"],
            profile_identity["profile_digest"],
        )
        readiness = saved_looks.get_automation_profile_readiness(
            profile_identity["look_key"], profile_identity["version"],
            profile_identity["content_digest"], profile_identity["profile_revision"],
            profile_identity["profile_digest"],
        )
        if (
            profile["profile_revision"] != 1
            or profile_identity["profile_revision"] != 1
            or not readiness["profile_ready"]
            or readiness["manual_available"] is not True
            or readiness != profile_identity["readiness"]
            or (profile_identity["look_key"], profile_identity["version"], profile_identity["content_digest"])
            != (saved["key"], saved["version"], saved["content_digest"])
            or saved["name"] != request["identity"]["look"]["name"]
            or saved["appearance"] != validated["resolved"]["appearance"]
            or (
                request["identity"]["operation"] == "create"
                and (saved["version"] != 1 or request["identity"]["target"] is not None)
            )
            or (
                request["identity"]["operation"] == "new_version"
                and (
                    saved["key"] != request["identity"]["target"]["look_key"]
                    or saved["version"] != request["identity"]["target"]["expected_version"] + 1
                )
            )
        ):
            raise ValueError("receipt profile is invalid")
        expected_profile = saved_looks.map_prospective_automation_profile(
            request["identity"]["automation"]["profile"],
            validated["resolved"]["appearance"],
            validated["source_args"],
            saved,
            resolved_source_definitions=validated["reference_definitions"],
        )
        expected_profile = _user_profile(expected_profile, saved)
        if {key: profile[key] for key in expected_profile} != expected_profile:
            raise ValueError("receipt accepted profile differs from its reviewed draft")
    except saved_looks.SavedLookError:
        _error(500, "combined_save_receipt_invalid", "Combined look save receipt is inconsistent.")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OverflowError, RecursionError):
        _error(500, "combined_save_receipt_invalid", "Combined look save receipt is inconsistent.")
    return result


def save(
    *,
    request_id: object,
    operation: str,
    look_payload: dict,
    automation: dict,
    create_look,
    source: dict | None = None,
    target: dict | None = None,
    source_save=None,
    enabled=None,
) -> dict:
    """Commit or replay one exact look/profile pair and its entire source operation."""
    canonical_id = _request_uuid(request_id)
    source_input = _validate_source_context(source or {"kind": "manual"})
    accepted_input = _automation_input(automation)
    if type(look_payload) is not dict:
        _error(422, "invalid_look", "Look data is invalid.")
    target_input = target
    normalized_look = _normalize_transport_look(look_payload, operation)
    input_body = _request_input(
        canonical_id, operation, target_input, source_input, normalized_look, accepted_input,
    )
    input_digest = resource_store.canonical_digest(input_body)

    _require_enabled(enabled)
    try:
        with db.transaction():
            _require_enabled(enabled)
            existing = db.one(
                "SELECT * FROM saved_look_combined_save_receipt WHERE request_id = ?",
                canonical_id,
            )
            if existing is not None:
                return _read_receipt(existing, canonical_id, input_digest)

            canonical_target, history, prior = _target_and_history(operation, target_input)
            appearance, source_args, canonical_source, reference_definitions = _source_garments(
                look_payload, history, prior,
            )
            canonical_profile = saved_looks.canonicalize_prospective_automation_profile(
                accepted_input["profile"], appearance, source_args,
            )
            suggestion_evidence = _load_suggestion(source_input, accepted_input["suggestion"])
            stage_evidence = _photo_stage_evidence(source_input)

            if operation == "new_version":
                saved_looks._require_active_look(canonical_target["look_key"])
                latest = history[0] if history else None
                if latest is None:
                    _error(404, "look_not_found", "Saved look was not found.")
                if latest["version"] != canonical_target["expected_version"]:
                    _error(409, "look_version_stale", "Saved look changed; reload before saving.")
                if latest["version"] == saved_looks._SQLITE_INTEGER_MAX:
                    _error(409, "look_version_limit", "Saved look cannot have another version.")
            elif operation != "create" or canonical_target is not None:
                _error(422, "invalid_look", "Combined look operation is invalid.")

            identity = {
                **input_body,
                "input_digest": input_digest,
                "target": canonical_target,
                "resolved_source": {
                    "appearance": appearance,
                    "garments": canonical_source["garments"],
                    "draft_content_digest": canonical_source["draft_content_digest"],
                    "reference_definitions": reference_definitions,
                    "mode": canonical_source["mode"],
                    "photo": stage_evidence,
                },
                "automation": {
                    "profile": canonical_profile,
                    **{field: True for field in _CONFIRMATIONS},
                    "suggestion": accepted_input["suggestion"],
                },
            }
            receipt_body = {
                "identity": identity,
                "suggestion_evidence": suggestion_evidence,
                # Preserve the request's profile ordering so its exact normalized
                # input digest remains independently verifiable on replay.
                "input_projection": {"automation_profile": accepted_input["profile"]},
            }
            request_digest = resource_store.canonical_digest(receipt_body)
            request_json = _canonical_json(receipt_body)

            def create_pair(payload: dict) -> dict:
                _require_enabled(enabled)
                saved = create_look(payload)
                mapped = saved_looks.map_prospective_automation_profile(
                    canonical_profile, appearance, source_args, saved,
                )
                profile = _user_profile(mapped, saved)
                published = saved_looks.publish_automation_profile(
                    saved["key"], saved["version"], saved["content_digest"], 0, profile,
                )
                readiness = saved_looks.get_automation_profile_readiness(
                    saved["key"], saved["version"], saved["content_digest"],
                    published["profile_revision"], published["profile_digest"],
                )
                if published["profile_revision"] != 1 or not readiness["profile_ready"]:
                    _error(422, "automation_profile_not_ready", "The reviewed profile is incomplete or conflicts with the saved look.")
                return {
                    **saved,
                    "automation_profile": {
                        "look_key": saved["key"],
                        "version": saved["version"],
                        "content_digest": saved["content_digest"],
                        "profile_revision": published["profile_revision"],
                        "profile_digest": published["profile_digest"],
                        "readiness": readiness,
                    },
                }

            result = source_save(create_pair) if source_save is not None else create_pair(look_payload)
            if (
                type(result) is not dict
                or "automation_profile" not in result
                or result["automation_profile"].get("profile_revision") != 1
            ):
                _error(409, "combined_save_incomplete", "Combined look save did not produce its reviewed profile.")
            _require_enabled(enabled)
            db.run(
                """INSERT INTO saved_look_combined_save_receipt
                   (request_id, request_digest, input_digest, request_json, result_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                canonical_id, request_digest, input_digest, request_json,
                _canonical_json(result), db.now(),
            )
            return result
    except sqlite3.IntegrityError:
        saved_looks._write_conflict()
