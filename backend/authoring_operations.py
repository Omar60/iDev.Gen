"""Persistence and closed views for recoverable authoring operation claims.

Remote calls stay with their operation workers. This module provides the
short transactional lease and response boundaries those workers use around
remote I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import sys
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


@dataclass(frozen=True)
class OperationClaim:
    """Backend-only identity captured by one worker after a successful start."""

    operation_id: str
    session_id: int
    plan_revision: int
    fencing_token: int
    request_digest: str


_OPERATION_LEASE_TICKET_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class OperationLeaseTicket:
    """Private proof binding one renewed lease to its effective inputs."""

    claim: OperationClaim
    lease_expires_at: str
    input_fingerprint: str
    _signature: str = field(repr=False, compare=False)


def _operation_lease_ticket_signature(
    claim: OperationClaim,
    lease_expires_at: str,
    input_fingerprint: str,
) -> str:
    payload = json.dumps(
        {
            "purpose": "authoring-operation-lease-ticket-v1",
            "claim": {
                "operation_id": claim.operation_id,
                "session_id": claim.session_id,
                "plan_revision": claim.plan_revision,
                "fencing_token": claim.fencing_token,
                "request_digest": claim.request_digest,
            },
            "lease_expires_at": lease_expires_at,
            "input_fingerprint": input_fingerprint,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_OPERATION_LEASE_TICKET_KEY, payload, hashlib.sha256).hexdigest()


def _is_authentic_operation_lease_ticket(ticket: OperationLeaseTicket) -> bool:
    if (
        not isinstance(ticket.claim, OperationClaim)
        or not isinstance(ticket.lease_expires_at, str)
        or not isinstance(ticket.input_fingerprint, str)
        or not isinstance(ticket._signature, str)
    ):
        return False
    try:
        expected = _operation_lease_ticket_signature(
            ticket.claim,
            ticket.lease_expires_at,
            ticket.input_fingerprint,
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(ticket._signature, expected)


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


class _OperationRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: StrictInt

    @field_validator("expected_revision", mode="before")
    @classmethod
    def validate_expected_revision(cls, value: Any) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("expected_revision must be a positive integer")
        return value


class _SharedSuggestionAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: StrictInt
    accepted: dict[StrictStr, StrictStr]

    @field_validator("expected_revision", mode="before")
    @classmethod
    def validate_expected_revision(cls, value: Any) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("expected_revision must be a positive integer")
        return value

    @field_validator("accepted", mode="before")
    @classmethod
    def validate_accepted(cls, value: Any) -> dict[str, str]:
        if (
            not isinstance(value, dict)
            or not value
            or len(value) > 2
            or any(
                field not in ("look", "initial_wardrobe")
                or not isinstance(text, str)
                for field, text in value.items()
            )
        ):
            raise ValueError("accepted must contain look and/or initial_wardrobe string values")
        return value


_OPERATION_DIAGNOSTICS = {
    "application_restarted": (
        "The application restarted before this operation finished. "
        "Completed work was preserved for resuming."
    ),
    "lease_expired": (
        "The authoring lease expired. Completed work was preserved for resuming."
    ),
    "cancel_requested": (
        "The operation was cancelled. Completed work was preserved for resuming."
    ),
    "cancel_pending": (
        "Cancellation was requested; any in-flight response will be discarded."
    ),
    "plan_changed": "The plan changed; this operation was cancelled.",
    "inputs_changed": (
        "The effective plan, resource, or workflow inputs changed; "
        "this operation was cancelled."
    ),
    "feature_disabled": (
        "Resource planning was disabled; this operation was cancelled (feature_disabled)."
    ),
    "item_failed": (
        "The authoring assistant could not complete this item. "
        "Completed work was preserved for retry."
    ),
    "input_authority_unavailable": (
        "The operation's original inputs could not be verified after migration. "
        "Reload the plan and start again."
    ),
}
_SAFE_OPERATION_DIAGNOSTICS = frozenset(_OPERATION_DIAGNOSTICS.values())


def _resource_planning_enabled() -> bool:
    """Read the live app gate when a backend worker calls this module directly."""
    for module_name in ("main", "backend.main"):
        app_module = sys.modules.get(module_name)
        gate = getattr(app_module, "is_resource_planning_enabled", None)
        if callable(gate):
            return bool(gate())
    return True


def _resolve_planning_enabled(requested: bool | None) -> bool:
    live = _resource_planning_enabled()
    return live if requested is None else live and requested


def normalize_operation_revision_request(payload: Any) -> int:
    """Validate the closed body shared by operation cancel and resume."""
    if not isinstance(payload, dict):
        raise AuthoringOperationError(422, "invalid_request", "Request body must be an object.")
    try:
        return _OperationRevisionRequest.model_validate(payload).expected_revision
    except ValidationError as exc:
        errors = exc.errors()
        if any(error.get("type") == "extra_forbidden" for error in errors):
            code, message = "extra_field_forbidden", "Unknown operation fields are not permitted."
        elif any(error.get("type") == "missing" for error in errors):
            code, message = "missing_field", "A required operation field is missing."
        else:
            code, message = "invalid_request", "The operation request does not match its contract."
        raise AuthoringOperationError(422, code, message) from exc


def normalize_shared_suggestion_acceptance(payload: Any) -> dict:
    """Validate the closed reviewed-values body for a suggestion acceptance."""
    if not isinstance(payload, dict):
        raise AuthoringOperationError(422, "invalid_request", "Request body must be an object.")
    try:
        values = _SharedSuggestionAcceptanceRequest.model_validate(payload).model_dump()
    except ValidationError as exc:
        errors = exc.errors()
        if any(error.get("type") == "extra_forbidden" for error in errors):
            code, message = "extra_field_forbidden", "Unknown operation fields are not permitted."
        elif any(error.get("type") == "missing" for error in errors):
            code, message = "missing_field", "A required operation field is missing."
        else:
            code, message = "invalid_request", "The acceptance request does not match its contract."
        raise AuthoringOperationError(422, code, message) from exc

    accepted = values["accepted"]
    return {
        "expected_revision": values["expected_revision"],
        "accepted": accepted,
        # Acceptance belongs to one succeeded operation. Its revision is
        # checked on the first write; replay identity is the reviewed content.
        "acceptance_digest": resource_store.canonical_digest({"accepted": accepted}),
    }


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


def _normalize_shared_suggestion_input(value: Any, *, plan_revision: int) -> dict:
    """Validate the exact assistant request stored for suggestion evidence."""
    required = {"messages", "model", "parameters", "plan_revision"}
    if not isinstance(value, dict) or set(value) != required:
        raise AuthoringOperationError(
            409,
            "operation_result_invalid",
            "The saved suggestion request evidence is invalid.",
        )
    messages = value["messages"]
    if not isinstance(messages, list):
        raise AuthoringOperationError(
            409,
            "operation_result_invalid",
            "The saved suggestion request evidence is invalid.",
        )
    normalized_messages = []
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in ("system", "user", "assistant")
            or not isinstance(message["content"], str)
        ):
            raise AuthoringOperationError(
                409,
                "operation_result_invalid",
                "The saved suggestion request evidence is invalid.",
            )
        normalized_messages.append({"role": message["role"], "content": message["content"]})
    model = value["model"]
    parameters = value["parameters"]
    input_revision = value["plan_revision"]
    if (
        not isinstance(model, str)
        or not model
        or type(parameters) is not dict
        or type(input_revision) is not int
        or input_revision != plan_revision
    ):
        raise AuthoringOperationError(
            409,
            "operation_result_invalid",
            "The saved suggestion request evidence is invalid.",
        )
    # Match the transport's non-secret JSON controls; credentials stay in headers.
    if set(parameters) - {"temperature", "stream", "response_format", "reasoning_effort"}:
        raise AuthoringOperationError(
            409,
            "operation_result_invalid",
            "The saved suggestion request evidence is invalid.",
        )
    normalized_parameters = {}
    if "temperature" in parameters:
        temperature = parameters["temperature"]
        if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
            raise AuthoringOperationError(
                409,
                "operation_result_invalid",
                "The saved suggestion request evidence is invalid.",
            )
        normalized_parameters["temperature"] = temperature
    if "stream" in parameters:
        if type(parameters["stream"]) is not bool or parameters["stream"] is not False:
            raise AuthoringOperationError(
                409,
                "operation_result_invalid",
                "The saved suggestion request evidence is invalid.",
            )
        normalized_parameters["stream"] = False
    if "response_format" in parameters:
        response_format = parameters["response_format"]
        if (
            type(response_format) is not dict
            or set(response_format) != {"type"}
            or type(response_format["type"]) is not str
            or response_format["type"] != "json_object"
        ):
            raise AuthoringOperationError(
                409,
                "operation_result_invalid",
                "The saved suggestion request evidence is invalid.",
            )
        normalized_parameters["response_format"] = {"type": "json_object"}
    if "reasoning_effort" in parameters:
        if type(parameters["reasoning_effort"]) is not str or parameters["reasoning_effort"] != "none":
            raise AuthoringOperationError(
                409,
                "operation_result_invalid",
                "The saved suggestion request evidence is invalid.",
            )
        normalized_parameters["reasoning_effort"] = "none"
    normalized = {
        "messages": normalized_messages,
        "model": model,
        "parameters": normalized_parameters,
        "plan_revision": input_revision,
    }
    try:
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AuthoringOperationError(
            409,
            "operation_result_invalid",
            "The saved suggestion request evidence is invalid.",
        ) from exc
    return json.loads(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")))


def _public_operation_result(row: dict, result: Any) -> Any:
    """Hide the exact private assistant input while exposing pending choices."""
    if row.get("kind") != "shared_suggestions" or result is None:
        return result
    if isinstance(result, dict) and set(result).issubset({"items", "input"}) and "items" in result:
        return {"items": result["items"]}
    return None


def build_operation_view(
    row: dict,
    *,
    can_cancel: bool | None = None,
    can_resume: bool = False,
) -> dict:
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

    safe_error = row.get("error")
    if "input_digest" in row and not row.get("input_digest"):
        safe_error = _OPERATION_DIAGNOSTICS["input_authority_unavailable"]
    elif safe_error not in _SAFE_OPERATION_DIAGNOSTICS:
        safe_error = (
            _OPERATION_DIAGNOSTICS["item_failed"]
            if row.get("failed_item") is not None
            else (
                "The operation could not be completed. Reload the plan and try again."
                if row.get("error") is not None
                else None
            )
        )

    failed = None
    if row.get("failed_item") is not None:
        failed = {
            "take_id": row["failed_item"],
            "error": safe_error or _OPERATION_DIAGNOSTICS["item_failed"],
        }

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
        "result": _public_operation_result(row, result),
        "error": safe_error,
        "can_cancel": row["state"] == "active" if can_cancel is None else can_cancel,
        "can_resume": can_resume,
    }


def _get_operation(operation_id: str) -> dict | None:
    return db.one("SELECT * FROM authoring_operation WHERE operation_id = ?", operation_id)


def load_worker_claim(
    session_id: int,
    operation_id: str,
    *,
    planning_enabled: bool | None = None,
) -> OperationClaim:
    """Load the private fence and request identity for an internal worker.

    The returned token is never included in ``OperationView`` or an HTTP
    response. A worker must renew this claim before each remote call or item
    scheduling boundary; every use rechecks it against the persisted row.
    """
    enabled = _resolve_planning_enabled(planning_enabled)
    _recover_pending_operations(
        planning_enabled=enabled,
        now_text=db.now(),
        session_id=session_id,
    )
    row = db.one(
        "SELECT * FROM authoring_operation WHERE session_id = ? AND operation_id = ?",
        session_id,
        operation_id,
    )
    if row is None:
        raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")
    with db.transaction():
        row = _recover_row_in_transaction(
            row,
            now_text=db.now(),
            planning_enabled=enabled,
            check_inputs=True,
        )
    if row["state"] != "active":
        raise AuthoringOperationError(409, "operation_not_active", "The authoring operation is not active.")
    return OperationClaim(
        operation_id=row["operation_id"],
        session_id=int(row["session_id"]),
        plan_revision=int(row["plan_revision"]),
        fencing_token=int(row["fencing_token"]),
        request_digest=row["request_digest"],
    )


def _utc_datetime(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise AuthoringOperationError(
            500,
            "operation_state_invalid",
            f"The saved operation {field} could not be read.",
        ) from exc
    if parsed.tzinfo is None:
        raise AuthoringOperationError(
            500,
            "operation_state_invalid",
            f"The saved operation {field} could not be read.",
        )
    return parsed.astimezone(timezone.utc)


def _require_live_claim(
    row: dict,
    claim: OperationClaim,
    *,
    now: datetime,
) -> None:
    if (
        row["operation_id"] != claim.operation_id
        or int(row["session_id"]) != claim.session_id
    ):
        raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")
    if (
        row["state"] == "expired"
        and row["request_digest"] == claim.request_digest
        and int(row["plan_revision"]) == claim.plan_revision
    ):
        raise AuthoringOperationError(409, "authoring_lease_expired", "The authoring operation lease has expired.")
    if int(row["fencing_token"]) != claim.fencing_token:
        raise AuthoringOperationError(409, "authoring_owner_stale", "This worker no longer owns the operation.")
    if row["request_digest"] != claim.request_digest:
        raise AuthoringOperationError(409, "authoring_inputs_stale", "The operation request inputs changed.")
    if int(row["plan_revision"]) != claim.plan_revision:
        raise AuthoringOperationError(409, "authoring_owner_stale", "This worker no longer owns the operation.")
    if row["state"] != "active":
        raise AuthoringOperationError(409, "operation_not_active", "The authoring operation is not active.")
    deadline = _utc_datetime(row["lease_expires_at"], field="lease")
    if now >= deadline:
        raise AuthoringOperationError(409, "authoring_lease_expired", "The authoring operation lease has expired.")


def _current_operation_context(
    row: dict,
) -> tuple[dict, list[str], list[str], list[str], str, str]:
    """Revalidate effective inputs and return ticket and durable fingerprints."""
    from backend import resource_preparation, workflow_binding

    session_id = int(row["session_id"])
    try:
        session = db.one("SELECT id, settings FROM session WHERE id = ?", session_id)
        if session is None:
            raise AuthoringOperationError(404, "session_not_found", "Session not found.")
        if session_plan.read_composition_mode(session.get("settings")) != session_plan.MODE_RESOURCE_V1:
            raise AuthoringOperationError(
                409,
                "plan_revision_stale",
                "The authoring plan is no longer current.",
            )

        draft_row = db.one(
            "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
            session_id,
        )
        if draft_row is None or int(draft_row["plan_revision"]) != int(row["plan_revision"]):
            raise AuthoringOperationError(
                409,
                "plan_revision_stale",
                "The authoring plan changed; discard this response and reload the plan.",
            )
        plan = json.loads(draft_row["plan_json"])
        if not isinstance(plan, dict):
            raise ValueError("saved plan is not an object")
        plan = session_plan.validate_draft(plan)
        if session_plan.classify_plan_authoring(plan) != session_plan.PLAN_AUTHORING_KIND_AUTOMATIC:
            raise AuthoringOperationError(
                409,
                "authoring_inputs_stale",
                "The current plan no longer permits this automatic operation.",
            )

        requested = _decode_json(row.get("requested_json"), default=None)
        completed = _decode_json(row.get("completed_json"), default=None)
        remaining = _decode_json(row.get("remaining_json"), default=None)
        if any(
            not isinstance(value, list) or any(not isinstance(item, str) for item in value)
            for value in (requested, completed, remaining)
        ):
            raise AuthoringOperationError(
                500,
                "operation_state_invalid",
                "The saved operation progress could not be read.",
            )
        failed_item = row.get("failed_item")
        if failed_item is not None and not isinstance(failed_item, str):
            raise AuthoringOperationError(
                500,
                "operation_state_invalid",
                "The saved operation progress could not be read.",
            )
        incomplete = ([failed_item] if failed_item is not None else []) + remaining
        if requested != completed + incomplete:
            raise AuthoringOperationError(
                500,
                "operation_state_invalid",
                "The saved operation progress is inconsistent.",
            )

        request_body = {
            "expected_revision": int(row["plan_revision"]),
            "kind": row["kind"],
        }
        if row["kind"] == "prepare_takes":
            request_body["take_ids"] = requested
            take_order = [
                take["take_id"]
                for take in plan.get("takes", [])
                if isinstance(take, dict) and isinstance(take.get("take_id"), str)
            ]
            try:
                positions = [take_order.index(take_id) for take_id in requested]
            except ValueError as exc:
                raise AuthoringOperationError(
                    409,
                    "authoring_inputs_stale",
                    "The requested takes changed; discard this response and reload the plan.",
                ) from exc
            if not requested or len(set(requested)) != len(requested) or positions != sorted(positions):
                raise AuthoringOperationError(
                    409,
                    "authoring_inputs_stale",
                    "The requested take order changed; discard this response and reload the plan.",
                )
        elif row["kind"] == "shared_suggestions":
            shared_state = plan["authoring"]["shared_state"]
            current_targets = [
                field for field in ("look", "initial_wardrobe")
                if shared_state[field]["origin"] == "none"
            ]
            if requested != current_targets or not requested:
                raise AuthoringOperationError(
                    409,
                    "authoring_inputs_stale",
                    "The missing shared choices changed; discard this response and reload the plan.",
                )
        else:
            raise AuthoringOperationError(
                500,
                "operation_state_invalid",
                "The saved operation kind could not be read.",
            )

        if resource_store.canonical_digest(request_body) != row["request_digest"]:
            raise AuthoringOperationError(
                409,
                "authoring_inputs_stale",
                "The operation request inputs changed; discard this response and reload the plan.",
            )

        binding = workflow_binding.validate_workflow_binding_against_session(session_id)
        if binding is None:
            raise AuthoringOperationError(
                409,
                "authoring_inputs_stale",
                "The current workflow binding is unavailable; discard this response and reload the plan.",
            )
        effective_inputs: dict[str, Any]
        if row["kind"] == "prepare_takes":
            effective_inputs = {
                "takes": [
                    {
                        "take_id": take_id,
                        "inputs": resource_preparation.prepare_take_inputs(
                            session_id,
                            int(row["plan_revision"]),
                            take_id,
                        ),
                    }
                    for take_id in requested
                ],
            }
        else:
            summary = resource_preparation.build_shared_state_summary(plan)
            if not isinstance(summary, dict) or summary.get("available") is not True:
                raise AuthoringOperationError(
                    409,
                    "authoring_inputs_stale",
                    "The current scene resources are no longer authorized; discard this response and reload the plan.",
                )
            effective_inputs = {"shared_summary": summary}
    except AuthoringOperationError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        workflow_binding.WorkflowChanged,
        workflow_binding.StoredPlanUnreadable,
    ) as exc:
        raise AuthoringOperationError(
            409,
            "authoring_inputs_stale",
            "The current plan, resource or workflow inputs are no longer valid.",
        ) from exc

    input_digest = resource_store.canonical_digest({
        "kind": row["kind"],
        "plan_revision": int(row["plan_revision"]),
        "request_digest": row["request_digest"],
        "requested": requested,
        "plan": plan,
        "workflow_id": binding,
        "effective_inputs": effective_inputs,
    })
    fingerprint = resource_store.canonical_digest({
        "operation_id": row["operation_id"],
        "fencing_token": int(row["fencing_token"]),
        "kind": row["kind"],
        "plan_revision": int(row["plan_revision"]),
        "request_digest": row["request_digest"],
        "progress": {
            "requested": requested,
            "completed": completed,
            "failed_item": failed_item,
            "remaining": remaining,
            "result": _decode_json(row.get("result_json"), default=None),
        },
        "plan": plan,
        "workflow_id": binding,
        "effective_inputs": effective_inputs,
    })
    return plan, requested, completed, remaining, fingerprint, input_digest


def _current_operation_inputs(row: dict) -> tuple[dict, list[str], list[str], list[str], str]:
    """Keep the worker-ticket projection stable while sharing authoritative validation."""
    return _current_operation_context(row)[:5]


def _require_original_input_digest(row: dict, current_digest: str) -> None:
    original = row.get("input_digest")
    if not isinstance(original, str) or not original or original != current_digest:
        raise AuthoringOperationError(
            409,
            "authoring_inputs_stale",
            "The effective operation inputs changed; discard this response and reload the plan.",
        )


def _diagnostic(code: str) -> str:
    return _OPERATION_DIAGNOSTICS[code]


def _transition_terminal(
    row: dict,
    *,
    state: str,
    diagnostic_code: str,
    now_text: str,
    bump_fence: bool = True,
) -> None:
    """Commit a terminal transition while preserving result and progress."""
    db.conn().execute(
        """UPDATE authoring_operation
           SET state = ?, fencing_token = fencing_token + ?, lease_expires_at = NULL,
               error = ?, updated_at = ?
           WHERE operation_id = ? AND state = ? AND fencing_token = ?""",
        (
            state,
            1 if bump_fence else 0,
            _diagnostic(diagnostic_code),
            now_text,
            row["operation_id"],
            row["state"],
            row["fencing_token"],
        ),
    )


def _recover_row_in_transaction(
    row: dict,
    *,
    now_text: str,
    planning_enabled: bool,
    check_inputs: bool,
) -> dict:
    """Recover one operation after the lease, process, gate, or plan changes."""
    if row["state"] not in ("active", "cancel_requested"):
        return row

    if not planning_enabled:
        _transition_terminal(
            row,
            state="cancelled",
            diagnostic_code="feature_disabled",
            now_text=now_text,
        )
    elif _utc_datetime(row["lease_expires_at"], field="lease") <= _utc_datetime(
        now_text, field="current time",
    ):
        _transition_terminal(
            row,
            state="cancelled" if row["state"] == "cancel_requested" else "expired",
            diagnostic_code=(
                "cancel_requested" if row["state"] == "cancel_requested" else "lease_expired"
            ),
            now_text=now_text,
        )
    elif check_inputs and row["state"] == "active":
        try:
            *_, current_digest = _current_operation_context(row)
            _require_original_input_digest(row, current_digest)
        except AuthoringOperationError as exc:
            if exc.code in ("plan_revision_stale", "authoring_inputs_stale"):
                _transition_terminal(
                    row,
                    state="cancelled",
                    diagnostic_code=(
                        "plan_changed" if exc.code == "plan_revision_stale" else "inputs_changed"
                    ),
                    now_text=now_text,
                )
            else:
                raise

    recovered = _get_operation(row["operation_id"])
    if recovered is None:
        raise AuthoringOperationError(
            500,
            "operation_state_invalid",
            "The operation could not be read after recovery.",
        )
    return recovered


def _recover_pending_operations(
    *,
    planning_enabled: bool,
    now_text: str,
    session_id: int | None = None,
    limit: int = 64,
) -> None:
    """Run a bounded expiry/disablement sweep; the target record is recovered separately."""
    query = (
        "SELECT * FROM authoring_operation WHERE state IN ('active', 'cancel_requested') "
        "AND (" + ("? = 0 OR " if not planning_enabled else "") + "lease_expires_at <= ?) "
    )
    params: list[Any] = []
    if not planning_enabled:
        params.append(0)
    params.append(now_text)
    if session_id is not None:
        query += "AND session_id = ? "
        params.append(session_id)
    query += "ORDER BY created_at, operation_id LIMIT ?"
    params.append(limit)

    with db.transaction():
        rows = db.conn().execute(query, tuple(params)).fetchall()
        for raw in rows:
            _recover_row_in_transaction(
                dict(raw),
                now_text=now_text,
                planning_enabled=planning_enabled,
                check_inputs=False,
            )


def recover_session_operations(
    session_id: int,
    *,
    planning_enabled: bool | None = None,
    now: str | None = None,
    check_inputs: bool = True,
) -> int:
    """Recover one session's current owner and a bounded global expiry batch."""
    enabled = _resolve_planning_enabled(planning_enabled)
    now_text = now or db.now()
    _recover_pending_operations(
        planning_enabled=enabled,
        now_text=now_text,
    )
    with db.transaction():
        rows = db.conn().execute(
            "SELECT * FROM authoring_operation WHERE session_id = ? "
            "AND state IN ('active', 'cancel_requested')",
            (session_id,),
        ).fetchall()
        for raw in rows:
            _recover_row_in_transaction(
                dict(raw),
                now_text=now_text,
                planning_enabled=enabled,
                check_inputs=check_inputs,
            )
    return len(rows)


def startup_recovery(*, planning_enabled: bool | None = None, now: str | None = None) -> int:
    """Recover every owner left by the prior process before serving requests."""
    enabled = _resolve_planning_enabled(planning_enabled)
    now_text = now or db.now()
    if enabled:
        active_state, active_error = "expired", _diagnostic("application_restarted")
        cancel_state, cancel_error = "cancelled", _diagnostic("cancel_requested")
    else:
        active_state = cancel_state = "cancelled"
        active_error = cancel_error = _diagnostic("feature_disabled")
    with db.transaction():
        updated = db.conn().execute(
            """UPDATE authoring_operation
               SET state = CASE state WHEN 'active' THEN ? ELSE ? END,
                   fencing_token = fencing_token + 1,
                   lease_expires_at = NULL,
                   error = CASE state WHEN 'active' THEN ? ELSE ? END,
                   updated_at = ?
               WHERE state IN ('active', 'cancel_requested')""",
            (active_state, cancel_state, active_error, cancel_error, now_text),
        )
        return int(updated.rowcount)


def cancel_for_plan_change(session_id: int, new_revision: int) -> int:
    """Fence every old-revision owner in the caller's plan-CAS transaction."""
    with db.transaction():
        now_text = db.now()
        updated = db.conn().execute(
            """UPDATE authoring_operation
               SET state = 'cancelled', fencing_token = fencing_token + 1,
                   lease_expires_at = NULL, error = ?, updated_at = ?
               WHERE session_id = ? AND plan_revision < ?
                 AND state IN ('active', 'cancel_requested')""",
            (_diagnostic("plan_changed"), now_text, session_id, new_revision),
        )
        return int(updated.rowcount)


def _operation_view(
    row: dict,
    *,
    planning_enabled: bool,
    assistant_available: bool,
) -> dict:
    can_resume = False
    if (
        planning_enabled
        and assistant_available
        and row["state"] in ("failed", "cancelled", "expired")
    ):
        try:
            *_, current_digest = _current_operation_context(row)
            _require_original_input_digest(row, current_digest)
            can_resume = True
        except AuthoringOperationError:
            pass
    return build_operation_view(
        row,
        can_cancel=row["state"] == "active",
        can_resume=can_resume,
    )


def _read_claim_row(claim: OperationClaim) -> dict:
    row = _get_operation(claim.operation_id)
    if row is None:
        raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")
    return row


def renew_operation_lease(
    claim: OperationClaim,
    *,
    planning_enabled: bool | None = None,
) -> OperationLeaseTicket:
    """Renew one live owner before a remote call or scheduling another item.

    The transaction commits before this function returns, so callers perform
    network I/O only after the SQLite transaction has ended.
    """
    if not isinstance(claim, OperationClaim):
        raise AuthoringOperationError(422, "invalid_request", "A backend operation claim is required.")
    enabled = _resolve_planning_enabled(planning_enabled)
    now_text = db.now()
    recover_session_operations(
        claim.session_id,
        planning_enabled=enabled,
        now=now_text,
        check_inputs=False,
    )
    enabled = _resolve_planning_enabled(planning_enabled)
    if not enabled:
        raise AuthoringOperationError(
            503,
            "resource_planning_disabled",
            "Resource planning is disabled by configuration.",
        )
    disabled_during_renewal = False
    with db.transaction():
        now = _utc_datetime(now_text, field="current time")
        row = _read_claim_row(claim)
        _require_live_claim(row, claim, now=now)
        *_, input_fingerprint, current_digest = _current_operation_context(row)
        _require_original_input_digest(row, current_digest)
        if not _resolve_planning_enabled(None):
            _transition_terminal(
                row,
                state="cancelled",
                diagnostic_code="feature_disabled",
                now_text=now_text,
            )
            disabled_during_renewal = True
        else:
            deadline = db.authoring_operation_lease_deadline(now)
            updated = db.conn().execute(
                """UPDATE authoring_operation
                   SET lease_expires_at = ?, updated_at = ?
                   WHERE operation_id = ? AND state = 'active'
                     AND fencing_token = ? AND request_digest = ? AND plan_revision = ?""",
                (
                    deadline,
                    now_text,
                    claim.operation_id,
                    claim.fencing_token,
                    claim.request_digest,
                    claim.plan_revision,
                ),
            )
            if updated.rowcount != 1:
                raise AuthoringOperationError(
                    409, "authoring_owner_stale", "This worker no longer owns the operation."
                )
    if disabled_during_renewal:
        raise AuthoringOperationError(
            503,
            "resource_planning_disabled",
            "Resource planning is disabled by configuration.",
        )
    return OperationLeaseTicket(
        claim=claim,
        lease_expires_at=deadline,
        input_fingerprint=input_fingerprint,
        _signature=_operation_lease_ticket_signature(claim, deadline, input_fingerprint),
    )


def _normalize_response_items(items: Any) -> list[dict]:
    if not isinstance(items, list) or not items or len(items) > 20:
        raise AuthoringOperationError(
            422,
            "invalid_operation_result",
            "A response must contain one or more ordered operation results.",
        )
    normalized = []
    for item in items:
        if type(item) is not dict or set(item) != {"target", "result"}:
            raise AuthoringOperationError(
                422,
                "invalid_operation_result",
                "Each operation result must contain exactly target and result.",
            )
        target = item["target"]
        if not isinstance(target, str) or not target:
            raise AuthoringOperationError(
                422,
                "invalid_operation_result",
                "Operation result targets must be non-empty strings.",
            )
        normalized.append({"target": target, "result": item["result"]})
    if len({item["target"] for item in normalized}) != len(normalized):
        raise AuthoringOperationError(
            422,
            "invalid_operation_result",
            "Operation result targets must not repeat.",
        )
    try:
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise AuthoringOperationError(
            422,
            "invalid_operation_result",
            "Operation results must contain JSON values.",
        ) from exc


def persist_operation_response(
    claim: OperationClaim,
    ticket: OperationLeaseTicket,
    items: Any,
    *,
    suggestion_input: Any = None,
    planning_enabled: bool | None = None,
) -> dict:
    """Atomically persist validated target results and ordered progress.

    Type-specific workers validate remote output before calling this helper.
    This transaction independently rechecks fencing, request/revision identity,
    and current resource/workflow authority so validated output alone cannot
    bypass those checks.
    """
    if not isinstance(claim, OperationClaim) or not isinstance(ticket, OperationLeaseTicket):
        raise AuthoringOperationError(422, "invalid_request", "A backend operation lease ticket is required.")
    if not _is_authentic_operation_lease_ticket(ticket):
        raise AuthoringOperationError(422, "invalid_request", "A backend operation lease ticket is required.")
    enabled = _resolve_planning_enabled(planning_enabled)
    now_text = db.now()
    recover_session_operations(
        claim.session_id,
        planning_enabled=enabled,
        now=now_text,
        check_inputs=False,
    )
    enabled = _resolve_planning_enabled(planning_enabled)
    if not enabled:
        raise AuthoringOperationError(
            503,
            "resource_planning_disabled",
            "Resource planning is disabled by configuration.",
        )
    cancelled_after_response = False
    response_view = None
    with db.transaction():
        now = _utc_datetime(now_text, field="current time")
        row = _read_claim_row(claim)
        if row["state"] == "cancel_requested":
            if (
                ticket.claim != claim
                or row["operation_id"] != claim.operation_id
                or int(row["session_id"]) != claim.session_id
                or int(row["plan_revision"]) != claim.plan_revision
                or int(row["fencing_token"]) != claim.fencing_token
                or row["request_digest"] != claim.request_digest
                or row["lease_expires_at"] != ticket.lease_expires_at
            ):
                raise AuthoringOperationError(
                    409, "authoring_owner_stale", "This worker no longer owns the operation."
                )
            _transition_terminal(
                row,
                state="cancelled",
                diagnostic_code="cancel_requested",
                now_text=now_text,
            )
            cancelled_after_response = True
        else:
            _require_live_claim(row, claim, now=now)
            if ticket.claim != claim:
                raise AuthoringOperationError(
                    409, "authoring_owner_stale", "This worker no longer owns the operation."
                )
            if row["lease_expires_at"] != ticket.lease_expires_at:
                raise AuthoringOperationError(
                    409, "authoring_owner_stale", "This worker no longer owns the operation."
                )

            # This check is inside the write transaction. If the gate changed
            # after the API precheck, commit cancellation without inspecting or
            # storing any returned assistant content.
            if not _resolve_planning_enabled(None):
                _transition_terminal(
                    row,
                    state="cancelled",
                    diagnostic_code="feature_disabled",
                    now_text=now_text,
                )
                saved = _get_operation(claim.operation_id)
                if saved is None:
                    raise AuthoringOperationError(
                        500,
                        "operation_state_invalid",
                        "The operation could not be read after cancellation.",
                    )
                response_view = build_operation_view(saved, can_cancel=False, can_resume=False)
            else:
                _, requested, completed, remaining, current_fingerprint = _current_operation_inputs(row)
                if current_fingerprint != ticket.input_fingerprint:
                    raise AuthoringOperationError(
                        409,
                        "authoring_inputs_stale",
                        "The effective operation inputs changed; discard this response and reload the plan.",
                    )
                normalized_items = _normalize_response_items(items)
                encoded_items = json.dumps(normalized_items, ensure_ascii=False, separators=(",", ":"))
                targets = [item["target"] for item in normalized_items]
                if targets != remaining[:len(targets)]:
                    raise AuthoringOperationError(
                        409,
                        "authoring_inputs_stale",
                        "The response does not match the next ordered operation targets.",
                    )

                previous_result = _decode_json(row.get("result_json"), default=None)
                if previous_result is None:
                    previous_result = {"items": []}
                allowed_result_keys = {"items"}
                if row["kind"] == "shared_suggestions":
                    allowed_result_keys.add("input")
                if (
                    type(previous_result) is not dict
                    or set(previous_result) - allowed_result_keys
                    or "items" not in previous_result
                    or not isinstance(previous_result["items"], list)
                ):
                    raise AuthoringOperationError(
                        500,
                        "operation_state_invalid",
                        "The saved operation result could not be read.",
                    )
                if row["kind"] == "prepare_takes" and suggestion_input is not None:
                    raise AuthoringOperationError(
                        422,
                        "invalid_operation_result",
                        "Suggestion request evidence is only valid for shared_suggestions operations.",
                    )
                saved_suggestion_input = previous_result.get("input")
                if row["kind"] == "shared_suggestions" and suggestion_input is not None:
                    if saved_suggestion_input is None and previous_result["items"]:
                        raise AuthoringOperationError(
                            409,
                            "authoring_inputs_stale",
                            "Suggestion request evidence must be saved with its first proposal response.",
                        )
                    normalized_input = _normalize_shared_suggestion_input(
                        suggestion_input,
                        plan_revision=int(row["plan_revision"]),
                    )
                    if (
                        saved_suggestion_input is not None
                        and resource_store.canonical_digest(saved_suggestion_input)
                        != resource_store.canonical_digest(normalized_input)
                    ):
                        raise AuthoringOperationError(
                            409,
                            "authoring_inputs_stale",
                            "Suggestion request evidence changed while the operation was active.",
                        )
                    saved_suggestion_input = normalized_input
                persisted_items = previous_result["items"] + json.loads(encoded_items)
                completed = completed + targets
                remaining = remaining[len(targets):]
                state = "succeeded" if not remaining else "active"

                # Recheck at the authoritative write boundary in case the
                # process gate changed while effective inputs were validated.
                if not _resolve_planning_enabled(None):
                    _transition_terminal(
                        row,
                        state="cancelled",
                        diagnostic_code="feature_disabled",
                        now_text=now_text,
                    )
                    saved = _get_operation(claim.operation_id)
                    if saved is None:
                        raise AuthoringOperationError(
                            500,
                            "operation_state_invalid",
                            "The operation could not be read after cancellation.",
                        )
                    response_view = build_operation_view(saved, can_cancel=False, can_resume=False)
                else:
                    updated = db.conn().execute(
                        """UPDATE authoring_operation
                           SET state = ?, lease_expires_at = ?, completed_json = ?,
                               remaining_json = ?, result_json = ?, updated_at = ?
                           WHERE operation_id = ? AND state = 'active'
                             AND fencing_token = ? AND request_digest = ? AND plan_revision = ?""",
                        (
                            state,
                            None if state == "succeeded" else row["lease_expires_at"],
                            json.dumps(completed, ensure_ascii=False, separators=(",", ":")),
                            json.dumps(remaining, ensure_ascii=False, separators=(",", ":")),
                            json.dumps(
                                ({"items": persisted_items, "input": saved_suggestion_input}
                                 if row["kind"] == "shared_suggestions" and saved_suggestion_input is not None
                                 else {"items": persisted_items}),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            now_text,
                            claim.operation_id,
                            claim.fencing_token,
                            claim.request_digest,
                            claim.plan_revision,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise AuthoringOperationError(
                            409, "authoring_owner_stale", "This worker no longer owns the operation."
                        )
                    saved = _get_operation(claim.operation_id)
                    if saved is None:
                        raise AuthoringOperationError(
                            500,
                            "operation_state_invalid",
                            "The operation result could not be read after saving.",
                        )
                    response_view = build_operation_view(saved)
    if cancelled_after_response:
        raise AuthoringOperationError(
            409,
            "operation_not_active",
            "The operation was cancelled; this response was discarded.",
        )
    return response_view


def start_operation(
    session_id: int,
    request: dict,
    *,
    planning_enabled: bool,
    assistant_available: bool,
) -> tuple[dict, bool]:
    """Atomically replay or claim one operation for this session."""
    planning_enabled = _resolve_planning_enabled(planning_enabled)
    recover_session_operations(session_id, planning_enabled=planning_enabled)
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
            return _operation_view(
                existing,
                planning_enabled=planning_enabled,
                assistant_available=assistant_available,
            ), False

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
                operation=_operation_view(
                    active,
                    planning_enabled=planning_enabled,
                    assistant_available=assistant_available,
                ),
            )

        if not assistant_available:
            raise AuthoringOperationError(
                409,
                "assistant_unavailable",
                "Configure the prompt assistant before starting this operation.",
            )

        operation_id = str(uuid.uuid4())
        requested_json = json.dumps(requested, ensure_ascii=False, separators=(",", ":"))
        candidate = {
            "operation_id": operation_id,
            "session_id": session_id,
            "plan_revision": actual_revision,
            "kind": kind,
            "request_digest": request["request_digest"],
            "fencing_token": 1,
            "requested_json": requested_json,
            "completed_json": "[]",
            "failed_item": None,
            "remaining_json": requested_json,
            "result_json": None,
        }
        *_, input_digest = _current_operation_context(candidate)
        now = db.now()
        db.run(
            """INSERT INTO authoring_operation (
                   operation_id, request_id, session_id, plan_revision, kind,
                   request_digest, input_digest, state, fencing_token, lease_expires_at,
                   requested_json, completed_json, failed_item, error,
                   remaining_json, result_json, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, '[]', NULL, NULL, ?, NULL, ?, ?)""",
            operation_id,
            request["request_id"],
            session_id,
            actual_revision,
            kind,
            request["request_digest"],
            input_digest,
            db.authoring_operation_lease_deadline(),
            requested_json,
            requested_json,
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
        return _operation_view(
            row,
            planning_enabled=planning_enabled,
            assistant_available=assistant_available,
        ), True


def fail_operation_item(
    claim: OperationClaim,
    ticket: OperationLeaseTicket,
    failed_target: str,
    *,
    planning_enabled: bool | None = None,
) -> dict:
    """Persist a safe per-item failure while retaining completed results for Resume."""
    if not isinstance(claim, OperationClaim) or not isinstance(ticket, OperationLeaseTicket):
        raise AuthoringOperationError(422, "invalid_request", "A backend operation lease ticket is required.")
    if not _is_authentic_operation_lease_ticket(ticket):
        raise AuthoringOperationError(422, "invalid_request", "A backend operation lease ticket is required.")
    if not isinstance(failed_target, str) or not failed_target:
        raise AuthoringOperationError(422, "invalid_request", "A failed operation target is required.")
    enabled = _resolve_planning_enabled(planning_enabled)
    now_text = db.now()
    recover_session_operations(
        claim.session_id,
        planning_enabled=enabled,
        now=now_text,
        check_inputs=False,
    )
    enabled = _resolve_planning_enabled(planning_enabled)
    if not enabled:
        raise AuthoringOperationError(
            503,
            "resource_planning_disabled",
            "Resource planning is disabled by configuration.",
        )

    with db.transaction():
        now = _utc_datetime(now_text, field="current time")
        row = _read_claim_row(claim)
        _require_live_claim(row, claim, now=now)
        if ticket.claim != claim or row["lease_expires_at"] != ticket.lease_expires_at:
            raise AuthoringOperationError(409, "authoring_owner_stale", "This worker no longer owns the operation.")
        _, _, _, remaining, current_fingerprint = _current_operation_inputs(row)
        if current_fingerprint != ticket.input_fingerprint:
            raise AuthoringOperationError(
                409,
                "authoring_inputs_stale",
                "The effective operation inputs changed; discard this response and reload the plan.",
            )
        if not remaining or remaining[0] != failed_target:
            raise AuthoringOperationError(
                409,
                "authoring_inputs_stale",
                "The failure does not match the next ordered operation target.",
            )
        updated = db.conn().execute(
            """UPDATE authoring_operation
               SET state = 'failed', lease_expires_at = NULL, failed_item = ?, error = ?,
                   remaining_json = ?, updated_at = ?
               WHERE operation_id = ? AND state = 'active' AND fencing_token = ?
                 AND request_digest = ? AND plan_revision = ?""",
            (
                failed_target,
                _diagnostic("item_failed"),
                json.dumps(remaining[1:], ensure_ascii=False, separators=(",", ":")),
                now_text,
                claim.operation_id,
                claim.fencing_token,
                claim.request_digest,
                claim.plan_revision,
            ),
        )
        if updated.rowcount != 1:
            raise AuthoringOperationError(409, "authoring_owner_stale", "This worker no longer owns the operation.")
        saved = _get_operation(claim.operation_id)
        if saved is None:
            raise AuthoringOperationError(
                500,
                "operation_state_invalid",
                "The operation result could not be read after saving.",
            )
        return _operation_view(
            saved,
            planning_enabled=enabled,
            assistant_available=True,
        )


def cancel_operation(
    session_id: int,
    operation_id: str,
    expected_revision: int,
    *,
    planning_enabled: bool | None = None,
    assistant_available: bool = True,
) -> tuple[dict, int]:
    """Stop future scheduling and fence late responses through persisted state."""
    enabled = _resolve_planning_enabled(planning_enabled)
    recover_session_operations(session_id, planning_enabled=enabled)
    get_operation(
        session_id,
        operation_id,
        planning_enabled=enabled,
        assistant_available=assistant_available,
    )
    now_text = db.now()
    missing = False
    with db.transaction():
        row = db.one(
            "SELECT * FROM authoring_operation WHERE session_id = ? AND operation_id = ?",
            session_id,
            operation_id,
        )
        if row is None:
            missing = True
            view = None
            status = 200
        else:
            if int(row["plan_revision"]) != expected_revision:
                raise AuthoringOperationError(
                    409,
                    "plan_revision_stale",
                    "The operation belongs to a different plan revision.",
                    operation=_operation_view(
                        row,
                        planning_enabled=enabled,
                        assistant_available=assistant_available,
                    ),
                )
            if row["state"] == "active":
                db.conn().execute(
                    """UPDATE authoring_operation
                       SET state = 'cancel_requested', error = ?, updated_at = ?
                       WHERE operation_id = ? AND state = 'active' AND fencing_token = ?""",
                    (
                        _diagnostic("cancel_pending"),
                        now_text,
                        operation_id,
                        row["fencing_token"],
                    ),
                )
                row = _get_operation(operation_id)
            view = _operation_view(
                row,
                planning_enabled=enabled,
                assistant_available=assistant_available,
            )
            status = 202 if row["state"] == "cancel_requested" else 200
    if missing:
        raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")
    return view, status


def resume_operation(
    session_id: int,
    operation_id: str,
    expected_revision: int,
    *,
    planning_enabled: bool | None = None,
    assistant_available: bool = True,
) -> tuple[dict, int]:
    """Claim a retry with a fresh fence after validating original inputs."""
    enabled = _resolve_planning_enabled(planning_enabled)
    recover_session_operations(session_id, planning_enabled=enabled)
    if not enabled:
        raise AuthoringOperationError(
            503,
            "resource_planning_disabled",
            "Resource planning is disabled by configuration.",
        )
    get_operation(
        session_id,
        operation_id,
        planning_enabled=True,
        assistant_available=assistant_available,
    )

    now_text = db.now()
    now = _utc_datetime(now_text, field="current time")
    missing = False
    with db.transaction():
        row = db.one(
            "SELECT * FROM authoring_operation WHERE session_id = ? AND operation_id = ?",
            session_id,
            operation_id,
        )
        if row is None:
            missing = True
            view = None
            status = 200
        else:
            if int(row["plan_revision"]) != expected_revision:
                raise AuthoringOperationError(
                    409,
                    "plan_revision_stale",
                    "The operation belongs to a different plan revision.",
                    operation=_operation_view(
                        row,
                        planning_enabled=True,
                        assistant_available=assistant_available,
                    ),
                )
            if row["state"] in ("active", "succeeded"):
                view = _operation_view(
                    row,
                    planning_enabled=True,
                    assistant_available=assistant_available,
                )
                status = 200
            elif row["state"] == "cancel_requested":
                raise AuthoringOperationError(
                    409,
                    "operation_cancel_pending",
                    "Cancellation is waiting for its in-flight lease to expire.",
                    operation=_operation_view(
                        row,
                        planning_enabled=True,
                        assistant_available=assistant_available,
                    ),
                )
            elif row["state"] in ("failed", "cancelled", "expired"):
                if not assistant_available:
                    raise AuthoringOperationError(
                        409,
                        "assistant_unavailable",
                        "Configure the prompt assistant before resuming this operation.",
                        operation=_operation_view(
                            row,
                            planning_enabled=True,
                            assistant_available=False,
                        ),
                    )
                try:
                    _, requested, completed, remaining, _, current_digest = (
                        _current_operation_context(row)
                    )
                    _require_original_input_digest(row, current_digest)
                except AuthoringOperationError as exc:
                    raise AuthoringOperationError(
                        exc.status_code,
                        exc.code,
                        exc.message,
                        operation=_operation_view(
                            row,
                            planning_enabled=True,
                            assistant_available=assistant_available,
                        ),
                    ) from exc
                failed_item = row.get("failed_item")
                retry_remaining = ([failed_item] if failed_item is not None else []) + remaining
                if requested != completed + retry_remaining or not retry_remaining:
                    raise AuthoringOperationError(
                        409,
                        "operation_not_resumable",
                        "The saved operation has no valid remaining work to resume.",
                        operation=_operation_view(
                            row,
                            planning_enabled=True,
                            assistant_available=assistant_available,
                        ),
                    )
                deadline = db.authoring_operation_lease_deadline(now)
                updated = db.conn().execute(
                    """UPDATE authoring_operation
                       SET state = 'active', fencing_token = fencing_token + 1,
                           lease_expires_at = ?, failed_item = NULL, error = NULL,
                           remaining_json = ?, updated_at = ?
                       WHERE operation_id = ? AND state IN ('failed', 'cancelled', 'expired')
                         AND fencing_token = ? AND plan_revision = ?""",
                    (
                        deadline,
                        json.dumps(retry_remaining, ensure_ascii=False, separators=(",", ":")),
                        now_text,
                        operation_id,
                        row["fencing_token"],
                        expected_revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise AuthoringOperationError(
                        409, "authoring_owner_stale", "The operation changed while it was being resumed."
                    )
                row = _get_operation(operation_id)
                if row is None:
                    raise AuthoringOperationError(
                        500,
                        "operation_state_invalid",
                        "The operation could not be read after resuming.",
                    )
                view = _operation_view(
                    row,
                    planning_enabled=True,
                    assistant_available=assistant_available,
                )
                status = 202
            else:
                raise AuthoringOperationError(
                    409,
                    "operation_not_resumable",
                    "The authoring operation cannot be resumed from its current state.",
                    operation=_operation_view(
                        row,
                        planning_enabled=True,
                        assistant_available=assistant_available,
                    ),
                )
    if missing:
        raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")
    return view, status


def get_operation(
    session_id: int,
    operation_id: str,
    *,
    planning_enabled: bool | None = None,
    assistant_available: bool = True,
) -> dict:
    """Recover and return one operation only when it belongs to the requested session."""
    enabled = _resolve_planning_enabled(planning_enabled)
    recover_session_operations(session_id, planning_enabled=enabled)
    with db.transaction():
        row = db.one(
            "SELECT * FROM authoring_operation WHERE session_id = ? AND operation_id = ?",
            session_id,
            operation_id,
        )
        if row is None:
            missing = True
            view = None
        else:
            row = _recover_row_in_transaction(
                row,
                now_text=db.now(),
                planning_enabled=enabled,
                check_inputs=True,
            )
            view = _operation_view(
                row,
                planning_enabled=enabled,
                assistant_available=assistant_available,
            )
            missing = False
    if missing:
        raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")
    return view


def accept_shared_suggestion(
    session_id: int,
    operation_id: str,
    request: dict,
    *,
    planning_enabled: bool | None = None,
) -> dict:
    """Accept a succeeded shared-suggestion result through one operation/plan CAS."""
    enabled = _resolve_planning_enabled(planning_enabled)
    if not enabled:
        raise AuthoringOperationError(
            503,
            "resource_planning_disabled",
            "Resource planning is disabled by configuration.",
        )
    acceptance_digest = request["acceptance_digest"]
    now_text = db.now()

    with db.transaction():
        if not _resolve_planning_enabled(None):
            raise AuthoringOperationError(
                503,
                "resource_planning_disabled",
                "Resource planning is disabled by configuration.",
            )
        row = db.one(
            "SELECT * FROM authoring_operation WHERE session_id = ? AND operation_id = ?",
            session_id,
            operation_id,
        )
        if row is None:
            raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")

        saved_digest = row.get("acceptance_digest")
        if saved_digest is not None:
            if saved_digest != acceptance_digest:
                raise AuthoringOperationError(
                    409,
                    "acceptance_conflict",
                    "This suggestion operation was already accepted with different values.",
                )
            saved_result = _decode_json(row.get("acceptance_result_json"), default=None)
            if not isinstance(saved_result, dict):
                raise AuthoringOperationError(
                    500,
                    "operation_state_invalid",
                    "The saved suggestion acceptance result could not be read.",
                )
            return saved_result

        if row["kind"] != "shared_suggestions":
            raise AuthoringOperationError(
                409,
                "operation_kind_invalid",
                "Only a shared-suggestion operation can be accepted.",
            )
        if row["state"] != "succeeded":
            raise AuthoringOperationError(
                409,
                "operation_not_succeeded",
                "Only a succeeded shared-suggestion operation can be accepted.",
            )
        expected_revision = request["expected_revision"]
        if expected_revision != int(row["plan_revision"]):
            raise AuthoringOperationError(
                409,
                "plan_revision_stale",
                "The suggestion operation belongs to a different plan revision.",
            )

        _, requested, completed, remaining, _, current_input_digest = _current_operation_context(row)
        _require_original_input_digest(row, current_input_digest)
        if completed != requested or remaining or row.get("failed_item") is not None:
            raise AuthoringOperationError(
                409,
                "operation_state_invalid",
                "The succeeded suggestion operation has incomplete progress.",
            )

        result = _decode_json(row.get("result_json"), default=None)
        if (
            type(result) is not dict
            or set(result) != {"items", "input"}
            or not isinstance(result["items"], list)
        ):
            raise AuthoringOperationError(
                409,
                "operation_result_invalid",
                "The succeeded suggestion operation has no valid proposal evidence.",
            )
        try:
            normalized_items = _normalize_response_items(result["items"])
        except AuthoringOperationError as exc:
            raise AuthoringOperationError(
                409,
                "operation_result_invalid",
                "The succeeded suggestion proposals are malformed.",
            ) from exc
        targets = [item["target"] for item in normalized_items]
        if targets != requested or any(not isinstance(item["result"], str) for item in normalized_items):
            raise AuthoringOperationError(
                409,
                "operation_result_invalid",
                "The succeeded suggestion proposals do not match the requested shared choices.",
            )
        output = {item["target"]: item["result"] for item in normalized_items}
        accepted = request["accepted"]
        if set(accepted) != set(requested):
            raise AuthoringOperationError(
                422,
                "invalid_request",
                "accepted must contain every field requested by this suggestion operation.",
            )
        evidence_input = _normalize_shared_suggestion_input(
            result["input"],
            plan_revision=int(row["plan_revision"]),
        )
        evidence = {
            "id": row["operation_id"],
            "kind": "shared_choices",
            "input": evidence_input,
            "output": output,
            "accepted": accepted,
        }

        try:
            saved_plan = session_plan.apply_shared_suggestion_acceptance(
                session_id,
                expected_revision,
                expected_fields=requested,
                evidence=evidence,
                accepted=accepted,
            )
        except session_plan.PlanRevisionStale as exc:
            raise AuthoringOperationError(
                409,
                "plan_revision_stale",
                "The authoring plan changed; reload it before accepting these suggestions.",
            ) from exc
        except session_plan.PlanConstantsFrozenAfterGenerated as exc:
            raise AuthoringOperationError(
                409,
                "plan_constants_frozen",
                "Generated work freezes the shared look and wardrobe values.",
            ) from exc
        except (session_plan.PlanValidationError, session_plan.SessionNotInResourceMode) as exc:
            raise AuthoringOperationError(
                409,
                "operation_result_invalid",
                "The current plan or suggestion evidence cannot be accepted.",
            ) from exc
        except session_plan.SessionNotFound as exc:
            raise AuthoringOperationError(404, "session_not_found", "Session not found.") from exc

        response = {
            "plan_revision": saved_plan["plan_revision"],
            "accepted": accepted,
            "evidence": saved_plan["evidence"],
            "conflicts": saved_plan["conflicts"],
        }
        updated = db.conn().execute(
            """UPDATE authoring_operation
               SET acceptance_digest = ?, acceptance_result_json = ?, updated_at = ?
               WHERE operation_id = ? AND state = 'succeeded'
                 AND acceptance_digest IS NULL AND acceptance_result_json IS NULL""",
            (
                acceptance_digest,
                json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                now_text,
                operation_id,
            ),
        )
        if updated.rowcount != 1:
            raise AuthoringOperationError(
                409,
                "acceptance_conflict",
                "The suggestion operation changed while acceptance was being saved.",
            )
        return response
