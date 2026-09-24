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


def load_worker_claim(session_id: int, operation_id: str) -> OperationClaim:
    """Load the private fence and request identity for an internal worker.

    The returned token is never included in ``OperationView`` or an HTTP
    response. A worker must renew this claim before each remote call or item
    scheduling boundary; every use rechecks it against the persisted row.
    """
    row = db.one(
        "SELECT * FROM authoring_operation WHERE session_id = ? AND operation_id = ?",
        session_id,
        operation_id,
    )
    if row is None:
        raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")
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


def _current_operation_inputs(row: dict) -> tuple[dict, list[str], list[str], list[str], str]:
    """Revalidate and fingerprint the exact effective inputs in-tx."""
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
        if requested != completed + remaining:
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
                    for take_id in remaining
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

    fingerprint = resource_store.canonical_digest({
        "operation_id": row["operation_id"],
        "fencing_token": int(row["fencing_token"]),
        "kind": row["kind"],
        "plan_revision": int(row["plan_revision"]),
        "request_digest": row["request_digest"],
        "progress": {
            "requested": requested,
            "completed": completed,
            "remaining": remaining,
            "result": _decode_json(row.get("result_json"), default=None),
        },
        "plan": plan,
        "workflow_id": binding,
        "effective_inputs": effective_inputs,
    })
    return plan, requested, completed, remaining, fingerprint


def _read_claim_row(claim: OperationClaim) -> dict:
    row = _get_operation(claim.operation_id)
    if row is None:
        raise AuthoringOperationError(404, "operation_not_found", "Authoring operation not found.")
    return row


def renew_operation_lease(claim: OperationClaim) -> OperationLeaseTicket:
    """Renew one live owner before a remote call or scheduling another item.

    The transaction commits before this function returns, so callers perform
    network I/O only after the SQLite transaction has ended.
    """
    if not isinstance(claim, OperationClaim):
        raise AuthoringOperationError(422, "invalid_request", "A backend operation claim is required.")
    with db.transaction():
        now_text = db.now()
        now = _utc_datetime(now_text, field="current time")
        row = _read_claim_row(claim)
        _require_live_claim(row, claim, now=now)
        _, _, _, _, input_fingerprint = _current_operation_inputs(row)
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
            raise AuthoringOperationError(409, "authoring_owner_stale", "This worker no longer owns the operation.")
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
    normalized_items = _normalize_response_items(items)
    encoded_items = json.dumps(normalized_items, ensure_ascii=False, separators=(",", ":"))
    with db.transaction():
        now_text = db.now()
        now = _utc_datetime(now_text, field="current time")
        row = _read_claim_row(claim)
        _require_live_claim(row, claim, now=now)
        if ticket.claim != claim:
            raise AuthoringOperationError(409, "authoring_owner_stale", "This worker no longer owns the operation.")
        if row["lease_expires_at"] != ticket.lease_expires_at:
            raise AuthoringOperationError(409, "authoring_owner_stale", "This worker no longer owns the operation.")
        _, requested, completed, remaining, current_fingerprint = _current_operation_inputs(row)
        if current_fingerprint != ticket.input_fingerprint:
            raise AuthoringOperationError(
                409,
                "authoring_inputs_stale",
                "The effective operation inputs changed; discard this response and reload the plan.",
            )
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
        if (
            type(previous_result) is not dict
            or set(previous_result) != {"items"}
            or not isinstance(previous_result["items"], list)
        ):
            raise AuthoringOperationError(
                500,
                "operation_state_invalid",
                "The saved operation result could not be read.",
            )
        persisted_items = previous_result["items"] + json.loads(encoded_items)
        completed = completed + targets
        remaining = remaining[len(targets):]
        state = "succeeded" if not remaining else "active"
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
                json.dumps({"items": persisted_items}, ensure_ascii=False, separators=(",", ":")),
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
        return build_operation_view(saved)


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
