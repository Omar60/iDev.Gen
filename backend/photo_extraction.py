"""Reviewed vision proposals for saved-look photo stages."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException

import db
import enhance
from backend import photo_staging
from backend import saved_looks
from backend import resource_planning

_PROPOSAL_ID = re.compile(r"^[0-9a-f]{32}$")
_UNRESOLVED_ID = re.compile(r"^u[1-9][0-9]{0,2}$")
_DATA_URI = re.compile(r"(?i)\bdata:")
_URI = re.compile(r"(?i)\b(?:https?|wss?|file|ftp)://\S+")
_SECRET_HEADER = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|cookie|set-cookie|api[_-]?(?:key|secret)|"
    r"access[_-]?token|client[_-]?secret|x-[\w-]*(?:key|secret|token))\s*[:=]"
    r"|\bbearer\s+\S+"
)
_LONG_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{64,}={0,2}(?![A-Za-z0-9+/=])")
_PROPOSAL_KEYS = {"appearance", "garments", "unresolved"}
_REQUEST_PARAMETER_KEYS = {"temperature", "stream", "response_format", "reasoning_effort"}
_EXTRACTION_INSTRUCTION = """Propose a reusable personal look from the supplied photograph.
Write in English and return exactly one JSON object with exactly these keys:
{"appearance":"string","garments":["visible garment wording",...],"unresolved":[{"id":"u1","detail":"uncertain visible detail"},...]}
Describe only clearly visible, general appearance that belongs in a constant look and separately list visible garments. Do not include a person's identity, name, character identity, background, setting, lighting, camera, pose, or anything hidden from view. Do not infer unseen garments or claim certainty about ambiguous details; add each ambiguity to unresolved with a unique ID u1, u2, and so on. Do not decide, rank, or suggest a garment removal order. The garment list is only a set of visible candidates; the user will arrange it. Use an empty string or empty list when there is no supported content. Do not include confidence scores or any other keys."""


class PhotoExtractionError(photo_staging.PhotoStageError):
    pass


def _writes_enabled(config: dict | None = None) -> bool:
    return resource_planning.is_enabled() and (
        config is None or resource_planning.is_enabled(config)
    )


def _fail(status: int, code: str, message: str) -> None:
    raise PhotoExtractionError(status, code, message)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unsafe_text(value: str, config: dict, encoded_image: str = "") -> bool:
    digest_only = re.fullmatch(r"[0-9a-f]{64}", value) is not None
    if (
        _DATA_URI.search(value)
        or _URI.search(value)
        or _SECRET_HEADER.search(value)
        or (_LONG_BASE64.search(value) and not digest_only)
    ):
        return True
    if encoded_image and len(encoded_image) >= 8 and encoded_image in value:
        return True
    lowered = value.casefold()
    for name in ("llm_url", "llm_key"):
        secret = config.get(name)
        if isinstance(secret, str) and len(secret.strip()) >= 4 and secret.strip().casefold() in lowered:
            return True
    endpoint = config.get("llm_url")
    if isinstance(endpoint, str) and endpoint.strip():
        raw_endpoint = endpoint.strip()
        parsed = urlsplit(raw_endpoint if "://" in raw_endpoint else f"//{raw_endpoint}")
        fragments = [
            parsed.netloc,
            parsed.hostname or "",
            parsed.username or "",
            parsed.password or "",
            parsed.path.rstrip("/"),
            parsed.query,
        ]
        request_path = parsed.path.rstrip("/")
        if not request_path.endswith("/chat/completions"):
            fragments.append(f"{request_path}/chat/completions")
        if any(fragment and fragment.casefold() in lowered for fragment in fragments):
            return True
    return False


def _check_safe_strings(value: object, config: dict, encoded_image: str = "") -> None:
    if isinstance(value, str):
        if _unsafe_text(value, config, encoded_image):
            _fail(422, "photo_review_invalid", "Photo text contains unsupported private content.")
    elif isinstance(value, dict):
        for child in value.values():
            _check_safe_strings(child, config, encoded_image)
    elif isinstance(value, list):
        for child in value:
            _check_safe_strings(child, config, encoded_image)


def _validate_output(value: object, config: dict, image_uri: str) -> dict:
    invalid = "The vision provider returned an invalid look proposal."
    if not isinstance(value, dict) or set(value) != _PROPOSAL_KEYS:
        _fail(502, "vision_request_failed", invalid)
    appearance = value["appearance"]
    garments = value["garments"]
    unresolved = value["unresolved"]
    if not isinstance(appearance, str) or len(appearance) > 4000:
        _fail(502, "vision_request_failed", invalid)
    if not isinstance(garments, list) or len(garments) > 64:
        _fail(502, "vision_request_failed", invalid)
    if any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in garments):
        _fail(502, "vision_request_failed", invalid)
    if not isinstance(unresolved, list) or len(unresolved) > 32:
        _fail(502, "vision_request_failed", invalid)
    ids: set[str] = set()
    normalized_unresolved = []
    for item in unresolved:
        if not isinstance(item, dict) or set(item) != {"id", "detail"}:
            _fail(502, "vision_request_failed", invalid)
        identity, detail = item["id"], item["detail"]
        if (
            not isinstance(identity, str)
            or not _UNRESOLVED_ID.fullmatch(identity)
            or identity in ids
            or not isinstance(detail, str)
            or not detail.strip()
            or len(detail) > 1000
        ):
            _fail(502, "vision_request_failed", invalid)
        ids.add(identity)
        normalized_unresolved.append({"id": identity, "detail": detail})

    result = {
        "appearance": appearance,
        "garments": list(garments),
        "unresolved": normalized_unresolved,
    }
    encoded_image = image_uri.split(",", 1)[1] if "," in image_uri else ""
    try:
        _check_safe_strings(result, config, encoded_image)
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        _fail(502, "vision_request_failed", invalid)
    except PhotoExtractionError:
        _fail(502, "vision_request_failed", invalid)
    return result


def _redact_request_evidence(
    evidence: object,
    *,
    image_uri: str,
    image: dict,
    config: dict,
) -> dict:
    invalid = "The vision provider returned invalid request evidence."
    if not isinstance(evidence, dict) or set(evidence) != {"messages", "model", "parameters"}:
        _fail(502, "vision_request_failed", invalid)
    if evidence["model"] != config.get("llm_vision_model", "").strip():
        _fail(502, "vision_request_failed", invalid)
    messages = evidence["messages"]
    parameters = evidence["parameters"]
    if not isinstance(messages, list) or not isinstance(parameters, dict):
        _fail(502, "vision_request_failed", invalid)
    if set(parameters) - _REQUEST_PARAMETER_KEYS:
        _fail(502, "vision_request_failed", invalid)
    projected = deepcopy(evidence)
    replaced = 0
    for message in projected["messages"]:
        if not isinstance(message, dict) or not isinstance(message.get("content"), (str, list)):
            _fail(502, "vision_request_failed", invalid)
        content = message["content"]
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    _fail(502, "vision_request_failed", invalid)
                if part.get("type") != "image_url":
                    continue
                image_part = part.get("image_url")
                if (
                    not isinstance(image_part, dict)
                    or set(image_part) != {"url"}
                    or image_part.get("url") != image_uri
                ):
                    _fail(502, "vision_request_failed", invalid)
                part["image_url"] = dict(image)
                replaced += 1
    if replaced != 1:
        _fail(502, "vision_request_failed", invalid)
    if not isinstance(projected["model"], str) or not projected["model"]:
        _fail(502, "vision_request_failed", invalid)
    if _unsafe_text(projected["model"], config):
        _fail(502, "vision_request_failed", invalid)
    try:
        _check_safe_strings(projected["messages"], config)
        _check_safe_strings(projected["parameters"], config)
        json.dumps(projected, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        _fail(502, "vision_request_failed", invalid)
    except PhotoExtractionError:
        _fail(502, "vision_request_failed", invalid)
    return projected


def _stage_error(row: dict | None) -> PhotoExtractionError:
    if row is None or row["state"] == "publishing":
        return PhotoExtractionError(404, "photo_stage_not_found", "Photo stage was not found.")
    if row["state"] == "expired":
        return PhotoExtractionError(410, "photo_stage_expired", "This photo stage has expired.")
    if row["state"] == "cancelled":
        return PhotoExtractionError(410, "photo_stage_cancelled", "This photo stage was cancelled.")
    return PhotoExtractionError(409, "photo_stage_saved", "This photo stage has already been saved.")


def _start_attempt(photo_id: str, proposal_id: str, image: dict, writes_enabled=None) -> int:
    with db.transaction():
        if writes_enabled is not None and not writes_enabled():
            raise PhotoExtractionError(
                503,
                "resource_planning_disabled",
                "Saved-look writing is disabled.",
            )
        row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
        if row is None or row["state"] != "staged":
            raise _stage_error(row)
        if datetime.now(timezone.utc) >= photo_staging._parse_iso(row["expires_at"]):
            raise PhotoExtractionError(410, "photo_stage_expired", "This photo stage has expired.")
        expected = {
            "source_sha256": image["sha256"],
            "media_type": image["media_type"],
            "byte_count": image["byte_count"],
            "width": image["width"],
            "height": image["height"],
        }
        if any(row[name] != value for name, value in expected.items()):
            raise PhotoExtractionError(409, "photo_stage_changed", "The staged photo changed; upload it again.")
        latest = db.one(
            "SELECT COALESCE(MAX(generation), 0) AS generation FROM photo_look_proposal WHERE photo_id = ?",
            photo_id,
        )["generation"]
        generation = latest + 1
        db.run(
            """INSERT INTO photo_look_proposal
               (photo_id, proposal_id, generation, state, created_at, image_sha256,
                media_type, byte_count, width, height)
               VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
            photo_id, proposal_id, generation, _now_iso(), image["sha256"],
            image["media_type"], image["byte_count"], image["width"], image["height"],
        )
    return generation


def _discard_attempt(photo_id: str, proposal_id: str) -> None:
    with db.transaction():
        db.run(
            "UPDATE photo_look_proposal SET state = 'failed' WHERE photo_id = ? AND proposal_id = ? AND state = 'pending'",
            photo_id, proposal_id,
        )


def _publish_attempt(
    photo_id: str,
    proposal_id: str,
    generation: int,
    image: dict,
    request_projection: dict,
    output: dict,
    writes_enabled,
) -> dict:
    failure = None
    published = False
    with db.transaction():
        row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
        if row is None or row["state"] != "staged":
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ? AND proposal_id = ?", photo_id, proposal_id)
            failure = _stage_error(row)
        elif datetime.now(timezone.utc) >= photo_staging._parse_iso(row["expires_at"]):
            db.run(
                "UPDATE look_photo_stage SET state = 'expired', cleanup_state = 'pending', cleanup_warning = '', updated_at = ? WHERE photo_id = ?",
                _now_iso(), photo_id,
            )
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", photo_id)
            photo_staging._schedule_cleanup(photo_id)
            failure = PhotoExtractionError(410, "photo_stage_expired", "This photo stage has expired.")
        elif not writes_enabled():
            db.run(
                "UPDATE photo_look_proposal SET state = 'failed' WHERE photo_id = ? AND proposal_id = ? AND state = 'pending'",
                photo_id, proposal_id,
            )
            failure = PhotoExtractionError(503, "resource_planning_disabled", "Saved-look writing is disabled.")
        else:
            attempt = db.one(
                "SELECT generation, state FROM photo_look_proposal WHERE photo_id = ? AND proposal_id = ?",
                photo_id, proposal_id,
            )
            latest = db.one(
                "SELECT COALESCE(MAX(generation), 0) AS generation FROM photo_look_proposal WHERE photo_id = ?",
                photo_id,
            )["generation"]
            if attempt is None or attempt["state"] != "pending" or attempt["generation"] != generation or latest != generation:
                db.run(
                    "UPDATE photo_look_proposal SET state = 'failed' WHERE photo_id = ? AND proposal_id = ? AND state = 'pending'",
                    photo_id, proposal_id,
                )
                failure = PhotoExtractionError(409, "photo_proposal_stale", "A newer extraction request replaced this proposal.")
            else:
                db.run(
                    "DELETE FROM photo_look_proposal WHERE photo_id = ? AND proposal_id <> ?",
                    photo_id, proposal_id,
                )
                db.run(
                    """UPDATE photo_look_proposal
                       SET state = 'ready', request_projection_json = ?, output_json = ?
                       WHERE photo_id = ? AND proposal_id = ? AND state = 'pending'""",
                    json.dumps(request_projection, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(output, ensure_ascii=False, separators=(",", ":")),
                    photo_id, proposal_id,
                )
                published = True
    if failure is not None:
        raise failure
    if not published:
        raise PhotoExtractionError(409, "photo_proposal_stale", "The extraction proposal is stale.")
    return {
        "proposal_id": proposal_id,
        "appearance": output["appearance"],
        "garments": output["garments"],
        "unresolved": output["unresolved"],
    }


async def extract(photo_id: str, config: dict, run_structured, writes_enabled) -> dict:
    """Ask for a proposal without holding a database transaction over HTTP."""
    def live_writes_enabled() -> bool:
        return _writes_enabled(config) and bool(writes_enabled())

    if not live_writes_enabled():
        raise PhotoExtractionError(503, "resource_planning_disabled", "Saved-look writing is disabled.")
    if not enhance.vision_configured(config):
        raise PhotoExtractionError(
            409,
            "vision_unavailable",
            "Photo reading needs a configured text assistant and an explicit vision model. Open Setup or describe it manually.",
        )
    data, image = photo_staging.read_staged_photo(photo_id)
    image_uri = f"data:{image['media_type']};base64,{base64.b64encode(data).decode('ascii')}"
    proposal_id = uuid4().hex
    if not live_writes_enabled():
        raise PhotoExtractionError(503, "resource_planning_disabled", "Saved-look writing is disabled.")
    generation = _start_attempt(photo_id, proposal_id, image, live_writes_enabled)
    request_evidence: dict[str, object] = {}
    instruction = enhance.EnhanceIn(instruction=_EXTRACTION_INSTRUCTION, image=image_uri)
    try:
        if not live_writes_enabled():
            raise PhotoExtractionError(
                503,
                "resource_planning_disabled",
                "Saved-look writing is disabled.",
            )
        output = await run_structured(
            config,
            instruction,
            image_uri,
            request_evidence=request_evidence,
        )
        if not live_writes_enabled():
            raise PhotoExtractionError(
                503,
                "resource_planning_disabled",
                "Saved-look writing is disabled.",
            )
        proposal = _validate_output(output, config, image_uri)
        request_projection = _redact_request_evidence(
            request_evidence,
            image_uri=image_uri,
            image=image,
            config=config,
        )
    except HTTPException:
        _discard_attempt(photo_id, proposal_id)
        raise
    except PhotoExtractionError:
        _discard_attempt(photo_id, proposal_id)
        raise
    except Exception:
        _discard_attempt(photo_id, proposal_id)
        raise PhotoExtractionError(
            502,
            "vision_request_failed",
            "The vision provider could not produce a usable look proposal.",
        ) from None
    try:
        return _publish_attempt(
            photo_id,
            proposal_id,
            generation,
            image,
            request_projection,
            proposal,
            live_writes_enabled,
        )
    finally:
        _discard_attempt(photo_id, proposal_id)


def save_review(photo_id: str, review: dict, config: dict, create_look, save_stage) -> dict:
    if not _writes_enabled(config):
        _fail(503, "resource_planning_disabled", "Saved-look writing is disabled.")
    proposal_id = review["proposal_id"]
    if not _PROPOSAL_ID.fullmatch(proposal_id):
        _fail(409, "photo_proposal_stale", "The extraction proposal is stale or does not belong to this photo stage.")
    if review["review_confirmed"] is not True or review["removal_order_confirmed"] is not True:
        _fail(422, "photo_review_required", "Review the proposal and confirm the complete garment order before saving.")
    replay = photo_staging.replay_saved_look(photo_id, review)
    if replay is not None:
        return replay
    submitted_garments = [dict(item) for item in review["garments"]]
    garments = [
        {"wording": item["wording"], "aside": item.get("aside", "")}
        for item in submitted_garments
    ]
    look_payload = {
        "name": review["name"],
        "appearance": review["appearance"],
        "garments": garments,
    }
    try:
        staged_bytes, _ = photo_staging.read_staged_photo(photo_id)
    except photo_staging.PhotoStageError:
        replay = photo_staging.replay_saved_look(photo_id, review)
        if replay is not None:
            return replay
        raise
    encoded_image = base64.b64encode(staged_bytes).decode("ascii")
    _check_safe_strings(look_payload, config, encoded_image)
    _check_safe_strings(review["unresolved_decisions"], config, encoded_image)

    def create_reviewed_look(payload: dict) -> dict:
        proposal_row = db.one(
            "SELECT * FROM photo_look_proposal WHERE photo_id = ? AND proposal_id = ? AND state = 'ready'",
            photo_id,
            proposal_id,
        )
        if proposal_row is None:
            _fail(409, "photo_proposal_stale", "The extraction proposal is stale or does not belong to this photo stage.")
        proposal = json.loads(proposal_row["output_json"])
        unresolved_ids = [item["id"] for item in proposal["unresolved"]]
        decisions = review["unresolved_decisions"]
        decision_ids = [item["id"] for item in decisions]
        if len(set(decision_ids)) != len(decision_ids) or set(decision_ids) != set(unresolved_ids):
            _fail(422, "photo_review_invalid", "Resolve or explicitly omit every unresolved detail before saving.")
        final_text = [look_payload["appearance"]]
        for garment in garments:
            final_text.extend((garment["wording"], garment["aside"]))
        for decision in decisions:
            if decision["action"] == "correct":
                correction = decision.get("correction")
                if not isinstance(correction, str) or not correction.strip() or not any(correction in text for text in final_text):
                    _fail(422, "photo_review_invalid", "Each corrected uncertainty must appear in the reviewed look text.")
            elif decision["action"] != "omit" or decision.get("correction") is not None:
                _fail(422, "photo_review_invalid", "Choose a correction or explicit omission for each unresolved detail.")

        result = create_look(payload)
        metadata = {
            "sha256": proposal_row["image_sha256"],
            "media_type": proposal_row["media_type"],
            "byte_count": proposal_row["byte_count"],
            "width": proposal_row["width"],
            "height": proposal_row["height"],
        }
        request_projection = json.loads(proposal_row["request_projection_json"])
        corrections = {
            "review_confirmed": True,
            "removal_order_confirmed": True,
            "name": review["name"],
            "appearance": review["appearance"],
            "garments": submitted_garments,
            "unresolved_decisions": decisions,
        }
        saved_looks.save_photo_evidence(
            result,
            metadata=metadata,
            request_projection=request_projection,
            output=proposal,
            corrections=corrections,
        )
        return result

    return save_stage(
        photo_id,
        look_payload,
        create_reviewed_look,
        expected_proposal_id=proposal_id,
        digest_payload=review,
    )
