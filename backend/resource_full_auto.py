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
import re
import secrets
from typing import Literal

import db
from backend import resource_planning


RUN_LEASE = timedelta(seconds=60)
RUN_HEARTBEAT_INTERVAL = timedelta(seconds=15)
ASSISTANT_SLOT_LEASE = timedelta(minutes=10)
ASSISTANT_SLOT_HEARTBEAT_INTERVAL = timedelta(seconds=15)
# Keep aligned with enhance.TIMEOUT; only Full Auto dispatches use this bound.
ASSISTANT_DISPATCH_TIMEOUT = timedelta(seconds=300)
# Persisted timestamps have one-second precision, so quarantine rounds safely up.
ASSISTANT_DISPATCH_TIMESTAMP_MARGIN = timedelta(seconds=1)
COMFY_PROBE_DELAYS = (5, 15, 30)
_RUN_CLAIM_KEY = secrets.token_bytes(32)
_MANUAL_EDIT_CLAIM_KEY = secrets.token_bytes(32)
_ASSISTANT_SLOT_CLAIM_KEY = secrets.token_bytes(32)
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
    if (
        row["phase"] not in {"execute", "reconcile"}
        or row["phase"] == "reconcile" and row["resume_phase"] != "execute"
        or bool(row["cancel_intent"])
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto ComfyUI probe owner is stale.")
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
    if (
        row["phase"] not in {"execute", "reconcile"}
        or row["phase"] == "reconcile" and row["resume_phase"] != "execute"
        or bool(row["cancel_intent"])
    ):
        raise FullAutoRunError(409, "run_owner_stale", "The Full Auto ComfyUI probe owner is stale.")
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


def unresolved_work_count(session_id: int, run_id: str) -> int:
    """Count local owned work conservatively; a linked shot may have dispatched."""
    count = 0
    session = db.one("SELECT status FROM session WHERE id = ?", session_id)
    if session is None:
        return 1
    if session["status"] == "running":
        count += 1
    operations = db.q(
        "SELECT state FROM authoring_operation WHERE session_id = ? AND full_auto_run_id = ?",
        session_id, run_id,
    )
    count += sum(item["state"] not in {"succeeded", "failed", "cancelled", "expired"} for item in operations)
    shots = db.q("SELECT status FROM shot WHERE session_id = ?", session_id)
    admission = db.one("SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id)
    # Failed status alone cannot prove a remote attempt settled until the later
    # attempt journal records that observation. Keep failed shots unresolved.
    count += sum(item["status"] not in {"done", "cancelled"} for item in shots)
    return count


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
    image_total = limits["image_attempts_total"]
    image_per_take = limits["image_attempts_per_take"]
    admission = db.one(
        "SELECT 1 FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    )
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
    image_remaining = max(0, image_total) if not submitted_ids and admission is None else 0
    blocker_code = row["blocker_code"]
    authority_issue = _current_authority_issue(row, db.now())
    unresolved = unresolved_work_count(session_id, run_id)
    if blocker_code is None:
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
    policy_is_supported = summary.get("policy_version") == _SUPPORTED_POLICY_VERSION
    authority_repairable = authority_issue not in {"authorization_invalid", "policy_changed"}
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
    reservation = db.one(
        "SELECT dispatched_at, settled_at FROM full_auto_assistant_reservation WHERE reservation_id = ?",
        int(reservation_id),
    )
    if reservation is not None and reservation["settled_at"] is not None:
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
        if row["lease_expires_at"] is None or _timestamp(
            row["lease_expires_at"], field_name="assistant lease",
        ) <= now:
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
) -> int:
    """Durably consume one call immediately before the physical POST."""
    with db.transaction():
        timestamp = _now_text(None)
        _run, _operation, grant, authorization, row, _checked_at = _require_live_assistant_slot(
            slot_claim, run_claim, operation_claim, provider_identity, now_text=timestamp,
        )
        if row["inflight_reservation_id"] is not None:
            raise FullAutoRunError(409, "assistant_slot_busy", "The Full Auto assistant slot is busy.")
        summary, limits = authorization["summary"], authorization["limits"]
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
        return reservation_id


def settle_assistant_dispatch(
    slot_claim: AssistantSlotClaim,
    reservation_id: int,
    metadata: object,
    *,
    now_text: str | None = None,
) -> None:
    """Record only safe transport facts, including an observed failed response."""
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
                   blocker_code = ?, assistant_retry_after_at = ?,
                   comfy_probe_scope = CASE WHEN ? = 'comfy_unavailable' THEN comfy_probe_scope ELSE NULL END,
                   comfy_probe_count = CASE WHEN ? = 'comfy_unavailable' THEN comfy_probe_count ELSE 0 END,
                   comfy_probe_due_at = CASE WHEN ? = 'comfy_unavailable' THEN comfy_probe_due_at ELSE NULL END,
                   evidence_json = ?, updated_at = ?
               WHERE run_id = ? AND epoch = ? AND fencing_token = ?""",
            to_state, next_phase, fence_delta, lease, lease_started, blocker_code,
            retry_after_at, blocker_code, blocker_code, blocker_code,
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
    elif scope_digest is not None or comparison_issued_at is not None or accept_changed_scope:
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
        row = _require_run(run_id)
        _check_control_cas(row, body["expected_run_revision"], body["expected_plan_revision"])
        if derive_work_pending:
            work_pending = unresolved_work_count(int(row["session_id"]), run_id) > 0
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
    image_total = limits["image_attempts_total"]
    has_submission = bool(db.one(
        "SELECT 1 FROM full_auto_submission_receipt WHERE run_id = ? LIMIT 1",
        row["run_id"],
    ))
    has_admission = db.one(
        "SELECT 1 FROM full_auto_runner_admission WHERE run_id = ?", row["run_id"],
    ) is not None
    image_remaining = max(0, image_total) if not has_submission and not has_admission else 0
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

        if row["state"] in {"blocked", "cancel_requested"} and bool(row["cancel_intent"]):
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
            if scope_digest is not None or eligibility is None:
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
            deadline = None if defer_claim else run_lease_deadline(timestamp)
            db.run(
                """UPDATE full_auto_run
                   SET state = ?, phase = ?, revision = revision + 1,
                       epoch = epoch + ?, fencing_token = fencing_token + 1,
                       lease_expires_at = ?, lease_started_at = ?, plan_revision = ?,
                       cancel_intent = 0, cancel_intent_at = NULL, blocker_code = NULL,
                       assistant_retry_after_at = NULL, comfy_probe_scope = NULL,
                       comfy_probe_count = 0, comfy_probe_due_at = NULL, resume_phase = NULL,
                       evidence_json = ?, updated_at = ?
                   WHERE run_id = ? AND revision = ? AND fencing_token = ?""",
                next_state, phase, 1 if is_new_epoch else 0, deadline,
                None if defer_claim else timestamp, current_plan_revision,
                _canonical_json({"kind": "resume", "new_epoch": is_new_epoch}),
                timestamp, run_id, row["revision"], row["fencing_token"],
            )
            row = _require_run(run_id)
            claim = None if defer_claim else _make_claim(row, False)

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
