"""Session-plan persistence and validation for the resource-v1 composition
mode (task 3.1 of ``adopt-resource-session-planning``).

This module is the small additive surface the routes and the rest of the
app call against. Its contract is deliberately narrow:

  * A draft plan is identified by its session (one current draft per
    session). The plan is JSON in the ``session_plan`` table.

  * The save is a compare-and-swap on ``plan_revision``: the caller
    passes the revision it last read, the save refuses (with a
    ``PlanRevisionStale``) if the actual revision differs, and otherwise
    bumps the revision by one. The bump happens inside a SQLite
    transaction so a concurrent save either wins outright or is refused
    with no partial write.

  * The plan's shape is validated. Malformed plans, missing selected
    revisions and out-of-range scopes raise ``PlanValidationError`` with
    a readable message that names the offending field. The validation
    runs BEFORE the database is touched, so a refused save never leaves
    a half-written plan row behind.

  * A session is only allowed to save a plan when it is in
    ``resource-v1`` composition mode. Legacy sessions cannot reach
    ``save_draft`` because the route refuses the request first.

What this module does NOT do: it does not resolve effective wardrobe
state, prepare prompts, queue shots, or invent take fields beyond the
stable take IDs. Those are 3.2 and later tasks. The draft is data only.
"""
from __future__ import annotations

import json
from typing import Any

import db


# -- Constants --------------------------------------------------------------


# The single explicit composition mode this module serves today. A future
# task can add a sibling constant; the validation here refuses anything
# it does not recognise, so an unknown mode cannot sneak in through a
# stale call site.
MODE_RESOURCE_V1 = "resource-v1"

# The two wardrobe-change scopes the draft recognises. ``this_take`` is
# an isolated override; ``from_here`` is a persistent change that walks
# the ordered takes until superseded. Anything else is rejected.
WARDROBE_SCOPE_THIS_TAKE = "this_take"
WARDROBE_SCOPE_FROM_HERE = "from_here"
VALID_WARDROBE_SCOPES: frozenset[str] = frozenset(
    {WARDROBE_SCOPE_THIS_TAKE, WARDROBE_SCOPE_FROM_HERE}
)


# -- Errors -----------------------------------------------------------------


class PlanValidationError(ValueError):
    """The draft is malformed or references a missing revision.

    The message names the offending field so a caller (or a 422 body)
    can tell the operator exactly what to fix. Raised BEFORE the
    database is touched, so a refused save never leaves a half-written
    plan row behind.
    """


class PlanRevisionStale(Exception):
    """The save's expected revision does not match the current row.

    A browser that read the plan at revision N, sat idle while another
    tab saved revision N+1, and then tried to save its own copy at
    revision N is refused here. The current row is left intact.
    """


class SessionNotInResourceMode(Exception):
    """The session is not in resource-v1 mode and cannot hold a plan.

    Legacy sessions have no row in ``session_plan``; attempting to
    save or read a plan for one is a request-shape error, not a
    404. The route maps it to a 400 with the current mode in the
    message so the operator can see why the request was refused.
    """


class SessionNotFound(Exception):
    """No session row exists for the given id.

    Surfaced separately from the not-in-resource-mode error so a 404
    is mapped to a 404 and not a 400.
    """


# -- Validation -------------------------------------------------------------


def validate_draft(plan: Any) -> dict:
    """Validate a resource-v1 plan and return a normalized copy.

    The input shape and what this function checks:

      * top-level is a dict;
      * ``version`` is exactly ``"resource-v1"`` — anything else is
        rejected so an unknown or stale mode cannot sneak in;
      * ``look`` is a string (default ``""``);
      * ``initial_wardrobe`` is a string (default ``""``);
      * ``takes`` is a list of dicts, each with a unique non-empty
        ``take_id`` (the stable identifier the spec requires). Extra
        fields on a take are passed through verbatim, so a future task
        can add ``camera``/``pose``/``expression`` without breaking 3.1;
      * ``selected_resources`` is a list of dicts, each carrying exactly
        the three keys the spec names: ``library_key``,
        ``source_id`` and ``content_digest``. All three must be
        non-empty strings;
      * ``wardrobe_changes`` is a list of dicts, each with a
        ``take_id`` that matches one of the takes, a ``scope`` of
        ``"this_take"`` or ``"from_here"``, and a string
        ``wardrobe`` value.

    Returns a new dict with the validated structure. Selected resources
    and wardrobe changes are normalized to a stable key order so the
    round-trip through ``json.dumps`` is deterministic.
    """
    if not isinstance(plan, dict):
        raise PlanValidationError(
            f"plan must be a dict, got {type(plan).__name__}"
        )

    version = plan.get("version")
    if version != MODE_RESOURCE_V1:
        raise PlanValidationError(
            f"plan.version must be {MODE_RESOURCE_V1!r}, got {version!r}"
        )

    look = plan.get("look", "")
    if not isinstance(look, str):
        raise PlanValidationError(
            f"plan.look must be a string, got {type(look).__name__}"
        )

    initial_wardrobe = plan.get("initial_wardrobe", "")
    if not isinstance(initial_wardrobe, str):
        raise PlanValidationError(
            f"plan.initial_wardrobe must be a string, got "
            f"{type(initial_wardrobe).__name__}"
        )

    takes = plan.get("takes", [])
    if not isinstance(takes, list):
        raise PlanValidationError(
            f"plan.takes must be a list, got {type(takes).__name__}"
        )
    seen_take_ids: set[str] = set()
    for index, take in enumerate(takes):
        if not isinstance(take, dict):
            raise PlanValidationError(
                f"plan.takes[{index}] must be a dict, got "
                f"{type(take).__name__}"
            )
        take_id = take.get("take_id")
        if not isinstance(take_id, str) or not take_id:
            raise PlanValidationError(
                f"plan.takes[{index}].take_id must be a non-empty string, "
                f"got {take_id!r}"
            )
        if take_id in seen_take_ids:
            raise PlanValidationError(
                f"plan.takes contains duplicate take_id {take_id!r}"
            )
        seen_take_ids.add(take_id)

    selected = plan.get("selected_resources", [])
    if not isinstance(selected, list):
        raise PlanValidationError(
            f"plan.selected_resources must be a list, got "
            f"{type(selected).__name__}"
        )
    normalized_selected: list[dict] = []
    for index, sel in enumerate(selected):
        if not isinstance(sel, dict):
            raise PlanValidationError(
                f"plan.selected_resources[{index}] must be a dict, got "
                f"{type(sel).__name__}"
            )
        for key in ("library_key", "source_id", "content_digest"):
            value = sel.get(key)
            if not isinstance(value, str) or not value:
                raise PlanValidationError(
                    f"plan.selected_resources[{index}].{key} must be a "
                    f"non-empty string, got {value!r}"
                )
        normalized_selected.append({
            "library_key": sel["library_key"],
            "source_id": sel["source_id"],
            "content_digest": sel["content_digest"],
        })

    changes = plan.get("wardrobe_changes", [])
    if not isinstance(changes, list):
        raise PlanValidationError(
            f"plan.wardrobe_changes must be a list, got "
            f"{type(changes).__name__}"
        )
    normalized_changes: list[dict] = []
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}] must be a dict, got "
                f"{type(change).__name__}"
            )
        change_take_id = change.get("take_id")
        if change_take_id not in seen_take_ids:
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}].take_id "
                f"{change_take_id!r} does not match any take in plan.takes"
            )
        scope = change.get("scope")
        if scope not in VALID_WARDROBE_SCOPES:
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}].scope must be one of "
                f"{sorted(VALID_WARDROBE_SCOPES)}, got {scope!r}"
            )
        wardrobe = change.get("wardrobe")
        if not isinstance(wardrobe, str):
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}].wardrobe must be a "
                f"string, got {type(wardrobe).__name__}"
            )
        normalized_changes.append({
            "take_id": change_take_id,
            "scope": scope,
            "wardrobe": wardrobe,
        })

    return {
        "version": MODE_RESOURCE_V1,
        "look": look,
        "initial_wardrobe": initial_wardrobe,
        "takes": list(takes),
        "selected_resources": normalized_selected,
        "wardrobe_changes": normalized_changes,
    }


def validate_selected_resources(selected: list[dict]) -> None:
    """Every selected resource must exist as an immutable revision.

    The plan pins each selected resource to the exact
    ``(library_key, source_id, content_digest)`` triple it was chosen
    from. A change to the source creates a NEW immutable revision; the
    old one stays readable, so a draft that pointed at the old revision
    is still valid as long as that triple is in the table. A triple
    that was never recorded, or that names a library that was never
    registered, is refused — the alternative (silently substituting the
    latest revision) is the bug the spec names in the "selected
    resources identify one exact immutable revision" rule.

    Raises ``PlanValidationError`` naming the offending triple. Runs
    BEFORE the database is touched in the save path, so a refused save
    never leaves a half-written plan row behind.
    """
    for index, sel in enumerate(selected):
        library_key = sel["library_key"]
        source_id = sel["source_id"]
        content_digest = sel["content_digest"]
        library = db.one(
            "SELECT id FROM resource_library WHERE library_key = ?",
            library_key,
        )
        if library is None:
            raise PlanValidationError(
                f"plan.selected_resources[{index}] references unregistered "
                f"library_key={library_key!r} source_id={source_id!r} "
                f"content_digest={content_digest!r}"
            )
        revision = db.one(
            "SELECT id FROM asset_revision "
            "WHERE library_id = ? AND source_id = ? AND content_digest = ?",
            library["id"], source_id, content_digest,
        )
        if revision is None:
            raise PlanValidationError(
                f"plan.selected_resources[{index}] references missing "
                f"revision library_key={library_key!r} source_id={source_id!r} "
                f"content_digest={content_digest!r}: no immutable "
                f"asset_revision row matches this triple"
            )


# -- Persistence ------------------------------------------------------------


def current_revision(session_id: int) -> int:
    """Return the current ``plan_revision`` for the session, or 0.

    A session with no row in ``session_plan`` reads as 0, which is the
    revision a first save is expected to pass. The function is a thin
    read; it does not raise on a missing session, so a caller that
    wants to distinguish "no plan" from "no session" should check the
    session row separately.
    """
    row = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?",
        session_id,
    )
    if row is None:
        return 0
    return int(row["plan_revision"])


def read_composition_mode(settings: Any) -> str:
    """Return the session's ``composition_mode`` from its ``settings`` JSON.

    The mode is stored inside the session's ``settings`` column, NOT
    on a dedicated row, so the value lives wherever the rest of the
    session's free-form settings live. A missing key reads as the
    empty string — the legacy default — and any value other than
    ``""`` or ``MODE_RESOURCE_V1`` is treated as a malformed session
    and the empty string is returned so a downstream caller falls
    through to the legacy path. Storing it in ``settings`` is what
    keeps the design's "absent means legacy" rule on the same
    JSON-as-TEXT idiom the rest of the column already follows.
    """
    if not isinstance(settings, str) or not settings:
        return ""
    try:
        decoded = json.loads(settings)
    except json.JSONDecodeError:
        return ""
    if not isinstance(decoded, dict):
        return ""
    value = decoded.get("composition_mode", "")
    if not isinstance(value, str):
        return ""
    if value not in ("", MODE_RESOURCE_V1):
        return ""
    return value


def get_draft(session_id: int) -> dict | None:
    """Return the current draft, or ``None`` if no plan exists.

    The returned dict has two keys: ``plan_revision`` (the integer the
    next save must echo back as ``expected_revision``) and ``plan``
    (the validated JSON decoded back into Python). Returns ``None``
    when the session has no plan row — the route maps that to a 404.
    """
    row = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    if row is None:
        return None
    return {
        "plan_revision": int(row["plan_revision"]),
        "plan": json.loads(row["plan_json"]),
    }


def save_draft(session_id: int, plan: Any, expected_revision: int) -> int:
    """Save a draft plan with a compare-and-swap on the revision.

    The save runs four steps, in this order:

      1. Confirm the session exists and is in ``resource-v1`` mode. A
         missing session raises ``SessionNotFound``; a session in any
         other mode raises ``SessionNotInResourceMode``. The mode is
         read from the session's ``settings`` JSON, not from a
         dedicated column, so an absent or malformed key reads as
         legacy.

      2. Validate the plan. A malformed plan or one whose selected
         resources are missing from the immutable revision table raises
         ``PlanValidationError``. Validation runs BEFORE the database
         is touched.

      3. Inside a SQLite transaction (``BEGIN IMMEDIATE``), read the
         current revision and compare it to ``expected_revision``. A
         mismatch raises ``PlanRevisionStale`` and the transaction
         rolls back without writing anything.

      4. Otherwise, INSERT a new row (when the session had no plan) or
         UPDATE the existing one with ``plan_revision + 1``. The
         transaction commits on exit.

    Returns the new ``plan_revision``. The CAS check plus the
    transaction wrap together are what pin "a stale save must not
    overwrite a newer draft" to the schema: there is no path in this
    function that bumps a revision by more than one, and there is no
    path that writes without first comparing.
    """
    session = db.one(
        "SELECT id, settings FROM session WHERE id = ?",
        session_id,
    )
    if session is None:
        raise SessionNotFound(f"session {session_id} not found")
    mode = read_composition_mode(session["settings"])
    if mode != MODE_RESOURCE_V1:
        raise SessionNotInResourceMode(
            f"session {session_id} composition_mode is {mode!r}, "
            f"expected {MODE_RESOURCE_V1!r}"
        )

    validated = validate_draft(plan)
    validate_selected_resources(validated["selected_resources"])

    encoded = json.dumps(
        validated, ensure_ascii=False, separators=(",", ":"),
    )
    now = db.now()

    with db.transaction():
        current = db.one(
            "SELECT plan_revision FROM session_plan WHERE session_id = ?",
            session_id,
        )
        actual = 0 if current is None else int(current["plan_revision"])
        if actual != expected_revision:
            raise PlanRevisionStale(
                f"session {session_id} plan revision is {actual}, "
                f"expected {expected_revision}; refusing to overwrite "
                f"newer draft"
            )
        new_revision = actual + 1
        if actual == 0:
            db.run(
                "INSERT INTO session_plan "
                "(session_id, mode, plan_revision, plan_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                session_id, MODE_RESOURCE_V1, new_revision, encoded, now, now,
            )
        else:
            db.run(
                "UPDATE session_plan SET plan_revision = ?, plan_json = ?, "
                "updated_at = ? WHERE session_id = ?",
                new_revision, encoded, now, session_id,
            )
    return new_revision
