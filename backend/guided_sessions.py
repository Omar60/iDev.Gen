"""Closed request handling and atomic persistence for guided sessions."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, field_validator

import db
import resource_readiness
import resource_store
from backend import resource_selection, session_plan, workflow_binding


_POLICY_FIELDS = ("camera", "framing", "pose", "expression")


def _all_vary_policy() -> dict[str, dict[str, str]]:
    return {field: {"mode": "vary"} for field in _POLICY_FIELDS}


class GuidedSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: StrictStr
    character_id: StrictInt
    workflow_id: StrictInt | None = None
    scene_anchor: dict[str, Any]
    photo_count: StrictInt
    brief: StrictStr = ""
    mode: Literal["automatic", "manual"] = "automatic"
    variation_policy: dict[str, Any] = Field(default_factory=_all_vary_policy)
    look: StrictStr = ""
    initial_wardrobe: StrictStr = ""

    @field_validator("request_id", mode="before")
    @classmethod
    def normalize_request_id(cls, value: Any) -> str:
        return resource_selection.normalize_client_uuid(value)

    @field_validator("character_id", mode="before")
    @classmethod
    def validate_character_id(cls, value: Any) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("character_id must be a positive integer")
        return value

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, value: Any) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value <= 0:
            raise ValueError("workflow_id must be null or a positive integer")
        return value

    @field_validator("photo_count", mode="before")
    @classmethod
    def validate_photo_count(cls, value: Any) -> int:
        return session_plan.validate_authoring_count(value)

    @field_validator("brief", mode="before")
    @classmethod
    def validate_brief(cls, value: Any) -> str:
        return session_plan.validate_authoring_brief(value)

    @field_validator("scene_anchor", mode="before")
    @classmethod
    def validate_scene_anchor(cls, value: Any) -> dict[str, str]:
        required = {"library_key", "source_id", "content_digest"}
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("scene_anchor must contain exactly library_key, source_id and content_digest")
        if any(not isinstance(value[key], str) or not value[key].strip() for key in required):
            raise ValueError("scene_anchor values must be non-empty strings")
        digest = value["content_digest"]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("scene_anchor.content_digest must be 64 lowercase hexadecimal characters")
        return {key: value[key] for key in ("library_key", "source_id", "content_digest")}

    @field_validator("variation_policy", mode="before")
    @classmethod
    def validate_variation_policy(cls, value: Any) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict) or set(value) != set(_POLICY_FIELDS):
            raise ValueError("variation_policy must contain exactly camera, framing, pose and expression")
        normalized: dict[str, dict[str, str]] = {}
        for field in _POLICY_FIELDS:
            dimension = value[field]
            if not isinstance(dimension, dict):
                raise ValueError(f"variation_policy.{field} must be an object")
            if dimension.get("mode") == "vary" and set(dimension) == {"mode"}:
                normalized[field] = {"mode": "vary"}
            elif (
                dimension.get("mode") == "fixed"
                and set(dimension) == {"mode", "value", "value_origin"}
                and isinstance(dimension.get("value"), str)
                and bool(dimension["value"])
                and dimension.get("value_origin") == "user"
            ):
                normalized[field] = {
                    "mode": "fixed",
                    "value": dimension["value"],
                    "value_origin": "user",
                }
            else:
                raise ValueError(f"variation_policy.{field} must be a closed vary or fixed value")
        return normalized


@dataclass(frozen=True)
class NormalizedGuidedRequest:
    request_id: str
    body: dict[str, Any]
    body_digest: str


class GuidedSessionError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def normalize_request(payload: Any) -> NormalizedGuidedRequest:
    try:
        parsed = GuidedSessionIn.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors()
        if any(error.get("type") == "extra_forbidden" for error in errors):
            code, message = "extra_field_forbidden", "Unknown guided creation fields are not permitted."
        elif any(error.get("type") == "missing" for error in errors):
            code, message = "missing_field", "A required guided creation field is missing."
        else:
            code, message = "invalid_request", "The guided creation request does not match its contract."
        raise GuidedSessionError(422, code, message) from exc

    values = parsed.model_dump()
    request_id = values.pop("request_id")
    return NormalizedGuidedRequest(
        request_id=request_id,
        body=values,
        body_digest=resource_store.canonical_digest(values),
    )


def _validate_ready_room(anchor: dict[str, str]) -> None:
    library = db.one(
        "SELECT id, kind FROM resource_library WHERE library_key = ?",
        anchor["library_key"],
    )
    if library is None or library["kind"] != "rooms":
        raise GuidedSessionError(
            422,
            "scene_anchor_not_ready",
            "Choose an existing ready rooms revision for the scene.",
        )
    try:
        revision = resource_store.get_revision(
            library_id=library["id"],
            source_id=anchor["source_id"],
            content_digest=anchor["content_digest"],
        )
    except (KeyError, TypeError, ValueError):
        revision = None
    if revision is None:
        raise GuidedSessionError(
            422,
            "scene_anchor_not_ready",
            "The selected scene revision does not exist; choose a current ready rooms revision.",
        )
    try:
        ready = resource_readiness.evaluate_readiness(
            "rooms", revision["payload"], revision.get("translation"),
        ).is_ready
    except (TypeError, ValueError):
        ready = False
    if not ready:
        raise GuidedSessionError(
            422,
            "scene_anchor_not_ready",
            "The selected rooms revision is not ready; resolve its required translations first.",
        )


def _initial_plan(body: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    look = body["look"]
    wardrobe = body["initial_wardrobe"]
    anchor = body["scene_anchor"]
    return session_plan.validate_draft({
        "version": session_plan.MODE_RESOURCE_V1,
        "look": look,
        "initial_wardrobe": wardrobe,
        "takes": [
            {"take_id": f"take-{index:03d}"}
            for index in range(1, body["photo_count"] + 1)
        ],
        "selected_resources": [anchor],
        "wardrobe_changes": [],
        "authoring": {
            "schema_version": 1,
            "mode": body["mode"],
            "brief": body["brief"],
            "scene_anchor": anchor,
            "workflow_binding": binding,
            "variation_policy": body["variation_policy"],
            "shared_state": {
                "look": {"origin": "user" if look else "none", "evidence_id": None},
                "initial_wardrobe": {
                    "origin": "user" if wardrobe else "none",
                    "evidence_id": None,
                },
            },
            "evidence": [],
            "look_snapshot": None,
            "wardrobe_progression": None,
        },
    })


def create_or_replay(request: NormalizedGuidedRequest) -> tuple[str, bool]:
    """Persist a new guided session or return the stored response for a retry."""
    with db.transaction():
        existing = db.one(
            "SELECT request_digest, response_json FROM guided_session_request WHERE request_id = ?",
            request.request_id,
        )
        if existing is not None:
            if existing["request_digest"] != request.body_digest:
                raise GuidedSessionError(
                    409,
                    "idempotency_conflict",
                    "request_id is already bound to different guided session content.",
                )
            return existing["response_json"], False

        body = request.body
        model = db.one("SELECT * FROM model WHERE id = ?", body["character_id"])
        if model is None:
            raise GuidedSessionError(422, "character_not_found", "Choose an existing character.")

        _validate_ready_room(body["scene_anchor"])
        try:
            settings = workflow_binding.effective_session_settings(model)
            binding = workflow_binding.resolve_effective_workflow(
                body["character_id"],
                body["workflow_id"],
                plan=None,
                settings=settings,
            )
        except workflow_binding.WorkflowRequired as exc:
            raise GuidedSessionError(422, exc.code, exc.message) from exc
        except workflow_binding.WorkflowCompatibilityError as exc:
            raise GuidedSessionError(422, exc.code, exc.message) from exc

        plan = _initial_plan(body, binding)
        plan["conflicts"] = session_plan.detect_resource_constant_conflicts(plan)
        settings["composition_mode"] = session_plan.MODE_RESOURCE_V1
        now = db.now()
        session_id = db.run(
            """INSERT INTO session (
                   model_id, name, look, wardrobe, workflow_id,
                   reference_workflow_id, anchor_shot_ids, settings,
                   manner, checkpoint, room_key, created_at
               ) VALUES (?, '', '', '', ?, NULL, '[]', ?, '', '', '', ?)""",
            body["character_id"], binding["workflow_id"],
            json.dumps(settings, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            now,
        )
        plan_json = json.dumps(plan, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        db.run(
            """INSERT INTO session_plan
               (session_id, mode, plan_revision, plan_json, created_at, updated_at)
               VALUES (?, ?, 1, ?, ?, ?)""",
            session_id, session_plan.MODE_RESOURCE_V1, plan_json, now, now,
        )
        response_json = json.dumps(
            {"session_id": session_id, "plan_revision": 1, "plan": plan},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        db.run(
            """INSERT INTO guided_session_request
               (request_id, request_digest, session_id, response_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            request.request_id, request.body_digest, session_id, response_json, now,
        )
        return response_json, True
