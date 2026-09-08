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
        result = save_resp.json()
        assert result["plan_revision"] == 1
        assert isinstance(result["conflicts"], list)

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
        result = first_save.json()
        assert result["plan_revision"] == 1
        assert isinstance(result["conflicts"], list)

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
        result = second_save.json()
        assert result["plan_revision"] == 2
        assert isinstance(result["conflicts"], list)

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
        result = first_save.json()
        assert result["plan_revision"] == 1
        assert isinstance(result["conflicts"], list)

        # A second save at the SAME revision: legal, bumps to 2.
        second_plan = _build_plan(
            revision, initial_wardrobe="a thin black silk shirt",
        )
        second_save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": second_plan, "expected_revision": 1},
        )
        assert second_save.status_code == 200, second_save.text
        result = second_save.json()
        assert result["plan_revision"] == 2
        assert isinstance(result["conflicts"], list)

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
        ra_result = ra.json()
        assert ra_result["plan_revision"] == 1
        assert isinstance(ra_result["conflicts"], list)
        rb_result = rb.json()
        assert rb_result["plan_revision"] == 1
        assert isinstance(rb_result["conflicts"], list)


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
        # Direct call: returns a dict with the new revision and
        # the computed conflicts list. The 3.3 tests pin the
        # conflict content; here we just confirm the service
        # returns the new shape.
        result = session_plan.save_draft(sid, plan, expected_revision=0)
        assert result["plan_revision"] == 1
        assert isinstance(result["conflicts"], list)
        # And the row is readable through the same service.
        draft = session_plan.get_draft(sid)
        assert draft["plan_revision"] == 1
        assert draft["plan"]["version"] == "resource-v1"
        # The plan JSON the save wrote carries the conflicts
        # under a top-level key so a later GET returns them
        # alongside the draft.
        assert "conflicts" in draft["plan"]
        assert isinstance(draft["plan"]["conflicts"], list)

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


# =====================================================================
# 8. Effective-wardrobe resolution (task 3.2 of
# ``adopt-resource-session-planning``). The resolver is a pure function
# of a validated resource-v1 plan. The eight tests below pin the rules
# the spec names for the task:
#
#   1. Constant default wardrobe: every take inherits
#      ``initial_wardrobe`` when there are no wardrobe changes.
#   2. A jacket added with ``from_here`` at take seven: takes one
#      through six keep the prior wardrobe, take seven onward
#      inherits the jacket.
#   3. An isolated ``this_take`` override: the named take switches
#      wardrobe; the following take inherits the prior persistent
#      state, not the override.
#   4. Removing a change through a later CAS save: the resolver
#      returns to the inherited state for the take that lost its
#      change.
#   5. Reordering takes across a persistent-change boundary: the
#      change follows its stable ``take_id``; the new order changes
#      who inherits what.
#   6. Multiple persistent changes and a one-take override
#      alongside them: each ``from_here`` supersedes the previous
#      one; a ``this_take`` between two persistent changes applies
#      to its take only.
#   7. Duplicate wardrobe-change events for one stable take ID are
#      refused at the validation boundary; no partial row is
#      written.
#   8. Existing legacy composition behavior is unchanged: a legacy
#      session with a wardrobe override and an editing reference
#      still composes the same prompts the baseline test pins.
# =====================================================================


class TestEffectiveWardrobeResolution:
    """Service-level tests for ``resolve_effective_wardrobes`` and
    the validation boundary that refuses duplicate events. The
    resolver is pure: each test plants a plan dict and asserts the
    dict the resolver returns, so a future change to the walk
    surfaces here as a focused failure rather than a hidden
    downstream prompt drift."""

    def test_every_take_inherits_initial_wardrobe_with_no_changes(self):
        """Test 1: constant default wardrobe. The plan's
        ``initial_wardrobe`` is the wardrobe of every take, with no
        changes to perturb the inherited state. Twelve takes is
        the size the acceptance demonstration uses; the number is
        arbitrary, the rule is "every take inherits the default"."""
        plan = {
            "version": "resource-v1",
            "look": "an invented look",
            "initial_wardrobe": INV_WARDROBE,
            "takes": [
                {"take_id": f"inv_take_{i:03d}"} for i in range(1, 13)
            ],
            "selected_resources": [],
            "wardrobe_changes": [],
        }
        effective = session_plan.resolve_effective_wardrobes(plan)
        assert len(effective) == 12
        for i in range(1, 13):
            assert effective[f"inv_take_{i:03d}"] == INV_WARDROBE, (
                f"take {i:03d} must inherit initial_wardrobe; got "
                f"{effective[f'inv_take_{i:03d}']!r}"
            )

    def test_a_jacket_added_with_from_here_at_take_seven(
        self, client, seeded,
    ):
        """Test 2: a jacket added at take seven with ``from_here``
        applies to take seven and every following take. Takes one
        through six keep the prior wardrobe. The plan round-trips
        through save and get, and the resolver reads the saved
        plan."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "jacket at seven",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_jacket_seven", "inv_room_jacket_seven", INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        plan["takes"] = [
            {"take_id": f"inv_take_{i:03d}", "label": f"frame {i}"}
            for i in range(1, 13)
        ]
        plan["wardrobe_changes"] = [
            {
                "take_id": "inv_take_007",
                "scope": "from_here",
                "wardrobe": INV_WARDROBE_JACKET,
            },
        ]
        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text

        effective = session_plan.resolve_effective_wardrobes(
            client.get(f"/api/sessions/{sid}/plan").json()["plan"],
        )
        # Takes one through six: initial wardrobe.
        for i in range(1, 7):
            assert effective[f"inv_take_{i:03d}"] == INV_WARDROBE, (
                f"take {i:03d} must keep initial_wardrobe before the "
                f"change; got {effective[f'inv_take_{i:03d}']!r}"
            )
        # Takes seven through twelve: jacket.
        for i in range(7, 13):
            assert effective[f"inv_take_{i:03d}"] == INV_WARDROBE_JACKET, (
                f"take {i:03d} must inherit the jacket from the "
                f"persistent change; got {effective[f'inv_take_{i:03d}']!r}"
            )

    def test_an_isolated_this_take_override_does_not_alter_following_takes(
        self, client, seeded,
    ):
        """Test 3: a one-take override applies to its take only.
        The following take inherits the prior persistent state,
        which (in this plan) is the initial wardrobe. The
        override is a perturbation, not a step in a walk."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "one take override",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_one_take", "inv_room_one_take", INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        plan["takes"] = [
            {"take_id": f"inv_take_{i:03d}"} for i in range(1, 7)
        ]
        # The override is on take 4; takes 5 and 6 must see the
        # initial wardrobe, NOT the override.
        plan["wardrobe_changes"] = [
            {
                "take_id": "inv_take_004",
                "scope": "this_take",
                "wardrobe": INV_WARDROBE_JACKET,
            },
        ]
        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text

        effective = session_plan.resolve_effective_wardrobes(
            client.get(f"/api/sessions/{sid}/plan").json()["plan"],
        )
        # Take 4: the override.
        assert effective["inv_take_004"] == INV_WARDROBE_JACKET
        # Take 5: NOT the override. Inherited state, which is
        # still the initial wardrobe because no ``from_here``
        # advanced it.
        assert effective["inv_take_005"] == INV_WARDROBE, (
            f"take 5 must inherit the prior persistent state, not "
            f"take 4's override; got {effective['inv_take_005']!r}"
        )
        # Belt and braces for take 6 too.
        assert effective["inv_take_006"] == INV_WARDROBE

    def test_removing_a_change_through_a_later_cas_save_restores_inherited(
        self, client, seeded,
    ):
        """Test 4: a saved change is removed by re-saving the
        plan without it. The CAS path bumps the revision, the
        new plan has no changes, and the resolver returns to
        the inherited state for every take — including the
        takes that used to inherit the now-removed change."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "remove a change via CAS",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_remove_change", "inv_room_remove_change",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        plan["takes"] = [
            {"take_id": f"inv_take_{i:03d}"} for i in range(1, 6)
        ]
        plan["wardrobe_changes"] = [
            {
                "take_id": "inv_take_003",
                "scope": "from_here",
                "wardrobe": INV_WARDROBE_JACKET,
            },
        ]
        # First save: the change is in place. The resolver
        # returns the jacket for takes 3 through 5.
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text
        first_effective = session_plan.resolve_effective_wardrobes(
            client.get(f"/api/sessions/{sid}/plan").json()["plan"],
        )
        assert first_effective["inv_take_001"] == INV_WARDROBE
        assert first_effective["inv_take_002"] == INV_WARDROBE
        assert first_effective["inv_take_003"] == INV_WARDROBE_JACKET
        assert first_effective["inv_take_004"] == INV_WARDROBE_JACKET
        assert first_effective["inv_take_005"] == INV_WARDROBE_JACKET

        # Second save: drop the change, keep the rest. The
        # expected revision is 1; the new revision is 2.
        revised = {**plan, "wardrobe_changes": []}
        second = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": revised, "expected_revision": 1},
        )
        assert second.status_code == 200, second.text
        result = second.json()
        assert result["plan_revision"] == 2
        assert isinstance(result["conflicts"], list)
        # The resolver returns to the inherited state for
        # every take. The takes that USED to inherit the
        # jacket now inherit the initial wardrobe; the take
        # whose change was removed is also back to the
        # inherited state. The order is what makes the rule
        # "removing a change restores the applicable
        # inherited wardrobe" concrete.
        second_effective = session_plan.resolve_effective_wardrobes(
            client.get(f"/api/sessions/{sid}/plan").json()["plan"],
        )
        for i in range(1, 6):
            assert second_effective[f"inv_take_{i:03d}"] == INV_WARDROBE, (
                f"after removing the change, take {i:03d} must inherit "
                f"initial_wardrobe; got {second_effective[f'inv_take_{i:03d}']!r}"
            )

    def test_reordering_takes_moves_the_persistent_change_with_the_take(
        self, client, seeded,
    ):
        """Test 5: reordering takes across a persistent-change
        boundary changes who inherits what. The change follows
        the stable ``take_id``, not the position; the resolver
        walks the NEW order, so a take that was once before
        the change is now after it (or vice versa) and
        inherits accordingly."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "reorder across a change",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_reorder_change", "inv_room_reorder_change",
            INV_ROOM_PAYLOAD,
        )
        # Three takes; the change sits on inv_take_002.
        plan = _build_plan(revision)
        plan["takes"] = [
            {"take_id": "inv_take_001"},
            {"take_id": "inv_take_002"},
            {"take_id": "inv_take_003"},
        ]
        plan["wardrobe_changes"] = [
            {
                "take_id": "inv_take_002",
                "scope": "from_here",
                "wardrobe": INV_WARDROBE_JACKET,
            },
        ]
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text
        # Before the reorder: take 1 is initial, takes 2 and 3
        # are jacket (the persistent change advanced the
        # inherited state at take 2).
        before = session_plan.resolve_effective_wardrobes(
            client.get(f"/api/sessions/{sid}/plan").json()["plan"],
        )
        assert before == {
            "inv_take_001": INV_WARDROBE,
            "inv_take_002": INV_WARDROBE_JACKET,
            "inv_take_003": INV_WARDROBE_JACKET,
        }

        # Reorder: take 2 first, then take 1, then take 3.
        # The change follows take 2; the resolver walks the
        # new order, so take 1 is now AFTER the change and
        # inherits the jacket.
        reordered = {
            **plan,
            "takes": [
                {"take_id": "inv_take_002"},
                {"take_id": "inv_take_001"},
                {"take_id": "inv_take_003"},
            ],
        }
        second = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": reordered, "expected_revision": 1},
        )
        assert second.status_code == 200, second.text
        after = session_plan.resolve_effective_wardrobes(
            client.get(f"/api/sessions/{sid}/plan").json()["plan"],
        )
        assert after == {
            "inv_take_002": INV_WARDROBE_JACKET,
            "inv_take_001": INV_WARDROBE_JACKET,
            "inv_take_003": INV_WARDROBE_JACKET,
        }, (
            f"after the reorder, every take is at or after the "
            f"persistent change; expected all three to inherit "
            f"the jacket, got {after!r}"
        )

    def test_multiple_persistent_changes_and_a_one_take_override(
        self, client, seeded,
    ):
        """Test 6: a plan with multiple ``from_here`` events
        AND a one-take override between two of them. Each
        ``from_here`` supersedes the previous; the one-take
        override is a perturbation between two persistent
        states and does not advance the inherited state. The
        resolver walks the ten takes in order."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "multiple persistent + override",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_multi_change", "inv_room_multi_change",
            INV_ROOM_PAYLOAD,
        )
        # Three wardrobe strings so the test distinguishes the
        # state transitions clearly.
        W_JACKET = INV_WARDROBE_JACKET
        W_COAT = (
            "a heavy dark wool coat over a thin grey linen shirt, dark "
            "cotton trousers, bare feet"
        )
        W_SCARF = (
            "a thin grey linen shirt, dark cotton trousers, bare feet, "
            "a thin red scarf knotted at her throat"
        )
        plan = _build_plan(revision)
        plan["takes"] = [
            {"take_id": f"inv_take_{i:03d}"} for i in range(1, 11)
        ]
        plan["wardrobe_changes"] = [
            {"take_id": "inv_take_003", "scope": "from_here", "wardrobe": W_JACKET},
            {"take_id": "inv_take_006", "scope": "from_here", "wardrobe": W_COAT},
            {"take_id": "inv_take_008", "scope": "this_take", "wardrobe": W_SCARF},
        ]
        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text

        effective = session_plan.resolve_effective_wardrobes(
            client.get(f"/api/sessions/{sid}/plan").json()["plan"],
        )
        # 1, 2: initial.
        for i in (1, 2):
            assert effective[f"inv_take_{i:03d}"] == INV_WARDROBE
        # 3, 4, 5: jacket (the first persistent change).
        for i in (3, 4, 5):
            assert effective[f"inv_take_{i:03d}"] == W_JACKET
        # 6, 7: coat (the second persistent change supersedes
        # the jacket from take 6 onward).
        for i in (6, 7):
            assert effective[f"inv_take_{i:03d}"] == W_COAT
        # 8: the one-take override. Take 8 sees the scarf, NOT
        # the coat. The inherited state stays at "coat" so
        # take 9 inherits the coat.
        assert effective["inv_take_008"] == W_SCARF
        # 9, 10: the override was a perturbation; the
        # inherited state is back to "coat".
        for i in (9, 10):
            assert effective[f"inv_take_{i:03d}"] == W_COAT, (
                f"the one-take override on take 8 must not advance "
                f"the inherited state; take {i:03d} should still "
                f"inherit the coat, got {effective[f'inv_take_{i:03d}']!r}"
            )

    def test_duplicate_wardrobe_change_events_are_refused_at_validation(
        self, client, seeded,
    ):
        """Test 7: a plan with two ``wardrobe_changes`` events
        for the same ``take_id`` is refused at the validation
        boundary (the same boundary ``save_draft`` uses). The
        API maps the refusal to a 422, the stored plan is
        byte-for-byte unchanged, and no ``session_plan`` row
        is written."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "duplicate change refused",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_duplicate", "inv_room_duplicate", INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        plan["takes"] = [
            {"take_id": f"inv_take_{i:03d}"} for i in range(1, 4)
        ]
        # Two events for inv_take_002: one this_take, one
        # from_here. Both target the same stable take_id.
        plan["wardrobe_changes"] = [
            {
                "take_id": "inv_take_002",
                "scope": "this_take",
                "wardrobe": INV_WARDROBE,
            },
            {
                "take_id": "inv_take_002",
                "scope": "from_here",
                "wardrobe": INV_WARDROBE_JACKET,
            },
        ]
        # The service layer refuses with PlanValidationError;
        # the validation message names the offending take_id
        # so the operator can fix the draft.
        with pytest.raises(session_plan.PlanValidationError) as exc:
            session_plan.resolve_effective_wardrobes(plan)
        assert "inv_take_002" in str(exc.value), str(exc.value)
        assert "more than one" in str(exc.value), str(exc.value)
        # And the save through the route is also refused. The
        # error class the route uses is the same one.
        resp = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert resp.status_code == 422, resp.text
        assert "inv_take_002" in resp.json()["detail"], resp.text
        # No partial persistence: the session has no plan row
        # and the resolver never ran on a stored draft.
        n = db.one(
            "SELECT COUNT(*) AS n FROM session_plan WHERE session_id=?",
            sid,
        )["n"]
        assert n == 0, (
            "a refused save must not leave a half-written plan "
            "row behind"
        )

    def test_legacy_composition_remains_unchanged_after_3_2(
        self, client, seeded,
    ):
        """Test 8: the new resolver and the duplicate-event
        validation do not touch the legacy composition path.
        A legacy session (no ``composition_mode``) still
        composes its shots through the existing ``_expand_shots``
        path. The assertion is a small, byte-for-byte check
        that the trigger, base, look, wardrobe and take
        prompt are joined the same way the 1.3 baseline pins
        on a different fixture, with the same explicit
        ``{trigger}`` placeholder rule."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "legacy still composes",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [
                # Standard take: full composition.
                {
                    "label": "wide",
                    "prompt": (
                        "standing square to the camera with her hands "
                        "at her sides, full body in frame."
                    ),
                },
                # Take with its own wardrobe: take wins, session's
                # wardrobe does not appear in the same line.
                {
                    "label": "jacket on",
                    "prompt": (
                        "turned three quarters to the camera, one hand "
                        "on her hip, the other raised to her collar."
                    ),
                    "wardrobe": (
                        "the same grey linen shirt, dark cotton "
                        "trousers, a loose dark jacket over the shirt, "
                        "bare feet"
                    ),
                },
                # Explicit {trigger} placeholder: NOT prepended a
                # second time. The {trigger} text is in the take
                # itself, so the composer does not double it.
                {
                    "label": "explicit trigger",
                    "prompt": (
                        "close-up of {trigger}, the camera centred on "
                        "her face."
                    ),
                },
            ],
        }).json()["id"]
        full = client.get(f"/api/sessions/{sid}").json()
        by_label = {s["shot_label"]: s for s in full["shots"]}
        # The session's mode is still legacy: no
        # ``composition_mode`` key in settings.
        row = db.one("SELECT settings FROM session WHERE id=?", sid)
        settings = json.loads(row["settings"])
        assert "composition_mode" not in settings, (
            f"legacy session must not carry composition_mode in "
            f"settings after 3.2; got {settings!r}"
        )
        # INV_LOOK already ends with a full stop, so the
        # composer's _sentences does not append another. The
        # baseline 1.3 test pins the same rule with a non-
        # terminating look, so this test also exercises the
        # "the look already has its own punctuation, leave it
        # alone" branch.
        # Standard take: trigger + base + look + wardrobe + take.
        assert by_label["wide"]["prompt"] == (
            "4da woman. photo, 35mm. " + INV_LOOK + " " + INV_WARDROBE + ". "
            "standing square to the camera with her hands at her "
            "sides, full body in frame."
        ), (
            f"legacy text-to-image composition drifted; got "
            f"{by_label['wide']['prompt']!r}"
        )
        # Per-take wardrobe: the take's wins, the session's
        # does not appear in the same line.
        assert by_label["jacket on"]["prompt"] == (
            "4da woman. photo, 35mm. " + INV_LOOK + " "
            "the same grey linen shirt, dark cotton trousers, a "
            "loose dark jacket over the shirt, bare feet. "
            "turned three quarters to the camera, one hand on her "
            "hip, the other raised to her collar."
        ), (
            f"per-take wardrobe override drifted; got "
            f"{by_label['jacket on']['prompt']!r}"
        )
        assert INV_WARDROBE not in by_label["jacket on"]["prompt"]
        # Explicit {trigger}: not prepended a second time.
        assert by_label["explicit trigger"]["prompt"] == (
            "photo, 35mm. " + INV_LOOK + " " + INV_WARDROBE + ". "
            "close-up of 4da woman, the camera centred on her face."
        ), (
            f"explicit {{trigger}} placeholder was not honoured; got "
            f"{by_label['explicit trigger']['prompt']!r}"
        )
        # The negative is still inherited from the model.
        for label, shot in by_label.items():
            assert shot["negative"] == "blurry", (
                f"shot {label!r} has negative {shot['negative']!r}, "
                f"expected 'blurry'"
            )


# =====================================================================
# 9. Constant identity/look and explicit preparation invalidation
# (task 3.3 of ``adopt-resource-session-planning``).
#
# The contract 3.3 pins:
#
#   * A resource-v1 plan's ``look`` is the authoritative constant for
#     the session's place and lighting. A selected ``rooms`` or
#     ``fused_scenes`` resource that carries its own descriptive
#     fields (``label``, ``scene_theme``, ``prompt``) is recorded in
#     provenance but does NOT silently replace the plan's look. The
#     conflict is surfaced in the save response so a frontend can
#     show it before preparation.
#
#   * A draft edit (a successful save) explicitly invalidates every
#     ``prepared_take`` row for the session that is in ``pending`` or
#     ``ready`` status, except rows already at the new plan revision.
#     Rows in ``generated`` or ``invalidated`` status are immutable
#     history; the pass leaves them alone, their ``final_prompt``,
#     ``provenance`` and ``linked_shot_id`` are byte-for-byte
#     unchanged.
#
#   * Once a session has at least one prepared_take in ``generated``
#     status, a save that changes the look, the initial wardrobe, or
#     the selected resources is refused with
#     ``PlanConstantsFrozenAfterGenerated``. The refusal leaves every
#     row byte-for-byte unchanged. Wardrobe changes and take
#     reorders are not constant changes; they remain legal and they
#     still invalidate ungenerated rows.
#
#   * Legacy composition (no ``composition_mode``) is unchanged: a
#     legacy session still composes its shots through
#     ``_expand_shots`` and refuses the plan routes.
# =====================================================================


# A second resource payload for the 3.3 conflict tests: a rooms entry
# whose ``label`` and ``scene_theme`` are visibly different from
# ``INV_LOOK`` so the structural conflict is well-formed and a
# future change to the plan's look does not silently erase the
# resource's content.
INV_ROOMS_CONFLICT_LABEL = "an invented forest clearing at noon"
INV_ROOMS_CONFLICT_THEME = (
    "An open clearing in a thin birch wood. The canopy is high and "
    "broken; the ground is dry pine needles. A single flat stone "
    "sits at the centre. The light comes straight down and casts no "
    "shadows."
)

# A fused_scenes payload for the same purpose: it carries its own
# ``prompt`` that names a different place from the plan's look.
INV_FUSED_PROMPT = (
    "A tall white room with a single window at the back. The floor "
    "is pale concrete, the walls are bare. The light is a flat, even "
    "north-facing glow with no warm tones."
)
INV_FUSED_PAYLOAD = {
    "id": "inv_fused_studio_white",
    "label": "an invented white room",
    "prompt": INV_FUSED_PROMPT,
    "tags": ["indoor", "studio", "white"],
    "weight": 1.0,
}


def _build_fused_revision(library_key: str, source_id: str, payload: dict) -> dict:
    """Like ``_build_revision`` but registers a ``fused_scenes`` library
    so the conflict detector picks the right scene kind."""
    library_id = resource_store.ensure_library(library_key, kind="fused_scenes")
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


def _plant_prepared_take(
    session_id: int, plan_revision: int, take_id: str, *,
    status: str = "ready", linked_shot_id: int | None = None,
    final_prompt: str = "a prepared prompt",
) -> int:
    """Plant a prepared_take row directly for a test.

    The resource-v1 path does not expose a "prepare a take" endpoint
    yet (3.4 / 4.x will); the 3.3 tests need rows in every state
    to pin the invalidation rules, and direct SQL is the only way
    to land the seed deterministically. Returns the new row id.
    """
    now = db.now()
    return db.run(
        "INSERT INTO prepared_take "
        "(session_id, plan_revision, take_id, final_prompt, "
        "effective_state, mapping_version, compiler_version, "
        "provenance, status, linked_shot_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        session_id, plan_revision, take_id, final_prompt,
        "{}", "v-test", "v-test", "{}", status, linked_shot_id, now, now,
    )


def _plant_shot(session_id: int, prompt: str = "a shot prompt") -> int:
    """Plant a minimal shot row for a test. Returns the new shot id."""
    now = db.now()
    return db.run(
        "INSERT INTO shot (session_id, prompt, status, created_at) "
        "VALUES (?, ?, 'done', ?)",
        session_id, prompt, now,
    )


def _prepared_take_rows(session_id: int) -> list[dict]:
    """Return every prepared_take row for the session, ordered by id."""
    return list(db.q(
        "SELECT id, take_id, plan_revision, status, final_prompt, "
        "linked_shot_id "
        "FROM prepared_take WHERE session_id = ? ORDER BY id",
        session_id,
    ))


class TestConstantsIdentityAndExplicitInvalidation:
    """Resource-v1 plans must keep the session's identity/look
    constant against contradicting resource suggestions, must
    explicitly invalidate ungenerated prepared_take rows on every
    successful save, and must refuse constant changes after a take
    has been generated. The suite pins the five invariants the spec
    names for 3.3."""

    # --- 9.1 Source suggestion does not silently overwrite the look ----

    def test_a_rooms_resource_does_not_silently_overwrite_the_plan_look(
        self, client, seeded,
    ):
        """A rooms resource that carries its own ``label`` and
        ``scene_theme`` does NOT replace the plan's look. The plan's
        look is preserved; the conflict is shown in the save
        response; the resource's content is recorded in provenance
        via the persisted ``selected_resources`` triple. Neither
        choice is silently overwritten.
        """
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "rooms resource vs look",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_resource_vs_look",
            "inv_room_resource_vs_look",
            {
                **INV_ROOM_PAYLOAD,
                "label": INV_ROOMS_CONFLICT_LABEL,
                "scene_theme": INV_ROOMS_CONFLICT_THEME,
            },
        )
        plan = _build_plan(revision)

        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text
        body = save.json()
        assert body["plan_revision"] == 1

        # The conflict list is non-empty and names the resource
        # by its triple. The plan's look is preserved verbatim.
        # The detector walks every descriptive_input field the
        # contract publishes for the resource's kind and produces
        # one neutral marker per non-empty field; a rooms
        # resource with both ``label`` and ``scene_theme`` set
        # produces two markers, each naming the offending field
        # and the value it carried. The marker is a single
        # neutral kind regardless of field or content; it does
        # NOT decide by content whether the value competes with
        # look or wardrobe.
        conflicts = body["conflicts"]
        assert isinstance(conflicts, list) and conflicts, (
            f"a rooms resource with its own label and scene_theme "
            f"must surface structural conflicts; got {conflicts!r}"
        )
        # Group by field so the assertion is robust to the order
        # the detector iterates the contract.
        by_field = {m["resource_field"]: m for m in conflicts}
        assert set(by_field) >= {"label", "scene_theme"}, (
            f"both label and scene_theme must surface; got {set(by_field)!r}"
        )
        for marker in conflicts:
            assert marker["kind"] == (
                "resource_descriptive_vs_plan_constants"
            ), (
                f"the marker kind is the single neutral value; got "
                f"{marker['kind']!r}"
            )
            assert marker["library_key"] == "inv_rooms_resource_vs_look"
            assert marker["source_id"] == "inv_room_resource_vs_look"
            assert marker["content_digest"] == revision["content_digest"]
            # The plan's look is shown because the plan set it;
            # the plan's initial_wardrobe is shown too because
            # ``_build_plan`` defaults it. The marker carries
            # both sides — neither is silently rewritten.
            assert marker["plan_look"] == INV_LOOK
            assert marker["plan_initial_wardrobe"] == INV_WARDROBE
            # The message is explicit about the human-review
            # rule and the no-content-classification rule.
            assert "human review is required" in marker["message"]
            assert (
                "does NOT decide by content" in marker["message"]
            )
        # The two specific fields the resource carries are
        # surfaced with their values, so the operator can see
        # exactly what the resource said.
        assert by_field["label"]["resource_value"] == INV_ROOMS_CONFLICT_LABEL
        assert by_field["scene_theme"]["resource_value"] == (
            INV_ROOMS_CONFLICT_THEME
        )

        # The plan's look is unchanged. A GET round-trip reads
        # back the exact ``look`` the caller saved, and the
        # selected_resources triple still identifies the
        # resource — neither side was rewritten.
        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan"]["look"] == INV_LOOK
        assert got["plan"]["selected_resources"] == [
            {
                "library_key": "inv_rooms_resource_vs_look",
                "source_id": "inv_room_resource_vs_look",
                "content_digest": revision["content_digest"],
            },
        ]
        # The plan JSON the GET returns carries the conflicts
        # alongside the draft.
        assert got["plan"].get("conflicts") == conflicts

    def test_a_fused_scenes_resource_does_not_silently_overwrite_the_plan_look(
        self, client, seeded,
    ):
        """A ``fused_scenes`` resource carries its own ``prompt``
        (free prose). The same rule applies: the plan's look
        wins, the resource's prompt is recorded, the conflict is
        shown. The detector is structural and neutral: it walks
        every ``descriptive_input`` field the contract publishes
        for the kind and produces a marker per non-empty field.
        The marker is the same single neutral kind regardless
        of field name or content."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "fused resource vs look",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
        }).json()["id"]
        revision = _build_fused_revision(
            "inv_fused_resource_vs_look",
            "inv_fused_resource_vs_look",
            INV_FUSED_PAYLOAD,
        )
        plan = _build_plan(revision)
        # Strip the wardrobe change from the default fixture so
        # the test is not also asserting the 3.2 behaviour; the
        # 3.3 invariant under test is "no silent overwrite of the
        # look by a fused_scenes resource".
        plan["wardrobe_changes"] = []

        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text
        body = save.json()
        assert body["plan_revision"] == 1

        # The fused_scenes contract names ``prompt`` and
        # ``label`` (and ``scene_theme``, ``tags``) as
        # descriptive_input fields. The fixture sets ``prompt``
        # and ``label``; the detector surfaces both. The marker
        # is per-field, not per-resource, and the kind is a
        # single neutral value.
        conflicts = body["conflicts"]
        assert conflicts, (
            f"a fused_scenes resource with its own prompt must "
            f"surface at least one structural conflict; got "
            f"{conflicts!r}"
        )
        by_field = {m["resource_field"]: m for m in conflicts}
        for marker in conflicts:
            assert marker["kind"] == (
                "resource_descriptive_vs_plan_constants"
            )
            assert marker["library_key"] == "inv_fused_resource_vs_look"
            assert marker["plan_look"] == INV_LOOK
            assert "human review is required" in marker["message"]
        assert by_field["prompt"]["resource_value"] == INV_FUSED_PROMPT
        assert by_field["label"]["resource_value"] == "an invented white room"
        # The plan's look is unchanged.
        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan"]["look"] == INV_LOOK

    def test_no_conflict_is_reported_when_no_plan_constant_is_set(
        self, client, seeded,
    ):
        """A draft with both ``look`` and ``initial_wardrobe`` empty
        is the "no fixed constant yet" state; there is nothing for
        a resource to compete with, so the detector returns an
        empty list. A later task that sets a constant is the
        natural place for a marker to appear."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "no fixed constant no marker",
            "composition_mode": "resource-v1",
            "look": "",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_no_constant",
            "inv_room_no_constant",
            {
                **INV_ROOM_PAYLOAD,
                "label": INV_ROOMS_CONFLICT_LABEL,
                "scene_theme": INV_ROOMS_CONFLICT_THEME,
            },
        )
        plan = _build_plan(revision)
        # Wipe BOTH constants: no look, no initial_wardrobe.
        plan["look"] = ""
        plan["initial_wardrobe"] = ""
        # Drop the wardrobe change so the test is not also
        # asserting 3.2 behaviour.
        plan["wardrobe_changes"] = []

        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text
        body = save.json()
        assert body["plan_revision"] == 1
        # No marker: no fixed plan constant, nothing to
        # surface the resource's descriptive input against.
        assert body["conflicts"] == []

    def test_auxiliary_resources_never_produce_a_conflict(
        self, client, seeded,
    ):
        """Auxiliary kinds (translation_map, cut_map, mined_families,
        mined_labels) carry no descriptive input. Selecting one
        alongside a non-empty look is NOT a conflict — the
        resource has no scene description that would compete with
        the look. The detector skips these kinds silently."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "auxiliary resource never conflicts",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
        }).json()["id"]
        # A translation_map auxiliary entry. Carries no
        # ``label``/``scene_theme``/``prompt`` (those are scene
        # fields); only record-shape payload.
        library_id = resource_store.ensure_library(
            "inv_translation_map_aux", kind="translation_map",
        )
        revision_id = resource_store.record_revision(
            library_id,
            "inv_translation_map_aux",
            {
                "fields": [
                    {"source": "uninvented-source", "translation": "an invented English line"},
                ],
            },
        )
        aux_revision = resource_store.get_revision(revision_id=revision_id)
        assert aux_revision is not None
        # Plan selects ONLY the auxiliary resource.
        plan = _build_plan({
            "library_key": "inv_translation_map_aux",
            "source_id": "inv_translation_map_aux",
            "content_digest": aux_revision["content_digest"],
        })
        plan["wardrobe_changes"] = []

        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text
        body = save.json()
        # The auxiliary resource carries no descriptive input
        # that would compete with the look. No conflict.
        assert body["conflicts"] == []

    def test_a_structured_value_like_tags_list_is_preserved_in_full_in_the_marker(
        self, client, seeded,
    ):
        """A JSON-shaped ``descriptive_input`` (a list, not a
        string) is preserved verbatim in the marker. The detector
        does not coerce, summarize, or string-format the value:
        a list of category tags is a different shape from a
        paragraph of prose, and a future preparation task reads
        the value as the resource wrote it. The plan's constants
        are shown alongside; the marker declares human review.
        """
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "structured tags list preserved",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
        }).json()["id"]
        # A rooms resource whose ``tags`` field is a JSON list
        # of category tags. The contract classifies ``tags`` as
        # ``descriptive_input`` for ``rooms``; the detector
        # must surface the field as a marker, with the list
        # preserved verbatim.
        revision = _build_revision(
            "inv_rooms_tags_list_preserved",
            "inv_room_tags_list_preserved",
            {
                **INV_ROOM_PAYLOAD,
                "label": "a plain descriptive label",
                "scene_theme": "a plain descriptive theme",
                "tags": [
                    "indoor", "studio", "morning",
                    "low-key", "north-facing-window",
                ],
            },
        )
        plan = _build_plan(revision)
        plan["wardrobe_changes"] = []

        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text
        body = save.json()
        by_field = {m["resource_field"]: m for m in body["conflicts"]}
        assert "tags" in by_field, (
            f"a tags list must surface as a structural conflict; "
            f"got fields={set(by_field)!r}"
        )
        marker = by_field["tags"]
        # The marker is the single neutral kind, regardless of
        # the value's shape.
        assert marker["kind"] == "resource_descriptive_vs_plan_constants"
        # The list is preserved verbatim: same length, same
        # order, same strings. The detector does NOT coerce the
        # list into a comma-joined string or a stringified
        # version.
        assert marker["resource_value"] == [
            "indoor", "studio", "morning",
            "low-key", "north-facing-window",
        ], (
            f"the tags list was not preserved verbatim; got "
            f"{marker['resource_value']!r}"
        )
        # The plan's constants are shown because the plan set
        # both; the marker names both sides, not just one.
        assert marker["plan_look"] == INV_LOOK
        assert marker["plan_initial_wardrobe"] == INV_WARDROBE
        # The human-review message.
        assert "human review is required" in marker["message"]

        # The plan's constants are preserved verbatim. The
        # triple in ``selected_resources`` is the only
        # reference to the resource the plan carries; the
        # full payload (including the tags list) stays in
        # ``asset_revision.payload``.
        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan"]["look"] == INV_LOOK
        assert got["plan"]["initial_wardrobe"] == INV_WARDROBE
        # The plan does NOT carry the payload; only the
        # immutable revision triple.
        assert got["plan"]["selected_resources"] == [
            {
                "library_key": "inv_rooms_tags_list_preserved",
                "source_id": "inv_room_tags_list_preserved",
                "content_digest": revision["content_digest"],
            },
        ]
        assert "tags" not in got["plan"], (
            "the plan must not carry the resource payload; "
            "tags is a resource field, not a plan field"
        )
        # The payload is in asset_revision. A future
        # preparation task reads it from there.
        payload = json.loads(db.one(
            "SELECT payload FROM asset_revision "
            "WHERE library_id = ? AND source_id = ? AND "
            "content_digest = ?",
            revision["library_id"], revision["source_id"],
            revision["content_digest"],
        )["payload"])
        assert payload["tags"] == [
            "indoor", "studio", "morning",
            "low-key", "north-facing-window",
        ]

    def test_scene_prose_with_material_words_does_not_classify_as_clothing(
        self, client, seeded,
    ):
        """Scene prose that mentions fabric or material words
        (``linen curtains``, ``leather sofa``) is NOT classified
        as a wardrobe conflict by the detector. The detector
        does not look at content: it surfaces a single neutral
        marker for every non-empty ``descriptive_input``,
        regardless of whether the words sound like clothing.
        A human reads the marker and decides. The marker names
        BOTH plan constants and the field's full value."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "scene prose with material words",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
        }).json()["id"]
        # A rooms resource whose scene_theme mentions fabric
        # and material words that COULD be misread as clothing
        # prose by a content-based classifier. The detector
        # does not look at content; the marker is the single
        # neutral kind.
        revision = _build_revision(
            "inv_rooms_scene_materials",
            "inv_room_scene_materials",
            {
                **INV_ROOM_PAYLOAD,
                "label": "a plain descriptive label",
                "scene_theme": (
                    "a quiet room with linen curtains by the window, "
                    "a leather sofa in the corner, a wool blanket "
                    "draped over the arm"
                ),
            },
        )
        plan = _build_plan(revision)
        plan["wardrobe_changes"] = []

        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text
        body = save.json()
        by_field = {m["resource_field"]: m for m in body["conflicts"]}
        scene_marker = by_field["scene_theme"]
        # Single neutral kind — NOT a wardrobe-specific kind.
        # The detector does not claim the value competes
        # exclusively with the plan's initial_wardrobe; it
        # shows both sides and asks for human review.
        assert scene_marker["kind"] == (
            "resource_descriptive_vs_plan_constants"
        )
        # Both plan constants are visible in the same marker.
        assert scene_marker["plan_look"] == INV_LOOK
        assert scene_marker["plan_initial_wardrobe"] == INV_WARDROBE
        # The full value is preserved verbatim — the detector
        # does not redact material words, does not split on
        # them, does not rewrite them. The operator reads the
        # whole sentence and decides.
        assert scene_marker["resource_value"] == (
            "a quiet room with linen curtains by the window, "
            "a leather sofa in the corner, a wool blanket "
            "draped over the arm"
        )
        # The message states the human-review rule and the
        # no-content-classification rule.
        assert "human review is required" in scene_marker["message"]
        assert "does NOT decide by content" in scene_marker["message"]

    def test_uniform_fit_value_is_visible_alongside_the_fixed_wardrobe(
        self, client, seeded,
    ):
        """The contract publishes ``uniform_fit`` as a
        ``descriptive_input`` for ``rooms``. A non-empty value
        is surfaced as a neutral marker. The marker shows the
        plan's fixed ``initial_wardrobe`` alongside the value;
        the marker does NOT claim the value replaces the
        wardrobe — it asks for human review and the detector
        does not classify the value by content."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "uniform_fit visible alongside wardrobe",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_uniform_fit_visible",
            "inv_room_uniform_fit_visible",
            {
                **INV_ROOM_PAYLOAD,
                "label": "a plain descriptive label",
                "scene_theme": "a plain descriptive theme",
                "uniform_fit": (
                    "a dark wool suit with shoulder pads, a stiff "
                    "white shirt, polished black shoes"
                ),
            },
        )
        plan = _build_plan(revision)
        plan["wardrobe_changes"] = []

        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text
        body = save.json()
        by_field = {m["resource_field"]: m for m in body["conflicts"]}
        # The uniform_fit field produces a marker. The marker
        # shows the value and the plan's initial_wardrobe; the
        # operator decides what to do.
        assert "uniform_fit" in by_field, (
            f"a uniform_fit value must surface as a marker; "
            f"got fields={set(by_field)!r}"
        )
        marker = by_field["uniform_fit"]
        # Single neutral kind — the marker does NOT claim the
        # value competes with the wardrobe exclusively. The
        # detector does not classify by content.
        assert marker["kind"] == (
            "resource_descriptive_vs_plan_constants"
        )
        # The value is preserved verbatim.
        assert marker["resource_value"] == (
            "a dark wool suit with shoulder pads, a stiff "
            "white shirt, polished black shoes"
        )
        # The plan's fixed wardrobe is shown alongside. The
        # operator sees BOTH sides — what the resource
        # suggested and what the plan holds constant.
        assert marker["plan_initial_wardrobe"] == INV_WARDROBE
        assert marker["plan_look"] == INV_LOOK
        # The human-review message.
        assert "human review is required" in marker["message"]
        assert "does NOT decide by content" in marker["message"]

        # The plan's constants are preserved verbatim. The
        # resource's uniform_fit value does NOT overwrite the
        # plan's initial_wardrobe; the plan only carries the
        # immutable revision triple, and the payload stays in
        # ``asset_revision.payload``.
        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan"]["look"] == INV_LOOK
        assert got["plan"]["initial_wardrobe"] == INV_WARDROBE
        assert "uniform_fit" not in got["plan"]
        payload = json.loads(db.one(
            "SELECT payload FROM asset_revision "
            "WHERE library_id = ? AND source_id = ? AND "
            "content_digest = ?",
            revision["library_id"], revision["source_id"],
            revision["content_digest"],
        )["payload"])
        assert payload["uniform_fit"] == (
            "a dark wool suit with shoulder pads, a stiff "
            "white shirt, polished black shoes"
        )

    def test_a_resource_identity_suggestion_never_modifies_session_model_id(
        self, client, seeded,
    ):
        """A resource that carries identity-related fields the
        contract declares for ``rooms`` (``profile`` and
        ``body_profile``) does NOT modify the session's
        ``model_id``. The session's ``model_id`` is the
        character binding; a plan save — including a save that
        selects a resource naming a different character — leaves
        it byte-for-byte unchanged. The plan carries only the
        immutable revision triple; the payload (including any
        ``profile``/``body_profile`` the resource carries)
        stays in ``asset_revision.payload`` and is not echoed
        into the plan, the session row, or the model row.

        The detector only iterates ``descriptive_input``
        fields; the contract does not classify ``profile`` or
        ``body_profile`` as ``descriptive_input`` for ``rooms``,
        so the detector does not surface a marker for them and
        a future preparation task reads them from the
        revision's payload, not from the plan."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "resource identity suggestion preserved",
            "composition_mode": "resource-v1",
        }).json()["id"]
        # Capture the model_id, trigger and name before the
        # save. The assertion is a byte-for-byte check on the
        # row the session create call wrote, plus the trigger
        # string the model row carries (the model_id is the
        # binding; the trigger is the human-readable label).
        before = db.one(
            "SELECT s.model_id AS model_id, m.trigger AS trigger, "
            "m.name AS name "
            "FROM session s JOIN model m ON s.model_id = m.id "
            "WHERE s.id = ?",
            sid,
        )
        # A rooms resource that names a different character in
        # the identity-related fields the contract declares for
        # ``rooms``. The plan route does not accept a
        # ``model_id`` field at all; the character is set on
        # session create and never rewritten by a plan save.
        revision = _build_revision(
            "inv_rooms_identity_suggestion",
            "inv_room_identity_suggestion",
            {
                **INV_ROOM_PAYLOAD,
                "profile": "an entirely different body profile",
                "body_profile": "a different body shape",
            },
        )
        plan = _build_plan(revision)
        plan["wardrobe_changes"] = []

        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text

        # The session's model_id is byte-for-byte the same.
        # The plan save did not move the character identity.
        # The trigger and the model's human-readable name are
        # also unchanged.
        after = db.one(
            "SELECT s.model_id AS model_id, m.trigger AS trigger, "
            "m.name AS name "
            "FROM session s JOIN model m ON s.model_id = m.id "
            "WHERE s.id = ?",
            sid,
        )
        assert after["model_id"] == before["model_id"], (
            f"the session's model_id was rewritten by a plan "
            f"save; before={before['model_id']!r}, "
            f"after={after['model_id']!r}"
        )
        assert after["trigger"] == before["trigger"]
        assert after["name"] == before["name"]

        # The detector did not surface a marker for the
        # identity-related fields. The contract does not
        # classify them as ``descriptive_input``; they are
        # carried in the resource's payload, which lives in
        # ``asset_revision.payload`` and is read from there by
        # a future preparation task.
        body = save.json()
        for marker in body["conflicts"]:
            assert marker.get("resource_field") not in (
                "profile", "body_profile",
            ), (
                f"a non-descriptive_input field was surfaced as "
                f"a conflict; got {marker!r}"
            )

        # The plan's selected_resources triple is the only
        # reference to the resource the plan carries. The
        # payload stays in asset_revision.
        got = client.get(f"/api/sessions/{sid}/plan").json()
        assert got["plan"]["selected_resources"] == [
            {
                "library_key": "inv_rooms_identity_suggestion",
                "source_id": "inv_room_identity_suggestion",
                "content_digest": revision["content_digest"],
            },
        ]
        assert "profile" not in got["plan"]
        assert "body_profile" not in got["plan"]
        payload = json.loads(db.one(
            "SELECT payload FROM asset_revision "
            "WHERE library_id = ? AND source_id = ? AND "
            "content_digest = ?",
            revision["library_id"], revision["source_id"],
            revision["content_digest"],
        )["payload"])
        assert payload["profile"] == "an entirely different body profile"
        assert payload["body_profile"] == "a different body shape"

    # --- 9.2 Draft edit invalidates ungenerated prepared_takes -------

    def test_a_draft_edit_invalidates_only_ungenerated_prepared_takes(
        self, client, seeded,
    ):
        """A successful save explicitly invalidates every prepared_take
        row for the session in ``pending`` or ``ready`` status, and
        leaves ``generated`` and ``invalidated`` rows untouched. The
        pass runs inside the same transaction as the plan write so a
        refused save is the only path that could leave the table in
        an inconsistent state — and a refused save never reaches it.
        """
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "draft edit invalidates ungenerated",
            "composition_mode": "resource-v1",
        }).json()["id"]
        # First save: the draft. The revision is now 1.
        revision = _build_revision(
            "inv_rooms_draft_invalidation",
            "inv_room_draft_invalidation",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text
        assert first.json()["plan_revision"] == 1

        # Plant one row per status, all at plan_revision 1
        # (the current revision). The second save will bump to
        # revision 2; the invalidation pass will then target
        # these rows because their plan_revision differs from 2.
        shot_id = _plant_shot(sid)
        ready_id = _plant_prepared_take(
            sid, 1, "inv_take_ready", status="ready",
        )
        pending_id = _plant_prepared_take(
            sid, 1, "inv_take_pending", status="pending",
        )
        generated_id = _plant_prepared_take(
            sid, 1, "inv_take_generated", status="generated",
            linked_shot_id=shot_id,
            final_prompt="the final prompt used for the generated take",
        )
        already_invalidated_id = _plant_prepared_take(
            sid, 1, "inv_take_invalidated", status="invalidated",
        )

        # Second save: same plan, no constant change. The
        # invalidation pass marks the ungenerated rows.
        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 1},
        )
        assert save.status_code == 200, save.text
        assert save.json()["plan_revision"] == 2

        # The ungenerated rows moved to ``invalidated``. The
        # generated row stays in ``generated`` with its prompt and
        # linked_shot_id unchanged. The already-invalidated row
        # stays as it was.
        rows_by_take = {
            row["take_id"]: row
            for row in _prepared_take_rows(sid)
        }
        assert rows_by_take["inv_take_ready"]["status"] == "invalidated"
        assert rows_by_take["inv_take_pending"]["status"] == "invalidated"
        assert rows_by_take["inv_take_generated"]["status"] == "generated"
        assert rows_by_take["inv_take_generated"]["final_prompt"] == (
            "the final prompt used for the generated take"
        )
        assert rows_by_take["inv_take_generated"]["linked_shot_id"] == shot_id
        assert rows_by_take["inv_take_invalidated"]["status"] == "invalidated"
        # The row ids are preserved: the pass is status-only, no
        # delete, no rewrite of the snapshot.
        assert rows_by_take["inv_take_ready"]["id"] == ready_id
        assert rows_by_take["inv_take_pending"]["id"] == pending_id
        assert rows_by_take["inv_take_generated"]["id"] == generated_id
        assert (
            rows_by_take["inv_take_invalidated"]["id"] == already_invalidated_id
        )

    def test_a_queued_or_generated_snapshot_remains_unchanged_after_a_draft_edit(
        self, client, seeded,
    ):
        """A generated prepared_take and the shot it points at are
        history. A later draft edit MUST NOT rewrite the row's
        prompt, status, or linked_shot_id, and MUST NOT touch the
        linked shot. The pass targets only the ungenerated rows.
        """
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "generated snapshot immutable",
            "composition_mode": "resource-v1",
        }).json()["id"]
        # First save: the draft. Revision 1.
        revision = _build_revision(
            "inv_rooms_generated_immutable",
            "inv_room_generated_immutable",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text
        assert first.json()["plan_revision"] == 1

        # Plant a finished shot the prepared_take will point at,
        # and a generated prepared_take row at the current
        # revision (1). The next save bumps to revision 2 and
        # the invalidation pass targets rows that are NOT at 2;
        # the generated row must remain untouched.
        shot_id = _plant_shot(
            sid, prompt="a finished photograph prompt",
        )
        original_shot = db.one(
            "SELECT prompt, status FROM shot WHERE id = ?", shot_id,
        )
        prepared_id = _plant_prepared_take(
            sid, 1, "inv_take_generated",
            status="generated", linked_shot_id=shot_id,
            final_prompt="a finalized, generated prompt",
        )
        original_prepared = db.one(
            "SELECT take_id, plan_revision, status, final_prompt, "
            "linked_shot_id, effective_state, provenance, "
            "mapping_version, compiler_version "
            "FROM prepared_take WHERE id = ?",
            prepared_id,
        )

        # Second save: a real draft edit. The wardrobe change is
        # NOT a constant change, so the constant-change guard
        # does not fire; the invalidation pass targets the
        # generated row, but the pass must leave generated rows
        # untouched.
        edited_plan = {
            **plan,
            "wardrobe_changes": [
                {
                    "take_id": "take-002",
                    "scope": "this_take",
                    "wardrobe": INV_WARDROBE_JACKET,
                },
            ],
        }
        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": edited_plan, "expected_revision": 1},
        )
        assert save.status_code == 200, save.text
        assert save.json()["plan_revision"] == 2

        # The generated prepared_take row is byte-for-byte the
        # same: status, plan_revision, final_prompt,
        # linked_shot_id, effective_state, provenance, mapping
        # version, compiler version — all unchanged.
        after_prepared = db.one(
            "SELECT take_id, plan_revision, status, final_prompt, "
            "linked_shot_id, effective_state, provenance, "
            "mapping_version, compiler_version "
            "FROM prepared_take WHERE id = ?",
            prepared_id,
        )
        assert after_prepared == original_prepared, (
            f"a generated prepared_take was rewritten by a draft "
            f"edit; before={original_prepared!r}, after={after_prepared!r}"
        )
        # The linked shot is also unchanged. The new save did not
        # touch its prompt or status.
        after_shot = db.one(
            "SELECT prompt, status FROM shot WHERE id = ?", shot_id,
        )
        assert after_shot == original_shot, (
            f"a linked shot was rewritten by a draft edit; "
            f"before={original_shot!r}, after={after_shot!r}"
        )

    # --- 9.3 Boundary after a generated take: constant change refused ---

    def test_constant_change_is_refused_after_a_generated_take(
        self, client, seeded,
    ):
        """Once a session has a generated prepared_take, a save
        that changes ``look`` is refused with
        ``PlanConstantsFrozenAfterGenerated``. The refusal leaves
        the plan row, every prepared_take row, and every linked
        shot byte-for-byte unchanged."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "constant change refused after generated",
            "composition_mode": "resource-v1",
            "look": INV_LOOK,
        }).json()["id"]
        # Plant a generated row. This is the row that pins
        # "the session has history".
        shot_id = _plant_shot(sid)
        _plant_prepared_take(
            sid, 1, "inv_take_generated", status="generated",
            linked_shot_id=shot_id, final_prompt="a finalized prompt",
        )
        # First save: the draft. Constants are set here.
        revision = _build_revision(
            "inv_rooms_constant_frozen",
            "inv_room_constant_frozen",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text
        assert first.json()["plan_revision"] == 1
        # Capture the stored state before the refused save.
        plan_row_before = db.one(
            "SELECT plan_revision, plan_json FROM session_plan "
            "WHERE session_id = ?",
            sid,
        )
        prepared_before = db.one(
            "SELECT status, final_prompt, linked_shot_id "
            "FROM prepared_take WHERE session_id = ? "
            "AND take_id = 'inv_take_generated'",
            sid,
        )
        shot_before = db.one(
            "SELECT prompt, status FROM shot WHERE id = ?", shot_id,
        )

        # Second save: change the look. Must be refused with 409
        # and a readable error that names the rule.
        changed_look_plan = {**plan, "look": INV_LOOK + " (a new description)"}
        refused = client.post(
            f"/api/sessions/{sid}/plan",
            json={
                "plan": changed_look_plan,
                "expected_revision": 1,
            },
        )
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert "look" in detail
        assert "initial_wardrobe" in detail
        assert "selected_resources" in detail
        assert "generated" in detail
        # The plan row, the generated prepared_take row, and the
        # linked shot are all byte-for-byte unchanged. No
        # partial mutation, no half-bumped revision.
        plan_row_after = db.one(
            "SELECT plan_revision, plan_json FROM session_plan "
            "WHERE session_id = ?",
            sid,
        )
        assert plan_row_after == plan_row_before, (
            f"a refused save mutated the plan row; "
            f"before={plan_row_before!r}, after={plan_row_after!r}"
        )
        prepared_after = db.one(
            "SELECT status, final_prompt, linked_shot_id "
            "FROM prepared_take WHERE session_id = ? "
            "AND take_id = 'inv_take_generated'",
            sid,
        )
        assert prepared_after == prepared_before
        shot_after = db.one(
            "SELECT prompt, status FROM shot WHERE id = ?", shot_id,
        )
        assert shot_after == shot_before

    def test_initial_wardrobe_change_is_refused_after_a_generated_take(
        self, client, seeded,
    ):
        """The constant-change guard covers ``initial_wardrobe``
        as well. Once a take is generated, the session's starting
        outfit is history: rewriting it would silently pretend a
        finished photograph used a state it did not."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "initial wardrobe change refused",
            "composition_mode": "resource-v1",
        }).json()["id"]
        _plant_prepared_take(
            sid, 1, "inv_take_generated", status="generated",
            linked_shot_id=_plant_shot(sid),
        )
        revision = _build_revision(
            "inv_rooms_initial_frozen",
            "inv_room_initial_frozen",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text

        changed = {
            **plan,
            "initial_wardrobe": (
                "a thin black wool jumper, dark cotton trousers, bare feet"
            ),
        }
        refused = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": changed, "expected_revision": 1},
        )
        assert refused.status_code == 409, refused.text
        assert "initial_wardrobe" in refused.json()["detail"]

    def test_selected_resources_change_is_refused_after_a_generated_take(
        self, client, seeded,
    ):
        """The constant-change guard covers ``selected_resources``.
        A save that swaps the bound resource triple (the scene
        identity) after a take has been generated is refused.
        Adding a NEW resource to the list is also a constant
        change because the list is the operator's statement of
        "these resources bind the session's identity"."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "selected resources change refused",
            "composition_mode": "resource-v1",
        }).json()["id"]
        _plant_prepared_take(
            sid, 1, "inv_take_generated", status="generated",
            linked_shot_id=_plant_shot(sid),
        )
        revision = _build_revision(
            "inv_rooms_selected_frozen",
            "inv_room_selected_frozen",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text

        # A second rooms resource to add to the list. The save
        # adds it; the resulting plan has TWO selected resources
        # and the new one is a constant change.
        second_revision = _build_revision(
            "inv_rooms_selected_frozen_other",
            "inv_room_selected_frozen_other",
            {**INV_ROOM_PAYLOAD, "label": "second invented room"},
        )
        changed = {
            **plan,
            "selected_resources": [
                {
                    "library_key": revision["library_key"],
                    "source_id": revision["source_id"],
                    "content_digest": revision["content_digest"],
                },
                {
                    "library_key": second_revision["library_key"],
                    "source_id": second_revision["source_id"],
                    "content_digest": second_revision["content_digest"],
                },
            ],
        }
        refused = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": changed, "expected_revision": 1},
        )
        assert refused.status_code == 409, refused.text
        assert "selected_resources" in refused.json()["detail"]

    def test_wardrobe_change_remains_allowed_after_a_generated_take(
        self, client, seeded,
    ):
        """Wardrobe changes are NOT constant changes. After a take
        is generated, a save that only edits ``wardrobe_changes``
        is allowed; the ungenerated rows get invalidated; the
        generated row stays. This is the spec's "wardrobe
        changes remain possible through new reviewed take
        revisions" rule."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "wardrobe change allowed after generated",
            "composition_mode": "resource-v1",
        }).json()["id"]
        # First save: the draft. Revision 1.
        revision = _build_revision(
            "inv_rooms_wardrobe_allowed",
            "inv_room_wardrobe_allowed",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text
        assert first.json()["plan_revision"] == 1

        # Plant the generated and ungenerated rows at the
        # current revision (1).
        _plant_prepared_take(
            sid, 1, "inv_take_generated", status="generated",
            linked_shot_id=_plant_shot(sid),
            final_prompt="a finalized prompt",
        )
        ungenerated_id = _plant_prepared_take(
            sid, 1, "inv_take_ungenerated", status="ready",
        )

        # Second save: a wardrobe change on take-002 only. NOT
        # a constant change (the look, initial_wardrobe, and
        # selected_resources are unchanged). The save is
        # accepted; the ungenerated row is invalidated; the
        # generated row is history.
        edited = {**plan, "wardrobe_changes": [
            {
                "take_id": "take-002",
                "scope": "this_take",
                "wardrobe": INV_WARDROBE_JACKET,
            },
        ]}
        second = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": edited, "expected_revision": 1},
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["plan_revision"] == 2

        # The generated row is still ``generated`` and the
        # ungenerated row moved to ``invalidated``.
        rows = _prepared_take_rows(sid)
        by_take = {row["take_id"]: row for row in rows}
        assert by_take["inv_take_generated"]["status"] == "generated"
        assert by_take["inv_take_generated"]["final_prompt"] == (
            "a finalized prompt"
        )
        assert by_take["inv_take_ungenerated"]["status"] == "invalidated"
        # The ungenerated row's id is preserved (the pass is
        # status-only, no delete, no rewrite of the snapshot).
        assert by_take["inv_take_ungenerated"]["id"] == ungenerated_id

    def test_take_reorder_remains_allowed_after_a_generated_take(
        self, client, seeded,
    ):
        """Reordering the takes list is NOT a constant change.
        A save that only changes take order is allowed after a
        generated take; the ungenerated rows are invalidated."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "take reorder allowed after generated",
            "composition_mode": "resource-v1",
        }).json()["id"]
        # First save: the draft. Revision 1.
        revision = _build_revision(
            "inv_rooms_reorder_allowed",
            "inv_room_reorder_allowed",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text
        assert first.json()["plan_revision"] == 1

        # Plant the rows at the current revision (1).
        _plant_prepared_take(
            sid, 1, "inv_take_generated", status="generated",
            linked_shot_id=_plant_shot(sid),
        )
        _plant_prepared_take(
            sid, 1, "inv_take_ungenerated", status="ready",
        )

        # Reorder the takes: take-003 first, then 001, 002.
        reordered = {
            **plan,
            "takes": [
                {"take_id": "take-003", "label": "jacket on"},
                {"take_id": "take-001", "label": "wide"},
                {"take_id": "take-002", "label": "close-up"},
            ],
        }
        second = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": reordered, "expected_revision": 1},
        )
        assert second.status_code == 200, second.text
        assert second.json()["plan_revision"] == 2
        # The ungenerated row was invalidated; the generated row
        # is history.
        by_take = {row["take_id"]: row for row in _prepared_take_rows(sid)}
        assert by_take["inv_take_generated"]["status"] == "generated"
        assert by_take["inv_take_ungenerated"]["status"] == "invalidated"

    def test_a_save_with_no_prepared_takes_does_not_error(
        self, client, seeded,
    ):
        """A session with no prepared_take rows at all is the
        natural state for a draft that has not been prepared yet.
        Saving a plan must succeed and the invalidation pass must
        update zero rows without raising. This is the
        "no-op invalidation" boundary case."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "save with no prepared takes",
            "composition_mode": "resource-v1",
        }).json()["id"]
        revision = _build_revision(
            "inv_rooms_no_prepared",
            "inv_room_no_prepared",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        save = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert save.status_code == 200, save.text
        assert save.json()["plan_revision"] == 1
        n = db.one(
            "SELECT COUNT(*) AS n FROM prepared_take "
            "WHERE session_id = ?",
            sid,
        )["n"]
        assert n == 0, "no prepared_take rows should exist for this session"

    # --- 9.4 Legacy composition remains unchanged after 3.3 -------

    def test_legacy_composition_remains_unchanged_after_3_3(
        self, client, seeded,
    ):
        """A legacy session (no ``composition_mode``) still
        composes its shots through ``_expand_shots`` after the
        3.3 invalidation pass and conflict detector were added.
        The plan routes still refuse a legacy session. The
        new ``conflicts`` field is not surfaced (legacy sessions
        do not reach the resource-v1 path at all)."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "legacy still composes after 3.3",
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
            "shots": [
                {"label": "wide", "prompt": "standing square to the camera"},
            ],
        }).json()["id"]
        # No composition_mode in settings.
        row = db.one("SELECT settings FROM session WHERE id = ?", sid)
        settings = json.loads(row["settings"])
        assert "composition_mode" not in settings

        # Plan routes are still refused on a legacy session.
        plan_resp = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": {"version": "resource-v1", "look": "x",
                            "initial_wardrobe": "y", "takes": [
                                {"take_id": "t1"}],
                            "selected_resources": [],
                            "wardrobe_changes": []},
                  "expected_revision": 0},
        )
        assert plan_resp.status_code == 400, plan_resp.text
        # The session's shot is composed the same way the
        # baseline test pins: trigger + base + look + wardrobe +
        # take, joined with full stops.
        full = client.get(f"/api/sessions/{sid}").json()
        assert full["look"] == INV_LOOK
        assert full["wardrobe"] == INV_WARDROBE
        assert len(full["shots"]) == 1
        assert "INV_LOOK" in full["shots"][0]["prompt"] or full["shots"][0][
            "prompt"
        ].startswith("4da woman. photo, 35mm. " + INV_LOOK), (
            f"legacy composition drifted after 3.3; got "
            f"{full['shots'][0]['prompt']!r}"
        )

    # --- 9.5 Constant-frozen guard at the service layer -----------

    def test_save_draft_refuses_a_constant_change_with_a_readable_error(
        self, client, seeded,
    ):
        """Service-level guard. The plan-route maps the error to a
        409, but the service-layer test pins the exception class
        and the message so a future refactor of the HTTP layer
        cannot silently change the contract."""
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "service-level constant guard",
            "composition_mode": "resource-v1",
        }).json()["id"]
        _plant_prepared_take(
            sid, 1, "inv_take_generated", status="generated",
            linked_shot_id=_plant_shot(sid),
        )
        revision = _build_revision(
            "inv_rooms_service_constant",
            "inv_room_service_constant",
            INV_ROOM_PAYLOAD,
        )
        plan = _build_plan(revision)
        first = session_plan.save_draft(sid, plan, expected_revision=0)
        assert first["plan_revision"] == 1

        # Direct call with a different look raises the named
        # error class.
        with pytest.raises(
            session_plan.PlanConstantsFrozenAfterGenerated,
        ) as exc:
            session_plan.save_draft(
                sid,
                {**plan, "look": "a different look that breaks the rule"},
                expected_revision=1,
            )
        message = str(exc.value)
        assert "look" in message
        assert "initial_wardrobe" in message
        assert "selected_resources" in message
        # The plan is unchanged: the refusal is before the write.
        draft = session_plan.get_draft(sid)
        assert draft["plan_revision"] == 1
        assert draft["plan"]["look"] == INV_LOOK


# =====================================================================
# 10. Incremental prepared-take persistence and recovery (task 3.4).
# =====================================================================


def _task34_plan(take_count: int = 3) -> dict:
    """Return an invented resource-v1 plan for preparation tests."""
    return {
        "version": "resource-v1",
        "look": "A quiet invented studio with soft side light.",
        "initial_wardrobe": "a plain charcoal shirt and dark trousers",
        "takes": [
            {
                "take_id": f"resume-{index:02d}",
                "label": f"invented take {index:02d}",
            }
            for index in range(1, take_count + 1)
        ],
        "selected_resources": [],
        "wardrobe_changes": [],
    }


def _task34_snapshot(index: int) -> dict:
    """Return the complete persisted snapshot for one invented take."""
    return {
        "final_prompt": f"invented finalized prompt {index:02d}",
        "effective_state": {
            "take_index": index,
            "wardrobe": ["charcoal shirt", "dark trousers"],
            "settings": {"steps": 20 + index, "cfg": 1.0 + index / 10},
        },
        "mapping_version": "mapping-test-v1",
        "compiler_version": "compiler-test-v1",
        "provenance": {
            "source": "invented preparation test",
            "take_index": index,
        },
    }


def _task34_resource_session(client, seeded, name: str) -> int:
    response = client.post("/api/sessions", json={
        "model_id": seeded["model_id"],
        "name": name,
        "composition_mode": "resource-v1",
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _task34_raw_prepared(session_id: int, revision: int, take_id: str) -> dict | None:
    return db.one(
        "SELECT id, session_id, plan_revision, take_id, final_prompt, "
        "effective_state, mapping_version, compiler_version, provenance, "
        "status, linked_shot_id, created_at, updated_at "
        "FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = ?",
        session_id, revision, take_id,
    )


class TestPreparedTakePersistenceAndRecovery:
    def test_begin_is_durable_pending_and_complete_round_trips_full_snapshot(
        self, client, seeded,
    ):
        sid = _task34_resource_session(client, seeded, "incremental preparation")
        plan = _task34_plan()
        saved = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert saved.status_code == 200, saved.text

        begun = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "resume-01"},
        )
        assert begun.status_code == 200, begun.text
        assert begun.json()["status"] == "pending"
        pending = _task34_raw_prepared(sid, 1, "resume-01")
        assert pending is not None
        assert pending["status"] == "pending"
        assert pending["final_prompt"] == ""
        assert pending["effective_state"] == "{}"
        assert pending["mapping_version"] == ""
        assert pending["compiler_version"] == ""
        assert pending["provenance"] == "{}"

        snapshot = _task34_snapshot(1)
        completed = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={"plan_revision": 1, "take_id": "resume-01", **snapshot},
        )
        assert completed.status_code == 200, completed.text
        body = completed.json()
        assert body["status"] == "ready"
        for key, value in snapshot.items():
            assert body[key] == value

        raw = _task34_raw_prepared(sid, 1, "resume-01")
        assert raw is not None
        assert raw["status"] == "ready"
        assert raw["final_prompt"] == snapshot["final_prompt"]
        assert json.loads(raw["effective_state"]) == snapshot["effective_state"]
        assert raw["mapping_version"] == snapshot["mapping_version"]
        assert raw["compiler_version"] == snapshot["compiler_version"]
        assert json.loads(raw["provenance"]) == snapshot["provenance"]

        reopened = client.get(f"/api/sessions/{sid}/plan")
        assert reopened.status_code == 200, reopened.text
        preparation = reopened.json()["preparation"]
        assert [row["take_id"] for row in preparation["completed"]] == ["resume-01"]
        assert [row["take_id"] for row in preparation["incomplete"]] == [
            "resume-02", "resume-03",
        ]
        assert preparation["completed"][0]["effective_state"] == snapshot[
            "effective_state"
        ]
        assert preparation["completed"][0]["provenance"] == snapshot["provenance"]

    def test_blank_snapshot_strings_are_refused_without_finalizing_pending(
        self, client, seeded,
    ):
        sid = _task34_resource_session(client, seeded, "blank preparation fields")
        assert client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": _task34_plan(7), "expected_revision": 0},
        ).status_code == 200

        invalid_values = [
            ("final_prompt", ""),
            ("final_prompt", "   "),
            ("mapping_version", ""),
            ("mapping_version", "   "),
            ("compiler_version", ""),
            ("compiler_version", "   "),
        ]
        for index, (field, value) in enumerate(invalid_values, start=1):
            take_id = f"resume-{index:02d}"
            assert client.post(
                f"/api/sessions/{sid}/plan/preparations/begin",
                json={"plan_revision": 1, "take_id": take_id},
            ).status_code == 200
            before = _task34_raw_prepared(sid, 1, take_id)
            assert before is not None
            snapshot = {**_task34_snapshot(index), field: value}

            with pytest.raises(
                session_plan.PlanValidationError,
                match=rf"{field} must be a non-empty string",
            ):
                session_plan.complete_preparation(sid, 1, take_id, **snapshot)
            assert _task34_raw_prepared(sid, 1, take_id) == before

            response = client.post(
                f"/api/sessions/{sid}/plan/preparations/complete",
                json={"plan_revision": 1, "take_id": take_id, **snapshot},
            )
            assert response.status_code == 422, response.text
            assert field in response.json()["detail"]
            assert _task34_raw_prepared(sid, 1, take_id) == before

        recovery = session_plan.recover_preparation(sid)
        assert recovery["completed"] == []
        assert recovery["incomplete"] == [
            {"take_id": f"resume-{index:02d}", "status": "pending"}
            for index in range(1, 7)
        ] + [{"take_id": "resume-07", "status": "missing"}]

        valid = {
            **_task34_snapshot(7),
            "final_prompt": "  invented finalized prompt 07  ",
            "mapping_version": " mapping-test-v1 ",
            "compiler_version": " compiler-test-v1 ",
        }
        assert client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "resume-07"},
        ).status_code == 200
        completed = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={"plan_revision": 1, "take_id": "resume-07", **valid},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "ready"
        for field in ("final_prompt", "mapping_version", "compiler_version"):
            assert completed.json()[field] == valid[field]

    def test_invalid_preparation_targets_refuse_without_partial_rows(
        self, client, seeded,
    ):
        sid = _task34_resource_session(client, seeded, "invalid preparation targets")
        plan = _task34_plan()
        saved = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert saved.status_code == 200, saved.text

        missing = client.post(
            "/api/sessions/999999/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "resume-01"},
        )
        assert missing.status_code == 404, missing.text

        stale = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 2, "take_id": "resume-01"},
        )
        assert stale.status_code == 409, stale.text

        unknown = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "resume-missing"},
        )
        assert unknown.status_code == 422, unknown.text

        legacy = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "legacy preparation refusal",
        })
        assert legacy.status_code == 200, legacy.text
        legacy_sid = legacy.json()["id"]
        legacy_begin = client.post(
            f"/api/sessions/{legacy_sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "resume-01"},
        )
        assert legacy_begin.status_code == 400, legacy_begin.text
        legacy_get = client.get(f"/api/sessions/{legacy_sid}/plan")
        assert legacy_get.status_code == 400, legacy_get.text

        assert db.one(
            "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id IN (?, ?)",
            sid, legacy_sid,
        )["n"] == 0

    def test_completed_snapshot_retry_is_idempotent_and_different_retry_is_refused(
        self, client, seeded,
    ):
        sid = _task34_resource_session(client, seeded, "immutable prepared snapshot")
        plan = _task34_plan()
        assert client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        ).status_code == 200
        assert client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "resume-01"},
        ).status_code == 200

        snapshot = _task34_snapshot(1)
        first = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={"plan_revision": 1, "take_id": "resume-01", **snapshot},
        )
        assert first.status_code == 200, first.text
        before = _task34_raw_prepared(sid, 1, "resume-01")
        assert before is not None

        identical = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={"plan_revision": 1, "take_id": "resume-01", **snapshot},
        )
        assert identical.status_code == 200, identical.text
        assert identical.json()["id"] == before["id"]
        assert db.one(
            "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ? "
            "AND plan_revision = 1 AND take_id = 'resume-01'",
            sid,
        )["n"] == 1

        different = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={
                "plan_revision": 1,
                "take_id": "resume-01",
                **{**snapshot, "final_prompt": "a different invented prompt"},
            },
        )
        assert different.status_code == 409, different.text
        assert "immutable ready history" in different.json()["detail"]
        assert _task34_raw_prepared(sid, 1, "resume-01") == before

    def test_three_of_twelve_survive_reopen_and_only_nine_are_resumable(
        self, isolated_db,
    ):
        now = db.now()
        model_id = db.run(
            "INSERT INTO model (name, created_at) VALUES (?, ?)",
            "invented reopen model", now,
        )
        sid = db.run(
            "INSERT INTO session (model_id, name, settings, created_at) "
            "VALUES (?, ?, ?, ?)",
            model_id, "invented reopen session",
            json.dumps({"composition_mode": "resource-v1"}), now,
        )
        plan = _task34_plan(12)
        saved = session_plan.save_draft(sid, plan, expected_revision=0)
        assert saved["plan_revision"] == 1

        for index in range(1, 4):
            take_id = f"resume-{index:02d}"
            session_plan.begin_preparation(sid, 1, take_id)
            session_plan.complete_preparation(
                sid, 1, take_id, **_task34_snapshot(index),
            )
        session_plan.begin_preparation(sid, 1, "resume-04")

        raw_before = list(db.q(
            "SELECT take_id, final_prompt, effective_state, mapping_version, "
            "compiler_version, provenance, status FROM prepared_take "
            "WHERE session_id = ? AND status = 'ready' ORDER BY take_id",
            sid,
        ))
        assert len(raw_before) == 3

        _close_silently()
        _open(isolated_db)

        raw_after = list(db.q(
            "SELECT take_id, final_prompt, effective_state, mapping_version, "
            "compiler_version, provenance, status FROM prepared_take "
            "WHERE session_id = ? AND status = 'ready' ORDER BY take_id",
            sid,
        ))
        assert raw_after == raw_before

        recovery = session_plan.recover_preparation(sid)
        assert recovery["plan_revision"] == 1
        assert [row["take_id"] for row in recovery["completed"]] == [
            "resume-01", "resume-02", "resume-03",
        ]
        assert len(recovery["incomplete"]) == 9
        assert recovery["incomplete"][0] == {
            "take_id": "resume-04", "status": "pending",
        }
        assert recovery["incomplete"][1:] == [
            {"take_id": f"resume-{index:02d}", "status": "missing"}
            for index in range(5, 13)
        ]
        assert recovery["history"] == []

    def test_finalize_persistence_failure_is_visible_and_rolls_back(
        self, client, seeded, monkeypatch,
    ):
        sid = _task34_resource_session(client, seeded, "visible persistence failure")
        plan = _task34_plan()
        assert client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        ).status_code == 200

        for take_id in ("resume-01", "resume-02"):
            assert client.post(
                f"/api/sessions/{sid}/plan/preparations/begin",
                json={"plan_revision": 1, "take_id": take_id},
            ).status_code == 200
        assert client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={"plan_revision": 1, "take_id": "resume-01", **_task34_snapshot(1)},
        ).status_code == 200
        completed_before = _task34_raw_prepared(sid, 1, "resume-01")
        assert completed_before is not None

        original_run = db.run

        def fail_finalize(sql: str, *args):
            if sql.startswith("UPDATE prepared_take SET final_prompt = ?"):
                raise sqlite3.OperationalError("invented prepared take write failure")
            return original_run(sql, *args)

        monkeypatch.setattr(db, "run", fail_finalize)
        failed = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={"plan_revision": 1, "take_id": "resume-02", **_task34_snapshot(2)},
        )
        assert failed.status_code == 500, failed.text
        assert "invented prepared take write failure" in failed.json()["detail"]

        interrupted = _task34_raw_prepared(sid, 1, "resume-02")
        assert interrupted is not None
        assert interrupted["status"] == "pending"
        assert interrupted["final_prompt"] == ""
        assert interrupted["effective_state"] == "{}"
        assert interrupted["mapping_version"] == ""
        assert interrupted["compiler_version"] == ""
        assert interrupted["provenance"] == "{}"
        assert _task34_raw_prepared(sid, 1, "resume-01") == completed_before

    def test_new_revision_keeps_generated_and_current_revision_rows_as_history_requires(
        self, client, seeded,
    ):
        sid = _task34_resource_session(client, seeded, "revision recovery history")
        plan = _task34_plan()
        first = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": plan, "expected_revision": 0},
        )
        assert first.status_code == 200, first.text

        ready_id = _plant_prepared_take(sid, 1, "resume-01", status="ready")
        pending_id = _plant_prepared_take(sid, 1, "resume-02", status="pending")
        generated_id = _plant_prepared_take(
            sid, 1, "resume-03", status="generated",
            linked_shot_id=_plant_shot(sid, "invented generated shot"),
        )
        future_id = _plant_prepared_take(sid, 2, "resume-01", status="pending")

        edited = {
            **plan,
            "takes": [plan["takes"][1], plan["takes"][0], plan["takes"][2]],
        }
        second = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": edited, "expected_revision": 1},
        )
        assert second.status_code == 200, second.text
        assert second.json()["plan_revision"] == 2

        rows = {row["id"]: row for row in db.q(
            "SELECT id, plan_revision, take_id, status, final_prompt, linked_shot_id "
            "FROM prepared_take WHERE session_id = ? ORDER BY id",
            sid,
        )}
        assert rows[ready_id]["status"] == "invalidated"
        assert rows[pending_id]["status"] == "invalidated"
        assert rows[generated_id]["status"] == "generated"
        assert rows[future_id]["status"] == "pending"

        recovery = client.get(f"/api/sessions/{sid}/plan")
        assert recovery.status_code == 200, recovery.text
        preparation = recovery.json()["preparation"]
        assert preparation["completed"] == []
        assert preparation["incomplete"] == [
            {"take_id": "resume-02", "status": "missing"},
            {"take_id": "resume-01", "status": "pending"},
            {"take_id": "resume-03", "status": "missing"},
        ]
        history = {row["id"]: row for row in preparation["history"]}
        assert history[ready_id]["status"] == "invalidated"
        assert history[pending_id]["status"] == "invalidated"
        assert history[generated_id]["status"] == "generated"

        current_generated = _plant_prepared_take(
            sid, 2, "resume-03", status="generated",
            linked_shot_id=_plant_shot(sid, "invented current generated shot"),
        )
        resumed = session_plan.recover_preparation(sid)
        assert [row["id"] for row in resumed["completed"]] == [current_generated]
        assert [row["take_id"] for row in resumed["incomplete"]] == [
            "resume-02", "resume-01",
        ]
