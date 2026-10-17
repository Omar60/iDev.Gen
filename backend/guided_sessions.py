"""Closed request handling and atomic persistence for guided sessions."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import uuid
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError,
    field_validator, model_validator,
)

import db
from backend import resource_planning
import resource_readiness
import resource_store
from backend import (
    comfy, resource_full_auto, resource_selection, saved_looks, session_plan,
    workflow_binding,
)


_POLICY_FIELDS = ("camera", "framing", "pose", "expression")
_CANVAS_FIELDS = frozenset({"width", "height"})
_CANVAS_STEP = 8
_FULL_BODY_FRAMING = "Full body, head to toe, both feet visible, with space above the head and below the feet."
_FULL_AUTO_AUTHORIZATION_VERSION = 1
_FULL_AUTO_SCOPE_MAX_AGE = timedelta(seconds=300)
_FULL_AUTO_AUTHORIZATION_LIFETIME = timedelta(days=7)
_FULL_AUTO_ASSISTANT_CALLS_PER_TAKE = 2
_FULL_AUTO_IMAGE_ATTEMPTS_PER_TAKE = 1
_FULL_AUTO_POLICY_VERSION = "resource-full-auto-v1"
_SQLITE_INTEGER_MAX = saved_looks._SQLITE_INTEGER_MAX
_ConfigInput = Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None


def _config_snapshot(config: _ConfigInput) -> dict[str, Any]:
    current = config() if callable(config) else config
    return dict(current or {})


def _all_vary_policy() -> dict[str, dict[str, str]]:
    return {field: {"mode": "vary"} for field in _POLICY_FIELDS}


def _positive_sqlite_integer(value: Any, field: str) -> int:
    if type(value) is not int or not 1 <= value <= _SQLITE_INTEGER_MAX:
        raise ValueError(f"{field} must be a positive SQLite-range integer")
    return value


def _validate_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be a canonical SHA-256 digest")
    return value


def _validate_utc_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("scope_issued_at must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("scope_issued_at must be a canonical UTC timestamp") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise ValueError("scope_issued_at must be a canonical UTC timestamp")
    return value


class SavedLookSelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    look_key: StrictStr
    version: StrictInt
    content_digest: StrictStr

    @field_validator("look_key", mode="before")
    @classmethod
    def validate_look_key(cls, value: Any) -> str:
        if not saved_looks._valid_key(value):
            raise ValueError("look_key must be a valid saved-look key")
        return value

    @field_validator("version", mode="before")
    @classmethod
    def validate_version(cls, value: Any) -> int:
        return _positive_sqlite_integer(value, "version")

    @field_validator("content_digest", mode="before")
    @classmethod
    def validate_content_digest(cls, value: Any) -> str:
        return _validate_sha256(value, "content_digest")


class FullAutoPreflightIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    authorization_version: StrictInt
    generate_without_pauses: StrictBool
    profile_revision: StrictInt
    profile_digest: StrictStr
    progression: Literal["all_stages_full_interval"]
    framing_intent: Literal["vary", "full_body"]

    @field_validator("authorization_version", mode="before")
    @classmethod
    def validate_authorization_version(cls, value: Any) -> int:
        if type(value) is not int or value != _FULL_AUTO_AUTHORIZATION_VERSION:
            raise ValueError("authorization_version must equal 1")
        return value

    @field_validator("generate_without_pauses", mode="before")
    @classmethod
    def validate_generate_consent(cls, value: Any) -> bool:
        if value is not True:
            raise ValueError("generate_without_pauses must be true")
        return value

    @field_validator("profile_revision", mode="before")
    @classmethod
    def validate_profile_revision(cls, value: Any) -> int:
        return _positive_sqlite_integer(value, "profile_revision")

    @field_validator("profile_digest", mode="before")
    @classmethod
    def validate_profile_digest(cls, value: Any) -> str:
        return _validate_sha256(value, "profile_digest")


class FullAutoCreationIn(FullAutoPreflightIn):
    scope_digest: StrictStr
    scope_issued_at: StrictStr

    @field_validator("scope_digest", mode="before")
    @classmethod
    def validate_scope_digest(cls, value: Any) -> str:
        return _validate_sha256(value, "scope_digest")

    @field_validator("scope_issued_at", mode="before")
    @classmethod
    def validate_scope_issued_at(cls, value: Any) -> str:
        return _validate_utc_timestamp(value)


class _GuidedSessionFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    character_id: StrictInt
    workflow_id: StrictInt | None = None
    scene_anchor: dict[str, Any]
    photo_count: StrictInt
    width: StrictInt | None = None
    height: StrictInt | None = None
    brief: StrictStr = ""
    mode: Literal["automatic", "manual"] = "automatic"
    variation_policy: dict[str, Any] = Field(default_factory=_all_vary_policy)
    look: StrictStr = ""
    initial_wardrobe: StrictStr = ""
    saved_look: SavedLookSelectionIn | None = None

    @field_validator("saved_look", mode="before")
    @classmethod
    def reject_explicit_null_saved_look(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("saved_look must be omitted or an object")
        return value

    @field_validator("character_id", mode="before")
    @classmethod
    def validate_character_id(cls, value: Any) -> int:
        return _positive_sqlite_integer(value, "character_id")

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, value: Any) -> int | None:
        if value is None:
            return None
        return _positive_sqlite_integer(value, "workflow_id")

    @field_validator("photo_count", mode="before")
    @classmethod
    def validate_photo_count(cls, value: Any) -> int:
        return session_plan.validate_authoring_count(value)

    @field_validator("width", "height", mode="before")
    @classmethod
    def validate_canvas_dimension(cls, value: Any) -> int:
        value = _positive_sqlite_integer(value, "canvas dimension")
        if value < _CANVAS_STEP or value % _CANVAS_STEP:
            raise ValueError("canvas dimensions must be positive multiples of 8")
        return value

    @model_validator(mode="after")
    def validate_canvas_override(self) -> _GuidedSessionFields:
        provided = self.model_fields_set & _CANVAS_FIELDS
        if provided and provided != _CANVAS_FIELDS:
            raise ValueError("width and height must be provided together")
        return self

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


class GuidedSessionIn(_GuidedSessionFields):
    request_id: StrictStr
    full_auto: FullAutoCreationIn | None = None

    @field_validator("request_id", mode="before")
    @classmethod
    def normalize_request_id(cls, value: Any) -> str:
        return resource_selection.normalize_client_uuid(value)

    @field_validator("full_auto", mode="before")
    @classmethod
    def reject_explicit_null_full_auto(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("full_auto must be omitted or an object")
        return value

    @model_validator(mode="after")
    def validate_saved_look_and_full_auto(self) -> GuidedSessionIn:
        if self.saved_look is not None and (self.look != "" or self.initial_wardrobe != ""):
            raise ValueError("saved_look cannot compete with look or initial_wardrobe overrides")
        if self.full_auto is not None:
            if self.saved_look is None:
                raise ValueError("Full Auto requires an exact saved_look selection")
            if self.mode != "automatic":
                raise ValueError("Full Auto requires automatic authoring mode")
        return self


class GuidedFullAutoPreflightIn(_GuidedSessionFields):
    full_auto: FullAutoPreflightIn

    @field_validator("full_auto", mode="before")
    @classmethod
    def reject_explicit_null_full_auto(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("full_auto must be an object")
        return value

    @model_validator(mode="after")
    def validate_preflight_inputs(self) -> GuidedFullAutoPreflightIn:
        if self.saved_look is None:
            raise ValueError("Full Auto requires an exact saved_look selection")
        if self.mode != "automatic":
            raise ValueError("Full Auto requires automatic authoring mode")
        if self.look != "" or self.initial_wardrobe != "":
            raise ValueError("saved_look cannot compete with look or initial_wardrobe overrides")
        return self


@dataclass(frozen=True)
class NormalizedGuidedRequest:
    request_id: str
    body: dict[str, Any]
    body_digest: str


class GuidedSessionError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str,
        current: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.current = current


def _request_validation_error(exc: ValidationError) -> GuidedSessionError:
    errors = exc.errors()
    if any(error.get("type") == "extra_forbidden" for error in errors):
        code, message = "extra_field_forbidden", "Unknown guided creation fields are not permitted."
    elif any(error.get("type") == "missing" for error in errors):
        code, message = "missing_field", "A required guided creation field is missing."
    else:
        code, message = "invalid_request", "The guided creation request does not match its contract."
    return GuidedSessionError(422, code, message)


def normalize_request(payload: Any) -> NormalizedGuidedRequest:
    try:
        parsed = GuidedSessionIn.model_validate(payload)
    except ValidationError as exc:
        raise _request_validation_error(exc) from exc

    values = parsed.model_dump()
    request_id = values.pop("request_id")
    # Keep the pre-override digest shape stable for clients retrying a request
    # created before width/height became optional guided fields.
    if not (parsed.model_fields_set & _CANVAS_FIELDS):
        values.pop("width")
        values.pop("height")
    if values.get("saved_look") is None:
        values.pop("saved_look")
    if values.get("full_auto") is None:
        values.pop("full_auto")
    return NormalizedGuidedRequest(
        request_id=request_id,
        body=values,
        body_digest=resource_store.canonical_digest(values),
    )


def normalize_preflight_request(payload: Any) -> dict[str, Any]:
    try:
        parsed = GuidedFullAutoPreflightIn.model_validate(payload)
    except ValidationError as exc:
        raise _request_validation_error(exc) from exc
    values = parsed.model_dump()
    if not (parsed.model_fields_set & _CANVAS_FIELDS):
        values.pop("width")
        values.pop("height")
    return values


def _validate_ready_room(anchor: dict[str, str]) -> tuple[dict, dict]:
    library = db.one(
        "SELECT * FROM resource_library WHERE library_key = ?",
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
    return library, revision


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


def _safe_full_auto_error(exc: Exception) -> GuidedSessionError:
    code = getattr(exc, "code", "workflow_compatibility_invalid")
    allowed = {"workflow_required", "workflow_compatibility_invalid"}
    if code not in allowed:
        code = "workflow_compatibility_invalid"
    message = (
        "Choose a compatible text-to-image workflow for Full Auto."
        if code == "workflow_required"
        else "The selected workflow is not compatible with Full Auto."
    )
    return GuidedSessionError(422, code, message)


def _selected_saved_look(selection: dict[str, Any]) -> dict:
    try:
        saved = saved_looks.get_active_version(selection["look_key"], selection["version"])
    except saved_looks.SavedLookError as exc:
        if exc.code == "look_not_found":
            raise GuidedSessionError(404, "look_not_found", "Saved look was not found.") from exc
        if exc.code == "look_archived":
            raise GuidedSessionError(409, "look_archived", exc.message) from exc
        raise GuidedSessionError(500, "saved_look_invalid", "The selected saved look could not be read.") from exc
    if saved["content_digest"] != selection["content_digest"]:
        raise GuidedSessionError(
            409, "saved_look_changed",
            "The selected saved-look version or snapshot digest cannot be verified.",
        )
    return saved


def _safe_profile_identifier(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 80
        or value != value.strip()
        or not resource_selection._is_safe_public_string(value)
        or any(not (char.isalnum() or char in "._-") for char in value)
    ):
        return None
    return value


def _profile_error_current(
    diagnostics: list[dict] | None,
    saved: dict | None,
) -> dict[str, Any]:
    """Project readiness diagnostics to safe identifiers and enum values."""
    diagnostic_codes = {
        "baseline_missing", "baseline_progression_conflict",
        "coverage_provenance_unaccepted", "coverage_state_missing",
        "coverage_unknown", "inventory_incomplete", "inventory_unconfirmed",
        "inventory_provenance_unaccepted", "appearance_progression_conflict",
        "appearance_provenance_unaccepted", "profile_provenance_unaccepted",
    }
    allowed_fields = {
        "appearance_segments", "authored_provenance", "baseline_coverage",
        "garment_coverage", "inventory_complete",
    }
    regions = {"chest", "hips", "legs", "feet"}
    stages = (
        saved_looks._automation_profile_coverage_stage_states(saved)
        if saved is not None else []
    )
    garments = {
        garment["key"]
        for garment in (
            [] if saved is None or saved["outfit"] is None
            else saved["outfit"]["garments"]
        )
    }
    projected: list[dict[str, Any]] = []
    for diagnostic in diagnostics or []:
        if not isinstance(diagnostic, dict):
            continue
        code = diagnostic.get("code")
        if code not in diagnostic_codes:
            continue
        item: dict[str, Any] = {"code": code}
        field = diagnostic.get("field")
        if field in allowed_fields:
            item["field"] = field
        elif code.startswith("baseline_"):
            item["field"] = "baseline_coverage"
        elif code.startswith("inventory_"):
            item["field"] = "inventory_complete"
        elif code.startswith("appearance_"):
            item["field"] = "appearance_segments"
        elif code.startswith("coverage_"):
            item["field"] = (
                "garment_coverage"
                if diagnostic.get("garment_key") in garments
                else "baseline_coverage"
            )
        elif code == "profile_provenance_unaccepted":
            item["field"] = "authored_provenance"

        region = diagnostic.get("region")
        if region in regions:
            item["region"] = region
        state = diagnostic.get("state")
        if state in {"worn", "aside"}:
            item["state"] = state

        garment_key = diagnostic.get("garment_key")
        if garment_key in garments:
            safe_key = _safe_profile_identifier(garment_key)
            if safe_key is not None:
                item["garment_key"] = safe_key
                if state in {"worn", "aside"}:
                    indices = [
                        index for index, stage in enumerate(stages)
                        if stage.get(garment_key) == state
                    ]
                elif code == "appearance_progression_conflict":
                    indices = [
                        index for index, stage in enumerate(stages)
                        if stage.get(garment_key) != "worn"
                    ]
                else:
                    indices = []
                if indices:
                    item["stage_indices"] = indices

        segment_id = _safe_profile_identifier(diagnostic.get("segment_id"))
        if segment_id is not None:
            item["segment_id"] = segment_id
        segment_ids = diagnostic.get("appearance_segment_ids")
        if isinstance(segment_ids, list):
            safe_ids = [
                identifier for value in segment_ids
                if (identifier := _safe_profile_identifier(value)) is not None
            ]
            if safe_ids:
                item["segment_ids"] = safe_ids

        if item.get("field") == "baseline_coverage" and stages:
            if code == "baseline_progression_conflict":
                item["stage_indices"] = [len(stages) - 1]
            elif code == "baseline_missing":
                item["stage_indices"] = list(range(len(stages)))
        projected.append(item)

    if not projected:
        projected = [{"code": "automation_profile_not_ready"}]
    return {
        "remedy": "update_automation_profile_in_looks",
        "manual_available": True,
        "affected": projected,
    }


def _profile_not_ready(
    diagnostics: list[dict] | None = None,
    *,
    saved: dict | None = None,
) -> GuidedSessionError:
    codes = {item.get("code") for item in diagnostics or []}
    if codes & {"coverage_unknown", "coverage_state_missing", "baseline_missing"}:
        code = "coverage_unknown"
        message = "The selected automation profile has unknown or missing coverage facts."
    elif "inventory_unconfirmed" in codes:
        code = "inventory_unconfirmed"
        message = "Confirm that the saved-look inventory is complete before using Full Auto."
    elif "appearance_progression_conflict" in codes:
        code = "appearance_progression_conflict"
        message = "Permanent appearance text conflicts with a wardrobe stage."
    elif "baseline_progression_conflict" in codes:
        code = "baseline_progression_conflict"
        message = "Baseline coverage conflicts with the selected wardrobe progression."
    else:
        code = "automation_profile_not_ready"
        message = "The exact automation profile is not ready for Full Auto."
    return GuidedSessionError(
        422, code, message,
        current=_profile_error_current(diagnostics, saved),
    )


def _insufficient_stage_count(required_count: int, available_count: int) -> GuidedSessionError:
    return GuidedSessionError(
        422,
        "insufficient_stage_count",
        f"Full Auto requires at least {required_count} takes to cover every saved-look stage; increase the count or continue manually.",
        current={
            "required_count": required_count,
            "available_count": available_count,
            "manual_available": True,
            "remedy": "increase_photo_count_or_use_manual_flow",
        },
    )


def _profile_application(
    saved: dict,
    full_auto: dict[str, Any],
) -> tuple[dict, dict]:
    try:
        application = saved_looks.get_reviewed_appearance_projection(
            saved["key"], saved["version"], full_auto["profile_revision"],
            saved["content_digest"], full_auto["profile_digest"],
        )
    except saved_looks.SavedLookError as exc:
        if exc.code == "automation_profile_stale":
            raise GuidedSessionError(
                409, "automation_profile_changed",
                "The selected automation-profile revision or digest cannot be verified.",
            ) from exc
        if exc.code == "look_not_found":
            raise GuidedSessionError(
                409, "saved_look_changed",
                "The selected saved-look version can no longer be verified.",
            ) from exc
        raise _profile_not_ready(saved=saved) from exc
    try:
        readiness = saved_looks.get_automation_profile_readiness(
            saved["key"], saved["version"], saved["content_digest"],
            full_auto["profile_revision"], full_auto["profile_digest"],
        )
    except saved_looks.SavedLookError as exc:
        if exc.code == "automation_profile_stale":
            raise GuidedSessionError(
                409, "automation_profile_changed",
                "The selected automation-profile revision or digest cannot be verified.",
            ) from exc
        raise _profile_not_ready(saved=saved) from exc
    if readiness["profile_ready"] is not True:
        raise _profile_not_ready(readiness.get("diagnostics"), saved=saved)
    return application, readiness


def _provider_identity(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    raw_url = config.get("llm_url", "")
    raw_model = config.get("llm_model", "")
    url = raw_url if isinstance(raw_url, str) else ""
    model = raw_model if isinstance(raw_model, str) else ""
    try:
        parsed = urlsplit(url)
        endpoint = {
            "scheme": parsed.scheme.lower(),
            "host": (parsed.hostname or "").lower(),
            "port": parsed.port,
            "path": parsed.path,
        }
    except ValueError:
        endpoint = {"configured": bool(url)}
    endpoint_identity = resource_store.canonical_digest(endpoint)
    model_identity = resource_store.canonical_digest({"model": model})
    safe_model = (
        model if model and len(model) <= 120
        and all(char.isalnum() or char in "._:-" for char in model)
        else "configured" if model else "unconfigured"
    )
    public = {
        "model": safe_model,
        "endpoint_identity": endpoint_identity,
    }
    if safe_model == "configured":
        public["model_identity"] = model_identity
    internal = {
        "endpoint_identity": endpoint_identity,
        "model": model,
    }
    return public, internal


def _validate_full_auto_canvas(
    body: dict[str, Any],
    settings: dict[str, Any],
    graph: dict,
    node_map: dict,
) -> dict[str, int]:
    explicit = "width" in body and "height" in body
    dimensions = {}
    for field in ("width", "height"):
        value = settings.get(field)
        if (
            type(value) is not int
            or not _CANVAS_STEP <= value <= _SQLITE_INTEGER_MAX
            or value % _CANVAS_STEP
        ):
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                "The effective canvas must use positive dimensions in multiples of 8.",
            )
        path = node_map.get(field)
        if path is None:
            if explicit:
                raise GuidedSessionError(
                    422, "workflow_compatibility_invalid",
                    "The selected workflow cannot apply the explicit canvas dimensions.",
                )
            dimensions[field] = value
            continue
        parts = path.split(".")
        if len(parts) != 3 or parts[1] != "inputs" or parts[2] != field:
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                "The selected workflow has an invalid canvas mapping.",
            )
        inputs = (graph.get(parts[0]) or {}).get("inputs")
        if not isinstance(inputs, dict) or parts[2] not in inputs:
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                "The selected workflow has an invalid canvas mapping.",
            )
        current = inputs[parts[2]]
        if isinstance(current, list):
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                "Connected workflow inputs cannot be replaced with canvas dimensions.",
            )
        if not (
            type(current) is int
            or (type(current) is float and math.isfinite(current))
        ):
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                "The selected workflow does not expose usable canvas dimensions.",
            )
        dimensions[field] = value
    return dimensions


def _validate_full_auto_selected_choices(
    model: dict,
    settings: dict[str, Any],
    graph: dict,
    node_map: dict,
    detected_map: dict,
) -> None:
    choices = workflow_binding.selected_workflow_choices(settings, model)
    for slot, value, label in choices:
        selected = bool(value)
        if not selected and slot not in node_map:
            continue
        expected = value
        if not selected:
            # With no override, compare against the workflow's own widget type;
            # do not copy its default into session settings or rewrite the graph.
            detected_path = detected_map.get(slot, "").split(".")
            if len(detected_path) != 3 or detected_path[1] != "inputs":
                continue
            detected_inputs = (graph.get(detected_path[0]) or {}).get("inputs")
            if not isinstance(detected_inputs, dict):
                continue
            expected = detected_inputs.get(detected_path[2])
            if isinstance(expected, list) or expected is None:
                continue
        path = node_map.get(slot, "").split(".")
        if len(path) != 3 or path[1] != "inputs":
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                f"The selected workflow has an invalid {label} mapping.",
            )
        inputs = (graph.get(path[0]) or {}).get("inputs")
        if not isinstance(inputs, dict) or path[2] not in inputs:
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                f"The selected workflow has an invalid {label} mapping.",
            )
        current = inputs[path[2]]
        if isinstance(current, list):
            if not selected:
                continue
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                f"The selected workflow connects the {label} input and cannot "
                f"apply the selected value.",
            )
        if type(current) is not type(expected):
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                f"The selected workflow has an incompatible widget for the "
                f"selected {label}.",
            )


def _full_auto_stage_assignments(plan: dict, saved: dict) -> tuple[list[int], list[dict]]:
    stage_states = saved_looks._automation_profile_coverage_stage_states(saved)
    if len(plan["takes"]) < len(stage_states):
        raise _insufficient_stage_count(len(stage_states), len(plan["takes"]))
    progression = plan["authoring"]["wardrobe_progression"]
    selected = progression["stage_indices"] if progression is not None else [0]
    take_count = len(plan["takes"])
    assignments = [
        0 if len(selected) == 1 else (index * (len(selected) - 1)) // (take_count - 1)
        for index in range(take_count)
    ]
    return assignments, stage_states


def _compile_full_auto_coverage(
    plan: dict,
    saved: dict,
    application: dict,
) -> tuple[dict, dict[str, dict], list[dict]]:
    assignments, _stage_states = _full_auto_stage_assignments(plan, saved)
    profile = application["profile"]
    stage_text = {}
    for stage_index in set(assignments):
        try:
            stage_text[stage_index] = saved_looks.compile_automation_profile_coverage(
                saved["key"], saved["version"], saved["content_digest"],
                profile["profile_revision"], profile["profile_digest"], stage_index,
            )
        except saved_looks.SavedLookError as exc:
            if exc.code == "automation_profile_stale":
                raise GuidedSessionError(
                    409, "automation_profile_changed",
                    "The selected automation-profile revision or digest cannot be verified.",
                ) from exc
            raise _profile_not_ready(saved=saved) from exc
    effective = session_plan.resolve_effective_wardrobes(plan)
    evidence: dict[str, dict] = {}
    grouped: dict[int, list[str]] = {index: [] for index in set(assignments)}
    for take, stage_index in zip(plan["takes"], assignments):
        take_id = take["take_id"]
        text = stage_text[stage_index]
        take["wardrobe_coverage"] = text
        evidence[take_id] = session_plan._profile_coverage_record(
            application, stage_index, effective[take_id], text,
        )
        grouped[stage_index].append(take_id)
    plan = session_plan.validate_draft(plan)
    stages = [
        {
            "stage_index": stage_index,
            "first_take_id": take_ids[0],
            "last_take_id": take_ids[-1],
            "take_count": len(take_ids),
            "coverage": stage_text[stage_index],
        }
        for stage_index, take_ids in sorted(grouped.items())
    ]
    return plan, evidence, stages


def _scope_request_body(body: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(body)
    authorization = dict(body["full_auto"])
    authorization.pop("scope_digest", None)
    authorization.pop("scope_issued_at", None)
    normalized["full_auto"] = authorization
    return normalized


def _full_auto_continuity_digest(plan: dict[str, Any]) -> str:
    authoring = plan["authoring"]
    return resource_store.canonical_digest({
        "look": plan["look"],
        "initial_wardrobe": plan["initial_wardrobe"],
        "selected_resources": plan["selected_resources"],
        "brief": authoring["brief"],
        "workflow_binding": authoring["workflow_binding"],
        "variation_policy": authoring["variation_policy"],
        "look_snapshot": authoring["look_snapshot"],
        "wardrobe_progression": authoring["wardrobe_progression"],
        "wardrobe_changes": plan["wardrobe_changes"],
        "policy_version": _FULL_AUTO_POLICY_VERSION,
        "coverage_compiler_version": saved_looks.AUTOMATION_COVERAGE_COMPILER_VERSION,
    })


def _resolve_request(
    body: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    scope_issued_at: str | None = None,
) -> dict[str, Any]:
    full_auto = body.get("full_auto")
    is_full_auto = full_auto is not None
    provider, provider_input = _provider_identity(config or {})
    model = db.one("SELECT * FROM model WHERE id = ?", body["character_id"])
    if model is None:
        raise GuidedSessionError(422, "character_not_found", "Choose an existing character.")

    try:
        room_library, room_revision = _validate_ready_room(body["scene_anchor"])
        settings = workflow_binding.effective_session_settings(model)
        if "width" in body:
            settings["width"] = body["width"]
            settings["height"] = body["height"]
        if is_full_auto:
            requested_workflow_id = body["workflow_id"] or model["workflow_id"]
            requested_workflow = (
                db.one("SELECT kind FROM workflow WHERE id = ?", requested_workflow_id)
                if requested_workflow_id is not None else None
            )
            if requested_workflow is not None and requested_workflow["kind"] != "t2i":
                raise GuidedSessionError(
                    422, "full_auto_workflow_unsupported",
                    "Full Auto requires a text-to-image (t2i) workflow; choose a compatible workflow or continue manually.",
                )
        binding = workflow_binding.resolve_effective_workflow(
            body["character_id"], body["workflow_id"], plan=None, settings=settings,
        )
    except workflow_binding.WorkflowRequired as exc:
        if is_full_auto:
            raise _safe_full_auto_error(exc) from exc
        raise GuidedSessionError(422, exc.code, exc.message) from exc
    except workflow_binding.WorkflowCompatibilityError as exc:
        if is_full_auto:
            raise _safe_full_auto_error(exc) from exc
        raise GuidedSessionError(422, exc.code, exc.message) from exc

    workflow_row = db.one("SELECT * FROM workflow WHERE id = ?", binding["workflow_id"])
    if workflow_row is None:
        raise GuidedSessionError(
            422, "workflow_compatibility_invalid", "The selected workflow is unavailable.",
        )
    if is_full_auto and workflow_row["kind"] != "t2i":
        raise GuidedSessionError(
            422, "full_auto_workflow_unsupported",
            "Full Auto requires a text-to-image (t2i) workflow; choose a compatible workflow or continue manually.",
        )

    try:
        graph = workflow_binding._parse_required_graph(workflow_row["graph"])
        node_map = workflow_binding._parse_optional_node_map(workflow_row["node_map"])
    except workflow_binding.WorkflowCompatibilityError as exc:
        if is_full_auto:
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                "The selected workflow is not compatible with Full Auto.",
            ) from exc
        raise GuidedSessionError(422, exc.code, exc.message) from exc

    effective_body = dict(body)
    if is_full_auto:
        try:
            detected_map = comfy.detect_map(graph)
        except (AttributeError, TypeError) as exc:
            raise GuidedSessionError(
                422, "workflow_compatibility_invalid",
                "The selected workflow is not compatible with Full Auto.",
            ) from exc
        if any(
            slot in node_map or slot in detected_map
            for slot in comfy.REFERENCE_SLOTS
        ):
            raise GuidedSessionError(
                422, "full_auto_workflow_unsupported",
                "Full Auto requires a t2i workflow without a generation "
                "reference; choose a compatible workflow or continue manually.",
            )
        _validate_full_auto_selected_choices(
            model, settings, graph, node_map, detected_map,
        )
        settings.update(_validate_full_auto_canvas(body, settings, graph, node_map))
        policy = {field: dict(value) for field, value in body["variation_policy"].items()}
        if full_auto["framing_intent"] == "full_body":
            fixed = policy["framing"]
            if fixed["mode"] == "fixed" and fixed["value"] != _FULL_BODY_FRAMING:
                raise GuidedSessionError(
                    422, "framing_conflict",
                    "Full-body intent conflicts with the selected fixed framing; clear or change that choice.",
                    current={
                        "field": "variation_policy.framing",
                        "framing_intent": "full_body",
                        "remedy": "clear_fixed_framing_or_choose_compatible_intent",
                    },
                )
            policy["framing"] = {
                "mode": "fixed", "value": _FULL_BODY_FRAMING, "value_origin": "user",
            }
        effective_body["variation_policy"] = policy

    initial_plan = _initial_plan(effective_body, binding)
    plan = initial_plan
    application = None
    coverage_evidence = None
    stages = []
    saved = None
    if body.get("saved_look") is not None:
        saved = _selected_saved_look(body["saved_look"])
        if is_full_auto:
            application, _readiness = _profile_application(saved, full_auto)
        decisions = {"look": "replace"}
        if saved["outfit"] is not None:
            decisions["initial_wardrobe"] = "replace"
        plan = session_plan.build_saved_look_application_candidate(
            plan, saved, decisions, profile_application=application,
        )
    elif is_full_auto:
        raise GuidedSessionError(
            422, "automation_profile_not_ready",
            "Full Auto requires an exact saved-look version.",
        )

    if is_full_auto:
        stage_count = len(saved_looks._automation_profile_coverage_stage_states(saved))
        if body["photo_count"] < stage_count:
            raise _insufficient_stage_count(stage_count, body["photo_count"])
        try:
            plan = session_plan.build_full_auto_wardrobe_progression_candidate(plan)
            session_plan._validate_profile_progression(application, plan)
        except session_plan.PlanValidationError as exc:
            if "requires at least" in str(exc):
                raise _insufficient_stage_count(
                    len(saved_looks._automation_profile_coverage_stage_states(saved)),
                    body["photo_count"],
                ) from exc
            raise _profile_not_ready(saved=saved) from exc
        except session_plan.PlanOwnershipConflict as exc:
            raise GuidedSessionError(
                422, "appearance_progression_conflict",
                "Permanent appearance text conflicts with a wardrobe stage.",
                current=_profile_error_current(
                    [{"code": "appearance_progression_conflict"}], saved,
                ),
            ) from exc
        plan, coverage_evidence, stages = _compile_full_auto_coverage(plan, saved, application)

    conflicts = session_plan.detect_resource_constant_conflicts(plan)
    if is_full_auto:
        plan["conflicts"] = conflicts
    effective_settings_digest = resource_store.canonical_digest(settings)
    settings["composition_mode"] = session_plan.MODE_RESOURCE_V1
    result = {
        "model": model,
        "room_library": room_library,
        "room_revision": room_revision,
        "settings": settings,
        "binding": binding,
        "workflow_row": workflow_row,
        "initial_plan": initial_plan,
        "plan": plan,
        "conflicts": conflicts,
        "profile_application": application,
        "coverage_evidence": coverage_evidence,
        "stages": stages,
        "provider": provider,
        "provider_input": provider_input,
    }
    if not is_full_auto:
        return result

    if scope_issued_at is None:
        raise ValueError("Full Auto scope_issued_at is required to resolve a quote")
    issued = _validate_utc_timestamp(scope_issued_at)
    expires = (datetime.fromisoformat(issued) + _FULL_AUTO_AUTHORIZATION_LIFETIME).isoformat(timespec="seconds")
    profile = application["profile"]
    safe_generation_settings: dict[str, Any] = {}
    steps = settings.get("steps")
    if type(steps) is int and 1 <= steps <= _SQLITE_INTEGER_MAX:
        safe_generation_settings["steps"] = steps
    cfg = settings.get("cfg")
    if type(cfg) in (int, float) and math.isfinite(cfg):
        safe_generation_settings["cfg"] = cfg
    for field in ("sampler", "scheduler"):
        value = settings.get(field)
        if isinstance(value, str) and len(value) <= 80 and all(
            char.isalnum() or char in " ._-+" for char in value
        ):
            safe_generation_settings[field] = value
    raw_model_name = model.get("name")
    safe_model_name = (
        raw_model_name
        if isinstance(raw_model_name, str)
        and len(raw_model_name) <= 120
        and resource_selection._is_safe_public_string(raw_model_name)
        and all(char.isalnum() or char in " ._-" for char in raw_model_name)
        else "Selected model"
    )
    summary = {
        "saved_look": {
            "look_key": saved["key"],
            "version": saved["version"],
            "content_digest": saved["content_digest"],
        },
        "automation_profile": {
            "revision": profile["profile_revision"],
            "digest": profile["profile_digest"],
            "ready": True,
        },
        "room": {
            "library_key": body["scene_anchor"]["library_key"],
            "source_id": body["scene_anchor"]["source_id"],
            "content_digest": body["scene_anchor"]["content_digest"],
            "translation_digest": resource_store.canonical_digest(
                room_revision.get("translation"),
            ),
        },
        "character_id": body["character_id"],
        "model_binding": {
            "model_id": model["id"],
            "name": safe_model_name,
            "trigger_digest": resource_store.canonical_digest(model["trigger"]),
            "base_positive_digest": resource_store.canonical_digest(model["base_positive"]),
            "base_negative_digest": resource_store.canonical_digest(model["base_negative"]),
            "lora_selection_digest": resource_store.canonical_digest({
                "name": model["lora_name"],
                "strength": model["lora_strength"],
            }),
            "effective_settings_digest": effective_settings_digest,
        },
        "workflow": {
            "workflow_id": binding["workflow_id"],
            "kind": binding["kind"],
            "graph_digest": binding["graph_digest"],
            "node_map_digest": binding["node_map_digest"],
        },
        "provider": provider,
        "generation_settings": safe_generation_settings,
        "canvas": {
            "width": settings["width"],
            "height": settings["height"],
        },
        "framing_intent": full_auto["framing_intent"],
        "effective_framing": effective_body["variation_policy"]["framing"],
        "variation_policy": effective_body["variation_policy"],
        "photo_count": body["photo_count"],
        "continuity_digest": _full_auto_continuity_digest(plan),
        "progression": {
            "policy": full_auto["progression"],
            "stages": stages,
        },
        "coverage_compiler_version": saved_looks.AUTOMATION_COVERAGE_COMPILER_VERSION,
        "policy_version": _FULL_AUTO_POLICY_VERSION,
        "limits": {
            "assistant_calls_per_take": _FULL_AUTO_ASSISTANT_CALLS_PER_TAKE,
            "assistant_calls_total": body["photo_count"] * _FULL_AUTO_ASSISTANT_CALLS_PER_TAKE,
            "image_attempts_per_take": _FULL_AUTO_IMAGE_ATTEMPTS_PER_TAKE,
            "image_attempts_total": body["photo_count"] * _FULL_AUTO_IMAGE_ATTEMPTS_PER_TAKE,
        },
        "scope_issued_at": issued,
        "authorization_expires_at": expires,
    }
    dependencies_digest = resource_store.canonical_digest({
        "model": model,
        "effective_settings": settings,
        "room_library": room_library,
        "room_revision": room_revision,
        "workflow": workflow_row,
        "workflow_binding": binding,
        "saved_look": saved,
        "profile_application": application,
        "provider": provider_input,
    })
    request_digest = resource_store.canonical_digest(_scope_request_body(body))
    summary["request_digest"] = request_digest
    summary["dependencies_digest"] = dependencies_digest
    scope_digest = resource_store.canonical_digest(summary)
    result.update({
        "summary": summary,
        "scope_digest": scope_digest,
        "scope_issued_at": issued,
        "expires_at": expires,
        "dependencies_digest": dependencies_digest,
        "request_digest": request_digest,
    })
    return result


def preflight_full_auto(body: dict[str, Any], *, config: _ConfigInput = None) -> dict[str, Any]:
    """Resolve a read-only Full Auto quote without creating session state."""
    with db.transaction():
        if not resource_planning.is_enabled():
            raise GuidedSessionError(
                503, "resource_planning_disabled",
                "Guided session creation is disabled by configuration.",
            )
        issued = datetime.now(timezone.utc).replace(microsecond=0).isoformat(timespec="seconds")
        resolution = _resolve_request(
            body, config=_config_snapshot(config), scope_issued_at=issued,
        )
        return {
            "summary": resolution["summary"],
            "scope_digest": resolution["scope_digest"],
            "scope_issued_at": issued,
        }


def _refreshed_scope(body: dict[str, Any], config: dict[str, Any], issued: str) -> dict[str, Any]:
    try:
        resolution = _resolve_request(body, config=config, scope_issued_at=issued)
    except GuidedSessionError as exc:
        return {"eligibility": {"code": exc.code}}
    return {
        "summary": resolution["summary"],
        "scope_digest": resolution["scope_digest"],
        "scope_issued_at": issued,
    }


def compare_full_auto_resume_scope(
    run_id: str,
    *,
    config: _ConfigInput = None,
    now_text: str | None = None,
    comparison_issued_at: str | None = None,
) -> dict[str, Any]:
    """Build a read-only old/new authorization comparison from current rows."""
    from backend import resource_full_auto

    now = resource_full_auto._now_text(now_text)
    now_instant = resource_full_auto._timestamp(now, field_name="current time")
    if comparison_issued_at is None:
        requested_comparison_issued = now
    else:
        issued_instant = resource_full_auto._timestamp(
            comparison_issued_at, field_name="Resume comparison timestamp",
        )
        if issued_instant.isoformat(timespec="seconds") != comparison_issued_at:
            raise resource_full_auto.FullAutoRunError(
                422, "invalid_control_request",
                "comparison_issued_at must be a canonical UTC timestamp.",
            )
        requested_comparison_issued = comparison_issued_at
        age = now_instant - issued_instant
        if age < timedelta(0) or age > _FULL_AUTO_SCOPE_MAX_AGE:
            raise resource_full_auto.FullAutoRunError(
                409, "resume_comparison_stale",
                "The Full Auto comparison expired; refresh it before resuming.",
            )
    with db.transaction():
        run = resource_full_auto._require_run(run_id)
        session_id = int(run["session_id"])
        grant = resource_full_auto.current_authorization(run_id, session_id=session_id)
        saved_summary, _saved_limits = resource_full_auto._validate_status_authorization(
            grant["authorization"],
        )
        plan_row = db.one(
            "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
            session_id,
        )
        session = db.one("SELECT model_id FROM session WHERE id = ?", session_id)
        if plan_row is None or session is None:
            raise resource_full_auto.FullAutoRunError(
                409, "full_auto_scope_unavailable",
                "The current session inputs are unavailable for Resume comparison.",
            )
        try:
            raw_plan = json.loads(plan_row["plan_json"])
            plan = session_plan.validate_draft(raw_plan)
        except (TypeError, json.JSONDecodeError, session_plan.PlanValidationError) as exc:
            raise resource_full_auto.FullAutoRunError(
                409, "full_auto_scope_unavailable",
                "The current session plan is unavailable for Resume comparison.",
            ) from exc

        context = db.one(
            "SELECT request_json FROM full_auto_run_scope_input WHERE run_id = ?",
            run_id,
        )
        authorization = {**grant["authorization"], "summary": saved_summary}
        if context is not None:
            try:
                body = json.loads(context["request_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise resource_full_auto.FullAutoRunError(
                    409, "full_auto_scope_unavailable",
                    "The saved Full Auto selection cannot be verified for Resume.",
                ) from exc
        else:
            old_summary = authorization.get("summary")
            saved = old_summary.get("saved_look") if isinstance(old_summary, dict) else None
            room = old_summary.get("room") if isinstance(old_summary, dict) else None
            if not isinstance(saved, dict) or not isinstance(room, dict):
                raise resource_full_auto.FullAutoRunError(
                    409, "full_auto_scope_unavailable",
                    "The saved Full Auto selection cannot be verified for Resume.",
                )
            body = {
                "character_id": int(session["model_id"]),
                "workflow_id": None,
                "scene_anchor": {
                    key: room[key] for key in ("library_key", "source_id", "content_digest")
                },
                "photo_count": len(plan["takes"]),
                "brief": plan["authoring"]["brief"],
                "mode": plan["authoring"]["mode"],
                "variation_policy": plan["authoring"]["variation_policy"],
                "look": "",
                "initial_wardrobe": "",
                "saved_look": {
                    "look_key": saved["look_key"],
                    "version": saved["version"],
                    "content_digest": saved["content_digest"],
                },
                "full_auto": {
                    key: authorization[key]
                    for key in (
                        "authorization_version", "generate_without_pauses",
                        "profile_revision", "profile_digest", "progression",
                        "framing_intent", "scope_digest", "scope_issued_at",
                    )
                    if key in authorization
                },
            }

        if not isinstance(body, dict) or not isinstance(body.get("full_auto"), dict):
            raise resource_full_auto.FullAutoRunError(
                409, "full_auto_scope_unavailable",
                "The saved Full Auto selection cannot be verified for Resume.",
            )
        root_receipt = db.one(
            "SELECT authorization_json, scope_digest, scope_issued_at "
            "FROM full_auto_run_receipt WHERE run_id = ?",
            run_id,
        )
        if root_receipt is None:
            raise resource_full_auto.FullAutoRunError(
                409, "full_auto_scope_unavailable",
                "The original Full Auto authorization cannot be verified for Resume.",
            )
        try:
            root_authorization = json.loads(root_receipt["authorization_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise resource_full_auto.FullAutoRunError(
                409, "full_auto_scope_unavailable",
                "The original Full Auto authorization cannot be verified for Resume.",
            ) from exc
        root_summary, _root_limits = resource_full_auto._validate_status_authorization(
            root_authorization,
        )
        original_full_auto = body["full_auto"]
        original_variation_policy = body.get("variation_policy")
        original_framing = (
            original_variation_policy.get("framing")
            if isinstance(original_variation_policy, dict) else None
        )
        original_profile = root_summary.get("automation_profile")
        original_request_bound = bool(
            isinstance(original_profile, dict)
            and original_full_auto.get("scope_digest") == root_receipt["scope_digest"]
            and original_full_auto.get("scope_digest") == root_authorization["scope_digest"]
            and original_full_auto.get("scope_issued_at") == root_receipt["scope_issued_at"]
            and original_full_auto.get("scope_issued_at") == root_authorization["scope_issued_at"]
            and original_full_auto.get("profile_revision") == root_authorization["profile_revision"]
            and original_full_auto.get("profile_revision") == original_profile.get("revision")
            and original_full_auto.get("profile_digest") == root_authorization["profile_digest"]
            and original_full_auto.get("profile_digest") == original_profile.get("digest")
            and resource_store.canonical_digest(_scope_request_body(body))
            == root_summary.get("request_digest")
        )
        body = dict(body)
        body["character_id"] = int(session["model_id"])
        body["photo_count"] = len(plan["takes"])
        body["brief"] = plan["authoring"]["brief"]
        body["mode"] = plan["authoring"]["mode"]
        plan_variation_policy = plan["authoring"]["variation_policy"]
        body["variation_policy"] = plan_variation_policy
        if (
            original_request_bound
            and original_full_auto.get("framing_intent") == "full_body"
            and root_summary.get("framing_intent") == "full_body"
            and saved_summary.get("framing_intent") == "full_body"
            and original_framing == {"mode": "vary"}
            and plan_variation_policy.get("framing") == {
                "mode": "fixed",
                "value": _FULL_BODY_FRAMING,
                "value_origin": "user",
            }
        ):
            # Preserve the raw creation digest; Full Auto reapplies fixed framing.
            body["variation_policy"] = {
                **plan_variation_policy,
                "framing": original_framing,
            }
        body["scene_anchor"] = plan["authoring"]["scene_anchor"]
        snapshot = plan["authoring"]["look_snapshot"]
        application = session_plan._load_session_profile_application(
            session_id, int(plan_row["plan_revision"]), plan,
        )
        if isinstance(snapshot, dict):
            body["saved_look"] = {
                "look_key": application["saved_look"]["key"] if application else snapshot["look_id"],
                "version": snapshot["version"],
                "content_digest": snapshot["content_digest"],
            }
        if application is not None:
            body["full_auto"] = dict(body["full_auto"])
            body["full_auto"]["profile_revision"] = application["profile"]["profile_revision"]
            body["full_auto"]["profile_digest"] = application["profile"]["profile_digest"]

        issued = grant["scope_issued_at"]
        try:
            current = _resolve_request(
                body, config=_config_snapshot(config), scope_issued_at=issued,
            )
        except GuidedSessionError as exc:
            raise resource_full_auto.FullAutoRunError(
                409, exc.code, exc.message,
            ) from exc
        current_summary = resource_full_auto._project_consent_summary({
            **current["summary"],
            "continuity_digest": _full_auto_continuity_digest(plan),
        })
        current_scope_digest = resource_store.canonical_digest(current_summary)
        current_plan_digest = resource_store.canonical_digest(raw_plan)
        old_summary = saved_summary
        unchanged = (
            current_scope_digest == grant["scope_digest"]
            and current["dependencies_digest"] == grant["dependencies_digest"]
            and current_plan_digest == grant["plan_digest"]
            and int(plan_row["plan_revision"]) == int(run["plan_revision"])
            and resource_full_auto._timestamp(
                grant["authorization_expires_at"], field_name="authorization expiry",
            ) > resource_full_auto._timestamp(now, field_name="current time")
        )

        target_ids = [take.get("take_id") for take in plan.get("takes", [])]
        progress_row = db.one(
            "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
        )
        progress = None if progress_row is None else resource_full_auto._read_prepare_progress(run_id)
        started_targets = resource_full_auto._validated_prepare_accounting(
            run, progress_row, progress, target_ids,
        )
        pending_take_id = next(
            (take_id for take_id in target_ids if take_id not in started_targets),
            None,
        )
        old_allowance = resource_full_auto.assistant_executable_allowance(
            run_id, pending_take_id,
        )
        budget_refill_required = (
            pending_take_id is not None and old_allowance["executable_remaining"] == 0
        )
        unchanged = unchanged and not budget_refill_required

        comparison_issued = requested_comparison_issued
        candidate = current if unchanged else _resolve_request(
            body, config=_config_snapshot(config), scope_issued_at=comparison_issued,
        )
        new_summary = resource_full_auto._project_consent_summary({
            **candidate["summary"],
            "continuity_digest": _full_auto_continuity_digest(plan),
        })
        expires = candidate["expires_at"]
        current_plan_revision = int(plan_row["plan_revision"])
        new_scope_digest = resource_store.canonical_digest(new_summary)
        continuity_fields = (
            "saved_look", "automation_profile", "room", "character_id",
            "model_binding", "workflow", "provider", "generation_settings",
            "canvas", "framing_intent", "effective_framing", "variation_policy",
            "request_digest", "progression", "coverage_compiler_version",
            "policy_version",
        )
        comparison_fields = (*continuity_fields, "photo_count", "limits", "dependencies_digest")
        changed_fields = [
            field for field in comparison_fields
            if old_summary.get(field) != new_summary.get(field)
        ]
        if current_plan_digest != grant["plan_digest"]:
            changed_fields.append("plan")
        if expires != grant["authorization_expires_at"]:
            changed_fields.append("deadline")
        if not unchanged:
            changed_fields.append("assistant_allowance")
        if session_plan.has_generated_take(session_id) and (
            (old_summary.get("continuity_digest") is not None
             and old_summary.get("continuity_digest") != new_summary.get("continuity_digest"))
            or any(field in changed_fields for field in continuity_fields)
        ):
            raise resource_full_auto.FullAutoRunError(
                409, "full_auto_continuity_requires_new_session",
                "Generated Full Auto history cannot continue under changed generation inputs; create a new session.",
            )

        if unchanged:
            additional_calls_total = 0
            additional_calls_per_take = 0
            new_executable_remaining = old_allowance["executable_remaining"]
            new_pending_take_remaining = old_allowance["pending_take_remaining"]
        else:
            new_limits = new_summary["limits"]
            additional_calls_total = int(new_limits["assistant_calls_total"])
            additional_calls_per_take = int(new_limits["assistant_calls_per_take"])
            new_pending_take_remaining = (
                additional_calls_per_take if pending_take_id is not None else None
            )
            new_executable_remaining = (
                min(additional_calls_total, additional_calls_per_take)
                if pending_take_id is not None else additional_calls_total
            )
        assistant_allowance = {
            "pending_take_id": pending_take_id,
            "old_executable_remaining": old_allowance["executable_remaining"],
            "old_pending_take_remaining": old_allowance["pending_take_remaining"],
            "additional_calls_total": additional_calls_total,
            "additional_calls_per_take": additional_calls_per_take,
            "new_executable_remaining": new_executable_remaining,
            "new_pending_take_remaining": new_pending_take_remaining,
        }
        retry_candidates = resource_full_auto._retry_candidates(session_id, run_id)
        image_retry_allowance = resource_full_auto._image_retry_allowance(
            run_id, int(_saved_limits["image_attempts_total"]),
            candidates=retry_candidates,
        )
        comparison_payload = {
            "run_id": run_id,
            "run_revision": int(run["revision"]),
            "plan_revision": current_plan_revision,
            "previous_authorization_digest": grant["authorization_digest"],
            "previous_scope_digest": grant["scope_digest"],
            "next_scope_digest": new_scope_digest,
            "next_dependencies_digest": candidate["dependencies_digest"],
            "next_plan_digest": current_plan_digest,
            # The exact quote timestamp binds the displayed renewal deadline.
            # Resume echoes it so recomputation stays stable throughout the
            # quote's bounded lifetime.
            "renewal_lifetime_seconds": int(_FULL_AUTO_AUTHORIZATION_LIFETIME.total_seconds()),
            "comparison_issued_at": comparison_issued,
            "assistant_allowance": assistant_allowance,
            "image_retry_allowance": image_retry_allowance,
            "reservation_counts_digest": old_allowance["reservation_counts_digest"],
        }
        comparison_digest = resource_store.canonical_digest(comparison_payload)
        if unchanged:
            new_summary = old_summary
            new_scope_digest = grant["scope_digest"]
            expires = grant["authorization_expires_at"]
            changed_fields = []
        return {
            "run_id": run_id,
            "run_revision": int(run["revision"]),
            "plan_revision": current_plan_revision,
            "changed": not unchanged,
            "scope_digest": comparison_digest,
            "comparison_issued_at": comparison_issued,
            "changed_fields": changed_fields,
            "assistant_allowance": assistant_allowance,
            "image_retry_allowance": image_retry_allowance,
            "old_scope": {
                "summary": old_summary,
                "scope_digest": grant["scope_digest"],
                "authorization_expires_at": grant["authorization_expires_at"],
            },
            "new_scope": {
                "summary": new_summary,
                "scope_digest": new_scope_digest,
                "authorization_expires_at": expires,
                "renewal_lifetime_seconds": int(_FULL_AUTO_AUTHORIZATION_LIFETIME.total_seconds()),
            },
            "_authorization": None if unchanged else {
                "authorization_version": body["full_auto"]["authorization_version"],
                "generate_without_pauses": body["full_auto"]["generate_without_pauses"],
                "profile_revision": body["full_auto"]["profile_revision"],
                "profile_digest": body["full_auto"]["profile_digest"],
                "progression": body["full_auto"]["progression"],
                "framing_intent": body["full_auto"]["framing_intent"],
                "scope_digest": new_scope_digest,
                "scope_issued_at": comparison_issued,
                "authorization_expires_at": expires,
                "summary": new_summary,
            },
            "_dependencies_digest": candidate["dependencies_digest"],
            "_plan_digest": current_plan_digest,
            "_previous_authorization_digest": grant["authorization_digest"],
            "_reservation_counts_digest": old_allowance["reservation_counts_digest"],
        }


def create_or_replay(
    request: NormalizedGuidedRequest,
    *,
    config: _ConfigInput = None,
) -> tuple[str, bool]:
    """Persist a new guided session or return the stored response for a retry."""
    if not resource_planning.is_enabled():
        raise GuidedSessionError(
            503,
            "resource_planning_disabled",
            "Guided session creation is disabled by configuration.",
        )
    with db.transaction():
        if not resource_planning.is_enabled():
            raise GuidedSessionError(
                503,
                "resource_planning_disabled",
                "Guided session creation is disabled by configuration.",
            )
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
        full_auto = body.get("full_auto")
        active_config = _config_snapshot(config)
        resolution = None
        if full_auto is not None:
            now_utc = datetime.now(timezone.utc).replace(microsecond=0)
            issued_text = full_auto["scope_issued_at"]
            issued = datetime.fromisoformat(issued_text)
            age = now_utc - issued
            if age < timedelta(0) or age > _FULL_AUTO_SCOPE_MAX_AGE:
                refreshed = _refreshed_scope(
                    body,
                    active_config,
                    now_utc.isoformat(timespec="seconds"),
                )
                raise GuidedSessionError(
                    409, "authorization_scope_changed",
                    "The Full Auto quote expired or was issued in the future; review the refreshed summary and authorize again.",
                    current=refreshed,
                )
            try:
                resolution = _resolve_request(
                    body, config=active_config, scope_issued_at=issued_text,
                )
            except GuidedSessionError as exc:
                raise GuidedSessionError(
                    409, "authorization_scope_changed",
                    "Full Auto eligibility or an authorized input changed; review a refreshed summary and authorize again.",
                    current=_refreshed_scope(
                        body,
                        active_config,
                        now_utc.isoformat(timespec="seconds"),
                    ),
                ) from exc
            if not hmac.compare_digest(full_auto["scope_digest"], resolution["scope_digest"]):
                raise GuidedSessionError(
                    409, "authorization_scope_changed",
                    "An authorized Full Auto input changed; review the refreshed summary and authorize again.",
                    current={
                        "summary": resolution["summary"],
                        "scope_digest": resolution["scope_digest"],
                        "scope_issued_at": issued_text,
                    },
                )
        else:
            resolution = _resolve_request(body, config=active_config)

        model = resolution["model"]
        settings = resolution["settings"]
        binding = resolution["binding"]
        plan = resolution["plan"]
        conflicts = resolution["conflicts"]
        settings["composition_mode"] = session_plan.MODE_RESOURCE_V1
        now = db.now()
        session_number = db.one(
            "SELECT COUNT(*) AS count FROM session WHERE model_id = ?",
            model["id"],
        )["count"] + 1
        session_id = db.run(
            """INSERT INTO session (
                   model_id, name, look, wardrobe, workflow_id,
                   reference_workflow_id, anchor_shot_ids, settings,
                   manner, checkpoint, room_key, created_at
               ) VALUES (?, ?, '', '', ?, NULL, '[]', ?, '', '', '', ?)""",
            body["character_id"], f"Session {session_number}", binding["workflow_id"],
            json.dumps(settings, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            now,
        )
        session_plan._persist_plan_revision(
            session_id,
            None,
            resolution["initial_plan"],
            plan,
            conflicts,
            carry_profile_evidence=False,
            profile_application=resolution["profile_application"],
            profile_coverage_evidence=resolution["coverage_evidence"],
        )
        stored_plan_row = db.one(
            "SELECT plan_json FROM session_plan WHERE session_id = ?", session_id,
        )
        stored_plan = json.loads(stored_plan_row["plan_json"])
        response = {"session_id": session_id, "plan_revision": 1, "plan": stored_plan}
        if full_auto is not None:
            run_id = str(uuid.uuid4())
            authorization = {
                "authorization_version": full_auto["authorization_version"],
                "generate_without_pauses": full_auto["generate_without_pauses"],
                "profile_revision": full_auto["profile_revision"],
                "profile_digest": full_auto["profile_digest"],
                "progression": full_auto["progression"],
                "framing_intent": full_auto["framing_intent"],
                "scope_digest": resolution["scope_digest"],
                "scope_issued_at": resolution["scope_issued_at"],
                "authorization_expires_at": resolution["expires_at"],
                "summary": resolution["summary"],
            }
            plan_digest = resource_store.canonical_digest(stored_plan)
            db.run(
                """INSERT INTO full_auto_run_receipt (
                       run_id, session_id, authorization_json, scope_digest,
                       dependencies_digest, plan_digest, scope_issued_at,
                       authorization_expires_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                run_id, session_id,
                json.dumps(authorization, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                resolution["scope_digest"], resolution["dependencies_digest"],
                plan_digest, resolution["scope_issued_at"], resolution["expires_at"], now,
            )
            db.run(
                "INSERT INTO full_auto_run_scope_input (run_id, request_json, created_at) VALUES (?, ?, ?)",
                run_id,
                json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                now,
            )
            resource_full_auto.create_run_in_transaction(
                run_id, session_id, plan_revision=1, created_at=now,
            )
            response["full_auto_run_id"] = run_id
        response_json = json.dumps(
            response, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )
        db.run(
            """INSERT INTO guided_session_request
               (request_id, request_digest, session_id, response_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            request.request_id, request.body_digest, session_id, response_json, now,
        )
        return response_json, True
