"""Durable state and control receipts for Full Auto runs.

This module owns only the local run state machine. It never performs assistant
or ComfyUI calls; settlement and reconciliation inputs must come from backend
services that actually observed those operations.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import uuid
from typing import Literal

import db
from backend import resource_planning


RUN_LEASE = timedelta(seconds=60)
RUN_HEARTBEAT_INTERVAL = timedelta(seconds=15)
RUNNER_LEASE = timedelta(seconds=60)
RUNNER_HEARTBEAT_INTERVAL = timedelta(seconds=15)
ASSISTANT_SLOT_LEASE = timedelta(minutes=10)
ASSISTANT_SLOT_HEARTBEAT_INTERVAL = timedelta(seconds=15)
# Keep aligned with enhance.TIMEOUT; only Full Auto dispatches use this bound.
ASSISTANT_DISPATCH_TIMEOUT = timedelta(seconds=300)
# Persisted timestamps have one-second precision, so quarantine rounds safely up.
ASSISTANT_DISPATCH_TIMESTAMP_MARGIN = timedelta(seconds=1)
COMFY_PROBE_DELAYS = (5, 15, 30)
_RUN_CLAIM_KEY = secrets.token_bytes(32)
_RUNNER_CLAIM_KEY = secrets.token_bytes(32)
_MANUAL_EDIT_CLAIM_KEY = secrets.token_bytes(32)
_ASSISTANT_SLOT_CLAIM_KEY = secrets.token_bytes(32)
_UNKNOWN_RETRY_EVIDENCE_KEY = secrets.token_bytes(32)
_LOCAL_ASSISTANT_DISPATCHES: set["AssistantDispatchFence"] = set()
_LOCAL_ASSISTANT_DISPATCHES_LOCK = threading.Lock()
_SUPPORTED_AUTHORIZATION_VERSION = 1
_SUPPORTED_POLICY_VERSION = "resource-full-auto-v1"
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PHASES = ("prepare", "validate", "approve", "submit", "execute", "reconcile")
_MAIN_PHASES = _PHASES[:-1]
_TERMINAL = frozenset({"cancelled", "failed", "succeeded", "completed_with_errors"})
_NONTERMINAL = frozenset({
    "active", "waiting_authoring", "waiting_runner", "blocked", "cancel_requested",
})
_PUBLIC_BLOCKER_CODES = frozenset({
    "authorization_expired", "authorization_scope_changed", "plan_revision_stale",
    "resource_planning_disabled", "manual_edit_pause", "execution_outcome_unknown",
    "run_work_unsettled", "policy_changed", "authorization_invalid", "budget_exhausted",
    "preparation_failed", "duplicate_review_required", "full_auto_review_incomplete",
    "duplicate_review_unavailable", "duplicate_review_not_required", "duplicate_review_stale",
    "full_auto_authorization_missing", "full_auto_authorization_invalid",
    "full_auto_plan_invalid", "full_auto_target_not_ready", "full_auto_targets_invalid",
    "linked_snapshot_invalid", "workflow_changed", "prepared_evidence_stale",
    "dependency_missing",
    "duplicate_evidence_missing", "duplicate_evidence_stale", "review_conflict",
    "stale_adaptation", "unresolved_placeholders", "full_auto_approval_stale",
    "full_auto_submission_incomplete", "submission_order_invalid", "submission_result_invalid",
    "submission_target_stale", "full_auto_scope_unavailable",
    "full_auto_continuity_requires_new_session", "dependency_changed", "correctable_input",
    "source_changed", "comfy_unavailable", "verification_fence",
    "assistant_unavailable", "assistant_transport_error", "assistant_rate_limited",
    "assistant_refused", "writer_output_invalid", "provider_changed",
})
_PUBLIC_RUN_VIEW_FIELDS = (
    "run_id", "session_id", "revision", "epoch", "plan_revision", "state", "phase",
    "cancel_intent", "blocker_code", "created_at", "updated_at",
)
_PUBLIC_POLICY_VERSION = re.compile(r"^resource-full-auto-v[1-9][0-9]{0,2}$")


class FullAutoRunError(Exception):
    """A stable domain failure; route adapters may map ``status_code`` to HTTP."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        current_run: dict | None = None,
        affected_sessions: list[int] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.current_run = current_run
        self.affected_sessions = affected_sessions


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
class RunnerClaim:
    """Backend-only capability for the shared serial GPU runner."""

    owner_kind: Literal["manual", "full_auto"]
    session_id: int
    run_id: str | None
    owner_nonce: str
    fencing_token: int
    lease_started_at: str
    lease_expires_at: str
    _signature: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class AssistantSlotClaim:
    """Backend-only FIFO slot capability for one Full Auto take."""

    run_id: str
    session_id: int
    take_id: str
    operation_id: str
    generation: int
    epoch: int
    run_fencing_token: int
    operation_fencing_token: int
    lease_expires_at: str
    _signature: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class AssistantDispatchFence:
    reservation_id: int
    database_identity: tuple[str | int, ...]


class RunClaimHeartbeat:
    """Renew one coordinator claim independently while its owner awaits I/O."""

    def __init__(self, claim: RunClaim):
        self.claim = claim
        self.error: FullAutoRunError | None = None
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> RunClaimHeartbeat:
        self._task = asyncio.create_task(self._renew())
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        if self._task is not None:
            if not self._task.done():
                self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.error is not None:
            raise self.error
        return False

    def check(self) -> RunClaim:
        if self.error is not None:
            raise self.error
        return self.claim

    @property
    def run_claim(self) -> RunClaim:
        """Expose the owner kind to shared runner code without probing it."""
        return self.claim

    def renew_now(self) -> RunClaim:
        """Refresh the shared capability at a foreground dispatch boundary."""
        self.check()
        try:
            self.claim = renew_run_claim(self.claim)
        except FullAutoRunError as exc:
            self.error = exc
            raise
        except Exception as exc:  # noqa: BLE001 - a failed renewal fences the owner
            self.error = FullAutoRunError(
                503, "run_heartbeat_failed", "The Full Auto run lease could not be renewed.",
            )
            raise self.error from exc
        return self.claim

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(RUN_HEARTBEAT_INTERVAL.total_seconds())
            try:
                self.renew_now()
            except FullAutoRunError:
                return


class RunnerClaimHeartbeat:
    """Renew the global runner lease and optional Full Auto owner during I/O."""

    def __init__(
        self, claim: RunnerClaim, run_claim: RunClaim | None = None,
        *, cancellation_recovery: bool = False,
    ):
        self.claim = claim
        self.run_claim = run_claim
        self.cancellation_recovery = cancellation_recovery
        self.error: FullAutoRunError | None = None
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> RunnerClaimHeartbeat:
        self._task = asyncio.create_task(self._renew())
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        if self._task is not None:
            if not self._task.done():
                self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.error is not None:
            raise self.error
        return False

    def check(self) -> RunClaim | None:
        if self.error is not None:
            raise self.error
        _require_runner_claim(self.claim, now_text=db.now())
        if self.run_claim is not None:
            if self.cancellation_recovery:
                require_cancellation_runner_claim(
                    self.run_claim.session_id, self.run_claim, self.claim,
                )
            else:
                _require_claim(
                    self.run_claim, now_text=db.now(),
                    reconciliation=self.run_claim.requires_reconciliation,
                )
        return self.run_claim

    def renew_now(self) -> RunClaim | None:
        if self.error is not None:
            raise self.error
        try:
            self.claim = renew_runner_claim(self.claim)
            if self.run_claim is not None:
                self.run_claim = renew_run_claim(self.run_claim)
        except FullAutoRunError as exc:
            self.error = exc
            raise
        except Exception as exc:  # noqa: BLE001 - a failed renewal fences the runner
            self.error = FullAutoRunError(
                503, "runner_heartbeat_failed", "The runner lease could not be renewed.",
            )
            raise self.error from exc
        return self.run_claim

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(RUNNER_HEARTBEAT_INTERVAL.total_seconds())
            try:
                self.renew_now()
            except FullAutoRunError:
                return


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
    schedule_cancellation_reconciliation: bool = False


@dataclass(frozen=True)
class UnknownRetryEvidence:
    """Server-observed current queue evidence for one explicitly risked attempt."""

    attempt_id: str
    queue_snapshot_digest: str
    capability_evidence_digest: str
    observed_at: str
    run_id: str = ""
    session_id: int = 0
    shot_id: int = 0
    request_id: str = ""
    expected_run_revision: int = 0
    expected_plan_revision: int = 0
    acceptance_epoch: int = 0
    scope_digest: str = ""
    comparison_issued_at: str = ""
    attempt_identity_digest: str = ""
    _signature: str = field(default="", repr=False, compare=False)


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


def assistant_retry_after_deadline(value: str | None, *, now_text: str | None = None) -> str | None:
    """Return a canonical absolute deadline for a valid Retry-After value."""
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    timestamp = _now_text(now_text)
    now = _timestamp(timestamp, field_name="current time")
    try:
        if value.isascii() and value.isdecimal():
            deadline = now + timedelta(seconds=int(value))
        else:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None or deadline.utcoffset() is None:
                return None
            deadline = deadline.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None
    if deadline <= now:
        return None
    return deadline.isoformat(timespec="seconds")


def run_lease_deadline(started_at: str | None = None) -> str:
    start_text = _now_text(started_at)
    deadline = _timestamp(start_text, field_name="current time") + RUN_LEASE
    return deadline.isoformat(timespec="seconds")


def _digest(value: dict) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _unknown_retry_attempt_identity(attempt: dict) -> str:
    fields = (
        "attempt_id", "run_id", "session_id", "shot_id", "attempt_number",
        "run_epoch", "run_fencing_token", "runner_fencing_token", "seed",
        "filename_prefix", "graph_digest", "client_prompt_id", "client_id",
        "capability_evidence_json", "dispatch_state", "completion_state",
    )
    return _digest({name: attempt.get(name) for name in fields})


def _unknown_retry_evidence_payload(evidence: UnknownRetryEvidence) -> bytes:
    return _canonical_json({
        "purpose": "full-auto-unknown-retry-queue-proof-v1",
        "attempt_id": evidence.attempt_id,
        "queue_snapshot_digest": evidence.queue_snapshot_digest,
        "capability_evidence_digest": evidence.capability_evidence_digest,
        "observed_at": evidence.observed_at,
        "run_id": evidence.run_id,
        "session_id": evidence.session_id,
        "shot_id": evidence.shot_id,
        "request_id": evidence.request_id,
        "expected_run_revision": evidence.expected_run_revision,
        "expected_plan_revision": evidence.expected_plan_revision,
        "acceptance_epoch": evidence.acceptance_epoch,
        "scope_digest": evidence.scope_digest,
        "comparison_issued_at": evidence.comparison_issued_at,
        "attempt_identity_digest": evidence.attempt_identity_digest,
    }).encode("utf-8")


def _make_unknown_retry_evidence(
    attempt: dict,
    *,
    queue_snapshot_digest: str,
    capability_evidence_digest: str,
    observed_at: str,
    request_id: str,
    expected_run_revision: int,
    expected_plan_revision: int,
    scope_digest: str,
    comparison_issued_at: str,
) -> UnknownRetryEvidence:
    """Seal one queue observation to its backend-loaded run and retry identities."""
    unsigned = UnknownRetryEvidence(
        attempt_id=attempt["attempt_id"],
        queue_snapshot_digest=queue_snapshot_digest,
        capability_evidence_digest=capability_evidence_digest,
        observed_at=observed_at,
        run_id=attempt["run_id"],
        session_id=int(attempt["session_id"]),
        shot_id=int(attempt["shot_id"]),
        request_id=request_id,
        expected_run_revision=expected_run_revision,
        expected_plan_revision=expected_plan_revision,
        acceptance_epoch=int(attempt["acceptance_epoch"]),
        scope_digest=scope_digest,
        comparison_issued_at=comparison_issued_at,
        attempt_identity_digest=_unknown_retry_attempt_identity(attempt),
    )
    signature = hmac.new(
        _UNKNOWN_RETRY_EVIDENCE_KEY,
        _unknown_retry_evidence_payload(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return UnknownRetryEvidence(**{**unsigned.__dict__, "_signature": signature})


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


def _check_run_session_identity(run_id: str, session_id: int | None) -> None:
    identity = db.one("SELECT session_id FROM full_auto_run WHERE run_id = ?", run_id)
    if identity is None or session_id is not None and int(identity["session_id"]) != session_id:
        raise FullAutoRunError(404, "full_auto_run_not_found", "Full Auto run not found.")


def _authorization_digest(authorization: dict, grant: dict) -> str:
    return _digest({
        "authorization": authorization,
        "scope_digest": grant["scope_digest"],
        "dependencies_digest": grant["dependencies_digest"],
        "plan_digest": grant["plan_digest"],
        "scope_issued_at": grant["scope_issued_at"],
        "authorization_expires_at": grant["authorization_expires_at"],
        "sequence": grant["sequence"],
        "comparison_digest": grant.get("comparison_digest"),
    })


def current_authorization(run_id: str, *, session_id: int | None = None) -> dict:
    """Return the validated latest grant while preserving its immutable root."""
    receipt = db.one("SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id)
    if receipt is None or session_id is not None and int(receipt["session_id"]) != session_id:
        raise FullAutoRunError(409, "full_auto_authorization_missing", "The Full Auto authorization is unavailable.")
    try:
        authorization = json.loads(receipt["authorization_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise FullAutoRunError(409, "full_auto_authorization_invalid", "The Full Auto authorization is invalid.") from exc
    if not isinstance(authorization, dict):
        raise FullAutoRunError(409, "full_auto_authorization_invalid", "The Full Auto authorization is invalid.")
    current = {
        "run_id": run_id,
        "session_id": int(receipt["session_id"]),
        "authorization": authorization,
        "scope_digest": receipt["scope_digest"],
        "dependencies_digest": receipt["dependencies_digest"],
        "plan_digest": receipt["plan_digest"],
        "scope_issued_at": receipt["scope_issued_at"],
        "authorization_expires_at": receipt["authorization_expires_at"],
        "sequence": 0,
    }
    current["authorization_digest"] = _authorization_digest(authorization, current)
    for replacement in db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    ):
        if (
            int(replacement["authorization_sequence"]) != current["sequence"] + 1
            or replacement["previous_authorization_digest"] != current["authorization_digest"]
        ):
            raise FullAutoRunError(409, "full_auto_authorization_invalid", "The Full Auto authorization chain is invalid.")
        try:
            authorization = json.loads(replacement["authorization_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise FullAutoRunError(409, "full_auto_authorization_invalid", "The Full Auto authorization chain is invalid.") from exc
        if not isinstance(authorization, dict):
            raise FullAutoRunError(409, "full_auto_authorization_invalid", "The Full Auto authorization chain is invalid.")
        current = {
            "run_id": run_id,
            "session_id": int(receipt["session_id"]),
            "authorization": authorization,
            "scope_digest": replacement["scope_digest"],
            "dependencies_digest": replacement["dependencies_digest"],
            "plan_digest": replacement["plan_digest"],
            "scope_issued_at": replacement["scope_issued_at"],
            "authorization_expires_at": replacement["authorization_expires_at"],
            "sequence": int(replacement["authorization_sequence"]),
            "comparison_digest": replacement["comparison_digest"],
        }
        current["authorization_digest"] = _authorization_digest(authorization, current)
    root_is_legacy = current["sequence"] == 0
    if (
        authorization.get("authorization_expires_at") != current["authorization_expires_at"]
        or ("scope_digest" in authorization and authorization.get("scope_digest") != current["scope_digest"])
        or ("scope_issued_at" in authorization and authorization.get("scope_issued_at") != current["scope_issued_at"])
        or (not root_is_legacy and (
            authorization.get("scope_digest") != current["scope_digest"]
            or authorization.get("scope_issued_at") != current["scope_issued_at"]
        ))
    ):
        raise FullAutoRunError(409, "full_auto_authorization_invalid", "The Full Auto authorization is invalid.")
    return current


def _bound_dependency_issue(authorization: dict) -> str | None:
    summary = authorization.get("summary") if isinstance(authorization, dict) else None
    if (
        not isinstance(authorization, dict)
        or not isinstance(summary, dict)
        or not {"model_binding", "workflow", "saved_look"}.issubset(summary)
        or "profile_revision" not in authorization
        or "profile_digest" not in authorization
    ):
        # Older domain tests and migrated policy-only receipts intentionally do
        # not represent a complete consent binding. Preserve their established
        # validation errors; only complete grants can report a lost dependency.
        return None
    try:
        summary, _limits = _validate_status_authorization(authorization)
        saved = summary["saved_look"]
        room = summary["room"]
        model = summary["model_binding"]
        workflow = summary["workflow"]
        profile_revision = authorization["profile_revision"]
        profile_digest = authorization["profile_digest"]
    except (KeyError, TypeError, FullAutoRunError):
        return "authorization_invalid"
    if any((
        db.one("SELECT 1 FROM model WHERE id = ?", model["model_id"]) is None,
        db.one("SELECT 1 FROM workflow WHERE id = ?", workflow["workflow_id"]) is None,
        db.one(
            "SELECT 1 FROM saved_look_version WHERE look_key = ? AND version = ?",
            saved["look_key"], saved["version"],
        ) is None,
        db.one(
            """SELECT 1 FROM saved_look_automation_profile
               WHERE look_key = ? AND look_version = ? AND profile_revision = ?
                 AND profile_digest = ?""",
            saved["look_key"], saved["version"], profile_revision, profile_digest,
        ) is None,
    )):
        return "dependency_missing"

    # Resolve the exact room identity captured by consent. A later revision for
    # the same source does not replace a lost authorized revision.
    library = db.one(
        "SELECT * FROM resource_library WHERE library_key = ?",
        room["library_key"],
    )
    if library is None:
        return "dependency_missing"
    if library["kind"] != "rooms":
        return "dependency_changed"
    revision_row = db.one(
        """SELECT id FROM asset_revision
           WHERE library_id = ? AND source_id = ? AND content_digest = ?""",
        library["id"], room["source_id"], room["content_digest"],
    )
    if revision_row is None:
        return "dependency_missing"

    try:
        from backend import guided_sessions, resource_store

        _resolved_library, revision = guided_sessions._validate_ready_room(room)
    except guided_sessions.GuidedSessionError:
        return "dependency_changed"
    if resource_store.canonical_digest(revision.get("translation")) != room["translation_digest"]:
        return "dependency_changed"
    return None


def _current_authority_issue(row: dict, now_text: str) -> str | None:
    """Return why this immutable grant can no longer authorize new work."""
    plan = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?",
        int(row["session_id"]),
    )
    if plan is None or int(plan["plan_revision"]) != int(row["plan_revision"]):
        return "plan_revision_stale"

    try:
        grant = current_authorization(row["run_id"], session_id=int(row["session_id"]))
        authorization = grant["authorization"]
        expiry = _timestamp(
            grant["authorization_expires_at"], field_name="authorization expiry",
        )
    except (TypeError, json.JSONDecodeError, FullAutoRunError):
        return "authorization_invalid"
    if expiry <= _timestamp(now_text, field_name="current time"):
        return "authorization_expired"

    dependency_issue = _bound_dependency_issue(authorization)
    if dependency_issue is not None:
        return dependency_issue

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


def _new_work_authority_issue(row: dict, now_text: str) -> str | None:
    """Include the live feature gate when deciding whether more work may start."""
    if not resource_planning.is_enabled():
        return "resource_planning_disabled"
    return _current_authority_issue(row, now_text)


def _authority_error(code: str) -> FullAutoRunError:
    messages = {
        "resource_planning_disabled": "Resource planning is disabled by configuration.",
        "plan_revision_stale": "The current plan changed; refresh consent before continuing.",
        "authorization_expired": "The Full Auto authorization has expired.",
        "authorization_invalid": "The persisted Full Auto authorization is invalid.",
        "policy_changed": "The recorded Full Auto policy is not supported.",
        "dependency_missing": "A resource bound to this Full Auto run is missing. Restore it or keep the run stopped.",
        "dependency_changed": "A resource bound to this Full Auto run changed after consent. Review the current scope before continuing.",
    }
    return FullAutoRunError(409, code, messages.get(code, "Full Auto authority is no longer valid."))


def _invalid_saved_status() -> FullAutoRunError:
    return FullAutoRunError(
        409, "run_state_invalid",
        "The saved Full Auto status could not be safely verified.",
    )


def _invalid_saved_preparation() -> FullAutoRunError:
    return FullAutoRunError(
        500, "run_state_invalid",
        "Saved Full Auto preparation could not be safely verified.",
    )


def _validate_public_run_view(view: dict) -> dict:
    """Validate the closed scalar run view before exposing persisted values."""
    if type(view) is not dict or any(field not in view for field in _PUBLIC_RUN_VIEW_FIELDS):
        raise _invalid_saved_status()
    run_id = view["run_id"]
    session_id = view["session_id"]
    revisions = (view["revision"], view["epoch"], view["plan_revision"])
    blocker = view["blocker_code"]
    if (
        type(run_id) is not str or not run_id or len(run_id) > 200
        or type(session_id) is not int or session_id <= 0
        or any(type(value) is not int or value <= 0 for value in revisions)
        or type(view["state"]) is not str or view["state"] not in _NONTERMINAL | _TERMINAL
        or type(view["phase"]) is not str or view["phase"] not in _PHASES
        or type(view["cancel_intent"]) is not bool
        or (blocker is not None and (type(blocker) is not str or blocker not in _PUBLIC_BLOCKER_CODES))
        or (view["state"] == "blocked") != (blocker is not None)
    ):
        raise _invalid_saved_status()
    _timestamp(view["created_at"], field_name="creation timestamp")
    _timestamp(view["updated_at"], field_name="update timestamp")
    return {field: view[field] for field in _PUBLIC_RUN_VIEW_FIELDS}


def _validate_status_authorization(authorization: object) -> tuple[dict, dict]:
    """Validate only the closed authorization fields used by public status."""
    auth_fields = {
        "authorization_version", "generate_without_pauses", "profile_revision",
        "profile_digest", "progression", "framing_intent", "scope_digest",
        "scope_issued_at", "authorization_expires_at", "summary",
    }
    summary_fields = {
        "saved_look", "automation_profile", "room", "character_id",
        "model_binding", "workflow", "provider", "generation_settings", "canvas",
        "framing_intent", "effective_framing", "variation_policy", "photo_count",
        "continuity_digest", "progression", "coverage_compiler_version",
        "policy_version", "limits", "scope_issued_at", "authorization_expires_at",
        "request_digest", "dependencies_digest",
    }
    limits_fields = {
        "assistant_calls_per_take", "assistant_calls_total",
        "image_attempts_per_take", "image_attempts_total",
    }
    if type(authorization) is not dict or set(authorization) != auth_fields:
        raise _invalid_saved_status()
    summary = authorization["summary"]
    if type(summary) is not dict or set(summary) not in (
        summary_fields, summary_fields - {"continuity_digest"},
    ):
        raise _invalid_saved_status()
    photo_count = summary["photo_count"]
    limits = summary["limits"]
    if (
        type(authorization["authorization_version"]) is not int
        or not 1 <= authorization["authorization_version"] <= 999
        or authorization["generate_without_pauses"] is not True
        or type(authorization["profile_revision"]) is not int
        or not 1 <= authorization["profile_revision"] <= 9_223_372_036_854_775_807
        or type(authorization["profile_digest"]) is not str
        or not _SHA256.fullmatch(authorization["profile_digest"])
        or authorization["progression"] != "all_stages_full_interval"
        or type(authorization["framing_intent"]) is not str
        or authorization["framing_intent"] not in {"vary", "full_body"}
        or type(authorization["scope_digest"]) is not str
        or not _SHA256.fullmatch(authorization["scope_digest"])
        or type(photo_count) is not int or not 1 <= photo_count <= 500
        or type(limits) is not dict or set(limits) != limits_fields
        or type(summary["policy_version"]) is not str
        or not _PUBLIC_POLICY_VERSION.fullmatch(summary["policy_version"])
        or type(summary["request_digest"]) is not str
        or not _SHA256.fullmatch(summary["request_digest"])
        or type(summary["dependencies_digest"]) is not str
        or not _SHA256.fullmatch(summary["dependencies_digest"])
        or ("continuity_digest" in summary and (
            type(summary["continuity_digest"]) is not str
            or not _SHA256.fullmatch(summary["continuity_digest"])
        ))
        or summary["scope_issued_at"] != authorization["scope_issued_at"]
        or summary["authorization_expires_at"] != authorization["authorization_expires_at"]
    ):
        raise _invalid_saved_status()
    try:
        for name in ("scope_issued_at", "authorization_expires_at"):
            value = authorization[name]
            if type(value) is not str or _timestamp(value, field_name="authorization timestamp").isoformat(timespec="seconds") != value:
                raise _invalid_saved_status()
    except FullAutoRunError as exc:
        raise _invalid_saved_status() from exc
    if (
        any(type(value) is not int or value <= 0 for value in limits.values())
        or limits["assistant_calls_per_take"] != 2
        or limits["image_attempts_per_take"] != 1
        or limits["assistant_calls_total"] != photo_count * limits["assistant_calls_per_take"]
        or limits["image_attempts_total"] != photo_count * limits["image_attempts_per_take"]
    ):
        raise _invalid_saved_status()
    projected = _project_consent_summary(summary)
    from backend import resource_store

    if resource_store.canonical_digest(projected) != authorization["scope_digest"]:
        raise _invalid_saved_status()
    return projected, limits


def _project_consent_summary(value: object) -> dict:
    """Validate and copy the complete closed public consent-summary shape."""
    fields = {
        "saved_look", "automation_profile", "room", "character_id",
        "model_binding", "workflow", "provider", "generation_settings", "canvas",
        "framing_intent", "effective_framing", "variation_policy", "photo_count",
        "continuity_digest", "progression", "coverage_compiler_version",
        "policy_version", "limits", "scope_issued_at", "authorization_expires_at",
        "request_digest", "dependencies_digest",
    }

    def fail() -> None:
        raise _invalid_saved_status()

    def digest(item: object) -> str:
        if type(item) is not str or not _SHA256.fullmatch(item):
            fail()
        return item

    def text(item: object, *, maximum: int = 500, safe_id: bool = False) -> str:
        if (
            type(item) is not str or not item or len(item) > maximum
            or item != item.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in item)
            or (safe_id and any(not (char.isalnum() or char in "._:-") for char in item))
        ):
            fail()
        return item

    def positive_int(item: object, *, maximum: int = 9_223_372_036_854_775_807) -> int:
        if type(item) is not int or not 1 <= item <= maximum:
            fail()
        return item

    def policy_dimension(item: object) -> dict:
        if type(item) is not dict:
            fail()
        mode = item.get("mode")
        if mode == "vary" and set(item) == {"mode"}:
            return {"mode": "vary"}
        if (
            mode == "fixed" and set(item) == {"mode", "value", "value_origin"}
            and item.get("value_origin") == "user"
        ):
            return {
                "mode": "fixed",
                "value": text(item["value"], maximum=1000),
                "value_origin": "user",
            }
        fail()

    if type(value) is not dict or set(value) not in (
        fields, fields - {"continuity_digest"},
    ):
        fail()

    saved = value["saved_look"]
    profile = value["automation_profile"]
    room = value["room"]
    model = value["model_binding"]
    workflow = value["workflow"]
    provider = value["provider"]
    generation = value["generation_settings"]
    canvas = value["canvas"]
    variation = value["variation_policy"]
    progression = value["progression"]
    limits = value["limits"]
    if (
        type(saved) is not dict or set(saved) != {"look_key", "version", "content_digest"}
        or type(profile) is not dict or set(profile) != {"revision", "digest", "ready"}
        or type(room) is not dict or set(room) != {"library_key", "source_id", "content_digest", "translation_digest"}
        or type(model) is not dict or set(model) != {
            "model_id", "name", "trigger_digest", "base_positive_digest",
            "base_negative_digest", "lora_selection_digest", "effective_settings_digest",
        }
        or type(workflow) is not dict or set(workflow) != {
            "workflow_id", "kind", "graph_digest", "node_map_digest",
        }
        or type(provider) is not dict
        or set(provider) not in (
            {"model", "endpoint_identity"},
            {"model", "endpoint_identity", "model_identity"},
        )
        or type(generation) is not dict
        or not set(generation).issubset({"steps", "cfg", "sampler", "scheduler"})
        or type(canvas) is not dict or set(canvas) != {"width", "height"}
        or type(variation) is not dict
        or set(variation) != {"camera", "framing", "pose", "expression"}
        or type(progression) is not dict or set(progression) != {"policy", "stages"}
        or type(progression["stages"]) is not list or not progression["stages"]
        or type(limits) is not dict or set(limits) != {
            "assistant_calls_per_take", "assistant_calls_total",
            "image_attempts_per_take", "image_attempts_total",
        }
    ):
        fail()

    if (
        text(saved["look_key"], maximum=128, safe_id=True) != saved["look_key"]
        or positive_int(saved["version"]) != saved["version"]
        or digest(saved["content_digest"]) != saved["content_digest"]
        or positive_int(profile["revision"]) != profile["revision"]
        or digest(profile["digest"]) != profile["digest"]
        or profile["ready"] is not True
        or text(room["library_key"], maximum=200, safe_id=True) != room["library_key"]
        or text(room["source_id"], maximum=200, safe_id=True) != room["source_id"]
        or digest(room["content_digest"]) != room["content_digest"]
        or digest(room["translation_digest"]) != room["translation_digest"]
        or positive_int(value["character_id"]) != value["character_id"]
        or positive_int(model["model_id"]) != model["model_id"]
        or type(model["name"]) is not str
        or not model["name"] or len(model["name"]) > 120
        or model["name"] != model["name"].strip()
        or any(not (char.isalnum() or char in " ._-") for char in model["name"])
        or any(digest(model[name]) != model[name] for name in (
            "trigger_digest", "base_positive_digest", "base_negative_digest",
            "lora_selection_digest", "effective_settings_digest",
        ))
        or positive_int(workflow["workflow_id"]) != workflow["workflow_id"]
        or text(workflow["kind"], maximum=40, safe_id=True) != workflow["kind"]
        or any(digest(workflow[name]) != workflow[name] for name in ("graph_digest", "node_map_digest"))
        or digest(provider["endpoint_identity"]) != provider["endpoint_identity"]
        or ("model_identity" in provider and digest(provider["model_identity"]) != provider["model_identity"])
        or text(provider["model"], maximum=120, safe_id=True) != provider["model"]
        or positive_int(canvas["width"], maximum=32768) != canvas["width"]
        or positive_int(canvas["height"], maximum=32768) != canvas["height"]
        or type(value["framing_intent"]) is not str
        or value["framing_intent"] not in {"vary", "full_body"}
        or value["photo_count"] != positive_int(value["photo_count"], maximum=500)
        or ("continuity_digest" in value and digest(value["continuity_digest"]) != value["continuity_digest"])
        or text(value["coverage_compiler_version"], maximum=100, safe_id=True) != value["coverage_compiler_version"]
        or type(value["policy_version"]) is not str
        or not _PUBLIC_POLICY_VERSION.fullmatch(value["policy_version"])
        or digest(value["request_digest"]) != value["request_digest"]
        or digest(value["dependencies_digest"]) != value["dependencies_digest"]
    ):
        fail()

    normalized_generation: dict[str, object] = {}
    for name, item in generation.items():
        if name == "steps":
            if type(item) is not int or not 1 <= item <= 9_223_372_036_854_775_807:
                fail()
            normalized_generation[name] = item
        elif name == "cfg":
            if type(item) not in {int, float}:
                fail()
            try:
                finite = math.isfinite(item)
            except (OverflowError, TypeError, ValueError):
                finite = False
            if not finite:
                fail()
            normalized_generation[name] = item
        else:
            normalized_generation[name] = text(item, maximum=80, safe_id=True)

    normalized_variation = {
        name: policy_dimension(variation[name])
        for name in ("camera", "framing", "pose", "expression")
    }
    normalized_progression = []
    for stage in progression["stages"]:
        if type(stage) is not dict or set(stage) != {
            "coverage", "first_take_id", "last_take_id", "stage_index", "take_count",
        }:
            fail()
        if type(stage["stage_index"]) is not int or stage["stage_index"] < 0:
            fail()
        normalized_progression.append({
            "coverage": text(stage["coverage"], maximum=1000),
            "first_take_id": text(stage["first_take_id"], maximum=200, safe_id=True),
            "last_take_id": text(stage["last_take_id"], maximum=200, safe_id=True),
            "stage_index": stage["stage_index"],
            "take_count": positive_int(stage["take_count"], maximum=500),
        })
    normalized_limits = {
        name: positive_int(item, maximum=250_000)
        for name, item in limits.items()
    }
    if (
        normalized_limits["assistant_calls_per_take"] != 2
        or normalized_limits["image_attempts_per_take"] != 1
        or normalized_limits["assistant_calls_total"] != value["photo_count"] * 2
        or normalized_limits["image_attempts_total"] != value["photo_count"]
        or progression["policy"] != "all_stages_full_interval"
    ):
        fail()

    projected = dict(value)
    projected["saved_look"] = {
        "look_key": saved["look_key"], "version": saved["version"],
        "content_digest": saved["content_digest"],
    }
    projected["automation_profile"] = {
        "revision": profile["revision"], "digest": profile["digest"], "ready": True,
    }
    projected["room"] = {
        "library_key": room["library_key"], "source_id": room["source_id"],
        "content_digest": room["content_digest"], "translation_digest": room["translation_digest"],
    }
    projected["model_binding"] = dict(model)
    projected["workflow"] = dict(workflow)
    projected["provider"] = dict(provider)
    projected["generation_settings"] = normalized_generation
    projected["canvas"] = {"width": canvas["width"], "height": canvas["height"]}
    projected["effective_framing"] = policy_dimension(value["effective_framing"])
    projected["variation_policy"] = normalized_variation
    projected["progression"] = {
        "policy": "all_stages_full_interval", "stages": normalized_progression,
    }
    projected["limits"] = normalized_limits
    return projected


def _view(row: dict) -> dict:
    """Return validated status-safe fields; lease deadlines and fences stay private."""
    if (
        type(row.get("session_id")) is not int
        or type(row.get("revision")) is not int
        or type(row.get("epoch")) is not int
        or type(row.get("plan_revision")) is not int
        or type(row.get("cancel_intent")) is not int
        or row.get("cancel_intent") not in (0, 1)
    ):
        raise _invalid_saved_status()
    return _validate_public_run_view({
        "run_id": row.get("run_id"),
        "session_id": row["session_id"],
        "revision": row["revision"],
        "epoch": row["epoch"],
        "plan_revision": row["plan_revision"],
        "state": row.get("state"),
        "phase": row.get("phase"),
        "cancel_intent": bool(row["cancel_intent"]),
        "blocker_code": row.get("blocker_code"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    })


def get_run(run_id: str) -> dict:
    return _view(_require_run(run_id))


def get_run_for_session(session_id: int) -> dict | None:
    row = _run_row_for_session(session_id)
    return None if row is None else _view(row)


_BLOCKER_REMEDIES = {
    "assistant_unavailable": (
        "No prompt assistant is configured for this Full Auto run.",
        ["configure_assistant", "resume", "cancel"],
    ),
    "assistant_transport_error": (
        "The prompt assistant could not complete the request.",
        ["check_provider", "resume", "cancel"],
    ),
    "assistant_rate_limited": (
        "The prompt assistant rate limited this run.",
        ["wait", "resume", "cancel"],
    ),
    "assistant_refused": (
        "The prompt assistant refused this take.",
        ["revise_brief", "manual_authoring", "cancel"],
    ),
    "writer_output_invalid": (
        "The prompt assistant returned unusable structured output.",
        ["revise_brief", "manual_authoring", "resume"],
    ),
    "provider_changed": (
        "The configured prompt assistant changed after Full Auto authorization.",
        ["compare_scope", "review_changes", "cancel"],
    ),
    "comfy_unavailable": (
        "ComfyUI remained unavailable after bounded read-only checks. Check the ComfyUI endpoint and availability in Setup.",
        ["review_status", "cancel"],
    ),
    "authorization_expired": (
        "The Full Auto authorization expired.",
        ["compare_scope", "resume_with_changed_scope", "cancel"],
    ),
    "authorization_scope_changed": (
        "Authorized inputs changed since the last comparison.",
        ["compare_scope", "review_changes", "cancel"],
    ),
    "plan_revision_stale": (
        "The session plan changed and needs a fresh scope comparison.",
        ["compare_scope", "review_changes", "cancel"],
    ),
    "resource_planning_disabled": (
        "Resource planning is disabled by configuration.",
        ["enable_resource_planning", "cancel"],
    ),
    "manual_edit_pause": (
        "The run is paused for a reviewed plan edit.",
        ["review_changes", "compare_scope", "cancel"],
    ),
    "execution_outcome_unknown": (
        "The remote generation outcome is not verified.",
        ["reconcile_cancellation", "review_status"],
    ),
    "run_work_unsettled": (
        "Owned Full Auto work has not reached a verified terminal outcome.",
        ["review_status", "cancel"],
    ),
    "dependency_missing": (
        "A resource bound to this Full Auto run is missing.",
        ["restore_dependency", "cancel"],
    ),
    "dependency_changed": (
        "A resource bound to this Full Auto run changed after consent.",
        ["compare_scope", "cancel"],
    ),
    "policy_changed": (
        "The recorded Full Auto policy is no longer supported.",
        ["review_status", "cancel"],
    ),
    "authorization_invalid": (
        "The saved Full Auto authorization could not be verified.",
        ["review_status", "cancel"],
    ),
    "budget_exhausted": (
        "The remaining Full Auto allowance cannot be verified as positive.",
        ["review_status", "cancel"],
    ),
}


def _require_comfy_probe_owner(claim: RunClaim, timestamp: str) -> dict:
    row = _require_claim(
        claim, now_text=timestamp, reconciliation=claim.requires_reconciliation,
    )
    cancellation_recovery = (
        claim.requires_reconciliation and bool(row["cancel_intent"])
        and row["state"] in {"blocked", "cancel_requested"}
        and row["phase"] == "reconcile" and row["resume_phase"] == "execute"
    )
    if (
        not cancellation_recovery and (
            row["phase"] not in {"execute", "reconcile"}
            or row["phase"] == "reconcile" and row["resume_phase"] != "execute"
            or bool(row["cancel_intent"])
        )
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto ComfyUI probe owner is stale.")
    if not cancellation_recovery:
        authority_issue = _current_authority_issue(row, timestamp)
        if authority_issue is not None:
            raise _authority_error(authority_issue)
        if not resource_planning.is_enabled():
            raise FullAutoRunError(
                503, "resource_planning_disabled", "Full Auto is disabled by configuration.",
            )
    return row


def _require_comfy_probe_authorization_deadline(row: dict, timestamp: str) -> None:
    """Recheck the grant deadline after authority validation, at the write edge."""
    try:
        grant = current_authorization(
            row["run_id"], session_id=int(row["session_id"]),
        )
        expiry = _timestamp(
            grant["authorization_expires_at"], field_name="authorization expiry",
        )
    except (TypeError, json.JSONDecodeError, FullAutoRunError) as exc:
        raise _authority_error("authorization_invalid") from exc
    if expiry <= _timestamp(timestamp, field_name="current time"):
        raise _authority_error("authorization_expired")


def _recheck_comfy_probe_owner_before_write(
    claim: RunClaim,
    now_text: str | None,
) -> tuple[dict, str]:
    """Revalidate authority, then check the live fence again at the write edge."""
    timestamp = _now_text(now_text)
    _require_comfy_probe_owner(claim, timestamp)
    timestamp = _now_text(now_text)
    row = _require_claim(claim, now_text=timestamp, reconciliation=claim.requires_reconciliation)
    cancellation_recovery = (
        claim.requires_reconciliation and bool(row["cancel_intent"])
        and row["state"] in {"blocked", "cancel_requested"}
        and row["phase"] == "reconcile" and row["resume_phase"] == "execute"
    )
    if (
        not cancellation_recovery and (
            row["phase"] not in {"execute", "reconcile"}
            or row["phase"] == "reconcile" and row["resume_phase"] != "execute"
            or bool(row["cancel_intent"])
        )
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto ComfyUI probe owner is stale.")
    if not cancellation_recovery:
        if not resource_planning.is_enabled():
            raise FullAutoRunError(
                503, "resource_planning_disabled", "Full Auto is disabled by configuration.",
            )
        # Authority validation can finish just as its grant expires. The fresh
        # timestamp above protects the deadline independently from the earlier
        # authority check, while this live claim check still protects the fence.
        _require_comfy_probe_authorization_deadline(row, timestamp)
    return row, timestamp


def _validate_comfy_probe_scope(scope: str) -> None:
    if not isinstance(scope, str) or not re.fullmatch(r"(?:dispatch|history):[a-f0-9]{1,64}", scope):
        raise FullAutoRunError(422, "invalid_probe_scope", "The ComfyUI probe scope is invalid.")


def comfy_probe_state(
    claim: RunClaim,
    scope: str,
    *,
    now_text: str | None = None,
) -> dict[str, object]:
    """Read the durable bounded-probe cursor for one owned dispatch/history boundary."""
    _validate_comfy_probe_scope(scope)
    with db.transaction():
        timestamp = _now_text(now_text)
        row = _require_comfy_probe_owner(claim, timestamp)
        if row["comfy_probe_scope"] != scope:
            return {"scope": scope, "count": 0, "due_at": None, "pending": False}
        count = row["comfy_probe_count"]
        due_at = row["comfy_probe_due_at"]
        if type(count) is not int or not 0 <= count <= len(COMFY_PROBE_DELAYS):
            raise FullAutoRunError(500, "run_state_invalid", "Saved ComfyUI probe state is invalid.")
        if due_at is not None:
            _timestamp(due_at, field_name="ComfyUI probe deadline")
        return {"scope": scope, "count": count, "due_at": due_at, "pending": True}


def schedule_comfy_probe(
    claim: RunClaim,
    scope: str,
    *,
    now_text: str | None = None,
) -> dict[str, object]:
    """Persist the initial 5-second read-only recovery deadline after a failed read."""
    _validate_comfy_probe_scope(scope)
    with db.transaction():
        timestamp = _now_text(now_text)
        current_time = _timestamp(timestamp, field_name="current time")
        row = _require_comfy_probe_owner(claim, timestamp)
        same_scope = row["comfy_probe_scope"] == scope
        count = int(row["comfy_probe_count"]) if same_scope else 0
        due_at = row["comfy_probe_due_at"] if same_scope else None
        if not 0 <= count <= len(COMFY_PROBE_DELAYS):
            raise FullAutoRunError(500, "run_state_invalid", "Saved ComfyUI probe state is invalid.")
        if not due_at and count < len(COMFY_PROBE_DELAYS):
            due_at = (current_time + timedelta(seconds=COMFY_PROBE_DELAYS[0])).isoformat(
                timespec="seconds",
            )
        row, timestamp = _recheck_comfy_probe_owner_before_write(claim, now_text)
        current_time = _timestamp(timestamp, field_name="current time")
        same_scope = row["comfy_probe_scope"] == scope
        count = int(row["comfy_probe_count"]) if same_scope else 0
        due_at = row["comfy_probe_due_at"] if same_scope else None
        if not 0 <= count <= len(COMFY_PROBE_DELAYS):
            raise FullAutoRunError(500, "run_state_invalid", "Saved ComfyUI probe state is invalid.")
        if not due_at and count < len(COMFY_PROBE_DELAYS):
            due_at = (current_time + timedelta(seconds=COMFY_PROBE_DELAYS[0])).isoformat(
                timespec="seconds",
            )
        db.run(
            """UPDATE full_auto_run
               SET comfy_probe_scope = ?, comfy_probe_count = ?, comfy_probe_due_at = ?,
                   updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?""",
            scope, count, due_at, timestamp, claim.run_id, claim.epoch, claim.fencing_token,
        )
        return {"scope": scope, "count": count, "due_at": due_at, "pending": True}


def reserve_comfy_probe(
    claim: RunClaim,
    scope: str,
    *,
    now_text: str | None = None,
) -> dict[str, object]:
    """Durably consume the next probe before its GET so a lost response cannot replay it."""
    _validate_comfy_probe_scope(scope)
    with db.transaction():
        timestamp = _now_text(now_text)
        current_time = _timestamp(timestamp, field_name="current time")
        row = _require_comfy_probe_owner(claim, timestamp)
        if row["comfy_probe_scope"] != scope:
            raise FullAutoRunError(409, "comfy_probe_not_scheduled", "No ComfyUI probe is scheduled.")
        count = row["comfy_probe_count"]
        due_at = row["comfy_probe_due_at"]
        if type(count) is not int or not 0 <= count <= len(COMFY_PROBE_DELAYS):
            raise FullAutoRunError(500, "run_state_invalid", "Saved ComfyUI probe state is invalid.")
        if count >= len(COMFY_PROBE_DELAYS) or due_at is None:
            raise FullAutoRunError(
                503, "comfy_unavailable", "ComfyUI did not recover after bounded read-only checks.",
            )
        if _timestamp(due_at, field_name="ComfyUI probe deadline") > current_time:
            raise FullAutoRunError(409, "comfy_probe_not_due", "The next ComfyUI probe is not due yet.")
        row, timestamp = _recheck_comfy_probe_owner_before_write(claim, now_text)
        current_time = _timestamp(timestamp, field_name="current time")
        if row["comfy_probe_scope"] != scope:
            raise FullAutoRunError(409, "comfy_probe_not_scheduled", "No ComfyUI probe is scheduled.")
        count = row["comfy_probe_count"]
        due_at = row["comfy_probe_due_at"]
        if type(count) is not int or not 0 <= count <= len(COMFY_PROBE_DELAYS):
            raise FullAutoRunError(500, "run_state_invalid", "Saved ComfyUI probe state is invalid.")
        if count >= len(COMFY_PROBE_DELAYS) or due_at is None:
            raise FullAutoRunError(
                503, "comfy_unavailable", "ComfyUI did not recover after bounded read-only checks.",
            )
        if _timestamp(due_at, field_name="ComfyUI probe deadline") > current_time:
            raise FullAutoRunError(409, "comfy_probe_not_due", "The next ComfyUI probe is not due yet.")
        next_count = count + 1
        next_due_at = None if next_count == len(COMFY_PROBE_DELAYS) else (
            current_time + timedelta(seconds=COMFY_PROBE_DELAYS[next_count])
        ).isoformat(timespec="seconds")
        db.run(
            """UPDATE full_auto_run
               SET comfy_probe_count = ?, comfy_probe_due_at = ?, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?""",
            next_count, next_due_at, timestamp,
            claim.run_id, claim.epoch, claim.fencing_token,
        )
        return {"scope": scope, "count": next_count, "due_at": next_due_at, "pending": True}


def clear_comfy_probe_state(
    claim: RunClaim,
    scope: str,
    *,
    now_text: str | None = None,
) -> None:
    """Clear only the completed read boundary's probe cursor."""
    _validate_comfy_probe_scope(scope)
    with db.transaction():
        timestamp = _now_text(now_text)
        row = _require_comfy_probe_owner(claim, timestamp)
        if row["comfy_probe_scope"] != scope:
            return
        row, timestamp = _recheck_comfy_probe_owner_before_write(claim, now_text)
        if row["comfy_probe_scope"] != scope:
            return
        db.run(
            """UPDATE full_auto_run
               SET comfy_probe_scope = NULL, comfy_probe_count = 0,
                   comfy_probe_due_at = NULL, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?""",
            timestamp, claim.run_id, claim.epoch, claim.fencing_token,
        )


def _cancel_unstarted_full_auto_shots(run: dict, timestamp: str) -> list[int]:
    """Cancel linked targets that have no dispatch journal, inside the cancel transaction."""
    progress = db.one(
        "SELECT target_ids_json FROM full_auto_prepare_progress WHERE run_id = ?",
        run["run_id"],
    )
    if progress is None:
        return []
    target_ids = _prepare_json_list(progress["target_ids_json"], field_name="targets")
    if not target_ids:
        return []
    placeholders = ",".join("?" for _ in target_ids)
    rows = db.q(
        f"""SELECT DISTINCT shot.id, shot.status
            FROM shot
            WHERE shot.session_id = ? AND shot.status IN ('pending', 'running')
              AND shot.id IN (
                  SELECT prepared.linked_shot_id
                  FROM prepared_take prepared
                  WHERE prepared.session_id = ? AND prepared.plan_revision = ?
                    AND prepared.take_id IN ({placeholders})
                  UNION
                  SELECT receipt.shot_id
                  FROM full_auto_submission_receipt receipt
                  WHERE receipt.run_id = ?
              )
              AND NOT EXISTS (
                  SELECT 1 FROM full_auto_execution_attempt attempt
                  WHERE attempt.shot_id = shot.id
              )""",
        int(run["session_id"]), int(run["session_id"]), int(run["plan_revision"]),
        *target_ids, run["run_id"],
    )
    shot_ids = [int(row["id"]) for row in rows]
    if shot_ids:
        shot_placeholders = ",".join("?" for _ in shot_ids)
        db.run(
            f"""UPDATE shot SET status = 'cancelled', finished_at = ?
                WHERE id IN ({shot_placeholders}) AND status IN ('pending', 'running')
                  AND NOT EXISTS (
                      SELECT 1 FROM full_auto_execution_attempt attempt
                      WHERE attempt.shot_id = shot.id
                  )""",
            timestamp, *shot_ids,
        )
    return shot_ids


def _restore_unstarted_full_auto_shots(run: dict, timestamp: str) -> int:
    """Continue only cancelled linked targets that never acquired an attempt journal."""
    progress = db.one(
        "SELECT target_ids_json FROM full_auto_prepare_progress WHERE run_id = ?",
        run["run_id"],
    )
    if progress is None:
        return 0
    target_ids = _prepare_json_list(progress["target_ids_json"], field_name="targets")
    if not target_ids:
        return 0
    placeholders = ",".join("?" for _ in target_ids)
    return db.run(
        f"""UPDATE shot SET status = 'pending', error = '', finished_at = ''
            WHERE session_id = ? AND status = 'cancelled'
              AND id IN (
                  SELECT prepared.linked_shot_id
                  FROM prepared_take prepared
                  WHERE prepared.session_id = ? AND prepared.plan_revision = ?
                    AND prepared.take_id IN ({placeholders})
                  UNION
                  SELECT receipt.shot_id
                  FROM full_auto_submission_receipt receipt
                  WHERE receipt.run_id = ?
              )
              AND NOT EXISTS (
                  SELECT 1 FROM full_auto_execution_attempt attempt
                  WHERE attempt.shot_id = shot.id
              )""",
        int(run["session_id"]), int(run["session_id"]), int(run["plan_revision"]),
        *target_ids, run["run_id"],
    )


def unresolved_work_count(
    session_id: int,
    run_id: str,
    *,
    ignored_shot_ids: set[int] | frozenset[int] = frozenset(),
    allow_running_session: bool = False,
) -> int:
    """Count local owned work conservatively; a linked shot may have dispatched."""
    if type(allow_running_session) is not bool or any(
        type(shot_id) is not int or shot_id <= 0 for shot_id in ignored_shot_ids
    ):
        raise ValueError("Invalid unresolved-work filter")
    ignored = set(ignored_shot_ids)
    count = 0
    session = db.one("SELECT status FROM session WHERE id = ?", session_id)
    if session is None:
        return 1
    if session["status"] == "running" and not allow_running_session:
        count += 1
    operations = db.q(
        "SELECT state FROM authoring_operation WHERE session_id = ? AND full_auto_run_id = ?",
        session_id, run_id,
    )
    count += sum(item["state"] not in {"succeeded", "failed", "cancelled", "expired"} for item in operations)
    pending_assistant = db.q(
        "SELECT reservation_id FROM full_auto_assistant_reservation "
        "WHERE run_id = ?",
        run_id,
    )
    # A durable reservation can remain quarantined after a lost response;
    # count only local transport/cancellation cleanup still physically running.
    count += sum(
        _local_assistant_dispatch_live(int(item["reservation_id"]))
        for item in pending_assistant
    )
    shots = db.q(
        "SELECT shot.id, shot.status, "
        "(SELECT attempt.run_id FROM full_auto_execution_attempt attempt "
        "WHERE attempt.shot_id = shot.id ORDER BY attempt.attempt_number DESC LIMIT 1) AS attempt_run_id, "
        "(SELECT attempt.completion_state FROM full_auto_execution_attempt attempt "
        "WHERE attempt.shot_id = shot.id ORDER BY attempt.attempt_number DESC LIMIT 1) AS attempt_state "
        "FROM shot WHERE shot.session_id = ?", session_id,
    )
    admission = db.one("SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id)
    # A failed status alone is ambiguous. Its own durable failed attempt is a
    # settlement receipt; every other failed shot remains conservative.
    count += sum(
        int(item["id"]) not in ignored
        and item["status"] not in {"done", "cancelled"}
        and not (
            item["status"] == "failed" and item["attempt_run_id"] == run_id
            and item["attempt_state"] == "failed"
        )
        for item in shots
    )
    if admission is not None:
        try:
            expected = int(current_authorization(run_id)["authorization"]["summary"]["photo_count"])
            linked = db.q(
                """SELECT sh.id, sh.session_id, sh.status,
                          (SELECT attempt.completion_state FROM full_auto_execution_attempt attempt
                           WHERE attempt.shot_id = sh.id ORDER BY attempt.attempt_number DESC LIMIT 1) AS attempt_state,
                          (SELECT attempt.run_id FROM full_auto_execution_attempt attempt
                           WHERE attempt.shot_id = sh.id ORDER BY attempt.attempt_number DESC LIMIT 1) AS attempt_run_id
                   FROM full_auto_submission_receipt receipt
                   LEFT JOIN shot sh ON sh.id = receipt.shot_id
                   WHERE receipt.run_id = ?""",
                run_id,
            )
            if len(linked) != expected:
                count += 1
            count += sum(
                shot["id"] is None or shot["session_id"] != session_id
                or (
                    int(shot["id"]) not in ignored
                    and shot["status"] not in {"done", "cancelled"}
                    and not (
                        shot["status"] == "failed"
                        and shot["attempt_run_id"] == run_id
                        and shot["attempt_state"] == "failed"
                    )
                )
                for shot in linked
            )
        except (FullAutoRunError, TypeError, KeyError, ValueError):
            count += 1
    return count


_UNKNOWN_OUTCOME_RETRY_WARNING = (
    "The prior request may have been accepted even though it is absent from the current queue. "
    "Retrying can create duplicate work. The server rechecks the reachable queue before accepting."
)


def _retry_candidates(session_id: int, run_id: str) -> dict:
    """Return only current linked-shot retry candidates and their latest attempt IDs."""
    rows = db.q(
        """SELECT sh.id AS shot_id, sh.status AS shot_status,
                  attempt.attempt_id, attempt.run_id AS attempt_run_id,
                  attempt.dispatch_state, attempt.completion_state
           FROM full_auto_submission_receipt receipt
           JOIN shot sh ON sh.id = receipt.shot_id
           LEFT JOIN full_auto_execution_attempt attempt
             ON attempt.attempt_id = (
                 SELECT latest.attempt_id FROM full_auto_execution_attempt latest
                 WHERE latest.shot_id = sh.id
                 ORDER BY latest.attempt_number DESC LIMIT 1
             )
           WHERE receipt.run_id = ? AND sh.session_id = ?
           ORDER BY receipt.target_index""",
        run_id, session_id,
    )
    known: list[int] = []
    unknown: list[dict] = []
    for item in rows:
        if (
            item["shot_status"] in {"failed", "cancelled"}
            and item["attempt_id"] is not None
            and item["attempt_run_id"] == run_id
            and item["completion_state"] == "failed"
            and db.one(
                "SELECT 1 FROM full_auto_attempt_retry_authorization WHERE attempt_id = ?",
                item["attempt_id"],
            ) is None
        ):
            known.append(int(item["shot_id"]))
        elif (
            item["shot_status"] != "done"
            and item["attempt_id"] is not None
            and item["attempt_run_id"] == run_id
            and item["completion_state"] == "pending"
            and item["dispatch_state"] in {"intent", "outcome_unknown"}
            and db.one(
                "SELECT 1 FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
                item["attempt_id"],
            ) is None
        ):
            unknown.append({
                "shot_id": int(item["shot_id"]),
                "attempt_id": item["attempt_id"],
            })
    return {"failed_shot_ids": known, "unknown_attempts": unknown}


def _image_retry_allowance(
    run_id: str,
    initial_total: int,
    *,
    candidates: dict | None = None,
) -> dict:
    """Project cumulative image capacity and the exact currently retryable targets."""
    granted = db.one(
        """SELECT COUNT(*) AS n FROM (
               SELECT attempt_id FROM full_auto_attempt_retry_authorization WHERE run_id = ?
               UNION
               SELECT attempt_id FROM full_auto_attempt_risk_acceptance WHERE run_id = ?
           )""",
        run_id, run_id,
    )
    consumed = db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ?",
        run_id,
    )
    if candidates is None:
        run = _require_run(run_id)
        candidates = _retry_candidates(int(run["session_id"]), run_id)
    failed_ids = candidates["failed_shot_ids"]
    unknown_attempts = candidates["unknown_attempts"]
    unknown_shot_ids = [item["shot_id"] for item in unknown_attempts]
    unknown_attempt_ids = [item["attempt_id"] for item in unknown_attempts]
    previous_grants = int(granted["n"])
    attempts_consumed = int(consumed["n"])
    authorized_total = initial_total + previous_grants
    failed_increment = len(failed_ids)
    unknown_increment = len(unknown_attempts)
    return {
        "initial_image_attempts_total": initial_total,
        "previous_retry_grants": previous_grants,
        "image_attempts_consumed": attempts_consumed,
        "authorized_image_attempts_total": authorized_total,
        "authorized_image_attempts_remaining": max(0, authorized_total - attempts_consumed),
        "retry_failed_shot_ids": failed_ids,
        "unknown_outcome_shot_ids": unknown_shot_ids,
        "unknown_attempt_ids": unknown_attempt_ids,
        "additional_if_retry_failed": failed_increment,
        "additional_if_accept_unknown_outcome_risk": unknown_increment,
        "authorized_total_after_retry_failed": authorized_total + failed_increment,
        "authorized_total_after_unknown_risk": authorized_total + unknown_increment,
        "authorized_total_after_both": authorized_total + failed_increment + unknown_increment,
    }


def unknown_attempts_for_retry(
    session_id: int,
    run_id: str,
    attempt_ids: list[str],
) -> list[dict]:
    """Load current unknown attempts for a queue probe, rejecting stale or cancelled work."""
    if (
        not isinstance(attempt_ids, list)
        or not attempt_ids
        or any(type(value) is not str or not re.fullmatch(r"[0-9a-f]{32}", value) for value in attempt_ids)
        or len(attempt_ids) != len(set(attempt_ids))
    ):
        raise FullAutoRunError(409, "retry_targets_changed", "The unknown retry targets changed; reload status.")
    run = _run_row(run_id)
    if run is None or int(run["session_id"]) != session_id:
        raise FullAutoRunError(404, "full_auto_run_not_found", "Full Auto run not found.")
    if (
        bool(run["cancel_intent"]) and run["state"] in {"blocked", "cancel_requested"}
    ):
        raise FullAutoRunError(
            409, "cancel_reconciliation_required",
            "Pending cancellation must settle before an unknown outcome can be retried.",
        )
    if run["state"] not in {"blocked", "cancelled", "failed", "completed_with_errors"}:
        raise FullAutoRunError(409, "run_not_resumable", "The Full Auto run cannot be retried in its current state.")
    candidates = _retry_candidates(session_id, run_id)["unknown_attempts"]
    if {item["attempt_id"] for item in candidates} != set(attempt_ids):
        raise FullAutoRunError(409, "retry_targets_changed", "The unknown retry targets changed; reload status.")
    placeholders = ",".join("?" for _ in attempt_ids)
    rows = db.q(
        f"SELECT * FROM full_auto_execution_attempt WHERE attempt_id IN ({placeholders}) ORDER BY shot_id",
        *attempt_ids,
    )
    if len(rows) != len(attempt_ids) or any(
        item["run_id"] != run_id or int(item["session_id"]) != session_id
        or item["completion_state"] != "pending"
        or item["dispatch_state"] not in {"intent", "outcome_unknown"}
        for item in rows
    ):
        raise FullAutoRunError(409, "retry_targets_changed", "The unknown retry targets changed; reload status.")
    acceptance_epoch = int(run["epoch"]) + (1 if run["state"] in _TERMINAL else 0)
    return [{**item, "acceptance_epoch": acceptance_epoch} for item in rows]


def retry_action_replay(
    run_id: str,
    request_id: str,
    expected_run_revision: int,
    expected_plan_revision: int,
    *,
    expected_session_id: int,
    scope_digest: str | None = None,
    comparison_issued_at: str | None = None,
    accept_changed_scope: bool = False,
    retry_failed_shots: bool = False,
    accept_unknown_outcome_risk: bool = False,
) -> RunActionResult | None:
    """Return a persisted Resume replay before any queue probe or other I/O."""
    _body, request_digest = _action_fields(
        "resume", run_id, request_id, expected_run_revision, expected_plan_revision,
        scope_digest=scope_digest, comparison_issued_at=comparison_issued_at,
        accept_changed_scope=accept_changed_scope,
        retry_failed_shots=retry_failed_shots,
        accept_unknown_outcome_risk=accept_unknown_outcome_risk,
    )
    with db.transaction():
        _check_run_session_identity(run_id, expected_session_id)
        return _replay_action(run_id, request_id, request_digest, "resume")


def _run_deletion_protected(row: dict) -> bool:
    return (
        row["state"] not in _TERMINAL
        or unresolved_work_count(int(row["session_id"]), row["run_id"]) > 0
        or db.one(
            "SELECT 1 FROM full_auto_attempt_risk_acceptance WHERE run_id = ? LIMIT 1",
            row["run_id"],
        ) is not None
    )


def _deletion_grants(run_id: str) -> list[dict] | None:
    try:
        current_authorization(run_id)
        rows = [db.one(
            "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
            run_id,
        )]
        rows.extend(db.q(
            "SELECT authorization_json FROM full_auto_run_authorization "
            "WHERE run_id = ? ORDER BY authorization_sequence",
            run_id,
        ))
        grants = []
        for row in rows:
            if row is None:
                return None
            authorization = json.loads(row["authorization_json"])
            _validate_status_authorization(authorization)
            grants.append(authorization)
        return grants
    except (FullAutoRunError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _authorization_uses_dependency(
    authorization: dict,
    dependency: str,
    identity: object,
) -> bool:
    summary, _limits = _validate_status_authorization(authorization)
    if dependency == "model":
        return summary["model_binding"]["model_id"] == identity
    if dependency == "workflow":
        return summary["workflow"]["workflow_id"] == identity
    if dependency == "look":
        return (summary["saved_look"]["look_key"], summary["saved_look"]["version"]) == identity
    if dependency == "profile":
        return (
            summary["saved_look"]["look_key"], summary["saved_look"]["version"],
            authorization["profile_revision"], authorization["profile_digest"],
        ) == identity
    raise ValueError(f"Unsupported Full Auto deletion dependency: {dependency}")


def require_deletion_allowed(dependency: str, identity: object) -> None:
    """Refuse deletion while any exact grant or unsettled run still depends on it."""
    affected: set[int] = set()
    if dependency == "session":
        rows = db.q("SELECT * FROM full_auto_run WHERE session_id = ?", identity)
        affected.update(
            int(row["session_id"]) for row in rows if _run_deletion_protected(row)
        )
    elif dependency == "shot":
        shot = db.one("SELECT * FROM shot WHERE id = ?", identity)
        if shot is None:
            return
        runs = db.q(
            "SELECT * FROM full_auto_run WHERE session_id = ?", shot["session_id"],
        )
        for row in runs:
            if not _run_deletion_protected(row):
                continue
            linked = db.one(
                "SELECT 1 FROM full_auto_submission_receipt WHERE run_id = ? AND shot_id = ? "
                "UNION SELECT 1 FROM prepared_take WHERE session_id = ? AND linked_shot_id = ? LIMIT 1",
                row["run_id"], identity, row["session_id"], identity,
            )
            uncertain_admission = db.one(
                "SELECT 1 FROM full_auto_deletion_uncertain_admission WHERE run_id = ?",
                row["run_id"],
            )
            if linked is not None or (
                uncertain_admission is not None
                and shot["status"] in {"pending", "running", "failed"}
            ):
                affected.add(int(row["session_id"]))
    else:
        if dependency not in {"model", "workflow", "look", "profile"}:
            raise ValueError(f"Unsupported Full Auto deletion dependency: {dependency}")
        for row in db.q("SELECT * FROM full_auto_run"):
            if not _run_deletion_protected(row):
                continue
            grants = _deletion_grants(row["run_id"])
            if grants is None or any(
                _authorization_uses_dependency(grant, dependency, identity)
                for grant in grants
            ):
                affected.add(int(row["session_id"]))

    if affected:
        ids = sorted(affected)
        raise FullAutoRunError(
            409,
            "full_auto_in_use",
            "Full Auto still depends on this item in session(s) "
            f"{', '.join(map(str, ids))}. Cancel each run and wait until its work settles before deleting.",
            affected_sessions=ids,
        )


def assistant_call_accounting(run_id: str) -> dict[str, int]:
    """Return cumulative linked-grant allowance and durable physical dispatches."""
    # current_authorization verifies that the linked renewal chain is complete
    # before any historical grant is included in public accounting.
    current_authorization(run_id)
    rows = [db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    )]
    rows.extend(db.q(
        "SELECT authorization_json FROM full_auto_run_authorization WHERE run_id = ? "
        "ORDER BY authorization_sequence", run_id,
    ))
    grants: list[dict] = []
    total = 0
    for row in rows:
        if row is None:
            raise FullAutoRunError(409, "full_auto_authorization_invalid", "The Full Auto authorization is invalid.")
        try:
            authorization = json.loads(row["authorization_json"])
            _summary, limits = _validate_status_authorization(authorization)
        except (TypeError, KeyError, json.JSONDecodeError, FullAutoRunError) as exc:
            raise FullAutoRunError(409, "full_auto_authorization_invalid", "The Full Auto authorization is invalid.") from exc
        grants.append(limits)
        total += int(limits["assistant_calls_total"])
    run = _run_row(run_id)
    if run is None:
        raise FullAutoRunError(404, "full_auto_run_not_found", "Full Auto run not found.")
    reservations = db.q(
        "SELECT * FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
        run_id,
    )
    counts: dict[int, int] = {}
    per_take: dict[tuple[int, str], int] = {}
    for reservation in reservations:
        sequence = reservation["authorization_sequence"]
        if (
            type(sequence) is not int or not 0 <= sequence < len(grants)
            or type(reservation["session_id"]) is not int
            or int(reservation["session_id"]) != int(run["session_id"])
            or type(reservation["take_id"]) is not str or not reservation["take_id"]
            or type(reservation["operation_id"]) is not str or not reservation["operation_id"]
            or type(reservation["slot_generation"]) is not int or reservation["slot_generation"] <= 0
            or type(reservation["request_number"]) is not int
            or not 1 <= reservation["request_number"] <= int(grants[sequence]["assistant_calls_per_take"])
        ):
            raise _invalid_saved_status()
        try:
            _timestamp(reservation["dispatched_at"], field_name="assistant dispatch")
            if reservation["settled_at"] is None:
                if reservation["response_metadata_json"] is not None:
                    raise _invalid_saved_status()
            else:
                _timestamp(reservation["settled_at"], field_name="assistant response")
                metadata = json.loads(reservation["response_metadata_json"])
                if _safe_assistant_response_metadata(metadata) != metadata:
                    raise _invalid_saved_status()
        except (TypeError, json.JSONDecodeError, FullAutoRunError) as exc:
            raise _invalid_saved_status() from exc
        counts[sequence] = counts.get(sequence, 0) + 1
        key = (sequence, reservation["take_id"])
        per_take[key] = per_take.get(key, 0) + 1
    if (
        any(count > int(grants[sequence]["assistant_calls_total"]) for sequence, count in counts.items())
        or any(count > int(grants[sequence]["assistant_calls_per_take"]) for (sequence, _take), count in per_take.items())
    ):
        raise _invalid_saved_status()
    reserved = len(reservations)
    if reserved > total:
        raise _invalid_saved_status()
    return {"authorized": total, "reserved": reserved, "remaining": max(0, total - reserved)}


def assistant_executable_allowance(
    run_id: str,
    pending_take_id: str | None,
) -> dict[str, Any]:
    """Return the latest grant's executable allowance and its reservation commitment."""
    historical = assistant_call_accounting(run_id)
    grant = current_authorization(run_id)
    _summary, limits = _validate_status_authorization(grant["authorization"])
    sequence = int(grant["sequence"])
    grouped = db.q(
        """SELECT authorization_sequence, take_id, COUNT(*) AS count
           FROM full_auto_assistant_reservation WHERE run_id = ?
           GROUP BY authorization_sequence, take_id
           ORDER BY authorization_sequence, take_id""",
        run_id,
    )
    by_grant: dict[int, int] = {}
    by_take: dict[str, int] = {}
    for row in grouped:
        row_sequence = int(row["authorization_sequence"])
        count = int(row["count"])
        by_grant[row_sequence] = by_grant.get(row_sequence, 0) + count
        if row_sequence == sequence:
            by_take[row["take_id"]] = count
    grant_reserved = by_grant.get(sequence, 0)
    grant_remaining = max(0, int(limits["assistant_calls_total"]) - grant_reserved)
    if pending_take_id is None:
        per_take_reserved = 0
        per_take_remaining = None
        executable_remaining = grant_remaining
    else:
        per_take_reserved = by_take.get(pending_take_id, 0)
        per_take_remaining = max(
            0, int(limits["assistant_calls_per_take"]) - per_take_reserved,
        )
        executable_remaining = min(grant_remaining, per_take_remaining)
    counts_digest = _digest({
        "authorization_sequence": sequence,
        "reserved_by_grant_and_take": [
            {
                "authorization_sequence": int(row["authorization_sequence"]),
                "take_id": row["take_id"],
                "count": int(row["count"]),
            }
            for row in grouped
        ],
    })
    return {
        "authorized": historical["authorized"],
        "reserved": historical["reserved"],
        "historical_remaining": historical["remaining"],
        "authorization_sequence": sequence,
        "grant_authorized": int(limits["assistant_calls_total"]),
        "grant_reserved": grant_reserved,
        "grant_remaining": grant_remaining,
        "per_take_authorized": int(limits["assistant_calls_per_take"]),
        "pending_take_id": pending_take_id,
        "pending_take_reserved": per_take_reserved,
        "pending_take_remaining": per_take_remaining,
        "executable_remaining": executable_remaining,
        "reservation_counts_digest": counts_digest,
    }


def public_run_status(session_id: int, run_id: str) -> dict:
    """Build a closed status projection without prompts, paths, or owner secrets."""
    row = _run_row(run_id)
    if row is None or int(row["session_id"]) != session_id:
        raise FullAutoRunError(404, "full_auto_run_not_found", "Full Auto run not found.")
    view = _view(row)
    grant = current_authorization(run_id, session_id=session_id)
    summary, limits = _validate_status_authorization(grant["authorization"])
    current_plan_revision, authorized_targets = _authoritative_plan_targets(
        session_id, summary["photo_count"],
    )
    progress_row = db.one("SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id)
    if progress_row is None:
        targets: list[str] = []
        prepared_ids: list[str] = []
        progress = None
    else:
        progress = _read_prepare_progress(run_id)
        targets = progress["target_ids"]
        prepared_ids = progress["completed_take_ids"]
    started_targets = _validated_prepare_accounting(
        row, progress_row, progress, authorized_targets,
    )
    if progress_row is None and db.one(
        "SELECT 1 FROM full_auto_submission_receipt WHERE run_id = ? LIMIT 1", run_id,
    ) is not None:
        raise _invalid_saved_status()
    receipts = [] if progress_row is None else _read_submission_receipts(
        run_id, session_id, int(progress_row["plan_revision"]), authorized_targets,
    )
    shots = db.q("SELECT id, status FROM shot WHERE session_id = ? ORDER BY id", session_id)
    shot_status = {int(item["id"]): item["status"] for item in shots}
    submitted_ids = [int(item["shot_id"]) for item in receipts]
    if any(shot_id not in shot_status for shot_id in submitted_ids):
        raise _invalid_saved_status()
    completed = [shot_id for shot_id in submitted_ids if shot_status[shot_id] == "done"]
    failed = [shot_id for shot_id in submitted_ids if shot_status[shot_id] == "failed"]
    cancelled = [shot_id for shot_id in submitted_ids if shot_status[shot_id] == "cancelled"]
    assistant_total = limits["assistant_calls_total"]
    assistant_per_take = limits["assistant_calls_per_take"]
    retry_candidates = _retry_candidates(session_id, run_id)
    image_allowance = _image_retry_allowance(
        run_id, limits["image_attempts_total"], candidates=retry_candidates,
    )
    image_total = image_allowance["authorized_image_attempts_total"]
    image_per_take = limits["image_attempts_per_take"]
    pending_assistant_take = next(
        (take_id for take_id in authorized_targets if take_id not in started_targets),
        None,
    )
    assistant_accounting = assistant_executable_allowance(run_id, pending_assistant_take)
    assistant_total = assistant_accounting["authorized"]
    assistant_remaining = assistant_accounting["grant_remaining"]
    assistant_executable_remaining = assistant_accounting["executable_remaining"]
    assistant_budget_available = (
        pending_assistant_take is None or assistant_executable_remaining > 0
    )
    assistant_budget_blocked = (
        pending_assistant_take is not None and assistant_executable_remaining == 0
    )
    image_remaining = image_allowance["authorized_image_attempts_remaining"]
    blocker_code = row["blocker_code"]
    authority_issue = _current_authority_issue(row, db.now())
    unresolved = unresolved_work_count(session_id, run_id)
    if authority_issue == "dependency_missing":
        blocker_code = authority_issue
    elif blocker_code is None:
        blocker_code = authority_issue
    if blocker_code is None and unresolved:
        blocker_code = "run_work_unsettled"
    if blocker_code is None and (assistant_budget_blocked or image_remaining == 0):
        blocker_code = "budget_exhausted"
    message, remedies = _BLOCKER_REMEDIES.get(
        blocker_code,
        ("Review the Full Auto run status and current session inputs.", ["review_status", "cancel"]),
    ) if blocker_code else (None, [])
    enabled = resource_planning.is_enabled()
    state = row["state"]
    cancelable = state in _NONTERMINAL
    reconcile_only = bool(row["cancel_intent"]) and state in {"blocked", "cancel_requested"}
    resumable_state = state in {"blocked", "cancelled", "failed", "completed_with_errors"}
    retry_state_available = (
        resumable_state and state != "succeeded"
        and not (bool(row["cancel_intent"]) and state in {"blocked", "cancel_requested"})
    )
    live_run_lease = row["lease_expires_at"] is not None and _timestamp(
        row["lease_expires_at"], field_name="run lease",
    ) > _timestamp(db.now(), field_name="current time")
    runner_owner = db.one(
        "SELECT owner_kind, session_id, run_id, lease_expires_at "
        "FROM global_runner_claim WHERE singleton = 1",
    )
    live_runner_lease = (
        runner_owner is not None and runner_owner["owner_kind"] is not None
        and int(runner_owner["session_id"] or 0) == session_id
        and runner_owner["run_id"] in {None, run_id}
        and runner_owner["lease_expires_at"] is not None
        and _timestamp(runner_owner["lease_expires_at"], field_name="runner lease")
        > _timestamp(db.now(), field_name="current time")
    )
    policy_is_supported = summary.get("policy_version") == _SUPPORTED_POLICY_VERSION
    authority_repairable = authority_issue not in {
        "authorization_invalid", "policy_changed", "dependency_missing",
    }
    budget_available = assistant_budget_available and image_remaining > 0
    can_resume = reconcile_only or (
        enabled and resumable_state and state != "succeeded" and unresolved == 0
        and authority_repairable and policy_is_supported and budget_available
    )
    retry_after_at = row.get("assistant_retry_after_at")
    retry_wait_active = False
    if blocker_code == "assistant_rate_limited" and retry_after_at:
        try:
            retry_wait_active = _timestamp(
                retry_after_at, field_name="assistant Retry-After deadline",
            ) > _timestamp(db.now(), field_name="current time")
        except FullAutoRunError:
            raise _invalid_saved_status()
        if retry_wait_active:
            message = f"The prompt assistant can be resumed after {retry_after_at} UTC."
            remedies = ["wait", "resume", "cancel"]
            can_resume = False
    view.update({
        "phase": row["phase"],
        "blocker_code": blocker_code,
        "current_plan_revision": current_plan_revision,
        "progress": {
            "target_count": len(targets) or int(summary.get("photo_count", 0)),
            "prepared_count": len(prepared_ids),
            "submitted_count": len(receipts),
            "completed_count": len(completed),
            "failed_count": len(failed),
            "cancelled_count": len(cancelled),
            "target_take_ids": targets,
            "prepared_take_ids": prepared_ids,
            "submitted_shot_ids": submitted_ids,
            "completed_shot_ids": completed,
            "failed_shot_ids": failed,
            "cancelled_shot_ids": cancelled,
            "unresolved_work_count": unresolved,
        },
        "authorization_version": grant["authorization"].get("authorization_version"),
        "policy_version": summary.get("policy_version"),
        "authorization_expires_at": grant["authorization_expires_at"],
        "allowances": {
            "assistant_calls_total": assistant_total,
            "assistant_calls_per_take": assistant_per_take,
            "assistant_calls_remaining_lower_bound": assistant_remaining,
            "image_attempts_initial_total": image_allowance["initial_image_attempts_total"],
            "image_retry_grants": image_allowance["previous_retry_grants"],
            "image_attempts_consumed": image_allowance["image_attempts_consumed"],
            "image_attempts_total": image_total,
            "image_attempts_per_take": image_per_take,
            "image_attempts_remaining_lower_bound": image_remaining,
        },
        "blocker": None if blocker_code is None else {
            "code": blocker_code, "message": message, "remedies": remedies,
        },
        "can_cancel": cancelable,
        "can_resume": can_resume,
        "resume_action": "reconcile_cancellation" if reconcile_only else "resume",
        "retry_control": {
            "failed_shot_ids": retry_candidates["failed_shot_ids"],
            "unknown_outcome_shot_ids": [
                item["shot_id"] for item in retry_candidates["unknown_attempts"]
            ],
            "unknown_attempt_ids": [
                item["attempt_id"] for item in retry_candidates["unknown_attempts"]
            ],
            "can_retry_failed_shots": bool(
                retry_candidates["failed_shot_ids"] and retry_state_available
                and not live_run_lease and not live_runner_lease
                and authority_repairable and policy_is_supported and enabled
            ),
            "can_accept_unknown_outcome_risk": bool(
                retry_candidates["unknown_attempts"] and retry_state_available
                and not live_run_lease and not live_runner_lease
                and authority_repairable and policy_is_supported and enabled
            ),
            "unknown_outcome_warning": (
                _UNKNOWN_OUTCOME_RETRY_WARNING
                if retry_candidates["unknown_attempts"] else None
            ),
            "image_retry_allowance": image_allowance,
        },
    })
    return view


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
                    created_at DESC, rowid DESC LIMIT 1""",
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

        if expired:
            quarantine_run_assistant_slots(run_id, now_text=timestamp)

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
            authority_issue = _new_work_authority_issue(row, timestamp)
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
    with db.transaction():
        # A caller can wait on SQLite's write lock long enough for its lease to
        # expire. Use the live clock only after the transaction has acquired
        # that lock; explicit timestamps remain deterministic for tests and
        # internal callers.
        timestamp = _now_text(now_text)
        row = _require_claim(claim, now_text=timestamp, reconciliation=claim.requires_reconciliation)
        # Keep the owner valid at the actual compare-and-swap boundary too.
        # The validation above is local, but must not accidentally revive a
        # lease if that work itself crosses the expiry instant.
        timestamp = _now_text(now_text)
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


def _runner_claim_payload(claim: RunnerClaim) -> bytes:
    return _canonical_json({
        "purpose": "global-runner-claim-v1",
        "owner_kind": claim.owner_kind,
        "session_id": claim.session_id,
        "run_id": claim.run_id,
        "owner_nonce": claim.owner_nonce,
        "fencing_token": claim.fencing_token,
        "lease_started_at": claim.lease_started_at,
        "lease_expires_at": claim.lease_expires_at,
    }).encode("utf-8")


def _make_runner_claim(row: dict) -> RunnerClaim:
    unsigned = RunnerClaim(
        owner_kind=row["owner_kind"],
        session_id=int(row["session_id"]),
        run_id=row["run_id"],
        owner_nonce=row["owner_nonce"],
        fencing_token=int(row["fencing_token"]),
        lease_started_at=row["lease_started_at"],
        lease_expires_at=row["lease_expires_at"],
        _signature="",
    )
    signature = hmac.new(_RUNNER_CLAIM_KEY, _runner_claim_payload(unsigned), hashlib.sha256).hexdigest()
    return RunnerClaim(**{**unsigned.__dict__, "_signature": signature})


def _require_runner_claim(claim: RunnerClaim, *, now_text: str) -> dict:
    if not isinstance(claim, RunnerClaim) or not isinstance(claim._signature, str):
        raise FullAutoRunError(409, "runner_owner_stale", "The runner owner is stale.")
    expected = hmac.new(_RUNNER_CLAIM_KEY, _runner_claim_payload(claim), hashlib.sha256).hexdigest()
    row = db.one("SELECT * FROM global_runner_claim WHERE singleton = 1")
    if (
        not hmac.compare_digest(expected, claim._signature)
        or row is None
        or row["owner_kind"] != claim.owner_kind
        or row["session_id"] != claim.session_id
        or row["run_id"] != claim.run_id
        or row["owner_nonce"] != claim.owner_nonce
        or int(row["fencing_token"]) != claim.fencing_token
        or row["lease_started_at"] != claim.lease_started_at
        or row["lease_expires_at"] != claim.lease_expires_at
    ):
        raise FullAutoRunError(409, "runner_owner_stale", "The runner owner is stale.")
    if _timestamp(row["lease_expires_at"], field_name="runner lease") <= _timestamp(
        now_text, field_name="current time",
    ):
        raise FullAutoRunError(409, "runner_lease_expired", "The runner lease expired.")
    return row


def _eligible_runner_waiters(now_text: str, *, exclude_run_id: str | None = None) -> list[dict]:
    if not resource_planning.is_enabled():
        return []
    rows = db.q(
        """SELECT * FROM full_auto_run
           WHERE state = 'waiting_runner' AND phase = 'execute'
             AND cancel_intent = 0 AND runner_ready_at IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1 FROM full_auto_runner_admission a WHERE a.run_id = full_auto_run.run_id
             )
           ORDER BY runner_ready_at, run_id"""
    )
    return [
        row for row in rows
        if row["run_id"] != exclude_run_id and _current_authority_issue(row, now_text) is None
    ]


def acquire_runner_claim(
    session_id: int,
    *,
    run_claim: RunClaim | None = None,
    recovery: bool = False,
    cancellation_recovery: bool = False,
    now_text: str | None = None,
) -> RunnerClaim | None:
    """Reserve the single local runner or return None for an eligible Full Auto wait."""
    with db.transaction():
        def validate(timestamp: str) -> tuple[dict | None, dict, bool]:
            run = None
            if run_claim is not None:
                if recovery:
                    run = _require_claim(
                        run_claim, now_text=timestamp,
                        reconciliation=run_claim.requires_reconciliation,
                    )
                    recovering_cancel_execute = (
                        cancellation_recovery and run_claim.requires_reconciliation
                        and bool(run["cancel_intent"])
                        and run["state"] in {"blocked", "cancel_requested"}
                        and run["phase"] == "reconcile"
                        and run["resume_phase"] == "execute"
                    )
                    recovering_expired_execute = (
                        run_claim.requires_reconciliation
                        and run["phase"] == "reconcile"
                        and run["resume_phase"] == "execute"
                    )
                    recovering_admitted_execute = (
                        not run_claim.requires_reconciliation
                        and run["phase"] == "execute"
                        and run["state"] in {"active", "waiting_runner"}
                        and not bool(run["cancel_intent"])
                    )
                    if (
                        int(run["session_id"]) != session_id
                        or not (
                            recovering_cancel_execute
                            or (
                                run["state"] == "active" and not run["cancel_intent"]
                                and recovering_expired_execute
                            )
                            or recovering_admitted_execute
                        )
                        or db.one(
                            "SELECT 1 FROM full_auto_runner_admission WHERE run_id = ?",
                            run_claim.run_id,
                        ) is None
                    ):
                        raise FullAutoRunError(
                            409, "run_owner_stale", "The Full Auto recovery owner is stale.",
                        )
                else:
                    run = require_session_mutation_allowed(
                        session_id, claim=run_claim, allowed_phases=("execute",),
                    )
                if run is None or (not recovery and run["phase"] != "execute"):
                    raise FullAutoRunError(
                        409, "run_owner_stale", "The Full Auto execution owner is stale.",
                    )
                run = _require_claim(
                    run_claim, now_text=timestamp,
                    reconciliation=run_claim.requires_reconciliation,
                )
                has_admission = db.one(
                    "SELECT 1 FROM full_auto_runner_admission WHERE run_id = ?", run_claim.run_id,
                ) is not None
                if (recovery and not has_admission) or (not recovery and has_admission):
                    raise FullAutoRunError(
                        409, "full_auto_runner_already_admitted",
                        "The Full Auto runner admission does not match this start.",
                    )
                if not recovery:
                    authority_issue = _new_work_authority_issue(run, timestamp)
                    if authority_issue is not None:
                        raise _authority_error(authority_issue)
            else:
                require_session_mutation_allowed(session_id)
                waiter = next(iter(_eligible_runner_waiters(timestamp)), None)
                if waiter is not None:
                    raise FullAutoRunError(
                        409, "runner_busy", "A Full Auto run is waiting for the runner.",
                        current_run=_view(waiter),
                    )

            slot = db.one("SELECT * FROM global_runner_claim WHERE singleton = 1")
            if slot is None:
                raise FullAutoRunError(
                    500, "runner_claim_missing", "The shared runner claim is unavailable.",
                )
            if slot["owner_kind"] is not None:
                expired = _timestamp(slot["lease_expires_at"], field_name="runner lease") <= _timestamp(
                    timestamp, field_name="current time",
                )
                if not expired:
                    return run, slot, False

            # A pre-lease process may have left a running shot before the
            # durable singleton existed. A free or expired claim cannot prove
            # that such remote work stopped, so every running shot fences all
            # new admissions until reconciliation settles it.
            unresolved = db.one(
                "SELECT id FROM shot WHERE status = 'running' LIMIT 1",
            )
            recover_owned_running = False
            if recovery and run_claim is not None and unresolved is not None:
                running = db.q("SELECT id, session_id FROM shot WHERE status = 'running'")
                recover_owned_running = bool(running) and all(
                    int(item["session_id"]) == session_id for item in running
                )
            if unresolved is not None and not recover_owned_running:
                return run, slot, False

            if run_claim is not None:
                waiters = _eligible_runner_waiters(timestamp, exclude_run_id=run_claim.run_id)
                if (
                    not cancellation_recovery and run["state"] != "waiting_runner"
                    and waiters and not recover_owned_running
                ):
                    return run, slot, False
                if not cancellation_recovery and run["state"] == "waiting_runner":
                    current_key = (run["runner_ready_at"] or "", run["run_id"])
                    if any((item["runner_ready_at"], item["run_id"]) < current_key for item in waiters):
                        return run, slot, False
            return run, slot, True

        timestamp = _now_text(now_text)
        run, slot, eligible = validate(timestamp)
        if not eligible:
            if run_claim is not None:
                return None
            reason = "The previous runner attempt is unresolved."
            if slot["owner_kind"] is not None and _timestamp(
                slot["lease_expires_at"], field_name="runner lease",
            ) > _timestamp(timestamp, field_name="current time"):
                reason = "The runner is already executing another session."
            raise FullAutoRunError(409, "runner_busy", reason)

        # Full authorization, FIFO, and stale-owner checks can involve many
        # reads. Validate them again against a fresh clock immediately before
        # writing the singleton owner row so a lease cannot cross its deadline
        # during admission and still receive dispatch authority.
        timestamp = _now_text(now_text)
        run, slot, eligible = validate(timestamp)
        if not eligible:
            if run_claim is not None:
                return None
            reason = "The previous runner attempt is unresolved."
            if slot["owner_kind"] is not None and _timestamp(
                slot["lease_expires_at"], field_name="runner lease",
            ) > _timestamp(timestamp, field_name="current time"):
                reason = "The runner is already executing another session."
            raise FullAutoRunError(409, "runner_busy", reason)

        owner_kind = "full_auto" if run_claim is not None else "manual"
        owner_nonce = uuid.uuid4().hex
        started_at = timestamp
        expires_at = (_timestamp(timestamp, field_name="current time") + RUNNER_LEASE).isoformat(timespec="seconds")
        fencing_token = int(slot["fencing_token"]) + 1
        changed = db.conn().execute(
            """UPDATE global_runner_claim
               SET owner_kind = ?, session_id = ?, run_id = ?, owner_nonce = ?,
                   fencing_token = ?, lease_started_at = ?, lease_expires_at = ?
               WHERE singleton = 1 AND fencing_token = ? AND owner_nonce IS ?""",
            (owner_kind, session_id, run_claim.run_id if run_claim is not None else None,
             owner_nonce, fencing_token, started_at, expires_at,
             slot["fencing_token"], slot["owner_nonce"]),
        ).rowcount
        if changed != 1:
            raise FullAutoRunError(409, "runner_owner_stale", "The shared runner changed during admission.")
        return _make_runner_claim(db.one(
            "SELECT * FROM global_runner_claim WHERE singleton = 1",
        ))


def renew_runner_claim(
    claim: RunnerClaim,
    *,
    now_text: str | None = None,
) -> RunnerClaim:
    with db.transaction():
        timestamp = _now_text(now_text)
        _require_runner_claim(claim, now_text=timestamp)
        timestamp = _now_text(now_text)
        _require_runner_claim(claim, now_text=timestamp)
        deadline = (
            _timestamp(timestamp, field_name="current time") + RUNNER_LEASE
        ).isoformat(timespec="seconds")
        changed = db.conn().execute(
            """UPDATE global_runner_claim
               SET lease_expires_at = ?, lease_started_at = ?
               WHERE singleton = 1 AND owner_nonce = ? AND fencing_token = ?
                 AND lease_expires_at = ? AND lease_started_at = ?""",
            (deadline, timestamp, claim.owner_nonce, claim.fencing_token,
             claim.lease_expires_at, claim.lease_started_at),
        ).rowcount
        if changed != 1:
            raise FullAutoRunError(409, "runner_owner_stale", "The runner owner is stale.")
        return _make_runner_claim(db.one("SELECT * FROM global_runner_claim WHERE singleton = 1"))


def release_runner_claim(
    claim: RunnerClaim, *, allow_cancelled_work: bool = False,
    now_text: str | None = None,
) -> bool:
    """Release only a live owner whose session has no unresolved dispatched shot."""
    if type(allow_cancelled_work) is not bool:
        raise FullAutoRunError(422, "runner_claim_invalid", "The runner release request is invalid.")
    with db.transaction():
        timestamp = _now_text(now_text)
        _require_runner_claim(claim, now_text=timestamp)
        unresolved = db.one("SELECT id FROM shot WHERE status = 'running' LIMIT 1")
        # Check again at the actual release boundary. The lease may expire
        # while the unresolved-work query runs, and expired ownership must not
        # be mistaken for proof that the remote queue settled.
        timestamp = _now_text(now_text)
        _require_runner_claim(claim, now_text=timestamp)
        if unresolved is not None or db.one("SELECT id FROM shot WHERE status = 'running' LIMIT 1") is not None:
            if not allow_cancelled_work or claim.owner_kind != "full_auto" or claim.run_id is None:
                return False
            run = db.one(
                "SELECT state, cancel_intent FROM full_auto_run WHERE run_id = ? AND session_id = ?",
                claim.run_id, claim.session_id,
            )
            running = db.q(
                """SELECT shot.id, attempt.run_id, attempt.session_id, attempt.completion_state
                   FROM shot LEFT JOIN full_auto_execution_attempt attempt
                     ON attempt.shot_id = shot.id
                    AND attempt.attempt_number = (
                        SELECT MAX(latest.attempt_number) FROM full_auto_execution_attempt latest
                        WHERE latest.shot_id = shot.id
                    )
                   WHERE shot.status = 'running'""",
            )
            if (
                run is None or run["state"] not in {"blocked", "cancel_requested"}
                or not bool(run["cancel_intent"])
                or not running
                or any(
                    item["run_id"] != claim.run_id
                    or int(item["session_id"] or 0) != claim.session_id
                    or item["completion_state"] not in {"pending", "output_available"}
                    for item in running
                )
            ):
                return False
        changed = db.conn().execute(
            """UPDATE global_runner_claim
               SET owner_kind = NULL, session_id = NULL, run_id = NULL, owner_nonce = NULL,
                   lease_started_at = NULL, lease_expires_at = NULL
               WHERE singleton = 1 AND owner_nonce = ? AND fencing_token = ?
                 AND lease_started_at = ? AND lease_expires_at = ?""",
            (claim.owner_nonce, claim.fencing_token, claim.lease_started_at, claim.lease_expires_at),
        ).rowcount
        if changed != 1:
            raise FullAutoRunError(409, "runner_owner_stale", "The runner owner is stale.")
        return True


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


def _assistant_slot_payload(claim: AssistantSlotClaim) -> bytes:
    return _canonical_json({
        "purpose": "full-auto-assistant-slot-v1",
        "run_id": claim.run_id,
        "session_id": claim.session_id,
        "take_id": claim.take_id,
        "operation_id": claim.operation_id,
        "generation": claim.generation,
        "epoch": claim.epoch,
        "run_fencing_token": claim.run_fencing_token,
        "operation_fencing_token": claim.operation_fencing_token,
        "lease_expires_at": claim.lease_expires_at,
    }).encode("utf-8")


def _make_assistant_slot_claim(row: dict) -> AssistantSlotClaim:
    unsigned = AssistantSlotClaim(
        run_id=row["run_id"],
        session_id=int(row["session_id"]),
        take_id=row["take_id"],
        operation_id=row["operation_id"],
        generation=int(row["generation"]),
        epoch=int(row["run_epoch"]),
        run_fencing_token=int(row["run_fencing_token"]),
        operation_fencing_token=int(row["operation_fencing_token"]),
        lease_expires_at=row["lease_expires_at"],
        _signature="",
    )
    signature = hmac.new(
        _ASSISTANT_SLOT_CLAIM_KEY, _assistant_slot_payload(unsigned), hashlib.sha256,
    ).hexdigest()
    return AssistantSlotClaim(**{**unsigned.__dict__, "_signature": signature})


def _authentic_assistant_slot_claim(claim: AssistantSlotClaim) -> bool:
    if not isinstance(claim, AssistantSlotClaim) or not isinstance(claim._signature, str):
        return False
    expected = hmac.new(
        _ASSISTANT_SLOT_CLAIM_KEY, _assistant_slot_payload(claim), hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, claim._signature)


def _assistant_owner_rows(
    run_claim: RunClaim,
    operation_claim: Any,
    take_id: str,
    *,
    now_text: str,
    provider_identity: object,
) -> tuple[dict, dict, dict, dict]:
    """Revalidate parent, child, provider and current grant at one local boundary."""
    from backend import authoring_operations

    if not isinstance(operation_claim, authoring_operations.OperationClaim):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto preparation owner is stale.")
    if not resource_planning.is_enabled():
        raise FullAutoRunError(
            503, "resource_planning_disabled", "Full Auto is disabled by configuration.",
        )
    run = require_prepare_claim(run_claim, session_id=operation_claim.session_id, now_text=now_text)
    operation = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?", operation_claim.operation_id,
    )
    if operation is None or (
        operation["state"] != "active"
        or int(operation["session_id"]) != operation_claim.session_id
        or int(operation["plan_revision"]) != operation_claim.plan_revision
        or int(operation["fencing_token"]) != operation_claim.fencing_token
        or operation["request_digest"] != operation_claim.request_digest
        or operation["full_auto_run_id"] != run_claim.run_id
        or operation["full_auto_run_epoch"] != run_claim.epoch
        or operation["full_auto_run_fencing_token"] != run_claim.fencing_token
        or operation["lease_expires_at"] is None
        or _timestamp(operation["lease_expires_at"], field_name="operation lease")
        <= _timestamp(now_text, field_name="current time")
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto preparation owner is stale.")
    try:
        remaining = json.loads(operation["remaining_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise FullAutoRunError(409, "prepare_child_owner_invalid", "The Full Auto preparation target is stale.") from exc
    if type(remaining) is not list or not remaining or remaining[0] != take_id:
        raise FullAutoRunError(409, "prepare_child_order_stale", "The Full Auto take is no longer next.")
    grant = current_authorization(run_claim.run_id, session_id=run_claim.session_id)
    summary, limits = _validate_status_authorization(grant["authorization"])
    if (
        type(provider_identity) is not dict
        or _canonical_json(provider_identity) != _canonical_json(summary["provider"])
    ):
        raise FullAutoRunError(
            409, "provider_changed",
            "The configured assistant provider changed after Full Auto authorization.",
        )
    return run, operation, grant, {"summary": summary, "limits": limits}


def _fresh_assistant_owner_rows(
    run_claim: RunClaim,
    operation_claim: Any,
    take_id: str,
    provider_identity: object,
    *,
    now_text: str | None = None,
) -> tuple[dict, dict, dict, dict, str]:
    """Recheck every assistant owner against the latest locked clock sample."""
    def validate(timestamp: str) -> tuple[dict, dict, dict, dict]:
        try:
            return _assistant_owner_rows(
                run_claim, operation_claim, take_id, now_text=timestamp,
                provider_identity=provider_identity,
            )
        except FullAutoRunError as exc:
            if exc.code == "run_lease_expired":
                raise FullAutoRunError(
                    409, "run_owner_stale", "The Full Auto prepare owner is stale.",
                ) from exc
            raise

    timestamp = _now_text(now_text)
    rows = validate(timestamp)
    checked_at = _now_text(None)
    if _timestamp(checked_at, field_name="current time") > _timestamp(
        timestamp, field_name="current time",
    ):
        timestamp = checked_at
        rows = validate(timestamp)

    # Validation itself can cross an expiry boundary. Check the rows once more
    # against a clock sample taken after that work, without trusting a timestamp
    # captured before the transaction or the owner lookups.
    run, operation, grant, authorization = rows
    now = _timestamp(checked_at, field_name="current time")
    if run.get("lease_expires_at") is None or _timestamp(
        run["lease_expires_at"], field_name="run lease",
    ) <= now:
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto prepare owner is stale.")
    if operation.get("lease_expires_at") is None or _timestamp(
        operation["lease_expires_at"], field_name="operation lease",
    ) <= now:
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto preparation owner is stale.")
    authority_issue = _current_authority_issue(run, checked_at)
    if authority_issue is not None:
        raise _authority_error(authority_issue)
    if not resource_planning.is_enabled():
        raise FullAutoRunError(
            503, "resource_planning_disabled", "Full Auto is disabled by configuration.",
        )
    return run, operation, grant, authorization, checked_at


def _assistant_slot_row(claim: AssistantSlotClaim) -> dict | None:
    return db.one(
        "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
        claim.run_id, claim.take_id,
    )


def _assistant_database_identity() -> tuple[str | int, ...]:
    # Some isolated callers install a raw sqlite connection without the app's
    # Row factory. Read under the shared-connection lock and use PRAGMA's
    # stable column order so both connection forms remain supported.
    with db._tx_lock:
        main = next(
            (row for row in db.conn().execute("PRAGMA database_list") if row[1] == "main"),
            None,
        )
    if main is None:
        raise RuntimeError("The Full Auto database identity is unavailable.")
    filename = main[2]
    if not filename:
        return ("memory", id(db.conn()))
    path = os.path.normcase(os.path.realpath(filename))
    stat = os.stat(path)
    return ("file", path, int(stat.st_dev), int(stat.st_ino))


def _register_local_assistant_dispatch(reservation_id: int) -> AssistantDispatchFence:
    fence = AssistantDispatchFence(reservation_id, _assistant_database_identity())
    with _LOCAL_ASSISTANT_DISPATCHES_LOCK:
        _LOCAL_ASSISTANT_DISPATCHES.add(fence)
    return fence


def finish_local_assistant_dispatch(fence: object) -> None:
    if not isinstance(fence, AssistantDispatchFence):
        return
    with _LOCAL_ASSISTANT_DISPATCHES_LOCK:
        _LOCAL_ASSISTANT_DISPATCHES.discard(fence)


def _local_assistant_dispatch_live(reservation_id: object) -> bool:
    if type(reservation_id) is not int:
        return False
    fence = AssistantDispatchFence(reservation_id, _assistant_database_identity())
    with _LOCAL_ASSISTANT_DISPATCHES_LOCK:
        return fence in _LOCAL_ASSISTANT_DISPATCHES


def _release_or_quarantine_slot(row: dict, *, now_text: str) -> None:
    """Release idle ownership, retaining an unsettled physical request fence."""
    reservation_id = row.get("inflight_reservation_id")
    if reservation_id is None:
        db.run(
            "UPDATE full_auto_assistant_slot SET state = 'released', lease_expires_at = NULL, "
            "updated_at = ? WHERE run_id = ? AND take_id = ?",
            now_text, row["run_id"], row["take_id"],
        )
        return
    locally_live = _local_assistant_dispatch_live(int(reservation_id))
    reservation = db.one(
        "SELECT dispatched_at, settled_at FROM full_auto_assistant_reservation WHERE reservation_id = ?",
        int(reservation_id),
    )
    if reservation is not None and reservation["settled_at"] is not None and not locally_live:
        db.run(
            "UPDATE full_auto_assistant_slot SET state = 'released', lease_expires_at = NULL, "
            "inflight_reservation_id = NULL, updated_at = ? WHERE run_id = ? AND take_id = ?",
            now_text, row["run_id"], row["take_id"],
        )
        return
    lease_deadline = row.get("lease_expires_at") or now_text
    if reservation is not None:
        timeout_deadline = (
            _timestamp(reservation["dispatched_at"], field_name="assistant dispatch")
            + ASSISTANT_DISPATCH_TIMEOUT + ASSISTANT_DISPATCH_TIMESTAMP_MARGIN
        ).isoformat(timespec="seconds")
        if _timestamp(timeout_deadline, field_name="assistant timeout") > _timestamp(
            lease_deadline, field_name="assistant lease",
        ):
            lease_deadline = timeout_deadline
    db.run(
        "UPDATE full_auto_assistant_slot SET state = 'quarantined', lease_expires_at = ?, "
        "updated_at = ? WHERE run_id = ? AND take_id = ?",
        lease_deadline, now_text, row["run_id"], row["take_id"],
    )


def _recover_assistant_slot(now_text: str) -> None:
    row = db.one(
        "SELECT * FROM full_auto_assistant_slot WHERE state IN ('active', 'quarantined') LIMIT 1",
    )
    if row is None:
        return
    now = _timestamp(now_text, field_name="current time")
    if row["state"] == "quarantined":
        deadline_expired = row["lease_expires_at"] is None or _timestamp(
            row["lease_expires_at"], field_name="assistant lease",
        ) <= now
        if deadline_expired and not _local_assistant_dispatch_live(
            row["inflight_reservation_id"],
        ):
            db.run(
                "UPDATE full_auto_assistant_slot SET state = 'released', lease_expires_at = NULL, "
                "inflight_reservation_id = NULL, updated_at = ? "
                "WHERE run_id = ? AND take_id = ? AND state = 'quarantined'",
                now_text, row["run_id"], row["take_id"],
            )
        return

    run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", row["run_id"])
    operation = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?", row["operation_id"],
    )
    parent_alive = run is not None and (
        run["state"] == "active" and run["phase"] == "prepare"
        and not bool(run["cancel_intent"])
        and int(run["epoch"]) == int(row["run_epoch"])
        and int(run["fencing_token"]) == int(row["run_fencing_token"])
        and run["lease_expires_at"] is not None
        and _timestamp(run["lease_expires_at"], field_name="run lease") > now
    )
    child_alive = operation is not None and (
        operation["state"] == "active"
        and int(operation["fencing_token"]) == int(row["operation_fencing_token"])
        and operation["lease_expires_at"] is not None
        and _timestamp(operation["lease_expires_at"], field_name="operation lease") > now
    )
    slot_alive = row["lease_expires_at"] is not None and _timestamp(
        row["lease_expires_at"], field_name="assistant lease",
    ) > now
    if not parent_alive or not child_alive or not slot_alive:
        _release_or_quarantine_slot(row, now_text=now_text)
        refreshed = db.one(
            "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            row["run_id"], row["take_id"],
        )
        if (
            refreshed is not None and refreshed["state"] == "quarantined"
            and refreshed["lease_expires_at"] is not None
            and _timestamp(refreshed["lease_expires_at"], field_name="assistant lease") <= now
            and not _local_assistant_dispatch_live(refreshed["inflight_reservation_id"])
        ):
            db.run(
                "UPDATE full_auto_assistant_slot SET state = 'released', lease_expires_at = NULL, "
                "inflight_reservation_id = NULL, updated_at = ? WHERE run_id = ? AND take_id = ?",
                now_text, row["run_id"], row["take_id"],
            )


def _discard_stale_assistant_waiters(now_text: str) -> None:
    """Remove expired or fenced waiters so they cannot hold FIFO priority."""
    now = _timestamp(now_text, field_name="current time")
    enabled = resource_planning.is_enabled()
    for slot in db.q(
        "SELECT * FROM full_auto_assistant_slot WHERE state = 'waiting' "
        "ORDER BY ready_at, run_id, rowid",
    ):
        run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", slot["run_id"])
        operation = db.one(
            "SELECT * FROM authoring_operation WHERE operation_id = ?", slot["operation_id"],
        )
        try:
            live = enabled and run is not None and operation is not None and (
                run["state"] == "active" and run["phase"] == "prepare"
                and not bool(run["cancel_intent"])
                and int(run["epoch"]) == int(slot["run_epoch"])
                and int(run["fencing_token"]) == int(slot["run_fencing_token"])
                and run["lease_expires_at"] is not None
                and _timestamp(run["lease_expires_at"], field_name="run lease") > now
                and operation["state"] == "active"
                and int(operation["session_id"]) == int(slot["session_id"])
                and int(operation["fencing_token"]) == int(slot["operation_fencing_token"])
                and operation["full_auto_run_id"] == slot["run_id"]
                and operation["full_auto_run_epoch"] == int(slot["run_epoch"])
                and operation["full_auto_run_fencing_token"] == int(slot["run_fencing_token"])
                and operation["lease_expires_at"] is not None
                and _timestamp(operation["lease_expires_at"], field_name="operation lease") > now
            )
            if live:
                live = _current_authority_issue(run, now_text) is None
        except (TypeError, ValueError, FullAutoRunError):
            live = False
        if not live:
            db.run(
                "UPDATE full_auto_assistant_slot SET state = 'released', updated_at = ? "
                "WHERE run_id = ? AND take_id = ? AND state = 'waiting'",
                now_text, slot["run_id"], slot["take_id"],
            )


def try_acquire_assistant_slot(
    run_claim: RunClaim,
    operation_claim: Any,
    take_id: str,
    provider_identity: object,
    *,
    now_text: str | None = None,
) -> AssistantSlotClaim | None:
    """Persist FIFO readiness and acquire the one cross-run Full Auto slot."""
    if type(take_id) is not str or not take_id:
        raise FullAutoRunError(422, "full_auto_target_not_ready", "The prepared take identity is invalid.")
    with db.transaction():
        timestamp = _now_text(None)
        run, operation, _grant, _authorization, timestamp = _fresh_assistant_owner_rows(
            run_claim, operation_claim, take_id, provider_identity, now_text=timestamp,
        )
        existing = db.one(
            "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            run_claim.run_id, take_id,
        )
        if existing is None:
            _run, _operation, _grant, _authorization, timestamp = _fresh_assistant_owner_rows(
                run_claim, operation_claim, take_id, provider_identity,
            )
            generation = 1
            db.run(
                """INSERT INTO full_auto_assistant_slot
                   (run_id, session_id, take_id, operation_id, ready_at, generation, state,
                    run_epoch, run_fencing_token, operation_fencing_token, lease_expires_at,
                    inflight_reservation_id, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?, NULL, NULL, ?)""",
                run_claim.run_id, run_claim.session_id, take_id, operation_claim.operation_id,
                timestamp, generation, run_claim.epoch, run_claim.fencing_token,
                operation_claim.fencing_token, timestamp,
            )
        elif existing["state"] in {"released", "completed"} or (
            existing["state"] == "waiting" and (
                existing["operation_id"] != operation_claim.operation_id
                or int(existing["run_epoch"]) != run_claim.epoch
                or int(existing["run_fencing_token"]) != run_claim.fencing_token
                or int(existing["operation_fencing_token"]) != operation_claim.fencing_token
            )
        ):
            _run, _operation, _grant, _authorization, timestamp = _fresh_assistant_owner_rows(
                run_claim, operation_claim, take_id, provider_identity,
            )
            generation = int(existing["generation"]) + 1
            db.run(
                """UPDATE full_auto_assistant_slot
                   SET session_id = ?, operation_id = ?, ready_at = ?, generation = ?,
                       state = 'waiting', run_epoch = ?, run_fencing_token = ?,
                       operation_fencing_token = ?, lease_expires_at = NULL,
                       inflight_reservation_id = NULL, updated_at = ?
                   WHERE run_id = ? AND take_id = ?
                     AND state IN ('released', 'completed', 'waiting')""",
                run_claim.session_id, operation_claim.operation_id, timestamp, generation,
                run_claim.epoch, run_claim.fencing_token, operation_claim.fencing_token,
                timestamp, run_claim.run_id, take_id,
            )

        timestamp = _now_text(None)
        _recover_assistant_slot(timestamp)
        _discard_stale_assistant_waiters(timestamp)
        current = db.one(
            "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            run_claim.run_id, take_id,
        )
        if current is None:
            raise FullAutoRunError(500, "assistant_slot_invalid", "The assistant slot could not be read.")
        if current["state"] == "active":
            return None
        if current["state"] != "waiting":
            return None
        occupied = db.one(
            "SELECT run_id, take_id FROM full_auto_assistant_slot "
            "WHERE state IN ('active', 'quarantined') LIMIT 1",
        )
        if occupied is not None:
            return None
        first = db.one(
            "SELECT * FROM full_auto_assistant_slot WHERE state = 'waiting' "
            "ORDER BY ready_at, run_id, rowid LIMIT 1",
        )
        if first is None or first["run_id"] != run_claim.run_id or first["take_id"] != take_id:
            return None
        _run, _operation, _grant, _authorization, timestamp = _fresh_assistant_owner_rows(
            run_claim, operation_claim, take_id, provider_identity,
        )
        _discard_stale_assistant_waiters(timestamp)
        current = db.one(
            "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            run_claim.run_id, take_id,
        )
        occupied = db.one(
            "SELECT run_id, take_id FROM full_auto_assistant_slot "
            "WHERE state IN ('active', 'quarantined') LIMIT 1",
        )
        first = db.one(
            "SELECT * FROM full_auto_assistant_slot WHERE state = 'waiting' "
            "ORDER BY ready_at, run_id, rowid LIMIT 1",
        )
        if (
            current is None or current["state"] != "waiting" or occupied is not None
            or first is None or first["run_id"] != run_claim.run_id
            or first["take_id"] != take_id
        ):
            return None
        timestamp = _now_text(None)
        _run, _operation, _grant, _authorization, timestamp = _fresh_assistant_owner_rows(
            run_claim, operation_claim, take_id, provider_identity, now_text=timestamp,
        )
        deadline = (
            _timestamp(timestamp, field_name="current time") + ASSISTANT_SLOT_LEASE
        ).isoformat(timespec="seconds")
        changed = db.conn().execute(
            """UPDATE full_auto_assistant_slot SET state = 'active', lease_expires_at = ?, updated_at = ?
               WHERE run_id = ? AND take_id = ? AND state = 'waiting' AND generation = ?""",
            (deadline, timestamp, run_claim.run_id, take_id, current["generation"]),
        ).rowcount
        if changed != 1:
            return None
        return _make_assistant_slot_claim(db.one(
            "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            run_claim.run_id, take_id,
        ))


def _require_live_assistant_slot(
    slot_claim: AssistantSlotClaim,
    run_claim: RunClaim,
    operation_claim: Any,
    provider_identity: object,
    *,
    now_text: str | None,
) -> tuple[dict, dict, dict, dict, dict, str]:
    if not _authentic_assistant_slot_claim(slot_claim):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto assistant slot is stale.")
    run, operation, grant, authorization, checked_at = _fresh_assistant_owner_rows(
        run_claim, operation_claim, slot_claim.take_id, provider_identity,
        now_text=now_text,
    )
    row = _assistant_slot_row(slot_claim)
    if row is None or (
        row["state"] != "active"
        or row["operation_id"] != operation_claim.operation_id
        or int(row["generation"]) != slot_claim.generation
        or int(row["run_epoch"]) != run_claim.epoch
        or int(row["run_fencing_token"]) != run_claim.fencing_token
        or int(row["operation_fencing_token"]) != operation_claim.fencing_token
        or row["lease_expires_at"] != slot_claim.lease_expires_at
        or _timestamp(row["lease_expires_at"], field_name="assistant lease")
        <= _timestamp(checked_at, field_name="current time")
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto assistant slot is stale.")
    return run, operation, grant, authorization, row, checked_at


def renew_assistant_slot(
    slot_claim: AssistantSlotClaim,
    run_claim: RunClaim,
    operation_claim: Any,
    provider_identity: object,
    *,
    now_text: str | None = None,
) -> AssistantSlotClaim:
    with db.transaction():
        timestamp = _now_text(None)
        _run, _operation, _grant, _authorization, row, timestamp = _require_live_assistant_slot(
            slot_claim, run_claim, operation_claim, provider_identity, now_text=timestamp,
        )
        deadline = (
            _timestamp(timestamp, field_name="current time") + ASSISTANT_SLOT_LEASE
        ).isoformat(timespec="seconds")
        changed = db.conn().execute(
            "UPDATE full_auto_assistant_slot SET lease_expires_at = ?, updated_at = ? "
            "WHERE run_id = ? AND take_id = ? AND generation = ? AND state = 'active' "
            "AND lease_expires_at = ? AND inflight_reservation_id IS ?",
            (deadline, timestamp, slot_claim.run_id, slot_claim.take_id, slot_claim.generation,
             row["lease_expires_at"], row["inflight_reservation_id"]),
        ).rowcount
        if changed != 1:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto assistant slot is stale.")
        return _make_assistant_slot_claim(db.one(
            "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            slot_claim.run_id, slot_claim.take_id,
        ))


def _safe_assistant_response_metadata(metadata: object) -> dict:
    if type(metadata) is not dict or set(metadata) - {
        "status", "retry_after", "structured_refusal", "content_filter", "usage",
    }:
        raise FullAutoRunError(500, "assistant_metadata_invalid", "Assistant transport metadata is invalid.")
    result: dict[str, object] = {}
    status = metadata.get("status")
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise FullAutoRunError(500, "assistant_metadata_invalid", "Assistant transport metadata is invalid.")
    result["status"] = status
    retry_after = metadata.get("retry_after")
    if retry_after is not None:
        if (
            type(retry_after) is not str or len(retry_after) > 128
            or any(not char.isprintable() for char in retry_after)
        ):
            raise FullAutoRunError(500, "assistant_metadata_invalid", "Assistant transport metadata is invalid.")
        result["retry_after"] = retry_after
    for name in ("structured_refusal", "content_filter"):
        value = metadata.get(name)
        if value is not None:
            if type(value) is not bool:
                raise FullAutoRunError(500, "assistant_metadata_invalid", "Assistant transport metadata is invalid.")
            result[name] = value
    usage = metadata.get("usage")
    if usage is not None:
        allowed = {"prompt_tokens", "completion_tokens", "total_tokens"}
        if type(usage) is not dict or set(usage) - allowed or any(
            type(value) is not int or value < 0 for value in usage.values()
        ):
            raise FullAutoRunError(500, "assistant_metadata_invalid", "Assistant transport metadata is invalid.")
        result["usage"] = usage
    return result


def reserve_assistant_dispatch(
    slot_claim: AssistantSlotClaim,
    run_claim: RunClaim,
    operation_claim: Any,
    provider_identity: object,
    *,
    now_text: str | None = None,
) -> AssistantDispatchFence:
    """Durably consume one call immediately before the physical POST."""
    fence = None
    try:
        with db.transaction():
            timestamp = _now_text(None)
            _run, _operation, grant, authorization, row, _checked_at = _require_live_assistant_slot(
                slot_claim, run_claim, operation_claim, provider_identity, now_text=timestamp,
            )
            if row["inflight_reservation_id"] is not None:
                raise FullAutoRunError(409, "assistant_slot_busy", "The Full Auto assistant slot is busy.")
            limits = authorization["limits"]
            sequence = int(grant["sequence"])
            per_take = int(db.one(
                """SELECT COUNT(*) AS count FROM full_auto_assistant_reservation
                   WHERE run_id = ? AND authorization_sequence = ? AND take_id = ?""",
                run_claim.run_id, sequence, slot_claim.take_id,
            )["count"])
            grant_total = int(db.one(
                """SELECT COUNT(*) AS count FROM full_auto_assistant_reservation
                   WHERE run_id = ? AND authorization_sequence = ?""",
                run_claim.run_id, sequence,
            )["count"])
            cumulative_reserved = int(db.one(
                "SELECT COUNT(*) AS count FROM full_auto_assistant_reservation WHERE run_id = ?",
                run_claim.run_id,
            )["count"])
            grant_rows = [db.one(
                "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
                run_claim.run_id,
            )]
            grant_rows.extend(db.q(
                "SELECT authorization_json FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
                run_claim.run_id,
            ))
            cumulative_limit = 0
            for item in grant_rows:
                if item is None:
                    continue
                try:
                    raw = json.loads(item["authorization_json"])
                    _grant_summary, grant_limits = _validate_status_authorization(raw)
                except (TypeError, KeyError, json.JSONDecodeError, FullAutoRunError) as exc:
                    raise FullAutoRunError(
                        409, "full_auto_authorization_invalid", "The Full Auto authorization chain is invalid.",
                    ) from exc
                cumulative_limit += int(grant_limits["assistant_calls_total"])
            if (
                per_take >= int(limits["assistant_calls_per_take"])
                or grant_total >= int(limits["assistant_calls_total"])
                or cumulative_reserved >= cumulative_limit
            ):
                raise FullAutoRunError(409, "budget_exhausted", "The authorized assistant call allowance is exhausted.")
            request_number = per_take + 1
            timestamp = _now_text(None)
            _run, _operation, _grant, _authorization, row, timestamp = _require_live_assistant_slot(
                slot_claim, run_claim, operation_claim, provider_identity, now_text=timestamp,
            )
            if row["inflight_reservation_id"] is not None:
                raise FullAutoRunError(409, "assistant_slot_busy", "The Full Auto assistant slot is busy.")
            cursor = db.conn().execute(
                """INSERT INTO full_auto_assistant_reservation
                   (run_id, authorization_sequence, session_id, take_id, operation_id,
                    slot_generation, request_number, dispatched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_claim.run_id, sequence, run_claim.session_id, slot_claim.take_id,
                 operation_claim.operation_id, slot_claim.generation, request_number, timestamp),
            )
            reservation_id = int(cursor.lastrowid)
            timestamp = _now_text(None)
            _run, _operation, _grant, _authorization, row, timestamp = _require_live_assistant_slot(
                slot_claim, run_claim, operation_claim, provider_identity, now_text=timestamp,
            )
            if row["inflight_reservation_id"] is not None:
                raise FullAutoRunError(409, "assistant_slot_busy", "The Full Auto assistant slot is busy.")
            updated = db.conn().execute(
                """UPDATE full_auto_assistant_slot SET inflight_reservation_id = ?, updated_at = ?
                   WHERE run_id = ? AND take_id = ? AND generation = ? AND state = 'active'
                     AND inflight_reservation_id IS NULL""",
                (reservation_id, timestamp, slot_claim.run_id, slot_claim.take_id, slot_claim.generation),
            ).rowcount
            if updated != 1:
                raise FullAutoRunError(409, "assistant_slot_busy", "The Full Auto assistant slot is busy.")
            fence = _register_local_assistant_dispatch(reservation_id)
    except BaseException:
        if fence is not None:
            finish_local_assistant_dispatch(fence)
        raise
    return fence


def settle_assistant_dispatch(
    slot_claim: AssistantSlotClaim,
    reservation_id: int | AssistantDispatchFence,
    metadata: object,
    *,
    now_text: str | None = None,
) -> None:
    """Record only safe transport facts, including an observed failed response."""
    if isinstance(reservation_id, AssistantDispatchFence):
        if reservation_id.database_identity != _assistant_database_identity():
            return
        reservation_id = reservation_id.reservation_id
    if type(reservation_id) is not int:
        raise FullAutoRunError(500, "assistant_reservation_invalid", "The assistant reservation is invalid.")
    safe = _safe_assistant_response_metadata(metadata)
    timestamp = _now_text(now_text)
    encoded = _canonical_json(safe)
    with db.transaction():
        row = db.one(
            """SELECT * FROM full_auto_assistant_reservation
               WHERE reservation_id = ? AND run_id = ? AND take_id = ? AND slot_generation = ?""",
            reservation_id, slot_claim.run_id, slot_claim.take_id, slot_claim.generation,
        )
        if row is None or row["settled_at"] is not None:
            return
        db.run(
            "UPDATE full_auto_assistant_reservation SET settled_at = ?, response_metadata_json = ? "
            "WHERE reservation_id = ? AND settled_at IS NULL",
            timestamp, encoded, reservation_id,
        )
        slot = _assistant_slot_row(slot_claim)
        if slot is None or int(slot["generation"]) != slot_claim.generation:
            return
        if slot["inflight_reservation_id"] != reservation_id:
            return
        if slot["state"] == "quarantined":
            db.run(
                "UPDATE full_auto_assistant_slot SET state = 'released', lease_expires_at = NULL, "
                "inflight_reservation_id = NULL, updated_at = ? WHERE run_id = ? AND take_id = ?",
                timestamp, slot_claim.run_id, slot_claim.take_id,
            )
        else:
            db.run(
                "UPDATE full_auto_assistant_slot SET inflight_reservation_id = NULL, updated_at = ? "
                "WHERE run_id = ? AND take_id = ? AND generation = ? AND inflight_reservation_id = ?",
                timestamp, slot_claim.run_id, slot_claim.take_id,
                slot_claim.generation, reservation_id,
            )


def check_assistant_response(
    slot_claim: AssistantSlotClaim,
    run_claim: RunClaim,
    operation_claim: Any,
    provider_identity: object,
    *,
    now_text: str | None = None,
) -> None:
    with db.transaction():
        _require_live_assistant_slot(
            slot_claim, run_claim, operation_claim, provider_identity,
            now_text=_now_text(now_text),
        )


def release_assistant_slot(
    slot_claim: AssistantSlotClaim,
    *,
    completed: bool,
    now_text: str | None = None,
) -> None:
    """Yield after a take settles, or quarantine if its request is still live."""
    if not _authentic_assistant_slot_claim(slot_claim) or type(completed) is not bool:
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto assistant slot is stale.")
    timestamp = _now_text(now_text)
    with db.transaction():
        row = _assistant_slot_row(slot_claim)
        if row is None or int(row["generation"]) != slot_claim.generation:
            return
        if row["inflight_reservation_id"] is not None:
            _release_or_quarantine_slot(row, now_text=timestamp)
            return
        db.run(
            "UPDATE full_auto_assistant_slot SET state = ?, lease_expires_at = NULL, "
            "updated_at = ? WHERE run_id = ? AND take_id = ? AND generation = ?",
            "completed" if completed else "released", timestamp,
            slot_claim.run_id, slot_claim.take_id, slot_claim.generation,
        )


def abandon_assistant_slot_waiter(
    run_claim: RunClaim,
    operation_claim: Any,
    take_id: str,
    *,
    now_text: str | None = None,
) -> None:
    """Drop FIFO readiness when its local worker coroutine is cancelled."""
    timestamp = _now_text(now_text)
    with db.transaction():
        db.run(
            "UPDATE full_auto_assistant_slot SET state = 'released', updated_at = ? "
            "WHERE run_id = ? AND take_id = ? AND operation_id = ? AND state = 'waiting' "
            "AND run_epoch = ? AND run_fencing_token = ? AND operation_fencing_token = ?",
            timestamp, run_claim.run_id, take_id, operation_claim.operation_id,
            run_claim.epoch, run_claim.fencing_token, operation_claim.fencing_token,
        )


def quarantine_run_assistant_slots(run_id: str, *, now_text: str) -> None:
    """Fence slots owned by a run before a replacement coordinator takes over."""
    with db.transaction():
        rows = db.q(
            "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND state = 'active'",
            run_id,
        )
        for row in rows:
            _release_or_quarantine_slot(row, now_text=now_text)


def has_live_cancelled_assistant_dispatch(claim: RunClaim) -> bool:
    """Return whether cancellation still owns a local physical assistant POST."""
    timestamp = _now_text(None)
    with db.transaction():
        run = _require_claim(claim, now_text=timestamp, reconciliation=True)
        if not bool(run["cancel_intent"]) or run["state"] not in {"blocked", "cancel_requested"}:
            raise FullAutoRunError(
                409, "cancel_reconciliation_required",
                "The run has no pending cancellation to reconcile.",
            )
        reservations = db.q(
            "SELECT reservation_id FROM full_auto_assistant_reservation "
            "WHERE run_id = ?",
            claim.run_id,
        )
    return any(
        _local_assistant_dispatch_live(int(item["reservation_id"]))
        for item in reservations
    )


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


def require_resumable_prepare_child(
    claim: RunClaim,
    operation: dict,
    *,
    now_text: str | None = None,
) -> dict:
    """Authorize rebinding one expired registered child to the current owner."""
    timestamp = _now_text(now_text)
    row = require_prepare_claim(
        claim, session_id=int(operation["session_id"]), now_text=timestamp,
    )
    if (
        operation.get("state") not in {"expired", "failed"}
        or operation.get("kind") != "prepare_takes"
        or operation.get("full_auto_run_id") != claim.run_id
        or type(operation.get("full_auto_run_epoch")) is not int
        or type(operation.get("full_auto_run_fencing_token")) is not int
        or int(operation["plan_revision"]) != int(row["plan_revision"])
    ):
        raise FullAutoRunError(409, "prepare_child_owner_invalid", "Only a terminal retryable child of this Full Auto run can be resumed.")

    progress = _read_prepare_progress(claim.run_id)
    child = next(
        (item for item in progress["children"] if item["operation_id"] == operation["operation_id"]),
        None,
    )
    requested = _prepare_json_list(operation["requested_json"], field_name="operation targets")
    completed = _prepare_json_list(operation["completed_json"], field_name="operation progress")
    if child is None or child["target_ids"] != requested:
        raise FullAutoRunError(409, "prepare_child_missing", "The expired operation is not the registered Full Auto child.")
    if completed != requested[:len(completed)]:
        raise FullAutoRunError(409, "prepare_progress_invalid", "The expired child progress is not an ordered prefix.")

    child_start = progress["target_ids"].index(requested[0])
    cursor = len(progress["completed_take_ids"])
    adopted_count = cursor - child_start
    if (
        adopted_count < 0
        or adopted_count >= len(requested)
        or progress["target_ids"][child_start:child_start + len(requested)] != requested
        or len(completed) > adopted_count
    ):
        raise FullAutoRunError(409, "prepare_progress_stale", "The expired child no longer matches the first uncheckpointed target range.")
    return {"progress": progress, "child": child, "child_start": child_start}


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
    target_positions = {take_id: index for index, take_id in enumerate(targets)}
    previous_child_end: int | None = None
    for child in db.q(
        "SELECT batch_index, operation_id, target_ids_json FROM full_auto_prepare_child "
        "WHERE run_id = ? ORDER BY batch_index",
        run_id,
    ):
        child_targets = _prepare_json_list(child["target_ids_json"], field_name="child targets")
        if (
            type(child["batch_index"]) is not int
            or child["batch_index"] != len(children)
            or not child_targets
            or len(child_targets) > 20
            or type(child["operation_id"]) is not str
            or not child["operation_id"]
        ):
            raise FullAutoRunError(500, "run_state_invalid", "Saved Full Auto child preparation is invalid.")
        child_start = target_positions.get(child_targets[0])
        if (
            child_start is None
            or targets[child_start:child_start + len(child_targets)] != child_targets
            or (previous_child_end is not None and child_start != previous_child_end)
        ):
            raise FullAutoRunError(500, "run_state_invalid", "Saved Full Auto child ranges overlap or are out of order.")
        previous_child_end = child_start + len(child_targets)
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


def _authoritative_plan_targets(session_id: int, photo_count: int) -> tuple[int, list[str]]:
    from backend import session_plan

    row = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    if row is None or type(row["plan_revision"]) is not int or row["plan_revision"] <= 0:
        raise _invalid_saved_status()
    try:
        plan = session_plan.validate_draft(json.loads(row["plan_json"]))
    except (TypeError, json.JSONDecodeError, session_plan.PlanValidationError) as exc:
        raise _invalid_saved_status() from exc
    targets = [take.get("take_id") for take in plan.get("takes", []) if type(take) is dict]
    if (
        len(targets) != photo_count
        or not targets
        or len(targets) > 500
        or any(type(value) is not str or not value for value in targets)
        or len(targets) != len(set(targets))
    ):
        raise _invalid_saved_status()
    return int(row["plan_revision"]), targets


def _validated_prepare_accounting(
    run_row: dict,
    progress_row: dict | None,
    progress: dict | None,
    authoritative_targets: list[str],
) -> set[str]:
    """Return targets whose authorized assistant allowance may be consumed."""
    run_id = run_row["run_id"]
    session_id = int(run_row["session_id"])
    if progress_row is None:
        if db.one("SELECT 1 FROM full_auto_prepare_child WHERE run_id = ?", run_id) is not None:
            raise _invalid_saved_preparation()
        if db.one(
            "SELECT 1 FROM authoring_operation WHERE full_auto_run_id = ? AND kind = 'prepare_takes'",
            run_id,
        ) is not None:
            raise _invalid_saved_preparation()
        return set()
    if (
        progress is None
        or type(progress_row["plan_revision"]) is not int
        or progress_row["plan_revision"] != int(run_row["plan_revision"])
        or progress["target_ids"] != authoritative_targets
    ):
        raise _invalid_saved_preparation()
    target_set = set(authoritative_targets)
    started = set(progress["completed_take_ids"])
    child_ids = {child["operation_id"] for child in progress["children"]}
    linked_operation_ids = {
        row["operation_id"] for row in db.q(
            "SELECT operation_id FROM authoring_operation WHERE full_auto_run_id = ? AND kind = 'prepare_takes'",
            run_id,
        )
    }
    if child_ids != linked_operation_ids:
        raise _invalid_saved_preparation()
    seen_child_targets: set[str] = set()
    for child in progress["children"]:
        child_targets = child["target_ids"]
        if (
            any(target not in target_set or target in seen_child_targets for target in child_targets)
        ):
            raise _invalid_saved_preparation()
        seen_child_targets.update(child_targets)
        operation = db.one(
            "SELECT * FROM authoring_operation WHERE operation_id = ?",
            child["operation_id"],
        )
        if (
            operation is None
            or type(operation["state"]) is not str
            or operation["state"] not in {"active", "cancel_requested", "succeeded", "failed", "cancelled", "expired"}
            or type(operation["session_id"]) is not int
            or operation["session_id"] != session_id
            or type(operation["plan_revision"]) is not int
            or operation["plan_revision"] != progress_row["plan_revision"]
            or operation["kind"] != "prepare_takes"
            or operation["full_auto_run_id"] != run_id
        ):
            raise _invalid_saved_preparation()
        requested = _prepare_json_list(operation["requested_json"], field_name="operation targets")
        completed = _prepare_json_list(operation["completed_json"], field_name="operation progress")
        remaining = _prepare_json_list(operation["remaining_json"], field_name="operation remaining targets")
        failed = operation["failed_item"]
        if (
            requested != child_targets
            or any(target not in target_set for target in requested)
            or (failed is not None and (type(failed) is not str or failed not in requested))
            or requested != completed + ([failed] if failed is not None else []) + remaining
        ):
            raise _invalid_saved_preparation()
        if operation["state"] == "succeeded":
            if failed is not None or remaining or completed != requested:
                raise _invalid_saved_preparation()
            started.update(requested)
        elif operation["state"] == "failed":
            started.update(completed)
            if failed is None:
                # The exact failed target is unavailable; conservatively spend
                # this child's allowance until reservation journals exist.
                started.update(requested)
            else:
                started.add(failed)
        else:
            # An interrupted or in-flight child may already have dispatched any
            # target in its batch, so none of that batch's budget is reusable.
            started.update(requested)
    if not started.issubset(target_set):
        raise _invalid_saved_preparation()
    return started


def _read_submission_receipts(
    run_id: str,
    session_id: int,
    plan_revision: int,
    target_ids: list[str],
) -> list[dict]:
    from backend import session_plan

    receipts = db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id = ? ORDER BY target_index",
        run_id,
    )
    if len(receipts) > len(target_ids):
        raise FullAutoRunError(500, "submission_receipt_invalid", "Saved Full Auto submission receipts are invalid.")
    for index, receipt in enumerate(receipts):
        prepared = db.one(
            "SELECT * FROM prepared_take WHERE id = ?", receipt["prepared_take_id"],
        )
        shot = db.one(
            "SELECT session_id, prompt FROM shot WHERE id = ?", receipt["shot_id"],
        )
        if (
            type(receipt["target_index"]) is not int
            or receipt["target_index"] != index
            or receipt["take_id"] != target_ids[index]
            or prepared is None
            or int(prepared["session_id"]) != session_id
            or int(prepared["plan_revision"]) != plan_revision
            or prepared["take_id"] != target_ids[index]
            or prepared["status"] != session_plan.PREPARED_TAKE_STATUS_GENERATED
            or prepared["linked_shot_id"] != receipt["shot_id"]
            or shot is None
            or int(shot["session_id"]) != session_id
            or shot["prompt"] != prepared["final_prompt"]
            or receipt["snapshot_digest"]
            != session_plan._full_auto_snapshot_reference(prepared)["snapshot_digest"]
        ):
            raise FullAutoRunError(500, "submission_receipt_invalid", "A Full Auto submission receipt no longer matches its linked shot.")
    return receipts


def submission_progress(claim: RunClaim) -> dict:
    """Return the ordered submission receipt prefix for the current submit owner."""
    with db.transaction():
        row = require_session_mutation_allowed(
            claim.session_id, claim=claim, allowed_phases=("submit",),
        )
        if row is None:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto submission owner is stale.")
        progress = _read_prepare_progress(claim.run_id)
        if progress["completed_take_ids"] != progress["target_ids"]:
            raise FullAutoRunError(409, "prepare_progress_stale", "Full Auto preparation has not completed every target.")
        receipts = _read_submission_receipts(
            claim.run_id, claim.session_id, int(row["plan_revision"]), progress["target_ids"],
        )
        return {
            "target_ids": progress["target_ids"],
            "receipts": receipts,
            "completed_count": len(receipts),
            "plan_revision": int(row["plan_revision"]),
        }


def record_submission_receipt(
    claim: RunClaim,
    target_index: int,
    take_id: str,
    shot_id: int,
    *,
    now_text: str | None = None,
) -> dict:
    """Checkpoint one already-committed prepared-take link in target order."""
    from backend import session_plan

    timestamp = _now_text(now_text)
    if type(target_index) is not int or target_index < 0 or not isinstance(take_id, str) or type(shot_id) is not int:
        raise FullAutoRunError(422, "submission_receipt_invalid", "The Full Auto submission receipt is invalid.")
    with db.transaction():
        row = require_session_mutation_allowed(
            claim.session_id, claim=claim, allowed_phases=("submit",),
        )
        if row is None:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto submission owner is stale.")
        plan_revision = int(row["plan_revision"])
        session_plan._require_resource_planning_enabled()
        progress = _read_prepare_progress(claim.run_id)
        if (
            progress["completed_take_ids"] != progress["target_ids"]
            or target_index >= len(progress["target_ids"])
            or progress["target_ids"][target_index] != take_id
        ):
            raise FullAutoRunError(409, "submission_target_stale", "The Full Auto submission target order changed.")
        current_revision, plan = session_plan._load_current_resource_plan(claim.session_id)
        if current_revision != plan_revision:
            raise FullAutoRunError(409, "plan_revision_stale", "The Full Auto plan changed before its submission receipt was saved.")
        review = session_plan._full_auto_duplicate_review_evidence(
            claim.session_id, current_revision, plan, claim.run_id,
        )
        session_plan._require_full_auto_review_ready(review)
        if review["target_ids"] != progress["target_ids"]:
            raise FullAutoRunError(409, "submission_target_stale", "The Full Auto target order changed before its submission receipt was saved.")
        try:
            session_plan._require_current_full_auto_policy_approval(
                claim.run_id, claim.session_id, review,
            )
        except session_plan.PlanReviewNotApproved as exc:
            raise FullAutoRunError(409, "full_auto_approval_stale", "The Full Auto approval no longer matches current evidence.") from exc
        receipts = _read_submission_receipts(
            claim.run_id, claim.session_id, plan_revision, progress["target_ids"],
        )
        if target_index < len(receipts):
            receipt = receipts[target_index]
            if receipt["shot_id"] == shot_id:
                return receipt
            raise FullAutoRunError(409, "submission_receipt_conflict", "The Full Auto target is already linked to a different shot.")
        if target_index != len(receipts):
            raise FullAutoRunError(409, "submission_order_invalid", "Full Auto submissions must be checkpointed in target order.")

        prepared = db.one(
            "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
            claim.session_id, plan_revision, take_id,
        )
        shot = db.one(
            "SELECT session_id, prompt FROM shot WHERE id = ?", shot_id,
        )
        if (
            prepared is None
            or prepared["status"] != session_plan.PREPARED_TAKE_STATUS_GENERATED
            or prepared["linked_shot_id"] != shot_id
            or shot is None
            or int(shot["session_id"]) != claim.session_id
            or shot["prompt"] != prepared["final_prompt"]
        ):
            raise FullAutoRunError(409, "linked_snapshot_invalid", "The submitted shot does not match the prepared target.")
        session_plan._require_resource_planning_enabled()
        row = require_session_mutation_allowed(
            claim.session_id, claim=claim, allowed_phases=("submit",),
        )
        if row is None or int(row["plan_revision"]) != plan_revision:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto submission owner is stale.")
        db.run(
            "INSERT INTO full_auto_submission_receipt "
            "(run_id, target_index, take_id, prepared_take_id, shot_id, snapshot_digest, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            claim.run_id, target_index, take_id, prepared["id"], shot_id,
            session_plan._full_auto_snapshot_reference(prepared)["snapshot_digest"], timestamp,
        )
        return _read_submission_receipts(
            claim.run_id, claim.session_id, plan_revision, progress["target_ids"],
        )[target_index]


def admit_runner_start(session_id: int, claim: RunClaim) -> dict:
    """Persist one safe runner admission before its in-process task is scheduled."""
    from backend import session_plan

    with db.transaction():
        row = require_session_mutation_allowed(
            session_id, claim=claim, allowed_phases=("execute",),
        )
        if row is None:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution owner is stale.")
        plan_revision = int(row["plan_revision"])
        if db.one(
            "SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", claim.run_id,
        ) is not None:
            raise FullAutoRunError(
                409, "full_auto_runner_already_admitted",
                "The Full Auto runner was already admitted; its execution outcome requires reconciliation.",
            )
        session_plan._require_resource_planning_enabled()
        current_revision, plan = session_plan._load_current_resource_plan(session_id)
        if current_revision != plan_revision:
            raise FullAutoRunError(409, "plan_revision_stale", "The Full Auto plan changed before execution.")
        progress = _read_prepare_progress(claim.run_id)
        if progress["completed_take_ids"] != progress["target_ids"]:
            raise FullAutoRunError(409, "prepare_progress_stale", "Full Auto preparation has not completed every target.")
        review = session_plan._full_auto_duplicate_review_evidence(
            session_id, current_revision, plan, claim.run_id,
        )
        session_plan._require_full_auto_review_ready(review)
        if review["target_ids"] != progress["target_ids"]:
            raise FullAutoRunError(409, "submission_target_stale", "The Full Auto target order changed before execution.")
        try:
            session_plan._require_current_full_auto_policy_approval(
                claim.run_id, session_id, review,
            )
        except session_plan.PlanReviewNotApproved as exc:
            raise FullAutoRunError(409, "full_auto_approval_stale", "The Full Auto approval no longer matches current evidence.") from exc
        receipts = _read_submission_receipts(
            claim.run_id, claim.session_id, plan_revision, progress["target_ids"],
        )
        if len(receipts) != len(progress["target_ids"]):
            raise FullAutoRunError(409, "full_auto_submission_incomplete", "Every Full Auto target must be linked before execution.")
        shot_rows = db.q(
            "SELECT id, status FROM shot WHERE session_id = ? ORDER BY id",
            session_id,
        )
        if (
            len(shot_rows) != len(receipts)
            or {item["id"] for item in shot_rows} != {item["shot_id"] for item in receipts}
            or [item["id"] for item in shot_rows] != [item["shot_id"] for item in receipts]
            or any(item["status"] != "pending" for item in shot_rows)
        ):
            raise FullAutoRunError(409, "full_auto_execution_state_stale", "The Full Auto session no longer contains only its linked pending shots.")
        submission_digest = _digest({
            "run_id": claim.run_id,
            "plan_revision": plan_revision,
            "receipts": [
                {
                    key: receipt[key]
                    for key in (
                        "target_index", "take_id", "prepared_take_id", "shot_id",
                        "snapshot_digest",
                    )
                }
                for receipt in receipts
            ],
        })
        session_plan._require_resource_planning_enabled()
        row = require_session_mutation_allowed(
            session_id, claim=claim, allowed_phases=("execute",),
        )
        if row is None or int(row["plan_revision"]) != plan_revision:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution owner is stale.")
        db.run(
            "INSERT INTO full_auto_runner_admission "
            "(run_id, session_id, plan_revision, target_digest, submission_digest, admitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            claim.run_id, session_id, plan_revision,
            review["target_digest"], submission_digest, _now_text(None),
        )
        return row


def validate_runner_recovery(session_id: int, claim: RunClaim) -> dict:
    """Validate the existing admission before resuming its journaled attempts."""
    from backend import session_plan

    with db.transaction():
        row = require_session_mutation_allowed(
            session_id, claim=claim, allowed_phases=("execute",),
        )
        admission = db.one(
            "SELECT * FROM full_auto_runner_admission WHERE run_id = ?",
            claim.run_id,
        )
        if row is None or row["run_id"] != claim.run_id or admission is None:
            raise FullAutoRunError(
                409, "full_auto_execution_state_stale",
                "The Full Auto runner admission is unavailable for recovery.",
            )
        session_plan._require_resource_planning_enabled()
        _validate_execution_dependencies(row)
        plan_revision = int(row["plan_revision"])
        current_revision, plan = session_plan._load_current_resource_plan(session_id)
        if current_revision != plan_revision or int(admission["plan_revision"]) != plan_revision:
            raise FullAutoRunError(
                409, "plan_revision_stale", "The Full Auto plan changed before execution recovery.",
            )
        progress = _read_prepare_progress(claim.run_id)
        if progress["completed_take_ids"] != progress["target_ids"]:
            raise FullAutoRunError(
                409, "prepare_progress_stale", "Full Auto preparation is incomplete.",
            )
        review = session_plan._full_auto_duplicate_review_evidence(
            session_id, current_revision, plan, claim.run_id,
        )
        session_plan._require_full_auto_review_ready(review)
        session_plan._require_current_full_auto_policy_approval(
            claim.run_id, session_id, review,
        )
        receipts = _read_submission_receipts(
            claim.run_id, session_id, plan_revision, progress["target_ids"],
        )
        if len(receipts) != len(progress["target_ids"]):
            raise FullAutoRunError(
                409, "full_auto_submission_incomplete",
                "Every Full Auto target must be linked before execution recovery.",
            )
        submission_digest = _digest({
            "run_id": claim.run_id,
            "plan_revision": plan_revision,
            "receipts": [
                {key: item[key] for key in (
                    "target_index", "take_id", "prepared_take_id", "shot_id", "snapshot_digest",
                )}
                for item in receipts
            ],
        })
        shots = db.q("SELECT id, status FROM shot WHERE session_id = ? ORDER BY id", session_id)
        if (
            admission["session_id"] != session_id
            or admission["target_digest"] != review["target_digest"]
            or admission["submission_digest"] != submission_digest
            or [item["id"] for item in shots] != [item["shot_id"] for item in receipts]
            or any(item["status"] not in {"pending", "running", "done", "failed"} for item in shots)
            or sum(item["status"] == "running" for item in shots) > 1
        ):
            raise FullAutoRunError(
                409, "full_auto_execution_state_stale",
                "The Full Auto runner state no longer matches its persisted admission.",
            )
        for shot in shots:
            attempt = db.one(
                "SELECT * FROM full_auto_execution_attempt WHERE shot_id = ? "
                "ORDER BY attempt_number DESC LIMIT 1", shot["id"],
            )
            if attempt is None:
                if shot["status"] in {"done", "failed"}:
                    continue
                continue
            if (
                attempt["run_id"] != claim.run_id
                or int(attempt["session_id"]) != session_id
                or (shot["status"] == "done" and attempt["completion_state"] != "completed")
                or (shot["status"] == "failed" and attempt["completion_state"] != "failed")
                or (shot["status"] == "running" and attempt["completion_state"] == "completed")
            ):
                raise FullAutoRunError(
                    409, "execution_outcome_unknown",
                    "A saved execution attempt does not match its shot state.",
                )
        return row


def _validate_execution_dependencies(run: dict) -> None:
    """Compare live dispatch inputs with the exact grant that admitted this run."""
    from backend import resource_store, workflow_binding

    grant = current_authorization(run["run_id"], session_id=int(run["session_id"]))
    summary, _limits = _validate_status_authorization(grant["authorization"])
    session = db.one(
        "SELECT model_id, workflow_id, settings FROM session WHERE id = ?",
        int(run["session_id"]),
    )
    if session is None:
        raise _authority_error("dependency_missing")
    model = db.one("SELECT * FROM model WHERE id = ?", session["model_id"])
    if model is None:
        raise _authority_error("dependency_missing")
    try:
        settings = json.loads(session["settings"] or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise _authority_error("dependency_changed") from exc
    if not isinstance(settings, dict):
        raise _authority_error("dependency_changed")
    settings.pop("composition_mode", None)
    live_model = {
        "model_id": int(model["id"]),
        "trigger_digest": resource_store.canonical_digest(model["trigger"]),
        "base_positive_digest": resource_store.canonical_digest(model["base_positive"]),
        "base_negative_digest": resource_store.canonical_digest(model["base_negative"]),
        "lora_selection_digest": resource_store.canonical_digest({
            "name": model["lora_name"], "strength": model["lora_strength"],
        }),
        "effective_settings_digest": resource_store.canonical_digest(settings),
    }
    if live_model != {
        key: summary["model_binding"][key]
        for key in (
            "model_id", "trigger_digest", "base_positive_digest",
            "base_negative_digest", "lora_selection_digest", "effective_settings_digest",
        )
    }:
        raise _authority_error("dependency_changed")
    try:
        current_model_settings = workflow_binding.effective_session_settings(model)
    except workflow_binding.WorkflowCompatibilityError as exc:
        raise _authority_error("dependency_changed") from exc
    # The authorization binds effective canvas dimensions. Guided Full Auto
    # stores these on the session whether they were inherited or explicitly
    # selected, so preserve them while checking the model's remaining live
    # defaults for drift.
    current_model_settings["width"] = summary["canvas"]["width"]
    current_model_settings["height"] = summary["canvas"]["height"]
    current_model_settings.pop("composition_mode", None)
    if resource_store.canonical_digest(current_model_settings) != summary["model_binding"][
        "effective_settings_digest"
    ]:
        raise _authority_error("dependency_changed")

    workflow_id = session["workflow_id"]
    try:
        if workflow_id is None or workflow_binding.validate_workflow_binding_against_session(
            int(run["session_id"]),
        ) is None:
            raise workflow_binding.WorkflowChanged("session has no frozen workflow binding")
        live_workflow = workflow_binding.build_workflow_binding(int(workflow_id))
    except (workflow_binding.WorkflowChanged, workflow_binding.WorkflowCompatibilityError,
            workflow_binding.StoredPlanUnreadable) as exc:
        raise FullAutoRunError(
            409, "workflow_changed",
            "The authorized workflow binding changed before generation; review the current scope.",
        ) from exc
    if live_workflow != summary["workflow"]:
        raise FullAutoRunError(
            409, "workflow_changed",
            "The authorized workflow changed before generation; review the current scope.",
        )


def begin_execution_attempt(
    claim: RunClaim,
    runner_claim: RunnerClaim,
    *,
    attempt_id: str,
    shot_id: int,
    seed: int,
    filename_prefix: str,
    graph_digest: str,
    client_id: str,
    capability_evidence: dict,
    client_prompt_id: str | None,
    source_workflow_binding: dict,
    source_effective_inputs: dict,
    now_text: str | None = None,
) -> dict:
    """Fence current inputs and commit dispatch intent immediately before POST."""
    if (
        not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id)
        or type(shot_id) is not int or shot_id <= 0
        or type(seed) is not int or seed <= 0
        or not isinstance(filename_prefix, str) or not filename_prefix
        or not isinstance(graph_digest, str) or not _SHA256.fullmatch(graph_digest)
        or not isinstance(client_id, str) or not client_id or len(client_id) > 128
    ):
        raise FullAutoRunError(422, "execution_attempt_invalid", "The execution attempt is invalid.")
    expected_capability_keys = {
        "schema", "endpoint_digest", "server_identity_digest", "server_version",
        "client_prompt_id", "advertised_client_prompt_id",
    }
    if (
        not isinstance(capability_evidence, dict)
        or set(capability_evidence) != expected_capability_keys
        or capability_evidence.get("schema") != "comfy-execution-capability-v1"
        or not isinstance(capability_evidence.get("endpoint_digest"), str)
        or not _SHA256.fullmatch(capability_evidence["endpoint_digest"])
        or not isinstance(capability_evidence.get("server_identity_digest"), str)
        or not _SHA256.fullmatch(capability_evidence["server_identity_digest"])
        or (capability_evidence.get("server_version") is not None
            and (not isinstance(capability_evidence["server_version"], str)
                 or not capability_evidence["server_version"].strip()))
        or type(capability_evidence.get("client_prompt_id")) is not bool
        or (capability_evidence.get("advertised_client_prompt_id") is not None
            and type(capability_evidence["advertised_client_prompt_id"]) is not bool)
        or (capability_evidence["client_prompt_id"]
            and (capability_evidence["advertised_client_prompt_id"] is not True
                 or capability_evidence["server_version"] is None))
    ):
        raise FullAutoRunError(422, "execution_attempt_invalid", "The capability evidence is invalid.")
    if capability_evidence["client_prompt_id"]:
        if (
            not isinstance(client_prompt_id, str)
            or not re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                client_prompt_id,
            )
            or str(uuid.UUID(client_prompt_id)) != client_prompt_id
        ):
            raise FullAutoRunError(422, "execution_attempt_invalid", "The client prompt ID is invalid.")
    elif client_prompt_id is not None:
        raise FullAutoRunError(422, "execution_attempt_invalid", "An unverified client prompt ID is not allowed.")

    with db.transaction():
        timestamp = _now_text(now_text)
        runner_row = _require_runner_claim(runner_claim, now_text=timestamp)
        if (
            runner_claim.owner_kind != "full_auto"
            or runner_claim.session_id != claim.session_id
            or runner_claim.run_id != claim.run_id
            or runner_row["run_id"] != claim.run_id
        ):
            raise FullAutoRunError(409, "runner_owner_stale", "The Full Auto runner owner is stale.")
        run = require_session_mutation_allowed(
            claim.session_id, claim=claim, allowed_phases=("execute",),
        )
        if run is None or run["run_id"] != claim.run_id:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution owner is stale.")
        if not resource_planning.is_enabled():
            raise FullAutoRunError(
                503, "resource_planning_disabled", "Resource planning is disabled by configuration.",
            )
        _validate_execution_dependencies(run)
        _validate_captured_workflow_source(claim.session_id, source_workflow_binding)
        _validate_captured_effective_inputs(run, source_effective_inputs)
        shot = db.one(
            "SELECT session_id, status FROM shot WHERE id = ?", shot_id,
        )
        if shot is None or int(shot["session_id"]) != claim.session_id or shot["status"] != "running":
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto shot is no longer ready to dispatch.")
        grant = current_authorization(claim.run_id, session_id=claim.session_id)
        _summary, limits = _validate_status_authorization(grant["authorization"])
        image_allowance = _image_retry_allowance(
            claim.run_id, limits["image_attempts_total"],
        )
        if image_allowance["image_attempts_consumed"] >= image_allowance["authorized_image_attempts_total"]:
            raise FullAutoRunError(
                409, "budget_exhausted",
                "The authorized cumulative image-attempt allowance is exhausted.",
            )
        previous = db.one(
            "SELECT attempt_id, run_id, dispatch_state, completion_state FROM full_auto_execution_attempt "
            "WHERE shot_id = ? ORDER BY attempt_number DESC LIMIT 1",
            shot_id,
        )
        if previous is not None:
            if previous["completion_state"] == "completed":
                raise FullAutoRunError(
                    409, "successful_shot_not_retryable",
                    "A successful Full Auto shot cannot be queued again.",
                )
            prior_unsettled = (
                previous["dispatch_state"] in {"intent", "outcome_unknown"}
                or previous["completion_state"] in {"pending", "output_available"}
            )
            risk_accepted = db.one(
                "SELECT 1 FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
                previous["attempt_id"],
            ) is not None
            if prior_unsettled and not risk_accepted:
                raise FullAutoRunError(
                    409, "execution_outcome_unknown",
                    "An earlier generation attempt has not reached a verified outcome.",
                )
            if (
                not prior_unsettled
                and previous["completion_state"] == "failed"
                and not risk_accepted
            ):
                authorized_retry = db.one(
                    """SELECT 1 FROM full_auto_attempt_retry_authorization
                       WHERE attempt_id = ? AND run_id = ? AND run_epoch = ?""",
                    previous["attempt_id"], claim.run_id, claim.epoch,
                )
                if authorized_retry is None:
                    raise FullAutoRunError(
                        409, "execution_retry_not_authorized",
                        "A failed Full Auto attempt requires explicit retry consent.",
                    )
        attempt_number = int(db.one(
            "SELECT COALESCE(MAX(attempt_number), 0) + 1 AS n "
            "FROM full_auto_execution_attempt WHERE shot_id = ?", shot_id,
        )["n"])
        # Dependency reads above can be slow and the run or single-runner lease
        # can expire while they are in progress. Fence again at the actual
        # intent write boundary; the physical POST follows immediately after
        # this short transaction commits.
        timestamp = _now_text(None)
        runner_row = _require_runner_claim(runner_claim, now_text=timestamp)
        if (
            runner_claim.owner_kind != "full_auto"
            or runner_claim.session_id != claim.session_id
            or runner_claim.run_id != claim.run_id
            or runner_row["run_id"] != claim.run_id
        ):
            raise FullAutoRunError(409, "runner_owner_stale", "The Full Auto runner owner is stale.")
        run = require_session_mutation_allowed(
            claim.session_id, claim=claim, allowed_phases=("execute",),
        )
        if run is None or run["run_id"] != claim.run_id:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution owner is stale.")
        if not resource_planning.is_enabled():
            raise FullAutoRunError(
                503, "resource_planning_disabled", "Resource planning is disabled by configuration.",
            )
        db.run(
            """INSERT INTO full_auto_execution_attempt (
                   attempt_id, run_id, session_id, shot_id, attempt_number,
                   run_epoch, run_fencing_token, runner_fencing_token, seed,
                   filename_prefix, graph_digest, client_prompt_id, client_id,
                   capability_evidence_json, dispatch_state, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'intent', ?, ?)""",
            attempt_id, claim.run_id, claim.session_id, shot_id, attempt_number,
            claim.epoch, claim.fencing_token, runner_claim.fencing_token, seed,
            filename_prefix, graph_digest, client_prompt_id, client_id,
            _canonical_json(capability_evidence), timestamp, timestamp,
        )
        return db.one("SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id)


def validate_execution_attempt_dispatch(
    claim: RunClaim,
    runner_claim: RunnerClaim,
    *,
    attempt_id: str,
    capability_evidence: dict,
    source_workflow_binding: dict,
    source_effective_inputs: dict,
) -> dict:
    """Recheck the authorized owner and frozen inputs after an identity GET, just before POST."""
    with db.transaction():
        timestamp = _now_text(None)
        runner_row = _require_runner_claim(runner_claim, now_text=timestamp)
        if (
            runner_claim.owner_kind != "full_auto"
            or runner_claim.session_id != claim.session_id
            or runner_claim.run_id != claim.run_id
            or runner_row["run_id"] != claim.run_id
        ):
            raise FullAutoRunError(409, "runner_owner_stale", "The Full Auto runner owner is stale.")
        run = require_session_mutation_allowed(
            claim.session_id, claim=claim, allowed_phases=("execute",),
        )
        if run is None or run["run_id"] != claim.run_id:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution owner is stale.")
        if not resource_planning.is_enabled():
            raise FullAutoRunError(
                503, "resource_planning_disabled", "Resource planning is disabled by configuration.",
            )
        _validate_execution_dependencies(run)
        _validate_captured_workflow_source(claim.session_id, source_workflow_binding)
        _validate_captured_effective_inputs(run, source_effective_inputs)

        attempt = db.one(
            "SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id,
        )
        if (
            attempt is None
            or not _attempt_matches_claim(attempt, claim, runner_claim)
            or attempt["dispatch_state"] != "intent"
            or attempt["completion_state"] != "pending"
        ):
            raise FullAutoRunError(
                409, "execution_outcome_unknown",
                "The execution intent changed before the remote request was sent.",
            )
        try:
            frozen = json.loads(attempt["capability_evidence_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise FullAutoRunError(
                409, "execution_outcome_unknown", "The saved ComfyUI identity evidence is unreadable.",
            ) from exc
        if capability_evidence != frozen:
            raise FullAutoRunError(
                409, "execution_capability_changed",
                "The ComfyUI identity changed before the generation request was sent.",
            )
        shot = db.one(
            "SELECT session_id, status FROM shot WHERE id = ?", attempt["shot_id"],
        )
        if (
            shot is None or int(shot["session_id"]) != claim.session_id
            or shot["status"] != "running"
        ):
            raise FullAutoRunError(
                409, "run_owner_stale", "The Full Auto shot is no longer ready to dispatch.",
            )
        # The dependency/file checks above can be slow. Re-fence the physical
        # dispatch boundary just as begin_execution_attempt does before saving
        # its intent; this transaction follows the identity GET and no remote
        # call may intervene between its commit and POST.
        timestamp = _now_text(None)
        runner_row = _require_runner_claim(runner_claim, now_text=timestamp)
        if (
            runner_claim.owner_kind != "full_auto"
            or runner_claim.session_id != claim.session_id
            or runner_claim.run_id != claim.run_id
            or runner_row["run_id"] != claim.run_id
        ):
            raise FullAutoRunError(409, "runner_owner_stale", "The Full Auto runner owner is stale.")
        run = require_session_mutation_allowed(
            claim.session_id, claim=claim, allowed_phases=("execute",),
        )
        if run is None or run["run_id"] != claim.run_id:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution owner is stale.")
        if not resource_planning.is_enabled():
            raise FullAutoRunError(
                503, "resource_planning_disabled", "Resource planning is disabled by configuration.",
            )
        attempt = db.one(
            "SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id,
        )
        if (
            attempt is None or not _attempt_matches_claim(attempt, claim, runner_claim)
            or attempt["dispatch_state"] != "intent"
            or attempt["completion_state"] != "pending"
            or attempt["capability_evidence_json"] != _canonical_json(capability_evidence)
        ):
            raise FullAutoRunError(
                409, "execution_outcome_unknown",
                "The execution intent changed before the remote request was sent.",
            )
        shot = db.one(
            "SELECT session_id, status FROM shot WHERE id = ?", attempt["shot_id"],
        )
        if (
            shot is None or int(shot["session_id"]) != claim.session_id
            or shot["status"] != "running"
        ):
            raise FullAutoRunError(
                409, "run_owner_stale", "The Full Auto shot is no longer ready to dispatch.",
            )
        return attempt


def _validate_captured_workflow_source(session_id: int, captured: dict) -> None:
    """Reject a graph/map snapshot that no longer matches the frozen live source."""
    from backend import workflow_binding

    session = db.one("SELECT workflow_id FROM session WHERE id = ?", session_id)
    if session is None or session["workflow_id"] is None:
        raise FullAutoRunError(
            409, "workflow_changed",
            "The authorized workflow binding changed before generation; review the current scope.",
        )
    try:
        current = workflow_binding.build_workflow_binding(int(session["workflow_id"]))
    except (workflow_binding.WorkflowChanged, workflow_binding.WorkflowCompatibilityError,
            workflow_binding.StoredPlanUnreadable) as exc:
        raise FullAutoRunError(
            409, "workflow_changed",
            "The authorized workflow binding changed before generation; review the current scope.",
        ) from exc
    if captured != current:
        raise FullAutoRunError(
            409, "workflow_changed",
            "The workflow source read for this shot no longer matches its authorized binding.",
        )


def _validate_captured_effective_inputs(run: dict, captured: dict) -> None:
    """Reject effective session/model inputs read before dispatch waits if they drifted."""
    grant = current_authorization(
        run["run_id"], session_id=int(run["session_id"]),
    )
    summary, _limits = _validate_status_authorization(grant["authorization"])
    model = summary["model_binding"]
    expected = {
        "model_id": model["model_id"],
        "lora_selection_digest": model["lora_selection_digest"],
        "effective_settings_digest": model["effective_settings_digest"],
    }
    if not isinstance(captured, dict) or captured != expected:
        raise _authority_error("dependency_changed")


def _attempt_matches_claim(attempt: dict, claim: RunClaim, runner_claim: RunnerClaim) -> bool:
    if not _authentic_claim(claim) or not isinstance(runner_claim, RunnerClaim):
        return False
    expected_runner_signature = hmac.new(
        _RUNNER_CLAIM_KEY, _runner_claim_payload(runner_claim), hashlib.sha256,
    ).hexdigest()
    return (
        hmac.compare_digest(expected_runner_signature, runner_claim._signature)
        and attempt["run_id"] == claim.run_id == runner_claim.run_id
        and int(attempt["session_id"]) == claim.session_id == runner_claim.session_id
        and int(attempt["run_epoch"]) == claim.epoch
        and int(attempt["run_fencing_token"]) == claim.fencing_token
        and int(attempt["runner_fencing_token"]) == runner_claim.fencing_token
        and runner_claim.owner_kind == "full_auto"
    )


def _attempt_matches_recovery_claim(
    attempt: dict, claim: RunClaim, runner_claim: RunnerClaim,
) -> bool:
    if not _authentic_claim(claim) or not isinstance(runner_claim, RunnerClaim):
        return False
    expected_runner_signature = hmac.new(
        _RUNNER_CLAIM_KEY, _runner_claim_payload(runner_claim), hashlib.sha256,
    ).hexdigest()
    return (
        hmac.compare_digest(expected_runner_signature, runner_claim._signature)
        and attempt["run_id"] == claim.run_id == runner_claim.run_id
        and int(attempt["session_id"]) == claim.session_id == runner_claim.session_id
        and int(attempt["run_epoch"]) == claim.epoch
        and int(attempt["run_fencing_token"]) <= claim.fencing_token
        and int(attempt["runner_fencing_token"]) <= runner_claim.fencing_token
        and runner_claim.owner_kind == "full_auto"
    )


def _require_execution_recovery_owner(
    attempt: dict, claim: RunClaim, runner_claim: RunnerClaim, *, now_text: str,
) -> dict:
    runner_row = _require_runner_claim(runner_claim, now_text=now_text)
    run = _require_claim(
        claim, now_text=now_text, reconciliation=claim.requires_reconciliation,
    )
    phase_is_current_execute = (
        not claim.requires_reconciliation and run["phase"] == "execute"
    )
    phase_is_recovery_execute = (
        claim.requires_reconciliation and run["phase"] == "reconcile"
        and run["resume_phase"] == "execute"
    )
    if (
        run is None or run["run_id"] != claim.run_id
        or run["state"] != "active"
        or not (phase_is_current_execute or phase_is_recovery_execute)
        or runner_claim.owner_kind != "full_auto"
        or runner_claim.session_id != claim.session_id
        or runner_claim.run_id != claim.run_id
        or runner_row["run_id"] != claim.run_id
        or not _attempt_matches_recovery_claim(attempt, claim, runner_claim)
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The execution recovery owner is stale.")
    shot = db.one(
        "SELECT session_id, status FROM shot WHERE id = ?", attempt["shot_id"],
    )
    if (
        shot is None or int(shot["session_id"]) != claim.session_id
        or shot["status"] != "running"
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The execution recovery shot is stale.")
    return run


def require_execution_recovery_claim(
    session_id: int, claim: RunClaim, *, now_text: str | None = None,
) -> dict:
    """Validate a live execute or execute-reconciliation owner without new authority checks."""
    timestamp = _now_text(now_text)
    run = _require_claim(
        claim, now_text=timestamp, reconciliation=claim.requires_reconciliation,
    )
    phase_is_current = not claim.requires_reconciliation and run["phase"] == "execute"
    phase_is_recovery = (
        claim.requires_reconciliation and run["phase"] == "reconcile"
        and run["resume_phase"] == "execute"
    )
    if (
        int(run["session_id"]) != session_id or run["state"] != "active"
        or run["cancel_intent"] or not (phase_is_current or phase_is_recovery)
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution owner is stale.")
    return run


def require_cancellation_reconciliation_claim(
    session_id: int, claim: RunClaim, *, now_text: str | None = None,
) -> dict:
    """Validate a fresh explicit reconciliation owner while cancellation remains pending."""
    timestamp = _now_text(now_text)
    run = _require_claim(claim, now_text=timestamp, reconciliation=True)
    if (
        int(run["session_id"]) != session_id or not bool(run["cancel_intent"])
        or run["state"] not in {"blocked", "cancel_requested"}
        or run["phase"] != "reconcile"
    ):
        raise FullAutoRunError(
            409, "cancel_reconciliation_required",
            "The Full Auto cancellation reconciliation owner is stale.",
        )
    return run


def require_cancellation_runner_claim(
    session_id: int,
    claim: RunClaim,
    runner_claim: RunnerClaim,
    *,
    now_text: str | None = None,
) -> dict:
    run = require_cancellation_reconciliation_claim(
        session_id, claim, now_text=now_text,
    )
    runner_row = _require_runner_claim(
        runner_claim, now_text=_now_text(now_text),
    )
    if (
        run["resume_phase"] != "execute"
        or runner_claim.owner_kind != "full_auto"
        or runner_claim.session_id != session_id
        or runner_claim.run_id != claim.run_id
        or runner_row["run_id"] != claim.run_id
    ):
        raise FullAutoRunError(
            409, "run_owner_stale",
            "The Full Auto cancellation runner owner is stale.",
        )
    return run


def require_cancellation_attempt_owner(
    attempt: dict,
    claim: RunClaim,
    runner_claim: RunnerClaim,
    *,
    now_text: str | None = None,
) -> dict:
    require_cancellation_runner_claim(
        claim.session_id, claim, runner_claim, now_text=now_text,
    )
    if not _attempt_matches_recovery_claim(attempt, claim, runner_claim):
        raise FullAutoRunError(
            409, "run_owner_stale",
            "The Full Auto cancellation attempt owner is stale.",
        )
    shot = db.one(
        "SELECT session_id, status FROM shot WHERE id = ?", attempt["shot_id"],
    )
    if (
        shot is None or int(shot["session_id"]) != claim.session_id
        or shot["status"] != "running"
    ):
        raise FullAutoRunError(
            409, "run_owner_stale", "The Full Auto cancellation shot is stale.",
        )
    return attempt


def record_execution_dispatch(
    attempt_id: str,
    claim: RunClaim,
    runner_claim: RunnerClaim,
    *,
    state: Literal["acknowledged", "outcome_unknown", "rejected"],
    prompt_id: str | None = None,
    error: str = "",
    recovery: bool = False,
    cancellation_recovery: bool = False,
) -> dict:
    """Persist a dispatch receipt against its immutable attempt fences.

    A queue response is evidence even if the lease expires while the response is
    returning, so this receipt deliberately does not require a live owner row.
    """
    if state not in {"acknowledged", "outcome_unknown", "rejected"}:
        raise ValueError("Unsupported execution dispatch state")
    if state == "acknowledged" and (not isinstance(prompt_id, str) or not prompt_id):
        raise FullAutoRunError(422, "execution_attempt_invalid", "The queue acknowledgement is invalid.")
    if state != "acknowledged" and prompt_id is not None:
        raise FullAutoRunError(422, "execution_attempt_invalid", "The queue receipt is invalid.")

    with db.transaction():
        attempt = db.one(
            "SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id,
        )
        if attempt is None:
            raise FullAutoRunError(409, "run_owner_stale", "The execution attempt receipt is stale.")
        if recovery and cancellation_recovery:
            raise FullAutoRunError(422, "execution_attempt_invalid", "The recovery authority is invalid.")
        if cancellation_recovery:
            require_cancellation_attempt_owner(
                attempt, claim, runner_claim, now_text=_now_text(None),
            )
        elif recovery:
            _require_execution_recovery_owner(
                attempt, claim, runner_claim, now_text=_now_text(None),
            )
        elif not _attempt_matches_claim(attempt, claim, runner_claim):
            raise FullAutoRunError(409, "run_owner_stale", "The execution attempt receipt is stale.")
        if (
            state == "acknowledged"
            and attempt["client_prompt_id"] is not None
            and attempt["client_prompt_id"] != prompt_id
        ):
            raise FullAutoRunError(
                409, "execution_outcome_unknown",
                "ComfyUI acknowledged a different prompt ID than the verified client ID.",
            )
        current = attempt["dispatch_state"]
        if state == "acknowledged":
            if current == "acknowledged":
                if attempt["prompt_id"] != prompt_id:
                    raise FullAutoRunError(409, "run_state_invalid", "The queue acknowledgement conflicts with saved evidence.")
                return attempt
            if current not in {"intent", "outcome_unknown"}:
                raise FullAutoRunError(409, "run_state_invalid", "The queue acknowledgement cannot advance this attempt.")
            db.run(
                "UPDATE full_auto_execution_attempt SET dispatch_state='acknowledged', "
                "prompt_id=?, updated_at=? WHERE attempt_id=?",
                prompt_id, _now_text(None), attempt_id,
            )
            db.run(
                "UPDATE shot SET prompt_id=? WHERE id=? AND session_id=?",
                prompt_id, attempt["shot_id"], attempt["session_id"],
            )
        elif state == "outcome_unknown":
            if current == "acknowledged":
                return attempt
            if current == "outcome_unknown":
                return attempt
            if current != "intent":
                raise FullAutoRunError(409, "run_state_invalid", "The uncertain queue response cannot advance this attempt.")
            db.run(
                "UPDATE full_auto_execution_attempt SET dispatch_state='outcome_unknown', "
                "error=?, updated_at=? WHERE attempt_id=?",
                error[:500], _now_text(None), attempt_id,
            )
        else:
            if current == "rejected":
                return attempt
            if current != "intent":
                raise FullAutoRunError(409, "run_state_invalid", "The queue refusal cannot advance this attempt.")
            db.run(
                "UPDATE full_auto_execution_attempt SET dispatch_state='rejected', "
                "completion_state='failed', error=?, updated_at=? WHERE attempt_id=?",
                error[:500], _now_text(None), attempt_id,
            )
        return db.one("SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id)


def record_execution_output(
    attempt_id: str,
    claim: RunClaim,
    runner_claim: RunnerClaim,
    *,
    image: dict,
    expected_filename: str,
    correlation_evidence: dict | None = None,
    recovery: bool = False,
    cancellation_recovery: bool = False,
) -> dict:
    """Save remote output evidence before the normal output-file move begins."""
    evidence = {
        "filename": image.get("filename"),
        "subfolder": image.get("subfolder") or "",
        "type": image.get("type"),
    }
    if correlation_evidence is not None:
        evidence["correlation"] = correlation_evidence
    with db.transaction():
        timestamp = _now_text(None)
        attempt = db.one(
            "SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id,
        )
        if attempt is None:
            raise FullAutoRunError(409, "run_owner_stale", "The execution output receipt is stale.")
        if recovery and cancellation_recovery:
            raise FullAutoRunError(422, "execution_attempt_invalid", "The recovery authority is invalid.")
        if cancellation_recovery:
            require_cancellation_attempt_owner(
                attempt, claim, runner_claim, now_text=timestamp,
            )
        elif recovery:
            _require_execution_recovery_owner(
                attempt, claim, runner_claim, now_text=timestamp,
            )
        else:
            runner_row = _require_runner_claim(runner_claim, now_text=timestamp)
            if (
                runner_claim.owner_kind != "full_auto"
                or runner_claim.session_id != claim.session_id
                or runner_claim.run_id != claim.run_id
                or runner_row["run_id"] != claim.run_id
            ):
                raise FullAutoRunError(409, "runner_owner_stale", "The Full Auto runner owner is stale.")
            run = require_session_mutation_allowed(
                claim.session_id, claim=claim, allowed_phases=("execute",),
            )
            if run is None or run["run_id"] != claim.run_id:
                raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution owner is stale.")
        if not recovery and not cancellation_recovery and not _attempt_matches_claim(
            attempt, claim, runner_claim,
        ):
            raise FullAutoRunError(409, "run_owner_stale", "The execution output receipt is stale.")
        encoded = _canonical_json(evidence)
        if attempt["completion_state"] == "output_available":
            if attempt["output_evidence_json"] != encoded or attempt["expected_filename"] != expected_filename:
                raise FullAutoRunError(409, "run_state_invalid", "The output evidence conflicts with the saved attempt.")
            return attempt
        output_only_recovery = (
            attempt["dispatch_state"] == "outcome_unknown"
            and isinstance(correlation_evidence, dict)
            and correlation_evidence == {
                "source": "output_png",
                "attempt_id": attempt["attempt_id"],
                "client_id": attempt["client_id"],
                "graph_digest": attempt["graph_digest"],
                "filename_prefix": attempt["filename_prefix"],
                "client_prompt_id": attempt["client_prompt_id"],
            }
        )
        if (
            attempt["completion_state"] != "pending"
            or (attempt["dispatch_state"] != "acknowledged" and not output_only_recovery)
        ):
            raise FullAutoRunError(409, "run_state_invalid", "The output cannot advance this attempt.")
        db.run(
            """UPDATE full_auto_execution_attempt
               SET completion_state='output_available', output_evidence_json=?,
                   expected_filename=?, updated_at=? WHERE attempt_id=?""",
            encoded, expected_filename, timestamp, attempt_id,
        )
        if output_only_recovery:
            # The remote prompt ID remains unknown, but a generated image must
            # not be mistaken for an imported photo by the existing clone path.
            db.run(
                "UPDATE shot SET prompt_id=? WHERE id=? AND session_id=?",
                f"idevgen-recovered-output:{attempt_id}",
                attempt["shot_id"], attempt["session_id"],
            )
        return db.one("SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id)


def finish_execution_attempt(
    attempt_id: str,
    claim: RunClaim,
    runner_claim: RunnerClaim,
    *,
    outcome: Literal["completed", "failed"],
    filename: str = "",
    error: str = "",
    recovery: bool = False,
    cancellation_recovery: bool = False,
) -> dict:
    """Commit attempt settlement and the existing shot result in one transaction."""
    with db.transaction():
        timestamp = _now_text(None)
        attempt = db.one(
            "SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id,
        )
        if attempt is None:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution result is stale.")
        if recovery and cancellation_recovery:
            raise FullAutoRunError(422, "execution_attempt_invalid", "The recovery authority is invalid.")
        if cancellation_recovery:
            require_cancellation_attempt_owner(
                attempt, claim, runner_claim, now_text=timestamp,
            )
        elif recovery:
            _require_execution_recovery_owner(
                attempt, claim, runner_claim, now_text=timestamp,
            )
        else:
            _require_runner_claim(runner_claim, now_text=timestamp)
            row = require_session_mutation_allowed(
                claim.session_id, claim=claim, allowed_phases=("execute",),
            )
            if (
                row is None or row["run_id"] != claim.run_id
                or not _attempt_matches_claim(attempt, claim, runner_claim)
            ):
                raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution result is stale.")
        if attempt["completion_state"] not in {"pending", "output_available", "failed"}:
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto execution result is stale.")
        shot = db.one(
            "SELECT status FROM shot WHERE id = ? AND session_id = ?",
            attempt["shot_id"], attempt["session_id"],
        )
        if shot is None or shot["status"] != "running":
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto shot result is stale.")
        if outcome == "completed":
            if (
                attempt["completion_state"] != "output_available"
                or filename != attempt["expected_filename"]
            ):
                raise FullAutoRunError(409, "run_state_invalid", "The saved output does not match its attempt.")
            db.run(
                "UPDATE full_auto_execution_attempt SET completion_state='completed', "
                "filename=?, updated_at=? WHERE attempt_id=?",
                filename, timestamp, attempt_id,
            )
            db.run(
                "UPDATE shot SET status='done', filename=?, error='', finished_at=? "
                "WHERE id=? AND session_id=?",
                filename, timestamp, attempt["shot_id"], attempt["session_id"],
            )
        elif outcome == "failed":
            if attempt["completion_state"] == "failed" and attempt["error"]:
                error = attempt["error"]
            if not error:
                raise FullAutoRunError(422, "execution_attempt_invalid", "A failed attempt needs an error message.")
            db.run(
                "UPDATE full_auto_execution_attempt SET completion_state='failed', "
                "error=?, updated_at=? WHERE attempt_id=?",
                error[:500], timestamp, attempt_id,
            )
            db.run(
                "UPDATE shot SET status='failed', error=?, finished_at=? "
                "WHERE id=? AND session_id=?",
                error[:500], timestamp, attempt["shot_id"], attempt["session_id"],
            )
        else:
            raise ValueError("Unsupported execution outcome")
        return db.one("SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id)


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
        child_start = progress["target_ids"].index(child["target_ids"][0])
        child_end = child_start + len(child["target_ids"])
        if (
            prior != progress["target_ids"][:len(prior)]
            or progress["target_ids"][child_start:child_end] != child["target_ids"]
            or len(prior) < child_start
        ):
            raise FullAutoRunError(409, "prepare_progress_stale", "The authoring child no longer matches ordered run progress.")
        adopted_from_child = len(prior) - child_start
        if adopted_from_child > len(child["target_ids"]) and persisted_completed != child["target_ids"]:
            raise FullAutoRunError(409, "prepare_progress_stale", "The authoring child was checkpointed out of order.")
        if persisted_completed != child["target_ids"][:len(persisted_completed)]:
            raise FullAutoRunError(409, "prepare_progress_invalid", "Child progress must be an ordered target prefix.")
        if persisted_completed[:min(adopted_from_child, len(child["target_ids"]))] != child["target_ids"][:min(adopted_from_child, len(child["target_ids"]))]:
            raise FullAutoRunError(409, "prepare_progress_stale", "The checkpointed child prefix changed.")

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

        newly_completed = persisted_completed[min(adopted_from_child, len(child["target_ids"])):]
        completed = prior + newly_completed
        # Acquiring the transaction may wait on SQLite's write lock, and
        # evidence validation can take time. Check the live owner once more
        # immediately before the checkpoint write so elapsed leases fail closed.
        timestamp = _now_text(None)
        row = require_prepare_claim(claim, now_text=timestamp)
        if completed != prior:
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
    assistant_retry_after: str | None = None,
    now_text: str | None = None,
) -> dict:
    """Apply only the nonterminal coordinator transitions from the state table."""
    if to_state not in {"active", "waiting_authoring", "waiting_runner", "blocked"}:
        raise FullAutoRunError(409, "run_transition_refused", "The requested run transition is not allowed.")
    if phase is not None and phase not in _MAIN_PHASES:
        raise FullAutoRunError(409, "run_phase_invalid", "The requested Full Auto phase is invalid.")
    if blocker_code is not None and not _SAFE_CODE.fullmatch(blocker_code):
        raise FullAutoRunError(422, "invalid_blocker_code", "The Full Auto blocker code is invalid.")
    with db.transaction():
        timestamp = _now_text(now_text)
        retry_after_at = (
            assistant_retry_after_deadline(assistant_retry_after, now_text=timestamp)
            if blocker_code == "assistant_rate_limited" else None
        )
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
                   runner_ready_at = CASE WHEN ? = 'waiting_runner' THEN ? ELSE NULL END,
                   blocker_code = ?, assistant_retry_after_at = ?,
                   comfy_probe_scope = CASE WHEN ? = 'comfy_unavailable' THEN comfy_probe_scope ELSE NULL END,
                   comfy_probe_count = CASE WHEN ? = 'comfy_unavailable' THEN comfy_probe_count ELSE 0 END,
                   comfy_probe_due_at = CASE WHEN ? = 'comfy_unavailable' THEN comfy_probe_due_at ELSE NULL END,
                   evidence_json = ?, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?""",
            to_state, next_phase, fence_delta, lease, lease_started,
            to_state, timestamp, blocker_code,
            retry_after_at, blocker_code, blocker_code, blocker_code,
            _canonical_json({"kind": "state_transition", "from": source, "to": to_state}),
            timestamp, claim.run_id, claim.epoch, claim.fencing_token,
        )
        return _view(_require_run(claim.run_id))


def release_waiting_runner_claim(
    claim: RunClaim,
    *,
    now_text: str | None = None,
) -> dict:
    """Drop a scheduler claim while preserving a waiting run's FIFO position."""
    with db.transaction():
        timestamp = _now_text(now_text)
        row = _require_claim(claim, now_text=timestamp)
        if row["state"] != "waiting_runner" or row["phase"] != "execute":
            raise FullAutoRunError(409, "run_owner_stale", "The Full Auto runner waiter is stale.")
        db.run(
            """UPDATE full_auto_run
               SET revision = revision + 1, fencing_token = fencing_token + 1,
                   lease_expires_at = NULL, lease_started_at = NULL, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?
                 AND state = 'waiting_runner'""",
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
                   cancel_intent_at = NULL, blocker_code = NULL,
                   assistant_retry_after_at = NULL, comfy_probe_scope = NULL,
                   comfy_probe_count = 0, comfy_probe_due_at = NULL, resume_phase = NULL,
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
    *,
    scope_digest: str | None = None,
    comparison_issued_at: str | None = None,
    accept_changed_scope: bool = False,
    retry_failed_shots: bool = False,
    accept_unknown_outcome_risk: bool = False,
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
    if type(accept_changed_scope) is not bool:
        raise FullAutoRunError(422, "invalid_control_request", "accept_changed_scope must be a boolean.")
    if type(retry_failed_shots) is not bool or type(accept_unknown_outcome_risk) is not bool:
        raise FullAutoRunError(422, "invalid_control_request", "Retry consent fields must be booleans.")
    if (retry_failed_shots or accept_unknown_outcome_risk) and (
        scope_digest is None or comparison_issued_at is None
    ):
        raise FullAutoRunError(
            422, "retry_comparison_required",
            "Refresh the Full Auto comparison and include its scope_digest and comparison_issued_at before retrying.",
        )
    if scope_digest is not None:
        if action != "resume" or not isinstance(scope_digest, str) or not _SHA256.fullmatch(scope_digest):
            raise FullAutoRunError(422, "invalid_control_request", "scope_digest is invalid.")
        body["scope_digest"] = scope_digest
    if comparison_issued_at is not None:
        if (
            action != "resume"
            or not isinstance(comparison_issued_at, str)
            or not 1 <= len(comparison_issued_at) <= 40
        ):
            raise FullAutoRunError(422, "invalid_control_request", "comparison_issued_at is invalid.")
        try:
            issued = _timestamp(comparison_issued_at, field_name="comparison timestamp")
        except FullAutoRunError as exc:
            raise FullAutoRunError(
                422, "invalid_control_request", "comparison_issued_at is invalid.",
            ) from exc
        if issued.isoformat(timespec="seconds") != comparison_issued_at:
            raise FullAutoRunError(
                422, "invalid_control_request",
                "comparison_issued_at must be a canonical UTC timestamp.",
            )
        body["comparison_issued_at"] = comparison_issued_at
    if action == "resume":
        body["accept_changed_scope"] = accept_changed_scope
        if retry_failed_shots:
            body["retry_failed_shots"] = True
        if accept_unknown_outcome_risk:
            body["accept_unknown_outcome_risk"] = True
    elif (
        scope_digest is not None or comparison_issued_at is not None
        or accept_changed_scope or retry_failed_shots or accept_unknown_outcome_risk
    ):
        raise FullAutoRunError(422, "invalid_control_request", "Scope acceptance is only valid for Resume.")
    return body, _digest(body)


def _replay_action(
    run_id: str,
    request_id: str,
    request_digest: str,
    expected_action: Literal["cancel", "resume"],
) -> RunActionResult | None:
    row = db.one(
        "SELECT action, request_digest, epoch, response_json FROM full_auto_run_action_receipt "
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
    authority = db.one("SELECT session_id FROM full_auto_run WHERE run_id = ?", run_id)
    if type(view) is not dict or set(view) != set(_PUBLIC_RUN_VIEW_FIELDS) | {"action", "request_id"}:
        raise FullAutoRunError(500, "run_receipt_invalid", "The saved Full Auto control receipt is invalid.")
    if (
        row["action"] != expected_action
        or view.get("action") != expected_action
        or type(view.get("request_id")) is not str
        or view.get("request_id") != request_id
        or type(view.get("run_id")) is not str
        or view.get("run_id") != run_id
        or authority is None
        or type(view.get("session_id")) is not int
        or view.get("session_id") != int(authority["session_id"])
        or type(view.get("epoch")) is not int
        or view.get("epoch") != int(row["epoch"])
    ):
        raise FullAutoRunError(409, "run_receipt_invalid", "The saved Full Auto control receipt is invalid.")
    try:
        public_view = _validate_public_run_view(view)
    except FullAutoRunError as exc:
        raise FullAutoRunError(500, "run_receipt_invalid", "The saved Full Auto control receipt is invalid.") from exc
    return RunActionResult(
        view={**public_view, "action": expected_action, "request_id": request_id},
        replayed=True,
    )


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
    current_plan = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?",
        int(row["session_id"]),
    )
    if current_plan is None or int(current_plan["plan_revision"]) != expected_plan_revision:
        raise FullAutoRunError(409, "plan_revision_stale", "The session plan changed; reload the current revision.")


def cancel_run(
    run_id: str,
    request_id: str,
    expected_run_revision: int,
    expected_plan_revision: int,
    *,
    work_pending: bool | None,
    derive_work_pending: bool = False,
    expected_session_id: int | None = None,
    now_text: str | None = None,
) -> RunActionResult:
    """Persist cancellation intent using backend-owned, transaction-current evidence."""
    if type(derive_work_pending) is not bool:
        raise FullAutoRunError(422, "invalid_control_request", "derive_work_pending must be a boolean.")
    body, request_digest = _action_fields(
        "cancel", run_id, request_id, expected_run_revision, expected_plan_revision,
    )
    timestamp = _now_text(now_text)
    with db.transaction():
        _check_run_session_identity(run_id, expected_session_id)
        replay = _replay_action(run_id, request_id, request_digest, "cancel")
        if replay is not None:
            return replay
        schedule_cancellation_reconciliation = False
        row = _require_run(run_id)
        _check_control_cas(row, body["expected_run_revision"], body["expected_plan_revision"])
        if row["state"] in {"cancel_requested", "blocked"} and bool(row["cancel_intent"]):
            # Repeated Cancel records its response but never polls or dispatches.
            pass
        elif row["state"] in {"active", "waiting_authoring", "waiting_runner", "blocked"}:
            _cancel_unstarted_full_auto_shots(row, timestamp)
            from backend import authoring_operations

            authoring_operations.cancel_full_auto_operations(
                int(row["session_id"]), run_id, now_text=timestamp,
            )
            quarantine_run_assistant_slots(run_id, now_text=timestamp)
            db.run(
                """UPDATE session SET status = 'cancelled' WHERE id = ? AND status = 'running'
                   AND NOT EXISTS (
                       SELECT 1 FROM shot WHERE session_id = ? AND status = 'running'
                   )""",
                int(row["session_id"]), int(row["session_id"]),
            )
            if derive_work_pending:
                work_pending = unresolved_work_count(int(row["session_id"]), run_id) > 0
            if type(work_pending) is not bool:
                raise FullAutoRunError(
                    409, "cancel_evidence_required", "Backend work-settlement evidence is required.",
                )
            old_phase = row["resume_phase"] or row["phase"]
            if work_pending:
                state, phase, resume_phase = "cancel_requested", "reconcile", old_phase
                blocker = None
                schedule_cancellation_reconciliation = True
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
            if state == "cancelled":
                db.run(
                    "UPDATE session SET status = 'cancelled' WHERE id = ?",
                    int(row["session_id"]),
                )
            row = _require_run(run_id)
        else:
            raise FullAutoRunError(409, "run_not_cancellable", "This Full Auto run cannot be cancelled in its current state.")
        view = _action_view(row, "cancel", request_id)
        _store_action(run_id, request_id, "cancel", request_digest, row, view, timestamp)
        return RunActionResult(
            view=view,
            schedule_cancellation_reconciliation=schedule_cancellation_reconciliation,
        )


def _resume_eligibility(
    row: dict,
    evidence: ResumeEligibility | None,
    *,
    current_plan_revision: int,
    scope_accepted: bool = False,
) -> str:
    if not isinstance(evidence, ResumeEligibility):
        raise FullAutoRunError(409, "resume_preflight_required", "Current Full Auto resume checks are required.")
    if type(evidence.current_plan_revision) is not int or evidence.current_plan_revision != current_plan_revision:
        raise FullAutoRunError(409, "plan_revision_stale", "The current plan changed; refresh consent before resuming.")
    if type(evidence.unresolved_work) is not int or evidence.unresolved_work != 0:
        raise FullAutoRunError(409, "run_work_unsettled", "The Full Auto run still has unresolved owned work.")
    if type(evidence.planning_enabled) is not bool or not evidence.planning_enabled:
        raise FullAutoRunError(503, "resource_planning_disabled", "Full Auto is disabled by configuration.")
    if type(evidence.authorization_valid) is not bool or not evidence.authorization_valid:
        raise FullAutoRunError(409, "authorization_expired", "The Full Auto authorization is no longer valid.")
    if type(evidence.policy_supported) is not bool or not evidence.policy_supported:
        raise FullAutoRunError(409, "policy_changed", "The recorded Full Auto policy is not supported.")
    if type(evidence.scope_matches) is not bool or not evidence.scope_matches and not scope_accepted:
        raise FullAutoRunError(409, "authorization_scope_changed", "The authorized Full Auto inputs changed.")
    if type(evidence.budget_available) is not bool or not evidence.budget_available:
        raise FullAutoRunError(409, "budget_exhausted", "The authorized Full Auto budget is exhausted.")
    if type(evidence.session_exclusive) is not bool or not evidence.session_exclusive:
        raise FullAutoRunError(409, "full_auto_run_conflict", "Another operation owns this session.")
    if evidence.next_state not in {"active", "waiting_authoring", "waiting_runner"}:
        raise FullAutoRunError(422, "invalid_resume_state", "The requested Full Auto resume state is invalid.")
    return evidence.next_state


def _current_resume_eligibility(
    row: dict,
    comparison: dict,
    *,
    now_text: str,
) -> ResumeEligibility:
    """Derive every resume gate from rows inside the control transaction."""
    session_id = int(row["session_id"])
    candidate = comparison.get("_authorization")
    if not isinstance(candidate, dict):
        candidate = current_authorization(
            row["run_id"], session_id=session_id,
        )["authorization"]
    summary, limits = _validate_status_authorization(candidate)
    expiry = candidate.get("authorization_expires_at")
    if not isinstance(expiry, str):
        raise FullAutoRunError(409, "full_auto_authorization_invalid", "The Full Auto authorization is invalid.")

    current_plan_revision, authorized_targets = _authoritative_plan_targets(
        session_id, summary["photo_count"],
    )
    progress_row = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", row["run_id"],
    )
    progress = None if progress_row is None else _read_prepare_progress(row["run_id"])
    started_targets = _validated_prepare_accounting(
        row, progress_row, progress, authorized_targets,
    )
    assistant_total = limits["assistant_calls_total"]
    assistant_per_take = limits["assistant_calls_per_take"]
    pending_assistant_take = next(
        (take_id for take_id in authorized_targets if take_id not in started_targets),
        None,
    )
    if bool(comparison.get("changed")):
        assistant_remaining = (
            assistant_total if pending_assistant_take is None
            else min(assistant_total, assistant_per_take)
        )
    else:
        assistant_remaining = assistant_executable_allowance(
            row["run_id"], pending_assistant_take,
        )["executable_remaining"]
    image_allowance = _image_retry_allowance(row["run_id"], limits["image_attempts_total"])
    image_remaining = image_allowance["authorized_image_attempts_remaining"]
    other_operations = db.one(
        """SELECT 1 FROM authoring_operation
           WHERE session_id = ? AND (full_auto_run_id IS NULL OR full_auto_run_id != ?)
             AND state NOT IN ('succeeded', 'failed', 'cancelled', 'expired') LIMIT 1""",
        session_id, row["run_id"],
    )
    other_run = db.one(
        """SELECT 1 FROM full_auto_run
           WHERE session_id = ? AND run_id != ?
             AND state NOT IN ('cancelled', 'failed', 'succeeded', 'completed_with_errors')
           LIMIT 1""",
        session_id, row["run_id"],
    )
    phase = row["resume_phase"] or row["phase"]
    return ResumeEligibility(
        current_plan_revision=current_plan_revision,
        unresolved_work=unresolved_work_count(session_id, row["run_id"]),
        authorization_valid=(
            _timestamp(expiry, field_name="authorization expiry")
            > _timestamp(now_text, field_name="current time")
        ),
        policy_supported=(
            candidate.get("authorization_version") == _SUPPORTED_AUTHORIZATION_VERSION
            and candidate.get("generate_without_pauses") is True
            and summary.get("policy_version") == _SUPPORTED_POLICY_VERSION
        ),
        budget_available=(
            (pending_assistant_take is None or (
                type(assistant_remaining) is int and assistant_remaining > 0
            ))
            and type(image_remaining) is int and image_remaining > 0
        ),
        session_exclusive=other_operations is None and other_run is None,
        scope_matches=not bool(comparison["changed"]),
        planning_enabled=resource_planning.is_enabled(),
        next_state="waiting_runner" if phase in {"submit", "execute"} else "waiting_authoring",
    )


def _validate_unknown_retry_evidence(
    evidence: list[UnknownRetryEvidence] | None,
    candidates: list[dict],
    *,
    run_id: str,
    session_id: int,
    request_id: str,
    run_epoch: int,
    expected_run_revision: int,
    expected_plan_revision: int,
    scope_digest: str,
    comparison_issued_at: str,
    timestamp: str,
) -> list[tuple[UnknownRetryEvidence, str]]:
    if not isinstance(evidence, list) or not evidence or any(
        type(item) is not UnknownRetryEvidence for item in evidence
    ):
        raise FullAutoRunError(
            409, "unknown_outcome_evidence_required",
            "A current backend queue check is required before retrying an unknown outcome.",
        )
    attempt_ids = [item["attempt_id"] for item in candidates]
    supplied_ids = [item.attempt_id for item in evidence]
    if len(supplied_ids) != len(set(supplied_ids)) or set(supplied_ids) != set(attempt_ids):
        raise FullAutoRunError(
            409, "retry_targets_changed", "The unknown retry targets changed; reload status.",
        )
    candidate_by_id = {item["attempt_id"]: item for item in candidates}
    now = _timestamp(timestamp, field_name="current time")
    values: list[tuple[UnknownRetryEvidence, str]] = []
    snapshot_digests = set()
    capability_digests = set()
    for item in evidence:
        if (
            not isinstance(item.attempt_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", item.attempt_id)
            or not isinstance(item.queue_snapshot_digest, str)
            or not _SHA256.fullmatch(item.queue_snapshot_digest)
            or not isinstance(item.capability_evidence_digest, str)
            or not _SHA256.fullmatch(item.capability_evidence_digest)
        ):
            raise FullAutoRunError(409, "unknown_outcome_evidence_invalid", "The queue evidence is invalid.")
        expected_signature = hmac.new(
            _UNKNOWN_RETRY_EVIDENCE_KEY,
            _unknown_retry_evidence_payload(item),
            hashlib.sha256,
        ).hexdigest()
        if not isinstance(item._signature, str) or not hmac.compare_digest(
            expected_signature, item._signature,
        ):
            raise FullAutoRunError(
                409, "unknown_outcome_evidence_untrusted",
                "The queue evidence was not sealed by the backend queue observer.",
            )
        candidate = candidate_by_id.get(item.attempt_id)
        attempt = db.one(
            "SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?",
            item.attempt_id,
        )
        if (
            candidate is None or attempt is None
            or attempt["run_id"] != run_id
            or int(attempt["session_id"]) != session_id
            or int(attempt["shot_id"]) != int(candidate["shot_id"])
            or item.run_id != run_id
            or item.session_id != session_id
            or item.shot_id != int(candidate["shot_id"])
            or item.request_id != request_id
            or item.expected_run_revision != expected_run_revision
            or item.expected_plan_revision != expected_plan_revision
            or item.acceptance_epoch != run_epoch
            or item.scope_digest != scope_digest
            or item.comparison_issued_at != comparison_issued_at
            or item.attempt_identity_digest != _unknown_retry_attempt_identity(dict(attempt))
        ):
            raise FullAutoRunError(
                409, "unknown_outcome_evidence_invalid",
                "The queue evidence does not match the current run, quote, request and attempt identities.",
            )
        observed = _timestamp(item.observed_at, field_name="queue observation")
        if observed > now or now - observed > RUN_LEASE:
            raise FullAutoRunError(
                409, "unknown_outcome_evidence_stale",
                "The queue evidence is stale; check the current queue again.",
            )
        snapshot_digests.add(item.queue_snapshot_digest)
        capability_digests.add(item.capability_evidence_digest)
        values.append((item, observed.isoformat(timespec="seconds")))
    if len(snapshot_digests) != 1 or len(capability_digests) != 1:
        raise FullAutoRunError(409, "unknown_outcome_evidence_invalid", "The queue evidence is inconsistent.")
    return values


def _retry_unsettled_work_count(
    session_id: int,
    run_id: str,
    unknown_shot_ids: set[int],
) -> int:
    unstarted_linked_shots = {
        int(item["shot_id"])
        for item in db.q(
            """SELECT receipt.shot_id FROM full_auto_submission_receipt receipt
               JOIN shot ON shot.id = receipt.shot_id
               WHERE receipt.run_id = ? AND shot.session_id = ? AND shot.status = 'pending'
                 AND (
                     NOT EXISTS (
                         SELECT 1 FROM full_auto_execution_attempt attempt
                         WHERE attempt.shot_id = shot.id
                     )
                     OR EXISTS (
                         SELECT 1 FROM full_auto_execution_attempt attempt
                         JOIN full_auto_attempt_retry_authorization grant_row
                           ON grant_row.attempt_id = attempt.attempt_id
                          AND grant_row.run_id = receipt.run_id
                         WHERE attempt.shot_id = shot.id
                           AND attempt.run_id = receipt.run_id
                           AND attempt.attempt_number = (
                               SELECT MAX(latest.attempt_number)
                               FROM full_auto_execution_attempt latest
                               WHERE latest.shot_id = shot.id
                           )
                           AND attempt.completion_state = 'failed'
                     )
                     OR EXISTS (
                         SELECT 1 FROM full_auto_execution_attempt attempt
                         JOIN full_auto_attempt_risk_acceptance risk
                           ON risk.attempt_id = attempt.attempt_id
                          AND risk.run_id = receipt.run_id
                         WHERE attempt.shot_id = shot.id
                           AND attempt.run_id = receipt.run_id
                           AND attempt.attempt_number = (
                               SELECT MAX(latest.attempt_number)
                               FROM full_auto_execution_attempt latest
                               WHERE latest.shot_id = shot.id
                           )
                     )
                 )""",
            run_id, session_id,
        )
    }
    ignored_shot_ids = unknown_shot_ids | unstarted_linked_shots
    if unknown_shot_ids:
        running = {
            int(item["id"])
            for item in db.q(
                "SELECT id FROM shot WHERE session_id = ? AND status = 'running'",
                session_id,
            )
        }
        session = db.one("SELECT status FROM session WHERE id = ?", session_id)
        if (
            not running.issubset(unknown_shot_ids)
            or session is None
            or session["status"] == "running" and not running
        ):
            return unresolved_work_count(session_id, run_id)
        runner_owner = db.one(
            "SELECT owner_kind, session_id, run_id, lease_expires_at "
            "FROM global_runner_claim WHERE singleton = 1",
        )
        if (
            runner_owner is not None
            and runner_owner["owner_kind"] is not None
            and int(runner_owner["session_id"] or 0) == session_id
            and runner_owner["run_id"] in {None, run_id}
            and runner_owner["lease_expires_at"] is not None
            and _timestamp(runner_owner["lease_expires_at"], field_name="runner lease")
            > _timestamp(db.now(), field_name="current time")
        ):
            raise FullAutoRunError(409, "run_already_claimed", "The Full Auto runner still owns this session.")
    return unresolved_work_count(
        session_id, run_id,
        ignored_shot_ids=ignored_shot_ids,
        allow_running_session=bool(unknown_shot_ids),
    )


def resume_run(
    run_id: str,
    request_id: str,
    expected_run_revision: int,
    expected_plan_revision: int,
    *,
    eligibility: ResumeEligibility | None,
    scope_digest: str | None = None,
    comparison_issued_at: str | None = None,
    accept_changed_scope: bool = False,
    retry_failed_shots: bool = False,
    accept_unknown_outcome_risk: bool = False,
    unknown_retry_evidence: list[UnknownRetryEvidence] | None = None,
    config: object = None,
    defer_claim: bool = False,
    expected_session_id: int | None = None,
    now_text: str | None = None,
) -> RunActionResult:
    """Persist a checked resume transition; optionally leave claiming to a later coordinator."""
    body, request_digest = _action_fields(
        "resume", run_id, request_id, expected_run_revision, expected_plan_revision,
        scope_digest=scope_digest, comparison_issued_at=comparison_issued_at,
        accept_changed_scope=accept_changed_scope,
        retry_failed_shots=retry_failed_shots,
        accept_unknown_outcome_risk=accept_unknown_outcome_risk,
    )
    if unknown_retry_evidence is not None and not accept_unknown_outcome_risk:
        raise FullAutoRunError(
            422, "invalid_control_request",
            "Unknown-outcome evidence is valid only with explicit risk acceptance.",
        )
    if type(defer_claim) is not bool:
        raise FullAutoRunError(422, "invalid_control_request", "defer_claim must be a boolean.")
    timestamp = _now_text(now_text)
    with db.transaction():
        _check_run_session_identity(run_id, expected_session_id)
        replay = _replay_action(run_id, request_id, request_digest, "resume")
        if replay is not None:
            return replay
        row = _require_run(run_id)
        if row["state"] in {"blocked", "cancel_requested"} and bool(row["cancel_intent"]):
            # Reconciliation is scoped to the run's original plan and remains
            # available after unrelated plan drift because it authorizes no
            # generation or input mutation.
            if int(row["revision"]) != body["expected_run_revision"]:
                raise FullAutoRunError(409, "run_revision_stale", "The Full Auto run changed; reload its current state.")
            if int(row["plan_revision"]) != body["expected_plan_revision"]:
                raise FullAutoRunError(409, "plan_revision_stale", "The Full Auto run plan changed; reload its current state.")
        else:
            _check_control_cas(row, body["expected_run_revision"], body["expected_plan_revision"])
        explicit_retry = retry_failed_shots or accept_unknown_outcome_risk
        if explicit_retry:
            # The control transaction can wait behind another SQLite writer.
            # Use a clock read taken after the write lock and CAS are acquired.
            timestamp = _now_text(None)
        retry_control_identity = (
            row["run_id"], int(row["session_id"]), int(row["revision"]),
            int(row["epoch"]), int(row["fencing_token"]), row["state"],
            bool(row["cancel_intent"]),
        )
        retry_failed_ids: list[int] = []
        retry_unknown_attempts: list[dict] = []
        retry_unknown_shot_ids: set[int] = set()
        validated_unknown_retry_evidence: list[tuple[UnknownRetryEvidence, str]] = []

        if row["state"] in {"blocked", "cancel_requested"} and bool(row["cancel_intent"]):
            if retry_failed_shots or accept_unknown_outcome_risk:
                raise FullAutoRunError(
                    409, "cancel_reconciliation_required",
                    "Pending cancellation must settle before a Full Auto retry can start.",
                )
            if scope_digest is not None or comparison_issued_at is not None or accept_changed_scope:
                raise FullAutoRunError(
                    409, "cancel_reconciliation_required",
                    "Pending cancellation must be reconciled before Full Auto can resume.",
                )
            if defer_claim:
                # This task exposes the control plane only. A later reconciliation
                # worker may claim the run after it can observe remote work.
                claim = None
            else:
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
            if _current_authority_issue(row, timestamp) == "dependency_missing":
                raise _authority_error("dependency_missing")
            retry_after_at = row["assistant_retry_after_at"]
            if row["blocker_code"] == "assistant_rate_limited" and retry_after_at is not None:
                if _timestamp(retry_after_at, field_name="assistant Retry-After deadline") > _timestamp(
                    timestamp, field_name="current time",
                ):
                    raise FullAutoRunError(
                        409, "assistant_retry_after",
                        f"Resume is available after {retry_after_at} UTC.",
                        current_run=_view(row),
                    )
            if scope_digest is not None and comparison_issued_at is None:
                raise FullAutoRunError(
                    422, "invalid_control_request",
                    "comparison_issued_at is required with scope_digest.",
                )
            if comparison_issued_at is not None and scope_digest is None:
                raise FullAutoRunError(
                    422, "invalid_control_request",
                    "scope_digest is required with comparison_issued_at.",
                )
            if row["state"] not in {"blocked", "cancelled", "failed", "completed_with_errors"}:
                raise FullAutoRunError(409, "run_not_resumable", "The Full Auto run cannot be resumed in its current state.")
            if row["state"] == "blocked" and row["lease_expires_at"] is not None:
                if _timestamp(row["lease_expires_at"], field_name="run lease") > _timestamp(timestamp, field_name="current time"):
                    raise FullAutoRunError(409, "run_already_claimed", "The Full Auto run already has an owner.")
            if row["state"] == "succeeded":
                raise FullAutoRunError(409, "run_not_resumable", "A successful Full Auto run is not resumable.")
            comparison = None
            scope_accepted = False
            next_state = None
            if scope_digest is not None or eligibility is None or explicit_retry:
                from backend import guided_sessions

                comparison = guided_sessions.compare_full_auto_resume_scope(
                    run_id, config=config, now_text=timestamp,
                    comparison_issued_at=comparison_issued_at,
                )
                if scope_digest is not None and not hmac.compare_digest(
                    comparison["scope_digest"], scope_digest,
                ):
                    raise FullAutoRunError(
                        409, "resume_comparison_stale",
                        "The Full Auto comparison is stale; refresh it before resuming.",
                    )
                scope_accepted = bool(comparison["changed"])
                if scope_accepted and scope_digest is None:
                    allowance = comparison.get("assistant_allowance", {})
                    if (
                        comparison.get("changed_fields") == ["assistant_allowance"]
                        and allowance.get("old_executable_remaining") == 0
                    ):
                        raise FullAutoRunError(
                            409, "budget_exhausted",
                            "The current Full Auto assistant allowance is exhausted; review and accept a new allowance.",
                        )
                    raise FullAutoRunError(
                        409, "authorization_scope_changed",
                        "Review and explicitly accept the changed Full Auto scope before resuming.",
                        current_run={
                            "run_id": run_id,
                            "revision": int(row["revision"]),
                            "plan_revision": int(comparison["plan_revision"]),
                            "scope_digest": comparison["scope_digest"],
                            "comparison_issued_at": comparison["comparison_issued_at"],
                            "changed_fields": comparison["changed_fields"],
                        },
                    )
                if scope_accepted and not accept_changed_scope:
                    raise FullAutoRunError(
                        409, "authorization_scope_changed",
                        "Review and explicitly accept the changed Full Auto scope before resuming.",
                        current_run={
                            "run_id": run_id,
                            "revision": int(row["revision"]),
                            "plan_revision": int(comparison["plan_revision"]),
                            "scope_digest": comparison["scope_digest"],
                            "comparison_issued_at": comparison["comparison_issued_at"],
                            "changed_fields": comparison["changed_fields"],
                        },
                    )
                eligibility = _current_resume_eligibility(
                    row, comparison, now_text=timestamp,
                )
                if explicit_retry:
                    phase = row["resume_phase"] or row["phase"]
                    if phase != "execute":
                        raise FullAutoRunError(
                            409, "retry_phase_invalid",
                            "Only an admitted Full Auto execution can retry existing shots.",
                        )
                    candidates = _retry_candidates(int(row["session_id"]), run_id)
                    quoted_retry_allowance = comparison.get("image_retry_allowance")
                    if not isinstance(quoted_retry_allowance, dict):
                        raise FullAutoRunError(
                            409, "resume_comparison_invalid",
                            "The image retry allowance could not be verified.",
                        )
                    if retry_failed_shots:
                        retry_failed_ids = candidates["failed_shot_ids"]
                        if (
                            not retry_failed_ids
                            or retry_failed_ids != quoted_retry_allowance.get("retry_failed_shot_ids")
                        ):
                            raise FullAutoRunError(
                                409, "retry_targets_changed",
                                "The failed retry targets changed; refresh the comparison.",
                            )
                    if accept_unknown_outcome_risk:
                        retry_unknown_attempts = candidates["unknown_attempts"]
                        unknown_ids = [item["attempt_id"] for item in retry_unknown_attempts]
                        unknown_shots = [item["shot_id"] for item in retry_unknown_attempts]
                        if (
                            not retry_unknown_attempts
                            or unknown_ids != quoted_retry_allowance.get("unknown_attempt_ids")
                            or unknown_shots != quoted_retry_allowance.get("unknown_outcome_shot_ids")
                        ):
                            raise FullAutoRunError(
                                409, "retry_targets_changed",
                                "The unknown retry targets changed; refresh the comparison.",
                            )
                        _validate_unknown_retry_evidence(
                            unknown_retry_evidence, retry_unknown_attempts,
                            run_id=run_id, session_id=int(row["session_id"]),
                            request_id=request_id,
                            run_epoch=int(row["epoch"]) + (1 if row["state"] in _TERMINAL else 0),
                            expected_run_revision=body["expected_run_revision"],
                            expected_plan_revision=body["expected_plan_revision"],
                            scope_digest=scope_digest,
                            comparison_issued_at=comparison_issued_at,
                            timestamp=timestamp,
                        )
                        retry_unknown_shot_ids = {
                            int(item["shot_id"]) for item in retry_unknown_attempts
                        }
                    elif unknown_retry_evidence is not None:
                        raise FullAutoRunError(
                            422, "invalid_control_request",
                            "Unknown-outcome evidence is valid only with explicit risk acceptance.",
                        )
                    if _retry_unsettled_work_count(
                        int(row["session_id"]), run_id, retry_unknown_shot_ids,
                    ) != 0:
                        raise FullAutoRunError(
                            409, "run_work_unsettled",
                            "Other Full Auto work must settle before these shots can be retried.",
                        )
                    retry_increment = (
                        (len(retry_failed_ids) if retry_failed_shots else 0)
                        + (len(retry_unknown_attempts) if accept_unknown_outcome_risk else 0)
                    )
                    if retry_increment <= 0:
                        raise FullAutoRunError(
                            409, "retry_targets_changed",
                            "No currently eligible retry targets match the displayed comparison.",
                        )
                    quote_increment = (
                        (quoted_retry_allowance["additional_if_retry_failed"] if retry_failed_shots else 0)
                        + (
                            quoted_retry_allowance["additional_if_accept_unknown_outcome_risk"]
                            if accept_unknown_outcome_risk else 0
                        )
                    )
                    if quote_increment != retry_increment:
                        raise FullAutoRunError(
                            409, "retry_targets_changed",
                            "The requested image-attempt increment changed; refresh the comparison.",
                        )
                    assistant_quote = comparison.get("assistant_allowance", {})
                    assistant_budget_available = (
                        assistant_quote.get("pending_take_id") is None
                        or assistant_quote.get("new_executable_remaining", 0) > 0
                    )
                    image_budget_after_retry = (
                        quoted_retry_allowance["authorized_image_attempts_remaining"]
                        + retry_increment
                    )
                    retry_eligibility = ResumeEligibility(
                        current_plan_revision=eligibility.current_plan_revision,
                        unresolved_work=0,
                        authorization_valid=eligibility.authorization_valid,
                        policy_supported=eligibility.policy_supported,
                        budget_available=(assistant_budget_available and image_budget_after_retry > 0),
                        session_exclusive=eligibility.session_exclusive,
                        scope_matches=eligibility.scope_matches,
                        planning_enabled=eligibility.planning_enabled,
                        next_state=eligibility.next_state,
                    )
                    next_state = _resume_eligibility(
                        row, retry_eligibility,
                        current_plan_revision=int(comparison["plan_revision"]),
                        scope_accepted=scope_accepted,
                    )
                    if retry_unknown_attempts:
                        validated_unknown_retry_evidence = _validate_unknown_retry_evidence(
                            unknown_retry_evidence, retry_unknown_attempts,
                            run_id=run_id, session_id=int(row["session_id"]),
                            request_id=request_id,
                            run_epoch=int(row["epoch"]) + (1 if row["state"] in _TERMINAL else 0),
                            expected_run_revision=body["expected_run_revision"],
                            expected_plan_revision=body["expected_plan_revision"],
                            scope_digest=scope_digest,
                            comparison_issued_at=comparison_issued_at,
                            timestamp=_now_text(None),
                        )
                else:
                    next_state = _resume_eligibility(
                        row, eligibility,
                        current_plan_revision=int(comparison["plan_revision"]),
                        scope_accepted=scope_accepted,
                    )
                if scope_accepted:
                    if not resource_planning.is_enabled():
                        raise FullAutoRunError(
                            503, "resource_planning_disabled",
                            "Full Auto authorization renewal is disabled by configuration.",
                        )
                    grant = current_authorization(run_id, session_id=int(row["session_id"]))
                    authorization = comparison["_authorization"]
                    if not isinstance(authorization, dict):
                        raise FullAutoRunError(
                            409, "full_auto_authorization_invalid",
                            "The renewed Full Auto authorization could not be verified.",
                        )
                    sequence = int(grant["sequence"]) + 1
                    db.run(
                        """INSERT INTO full_auto_run_authorization
                           (run_id, authorization_sequence, previous_authorization_digest,
                            comparison_digest, authorization_json, scope_digest,
                            dependencies_digest, plan_digest, scope_issued_at,
                            authorization_expires_at, run_revision, plan_revision, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        run_id, sequence, grant["authorization_digest"],
                        comparison["scope_digest"], _canonical_json(authorization),
                        authorization["scope_digest"], comparison["_dependencies_digest"],
                        comparison["_plan_digest"], authorization["scope_issued_at"],
                        authorization["authorization_expires_at"], int(row["revision"]),
                        int(comparison["plan_revision"]), timestamp,
                    )
                    db.run(
                        """UPDATE full_auto_run
                           SET plan_revision = ?, updated_at = ?
                           WHERE run_id = ? AND revision = ? AND fencing_token = ?""",
                        int(comparison["plan_revision"]), timestamp,
                        run_id, row["revision"], row["fencing_token"],
                    )
                    row = _require_run(run_id)

            current_plan = db.one(
                "SELECT plan_revision FROM session_plan WHERE session_id = ?",
                int(row["session_id"]),
            )
            current_plan_revision = int(current_plan["plan_revision"]) if current_plan is not None else 0
            if comparison is None:
                authority_issue = _current_authority_issue(row, timestamp)
                if authority_issue is not None:
                    raise _authority_error(authority_issue)
                next_state = _resume_eligibility(
                    row, eligibility,
                    current_plan_revision=current_plan_revision,
                )
            elif current_plan_revision != int(comparison["plan_revision"]):
                raise FullAutoRunError(
                    409, "resume_comparison_stale",
                    "The session plan changed during Resume; refresh the comparison.",
                )
            is_new_epoch = row["state"] in _TERMINAL
            phase = row["resume_phase"] or row["phase"]
            if phase not in _MAIN_PHASES:
                raise FullAutoRunError(409, "run_reconciliation_required", "The Full Auto run phase must be reconciled before resuming.")
            if row["lease_expires_at"] is not None and _timestamp(
                row["lease_expires_at"], field_name="run lease",
            ) > _timestamp(timestamp, field_name="current time"):
                raise FullAutoRunError(409, "run_already_claimed", "The Full Auto run already has an owner.")
            if explicit_retry:
                # Revalidate the proof and every mutable authorization/fence at
                # the last boundary before retry state and receipts are written.
                timestamp = _now_text(None)
                current_row = _require_run(run_id)
                current_identity = (
                    current_row["run_id"], int(current_row["session_id"]),
                    int(current_row["revision"]), int(current_row["epoch"]),
                    int(current_row["fencing_token"]), current_row["state"],
                    bool(current_row["cancel_intent"]),
                )
                if current_identity != retry_control_identity:
                    raise FullAutoRunError(
                        409, "run_revision_stale",
                        "The Full Auto run changed before retry authorization; refresh its current state.",
                    )
                if (
                    current_row["state"] in {"blocked", "cancel_requested"}
                    and bool(current_row["cancel_intent"])
                ):
                    raise FullAutoRunError(
                        409, "cancel_reconciliation_required",
                        "Pending cancellation must settle before a Full Auto retry can start.",
                    )
                if not resource_planning.is_enabled():
                    raise FullAutoRunError(
                        503, "resource_planning_disabled",
                        "Full Auto retries are disabled by configuration.",
                    )
                authority_issue = _current_authority_issue(current_row, timestamp)
                if authority_issue is not None:
                    raise _authority_error(authority_issue)
                issued_at = _timestamp(
                    comparison_issued_at, field_name="Resume comparison timestamp",
                )
                quote_age = _timestamp(timestamp, field_name="current time") - issued_at
                from backend import guided_sessions

                if quote_age < timedelta(0) or quote_age > guided_sessions._FULL_AUTO_SCOPE_MAX_AGE:
                    raise FullAutoRunError(
                        409, "resume_comparison_stale",
                        "The Full Auto comparison expired; refresh it before retrying.",
                    )
                current_plan = db.one(
                    "SELECT plan_revision FROM session_plan WHERE session_id = ?",
                    int(current_row["session_id"]),
                )
                if (
                    current_plan is None
                    or int(current_plan["plan_revision"]) != int(comparison["plan_revision"])
                    or int(current_row["plan_revision"]) != int(comparison["plan_revision"])
                ):
                    raise FullAutoRunError(
                        409, "resume_comparison_stale",
                        "The session plan changed before retry authorization; refresh the comparison.",
                    )
                if current_row["lease_expires_at"] is not None and _timestamp(
                    current_row["lease_expires_at"], field_name="run lease",
                ) > _timestamp(timestamp, field_name="current time"):
                    raise FullAutoRunError(
                        409, "run_already_claimed",
                        "The Full Auto run already has an owner.",
                    )
                current_candidates = _retry_candidates(int(current_row["session_id"]), run_id)
                if retry_failed_shots and current_candidates["failed_shot_ids"] != retry_failed_ids:
                    raise FullAutoRunError(
                        409, "retry_targets_changed",
                        "The failed retry targets changed; refresh the comparison.",
                    )
                current_unknown = current_candidates["unknown_attempts"]
                if accept_unknown_outcome_risk and (
                    [item["attempt_id"] for item in current_unknown]
                    != [item["attempt_id"] for item in retry_unknown_attempts]
                    or [item["shot_id"] for item in current_unknown]
                    != [item["shot_id"] for item in retry_unknown_attempts]
                ):
                    raise FullAutoRunError(
                        409, "retry_targets_changed",
                        "The unknown retry targets changed; refresh the comparison.",
                    )
                if accept_unknown_outcome_risk:
                    validated_unknown_retry_evidence = _validate_unknown_retry_evidence(
                        unknown_retry_evidence, current_unknown,
                        run_id=run_id, session_id=int(current_row["session_id"]),
                        request_id=request_id,
                        run_epoch=int(current_row["epoch"]) + (
                            1 if current_row["state"] in _TERMINAL else 0
                        ),
                        expected_run_revision=body["expected_run_revision"],
                        expected_plan_revision=body["expected_plan_revision"],
                        scope_digest=scope_digest,
                        comparison_issued_at=comparison_issued_at,
                        timestamp=timestamp,
                    )
                if _retry_unsettled_work_count(
                    int(current_row["session_id"]), run_id,
                    {int(item["shot_id"]) for item in current_unknown}
                    if accept_unknown_outcome_risk else set(),
                ) != 0:
                    raise FullAutoRunError(
                        409, "run_work_unsettled",
                        "Other Full Auto work must settle before these shots can be retried.",
                    )
                fresh_eligibility = _current_resume_eligibility(
                    current_row, comparison, now_text=timestamp,
                )
                if explicit_retry:
                    fresh_eligibility = ResumeEligibility(
                        current_plan_revision=fresh_eligibility.current_plan_revision,
                        unresolved_work=0,
                        authorization_valid=fresh_eligibility.authorization_valid,
                        policy_supported=fresh_eligibility.policy_supported,
                        budget_available=(
                            assistant_budget_available and image_budget_after_retry > 0
                        ),
                        session_exclusive=fresh_eligibility.session_exclusive,
                        scope_matches=fresh_eligibility.scope_matches,
                        planning_enabled=fresh_eligibility.planning_enabled,
                        next_state=fresh_eligibility.next_state,
                    )
                next_state = _resume_eligibility(
                    current_row, fresh_eligibility,
                    current_plan_revision=int(comparison["plan_revision"]),
                    scope_accepted=scope_accepted,
                )
                row = current_row
            if (
                row["state"] == "cancelled" and bool(row["cancel_intent"])
                and phase == "execute"
            ):
                _restore_unstarted_full_auto_shots(row, timestamp)
            deadline = None if defer_claim else run_lease_deadline(timestamp)
            db.run(
                """UPDATE full_auto_run
                   SET state = ?, phase = ?, revision = revision + 1,
                       epoch = epoch + ?, fencing_token = fencing_token + 1,
                       lease_expires_at = ?, lease_started_at = ?, plan_revision = ?,
                       cancel_intent = 0, cancel_intent_at = NULL, blocker_code = NULL,
                       assistant_retry_after_at = NULL, comfy_probe_scope = NULL,
                       comfy_probe_count = 0, comfy_probe_due_at = NULL, resume_phase = NULL,
                   runner_ready_at = CASE WHEN ? = 'waiting_runner' THEN ? ELSE NULL END,
                       evidence_json = ?, updated_at = ?
                   WHERE run_id = ? AND revision = ? AND fencing_token = ?""",
                next_state, phase, 1 if is_new_epoch else 0, deadline,
                None if defer_claim else timestamp, current_plan_revision,
                next_state, timestamp,
                _canonical_json({"kind": "resume", "new_epoch": is_new_epoch}),
                timestamp, run_id, row["revision"], row["fencing_token"],
            )
            row = _require_run(run_id)
            claim = None if defer_claim else _make_claim(row, False)

        view = _action_view(row, "resume", request_id)
        _store_action(run_id, request_id, "resume", request_digest, row, view, timestamp)
        if explicit_retry:
            # Revalidate every retry at the last write boundary, after the
            # action receipt has been serialized. Any refusal rolls the whole
            # control transaction back, including shot and receipt changes.
            timestamp = _now_text(None)
            current_row = _require_run(run_id)
            if (
                int(current_row["session_id"]) != int(row["session_id"])
                or int(current_row["revision"]) != int(row["revision"])
                or int(current_row["epoch"]) != int(row["epoch"])
                or int(current_row["fencing_token"]) != int(row["fencing_token"])
                or current_row["state"] != row["state"]
                or current_row["phase"] != "execute"
                or bool(current_row["cancel_intent"])
            ):
                raise FullAutoRunError(
                    409, "run_revision_stale",
                    "The Full Auto run changed before retry authorization; refresh its current state.",
                )
            if not resource_planning.is_enabled():
                raise FullAutoRunError(
                    503, "resource_planning_disabled",
                    "Full Auto retries are disabled by configuration.",
                )
            authority_issue = _current_authority_issue(current_row, timestamp)
            if authority_issue is not None:
                raise _authority_error(authority_issue)
            from backend import guided_sessions

            quote_age = _timestamp(
                timestamp, field_name="current time",
            ) - _timestamp(comparison_issued_at, field_name="Resume comparison timestamp")
            if (
                quote_age < timedelta(0)
                or quote_age > guided_sessions._FULL_AUTO_SCOPE_MAX_AGE
            ):
                raise FullAutoRunError(
                    409, "resume_comparison_stale",
                    "The Full Auto comparison expired; refresh it before retrying.",
                )
            current_plan = db.one(
                "SELECT plan_revision FROM session_plan WHERE session_id = ?",
                int(current_row["session_id"]),
            )
            if (
                current_plan is None
                or int(current_plan["plan_revision"]) != int(comparison["plan_revision"])
                or int(current_row["plan_revision"]) != int(comparison["plan_revision"])
            ):
                raise FullAutoRunError(
                    409, "resume_comparison_stale",
                    "The session plan changed before retry authorization; refresh the comparison.",
                )
            current_candidates = _retry_candidates(int(current_row["session_id"]), run_id)
            quoted_retry_allowance = comparison.get("image_retry_allowance")
            if not isinstance(quoted_retry_allowance, dict):
                raise FullAutoRunError(
                    409, "resume_comparison_invalid",
                    "The image retry allowance could not be verified.",
                )
            if retry_failed_shots and (
                current_candidates["failed_shot_ids"] != retry_failed_ids
                or current_candidates["failed_shot_ids"]
                != quoted_retry_allowance.get("retry_failed_shot_ids")
            ):
                raise FullAutoRunError(
                    409, "retry_targets_changed",
                    "The failed retry targets changed; refresh the comparison.",
                )
            current_unknown = current_candidates["unknown_attempts"]
            if accept_unknown_outcome_risk and (
                [item["attempt_id"] for item in current_unknown]
                != [item["attempt_id"] for item in retry_unknown_attempts]
                or [item["shot_id"] for item in current_unknown]
                != [item["shot_id"] for item in retry_unknown_attempts]
                or [item["attempt_id"] for item in current_unknown]
                != quoted_retry_allowance.get("unknown_attempt_ids")
                or [item["shot_id"] for item in current_unknown]
                != quoted_retry_allowance.get("unknown_outcome_shot_ids")
            ):
                raise FullAutoRunError(
                    409, "retry_targets_changed",
                    "The unknown retry targets changed; refresh the comparison.",
                )
            if accept_unknown_outcome_risk:
                validated_unknown_retry_evidence = _validate_unknown_retry_evidence(
                    unknown_retry_evidence, current_unknown,
                    run_id=run_id, session_id=int(current_row["session_id"]),
                    request_id=request_id, run_epoch=int(current_row["epoch"]),
                    expected_run_revision=body["expected_run_revision"],
                    expected_plan_revision=body["expected_plan_revision"],
                    scope_digest=scope_digest,
                    comparison_issued_at=comparison_issued_at,
                    timestamp=timestamp,
                )
        if retry_failed_ids:
            placeholders = ",".join("?" for _ in retry_failed_ids)
            db.run(
                f"""UPDATE shot SET status = 'pending', error = '', finished_at = ''
                    WHERE session_id = ? AND id IN ({placeholders})
                      AND status IN ('failed', 'cancelled')""",
                int(row["session_id"]), *retry_failed_ids,
            )
        if retry_unknown_attempts:
            placeholders = ",".join("?" for _ in retry_unknown_attempts)
            db.run(
                f"""UPDATE shot SET status = 'pending', error = '', finished_at = ''
                    WHERE session_id = ? AND id IN ({placeholders})
                      AND status IN ('running', 'failed', 'cancelled')""",
                int(row["session_id"]),
                *(int(item["shot_id"]) for item in retry_unknown_attempts),
            )
        if retry_failed_ids:
            for shot_id in retry_failed_ids:
                attempt = db.one(
                    """SELECT attempt_id, run_id, session_id, shot_id
                       FROM full_auto_execution_attempt
                       WHERE shot_id = ? ORDER BY attempt_number DESC LIMIT 1""",
                    shot_id,
                )
                if (
                    attempt is None or attempt["run_id"] != run_id
                    or int(attempt["session_id"]) != int(row["session_id"])
                    or int(attempt["shot_id"]) != shot_id
                ):
                    raise FullAutoRunError(
                        409, "retry_targets_changed",
                        "The failed retry targets changed; reload status.",
                    )
                db.run(
                    """INSERT INTO full_auto_attempt_retry_authorization (
                           attempt_id, run_id, session_id, shot_id, request_id,
                           run_epoch, authorized_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    attempt["attempt_id"], run_id, int(row["session_id"]), shot_id,
                    request_id, int(row["epoch"]), timestamp,
                )
        if retry_unknown_attempts:
            candidates_by_id = {
                item["attempt_id"]: item for item in retry_unknown_attempts
            }
            evidence_by_id = {
                item.attempt_id: (item, observed_at)
                for item, observed_at in validated_unknown_retry_evidence
            }
            for attempt_id in sorted(candidates_by_id):
                item, observed_at = evidence_by_id[attempt_id]
                candidate = candidates_by_id[attempt_id]
                db.run(
                    """INSERT INTO full_auto_attempt_risk_acceptance (
                           attempt_id, run_id, session_id, shot_id, request_id, run_epoch,
                           queue_snapshot_digest, capability_evidence_digest, observed_at, accepted_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    attempt_id, run_id, int(row["session_id"]), int(candidate["shot_id"]),
                    request_id, int(row["epoch"]), item.queue_snapshot_digest,
                    item.capability_evidence_digest, observed_at, timestamp,
                )
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
            db.run(
                """UPDATE session SET status = 'cancelled' WHERE id = ? AND status = 'running'
                   AND NOT EXISTS (
                       SELECT 1 FROM shot WHERE session_id = ? AND status = 'running'
                   )""",
                int(row["session_id"]), int(row["session_id"]),
            )
            if unresolved_work_count(int(row["session_id"]), claim.run_id) != 0 or db.one(
                """SELECT 1 FROM full_auto_execution_attempt
                   WHERE run_id = ? AND completion_state IN ('pending', 'output_available')
                   LIMIT 1""",
                claim.run_id,
            ) is not None:
                raise FullAutoRunError(
                    409, "run_work_unsettled",
                    "Cancellation cannot settle while owned work remains unresolved.",
                )
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
            authority_issue = _new_work_authority_issue(row, timestamp)
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
