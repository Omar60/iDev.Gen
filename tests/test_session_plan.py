"""Tests for task 3.1 of ``adopt-resource-session-planning``:
explicit resource composition mode and versioned session-plan draft
persistence.

The scope of this suite is the seven invariants the task names:

  1. A legacy session with no ``composition_mode`` behaves exactly
     as it did before this task. The new column reads as the empty
     string on existing rows, the new ``/plan`` route is refused on
     a legacy session, and a legacy composition that uses a real
     catalogue still composes the same way the baseline test pins.

  2. A ``resource-v1`` draft round-trips through save + get with
     stable take IDs, the exact ``(library_key, source_id,
     content_digest)`` triple the plan was saved with, and the
     same look, initial wardrobe and wardrobe-change events.

  3. A newer save refuses a stale compare-and-swap attempt. The
     stored plan and revision are unchanged after the refusal; no
     silent bump, no silent overwrite.

  4. An unknown or changed resource digest is refused rather than
     silently replaced. A plan that names a triple which was never
     recorded as an immutable revision is rejected with a readable
     error and no partial persistence. A plan that named an old
     revision keeps pointing at the old revision after a new one
     has been added: the substitute-the-latest-revision bug the
     spec names is the bug the lookup pins down.

  5. Resource-v1 persistence does not invoke measured-catalogue
     gates. A session created with ``composition_mode="resource-v1"``
     and an empty component table is accepted, where a legacy
     session with the same manner and shots would have been
     refused with a 422.

  6. The additive migration preserves existing session rows and
     repeated ``db.connect`` against the same database works. A
     database written before this task gains the new column with
     ``""`` as the default and the new ``session_plan`` table on
     reopen, with no destructive down-migration.

  7. The personal-data guard remains green after the new module
     and tests are in place.

All fixtures are invented English-only data; no source corpus, no
machine paths, no real names, no GPU, no ComfyUI, no network.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import db
import resource_store
import session_plan


ROOT = Path(__file__).resolve().parents[1]


# Reuse the canonical privacy-regex set rather than keeping a private
# copy here. A fork that diverges from the upstream guard is the leak
# the guard is supposed to catch, and the test_no_personal_data suite
# already runs against the same patterns over every tracked file.
from test_no_personal_data import PATTERNS as PRIVACY_PATTERNS  # noqa: E402


# ---- Isolated database helpers (mirrors the test_resource_store
# pattern: each test gets a fresh database, the global db._conn is
# reset between tests, and the WAL file is closed on teardown so
# Windows does not hold the file open between cases). ------------------


def _open(path: Path) -> sqlite3.Connection:
    db._conn = None  # noqa: SLF001
    return db.connect(path)


def _close_silently() -> None:
    conn = db._conn  # noqa: SLF001
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    db._conn = None  # noqa: SLF001


@pytest.fixture
def isolated_db(tmp_path):
    path = Path(tmp_path) / "session-plan.db"
    _open(path)
    try:
        yield path
    finally:
        _close_silently()


# ---- Invented English-only fixtures --------------------------------------


INV_LOOK = (
    "A small studio with a bare grey backdrop. Soft light from a "
    "single large window falls on her right cheek. She wears her "
    "hair loose, brushed forward over one shoulder, with no makeup "
    "beyond a faint pink lip."
)
INV_WARDROBE = (
    "a thin grey linen shirt with the sleeves rolled to the elbows, "
    "dark cotton trousers, bare feet"
)
INV_WARDROBE_JACKET = (
    "a thin grey linen shirt, a loose dark jacket over the shirt, "
    "dark cotton trousers, bare feet"
)

# A resource payload whose digest the plan will pin to. English-only,
# invented, no source corpus, no operator identifiers.
INV_ROOM_PAYLOAD = {
    "id": "inv_room_studio_dawn",
    "label": "invented studio at dawn",
    "scene_theme": (
        "A bare studio with a tall north-facing window. Soft grey "
        "light enters from the side and leaves the back wall in "
        "shadow. A single wooden chair stands between the subject "
        "and the camera, and a folded white sheet covers the floor."
    ),
    "tags": ["indoor", "studio", "morning"],
    "weight": 1.0,
}

# A second payload, same (library, source_id) but different content.
# This is what a refreshed source file carries. The original payload
# is still readable as a separate immutable revision.
INV_ROOM_PAYLOAD_V2 = {
    **INV_ROOM_PAYLOAD,
    "scene_theme": (
        "A bare studio with a tall north-facing window. The back "
        "wall is now lit by a soft warm bounce, as if a second "
        "window were added."
    ),
    "weight": 1.2,
}


def _build_revision(library_key: str, source_id: str, payload: dict) -> dict:
    """Register a library, record a revision, return the stored row.

    The returned dict carries the actual ``content_digest`` the
    database wrote. The plan the test saves MUST use the digest
    computed by the persistence layer (not a hand-written one) so a
    future change to the canonical form surfaces here as an explicit
    failure rather than a silent mismatch.
    """
    library_id = resource_store.ensure_library(library_key, kind="rooms")
    revision_id = resource_store.record_revision(library_id, source_id, payload)
    revision = resource_store.get_revision(revision_id=revision_id)
    assert revision is not None
    return {
        "library_id": library_id,
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": revision["content_digest"],
        "revision_id": revision_id,
    }


def _build_plan(revision: dict, **overrides) -> dict:
    """An invented resource-v1 plan pinning the given revision.

    Optional overrides let each test focus on one shape (a missing
    take, a wrong scope, a wardrobe change that names no take)
    without rewriting the whole dict.
    """
    plan = {
        "version": "resource-v1",
        "look": INV_LOOK,
        "initial_wardrobe": INV_WARDROBE,
        "takes": [
            {"take_id": "take-001", "label": "wide"},
            {"take_id": "take-002", "label": "close-up"},
            {"take_id": "take-003", "label": "jacket on"},
        ],
        "selected_resources": [
            {
                "library_key": revision["library_key"],
                "source_id": revision["source_id"],
                "content_digest": revision["content_digest"],
            },
        ],
        "wardrobe_changes": [
            {
                "take_id": "take-003",
                "scope": "from_here",
                "wardrobe": INV_WARDROBE_JACKET,
            },
        ],
    }
    plan.update(overrides)
    return plan


# =====================================================================
# 1. Legacy behaviour is unchanged when composition_mode is absent.
# =====================================================================


class TestLegacyCompositionUnchanged:
    """A session without composition_mode reads as legacy and composes
    the same way the baseline test pins. The new column MUST NOT
    perturb the legacy code path."""

    def test_a_legacy_session_round_trips_with_composition_mode_empty(
        self, client, seeded,
    ):
        # No composition_mode in the body. The route must default it
        # to the empty string and persist that.
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "legacy baseline",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [
                {"prompt": "a wide shot"},
            ],
        }).json()["id"]

        row = db.one("SELECT settings FROM session WHERE id=?", sid)
        settings = json.loads(row["settings"])
        # Legacy session: no `composition_mode` key in settings, and
        # reading the mode from settings returns the empty string.
        assert "composition_mode" not in settings, (
            f"legacy session must not carry composition_mode in settings; "
            f"got {settings!r}"
        )
        assert session_plan.read_composition_mode(row["settings"]) == ""

        # The session's full payload round-trips through GET the same
        # way the baseline test pins (look and wardrobe unchanged).
        full = client.get(f"/api/sessions/{sid}").json()
        assert full["look"] == INV_LOOK
        assert full["wardrobe"] == INV_WARDROBE
        # And the settings the API returns do not carry the key.
        assert "composition_mode" not in full["settings"]
        # The composed shot is still written: legacy _expand_shots ran.
        assert len(full["shots"]) == 1
        assert full["shots"][0]["prompt"], (
            "legacy session must still compose a shot prompt"
        )

    def test_legacy_session_refuses_the_plan_routes(self, client, seeded):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "legacy refuses plan",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [{"prompt": "a wide shot"}],
        }).json()["id"]

        # GET plan: 400, the session is not in resource-v1 mode.
        resp = client.get(f"/api/sessions/{sid}/plan")
        assert resp.status_code == 400, resp.text
        assert "resource-v1" in resp.json()["detail"], resp.text

        # POST plan: 400, the session is not in resource-v1 mode.
        revision = _build_revision(
            "inv_rooms_legacy_refuses",
            "inv_room_legacy",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        resp = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert resp.status_code == 400, resp.text
        assert "resource-v1" in resp.json()["detail"], resp.text

        # And nothing was written: a legacy session has no plan row.
        n = db.one(
            "SELECT COUNT(*) AS n FROM session_plan WHERE session_id=?",
            sid,
        )["n"]
        assert n == 0

    def test_composition_mode_value_other_than_empty_or_resource_v1_is_refused(
        self, client, seeded,
    ):
        resp = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "bad mode",
            "composition_mode": "resource-v2",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [{"prompt": "a wide shot"}],
        })
        assert resp.status_code == 400, resp.text
        assert "composition_mode" in resp.json()["detail"], resp.text

        # The route refused the request, so no session was written.
        n = db.one("SELECT COUNT(*) AS n FROM session")["n"]
        assert n == 0


# =====================================================================
# 2. resource-v1 draft round-trips with stable take IDs and exact
# selected revision identity.
# =====================================================================


class TestResourceV1DraftRoundTrip:
    """A resource-v1 plan written through the API is read back
    byte-for-byte. The selected revision is identified by the exact
    (library_key, source_id, content_digest) the caller saved, and
    the take IDs and order survive a round-trip."""

    def test_a_resource_v1_draft_round_trips_through_save_and_get(
        self, client, seeded,
    ):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "resource-v1 round trip",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
        }).json()["id"]
        # The mode is persisted in settings, not on a dedicated
        # column, and reading it back returns the value the
        # service uses for the plan-route check.
        row = db.one("SELECT settings FROM session WHERE id=?", sid)
        settings = json.loads(row["settings"])
        assert settings.get("composition_mode") == "resource-v1"

        revision = _build_revision(
            "inv_rooms_round_trip",
            "inv_room_round_trip",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)

        # First save: expected_revision 0 -> server returns 1.
        save_resp = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save_resp.status_code == 200, save_resp.text
        assert save_resp.json() == {"plan_revision": 1}, save_resp.text

        # Read it back. The take IDs and order are preserved
        # verbatim; the selected resource is identified by the
        # exact (library_key, source_id, content_digest) the caller
        # pinned to; the look, initial_wardrobe and wardrobe
        # changes round-trip exactly.
        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan_revision"] == 1
        assert got["plan"]["version"] == "resource-v1"
        assert got["plan"]["look"] == INV_LOOK
        assert got["plan"]["initial_wardrobe"] == INV_WARDROBE
        # Take IDs and order.
        assert [t["take_id"] for t in got["plan"]["takes"]] == [
            "take-001", "take-002", "take-003",
        ]
        # Each take is a dict the caller wrote — the service
        # preserves the dict shape so a future task can add
        # camera/pose/expression fields without breaking 3.1.
        for take in got["plan"]["takes"]:
            assert "take_id" in take
        # Selected resources: exact triple, no substitution.
        assert got["plan"]["selected_resources"] == [
            {
                "library_key": revision["library_key"],
                "source_id": revision["source_id"],
                "content_digest": revision["content_digest"],
            },
        ]
        # Wardrobe changes round-trip with their scope and value.
        assert got["plan"]["wardrobe_changes"] == [
            {
                "take_id": "take-003",
                "scope": "from_here",
                "wardrobe": INV_WARDROBE_JACKET,
            },
        ]

    def test_stable_take_ids_survive_a_reorder_through_a_new_save(
        self, client, seeded,
    ):
        """Reorder test: the take IDs and the new order survive a
        new save, and the wardrobe_change pinned to take-003
        follows the take, not the index. The spec calls this out
        explicitly: 'stable IDs prevent reorder from attaching a
        preparation to the wrong take'."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "resource-v1 reorder",
            "composition_mode": "resource-v1",
        }).json()["id"]

        revision = _build_revision(
            "inv_rooms_reorder",
            "inv_room_reorder",
            INV_ROOM_PAYLOAD,
        )
        # First plan: take-001, take-002, take-003, with a
        # wardrobe change on take-003.
        first = _build_plan(revision)
        first_save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": first, "expected_revision": 0},
        )
        assert first_save.json() == {"plan_revision": 1}

        # Reorder: take-003 first. The wardrobe change that
        # originally named take-003 still names take-003; the
        # caller is the only one who knows which take the
        # jacket follows.
        reordered = {
            **first,
            "takes": [
                {"take_id": "take-003", "label": "jacket on"},
                {"take_id": "take-001", "label": "wide"},
                {"take_id": "take-002", "label": "close-up"},
            ],
        }
        second_save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": reordered, "expected_revision": 1},
        )
        assert second_save.status_code == 200, second_save.text
        assert second_save.json() == {"plan_revision": 2}, second_save.text

        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan_revision"] == 2
        assert [t["take_id"] for t in got["plan"]["takes"]] == [
            "take-003", "take-001", "take-002",
        ]
        # The wardrobe change is still pinned to take-003 — the
        # stable ID, not the position.
        assert got["plan"]["wardrobe_changes"][0]["take_id"] == "take-003"


# =====================================================================
# 3. A newer save refuses a stale compare-and-swap attempt.
# =====================================================================


class TestCompareAndSwap:
    """The CAS check is the whole point of the revision column. A
    save that names the wrong expected revision is refused with
    409, and the stored plan and revision are unchanged."""

    def test_a_stale_save_is_refused_without_mutation(
        self, client, seeded,
    ):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "cas stale",
            "composition_mode": "resource-v1",
        }).json()["id"]

        revision = _build_revision(
            "inv_rooms_cas",
            "inv_room_cas",
            INV_ROOM_PAYLOAD,
        )
        first_plan = _build_plan(revision)
        first_save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": first_plan, "expected_revision": 0},
        )
        assert first_save.json() == {"plan_revision": 1}

        # A second save at the SAME revision: legal, bumps to 2.
        second_plan = _build_plan(
            revision, initial_wardrobe="a thin black silk shirt",
        )
        second_save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": second_plan, "expected_revision": 1},
        )
        assert second_save.status_code == 200, second_save.text
        assert second_save.json() == {"plan_revision": 2}

        # A third save with expected_revision 0 (stale — current is
        # 2). Must be refused with 409.
        third_plan = _build_plan(
            revision, initial_wardrobe="a thin white linen shirt",
        )
        stale = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": third_plan, "expected_revision": 0},
        )
        assert stale.status_code == 409, stale.text
        assert "expected 0" in stale.json()["detail"]
        assert "is 2" in stale.json()["detail"]

        # The stored plan is the second save's content, unchanged.
        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan_revision"] == 2
        assert got["plan"]["initial_wardrobe"] == "a thin black silk shirt"
        # And the third save's wardrobe never reached the row.
        assert got["plan"]["initial_wardrobe"] != "a thin white linen shirt"

    def test_a_stale_save_on_a_session_with_no_plan_is_refused(
        self, client, seeded,
    ):
        """expected_revision must match reality. A session with no
        plan reads as 0; passing anything else is a stale save."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "cas no plan yet",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_cas_empty",
            "inv_room_cas_empty",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        resp = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 1},
        )
        assert resp.status_code == 409, resp.text
        # No row was written.
        n = db.one(
            "SELECT COUNT(*) AS n FROM session_plan WHERE session_id=?",
            sid,
        )["n"]
        assert n == 0

    def test_two_sessions_can_have_independent_revisions(
        self, client, seeded,
    ):
        """CAS is per-session: bumping one session's plan does
        not affect another's expected_revision."""
        sid_a = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "cas a",
            "composition_mode": "resource-v1",
        }).json()["id"]
        sid_b = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "cas b",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_cas_pair",
            "inv_room_cas_pair",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        ra = client.post(
            f"/api/sessions/{sid_a}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        rb = client.post(
            f"/api/sessions/{sid_b}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert ra.json() == {"plan_revision": 1}
        assert rb.json() == {"plan_revision": 1}


# =====================================================================
# 4. An unknown or changed resource digest is refused rather than
# silently replaced.
# =====================================================================


class TestSelectedRevisionsAreExact:
    """The selected resource must identify one exact immutable
    revision by its (library_key, source_id, content_digest) triple.
    A triple that was never recorded is refused; a plan that points
    at an old revision keeps pointing at the old revision after a
    new one has been added."""

    def test_an_unknown_digest_is_refused_with_a_readable_error(
        self, client, seeded,
    ):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "unknown digest",
            "composition_mode": "resource-v1",
        }).json()["id"]
        # Build a real revision so the library exists, then point
        # the plan at a digest that was never recorded.
        revision = _build_revision(
            "inv_rooms_unknown_digest",
            "inv_room_unknown_digest",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        plan["selected_resources"][0]["content_digest"] = (
            "deadbeef" * 8  # 64 hex chars, but no row matches it
        )
        resp = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        # Readable: the error names the offending triple.
        assert "inv_rooms_unknown_digest" in detail
        assert "inv_room_unknown_digest" in detail
        assert "deadbeef" * 2 in detail or "deadbeefdeadbeef" in detail
        # No partial persistence: no plan row was written.
        n = db.one(
            "SELECT COUNT(*) AS n FROM session_plan WHERE session_id=?",
            sid,
        )["n"]
        assert n == 0

    def test_an_unknown_library_is_refused_with_a_readable_error(
        self, client, seeded,
    ):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "unknown library",
            "composition_mode": "resource-v1",
        }).json()["id"]
        plan = _build_plan({
            "library_key": "inv_library_never_seen",
            "source_id": "inv_room_never_seen",
            "content_digest": "00" * 32,
        })
        resp = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert resp.status_code == 422, resp.text
        assert "unregistered" in resp.json()["detail"]
        n = db.one(
            "SELECT COUNT(*) AS n FROM session_plan WHERE session_id=?",
            sid,
        )["n"]
        assert n == 0

    def test_a_new_revision_does_not_silently_replace_an_old_plan(
        self, client, seeded,
    ):
        """The plan pins the old revision by digest. Adding a new
        revision for the same (library, source_id) does NOT
        rewrite the plan. This is what 'never silently replace
        it with a newer revision' pins down."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "old revision sticks",
            "composition_mode": "resource-v1",
        }).json()["id"]

        library_id = resource_store.ensure_library(
            "inv_rooms_old_sticks", kind="rooms",
        )
        old_id = resource_store.record_revision(
            library_id, "inv_room_old_sticks", INV_ROOM_PAYLOAD,
        )
        old = resource_store.get_revision(revision_id=old_id)
        old_digest = old["content_digest"]
        old_plan = _build_plan({
            "library_key": "inv_rooms_old_sticks",
            "source_id": "inv_room_old_sticks",
            "content_digest": old_digest,
        })
        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": old_plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text

        # A new revision for the same (library, source) is
        # added — different content, different digest.
        new_id = resource_store.record_revision(
            library_id, "inv_room_old_sticks", INV_ROOM_PAYLOAD_V2,
        )
        new = resource_store.get_revision(revision_id=new_id)
        assert new["content_digest"] != old_digest

        # The plan still reads the old digest. No silent swap.
        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan"]["selected_resources"][0]["content_digest"] == old_digest
        assert got["plan"]["selected_resources"][0]["content_digest"] != new["content_digest"]

    def test_a_plan_pinning_a_known_old_revision_round_trips(
        self, client, seeded,
    ):
        """A plan that targets the new revision's digest while
        a different (older) revision is the one named in the
        plan is still legal as long as the named digest is in
        the table. The substitute-the-latest bug is the bug
        the per-digest lookup prevents."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "old digest named",
            "composition_mode": "resource-v1",
        }).json()["id"]

        library_id = resource_store.ensure_library(
            "inv_rooms_old_named", kind="rooms",
        )
        old_id = resource_store.record_revision(
            library_id, "inv_room_old_named", INV_ROOM_PAYLOAD,
        )
        new_id = resource_store.record_revision(
            library_id, "inv_room_old_named", INV_ROOM_PAYLOAD_V2,
        )
        old = resource_store.get_revision(revision_id=old_id)

        # Plan names the OLD revision. The save must succeed
        # because the OLD revision is still in the table.
        plan = _build_plan({
            "library_key": "inv_rooms_old_named",
            "source_id": "inv_room_old_named",
            "content_digest": old["content_digest"],
        })
        resp = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert resp.status_code == 200, resp.text

        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan"]["selected_resources"][0]["content_digest"] == old["content_digest"]


# =====================================================================
# 5. resource-v1 persistence does not invoke measured-catalogue
# gates.
# =====================================================================


class TestResourceV1BypassesMeasuredCatalogue:
    """A resource-v1 session is created with an empty component
    table (no measured cameras, no measured acts, no measured
    framings). A legacy session with the same inputs would be
    refused with 422; the resource-v1 path must be accepted."""

    def test_a_resource_v1_session_with_an_empty_catalogue_is_accepted(
        self, client, seeded,
    ):
        # The component table is the empty one the client fixture
        # leaves behind. A resource-v1 session with no shots and
        # no measured catalogue is the path task 3.1 explicitly
        # names as 'must not be blocked by missing catalogue'.
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "resource-v1 empty catalogue",
            "composition_mode": "resource-v1",
            "manner": "directed",  # a value the gate would check
        }).json()["id"]
        row = db.one("SELECT settings FROM session WHERE id=?", sid)
        settings = json.loads(row["settings"])
        # The mode lives in settings, not on a dedicated column.
        assert settings.get("composition_mode") == "resource-v1"
        assert session_plan.read_composition_mode(row["settings"]) == "resource-v1"

    def test_a_resource_v1_session_with_shots_bypasses_the_measured_gate(
        self, client, seeded,
    ):
        """Even with shots supplied, the resource-v1 path must
        not invoke the measured-catalogue gate. The gate runs
        only when composition_mode is empty."""
        resp = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "resource-v1 with shots",
            "composition_mode": "resource-v1",
            "manner": "directed",
            "shots": [{"prompt": "an invented shot prompt"}],
        })
        assert resp.status_code == 200, resp.text

    def test_a_legacy_session_with_an_empty_catalogue_is_still_refused(
        self, client, seeded,
    ):
        """The flip side: a legacy session with the same
        manner-and-shots combination IS refused. This is what
        'legacy behaviour is unchanged' means structurally."""
        # The seeded client fixture seeds a measured catalogue.
        # Wipe it to make the gate fire.
        db.run("DELETE FROM component")
        resp = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "legacy empty catalogue",
            "manner": "directed",
            "shots": [{"prompt": "an invented shot prompt"}],
        })
        assert resp.status_code == 422, resp.text
        assert "camera catalogue is empty" in resp.json()["detail"]


# =====================================================================
# 6. The additive migration preserves existing session rows and
# repeated db.connect against the same database works.
# =====================================================================


class TestAdditiveMigration:
    """A database written before this task gains the new column
    with ``''`` as the default and the new ``session_plan`` table
    on reopen. Existing rows are byte-for-byte unchanged. Repeated
    ``db.connect`` calls are safe."""

    def test_a_pre_composition_mode_database_round_trips_on_reopen(
        self, tmp_path,
    ):
        """Plant a database without ``session_plan`` and with a
        session whose settings carry no ``composition_mode`` key,
        then reopen through ``db.connect``. The new table is
        created, the legacy row survives, and the settings are
        read back without a ``composition_mode`` key. There is no
        ``session.composition_mode`` column to drop: the test
        asserts that the column never existed in the first place.
        """
        path = Path(tmp_path) / "pre-mode.db"
        conn = db.connect(path)
        # Drop the new tables to simulate a pre-3.1 database. Both
        # CREATE TABLE IF NOT EXISTS in SCHEMA mean dropping is a
        # no-op when the table is missing, which is the case the
        # test plants.
        conn.execute("DROP TABLE IF EXISTS session_plan")
        conn.execute("DROP TABLE IF EXISTS prepared_take")
        # Plant a session in the legacy shape: settings is the
        # empty object, no ``composition_mode`` key, no dedicated
        # column.
        conn.execute(
            "INSERT INTO model (name, trigger, created_at) "
            "VALUES ('inv_model', '4da woman', 'now')"
        )
        mid = conn.execute("SELECT id FROM model").fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO session (model_id, name, look, wardrobe, "
            "settings, tags, created_at) "
            "VALUES (?, 'pre mode', 'an invented look', "
            "'an invented wardrobe', '{}', '[]', 'now')",
            (mid,),
        )
        sid = cur.lastrowid
        conn.execute(
            "INSERT INTO shot (session_id, prompt, components, created_at) "
            "VALUES (?, 'a legacy prompt', '{}', 'now')",
            (sid,),
        )
        conn.commit()
        conn.close()

        # Reopen: SCHEMA + _migrate run.
        _open(path)
        try:
            row = db.one(
                "SELECT name, look, wardrobe, settings FROM session WHERE id=?",
                sid,
            )
            # Legacy data survives byte-for-byte.
            assert row["name"] == "pre mode"
            assert row["look"] == "an invented look"
            assert row["wardrobe"] == "an invented wardrobe"
            # And settings did not gain a composition_mode key
            # on reopen: a legacy session has no key, the empty
            # default, and the service reads it as legacy.
            settings = json.loads(row["settings"])
            assert "composition_mode" not in settings
            assert session_plan.read_composition_mode(row["settings"]) == ""
            # The shot row also survives untouched.
            shot = db.one(
                "SELECT prompt FROM shot WHERE session_id=?", sid,
            )
            assert shot["prompt"] == "a legacy prompt"
            # The new tables were created on reopen.
            names = {r["name"] for r in db.q(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )}
            assert "session_plan" in names
            assert "prepared_take" in names
        finally:
            _close_silently()

    def test_resource_v1_mode_persists_in_settings_across_reopen(
        self, tmp_path,
    ):
        """The mode lives in the session's settings, not in a
        dedicated column, so a resource-v1 session created before
        ``db.close`` reads as resource-v1 on reopen."""
        path = Path(tmp_path) / "rv1-reopen.db"
        _open(path)
        try:
            db.run(
                "INSERT INTO model (name, trigger, created_at) "
                "VALUES ('inv_model', '4da woman', 'now')"
            )
            mid = db.one("SELECT id FROM model")["id"]
            settings = json.dumps({"composition_mode": "resource-v1"})
            sid = db.run(
                "INSERT INTO session (model_id, name, settings, created_at) "
                "VALUES (?, 'rv1 persisted', ?, 'now')",
                mid, settings,
            )
        finally:
            _close_silently()

        _open(path)
        try:
            row = db.one("SELECT settings FROM session WHERE id=?", sid)
            settings = json.loads(row["settings"])
            assert settings.get("composition_mode") == "resource-v1"
            assert session_plan.read_composition_mode(row["settings"]) == "resource-v1"
        finally:
            _close_silently()

    def test_repeated_db_connect_on_the_same_path_is_idempotent(
        self, tmp_path,
    ):
        """Reopening the same database repeatedly does not raise
        and does not lose data. The CREATE TABLE IF NOT EXISTS in
        SCHEMA and the column-existence checks in _migrate together
        are what make repeated connects safe."""
        path = Path(tmp_path) / "repeat.db"
        _open(path)
        try:
            db.run(
                "INSERT INTO model (name, trigger, created_at) "
                "VALUES ('m1', 't1', 'now')"
            )
            mid = db.one("SELECT id FROM model")["id"]
            db.run(
                "INSERT INTO session (model_id, name, created_at) "
                "VALUES (?, 's1', 'now')",
                mid,
            )
        finally:
            _close_silently()

        # Three more connect cycles.
        for _ in range(3):
            _open(path)
            try:
                n = db.one("SELECT COUNT(*) AS n FROM session")["n"]
                assert n == 1
            finally:
                _close_silently()

        # Final state: every table and column is still there.
        _open(path)
        try:
            names = {
                r["name"] for r in db.q(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "session_plan" in names
            assert "prepared_take" in names
            assert "resource_library" in names
            assert "asset_revision" in names
            # The session table has NO composition_mode column: the
            # mode lives in the settings JSON, not on a dedicated row.
            cols = {r["name"] for r in db.q("PRAGMA table_info(session)")}
            assert "composition_mode" not in cols, sorted(cols)
        finally:
            _close_silently()

    def test_fresh_database_has_session_plan_and_prepared_take(
        self, isolated_db,
    ):
        """The two new tables exist on a fresh database written
        by the current SCHEMA. The session.composition_mode
        column does NOT exist (the mode is in settings)."""
        names = {
            r["name"] for r in db.q(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "session_plan" in names, sorted(names)
        assert "prepared_take" in names, sorted(names)
        cols = {r["name"] for r in db.q("PRAGMA table_info(session)")}
        assert "composition_mode" not in cols, sorted(cols)
        cols = {r["name"] for r in db.q("PRAGMA table_info(session_plan)")}
        assert {"id", "session_id", "mode", "plan_revision",
                "plan_json", "created_at", "updated_at"} <= cols, cols
        cols = {r["name"] for r in db.q("PRAGMA table_info(prepared_take)")}
        assert {"id", "session_id", "plan_revision", "take_id",
                "final_prompt", "effective_state", "mapping_version",
                "compiler_version", "provenance", "status",
                "linked_shot_id", "created_at", "updated_at"} <= cols, cols


# =====================================================================
# 6b. ``prepared_take`` schema, composite UNIQUE, FK lifecycle.
# =====================================================================


class TestPreparedTakeSchema:
    """The ``prepared_take`` table that task 3.1 introduces is
    additive: the new table exists on fresh and migrated
    databases, the composite UNIQUE on (session_id, plan_revision,
    take_id) refuses a duplicate, and the foreign keys behave the
    way the design specifies (CASCADE on session delete, SET NULL
    on shot delete). 3.2/3.3/3.4 will drive the writes; this
    suite only pins the schema so a later task can build on it
    without re-litigating the lifecycle."""

    def test_prepared_take_table_exists_with_expected_columns(
        self, isolated_db,
    ):
        names = {r["name"] for r in db.q(
            "SELECT name FROM sqlite_master WHERE type='table'",
        )}
        assert "prepared_take" in names, sorted(names)
        cols = {r["name"] for r in db.q("PRAGMA table_info(prepared_take)")}
        expected = {
            "id", "session_id", "plan_revision", "take_id",
            "final_prompt", "effective_state", "mapping_version",
            "compiler_version", "provenance", "status",
            "linked_shot_id", "created_at", "updated_at",
        }
        assert expected <= cols, (
            f"prepared_take missing columns {expected - cols}; "
            f"got {cols}"
        )

    def test_prepared_take_has_composite_unique_on_plan_triple(
        self, isolated_db,
    ):
        """A direct INSERT that names the same (session_id,
        plan_revision, take_id) twice is rejected at the SQL
        level. This is the rule that lets a future re-prepare
        for the same take under a NEW plan_revision land as a
        new row, while refusing a stale prepare for the same
        triple.
        """
        # Plant a session and a model.
        db.run(
            "INSERT INTO model (name, trigger, created_at) "
            "VALUES ('m', 't', 'now')"
        )
        mid = db.one("SELECT id FROM model")["id"]
        sid = db.run(
            "INSERT INTO session (model_id, name, created_at) "
            "VALUES (?, 's', 'now')",
            mid,
        )
        # First insert: legal.
        db.run(
            "INSERT INTO prepared_take "
            "(session_id, plan_revision, take_id, created_at, updated_at) "
            "VALUES (?, 1, 'take-1', 'now', 'now')",
            sid,
        )
        # Same triple: refused.
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            db.run(
                "INSERT INTO prepared_take "
                "(session_id, plan_revision, take_id, created_at, updated_at) "
                "VALUES (?, 1, 'take-1', 'now', 'now')",
                sid,
            )
        # Different plan_revision: legal, second row.
        db.run(
            "INSERT INTO prepared_take "
            "(session_id, plan_revision, take_id, created_at, updated_at) "
            "VALUES (?, 2, 'take-1', 'now', 'now')",
            sid,
        )
        # Different take_id: legal, third row.
        db.run(
            "INSERT INTO prepared_take "
            "(session_id, plan_revision, take_id, created_at, updated_at) "
            "VALUES (?, 1, 'take-2', 'now', 'now')",
            sid,
        )
        n = db.one("SELECT COUNT(*) AS n FROM prepared_take")["n"]
        assert n == 3

    def test_prepared_take_check_rejects_empty_take_id(
        self, isolated_db,
    ):
        db.run(
            "INSERT INTO model (name, trigger, created_at) "
            "VALUES ('m', 't', 'now')"
        )
        mid = db.one("SELECT id FROM model")["id"]
        sid = db.run(
            "INSERT INTO session (model_id, name, created_at) "
            "VALUES (?, 's', 'now')",
            mid,
        )
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            db.run(
                "INSERT INTO prepared_take "
                "(session_id, plan_revision, take_id, created_at, updated_at) "
                "VALUES (?, 1, '', 'now', 'now')",
                sid,
            )

    def test_prepared_take_status_check_lists_four_values(
        self, isolated_db,
    ):
        """The status CHECK is the four-state vocabulary 3.1
        reserves for the future preparation/submission flow:
        pending, ready, invalidated, generated."""
        db.run(
            "INSERT INTO model (name, trigger, created_at) "
            "VALUES ('m', 't', 'now')"
        )
        mid = db.one("SELECT id FROM model")["id"]
        sid = db.run(
            "INSERT INTO session (model_id, name, created_at) "
            "VALUES (?, 's', 'now')",
            mid,
        )
        for status in ("pending", "ready", "invalidated", "generated"):
            db.run(
                "INSERT INTO prepared_take "
                "(session_id, plan_revision, take_id, status, "
                "created_at, updated_at) "
                "VALUES (?, 1, ?, ?, 'now', 'now')",
                sid, f"take-{status}", status,
            )
        # An unknown status is refused at the SQL level.
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            db.run(
                "INSERT INTO prepared_take "
                "(session_id, plan_revision, take_id, status, "
                "created_at, updated_at) "
                "VALUES (?, 1, 'take-bad', 'mystery', 'now', 'now')",
                sid,
            )

    def test_prepared_take_cascades_when_session_is_deleted(
        self, isolated_db,
    ):
        """A prepared_take is part of the session's history: when
        the session is deleted, its prepared_take rows go with it.
        Same lifecycle the existing session/shot tables use."""
        db.run(
            "INSERT INTO model (name, trigger, created_at) "
            "VALUES ('m', 't', 'now')"
        )
        mid = db.one("SELECT id FROM model")["id"]
        sid = db.run(
            "INSERT INTO session (model_id, name, created_at) "
            "VALUES (?, 's', 'now')",
            mid,
        )
        db.run(
            "INSERT INTO prepared_take "
            "(session_id, plan_revision, take_id, created_at, updated_at) "
            "VALUES (?, 1, 'take-1', 'now', 'now')",
            sid,
        )
        n = db.one("SELECT COUNT(*) AS n FROM prepared_take")["n"]
        assert n == 1
        db.run("DELETE FROM session WHERE id=?", sid)
        n = db.one("SELECT COUNT(*) AS n FROM prepared_take")["n"]
        assert n == 0

    def test_prepared_take_linked_shot_set_null_when_shot_deleted(
        self, isolated_db,
    ):
        """The linked_shot_id is a soft link: a finished shot is
        history even when rolled back, so deleting the shot
        leaves the prepared_take row in place with a NULL link.
        The prompt and provenance survive the rollback; only
        the soft link drops."""
        db.run(
            "INSERT INTO model (name, trigger, created_at) "
            "VALUES ('m', 't', 'now')"
        )
        mid = db.one("SELECT id FROM model")["id"]
        sid = db.run(
            "INSERT INTO session (model_id, name, created_at) "
            "VALUES (?, 's', 'now')",
            mid,
        )
        shot_id = db.run(
            "INSERT INTO shot (session_id, prompt, created_at) "
            "VALUES (?, 'a shot', 'now')",
            sid,
        )
        db.run(
            "INSERT INTO prepared_take "
            "(session_id, plan_revision, take_id, linked_shot_id, "
            "status, created_at, updated_at) "
            "VALUES (?, 1, 'take-1', ?, 'generated', 'now', 'now')",
            sid, shot_id,
        )
        n = db.one("SELECT COUNT(*) AS n FROM prepared_take")["n"]
        assert n == 1
        # Delete the shot. The prepared_take stays; the soft
        # link drops to NULL.
        db.run("DELETE FROM shot WHERE id=?", shot_id)
        row = db.one(
            "SELECT linked_shot_id, status, take_id "
            "FROM prepared_take WHERE session_id=?",
            sid,
        )
        assert row is not None, "prepared_take row must survive a shot delete"
        assert row["linked_shot_id"] is None
        assert row["status"] == "generated"
        assert row["take_id"] == "take-1"

    def test_prepared_take_appears_after_a_pre_3_1_reopen(
        self, tmp_path,
    ):
        """A database written before 3.1 gains the
        ``prepared_take`` table on reopen, with the composite
        UNIQUE and the CASCADE / SET NULL foreign keys. The
        test plants a session, reopens, and confirms a
        prepared_take row can be written and read back."""
        path = Path(tmp_path) / "pre-3.1.db"
        _open(path)
        try:
            db.run(
                "INSERT INTO model (name, trigger, created_at) "
                "VALUES ('m', 't', 'now')"
            )
            mid = db.one("SELECT id FROM model")["id"]
            db.run(
                "INSERT INTO session (model_id, name, created_at) "
                "VALUES (?, 's', 'now')",
                mid,
            )
            # Confirm the table does not exist yet (the test
            # bypasses SCHEMA through a fresh DB that runs it,
            # but for symmetry with the other migration tests
            # we drop it first).
            db.run("DROP TABLE IF EXISTS prepared_take")
            names = {r["name"] for r in db.q(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )}
            assert "prepared_take" not in names
        finally:
            _close_silently()

        # Reopen: SCHEMA recreates the table.
        _open(path)
        try:
            names = {r["name"] for r in db.q(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )}
            assert "prepared_take" in names
            sid = db.one("SELECT id FROM session")["id"]
            db.run(
                "INSERT INTO prepared_take "
                "(session_id, plan_revision, take_id, "
                "final_prompt, status, created_at, updated_at) "
                "VALUES (?, 1, 'take-mig', 'a stored prompt', "
                "'ready', 'now', 'now')",
                sid,
            )
            got = db.one(
                "SELECT take_id, final_prompt, status, "
                "mapping_version, compiler_version, "
                "effective_state, provenance "
                "FROM prepared_take WHERE session_id=?",
                sid,
            )
            assert got["take_id"] == "take-mig"
            assert got["final_prompt"] == "a stored prompt"
            assert got["status"] == "ready"
            assert got["mapping_version"] == ""
            assert got["compiler_version"] == ""
            assert json.loads(got["effective_state"]) == {}
            assert json.loads(got["provenance"]) == {}
        finally:
            _close_silently()


# =====================================================================
# 6c. Reserved-field guard: `composition_mode` is a top-level
# SessionIn / SessionPatch field, not a key that may be injected
# through the free-form `settings` dict. A nested value would
# otherwise set the mode without going through the top-level
# validation, which is the hybrid-bypass the routes refuse.
# =====================================================================


class TestReservedFieldGuard:
    """`composition_mode` must only land in ``session.settings`` via
    the top-level ``SessionIn.composition_mode`` /
    ``SessionPatch.composition_mode`` field. A request that injects
    it through the ``settings`` dict is refused with a clear 400,
    no row is written, and the operator's data is left unchanged.

    The four cases this class pins:

      1. Create: a body whose ``settings`` carries
         ``composition_mode`` is refused, no session is created, and
         the absence of a top-level field does not let a hybrid
         session through.

      2. PATCH on a resource-v1 session with a saved plan: a
         ``settings`` injection of any value is refused. The
         session's settings and the plan's revision are unchanged.

      3. PATCH on a legacy session: a ``settings`` injection is
         refused at the same boundary.

      4. The valid top-level path (covered by the existing
         resource-v1 round-trip and update tests) is unaffected by
         the new guard.
    """

    def test_create_with_settings_composition_mode_is_refused(
        self, client, seeded,
    ):
        """Test 1: the create path refuses a nested
        ``settings.composition_mode``, regardless of whether the
        top-level field is set, and writes no session."""
        # No top-level field; only a nested value. The guard
        # refuses; no session row is created.
        resp = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "nested mode in create",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [{"prompt": "a wide shot"}],
            "settings": {"composition_mode": "resource-v1"},
        })
        assert resp.status_code == 400, resp.text
        assert "reserved field" in resp.json()["detail"], resp.text
        n = db.one("SELECT COUNT(*) AS n FROM session")["n"]
        assert n == 0, (
            "the route refused the request; no session row may "
            "have been written"
        )

    def test_create_with_settings_composition_mode_and_top_level_agrees_is_refused(
        self, client, seeded,
    ):
        """A nested injection is refused even when the top-level
        value agrees. The contract is about the boundary, not
        about whether the values match: a body that injects the
        mode through ``settings`` is structurally wrong, and a
        silent strip would be the bug."""
        resp = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "nested mode agrees with top level",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "settings": {"composition_mode": "resource-v1"},
        })
        assert resp.status_code == 400, resp.text
        assert "reserved field" in resp.json()["detail"], resp.text
        n = db.one("SELECT COUNT(*) AS n FROM session")["n"]
        assert n == 0

    def test_create_with_legacy_shots_and_nested_mode_is_refused(
        self, client, seeded,
    ):
        """The 'hybrid' the guard exists to prevent: top-level
        says nothing (so the route would dispatch to the legacy
        ``_expand_shots`` path), but ``settings`` carries
        resource-v1. Without the guard, the session would land
        as legacy-expanded and the service would later read the
        mode as resource-v1 — a hybrid that no other check
        catches. The test fails loud before that hybrid can be
        written."""
        resp = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "hybrid attempt",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [{"prompt": "a wide shot"}],
            "settings": {"composition_mode": "resource-v1"},
        })
        assert resp.status_code == 400, resp.text
        # No session, no shot, no plan.
        n_sessions = db.one("SELECT COUNT(*) AS n FROM session")["n"]
        n_shots = db.one("SELECT COUNT(*) AS n FROM shot")["n"]
        n_plans = db.one("SELECT COUNT(*) AS n FROM session_plan")["n"]
        assert n_sessions == 0
        assert n_shots == 0
        assert n_plans == 0

    def test_patch_on_resource_v1_with_nested_mode_is_refused(
        self, client, seeded,
    ):
        """Test 2: a resource-v1 session with a saved plan
        refuses a PATCH that injects the mode through
        ``settings``, regardless of the nested value. Settings
        and plan revision stay byte-for-byte equal."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "rv1 patch guard",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_rv1_patch_guard",
            "inv_room_rv1_patch_guard",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        # Snapshot the row before the refused PATCH.
        before_settings = db.one(
            "SELECT settings FROM session WHERE id=?", sid,
        )["settings"]
        before_revision = session_plan.current_revision(sid)

        for nested_value in ("", "resource-v1", "mystery-mode"):
            resp = client.patch(f"/api/sessions/{sid}", json={
                "settings": {"composition_mode": nested_value},
            })
            assert resp.status_code == 400, (
                f"nested value {nested_value!r} must be refused, got "
                f"{resp.text}"
            )
            assert "reserved field" in resp.json()["detail"], resp.text

        # Nothing changed: the settings, the plan, and the plan
        # revision are exactly what the first save produced.
        after_settings = db.one(
            "SELECT settings FROM session WHERE id=?", sid,
        )["settings"]
        assert after_settings == before_settings
        assert session_plan.current_revision(sid) == before_revision
        # And the plan JSON is unchanged.
        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan_revision"] == 1
        assert got["plan"]["look"] == INV_LOOK

    def test_patch_on_legacy_with_nested_mode_is_refused(
        self, client, seeded,
    ):
        """Test 3: a legacy session refuses a PATCH that injects
        the mode through ``settings`` at the same boundary."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "legacy patch guard",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [{"prompt": "a wide shot"}],
        }).json()["id"]
        before_settings = db.one(
            "SELECT settings FROM session WHERE id=?", sid,
        )["settings"]
        resp = client.patch(f"/api/sessions/{sid}", json={
            "settings": {"composition_mode": "resource-v1"},
        })
        assert resp.status_code == 400, resp.text
        assert "reserved field" in resp.json()["detail"], resp.text
        after_settings = db.one(
            "SELECT settings FROM session WHERE id=?", sid,
        )["settings"]
        # Settings are byte-for-byte equal: the refused PATCH
        # did not write anything.
        assert after_settings == before_settings
        # And the session still reads as legacy.
        assert session_plan.read_composition_mode(after_settings) == ""

    def test_create_without_composition_mode_anywhere_still_works(
        self, client, seeded,
    ):
        """Test 4a: a body with no composition_mode anywhere
        (top-level absent, settings dict absent or without the
        key) lands as a legacy session — the guard does not
        change the absent case."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "no mode anywhere",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [{"prompt": "a wide shot"}],
            # settings omitted entirely
        }).json()["id"]
        settings = json.loads(db.one(
            "SELECT settings FROM session WHERE id=?", sid,
        )["settings"])
        assert "composition_mode" not in settings
        assert session_plan.read_composition_mode(
            db.one("SELECT settings FROM session WHERE id=?", sid)["settings"]
        ) == ""

    def test_create_with_top_level_resource_v1_still_works(
        self, client, seeded,
    ):
        """Test 4b: the valid top-level path is unaffected by
        the guard. A create with ``composition_mode='resource-v1'``
        on the top level (and no nested key) lands as a
        resource-v1 session."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "top-level rv1 still works",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "settings": {"width": 832, "height": 1216},
        }).json()["id"]
        settings = json.loads(db.one(
            "SELECT settings FROM session WHERE id=?", sid,
        )["settings"])
        assert settings.get("composition_mode") == "resource-v1"
        # The unrelated settings still landed.
        assert settings.get("width") == 832
        assert settings.get("height") == 1216
        # And the top-level field path the plan routes use
        # agrees.
        assert session_plan.read_composition_mode(
            db.one("SELECT settings FROM session WHERE id=?", sid)["settings"]
        ) == "resource-v1"

    def test_patch_with_top_level_composition_mode_still_works(
        self, client, seeded,
    ):
        """Test 4c: the valid top-level PATCH path is unaffected
        by the guard. A PATCH with ``composition_mode='resource-v1'``
        on the top level (and no nested key) flips the session's
        mode and merges the rest of the body as before."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "patch top-level",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [{"prompt": "a wide shot"}],
        }).json()["id"]
        # First PATCH: opt into resource-v1, set width via settings.
        resp = client.patch(f"/api/sessions/{sid}", json={
            "composition_mode": "resource-v1",
            "settings": {"width": 768},
        })
        assert resp.status_code == 200, resp.text
        settings = json.loads(db.one(
            "SELECT settings FROM session WHERE id=?", sid,
        )["settings"])
        assert settings.get("composition_mode") == "resource-v1"
        assert settings.get("width") == 768
        # Second PATCH: clear the mode through the top-level
        # empty string. The nested-key check would refuse an
        # empty-string-injection through settings; the top-level
        # path is the one that removes the key.
        resp = client.patch(f"/api/sessions/{sid}", json={
            "composition_mode": "",
        })
        assert resp.status_code == 200, resp.text
        settings = json.loads(db.one(
            "SELECT settings FROM session WHERE id=?", sid,
        )["settings"])
        assert "composition_mode" not in settings
        assert session_plan.read_composition_mode(
            db.one("SELECT settings FROM session WHERE id=?", sid)["settings"]
        ) == ""


# =====================================================================
# 6d. Reserved-field inheritance guard: a model's settings JSON
# may carry anything an operator typed in, but the session
# creation route must not bleed the reserved `composition_mode`
# key from a model row into a newly created session. The model
# route is unchanged and model rows are not rewritten; only the
# dict the create call merges is filtered, so the structural
# rule is forward-looking.
# =====================================================================


class TestModelSettingsInheritanceGuard:
    """``composition_mode`` is a session-only field. A model's
    settings JSON is free-form and the model route is unchanged;
    a pre-existing model row may carry any key. The session
    creation route must drop the reserved key from the
    inherited set so a session created against such a model
    does not land as a hybrid (legacy-expanded yet reading as
    resource-v1 on the next dispatch)."""

    def _plant_model_with_composition_mode(
        self, name: str, workflow_id: int,
    ) -> int:
        """Plant a model whose settings JSON carries the
        reserved key alongside an invented width value. The
        plant bypasses the model route so the route's own
        validation does not interfere with the assertion that
        follows: the structural rule lives in the session
        route, and the test is about that route, not the
        model one.
        """
        db.run(
            "INSERT INTO model (name, trigger, workflow_id, settings, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            name,
            "4da woman",
            workflow_id,
            json.dumps({
                "composition_mode": "resource-v1",
                "width": 832,
            }),
            "now",
        )
        row = db.one("SELECT id, settings FROM model WHERE name=?", name)
        # Sanity: the plant actually carried the reserved key.
        # A test that ran against an unrelated row would still
        # pass the post-condition; this assertion is the only
        # way to know the plant is the one the test is reading.
        assert "composition_mode" in json.loads(row["settings"])
        return int(row["id"])

    def test_inherited_model_composition_mode_does_not_bleed_into_session(
        self, client, seeded,
    ):
        """Test 1: a model with ``composition_mode`` in its
        settings does not bleed the mode into a new session.
        The session expands legacy shots, has no
        ``composition_mode`` key in its stored settings, and
        inherits the unrelated ``width`` value."""
        model_id = self._plant_model_with_composition_mode(
            "inv_inherit_legacy", seeded["workflow_id"],
        )

        # Create a session WITHOUT a top-level composition_mode
        # field. The route must inherit the model's width but
        # NOT its composition_mode.
        sid = client.post("/api/sessions", json={
            "model_id": model_id,
            "name": "inherit without top level",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [{"prompt": "a wide shot"}],
        }).json()["id"]

        row = db.one("SELECT settings FROM session WHERE id=?", sid)
        settings = json.loads(row["settings"])
        # The reserved key did not bleed.
        assert "composition_mode" not in settings, (
            f"session must not carry composition_mode when no top-"
            f"level value was set; got settings={settings!r}"
        )
        # The mode is read as legacy.
        assert session_plan.read_composition_mode(row["settings"]) == ""
        # The unrelated model setting WAS inherited.
        assert settings.get("width") == 832
        # And the legacy expansion path ran: the session has
        # a composed shot. This is the "legacy-expanded" half
        # of the hybrid the guard prevents.
        full = client.get(f"/api/sessions/{sid}").json()
        assert len(full["shots"]) == 1
        assert full["shots"][0]["prompt"], (
            "legacy _expand_shots must have run for a session "
            "without a top-level composition_mode"
        )

    def test_top_level_composition_mode_persists_with_inherited_model_settings(
        self, client, seeded,
    ):
        """Test 2: with the same model, a top-level
        ``composition_mode='resource-v1'`` lands as resource-v1
        and the unrelated model settings are still inherited.
        The top-level field is the only create-time source."""
        model_id = self._plant_model_with_composition_mode(
            "inv_inherit_rv1", seeded["workflow_id"],
        )

        sid = client.post("/api/sessions", json={
            "model_id": model_id,
            "name": "top level wins",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
        }).json()["id"]

        row = db.one("SELECT settings FROM session WHERE id=?", sid)
        settings = json.loads(row["settings"])
        # The top-level path is the one that set the key.
        assert settings.get("composition_mode") == "resource-v1"
        # And the service reads it as resource-v1.
        assert session_plan.read_composition_mode(row["settings"]) == "resource-v1"
        # The unrelated model setting was inherited even on
        # the resource-v1 path: the strip is per-key, not
        # whole-dict, and the resource-v1 path inherits the
        # rest of the model settings the same way the legacy
        # path does.
        assert settings.get("width") == 832

    def test_planted_model_row_is_unchanged_after_session_creation(
        self, client, seeded,
    ):
        """Test 3: the model row is byte-for-byte unchanged
        after a session is created against it. The route
        rewrites the session's settings, never the model's.
        A pre-existing model carrying the reserved key is
        left as-is — the rule is forward-looking and does
        not rewrite the catalogue."""
        model_id = self._plant_model_with_composition_mode(
            "inv_inherit_unchanged", seeded["workflow_id"],
        )
        model_settings_before = db.one(
            "SELECT settings FROM model WHERE id=?", model_id,
        )["settings"]
        model_name_before = db.one(
            "SELECT name FROM model WHERE id=?", model_id,
        )["name"]

        # Create a session against the model. The session
        # write does not touch the model row.
        client.post("/api/sessions", json={
            "model_id": model_id,
            "name": "no model mutation",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [{"prompt": "a wide shot"}],
        })

        model_settings_after = db.one(
            "SELECT settings FROM model WHERE id=?", model_id,
        )["settings"]
        model_name_after = db.one(
            "SELECT name FROM model WHERE id=?", model_id,
        )["name"]
        # The model row is byte-for-byte equal: same settings
        # JSON text, same name. The route did not write to
        # the model.
        assert model_settings_after == model_settings_before
        assert model_name_after == model_name_before
        # The reserved key is still on the model — we do not
        # silently rewrite model rows; the new sessions the
        # model produces from this point on will simply drop
        # the key from the dict they merge.
        assert "composition_mode" in json.loads(model_settings_after)


# =====================================================================
# 7. The personal-data guard remains green.
# =====================================================================


class TestPersonalDataGuard:
    """The new module and the new test file do not introduce
    any tracked privacy leak. Reuse the canonical patterns from
    ``test_no_personal_data`` so a private fork that diverges
    from the upstream guard fails here."""

    def test_session_plan_py_carries_no_personal_data(self):
        text = (ROOT / "backend" / "session_plan.py").read_text(encoding="utf-8")
        for label, pattern in PRIVACY_PATTERNS.items():
            match = pattern.search(text)
            assert match is None, (
                f"backend/session_plan.py contains a {label} match: "
                f"{match.group(0)!r}"
            )

    def test_test_session_plan_py_carries_no_personal_data(self):
        text = (ROOT / "tests" / "test_session_plan.py").read_text(encoding="utf-8")
        for label, pattern in PRIVACY_PATTERNS.items():
            match = pattern.search(text)
            assert match is None, (
                f"tests/test_session_plan.py contains a {label} match: "
                f"{match.group(0)!r}"
            )


# =====================================================================
# Service-level sanity tests for session_plan.save_draft / get_draft.
# The HTTP layer above is what the rest of the suite exercises; these
# tests pin the Python surface independently so a future route rewrite
# cannot quietly bypass the validation or the CAS check.
# =====================================================================


class TestSessionPlanService:
    """The service layer is the contract the route exposes. Pin
    its behaviour directly so a refactor that only changes the
    HTTP boundary cannot silently change what is and is not
    accepted."""

    def test_save_draft_returns_one_for_the_first_save(
        self, client, seeded,
    ):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "service first save",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_service_first",
            "inv_room_service_first",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        # Direct call: returns the new revision.
        new_rev = session_plan.save_draft(sid, plan, expected_revision=0)
        assert new_rev == 1
        # And the row is readable through the same service.
        draft = session_plan.get_draft(sid)
        assert draft["plan_revision"] == 1
        assert draft["plan"]["version"] == "resource-v1"

    def test_save_draft_raises_on_stale_revision(
        self, client, seeded,
    ):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "service stale",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_service_stale",
            "inv_room_service_stale",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        session_plan.save_draft(sid, plan, expected_revision=0)
        with pytest.raises(session_plan.PlanRevisionStale):
            session_plan.save_draft(sid, plan, expected_revision=0)

    def test_save_draft_refuses_a_legacy_session(
        self, client, seeded,
    ):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "service legacy refused",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_service_legacy",
            "inv_room_service_legacy",
            INV_ROOM_PAYLOAD,
        )
        with pytest.raises(session_plan.SessionNotInResourceMode):
            session_plan.save_draft(sid, _build_plan(revision), expected_revision=0)

    def test_save_draft_validates_malformed_payloads(
        self, client, seeded,
    ):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "service malformed",
            "composition_mode": "resource-v1",
        }).json()["id"]

        # Wrong version.
        bad_version = {"version": "resource-v2", "takes": []}
        with pytest.raises(session_plan.PlanValidationError) as exc:
            session_plan.save_draft(sid, bad_version, expected_revision=0)
        assert "version" in str(exc.value)

        # Missing take_id.
        bad_take_id = {
            "version": "resource-v1",
            "takes": [{"label": "no id here"}],
        }
        with pytest.raises(session_plan.PlanValidationError) as exc:
            session_plan.save_draft(sid, bad_take_id, expected_revision=0)
        assert "take_id" in str(exc.value)

        # Duplicate take_id.
        dup_take = {
            "version": "resource-v1",
            "takes": [
                {"take_id": "x"},
                {"take_id": "x"},
            ],
        }
        with pytest.raises(session_plan.PlanValidationError) as exc:
            session_plan.save_draft(sid, dup_take, expected_revision=0)
        assert "duplicate" in str(exc.value)

        # Wardrobe change references a take that does not exist.
        bad_change = {
            "version": "resource-v1",
            "takes": [{"take_id": "x"}],
            "wardrobe_changes": [
                {"take_id": "y", "scope": "this_take", "wardrobe": ""},
            ],
        }
        with pytest.raises(session_plan.PlanValidationError) as exc:
            session_plan.save_draft(sid, bad_change, expected_revision=0)
        assert "does not match" in str(exc.value)

        # Wardrobe change with an unknown scope.
        bad_scope = {
            "version": "resource-v1",
            "takes": [{"take_id": "x"}],
            "wardrobe_changes": [
                {"take_id": "x", "scope": "always", "wardrobe": ""},
            ],
        }
        with pytest.raises(session_plan.PlanValidationError) as exc:
            session_plan.save_draft(sid, bad_scope, expected_revision=0)
        assert "scope" in str(exc.value)

        # No partial persistence: a refused save leaves no row.
        n = db.one(
            "SELECT COUNT(*) AS n FROM session_plan WHERE session_id=?",
            sid,
        )["n"]
        assert n == 0

    def test_get_draft_returns_none_when_no_plan_exists(
        self, client, seeded,
    ):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "service get none",
            "composition_mode": "resource-v1",
        }).json()["id"]
        assert session_plan.get_draft(sid) is None
        assert session_plan.current_revision(sid) == 0
