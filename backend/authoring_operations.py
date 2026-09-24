"""Persistence and closed views for recoverable authoring operation claims.

This module implements the start/status boundary only. Remote execution,
lease renewal, cancellation, resume and recovery belong to later workflow
tasks and are intentionally not performed here.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError, field_validator

import db
import resource_selection
import resource_store
from backend import session_plan


class AuthoringOperationError(Exception):
    """An error with the stable public contract for authoring operations."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        operation: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.operation = operation


class _StartBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: StrictStr
    expected_revision: StrictInt

    @field_validator("request_id", mode="before")
    @classmethod
    def normalize_request_id(cls, value: Any) -> str:
        try:
            return resource_selection.normalize_client_uuid(value)
        except ValueError as exc:
            raise ValueError("request_id must be a UUID string") from exc

    @field_validator("expected_revision", mode="before")
    @classmethod
    def validate_expected_revision(cls, value: Any) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("expected_revision must be a positive integer")
        return value


class _SharedSuggestionsStart(_StartBase):
    kind: Literal["shared_suggestions"]


class _PrepareTakesStart(_StartBase):
    kind: Literal["prepare_takes"]
    take_ids: list[StrictStr]

    @field_validator("take_ids", mode="before")
    @classmethod
    def validate_take_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, list) or not value:
            raise ValueError("take_ids must be a non-empty list")
        if any(not isinstance(take_id, str) or not take_id for take_id in value):
            raise ValueError("take_ids must contain non-empty strings")
        if len(value) > 20:
            raise ValueError("at most 20 takes may be prepared in one operation")
        if len(set(value)) != len(value):
            raise ValueError("take_ids must not contain duplicates")
        return value


def normalize_start_request(payload: Any) -> dict:
    """Validate and normalize the exact POST body before entering a transaction."""
    if not isinstance(payload, dict):
        raise AuthoringOperationError(422, "invalid_request", "Request body must be an object.")

    if "kind" not in payload:
        raise AuthoringOperationError(422, "missing_field", "A required operation field is missing.")
    kind = payload.get("kind")
    if kind == "shared_suggestions":
        if "take_ids" in payload:
            raise AuthoringOperationError(
                422,
                "invalid_request",
                "take_ids must be omitted for shared_suggestions.",
            )
        model_type = _SharedSuggestionsStart
    elif kind == "prepare_takes":
        model_type = _PrepareTakesStart
    else:
        raise AuthoringOperationError(
            422,
            "invalid_request",
            "kind must be shared_suggestions or prepare_takes.",
        )

    try:
        values = model_type.model_validate(payload).model_dump()
    except ValidationError as exc:
        errors = exc.errors()
        if any(error.get("type") == "extra_forbidden" for error in errors):
            code, message = "extra_field_forbidden", "Unknown operation fields are not permitted."
        elif any(error.get("type") == "missing" for error in errors):
            code, message = "missing_field", "A required operation field is missing."
        else:
            code, message = "invalid_request", "The operation request does not match its contract."
        raise AuthoringOperationError(422, code, message) from exc

    request_id = values.pop("request_id")
    digest = resource_store.canonical_digest(values)
    return {"request_id": request_id, "request_digest": digest, **values}


def _decode_json(value: str | None, *, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AuthoringOperationError(
            500,
            "operation_state_invalid",
            "The saved operation state could not be read.",
        ) from exc


def build_operation_view(row: dict) -> dict:
    """Project exactly the public OperationView fields from a persisted row."""
    requested = _decode_json(row.get("requested_json"), default=[])
    completed = _decode_json(row.get("completed_json"), default=[])
    remaining = _decode_json(row.get("remaining_json"), default=[])
    result = _decode_json(row.get("result_json"), default=None)
    if not all(isinstance(value, list) for value in (requested, completed, remaining)):
        raise AuthoringOperationError(
            500,
            "operation_state_invalid",
            "The saved operation progress could not be read.",
        )

    failed = None
    if row.get("failed_item") is not None:
        failed = {"take_id": row["failed_item"], "error": row.get("error") or "Operation failed."}

    # Task 5.2 exposes only start and status. Until their dedicated routes are
    # implemented, neither action is available through this API surface.
    return {
        "operation_id": row["operation_id"],
        "session_id": int(row["session_id"]),
        "plan_revision": int(row["plan_revision"]),
        "kind": row["kind"],
        "state": row["state"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "lease_expires_at": row["lease_expires_at"],
        "progress": {
            "requested": requested,
            "completed": completed,
            "failed": failed,
            "remaining": remaining,
        },
        "result": result,
        "error": row.get("error"),
        "can_cancel": False,
        "can_resume": False,
    }


def _get_operation(operation_id: str) -> dict | None:
    return db.one("SELECT * FROM authoring_operation WHERE operation_id = ?", operation_id)


def start_operation(
    session_id: int,
    request: dict,
    *,
    planning_enabled: bool,
    assistant_available: bool,
) -> tuple[dict, bool]:
    """Atomically replay or claim one operation for this session."""
    if not planning_enabled:
        raise AuthoringOperationError(
            503,
            "resource_planning_disabled",
            "Resource planning is disabled by configuration.",
        )

    with db.transaction():
        session = db.one("SELECT id, settings FROM session WHERE id = ?", session_id)
        if session is None:
            raise AuthoringOperationError(404, "session_not_found", "Session not found.")

        existing = db.one(
            "SELECT * FROM authoring_operation WHERE session_id = ? AND request_id = ?",
            session_id,
            request["request_id"],
        )
        if existing is not None:
            if existing["request_digest"] != request["request_digest"]:
                raise AuthoringOperationError(
                    409,
                    "idempotency_conflict",
                    "request_id is already bound to different operation content.",
                )
            return build_operation_view(existing), False

        if session_plan.read_composition_mode(session.get("settings")) != session_plan.MODE_RESOURCE_V1:
            raise AuthoringOperationError(
                409,
                "plan_revision_stale",
                "This session has no current resource authoring plan.",
            )

        draft_row = db.one(
            "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
            session_id,
        )
        if draft_row is None:
            raise AuthoringOperationError(
                409,
                "plan_revision_stale",
                "This session has no current authoring plan revision.",
            )
        actual_revision = int(draft_row["plan_revision"])
        if actual_revision != request["expected_revision"]:
            raise AuthoringOperationError(
                409,
                "plan_revision_stale",
                "The authoring plan changed; reload it before starting an operation.",
            )
        try:
            plan = json.loads(draft_row["plan_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise AuthoringOperationError(
                500,
                "plan_state_invalid",
                "The saved authoring plan could not be read.",
            ) from exc
        if not isinstance(plan, dict):
            raise AuthoringOperationError(
                500,
                "plan_state_invalid",
                "The saved authoring plan could not be read.",
            )

        try:
            plan = session_plan.validate_draft(plan)
            authoring_kind = session_plan.classify_plan_authoring(plan)
        except session_plan.PlanValidationError as exc:
            raise AuthoringOperationError(
                500,
                "plan_state_invalid",
                "The saved authoring plan could not be validated.",
            ) from exc
        if authoring_kind != session_plan.PLAN_AUTHORING_KIND_AUTOMATIC:
            raise AuthoringOperationError(
                422,
                "invalid_request",
                "Authoring operations require an automatic authoring plan.",
            )

        kind = request["kind"]
        if kind == "prepare_takes":
            ordered_take_ids = [
                take["take_id"]
                for take in plan.get("takes", [])
                if isinstance(take, dict) and isinstance(take.get("take_id"), str)
            ]
            positions: list[int] = []
            for take_id in request["take_ids"]:
                if take_id not in ordered_take_ids:
                    raise AuthoringOperationError(
                        404,
                        "take_not_found",
                        "One or more requested takes do not exist in this plan.",
                    )
                positions.append(ordered_take_ids.index(take_id))
            if positions != sorted(positions):
                raise AuthoringOperationError(
                    422,
                    "invalid_request",
                    "take_ids must follow the plan's stable take order.",
                )
            requested = list(request["take_ids"])
        else:
            shared_state = plan["authoring"]["shared_state"]
            requested = [
                field
                for field in ("look", "initial_wardrobe")
                if shared_state[field]["origin"] == "none"
            ]
            if not requested:
                raise AuthoringOperationError(
                    422,
                    "invalid_request",
                    "There are no missing shared choices to suggest.",
                )

        active = db.one(
            "SELECT * FROM authoring_operation WHERE session_id = ? "
            "AND state IN ('active', 'cancel_requested') LIMIT 1",
            session_id,
        )
        if active is not None:
            raise AuthoringOperationError(
                409,
                "authoring_active",
                "Another authoring operation is already active for this session.",
                operation=build_operation_view(active),
            )

        if not assistant_available:
            raise AuthoringOperationError(
                409,
                "assistant_unavailable",
                "Configure the prompt assistant before starting this operation.",
            )

        now = db.now()
        operation_id = str(uuid.uuid4())
        db.run(
            """INSERT INTO authoring_operation (
                   operation_id, request_id, session_id, plan_revision, kind,
                   request_digest, state, fencing_token, lease_expires_at,
                   requested_json, completed_json, failed_item, error,
                   remaining_json, result_json, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, '[]', NULL, NULL, ?, NULL, ?, ?)""",
            operation_id,
            request["request_id"],
            session_id,
            actual_revision,
            kind,
            request["request_digest"],
            db.authoring_operation_lease_deadline(),
            json.dumps(requested, ensure_ascii=False, separators=(",", ":")),
            json.dumps(requested, ensure_ascii=False, separators=(",", ":")),
            now,
            now,
        )
        row = _get_operation(operation_id)
        if row is None:
            raise AuthoringOperationError(
                500,
                "operation_state_invalid",
                "The operation claim could not be read after saving.",
            )
        return build_operation_view(row), True


def get_operation(session_id: int, operation_id: str) -> dict:
    """Return one operation only when it belongs to the requested session."""
    row = db.one(
        "SELECT operation.* FROM authoring_operation AS operation "
        "JOIN session ON session.id = operation.session_id "
        "WHERE operation.session_id = ? AND operation.operation_id = ?",
        session_id,
        operation_id,
    )
    if row is None:
        raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")
    return build_operation_view(row)
