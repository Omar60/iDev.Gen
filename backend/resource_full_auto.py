"""Durable state and control receipts for Full Auto runs.

This module owns only the local run state machine. It never performs assistant
or ComfyUI calls; settlement and reconciliation inputs must come from backend
services that actually observed those operations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
from typing import Literal

import db


RUN_LEASE = timedelta(seconds=60)
_RUN_CLAIM_KEY = secrets.token_bytes(32)
_MANUAL_EDIT_CLAIM_KEY = secrets.token_bytes(32)
_SUPPORTED_AUTHORIZATION_VERSION = 1
_SUPPORTED_POLICY_VERSION = "resource-full-auto-v1"
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PHASES = ("prepare", "validate", "approve", "submit", "execute", "reconcile")
_MAIN_PHASES = _PHASES[:-1]
_TERMINAL = frozenset({"cancelled", "failed", "succeeded", "completed_with_errors"})
_NONTERMINAL = frozenset({
    "active", "waiting_authoring", "waiting_runner", "blocked", "cancel_requested",
})


class FullAutoRunError(Exception):
    """A stable domain failure; route adapters may map ``status_code`` to HTTP."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        current_run: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.current_run = current_run


@dataclass(frozen=True)
class RunClaim:
    """Backend-only lease capability. Never accept or return one from a client."""

    run_id: str
    session_id: int
    epoch: int
    fencing_token: int
    lease_started_at: str
    lease_expires_at: str
    requires_reconciliation: bool
    _signature: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class ManualEditClaim:
    """Private one-revision authority issued only after an explicit pause."""

    run_id: str
    session_id: int
    revision: int
    epoch: int
    fencing_token: int
    plan_revision: int
    _signature: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class RunActionResult:
    """Closed persisted control result plus an optional backend-only claim."""

    view: dict
    claim: RunClaim | None = field(default=None, repr=False, compare=False)
    replayed: bool = False


@dataclass(frozen=True)
class ResumeEligibility:
    """Backend-computed facts required before a run can continue."""

    current_plan_revision: int
    unresolved_work: int
    authorization_valid: bool
    policy_supported: bool
    budget_available: bool
    session_exclusive: bool
    scope_matches: bool
    planning_enabled: bool
    next_state: Literal["active", "waiting_authoring", "waiting_runner"]


@dataclass(frozen=True)
class CompletionEvidence:
    """Settlement facts computed by the backend after owned work is observed."""

    outcome: Literal[
        "all_succeeded", "mixed", "all_shots_failed", "internal_failure", "unknown",
    ]
    unresolved_work: int


@dataclass(frozen=True)
class CancellationEvidence:
    """Backend-only observation; this state service performs no remote probe."""

    outcome: Literal["running", "settled", "unknown"]
    observed_at: str
    unresolved_work: int
    blocker_code: str | None = None
    attempt_ids: tuple[str, ...] = ()
    shot_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LeaseReconciliationEvidence:
    """Backend-only checkpoint evidence after an expired coordinator lease."""

    outcome: Literal["safe_to_continue", "unknown"]
    unresolved_work: int
    blocker_code: str | None = None


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _timestamp(value: str, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise FullAutoRunError(500, "run_state_invalid", f"The saved {field_name} is invalid.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FullAutoRunError(
            500, "run_state_invalid", f"The saved {field_name} is invalid.",
        ) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise FullAutoRunError(500, "run_state_invalid", f"The saved {field_name} is invalid.")
    return result.astimezone(timezone.utc)


def _now_text(value: str | None) -> str:
    text = value if value is not None else db.now()
    _timestamp(text, field_name="current time")
    return text


def run_lease_deadline(started_at: str | None = None) -> str:
    start_text = _now_text(started_at)
    deadline = _timestamp(start_text, field_name="current time") + RUN_LEASE
    return deadline.isoformat(timespec="seconds")


def _digest(value: dict) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise FullAutoRunError(422, "invalid_control_request", f"{field_name} must be a positive integer.")
    return value


def _run_row(run_id: str) -> dict | None:
    return db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)


def _require_run(run_id: str) -> dict:
    row = _run_row(run_id)
    if row is None:
        raise FullAutoRunError(404, "full_auto_run_not_found", "Full Auto run not found.")
    return row


def _current_authority_issue(row: dict, now_text: str) -> str | None:
    """Return why this immutable grant can no longer authorize new work."""
    plan = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?",
        int(row["session_id"]),
    )
    if plan is None or int(plan["plan_revision"]) != int(row["plan_revision"]):
        return "plan_revision_stale"

    receipt = db.one(
        """SELECT session_id, authorization_json, authorization_expires_at
           FROM full_auto_run_receipt WHERE run_id = ?""",
        row["run_id"],
    )
    if receipt is None or int(receipt["session_id"]) != int(row["session_id"]):
        return "authorization_invalid"
    try:
        authorization = json.loads(receipt["authorization_json"])
        expiry = _timestamp(
            receipt["authorization_expires_at"], field_name="authorization expiry",
        )
    except (TypeError, json.JSONDecodeError, FullAutoRunError):
        return "authorization_invalid"
    if not isinstance(authorization, dict):
        return "authorization_invalid"
    if authorization.get("authorization_expires_at") != receipt["authorization_expires_at"]:
        return "authorization_invalid"
    if expiry <= _timestamp(now_text, field_name="current time"):
        return "authorization_expired"

    summary = authorization.get("summary")
    if (
        type(authorization.get("authorization_version")) is not int
        or authorization["authorization_version"] != _SUPPORTED_AUTHORIZATION_VERSION
        or authorization.get("generate_without_pauses") is not True
        or not isinstance(summary, dict)
        or summary.get("policy_version") != _SUPPORTED_POLICY_VERSION
    ):
        return "policy_changed"
    return None


def _authority_error(code: str) -> FullAutoRunError:
    messages = {
        "plan_revision_stale": "The current plan changed; refresh consent before continuing.",
        "authorization_expired": "The Full Auto authorization has expired.",
        "authorization_invalid": "The persisted Full Auto authorization is invalid.",
        "policy_changed": "The recorded Full Auto policy is not supported.",
    }
    return FullAutoRunError(409, code, messages.get(code, "Full Auto authority is no longer valid."))


def _view(row: dict) -> dict:
    """Return status-safe fields only; lease deadlines and fences stay private."""
    return {
        "run_id": row["run_id"],
        "session_id": int(row["session_id"]),
        "revision": int(row["revision"]),
        "epoch": int(row["epoch"]),
        "plan_revision": int(row["plan_revision"]),
        "state": row["state"],
        "phase": row["phase"],
        "cancel_intent": bool(row["cancel_intent"]),
        "blocker_code": row["blocker_code"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_run(run_id: str) -> dict:
    return _view(_require_run(run_id))


def get_run_for_session(session_id: int) -> dict | None:
    row = _run_row_for_session(session_id)
    return None if row is None else _view(row)


def _manual_edit_claim_payload(claim: ManualEditClaim) -> bytes:
    return _canonical_json({
        "purpose": "full-auto-manual-edit-v1",
        "run_id": claim.run_id,
        "session_id": claim.session_id,
        "revision": claim.revision,
        "epoch": claim.epoch,
        "fencing_token": claim.fencing_token,
        "plan_revision": claim.plan_revision,
    }).encode("utf-8")


def _make_manual_edit_claim(row: dict) -> ManualEditClaim:
    unsigned = ManualEditClaim(
        run_id=row["run_id"],
        session_id=int(row["session_id"]),
        revision=int(row["revision"]),
        epoch=int(row["epoch"]),
        fencing_token=int(row["fencing_token"]),
        plan_revision=int(row["plan_revision"]),
        _signature="",
    )
    signature = hmac.new(
        _MANUAL_EDIT_CLAIM_KEY, _manual_edit_claim_payload(unsigned), hashlib.sha256,
    ).hexdigest()
    return ManualEditClaim(**{**unsigned.__dict__, "_signature": signature})


def _require_manual_edit_claim(
    claim: ManualEditClaim,
    row: dict,
    *,
    session_id: int,
    expected_plan_revision: int | None,
) -> dict:
    if not isinstance(claim, ManualEditClaim) or not isinstance(claim._signature, str):
        raise FullAutoRunError(409, "full_auto_edit_authority_stale", "The paused Full Auto edit authority is stale.")
    expected = hmac.new(
        _MANUAL_EDIT_CLAIM_KEY, _manual_edit_claim_payload(claim), hashlib.sha256,
    ).hexdigest()
    actual_plan = db.one("SELECT plan_revision FROM session_plan WHERE session_id = ?", session_id)
    if (
        not hmac.compare_digest(expected, claim._signature)
        or claim.run_id != row["run_id"]
        or claim.session_id != session_id
        or claim.revision != int(row["revision"])
        or claim.epoch != int(row["epoch"])
        or claim.fencing_token != int(row["fencing_token"])
        or claim.plan_revision != int(row["plan_revision"])
        or expected_plan_revision is not None
        and expected_plan_revision != claim.plan_revision
        or actual_plan is None
        or int(actual_plan["plan_revision"]) != claim.plan_revision
        or row["state"] != "blocked"
        or row["blocker_code"] != "manual_edit_pause"
        or bool(row["cancel_intent"])
    ):
        raise FullAutoRunError(409, "full_auto_edit_authority_stale", "The paused Full Auto edit authority is stale.")
    return row


def require_session_mutation_allowed(
    session_id: int,
    *,
    claim: RunClaim | None = None,
    allowed_phases: tuple[str, ...] = (),
    edit_claim: ManualEditClaim | None = None,
    expected_plan_revision: int | None = None,
    target_id: str | None = None,
    operation_id: str | None = None,
) -> dict | None:
    """Fence shared writes unless a live phase owner or paused-edit capability authorizes them.

    Call inside the transaction that performs the protected mutation. Manual
    sessions and terminal Full Auto runs retain their existing behavior.
    """
    row = _run_row_for_session(session_id)
    if row is None or row["state"] in _TERMINAL:
        if claim is not None or edit_claim is not None:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto run owner is stale.")
        return None

    safe = _view(row)
    if edit_claim is not None:
        if claim is not None:
            raise FullAutoRunError(409, "invalid_owner_capability", "Only one Full Auto owner capability may be used.", current_run=safe)
        return _require_manual_edit_claim(
            edit_claim, row, session_id=session_id,
            expected_plan_revision=expected_plan_revision,
        )
    if claim is None:
        raise FullAutoRunError(
            409, "full_auto_session_owned",
            "Full Auto owns this session. Pause it for an explicit edit or settle Cancel before manual work.",
            current_run=safe,
        )

    if not allowed_phases or any(phase not in _MAIN_PHASES for phase in allowed_phases):
        raise FullAutoRunError(500, "invalid_owner_phase", "The Full Auto mutation phase is invalid.", current_run=safe)
    try:
        current = _require_claim(claim, now_text=db.now(), reconciliation=False)
    except FullAutoRunError as exc:
        raise FullAutoRunError(
            exc.status_code, exc.code, exc.message, current_run=safe,
        ) from exc
    if (
        int(current["session_id"]) != session_id
        or current["phase"] not in allowed_phases
        or bool(current["cancel_intent"])
        or expected_plan_revision is not None
        and expected_plan_revision != int(current["plan_revision"])
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto owner is not valid for this mutation phase.", current_run=safe)
    authority_issue = _current_authority_issue(current, db.now())
    if authority_issue is not None:
        raise FullAutoRunError(409, authority_issue, "Full Auto authority is no longer valid for new work.", current_run=safe)
    if target_id is not None or operation_id is not None:
        if current["phase"] != "prepare" or not isinstance(target_id, str) or not target_id or not isinstance(operation_id, str) or not operation_id:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto owner is not valid for this preparation target.", current_run=safe)
        try:
            require_prepare_target_claim(
                claim, target_id, operation_id, now_text=db.now(),
            )
        except FullAutoRunError as exc:
            raise FullAutoRunError(
                exc.status_code, exc.code, exc.message, current_run=safe,
            ) from exc
    return current


def require_prepare_target_claim(
    claim: RunClaim,
    target_id: str,
    operation_id: str,
    *,
    now_text: str | None = None,
) -> dict:
    """Require the current registered child to own this exact preparation target."""
    timestamp = _now_text(now_text)
    with db.transaction():
        row = require_prepare_claim(claim, now_text=timestamp)
        progress = _read_prepare_progress(claim.run_id)
        child = next(
            (item for item in progress["children"] if item["operation_id"] == operation_id),
            None,
        )
        operation = db.one(
            "SELECT session_id, plan_revision, kind, state, full_auto_run_id, "
            "full_auto_run_epoch, full_auto_run_fencing_token "
            "FROM authoring_operation WHERE operation_id = ?",
            operation_id,
        )
        if (
            child is None
            or target_id not in child["target_ids"]
            or target_id in progress["completed_take_ids"]
            or operation is None
            or int(operation["session_id"]) != int(row["session_id"])
            or int(operation["plan_revision"]) != int(row["plan_revision"])
            or operation["kind"] != "prepare_takes"
            or operation["state"] != "active"
            or operation["full_auto_run_id"] != claim.run_id
            or operation["full_auto_run_epoch"] != claim.epoch
            or operation["full_auto_run_fencing_token"] != claim.fencing_token
        ):
            raise FullAutoRunError(409, "run_target_not_owned", "The Full Auto child does not own this preparation target.")
        return row


def _run_row_for_session(session_id: int) -> dict | None:
    return db.one(
        """SELECT * FROM full_auto_run WHERE session_id = ?
           ORDER BY CASE WHEN state IN ('cancelled', 'failed', 'succeeded', 'completed_with_errors')
                         THEN 1 ELSE 0 END,
                    created_at DESC, run_id DESC LIMIT 1""",
        session_id,
    )


def pause_for_manual_edit(
    run_id: str,
    expected_run_revision: int,
    expected_plan_revision: int,
    *,
    now_text: str | None = None,
) -> tuple[dict, ManualEditClaim]:
    """Fence idle owned work and issue private authority for one normal plan CAS.

    This is an explicit suspended state, distinct from settled Cancel/manual
    transfer. The Full Auto run stays blocked and keeps session ownership.
    """
    timestamp = _now_text(now_text)
    claim = None
    view = None
    with db.transaction():
        row = _require_run(run_id)
        if (
            int(row["revision"]) != expected_run_revision
            or int(row["plan_revision"]) != expected_plan_revision
        ):
            raise FullAutoRunError(409, "run_revision_stale", "The Full Auto run changed before it could be paused.", current_run=_view(row))
        if row["state"] not in {"active", "waiting_authoring", "waiting_runner", "blocked"} or row["cancel_intent"]:
            raise FullAutoRunError(409, "run_not_pauseable", "This Full Auto run cannot be paused for editing.", current_run=_view(row))
        if row["state"] == "blocked" and row["blocker_code"] != "manual_edit_pause":
            raise FullAutoRunError(409, "run_not_pauseable", "This Full Auto run requires reconciliation before editing.", current_run=_view(row))
        session_id = int(row["session_id"])
        session = db.one("SELECT status FROM session WHERE id = ?", session_id)
        unsettled_shot = db.one(
            "SELECT id FROM shot WHERE session_id = ? AND status IN ('pending', 'running') LIMIT 1",
            session_id,
        )
        if session is None or session["status"] == "running" or unsettled_shot is not None:
            raise FullAutoRunError(409, "run_work_unsettled", "Owned work must settle before Full Auto can be paused for editing.", current_run=_view(row))
        plan = db.one("SELECT plan_revision FROM session_plan WHERE session_id = ?", int(row["session_id"]))
        if plan is None or int(plan["plan_revision"]) != expected_plan_revision:
            raise FullAutoRunError(409, "plan_revision_stale", "The current plan changed before Full Auto could be paused.", current_run=_view(row))
        changed = db.conn().execute(
            """UPDATE full_auto_run
               SET state = 'blocked', revision = revision + 1,
                   fencing_token = fencing_token + 1, lease_expires_at = NULL,
                   lease_started_at = NULL, resume_phase = phase,
                   cancel_intent = 0, cancel_intent_at = NULL,
                   blocker_code = 'manual_edit_pause',
                   evidence_json = ?, updated_at = ?
               WHERE run_id = ? AND revision = ? AND fencing_token = ?""",
            (_canonical_json({"kind": "manual_edit_pause"}), timestamp,
             run_id, row["revision"], row["fencing_token"]),
        ).rowcount
        if changed != 1:
            raise FullAutoRunError(409, "run_revision_stale", "The Full Auto run changed before it could be paused.", current_run=_view(row))
        updated = _require_run(run_id)
        # Fence any late assistant response using the same child-operation CAS
        # used when a normal plan revision invalidates preparation work.
        from backend import authoring_operations

        authoring_operations.cancel_for_plan_change(session_id, expected_plan_revision + 1)
        claim = _make_manual_edit_claim(updated)
        view = _view(updated)
    if view is None or claim is None:
        raise FullAutoRunError(500, "run_pause_failed", "The Full Auto pause was not saved.")
    return view, claim


def create_run_in_transaction(
    run_id: str,
    session_id: int,
    *,
    plan_revision: int = 1,
    created_at: str | None = None,
) -> dict:
    """Create the initial state beside its receipt inside guided creation."""
    if not db.conn().in_transaction:
        raise FullAutoRunError(
            500, "run_creation_not_atomic", "Full Auto run creation requires the guided transaction.",
        )
    if not isinstance(run_id, str) or not run_id or type(session_id) is not int or session_id <= 0:
        raise FullAutoRunError(500, "run_state_invalid", "The Full Auto creation identity is invalid.")
    plan_revision = _positive_int(plan_revision, "plan_revision")
    timestamp = _now_text(created_at)
    receipt = db.one(
        "SELECT session_id FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    )
    if receipt is None or int(receipt["session_id"]) != session_id:
        raise FullAutoRunError(
            409, "run_receipt_missing", "The immutable Full Auto authorization receipt is missing.",
        )
    plan = db.one("SELECT plan_revision FROM session_plan WHERE session_id = ?", session_id)
    if plan is None or int(plan["plan_revision"]) != plan_revision:
        raise FullAutoRunError(409, "plan_revision_stale", "The initial Full Auto plan is unavailable.")
    db.run(
        """INSERT INTO full_auto_run (
               run_id, session_id, state, phase, revision, epoch, plan_revision,
               fencing_token, lease_expires_at, cancel_intent, blocker_code,
               resume_phase, evidence_json, created_at, updated_at
           ) VALUES (?, ?, 'active', 'prepare', 1, 1, ?, 1, NULL, 0,
                     NULL, NULL, '{}', ?, ?)""",
        run_id, session_id, plan_revision, timestamp, timestamp,
    )
    return _view(_require_run(run_id))


def _claim_payload(claim: RunClaim) -> bytes:
    return _canonical_json({
        "purpose": "full-auto-run-claim-v1",
        "run_id": claim.run_id,
        "session_id": claim.session_id,
        "epoch": claim.epoch,
        "fencing_token": claim.fencing_token,
        "lease_started_at": claim.lease_started_at,
        "lease_expires_at": claim.lease_expires_at,
        "requires_reconciliation": claim.requires_reconciliation,
    }).encode("utf-8")


def _make_claim(row: dict, requires_reconciliation: bool) -> RunClaim:
    unsigned = RunClaim(
        run_id=row["run_id"],
        session_id=int(row["session_id"]),
        epoch=int(row["epoch"]),
        fencing_token=int(row["fencing_token"]),
        lease_started_at=row["lease_started_at"],
        lease_expires_at=row["lease_expires_at"],
        requires_reconciliation=requires_reconciliation,
        _signature="",
    )
    signature = hmac.new(_RUN_CLAIM_KEY, _claim_payload(unsigned), hashlib.sha256).hexdigest()
    return RunClaim(**{**unsigned.__dict__, "_signature": signature})


def _authentic_claim(claim: RunClaim) -> bool:
    if not isinstance(claim, RunClaim) or not isinstance(claim._signature, str):
        return False
    expected = hmac.new(_RUN_CLAIM_KEY, _claim_payload(claim), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, claim._signature)


def _require_claim(
    claim: RunClaim,
    *,
    now_text: str,
    reconciliation: bool = False,
) -> dict:
    if not _authentic_claim(claim):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto run owner is stale.")
    row = _require_run(claim.run_id)
    if (
        int(row["session_id"]) != claim.session_id
        or int(row["epoch"]) != claim.epoch
        or int(row["fencing_token"]) != claim.fencing_token
        or row["lease_started_at"] != claim.lease_started_at
        or row["lease_expires_at"] != claim.lease_expires_at
        or row["lease_expires_at"] is None
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto run owner is stale.")
    if _timestamp(row["lease_expires_at"], field_name="run lease") <= _timestamp(
        now_text, field_name="current time",
    ):
        raise FullAutoRunError(409, "run_lease_expired", "The Full Auto run lease expired.")
    if claim.requires_reconciliation != reconciliation:
        code = "run_reconciliation_required" if claim.requires_reconciliation else "run_reconciliation_unexpected"
        raise FullAutoRunError(409, code, "The Full Auto run requires a different owner operation.")
    if row["state"] in _TERMINAL:
        raise FullAutoRunError(409, "run_terminal", "The current Full Auto execution epoch is terminal.")
    if reconciliation:
        if row["state"] not in {"active", "waiting_authoring", "waiting_runner", "blocked", "cancel_requested"}:
            raise FullAutoRunError(409, "run_reconciliation_unexpected", "The run cannot be reconciled.")
    elif row["state"] in {"blocked", "cancel_requested"}:
        raise FullAutoRunError(409, "run_control_required", "The Full Auto run requires an explicit control action.")
    return row


def claim_run(run_id: str, *, now_text: str | None = None) -> RunClaim:
    """Claim an unowned run; expired ownership first enters reconciliation."""
    timestamp = _now_text(now_text)
    current = _timestamp(timestamp, field_name="current time")
    authority_issue = None
    claim = None
    with db.transaction():
        row = _require_run(run_id)
        if row["state"] not in _NONTERMINAL or row["state"] == "blocked":
            raise FullAutoRunError(409, "run_not_claimable", "The Full Auto run is not claimable.")
        old_deadline = row["lease_expires_at"]
        expired = False
        if old_deadline is not None:
            expired = _timestamp(old_deadline, field_name="run lease") <= current
            if not expired:
                raise FullAutoRunError(409, "run_already_claimed", "The Full Auto run already has an owner.")

        state_requires_reconciliation = row["state"] == "cancel_requested" or bool(row["cancel_intent"])
        prior_phase = row["resume_phase"]
        phase = row["phase"]
        if expired:
            if phase != "reconcile":
                prior_phase = phase
                phase = "reconcile"
            elif prior_phase is None and not state_requires_reconciliation:
                raise FullAutoRunError(
                    409, "run_reconciliation_required", "The expired run has no saved phase to recover.",
                )
        requires_reconciliation = expired or state_requires_reconciliation or phase == "reconcile"
        if requires_reconciliation and phase == "reconcile" and prior_phase is None and not state_requires_reconciliation:
            raise FullAutoRunError(
                409, "run_reconciliation_required", "The run has no saved phase to recover.",
            )

        if not requires_reconciliation:
            authority_issue = _current_authority_issue(row, timestamp)
        if authority_issue is not None:
            # Preserve the run identity while making stale authority explicit.
            # Commit this blocker before returning the refusal to the caller.
            db.run(
                """UPDATE full_auto_run
                   SET state = 'blocked', revision = revision + 1,
                       fencing_token = fencing_token + 1, lease_expires_at = NULL,
                       lease_started_at = NULL, blocker_code = ?, updated_at = ?
                   WHERE run_id = ? AND revision = ? AND fencing_token = ?""",
                authority_issue, timestamp, run_id, row["revision"], row["fencing_token"],
            )
        else:
            deadline = run_lease_deadline(timestamp)
            db.run(
                """UPDATE full_auto_run
                   SET phase = ?, resume_phase = ?, fencing_token = fencing_token + 1,
                       revision = revision + 1, lease_expires_at = ?, lease_started_at = ?,
                       updated_at = ?
                   WHERE run_id = ? AND revision = ? AND fencing_token = ?""",
                phase, prior_phase, deadline, timestamp, timestamp, run_id,
                row["revision"], row["fencing_token"],
            )
            updated = _require_run(run_id)
            claim = _make_claim(updated, requires_reconciliation)
    if authority_issue is not None:
        raise _authority_error(authority_issue)
    if claim is None:
        raise FullAutoRunError(500, "run_claim_failed", "The Full Auto run claim was not saved.")
    return claim


def renew_run_claim(
    claim: RunClaim,
    *,
    now_text: str | None = None,
) -> RunClaim:
    timestamp = _now_text(now_text)
    with db.transaction():
        row = _require_claim(claim, now_text=timestamp, reconciliation=claim.requires_reconciliation)
        deadline = run_lease_deadline(timestamp)
        changed = db.conn().execute(
            """UPDATE full_auto_run SET lease_expires_at = ?, lease_started_at = ?, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?
                 AND lease_expires_at = ? AND lease_started_at = ?""",
            (deadline, timestamp, timestamp, claim.run_id, claim.epoch,
             claim.fencing_token, claim.lease_expires_at, claim.lease_started_at),
        ).rowcount
        if changed != 1:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto run owner is stale.")
        return _make_claim(_require_run(claim.run_id), claim.requires_reconciliation)


def require_prepare_claim(
    claim: RunClaim,
    *,
    session_id: int | None = None,
    now_text: str | None = None,
) -> dict:
    """Authenticate a live prepare-phase capability at a child-work boundary."""
    timestamp = _now_text(now_text)
    row = _require_claim(claim, now_text=timestamp, reconciliation=False)
    if (
        row["state"] != "active"
        or row["phase"] != "prepare"
        or bool(row["cancel_intent"])
        or (session_id is not None and int(row["session_id"]) != session_id)
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto prepare owner is stale.")
    authority_issue = _current_authority_issue(row, timestamp)
    if authority_issue is not None:
        raise _authority_error(authority_issue)
    return row


def _prepare_json_list(raw: str, *, field_name: str) -> list[str]:
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise FullAutoRunError(500, "run_state_invalid", f"Saved preparation {field_name} is invalid.") from exc
    if (
        type(values) is not list
        or any(type(value) is not str or not value for value in values)
        or len(values) != len(set(values))
    ):
        raise FullAutoRunError(500, "run_state_invalid", f"Saved preparation {field_name} is invalid.")
    return values


def _read_prepare_progress(run_id: str) -> dict:
    row = db.one("SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id)
    if row is None:
        raise FullAutoRunError(409, "prepare_checkpoint_missing", "Full Auto preparation has no saved target checkpoint.")
    targets = _prepare_json_list(row["target_ids_json"], field_name="targets")
    completed = _prepare_json_list(row["completed_ids_json"], field_name="progress")
    if (
        not targets
        or len(targets) > 500
        or completed != targets[:len(completed)]
    ):
        raise FullAutoRunError(500, "run_state_invalid", "Saved Full Auto preparation progress is invalid.")
    children = []
    for child in db.q(
        "SELECT batch_index, operation_id, target_ids_json FROM full_auto_prepare_child "
        "WHERE run_id = ? ORDER BY batch_index",
        run_id,
    ):
        child_targets = _prepare_json_list(child["target_ids_json"], field_name="child targets")
        if (
            type(child["batch_index"]) is not int
            or not child_targets
            or len(child_targets) > 20
            or not isinstance(child["operation_id"], str)
        ):
            raise FullAutoRunError(500, "run_state_invalid", "Saved Full Auto child preparation is invalid.")
        children.append({
            "batch_index": child["batch_index"],
            "operation_id": child["operation_id"],
            "target_ids": child_targets,
        })
    return {
        "target_ids": targets,
        "completed_take_ids": completed,
        "children": children,
        "remaining_take_ids": targets[len(completed):],
    }


def initialize_prepare_progress(claim: RunClaim) -> dict:
    """Freeze the exact ordered plan targets before creating any child operation."""
    from backend import session_plan
    from backend import resource_preparation

    timestamp = _now_text(None)
    row = require_prepare_claim(claim, now_text=timestamp)
    plan_row = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        int(row["session_id"]),
    )
    if plan_row is None or int(plan_row["plan_revision"]) != int(row["plan_revision"]):
        raise FullAutoRunError(409, "plan_revision_stale", "The Full Auto plan changed before preparation.")
    try:
        plan = json.loads(plan_row["plan_json"])
        plan = session_plan.validate_draft(plan)
    except (TypeError, json.JSONDecodeError, session_plan.PlanValidationError) as exc:
        raise FullAutoRunError(409, "plan_state_invalid", "The saved Full Auto plan could not be validated.") from exc
    targets = [take["take_id"] for take in plan.get("takes", [])]
    if (
        not targets
        or len(targets) > 500
        or any(type(take_id) is not str or not take_id for take_id in targets)
        or len(targets) != len(set(targets))
    ):
        raise FullAutoRunError(409, "plan_state_invalid", "The Full Auto plan has an invalid preparation target set.")

    # A previously prepared current prefix is already completed work. Validate
    # every row through the real evidence verifier before adopting it; no row or
    # provenance is rewritten here.
    validated_prefix: list[str] = []
    validation_cache: dict = {}
    for take_id in targets:
        snapshot = session_plan._prepared_take_row(
            int(row["session_id"]), int(row["plan_revision"]), take_id,
        )
        if snapshot is None or snapshot["status"] not in (
            session_plan.PREPARED_TAKE_STATUS_READY,
            session_plan.PREPARED_TAKE_STATUS_GENERATED,
        ):
            break
        try:
            resource_preparation.validate_authoring_prepared_evidence(
                int(row["session_id"]), int(row["plan_revision"]), take_id,
                row=snapshot, _predecessor_validation_cache=validation_cache,
            )
        except session_plan.AuthoringEvidenceInvalid:
            break
        validated_prefix.append(take_id)

    with db.transaction():
        # Validation above can take long enough for the parent lease or the
        # prepared evidence to change. Re-read the clock after acquiring the
        # write transaction, and revalidate every adopted snapshot against the
        # authoritative rows and dependencies while concurrent writers are
        # excluded. The evidence validator is read-only local/database work;
        # no remote authoring or generation call runs in this transaction.
        transaction_timestamp = _now_text(None)
        row = require_prepare_claim(claim, now_text=transaction_timestamp)
        current_plan_row = db.one(
            "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
            int(row["session_id"]),
        )
        if (
            current_plan_row is None
            or int(current_plan_row["plan_revision"]) != int(row["plan_revision"])
            or current_plan_row["plan_json"] != plan_row["plan_json"]
        ):
            raise FullAutoRunError(409, "plan_revision_stale", "The Full Auto plan changed before preparation.")
        current_validation_cache: dict = {}
        for take_id in validated_prefix:
            current_snapshot = session_plan._prepared_take_row(
                int(row["session_id"]), int(row["plan_revision"]), take_id,
            )
            if current_snapshot is None or current_snapshot["status"] not in (
                session_plan.PREPARED_TAKE_STATUS_READY,
                session_plan.PREPARED_TAKE_STATUS_GENERATED,
            ):
                raise FullAutoRunError(
                    409, "prepared_evidence_stale",
                    "Prepared Full Auto evidence changed before its checkpoint was saved.",
                )
            try:
                resource_preparation.validate_authoring_prepared_evidence(
                    int(row["session_id"]), int(row["plan_revision"]), take_id,
                    row=current_snapshot,
                    _predecessor_validation_cache=current_validation_cache,
                )
            except session_plan.AuthoringEvidenceInvalid as exc:
                raise FullAutoRunError(
                    409, "prepared_evidence_stale",
                    "Prepared Full Auto evidence changed before its checkpoint was saved.",
                ) from exc
        # The full evidence check can itself be lengthy for a large existing
        # prefix. Recheck ownership after it, immediately before persistence.
        commit_timestamp = _now_text(None)
        row = require_prepare_claim(claim, now_text=commit_timestamp)
        encoded = _canonical_json(targets)
        existing = db.one(
            "SELECT plan_revision, target_ids_json, completed_ids_json "
            "FROM full_auto_prepare_progress WHERE run_id = ?",
            claim.run_id,
        )
        if existing is None:
            db.run(
                """INSERT INTO full_auto_prepare_progress
                   (run_id, plan_revision, target_ids_json, completed_ids_json, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                claim.run_id, int(row["plan_revision"]), encoded,
                _canonical_json(validated_prefix), commit_timestamp,
            )
        elif (
            int(existing["plan_revision"]) != int(row["plan_revision"])
            or existing["target_ids_json"] != encoded
        ):
            raise FullAutoRunError(409, "prepare_checkpoint_changed", "The saved Full Auto target set changed.")
        else:
            completed = _prepare_json_list(
                existing["completed_ids_json"], field_name="progress",
            )
            if validated_prefix[:len(completed)] != completed:
                raise FullAutoRunError(
                    409, "prepared_evidence_stale",
                    "Previously completed Full Auto evidence is no longer current.",
                )
            if len(validated_prefix) > len(completed):
                db.run(
                    "UPDATE full_auto_prepare_progress SET completed_ids_json = ?, updated_at = ? "
                    "WHERE run_id = ?",
                    _canonical_json(validated_prefix), commit_timestamp, claim.run_id,
                )
        return _read_prepare_progress(claim.run_id)


def register_prepare_child(
    claim: RunClaim,
    operation_id: str,
    target_ids: list[str],
) -> dict:
    """Bind one durable authoring operation to the next ordered <=20 targets."""
    if not isinstance(operation_id, str) or not operation_id:
        raise FullAutoRunError(422, "prepare_child_invalid", "A child operation identity is required.")
    if type(target_ids) is not list or not target_ids or len(target_ids) > 20:
        raise FullAutoRunError(422, "prepare_child_invalid", "A child operation must own one to twenty targets.")
    with db.transaction():
        row = require_prepare_claim(claim, now_text=_now_text(None))
        progress = _read_prepare_progress(claim.run_id)
        existing = db.one(
            "SELECT batch_index, target_ids_json FROM full_auto_prepare_child "
            "WHERE run_id = ? AND operation_id = ?",
            claim.run_id, operation_id,
        )
        encoded = _canonical_json(target_ids)
        if existing is not None:
            if existing["target_ids_json"] != encoded:
                raise FullAutoRunError(409, "prepare_child_conflict", "The child operation targets changed.")
            return progress
        expected = progress["remaining_take_ids"][:20]
        if target_ids != expected:
            raise FullAutoRunError(409, "prepare_child_order_stale", "The child targets do not match the next ordered plan batch.")
        operation = db.one(
            "SELECT session_id, plan_revision, kind, full_auto_run_id, "
            "full_auto_run_epoch, full_auto_run_fencing_token FROM authoring_operation "
            "WHERE operation_id = ?",
            operation_id,
        )
        if (
            operation is None
            or int(operation["session_id"]) != int(row["session_id"])
            or int(operation["plan_revision"]) != int(row["plan_revision"])
            or operation["kind"] != "prepare_takes"
            or operation["full_auto_run_id"] != claim.run_id
            or operation["full_auto_run_epoch"] != claim.epoch
            or operation["full_auto_run_fencing_token"] != claim.fencing_token
        ):
            raise FullAutoRunError(409, "prepare_child_owner_invalid", "The authoring operation is not owned by this Full Auto run.")
        next_index = len(progress["children"])
        timestamp = _now_text(None)
        row = require_prepare_claim(claim, now_text=timestamp)
        db.run(
            """INSERT INTO full_auto_prepare_child
               (run_id, batch_index, operation_id, target_ids_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            claim.run_id, next_index, operation_id, encoded, timestamp,
        )
        return _read_prepare_progress(claim.run_id)


def checkpoint_prepare_child(
    claim: RunClaim,
    operation_id: str,
    completed_take_ids: list[str] | None = None,
) -> dict:
    """Persist an ordered prefix read back from the fenced authoring child row."""
    from backend import resource_preparation
    from backend import session_plan

    if completed_take_ids is not None and type(completed_take_ids) is not list:
        raise FullAutoRunError(422, "prepare_progress_invalid", "Child preparation progress is invalid.")
    with db.transaction():
        row = require_prepare_claim(claim, now_text=_now_text(None))
        progress = _read_prepare_progress(claim.run_id)
        child = next((item for item in progress["children"] if item["operation_id"] == operation_id), None)
        if child is None:
            raise FullAutoRunError(409, "prepare_child_missing", "The authoring child is not registered to this run.")
        operation = db.one(
            "SELECT session_id, plan_revision, kind, state, requested_json, completed_json, "
            "full_auto_run_id, full_auto_run_epoch, full_auto_run_fencing_token "
            "FROM authoring_operation WHERE operation_id = ?",
            operation_id,
        )
        if (
            operation is None
            or int(operation["session_id"]) != int(row["session_id"])
            or int(operation["plan_revision"]) != int(row["plan_revision"])
            or operation["kind"] != "prepare_takes"
            or operation["full_auto_run_id"] != claim.run_id
            or operation["full_auto_run_epoch"] != claim.epoch
            or operation["full_auto_run_fencing_token"] != claim.fencing_token
        ):
            raise FullAutoRunError(409, "prepare_child_owner_invalid", "The authoring operation is not owned by this Full Auto run.")
        persisted_targets = _prepare_json_list(
            operation["requested_json"], field_name="child targets",
        )
        persisted_completed = _prepare_json_list(
            operation["completed_json"], field_name="child progress",
        )
        if (
            operation["state"] not in {"succeeded", "failed", "cancelled", "expired"}
            or persisted_targets != child["target_ids"]
            or (completed_take_ids is not None and completed_take_ids != persisted_completed)
        ):
            raise FullAutoRunError(
                409, "prepare_progress_invalid",
                "The child operation progress is not a persisted terminal result.",
            )
        prior = progress["completed_take_ids"]
        expected_prior = progress["target_ids"][:len(prior)]
        expected_child = progress["target_ids"][len(prior):len(prior) + len(child["target_ids"])]
        if prior != expected_prior or child["target_ids"] != expected_child:
            raise FullAutoRunError(409, "prepare_progress_stale", "The authoring child no longer matches ordered run progress.")
        if persisted_completed != child["target_ids"][:len(persisted_completed)]:
            raise FullAutoRunError(409, "prepare_progress_invalid", "Child progress must be an ordered target prefix.")

        # A terminal authoring row is not sufficient evidence to advance the
        # parent checkpoint. Revalidate only snapshots newly claimed by this
        # child, under the same persistence transaction, against their current
        # authoritative inputs. A fresh cache is deliberately scoped to this
        # bounded child; it never spans batches or coordinator calls.
        validation_cache: dict = {}
        for take_id in persisted_completed:
            snapshot = session_plan._prepared_take_row(
                int(row["session_id"]), int(row["plan_revision"]), take_id,
            )
            if snapshot is None or snapshot["status"] not in (
                session_plan.PREPARED_TAKE_STATUS_READY,
                session_plan.PREPARED_TAKE_STATUS_GENERATED,
            ):
                raise FullAutoRunError(
                    409, "prepared_evidence_stale",
                    "A completed child snapshot is no longer current.",
                )
            try:
                resource_preparation.validate_authoring_prepared_evidence(
                    int(row["session_id"]), int(row["plan_revision"]), take_id,
                    row=snapshot,
                    _predecessor_validation_cache=validation_cache,
                )
            except session_plan.AuthoringEvidenceInvalid as exc:
                raise FullAutoRunError(
                    409, "prepared_evidence_stale",
                    "A completed child snapshot is no longer current.",
                ) from exc

        completed = prior + persisted_completed
        # Acquiring the transaction may wait on SQLite's write lock, and
        # evidence validation can take time. Check the live owner once more
        # immediately before the checkpoint write so elapsed leases fail closed.
        timestamp = _now_text(None)
        row = require_prepare_claim(claim, now_text=timestamp)
        db.run(
            "UPDATE full_auto_prepare_progress SET completed_ids_json = ?, updated_at = ? WHERE run_id = ?",
            _canonical_json(completed), timestamp, claim.run_id,
        )
        return _read_prepare_progress(claim.run_id)


def _valid_phase_step(current: str, requested: str) -> bool:
    if requested == current:
        return True
    try:
        return _MAIN_PHASES.index(requested) == _MAIN_PHASES.index(current) + 1
    except ValueError:
        return False


def advance_run(
    claim: RunClaim,
    to_state: Literal["active", "waiting_authoring", "waiting_runner", "blocked"],
    *,
    phase: str | None = None,
    blocker_code: str | None = None,
    now_text: str | None = None,
) -> dict:
    """Apply only the nonterminal coordinator transitions from the state table."""
    timestamp = _now_text(now_text)
    if to_state not in {"active", "waiting_authoring", "waiting_runner", "blocked"}:
        raise FullAutoRunError(409, "run_transition_refused", "The requested run transition is not allowed.")
    if phase is not None and phase not in _MAIN_PHASES:
        raise FullAutoRunError(409, "run_phase_invalid", "The requested Full Auto phase is invalid.")
    if blocker_code is not None and not _SAFE_CODE.fullmatch(blocker_code):
        raise FullAutoRunError(422, "invalid_blocker_code", "The Full Auto blocker code is invalid.")

    with db.transaction():
        row = _require_claim(claim, now_text=timestamp)
        authority_issue = _current_authority_issue(row, timestamp)
        if authority_issue is not None and to_state != "blocked":
            raise _authority_error(authority_issue)
        if authority_issue is not None and blocker_code != authority_issue:
            raise _authority_error(authority_issue)
        source = row["state"]
        allowed = {
            "active": {"active", "waiting_authoring", "waiting_runner", "blocked"},
            "waiting_authoring": {"active", "blocked"},
            "waiting_runner": {"active", "blocked"},
        }
        if to_state not in allowed.get(source, set()):
            raise FullAutoRunError(409, "run_transition_refused", "The requested run transition is not allowed.")
        next_phase = phase or row["phase"]
        if source == "active" and to_state == "active":
            if not _valid_phase_step(row["phase"], next_phase) or next_phase == row["phase"]:
                raise FullAutoRunError(409, "run_phase_transition_refused", "The requested run phase transition is not allowed.")
        elif next_phase != row["phase"]:
            raise FullAutoRunError(409, "run_phase_transition_refused", "A waiting or blocked transition cannot change phase.")
        if to_state == "blocked":
            if blocker_code is None:
                raise FullAutoRunError(422, "blocker_required", "A blocked run requires a stable reason code.")
        elif blocker_code is not None:
            raise FullAutoRunError(422, "blocker_not_allowed", "Only a blocked run can carry a blocker code.")

        release = to_state in {"waiting_authoring", "waiting_runner", "blocked"}
        fence_delta = 1 if release else 0
        lease = None if release else row["lease_expires_at"]
        lease_started = None if release else row["lease_started_at"]
        db.run(
            """UPDATE full_auto_run
               SET state = ?, phase = ?, revision = revision + 1,
                   fencing_token = fencing_token + ?, lease_expires_at = ?,
                   lease_started_at = ?,
                   blocker_code = ?, evidence_json = ?, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?""",
            to_state, next_phase, fence_delta, lease, lease_started, blocker_code,
            _canonical_json({"kind": "state_transition", "from": source, "to": to_state}),
            timestamp, claim.run_id, claim.epoch, claim.fencing_token,
        )
        return _view(_require_run(claim.run_id))


def complete_run(
    claim: RunClaim,
    evidence: CompletionEvidence,
    *,
    now_text: str | None = None,
) -> dict:
    """Persist a terminal result only after backend evidence proves settlement."""
    timestamp = _now_text(now_text)
    if not isinstance(evidence, CompletionEvidence):
        raise FullAutoRunError(409, "settlement_evidence_required", "Backend settlement evidence is required.")
    if type(evidence.unresolved_work) is not int or evidence.unresolved_work != 0:
        raise FullAutoRunError(
            409, "run_work_unsettled", "The Full Auto run still has unresolved owned work.",
        )
    states = {
        "all_succeeded": "succeeded",
        "mixed": "completed_with_errors",
        "all_shots_failed": "failed",
        "internal_failure": "failed",
    }
    target = states.get(evidence.outcome)
    if target is None:
        raise FullAutoRunError(409, "run_work_unsettled", "The Full Auto outcome is unknown.")

    with db.transaction():
        row = _require_claim(claim, now_text=timestamp)
        if evidence.outcome == "internal_failure":
            if row["state"] not in {"active", "waiting_authoring", "waiting_runner"}:
                raise FullAutoRunError(409, "run_transition_refused", "The internal failure cannot be settled from this state.")
        elif row["state"] != "active" or row["phase"] != "execute":
            raise FullAutoRunError(409, "run_transition_refused", "Execution results require the active execute phase.")
        db.run(
            """UPDATE full_auto_run
               SET state = ?, revision = revision + 1,
                   fencing_token = fencing_token + 1, lease_expires_at = NULL,
                   lease_started_at = NULL, cancel_intent = 0,
                   cancel_intent_at = NULL, blocker_code = NULL, resume_phase = NULL,
                   evidence_json = ?, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?""",
            target,
            _canonical_json({
                "kind": "settled_completion", "outcome": evidence.outcome,
                "unresolved_work": evidence.unresolved_work,
            }),
            timestamp, claim.run_id, claim.epoch, claim.fencing_token,
        )
        return _view(_require_run(claim.run_id))


def _action_fields(
    action: Literal["cancel", "resume"],
    run_id: str,
    request_id: str,
    expected_run_revision: int,
    expected_plan_revision: int,
) -> tuple[dict, str]:
    if not isinstance(run_id, str) or not run_id:
        raise FullAutoRunError(422, "invalid_control_request", "run_id is required.")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
        raise FullAutoRunError(422, "invalid_control_request", "request_id is invalid.")
    body = {
        "action": action,
        "run_id": run_id,
        "expected_run_revision": _positive_int(expected_run_revision, "expected_run_revision"),
        "expected_plan_revision": _positive_int(expected_plan_revision, "expected_plan_revision"),
    }
    return body, _digest(body)


def _replay_action(
    run_id: str,
    request_id: str,
    request_digest: str,
) -> RunActionResult | None:
    row = db.one(
        "SELECT request_digest, response_json FROM full_auto_run_action_receipt "
        "WHERE run_id = ? AND request_id = ?",
        run_id, request_id,
    )
    if row is None:
        return None
    if not hmac.compare_digest(row["request_digest"], request_digest):
        raise FullAutoRunError(
            409, "idempotency_conflict", "request_id is already bound to different Full Auto control content.",
        )
    try:
        view = json.loads(row["response_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise FullAutoRunError(500, "run_receipt_invalid", "The saved Full Auto control receipt is invalid.") from exc
    if not isinstance(view, dict):
        raise FullAutoRunError(500, "run_receipt_invalid", "The saved Full Auto control receipt is invalid.")
    return RunActionResult(view=view, replayed=True)


def _action_view(row: dict, action: str, request_id: str) -> dict:
    return {**_view(row), "action": action, "request_id": request_id}


def _store_action(
    run_id: str,
    request_id: str,
    action: str,
    request_digest: str,
    row: dict,
    view: dict,
    timestamp: str,
) -> None:
    db.run(
        """INSERT INTO full_auto_run_action_receipt
           (run_id, request_id, action, request_digest, epoch, response_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        run_id, request_id, action, request_digest, row["epoch"],
        _canonical_json(view), timestamp,
    )


def _check_control_cas(row: dict, expected_run_revision: int, expected_plan_revision: int) -> None:
    if int(row["revision"]) != expected_run_revision:
        raise FullAutoRunError(409, "run_revision_stale", "The Full Auto run changed; reload its current state.")
    if int(row["plan_revision"]) != expected_plan_revision:
        raise FullAutoRunError(409, "plan_revision_stale", "The Full Auto run belongs to a different plan revision.")


def cancel_run(
    run_id: str,
    request_id: str,
    expected_run_revision: int,
    expected_plan_revision: int,
    *,
    work_pending: bool | None,
    now_text: str | None = None,
) -> RunActionResult:
    """Persist cancellation intent; callers supply backend-owned work evidence."""
    body, request_digest = _action_fields(
        "cancel", run_id, request_id, expected_run_revision, expected_plan_revision,
    )
    timestamp = _now_text(now_text)
    with db.transaction():
        replay = _replay_action(run_id, request_id, request_digest)
        if replay is not None:
            return replay
        row = _require_run(run_id)
        _check_control_cas(row, body["expected_run_revision"], body["expected_plan_revision"])
        if row["state"] in {"cancel_requested", "blocked"} and bool(row["cancel_intent"]):
            # Repeated Cancel records its response but never polls or dispatches.
            pass
        elif row["state"] in {"active", "waiting_authoring", "waiting_runner", "blocked"}:
            if type(work_pending) is not bool:
                raise FullAutoRunError(
                    409, "cancel_evidence_required", "Backend work-settlement evidence is required.",
                )
            old_phase = row["resume_phase"] or row["phase"]
            if work_pending:
                state, phase, resume_phase = "cancel_requested", "reconcile", old_phase
                blocker = None
            else:
                state = "cancelled"
                phase = row["phase"]
                resume_phase = old_phase if phase == "reconcile" else row["resume_phase"]
                blocker = None
            db.run(
                """UPDATE full_auto_run
                   SET state = ?, phase = ?, revision = revision + 1,
                       fencing_token = fencing_token + 1, lease_expires_at = NULL,
                       lease_started_at = NULL, cancel_intent = 1,
                       cancel_intent_at = ?, blocker_code = ?, resume_phase = ?,
                       evidence_json = ?, updated_at = ?
                   WHERE run_id = ? AND revision = ? AND fencing_token = ?""",
                state, phase, timestamp, blocker, resume_phase,
                _canonical_json({"kind": "cancel", "work_pending": work_pending}), timestamp,
                run_id, row["revision"], row["fencing_token"],
            )
            row = _require_run(run_id)
        else:
            raise FullAutoRunError(409, "run_not_cancellable", "This Full Auto run cannot be cancelled in its current state.")
        view = _action_view(row, "cancel", request_id)
        _store_action(run_id, request_id, "cancel", request_digest, row, view, timestamp)
        return RunActionResult(view=view)


def _resume_eligibility(row: dict, evidence: ResumeEligibility | None) -> str:
    if not isinstance(evidence, ResumeEligibility):
        raise FullAutoRunError(409, "resume_preflight_required", "Current Full Auto resume checks are required.")
    if type(evidence.current_plan_revision) is not int or evidence.current_plan_revision != int(row["plan_revision"]):
        raise FullAutoRunError(409, "plan_revision_stale", "The current plan changed; refresh consent before resuming.")
    if type(evidence.unresolved_work) is not int or evidence.unresolved_work != 0:
        raise FullAutoRunError(409, "run_work_unsettled", "The Full Auto run still has unresolved owned work.")
    if type(evidence.planning_enabled) is not bool or not evidence.planning_enabled:
        raise FullAutoRunError(503, "resource_planning_disabled", "Full Auto is disabled by configuration.")
    if type(evidence.authorization_valid) is not bool or not evidence.authorization_valid:
        raise FullAutoRunError(409, "authorization_expired", "The Full Auto authorization is no longer valid.")
    if type(evidence.policy_supported) is not bool or not evidence.policy_supported:
        raise FullAutoRunError(409, "policy_changed", "The recorded Full Auto policy is not supported.")
    if type(evidence.scope_matches) is not bool or not evidence.scope_matches:
        raise FullAutoRunError(409, "authorization_scope_changed", "The authorized Full Auto inputs changed.")
    if type(evidence.budget_available) is not bool or not evidence.budget_available:
        raise FullAutoRunError(409, "budget_exhausted", "The authorized Full Auto budget is exhausted.")
    if type(evidence.session_exclusive) is not bool or not evidence.session_exclusive:
        raise FullAutoRunError(409, "full_auto_run_conflict", "Another operation owns this session.")
    if evidence.next_state not in {"active", "waiting_authoring", "waiting_runner"}:
        raise FullAutoRunError(422, "invalid_resume_state", "The requested Full Auto resume state is invalid.")
    return evidence.next_state


def resume_run(
    run_id: str,
    request_id: str,
    expected_run_revision: int,
    expected_plan_revision: int,
    *,
    eligibility: ResumeEligibility | None,
    now_text: str | None = None,
) -> RunActionResult:
    """Resume a valid epoch or acquire a read-only cancel-reconciliation lease."""
    body, request_digest = _action_fields(
        "resume", run_id, request_id, expected_run_revision, expected_plan_revision,
    )
    timestamp = _now_text(now_text)
    with db.transaction():
        replay = _replay_action(run_id, request_id, request_digest)
        if replay is not None:
            return replay
        row = _require_run(run_id)
        _check_control_cas(row, body["expected_run_revision"], body["expected_plan_revision"])

        if row["state"] == "blocked" and bool(row["cancel_intent"]):
            deadline = run_lease_deadline(timestamp)
            if row["lease_expires_at"] is not None and _timestamp(
                row["lease_expires_at"], field_name="run lease",
            ) > _timestamp(timestamp, field_name="current time"):
                raise FullAutoRunError(409, "run_already_claimed", "Cancellation reconciliation already has an owner.")
            db.run(
                """UPDATE full_auto_run
                   SET revision = revision + 1, fencing_token = fencing_token + 1,
                       lease_expires_at = ?, lease_started_at = ?, updated_at = ?
                   WHERE run_id = ? AND revision = ? AND fencing_token = ?""",
                deadline, timestamp, timestamp, run_id, row["revision"], row["fencing_token"],
            )
            row = _require_run(run_id)
            claim = _make_claim(row, True)
        else:
            if row["state"] not in {"blocked", "cancelled", "failed", "completed_with_errors"}:
                raise FullAutoRunError(409, "run_not_resumable", "The Full Auto run cannot be resumed in its current state.")
            if row["state"] == "blocked" and row["lease_expires_at"] is not None:
                if _timestamp(row["lease_expires_at"], field_name="run lease") > _timestamp(timestamp, field_name="current time"):
                    raise FullAutoRunError(409, "run_already_claimed", "The Full Auto run already has an owner.")
            if row["state"] == "succeeded":
                raise FullAutoRunError(409, "run_not_resumable", "A successful Full Auto run is not resumable.")
            authority_issue = _current_authority_issue(row, timestamp)
            if authority_issue is not None:
                raise _authority_error(authority_issue)
            next_state = _resume_eligibility(row, eligibility)
            is_new_epoch = row["state"] in _TERMINAL
            phase = row["resume_phase"] or row["phase"]
            if phase not in _MAIN_PHASES:
                raise FullAutoRunError(409, "run_reconciliation_required", "The Full Auto run phase must be reconciled before resuming.")
            if row["lease_expires_at"] is not None and _timestamp(
                row["lease_expires_at"], field_name="run lease",
            ) > _timestamp(timestamp, field_name="current time"):
                raise FullAutoRunError(409, "run_already_claimed", "The Full Auto run already has an owner.")
            deadline = run_lease_deadline(timestamp)
            db.run(
                """UPDATE full_auto_run
                   SET state = ?, phase = ?, revision = revision + 1,
                       epoch = epoch + ?, fencing_token = fencing_token + 1,
                       lease_expires_at = ?, lease_started_at = ?,
                       cancel_intent = 0, cancel_intent_at = NULL, blocker_code = NULL,
                       resume_phase = NULL,
                       evidence_json = ?, updated_at = ?
                   WHERE run_id = ? AND revision = ? AND fencing_token = ?""",
                next_state, phase, 1 if is_new_epoch else 0, deadline, timestamp,
                _canonical_json({"kind": "resume", "new_epoch": is_new_epoch}),
                timestamp, run_id, row["revision"], row["fencing_token"],
            )
            row = _require_run(run_id)
            claim = _make_claim(row, False)

        view = _action_view(row, "resume", request_id)
        _store_action(run_id, request_id, "resume", request_digest, row, view, timestamp)
        return RunActionResult(view=view, claim=claim)


def reconcile_cancellation(
    claim: RunClaim,
    evidence: CancellationEvidence,
    *,
    now_text: str | None = None,
) -> dict:
    """Apply observed cancellation facts without performing any remote I/O."""
    timestamp = _now_text(now_text)
    if not isinstance(evidence, CancellationEvidence):
        raise FullAutoRunError(409, "reconciliation_evidence_required", "Backend cancellation evidence is required.")
    if evidence.outcome not in {"running", "settled", "unknown"}:
        raise FullAutoRunError(422, "reconciliation_evidence_invalid", "The cancellation evidence is invalid.")
    if type(evidence.unresolved_work) is not int or evidence.unresolved_work < 0:
        raise FullAutoRunError(422, "reconciliation_evidence_invalid", "The cancellation evidence is invalid.")
    observed = _timestamp(evidence.observed_at, field_name="reconciliation observation")
    if observed > _timestamp(timestamp, field_name="current time"):
        raise FullAutoRunError(422, "reconciliation_evidence_invalid", "The cancellation evidence is from the future.")
    if evidence.outcome == "settled" and evidence.unresolved_work != 0:
        raise FullAutoRunError(409, "run_work_unsettled", "Cancellation cannot settle while owned work remains unresolved.")
    if evidence.outcome == "running" and evidence.unresolved_work == 0:
        raise FullAutoRunError(409, "reconciliation_evidence_invalid", "A running result requires unresolved owned work.")
    if evidence.outcome == "unknown" and evidence.unresolved_work == 0:
        raise FullAutoRunError(409, "reconciliation_evidence_invalid", "An unknown result must retain unresolved work evidence.")
    blocker = evidence.blocker_code
    if evidence.outcome == "unknown":
        blocker = blocker or "execution_outcome_unknown"
        if not _SAFE_CODE.fullmatch(blocker):
            raise FullAutoRunError(422, "reconciliation_evidence_invalid", "The cancellation evidence is invalid.")
    elif blocker is not None:
        raise FullAutoRunError(422, "reconciliation_evidence_invalid", "The cancellation evidence is invalid.")
    ids = (*evidence.attempt_ids, *evidence.shot_ids)
    if any(not isinstance(value, str) or not value or len(value) > 200 for value in ids):
        raise FullAutoRunError(422, "reconciliation_evidence_invalid", "The cancellation evidence is invalid.")

    with db.transaction():
        row = _require_claim(claim, now_text=timestamp, reconciliation=True)
        if not bool(row["cancel_intent"]) or row["state"] not in {"blocked", "cancel_requested"}:
            raise FullAutoRunError(409, "cancel_reconciliation_required", "The run has no pending cancellation to reconcile.")
        cancel_boundary = row["cancel_intent_at"]
        claim_boundary = row["lease_started_at"]
        if cancel_boundary is None or claim_boundary is None:
            raise FullAutoRunError(409, "reconciliation_evidence_stale", "Cancellation evidence has no current durable boundary.")
        if observed < _timestamp(cancel_boundary, field_name="cancellation intent") or observed < _timestamp(
            claim_boundary, field_name="reconciliation claim",
        ):
            raise FullAutoRunError(409, "reconciliation_evidence_stale", "Cancellation evidence predates the current cancellation or reconciliation claim.")
        if evidence.outcome == "settled":
            state, lease, fence_delta, reason = "cancelled", None, 1, None
        elif evidence.outcome == "running":
            state, lease, fence_delta, reason = "cancel_requested", row["lease_expires_at"], 0, None
        else:
            state, lease, fence_delta, reason = "blocked", None, 1, blocker
        db.run(
            """UPDATE full_auto_run
               SET state = ?, phase = 'reconcile', revision = revision + 1,
                   fencing_token = fencing_token + ?, lease_expires_at = ?,
                   lease_started_at = ?,
                   blocker_code = ?, evidence_json = ?, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?""",
            state, fence_delta, lease,
            row["lease_started_at"] if state == "cancel_requested" else None, reason,
            _canonical_json({
                "kind": "cancel_reconciliation", "outcome": evidence.outcome,
                "observed_at": evidence.observed_at,
                "unresolved_work": evidence.unresolved_work,
                "attempt_ids": list(evidence.attempt_ids),
                "shot_ids": list(evidence.shot_ids),
            }),
            timestamp, claim.run_id, claim.epoch, claim.fencing_token,
        )
        return _view(_require_run(claim.run_id))


def reconcile_expired_lease(
    claim: RunClaim,
    evidence: LeaseReconciliationEvidence,
    *,
    now_text: str | None = None,
) -> tuple[dict, RunClaim | None]:
    """Restore a saved phase only when backend checkpoints prove safe continuation."""
    timestamp = _now_text(now_text)
    if not isinstance(evidence, LeaseReconciliationEvidence):
        raise FullAutoRunError(409, "reconciliation_evidence_required", "Backend lease-reconciliation evidence is required.")
    if type(evidence.unresolved_work) is not int or evidence.unresolved_work < 0:
        raise FullAutoRunError(422, "reconciliation_evidence_invalid", "The lease-reconciliation evidence is invalid.")
    with db.transaction():
        row = _require_claim(claim, now_text=timestamp, reconciliation=True)
        if row["cancel_intent"] or row["state"] == "cancel_requested":
            raise FullAutoRunError(409, "cancel_reconciliation_required", "Pending cancellation requires read-only cancellation reconciliation.")
        if row["resume_phase"] not in _MAIN_PHASES:
            raise FullAutoRunError(409, "run_reconciliation_required", "The saved Full Auto phase is unavailable.")
        if evidence.outcome == "safe_to_continue" and evidence.unresolved_work == 0:
            authority_issue = _current_authority_issue(row, timestamp)
            if authority_issue is None:
                phase, state, blocker = row["resume_phase"], row["state"], None
                lease = row["lease_expires_at"]
                fence_delta = 0
            else:
                phase, state, blocker = "reconcile", "blocked", authority_issue
                lease, fence_delta = None, 1
        elif evidence.outcome == "unknown" and evidence.unresolved_work > 0:
            authority_issue = None
            blocker = evidence.blocker_code or "execution_outcome_unknown"
            if not _SAFE_CODE.fullmatch(blocker):
                raise FullAutoRunError(422, "reconciliation_evidence_invalid", "The lease-reconciliation evidence is invalid.")
            phase, state, lease, fence_delta = "reconcile", "blocked", None, 1
        else:
            raise FullAutoRunError(409, "reconciliation_evidence_invalid", "The lease-reconciliation evidence is inconsistent.")
        lease_started = row["lease_started_at"] if state != "blocked" else None
        db.run(
            """UPDATE full_auto_run
               SET state = ?, phase = ?, revision = revision + 1,
                   fencing_token = fencing_token + ?, lease_expires_at = ?,
                   lease_started_at = ?,
                   blocker_code = ?, resume_phase = ?, evidence_json = ?, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?""",
            state, phase, fence_delta, lease, lease_started, blocker,
            None if state != "blocked" else row["resume_phase"],
            _canonical_json({
                "kind": "lease_reconciliation", "outcome": evidence.outcome,
                "unresolved_work": evidence.unresolved_work,
                "authority_issue": authority_issue,
            }),
            timestamp, claim.run_id, claim.epoch, claim.fencing_token,
        )
        updated = _require_run(claim.run_id)
        next_claim = None
        if evidence.outcome == "safe_to_continue" and authority_issue is None:
            next_claim = _make_claim(updated, False)
        return _view(updated), next_claim
