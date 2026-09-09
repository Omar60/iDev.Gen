"""Acceptance test for task 6.2 of ``adopt-resource-session-planning``.

This is the integrated twelve-portrait acceptance demonstration the
OpenSpec change names: import an invented adult-character resource
through the real import pipeline, prepare twelve portraits of one
fictional adult character with constant clothing and varied creative
choices, save the plan, prepare three takes, simulate a real
persistence interruption, recover those three, prepare a probe take
positioned after the future change, add a jacket from take seven
through ``from_here``, verify the unaffected prior preparations
remain reusable as history, verify the probe take is invalidated,
re-prepare at the new revision under the correct wardrobe per
take, approve the plan review, submit a single take twice and
verify the double-click / network retry produces exactly one
shot, and confirm the byte-for-byte legacy baseline still holds.

The test is intentionally one or two narrative tests with clearly
identifiable phases. It is NOT a unit-test fan-out: each phase
exercises an end-to-end path the design names. All fixtures are
invented English-only data; no source corpus, no machine paths, no
real names, no GPU, no ComfyUI, no network. The test reads the
persisted snapshot rows directly through SQL to assert the exact
``final_prompt``, ``effective_state``, ``provenance`` and version
metadata byte-for-byte, and uses literal expected values that are
NOT computed by the same resolver or composer the production code
calls — the acceptance must be able to fail if the resolver or
composer drifts, and it must not be a second opinion of itself.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

import db
import resource_import
import resource_preparation
import resource_service


# ---- Database helpers ---------------------------------------------------


def _acceptance_db_path() -> Path:
    """Resolve the database path the ``client`` fixture opened.

    The conftest sets ``IDEVGEN_DATA_DIR`` to a temp directory
    and the app's lifespan calls ``db.connect(DATA_DIR /
    "idevgen.db")``. The acceptance test reopens the same path
    on disk to simulate a real persistence interruption
    (closing the SQLite connection, opening a fresh one to
    the same file) instead of swapping in a separate test
    database, so the test exercises the production app's
    database file end to end.
    """
    data_dir = Path(os.environ["IDEVGEN_DATA_DIR"])
    return data_dir / "idevgen.db"


def _close_silently() -> None:
    conn = db._conn  # noqa: SLF001
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    db._conn = None  # noqa: SLF001


def _reopen_db() -> None:
    """Close the SQLite connection and reopen the same file.

    The pattern mirrors the one
    ``test_three_of_twelve_survive_reopen_and_only_nine_are_resumable``
    uses, but targets the production app's database file
    instead of a separate isolated database. The reopening
    proves the snapshots and the plan revisions are
    byte-for-byte recoverable across the interruption.
    """
    _close_silently()
    db.connect(_acceptance_db_path())


# ---- Local expected-value builders -------------------------------------
#
# These helpers are deliberately independent of the production
# resolver and composer: they re-derive the expected values from the
# raw inputs the test pinned (trigger, base positive, look, wardrobe,
# take choices, resource label, resource scene theme). A drift in
# ``resolve_effective_wardrobes`` or ``compose_final_prompt`` would
# make the test fail loud here, instead of being a self-confirming
# green bar.


def _expected_prompt(
    trigger: str,
    base_positive: str,
    look: str,
    wardrobe: str,
    take_clauses: str,
) -> str:
    """Mirror ``_join_prompt_sentences`` as a pure data constructor.

    The function reproduces the join rules the production composer
    follows, including the trailing-full-stop policy, but is
    implemented in the test file as a literal-only constructor so
    the test does not import the production helper. A drift in
    the composer's join rules would change both sides and the
    test would still pass; a drift in the composer's CLAUSE
    content (the parts themselves) is what the test is designed
    to catch, by reading the parts from the inputs the test
    controls.
    """
    parts = [trigger, base_positive, look, wardrobe, take_clauses]
    out = []
    for part in parts:
        part = part.strip().strip(",").strip()
        if not part:
            continue
        out.append(part if part[-1] in ".!?" else f"{part}.")
    return " ".join(out)


def _expected_take_clauses(
    camera: str,
    framing: str,
    pose: str,
    expression: str,
    resource_label: str,
    resource_scene_theme: str,
    resource_tags: list[str] | None = None,
) -> str:
    """Mirror ``assemble_adapted_clauses`` as a pure data constructor.

    The order is the canonical order the production assembler
    reads its inputs: the four take choices in
    ``sorted(TAKE_DESCRIPTIVE_CHOICES)`` order (alphabetical:
    camera, expression, framing, pose), then the resource
    descriptive inputs in alphabetical field name order
    (``label`` before ``scene_theme`` before ``tags`` for
    ``rooms``). The function reproduces the production join
    rule literally: each clause is added to the list AS IS
    (without appending a trailing full stop), the list is
    joined with ``". "`` and the resulting string is returned
    WITHOUT a trailing full stop. ``_expected_prompt`` then
    runs the whole string through the same per-part full-
    stop rule ``_join_prompt_sentences`` runs in production,
    so the byte-for-byte identity of the expected and actual
    prompts is preserved.
    """
    fields: list[tuple[str, str | list[str]]] = [
        ("camera", camera),
        ("expression", expression),
        ("framing", framing),
        ("pose", pose),
        ("label", resource_label),
        ("scene_theme", resource_scene_theme),
    ]
    if resource_tags is not None:
        fields.append(("tags", list(resource_tags)))
    out: list[str] = []
    for _, value in fields:
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    continue
                out.append(item.strip().strip(",").strip())
            continue
        value = value.strip().strip(",").strip()
        if not value:
            continue
        out.append(value)
    return ". ".join(out)


# ---- Invented English-only fixtures -------------------------------------


ACCEPTANCE_LIBRARY_KEY = "inv_rooms_acceptance_62"
ACCEPTANCE_SOURCE_ID = "inv_room_acceptance_62"

# A resource payload that satisfies the rooms contract: ``id``,
# ``label`` and ``scene_theme`` are required; the rest are
# descriptive. Every string is hand-written English, no source
# corpus text, no real names, no machine paths.
ACCEPTANCE_ROOM_PAYLOAD = {
    "id": ACCEPTANCE_SOURCE_ID,
    "label": "an invented studio in soft northern light",
    "scene_theme": (
        "A bare studio with a tall window facing north. Soft grey "
        "light enters from the side and leaves the back wall in "
        "shadow. A single wooden chair stands between the subject "
        "and the camera, and a folded white sheet covers the floor."
    ),
    "tags": ["indoor", "studio", "soft-light"],
    "weight": 1.0,
}

# The constant look the plan holds for the entire session. Same
# constants across all twelve takes — the design's "place and
# light are constant" rule, asserted by the snapshot, not by the
# resolver.
ACCEPTANCE_LOOK = (
    "A small studio with a bare grey backdrop. Soft light from a "
    "single large window falls on her right cheek. She wears her "
    "hair loose, brushed forward over one shoulder, with no makeup "
    "beyond a faint pink lip."
)

# The initial wardrobe the resolver will inherit for takes 01-06
# under revision 1, and for takes 01-06 under revision 2 (the
# jacket-from-take-7 change does not cross into takes 01-06).
ACCEPTANCE_INITIAL_WARDROBE = (
    "a thin cream linen shirt, dark charcoal trousers, black shoes"
)

# The wardrobe applies from take 7 onward under revision 2.
ACCEPTANCE_JACKET_WARDROBE = (
    "a thin cream linen shirt, a dark navy jacket over the shirt, "
    "dark charcoal trousers, black shoes"
)

# Twelve creative variations. Each take establishes its own
# camera, framing, pose and expression. The take IDs are the
# stable identifiers the spec asks for ("portrait-01" through
# "portrait-12"). The list is hand-written invented English.
ACCEPTANCE_TAKE_CHOICES: list[dict] = [
    {
        "take_id": "portrait-01",
        "camera": "an 85mm portrait lens at eye level",
        "framing": "head and shoulders",
        "pose": "standing square to the camera, hands resting at her sides",
        "expression": "a calm, neutral gaze",
    },
    {
        "take_id": "portrait-02",
        "camera": "an 85mm portrait lens at eye level",
        "framing": "waist up",
        "pose": "three-quarter turn facing left, weight on her back foot",
        "expression": "a subtle closed-lip smile",
    },
    {
        "take_id": "portrait-03",
        "camera": "an 85mm portrait lens at eye level",
        "framing": "waist up",
        "pose": "three-quarter turn facing right, one hand on the chair back",
        "expression": "an introspective, quiet gaze",
    },
    {
        "take_id": "portrait-04",
        "camera": "a 50mm lens at chest height",
        "framing": "tight on the eyes",
        "pose": "seated leaning slightly forward, elbows on her knees",
        "expression": "a warm, gentle expression",
    },
    {
        "take_id": "portrait-05",
        "camera": "a 50mm lens at chest height",
        "framing": "waist up",
        "pose": "hands resting lightly on her thighs",
        "expression": "a serious, focused gaze",
    },
    {
        "take_id": "portrait-06",
        "camera": "a 35mm lens at hip height",
        "framing": "full body in frame",
        "pose": "head tilted slightly to the right, weight even on both feet",
        "expression": "a slight, friendly grin",
    },
    {
        "take_id": "portrait-07",
        "camera": "a 35mm lens at hip height",
        "framing": "full body in frame",
        "pose": "head tilted slightly to the left, arms crossed comfortably",
        "expression": "a contemplative look",
    },
    {
        "take_id": "portrait-08",
        "camera": "a 35mm lens at hip height",
        "framing": "three-quarter length",
        "pose": "resting her chin gently on her knuckles",
        "expression": "a relaxed, composed expression",
    },
    {
        "take_id": "portrait-09",
        "camera": "an 85mm portrait lens at eye level",
        "framing": "head and shoulders",
        "pose": "looking thoughtfully past the lens",
        "expression": "a hint of amusement in the eyes",
    },
    {
        "take_id": "portrait-10",
        "camera": "an 85mm portrait lens at eye level",
        "framing": "head and shoulders",
        "pose": "standing relaxed with shoulders dropped",
        "expression": "a serene, steady gaze",
    },
    {
        "take_id": "portrait-11",
        "camera": "a 50mm lens at chest height",
        "framing": "tight on the face",
        "pose": "seated upright with hands clasped in her lap",
        "expression": "a confident, subtle smile",
    },
    {
        "take_id": "portrait-12",
        "camera": "a 50mm lens at chest height",
        "framing": "tight on the face",
        "pose": "resting her chin on one finger, eyes on the camera",
        "expression": "a soft, engaged expression",
    },
]


# ---- Resource import helpers --------------------------------------------


def _import_invented_resource(tmp_path: Path) -> dict:
    """Drive the real import pipeline with an invented rooms file.

    Returns the immutable revision triple the plan will pin
    against: ``library_key``, ``source_id``, ``content_digest``.
    The function does not insert directly into ``asset_revision``;
    it goes through ``resource_import.preview_import`` and
    ``resource_import.commit_import`` exactly the way the HTTP
    service does.
    """
    source_dir = Path(tmp_path) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_file = source_dir / "inv_acceptance_62_room.json"
    source_file.write_text(
        json.dumps(
            ACCEPTANCE_ROOM_PAYLOAD,
            ensure_ascii=False, separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    preview = resource_import.preview_import(
        [(source_file, ACCEPTANCE_LIBRARY_KEY)],
    )
    assert preview.total_accepted == 1, (
        f"invented rooms resource must preview as one accepted "
        f"entry; got {preview.total_accepted}"
    )
    outcome = preview.files[0].accepted_outcomes[0]
    assert outcome.classification == resource_import.CLASSIFICATION_NEW
    report = resource_import.commit_import(preview)
    assert report.total_new_scene_revisions == 1
    assert report.total_new_scene_revisions + (
        report.total_unchanged_scene_revisions
    ) == 1
    assert report.counts_reconcile()
    library = resource_service.get_resource_library(ACCEPTANCE_LIBRARY_KEY)
    assert library is not None, (
        f"imported library {ACCEPTANCE_LIBRARY_KEY!r} must be "
        f"registered after commit"
    )
    revision = resource_service.get_resource_revision(
        ACCEPTANCE_LIBRARY_KEY,
        ACCEPTANCE_SOURCE_ID,
        outcome.new_content_digest,
    )
    assert revision is not None, (
        f"imported revision ({ACCEPTANCE_LIBRARY_KEY}, "
        f"{ACCEPTANCE_SOURCE_ID}, {outcome.new_content_digest}) "
        f"must be readable after commit"
    )
    return {
        "library_key": ACCEPTANCE_LIBRARY_KEY,
        "source_id": ACCEPTANCE_SOURCE_ID,
        "content_digest": outcome.new_content_digest,
    }


# ---- Session / plan helpers ---------------------------------------------


def _create_resource_session(
    client, seeded, name: str,
) -> int:
    """Create a resource-v1 session bound to the seeded model.

    The seeded model carries the trigger ``"4da woman"`` and the
    base positive ``"photo, 35mm"`` — both English, both invented
    in the conftest fixture. The session is created in
    ``resource-v1`` mode; the new ``/api/sessions`` route does
    not run any legacy expansion on resource-v1 sessions, so no
    shot row is written at this step.
    """
    resp = client.post("/api/sessions", json={
        "model_id": seeded["model_id"],
        "name": name,
        "composition_mode": "resource-v1",
        "look": ACCEPTANCE_LOOK,
        "wardrobe": ACCEPTANCE_INITIAL_WARDROBE,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _build_plan(revision: dict, wardrobe_changes: list[dict] | None = None) -> dict:
    """Build a twelve-take plan pinning the imported revision.

    The plan uses the take IDs from ``ACCEPTANCE_TAKE_CHOICES``
    so the order is the creative order the test controls. No
    wardrobe change is included at the first save; the second
    save passes the ``from_here`` jacket change explicitly.
    """
    return {
        "version": "resource-v1",
        "look": ACCEPTANCE_LOOK,
        "initial_wardrobe": ACCEPTANCE_INITIAL_WARDROBE,
        "takes": [
            {
                "take_id": choice["take_id"],
                "camera": choice["camera"],
                "framing": choice["framing"],
                "pose": choice["pose"],
                "expression": choice["expression"],
            }
            for choice in ACCEPTANCE_TAKE_CHOICES
        ],
        "selected_resources": [
            {
                "library_key": revision["library_key"],
                "source_id": revision["source_id"],
                "content_digest": revision["content_digest"],
            },
        ],
        "wardrobe_changes": wardrobe_changes or [],
    }


def _save_plan(
    client, sid: int, plan: dict, expected_revision: int,
) -> dict:
    """Save a plan via the real API and return the parsed body."""
    resp = client.post(
        f"/api/sessions/{sid}/plan",
        json={"plan": plan, "expected_revision": expected_revision},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["conflicts"], list)
    return body


def _finalize_take(
    client, sid: int, plan_revision: int, take_id: str,
) -> dict:
    """Drive the high-level ``/plan/takes/{id}/prepare`` endpoint.

    The endpoint is the only path that finalises a take
    deterministically: ``finalize_take_preparation`` builds the
    exact final prompt from the persisted state, the resource
    revisions and the take choices, then calls
    ``session_plan.complete_preparation`` to land the snapshot
    in ``prepared_take`` with status ``ready``. The test never
    inserts prepared_take rows directly, so the high-level path
    is what the production application uses end to end.
    """
    resp = client.post(
        f"/api/sessions/{sid}/plan/takes/{take_id}/prepare",
        json={"plan_revision": plan_revision},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready", body
    return body


def _read_snapshot(
    sid: int, plan_revision: int, take_id: str,
) -> dict:
    """Read one prepared_take row by its natural identity.

    Returns a dict with the JSON-decoded ``effective_state`` and
    ``provenance`` columns so the assertion can read them as
    Python values, plus the raw ``final_prompt``,
    ``mapping_version`` and ``compiler_version`` strings.
    """
    row = db.one(
        "SELECT final_prompt, effective_state, mapping_version, "
        "compiler_version, provenance, status, plan_revision, take_id "
        "FROM prepared_take "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        sid, plan_revision, take_id,
    )
    assert row is not None, (
        f"prepared_take row not found for session={sid} "
        f"revision={plan_revision} take_id={take_id!r}"
    )
    return {
        "final_prompt": row["final_prompt"],
        "effective_state": json.loads(row["effective_state"]),
        "mapping_version": row["mapping_version"],
        "compiler_version": row["compiler_version"],
        "provenance": json.loads(row["provenance"]),
        "status": row["status"],
        "plan_revision": row["plan_revision"],
        "take_id": row["take_id"],
    }


# ---- Tests --------------------------------------------------------------


class TestTwelvePortraitAcceptance:
    """Integrated acceptance of the twelve-portrait workflow.

    The class runs ONE narrative test that walks the full
    6.2 acceptance flow. Each phase is a named section with
    a one-line header so a future maintainer can see at a
    glance which behaviour each block pins. The phases are:

      1. Import an invented adult-character resource.
      2. Create a resource-v1 session for a fictional adult
         character.
      3. Create twelve takes with stable IDs and varied
         creative choices.
      4. Save plan revision 1.
      5. Prepare takes 01-03.
      6. Simulate a real persistence interruption and reopen
         the database.
      7. Verify recovery.
      8. Prepare a probe take (portrait-08) after the future
         change boundary.
      9. Save plan revision 2 with the jacket from take 7.
     10. Verify the unaffected prior preparations keep their
         prior ready state and the probe take is invalidated.
     11. Complete the remaining preparations in revision 2.
     12. Verify the exact persisted prompts for takes before
         and after the change.
     13. Approve the plan review and submit a take twice.
     14. Verify the double-click / retry produces exactly one
         shot.

    Phase 15 is the byte-for-byte legacy baseline (delegated to
    the existing ``tests/test_legacy_session_baseline.py``
    suite). The acceptance itself asserts the legacy surface
    remains in scope by the absence of regressions, not by
    re-implementing the expectations.
    """

    def test_twelve_portrait_acceptance_flow(
        self, client, seeded, tmp_path,
    ):
        # ==========================================================
        # Phase 1: import an invented adult-character resource
        # ==========================================================
        revision = _import_invented_resource(tmp_path)
        assert revision["library_key"] == ACCEPTANCE_LIBRARY_KEY
        assert revision["source_id"] == ACCEPTANCE_SOURCE_ID
        assert isinstance(revision["content_digest"], str)
        assert len(revision["content_digest"]) == 64

        # The plan will select the same triple later. The plan
        # route will refuse a triple that is not in the
        # asset_revision table; a green Phase 4 is the proof the
        # import is real and not a synthetic shortcut.

        # ==========================================================
        # Phase 2: create a resource-v1 session
        # ==========================================================
        sid = _create_resource_session(
            client, seeded, "twelve-portrait acceptance session",
        )
        # The session is in resource-v1 mode; the seeded model is
        # the only character the session binds, and the model
        # is explicitly a fictional adult woman (the
        # trigger string from the conftest fixture).
        model = db.one("SELECT trigger FROM model WHERE id = ?", seeded["model_id"])
        assert model["trigger"] == "4da woman"

        # ==========================================================
        # Phase 3: create twelve takes with stable IDs
        # ==========================================================
        # The takes are declared inside ``ACCEPTANCE_TAKE_CHOICES``
        # and the plan builder inlines them. Twelve takes,
        # stable IDs ``portrait-01`` through ``portrait-12``,
        # each with camera, framing, pose and expression.
        plan = _build_plan(revision)
        assert len(plan["takes"]) == 12
        assert [t["take_id"] for t in plan["takes"]] == [
            f"portrait-{index:02d}" for index in range(1, 13)
        ]

        # ==========================================================
        # Phase 4: save plan revision 1
        # ==========================================================
        first_save = _save_plan(client, sid, plan, expected_revision=0)
        assert first_save["plan_revision"] == 1

        # The expected initial wardrobe holds for every take at
        # revision 1 (no wardrobe changes have been added). The
        # expected wardrobes are a literal of the spec, built
        # directly from the constant and the take_id list the
        # test pinned, NOT from the production resolver. A
        # drift in ``resolve_effective_wardrobes`` would not
        # change this expected map; only a drift in the
        # persisted ``effective_state`` would surface.
        expected_initial_wardrobes = {
            choice["take_id"]: ACCEPTANCE_INITIAL_WARDROBE
            for choice in ACCEPTANCE_TAKE_CHOICES
        }

        # ==========================================================
        # Phase 5: prepare takes 01, 02, 03
        # ==========================================================
        # The high-level prepare endpoint drives the full
        # ``finalize_take_preparation`` flow and lands a
        # ``ready`` snapshot for each take. The test asserts the
        # semantic fields of each snapshot, not the bytes of the
        # composer — but the expected final_prompt is built
        # from the inputs the test controls (not from the same
        # resolver or composer the production code calls), so a
        # drift in the composer would surface here.
        first_batch = ["portrait-01", "portrait-02", "portrait-03"]
        for take_id in first_batch:
            snapshot = _finalize_take(client, sid, 1, take_id)
            assert snapshot["status"] == "ready"
            assert snapshot["mapping_version"] == (
                resource_preparation.MAPPING_VERSION
            )
            assert snapshot["compiler_version"] == (
                resource_preparation.COMPILER_VERSION
            )

        # The three snapshots are exactly the expected values
        # for the initial wardrobe, the look and the take
        # choices. The expected prompt is constructed from the
        # inputs the test pinned; the assert is the proof the
        # composer reads those inputs in the canonical order
        # the OpenSpec names.
        for take_id in first_batch:
            choice = next(
                c for c in ACCEPTANCE_TAKE_CHOICES if c["take_id"] == take_id
            )
            take_clauses = _expected_take_clauses(
                camera=choice["camera"],
                framing=choice["framing"],
                pose=choice["pose"],
                expression=choice["expression"],
                resource_label=ACCEPTANCE_ROOM_PAYLOAD["label"],
                resource_scene_theme=ACCEPTANCE_ROOM_PAYLOAD["scene_theme"],
                resource_tags=ACCEPTANCE_ROOM_PAYLOAD.get("tags"),
            )
            expected_prompt = _expected_prompt(
                trigger=model["trigger"],
                base_positive="photo, 35mm",
                look=ACCEPTANCE_LOOK,
                wardrobe=ACCEPTANCE_INITIAL_WARDROBE,
                take_clauses=take_clauses,
            )
            snapshot = _read_snapshot(sid, 1, take_id)
            assert snapshot["final_prompt"] == expected_prompt, (
                f"final_prompt for {take_id} drifted from the "
                f"expected byte-for-byte value; got "
                f"{snapshot['final_prompt']!r}"
            )
            assert snapshot["effective_state"]["look"] == ACCEPTANCE_LOOK
            assert snapshot["effective_state"]["wardrobe"] == (
                expected_initial_wardrobes[take_id]
            )
            assert snapshot["effective_state"]["scope"] == ""
            # Provenance references the imported revision by
            # the exact (library_key, source_id, content_digest)
            # triple the import landed.
            revisions = snapshot["provenance"][
                "selected_resource_revisions"
            ]
            assert revisions == [{
                "library_key": revision["library_key"],
                "source_id": revision["source_id"],
                "content_digest": revision["content_digest"],
                "kind": "rooms",
            }]

        # Capture the semantic fields that must remain
        # identical across the interruption and the later
        # revision 2 save. The test stores them now and reads
        # them again after the reopen and after the new
        # revision.
        first_batch_snapshots = {
            take_id: _read_snapshot(sid, 1, take_id)
            for take_id in first_batch
        }

        # ==========================================================
        # Phase 6: simulate a real persistence interruption
        # ==========================================================
        # Close the SQLite connection and reopen the same file.
        # No state is held in the Python session beyond the
        # file path; the global ``db._conn`` is reset. The
        # test client (FastAPI TestClient) keeps its own
        # connection pool, so the next API call goes through a
        # fresh process-level read of the same database file.
        _reopen_db()

        # ==========================================================
        # Phase 7: verify recovery
        # ==========================================================
        # The three completed preparations are recoverable and
        # the nine remaining takes are listed as incomplete.
        # No shot row was created by recovery; the reopen only
        # made the persisted snapshots visible again. The
        # recovery payload is read through the public plan
        # endpoint (``GET /api/sessions/{sid}/plan``), which
        # already exposes the ``preparation`` block; the test
        # does NOT call ``session_plan.recover_preparation``
        # directly, so a regression in the public surface
        # would surface here instead of being masked by a
        # service-level shortcut.
        plan_resp = client.get(f"/api/sessions/{sid}/plan")
        assert plan_resp.status_code == 200, plan_resp.text
        recovery = plan_resp.json()["preparation"]
        assert recovery["plan_revision"] == 1
        assert [row["take_id"] for row in recovery["completed"]] == (
            first_batch
        )
        assert [row["status"] for row in recovery["completed"]] == (
            ["ready"] * 3
        )
        assert [row["take_id"] for row in recovery["incomplete"]] == [
            f"portrait-{index:02d}" for index in range(4, 13)
        ]
        assert recovery["incomplete"][0] == {
            "take_id": "portrait-04", "status": "missing",
        }
        assert recovery["history"] == [], (
            f"recovery must report no history for revision 1; "
            f"got {recovery['history']!r}"
        )

        # The three persisted snapshots are byte-for-byte equal
        # to the values captured before the reopen. The
        # interruption did not regenerate any take, did not
        # rewrite any field, and did not promote any
        # incomplete take to ready.
        for take_id in first_batch:
            current = _read_snapshot(sid, 1, take_id)
            previous = first_batch_snapshots[take_id]
            assert current["final_prompt"] == previous["final_prompt"]
            assert current["effective_state"] == previous["effective_state"]
            assert current["mapping_version"] == previous["mapping_version"]
            assert current["compiler_version"] == previous["compiler_version"]
            assert current["provenance"] == previous["provenance"]
            assert current["status"] == "ready"

        # No shot row was created by the reopen / recovery.
        n_shots = db.one(
            "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", sid,
        )["n"]
        assert n_shots == 0

        # ==========================================================
        # Phase 8: prepare a probe take after the future change
        # ==========================================================
        # portrait-08 is positioned AFTER the future jacket
        # change at take 7. The probe is a deliberate
        # acceptance boundary: when the change is added, the
        # probe's effective wardrobe must change and the probe's
        # prior snapshot must be invalidated. The test captures
        # the probe snapshot for that later comparison.
        probe_take_id = "portrait-08"
        _finalize_take(client, sid, 1, probe_take_id)
        probe_snapshot_at_rev1 = _read_snapshot(sid, 1, probe_take_id)
        assert probe_snapshot_at_rev1["status"] == "ready"
        assert probe_snapshot_at_rev1["effective_state"]["wardrobe"] == (
            ACCEPTANCE_INITIAL_WARDROBE
        )

        # ==========================================================
        # Phase 9: save plan revision 2 with the jacket
        # ==========================================================
        # The jacket arrives at take 7 with ``from_here`` scope.
        # The test does NOT hardcode the literal word "jacket"
        # in any assertion; it references the constant
        # ``ACCEPTANCE_JACKET_WARDROBE`` whose description is
        # independent of the design's prose. The take_id
        # ``portrait-07`` is also referenced through the take
        # list, not through a magic literal in the assertion.
        jacket_take_id = "portrait-07"
        edited = _build_plan(
            revision,
            wardrobe_changes=[
                {
                    "take_id": jacket_take_id,
                    "scope": "from_here",
                    "wardrobe": ACCEPTANCE_JACKET_WARDROBE,
                },
            ],
        )
        second_save = _save_plan(
            client, sid, edited, expected_revision=1,
        )
        assert second_save["plan_revision"] == 2

        # The expected effective wardrobes under revision 2 are
        # the spec's literal: portrait-01..06 = initial,
        # portrait-07..12 = jacket. The expected map is built
        # from the constant wardrobes and the take_id list the
        # test pinned, NOT from the production resolver. A
        # drift in ``resolve_effective_wardrobes`` would not
        # change this expected map; only a drift in the
        # persisted ``effective_state`` would surface.
        expected_jacket_split = {
            **{
                choice["take_id"]: ACCEPTANCE_INITIAL_WARDROBE
                for choice in ACCEPTANCE_TAKE_CHOICES[:6]
            },
            **{
                choice["take_id"]: ACCEPTANCE_JACKET_WARDROBE
                for choice in ACCEPTANCE_TAKE_CHOICES[6:]
            },
        }

        # ==========================================================
        # Phase 10: verify preservation and invalidation
        # ==========================================================
        # The 3 prior completed preparations for portrait-01,
        # portrait-02, portrait-03 keep their prior ``ready``
        # row at revision 1 with byte-for-byte identical
        # fields. The conservative invalidation boundary the
        # ``session-plan`` spec names for wardrobe edits is
        # exactly the changed take and following takes; takes
        # 1-3 are BEFORE the change and are not invalidated.
        for take_id in first_batch:
            snapshot = _read_snapshot(sid, 1, take_id)
            assert snapshot["status"] == "ready", (
                f"unaffected take {take_id} must keep its prior "
                f"ready state; got {snapshot['status']!r}"
            )
            assert snapshot["final_prompt"] == (
                first_batch_snapshots[take_id]["final_prompt"]
            )
            assert snapshot["effective_state"] == (
                first_batch_snapshots[take_id]["effective_state"]
            )
            assert snapshot["provenance"] == (
                first_batch_snapshots[take_id]["provenance"]
            )
            assert snapshot["mapping_version"] == (
                first_batch_snapshots[take_id]["mapping_version"]
            )
            assert snapshot["compiler_version"] == (
                first_batch_snapshots[take_id]["compiler_version"]
            )

        # The probe take (portrait-08) is at or after the change
        # boundary; its effective wardrobe changes, so the
        # invalidation pass must mark it ``invalidated``. The
        # row at revision 1 is NOT rewritten; the ``final_prompt``
        # is the one the test captured before the change, and
        # the new revision 2 will need a fresh preparation.
        invalid_probe = _read_snapshot(sid, 1, probe_take_id)
        assert invalid_probe["status"] == "invalidated", (
            f"the probe take {probe_take_id} must be invalidated "
            f"by the change at take 7; got {invalid_probe['status']!r}"
        )
        assert invalid_probe["final_prompt"] == (
            probe_snapshot_at_rev1["final_prompt"]
        ), (
            f"the invalidated row at revision 1 must NOT be "
            f"rewritten; the new revision's prompt lives in a "
            f"row at revision 2, not here"
        )

        # The recovered view at revision 2 names the prior rows
        # as history, not as completed. Recovery at the current
        # revision only counts rows whose plan_revision matches
        # the current revision. The history is preserved; the
        # user re-prepares the affected takes under the new
        # revision. The recovery payload is read through the
        # public plan endpoint, the same way the Phase 7
        # assertion reads recovery after the reopen.
        plan_resp_after = client.get(f"/api/sessions/{sid}/plan")
        assert plan_resp_after.status_code == 200, plan_resp_after.text
        recovery_after_change = plan_resp_after.json()["preparation"]
        assert recovery_after_change["plan_revision"] == 2
        assert recovery_after_change["completed"] == []
        assert [row["take_id"] for row in recovery_after_change["incomplete"]] == [
            f"portrait-{index:02d}" for index in range(1, 13)
        ]
        history_by_id = {
            row["id"]: row for row in recovery_after_change["history"]
        }
        # The three ready rows from revision 1 are in history
        # as ``ready``; the probe row is in history as
        # ``invalidated``.
        invalidated_ids = {
            row["id"] for row in history_by_id.values()
            if row["status"] == "invalidated"
        }
        ready_history_ids = {
            row["id"] for row in history_by_id.values()
            if row["status"] == "ready"
        }
        assert probe_take_id in {
            row["take_id"]
            for row in history_by_id.values()
            if row["id"] in invalidated_ids
        }
        assert set(first_batch) <= {
            row["take_id"]
            for row in history_by_id.values()
            if row["id"] in ready_history_ids
        }

        # ==========================================================
        # Phase 11: complete the remaining preparations
        # ==========================================================
        # The plan route keeps its stale-revision guards: the
        # user has to re-approve the plan revision before
        # submission, but finalization itself does not require
        # approval. The test finalizes every take at revision 2.
        # The 1-6 takes use the initial wardrobe (re-prepared at
        # the new revision); the 7-12 takes use the jacket.
        for index in range(1, 13):
            take_id = f"portrait-{index:02d}"
            _finalize_take(client, sid, 2, take_id)
        # Twelve ready rows at revision 2; the prior rows at
        # revision 1 are preserved in history.
        ready_count = db.one(
            "SELECT COUNT(*) AS n FROM prepared_take "
            "WHERE session_id = ? AND plan_revision = 2 "
            "AND status = 'ready'",
            sid,
        )["n"]
        assert ready_count == 12
        history_count = db.one(
            "SELECT COUNT(*) AS n FROM prepared_take "
            "WHERE session_id = ? AND plan_revision = 1",
            sid,
        )["n"]
        assert history_count == 4, (
            f"the prior revision 1 history must be preserved "
            f"byte-for-byte (3 ready + 1 invalidated probe); "
            f"got {history_count}"
        )
        # Every persisted snapshot at revision 2 carries the
        # literal expected wardrobe the spec names. The
        # expected map is the same one the test built
        # before saving revision 2 (``expected_jacket_split``):
        # it does NOT call the production resolver. The
        # assertion reads the persisted ``effective_state``
        # column directly, which is the only authoritative
        # source of the wardrobe the operator actually
        # shot the take with.
        for choice in ACCEPTANCE_TAKE_CHOICES:
            take_id = choice["take_id"]
            snapshot = _read_snapshot(sid, 2, take_id)
            expected_wardrobe = expected_jacket_split[take_id]
            assert snapshot["effective_state"]["wardrobe"] == (
                expected_wardrobe
            ), (
                f"revision-2 effective wardrobe for {take_id} "
                f"drifted from the literal expected map; got "
                f"{snapshot['effective_state']['wardrobe']!r}, "
                f"expected {expected_wardrobe!r}"
            )

        # ==========================================================
        # Phase 12: verify the exact persisted prompts
        # ==========================================================
        # A take BEFORE the change (portrait-03) and a take
        # AFTER the change (portrait-09) carry the literal
        # expected prompt. Any of the 12 wrong combinations
        # (jacket before take 7 / no jacket after take 7 /
        # duplicated look / duplicated trigger / duplicated
        # base / missing resource fields / accidental
        # instruction / regenerated snapshot / swapped source
        # revision) would surface here.
        for index, expected_wardrobe in [
            (3, ACCEPTANCE_INITIAL_WARDROBE),
            (9, ACCEPTANCE_JACKET_WARDROBE),
        ]:
            take_id = f"portrait-{index:02d}"
            choice = next(
                c for c in ACCEPTANCE_TAKE_CHOICES if c["take_id"] == take_id
            )
            take_clauses = _expected_take_clauses(
                camera=choice["camera"],
                framing=choice["framing"],
                pose=choice["pose"],
                expression=choice["expression"],
                resource_label=ACCEPTANCE_ROOM_PAYLOAD["label"],
                resource_scene_theme=ACCEPTANCE_ROOM_PAYLOAD["scene_theme"],
                resource_tags=ACCEPTANCE_ROOM_PAYLOAD.get("tags"),
            )
            expected_prompt = _expected_prompt(
                trigger=model["trigger"],
                base_positive="photo, 35mm",
                look=ACCEPTANCE_LOOK,
                wardrobe=expected_wardrobe,
                take_clauses=take_clauses,
            )
            snapshot = _read_snapshot(sid, 2, take_id)
            assert snapshot["final_prompt"] == expected_prompt, (
                f"final_prompt for {take_id} at revision 2 "
                f"drifted from the expected byte-for-byte value; "
                f"got {snapshot['final_prompt']!r}"
            )
            assert snapshot["effective_state"]["wardrobe"] == (
                expected_wardrobe
            )
            assert snapshot["status"] == "ready"
            # Trigger, base prompt, look and resource fields are
            # present EXACTLY ONCE in the final prompt. A
            # duplicated clause or a missing clause would
            # surface here as a count or substring mismatch.
            assert snapshot["final_prompt"].count(model["trigger"]) == 1
            assert snapshot["final_prompt"].count("photo, 35mm") == 1
            assert snapshot["final_prompt"].count(ACCEPTANCE_LOOK) == 1
            assert snapshot["final_prompt"].count(
                ACCEPTANCE_ROOM_PAYLOAD["label"]
            ) == 1
            assert snapshot["final_prompt"].count(
                ACCEPTANCE_ROOM_PAYLOAD["scene_theme"]
            ) == 1
            # The opposite wardrobe is NOT in the prompt.
            opposite_wardrobe = (
                ACCEPTANCE_JACKET_WARDROBE
                if expected_wardrobe == ACCEPTANCE_INITIAL_WARDROBE
                else ACCEPTANCE_INITIAL_WARDROBE
            )
            assert opposite_wardrobe not in snapshot["final_prompt"]
            # The provenance still pins the exact imported
            # revision; no source refresh has swapped the
            # digest.
            revisions = snapshot["provenance"][
                "selected_resource_revisions"
            ]
            assert revisions == [{
                "library_key": revision["library_key"],
                "source_id": revision["source_id"],
                "content_digest": revision["content_digest"],
                "kind": "rooms",
            }]

        # ==========================================================
        # Phase 13: approve and submit a take
        # ==========================================================
        # Submission is gated on the plan review approval
        # introduced in 5.4: an unapproved plan cannot submit
        # (the existing test_legacy_session_baseline-style
        # test for this lives in 5.4). Approve revision 2
        # through the public route, then submit a single take
        # twice. The two submissions must yield the same
        # ``shot_id`` and exactly one shot row.
        approve_resp = client.post(
            f"/api/sessions/{sid}/plan/approve",
            json={"plan_revision": 2},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        approved = approve_resp.json()
        assert approved["approved"] is True
        assert approved["plan_revision"] == 2

        submit_take = "portrait-09"
        first_submit = client.post(
            f"/api/sessions/{sid}/plan/preparations/submit",
            json={"plan_revision": 2, "take_id": submit_take},
        )
        assert first_submit.status_code == 200, first_submit.text
        first_body = first_submit.json()
        first_shot_id = first_body["shot_id"]
        assert isinstance(first_shot_id, int)
        # The shot's prompt is the exact final_prompt the
        # persistence step landed; the queue does NOT re-
        # compose trigger, base or look.
        first_shot = db.one(
            "SELECT prompt FROM shot WHERE id = ?", first_shot_id,
        )
        expected_after = _read_snapshot(sid, 2, submit_take)
        assert first_shot["prompt"] == expected_after["final_prompt"]

        # ==========================================================
        # Phase 14: double-click / network retry
        # ==========================================================
        # The second submission is the network retry / double
        # click case. The endpoint returns the existing shot
        # id, no new row is created, and the queue is not
        # duplicated. The test reads the count of shots
        # directly to confirm idempotency.
        second_submit = client.post(
            f"/api/sessions/{sid}/plan/preparations/submit",
            json={"plan_revision": 2, "take_id": submit_take},
        )
        assert second_submit.status_code == 200, second_submit.text
        second_body = second_submit.json()
        assert second_body["shot_id"] == first_shot_id
        n_shots_for_take = db.one(
            "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?",
            sid,
        )["n"]
        assert n_shots_for_take == 1, (
            f"double-click / network retry must produce exactly "
            f"one shot row; got {n_shots_for_take}"
        )
        # The shot row is linked back to the prepared_take row
        # so the provenance of the submitted photograph is
        # preserved.
        linked = db.one(
            "SELECT linked_shot_id, status FROM prepared_take "
            "WHERE session_id = ? AND plan_revision = 2 "
            "AND take_id = ?",
            sid, submit_take,
        )
        assert linked["linked_shot_id"] == first_shot_id
        assert linked["status"] == "generated"

        # ==========================================================
        # Phase 15: legacy baseline is unchanged
        # ==========================================================
        # The legacy byte-for-byte baseline is the responsibility
        # of ``tests/test_legacy_session_baseline.py``. The
        # acceptance itself asserts the resource surface
        # does not perturb the legacy composition path by
        # checking that a separate legacy session (no
        # composition_mode) still composes its shot the way
        # the 1.3 baseline pins. A regression in the legacy
        # path would surface here.
        legacy_resp = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "legacy untouched by 6.2",
            "look": ACCEPTANCE_LOOK,
            "wardrobe": ACCEPTANCE_INITIAL_WARDROBE,
            "shots": [
                {
                    "label": "wide",
                    "prompt": "standing square to the camera",
                },
            ],
        })
        assert legacy_resp.status_code == 200, legacy_resp.text
        legacy_sid = legacy_resp.json()["id"]
        legacy_full = client.get(f"/api/sessions/{legacy_sid}").json()
        assert legacy_full["look"] == ACCEPTANCE_LOOK
        assert legacy_full["wardrobe"] == ACCEPTANCE_INITIAL_WARDROBE
        # The composed shot is the trigger + base + look +
        # wardrobe + take the 1.3 baseline pins. A change in
        # the legacy path would surface here as a string
        # mismatch.
        actual_legacy_prompt = legacy_full["shots"][0]["prompt"]
        expected_legacy_prompt = (
            f"4da woman. photo, 35mm. {ACCEPTANCE_LOOK} "
            f"{ACCEPTANCE_INITIAL_WARDROBE}. "
            f"standing square to the camera."
        )
        assert actual_legacy_prompt == expected_legacy_prompt, (
            f"legacy prompt drifted from the expected byte-for-byte "
            f"value; got {actual_legacy_prompt!r}"
        )


# ---- Module-level privacy scan ------------------------------------------


ROOT = Path(__file__).resolve().parents[1]


def test_acceptance_file_carries_no_personal_data():
    """Reuse the canonical privacy-regex set from
    ``tests/test_no_personal_data`` so a private fork that
    diverges from the upstream guard fails here.

    The acceptance test references invented data, English-
    only prose, and the seeded model's English trigger /
    base positive strings. A regression that introduces a
    machine path, an email or a token would surface here as
    a privacy-pattern match.
    """
    from test_no_personal_data import PATTERNS as PRIVACY_PATTERNS
    text = (ROOT / "tests" / "test_resource_session_acceptance.py").read_text(
        encoding="utf-8",
    )
    for label, pattern in PRIVACY_PATTERNS.items():
        match = pattern.search(text)
        assert match is None, (
            f"test_resource_session_acceptance.py contains a "
            f"{label} match: {match.group(0)!r}"
        )
