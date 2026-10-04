from __future__ import annotations

import pytest

import db


@pytest.fixture(autouse=True)
def _clear_legacy_import_receipts(client):
    db.run("DELETE FROM saved_look_legacy_import_receipt")
    yield
    db.run("DELETE FROM saved_look_legacy_import_receipt")


def _counts() -> dict[str, int]:
    return {
        table: db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
        for table in (
            "garment", "outfit", "saved_look_version", "saved_look_import_receipt",
            "saved_look_legacy_import_receipt",
        )
    }


def _preview(client, document: dict):
    return client.post("/api/looks/import/preview", json=document)


def _commit(client, document: dict, preview: dict, choice: str = "import"):
    return client.post("/api/looks/import/commit", json={
        "envelope": document,
        "preview_token": preview["preview_token"],
        "review_digest": preview["review_digest"],
        "choice": choice,
    })


def _insert_garment(key: str, wording: str, aside: str = "") -> None:
    db.run(
        "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
        key, wording, aside, db.now(),
    )


def _insert_outfit(key: str, label: str, garments: str) -> None:
    db.run(
        "INSERT INTO outfit (key, label, garments, created_at) VALUES (?, ?, ?, ?)",
        key, label, garments, db.now(),
    )


def test_save_copy_replays_only_the_reviewed_choice_and_fresh_receipt_uses_import(client):
    _insert_garment("stable-shirt", "the original shirt")
    _insert_outfit("stable-outfit", "Original", "stable-shirt")
    document = {
        "garments": [{"key": "stable-shirt", "wording": "a changed shirt"}],
        "outfits": [{"key": "stable-outfit", "label": "Changed", "garments": ["stable-shirt"]}],
    }
    plan = _preview(client, document).json()
    assert plan["choices"] == ["save_copy"]
    before = _counts()

    rejected = _commit(client, document, plan, "import")
    assert rejected.status_code == 409
    assert _counts() == before

    saved = _commit(client, document, plan, "save_copy")
    assert saved.status_code == 200, saved.text
    after_save = _counts()
    assert after_save == {
        **before,
        "garment": before["garment"] + 1,
        "outfit": before["outfit"] + 1,
        "saved_look_legacy_import_receipt": before["saved_look_legacy_import_receipt"] + 1,
    }

    exact_retry = _commit(client, document, plan, "save_copy")
    assert exact_retry.status_code == 200, exact_retry.text
    assert exact_retry.json()["no_op"] is True
    assert _counts() == after_save

    wrong_original_retry = _commit(client, document, plan, "import")
    assert wrong_original_retry.status_code == 409
    assert _counts() == after_save

    replay_plan_response = _preview(client, document)
    assert replay_plan_response.status_code == 200, replay_plan_response.text
    replay_plan = replay_plan_response.json()
    assert replay_plan["mode"] == "receipt_replay"
    rejected_replay_choice = _commit(client, document, replay_plan, "save_copy")
    assert rejected_replay_choice.status_code == 409
    assert _counts() == after_save

    replay = _commit(client, document, replay_plan, "import")
    assert replay.status_code == 200, replay.text
    assert replay.json()["no_op"] is True
    assert _counts() == after_save


def test_catalogue_only_reference_is_revalidated_at_commit_and_receipt_replay(client):
    _insert_garment("catalogue-shirt", "a soft linen shirt", "folded sleeves")
    document = {"outfits": [{"key": "catalogue-outfit", "garments": ["catalogue-shirt"]}]}
    plan_response = _preview(client, document)
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    before = _counts()

    db.run("UPDATE garment SET wording = ? WHERE key = ?", "a changed shirt", "catalogue-shirt")
    stale = _commit(client, document, plan)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "look_import_preview_stale"
    assert _counts() == before

    db.run("UPDATE garment SET wording = ? WHERE key = ?", "a soft linen shirt", "catalogue-shirt")
    fresh_plan_response = _preview(client, document)
    assert fresh_plan_response.status_code == 200, fresh_plan_response.text
    committed = _commit(client, document, fresh_plan_response.json())
    assert committed.status_code == 200, committed.text
    after_commit = _counts()
    assert after_commit["outfit"] == before["outfit"] + 1
    assert after_commit["saved_look_version"] == before["saved_look_version"]

    db.run("UPDATE garment SET aside = ? WHERE key = ?", "tied at the waist", "catalogue-shirt")
    corrupted_replay = _preview(client, document)
    assert corrupted_replay.status_code == 500
    assert corrupted_replay.json()["detail"]["code"] == "look_import_receipt_invalid"
    assert _counts() == after_commit


def test_outfit_label_alone_conflicts_without_rewording_or_reusing_key(client):
    _insert_garment("cotton-shirt", "a cotton shirt")
    _insert_outfit("cotton-outfit", "Old label", "cotton-shirt")
    document = {
        "garments": [{"key": "cotton-shirt", "wording": "a cotton shirt"}],
        "outfits": [{"key": "cotton-outfit", "label": "New label", "garments": ["cotton-shirt"]}],
    }
    plan_response = _preview(client, document)
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["choices"] == ["save_copy"]
    assert plan["mapping"]["garments"] == {"cotton-shirt": "cotton-shirt"}
    assert plan["mapping"]["outfits"]["cotton-outfit"] != "cotton-outfit"
    before = _counts()

    result = _commit(client, document, plan, "save_copy")
    assert result.status_code == 200, result.text
    assert db.one("SELECT wording FROM garment WHERE key = ?", "cotton-shirt")["wording"] == "a cotton shirt"
    assert db.one("SELECT label FROM outfit WHERE key = ?", "cotton-outfit")["label"] == "Old label"
    copied_key = result.json()["mapping"]["outfits"]["cotton-outfit"]
    assert db.one("SELECT label, garments FROM outfit WHERE key = ?", copied_key) == {
        "label": "New label", "garments": "cotton-shirt",
    }
    assert _counts() == {
        **before,
        "outfit": before["outfit"] + 1,
        "saved_look_legacy_import_receipt": before["saved_look_legacy_import_receipt"] + 1,
    }


def test_already_equal_import_stays_a_no_op_without_creating_a_receipt(client):
    _insert_garment("exact-shirt", "a clean cotton shirt")
    _insert_outfit("exact-outfit", "Clean layers", "exact-shirt")
    document = {
        "garments": [{"key": "exact-shirt", "wording": "a clean cotton shirt"}],
        "outfits": [{"key": "exact-outfit", "label": "Clean layers", "garments": ["exact-shirt"]}],
    }
    before = _counts()

    for _ in range(2):
        plan_response = _preview(client, document)
        assert plan_response.status_code == 200, plan_response.text
        plan = plan_response.json()
        assert plan["mode"] == "already_equal"
        committed = _commit(client, document, plan)
        assert committed.status_code == 200, committed.text
        assert committed.json()["no_op"] is True
        assert _counts() == before


def test_historical_snapshot_content_does_not_reword_a_changed_catalogue_key(client):
    _insert_garment("historic-shirt", "the archived shirt")
    _insert_outfit("historic-outfit", "Archived outfit", "historic-shirt")
    created = client.post("/api/looks", json={
        "name": "Archived look", "appearance": "short curls", "outfit_key": "historic-outfit",
    })
    assert created.status_code == 200, created.text
    original_snapshot = created.json()

    db.run("UPDATE garment SET wording = ? WHERE key = ?", "the current shirt", "historic-shirt")
    document = {
        "garments": [{"key": "historic-shirt", "wording": "the archived shirt"}],
        "outfits": [{"key": "new-archived-outfit", "garments": ["historic-shirt"]}],
    }
    plan_response = _preview(client, document)
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["choices"] == ["save_copy"]
    assert plan["mapping"]["garments"]["historic-shirt"] != "historic-shirt"
    before = _counts()

    saved_copy = _commit(client, document, plan, "save_copy")
    assert saved_copy.status_code == 200, saved_copy.text
    assert db.one("SELECT wording FROM garment WHERE key = ?", "historic-shirt")["wording"] == "the current shirt"
    stored = client.get(f"/api/looks/{original_snapshot['key']}/versions/1")
    assert stored.status_code == 200, stored.text
    assert stored.json()["outfit"]["garments"][0]["wording"] == "the archived shirt"
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_version WHERE look_key = ?",
        original_snapshot["key"],
    )["count"] == 1
    assert _counts() == {
        **before,
        "garment": before["garment"] + 1,
        "outfit": before["outfit"] + 1,
        "saved_look_legacy_import_receipt": before["saved_look_legacy_import_receipt"] + 1,
    }


def test_late_invalid_legacy_collection_is_refused_before_any_rows_or_receipt(client):
    document = {
        "garments": [
            {"key": "valid-first", "wording": "a valid first garment"},
            {"key": "invalid-last", "wording": 17},
        ],
        "outfits": [{"key": "uses-first", "garments": ["valid-first"]}],
    }
    before = _counts()
    response = _preview(client, document)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_look"
    assert _counts() == before


def test_same_digest_receipt_detects_local_destination_edits_without_reallocation(client):
    document = {
        "garments": [{"key": "replay-shirt", "wording": "a blue cotton shirt"}],
        "outfits": [{"key": "replay-outfit", "garments": ["replay-shirt"]}],
    }
    plan = _preview(client, document).json()
    committed = _commit(client, document, plan)
    assert committed.status_code == 200, committed.text
    after_commit = _counts()

    db.run("UPDATE outfit SET label = ? WHERE key = ?", "silently changed", "replay-outfit")
    replay = _preview(client, document)
    assert replay.status_code == 500
    assert replay.json()["detail"]["code"] == "look_import_receipt_invalid"
    assert _counts() == after_commit


def test_receipt_insert_failure_rolls_back_early_legacy_catalogue_inserts(client):
    document = {
        "garments": [{"key": "atomic-shirt", "wording": "a soft cotton shirt"}],
        "outfits": [{"key": "atomic-outfit", "garments": ["atomic-shirt"]}],
    }
    plan_response = _preview(client, document)
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    before = _counts()
    db.conn().execute(
        """CREATE TRIGGER reject_legacy_receipt_for_test
           BEFORE INSERT ON saved_look_legacy_import_receipt
           BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END"""
    )
    db.conn().commit()
    try:
        failed = _commit(client, document, plan)
    finally:
        db.conn().execute("DROP TRIGGER IF EXISTS reject_legacy_receipt_for_test")
        db.conn().commit()

    assert failed.status_code == 409
    assert failed.json()["detail"]["code"] == "look_import_preview_stale"
    assert "injected receipt failure" not in failed.text
    assert _counts() == before
    assert db.one("SELECT COUNT(*) AS count FROM garment WHERE key='atomic-shirt'")["count"] == 0
    assert db.one("SELECT COUNT(*) AS count FROM outfit WHERE key='atomic-outfit'")["count"] == 0


def test_preview_tokens_cannot_cross_portable_and_legacy_import_protocols(client):
    legacy_document = {
        "garments": [{"key": "protocol-shirt", "wording": "a cotton shirt"}],
    }
    portable_document = {
        "schema_version": 1,
        "look": {
            "key": "protocol-look", "version": 1, "name": "Protocol look",
            "appearance": "", "outfit": None,
        },
        "garments": [],
        "provenance": None,
    }
    legacy_plan_response = _preview(client, legacy_document)
    portable_plan_response = _preview(client, portable_document)
    assert legacy_plan_response.status_code == 200, legacy_plan_response.text
    assert portable_plan_response.status_code == 200, portable_plan_response.text
    legacy_plan = legacy_plan_response.json()
    portable_plan = portable_plan_response.json()
    before = _counts()

    legacy_token_for_portable = client.post("/api/looks/import/commit", json={
        "envelope": portable_document,
        "preview_token": legacy_plan["preview_token"],
        "review_digest": legacy_plan["review_digest"],
        "choice": "import",
    })
    assert legacy_token_for_portable.status_code == 409
    assert legacy_token_for_portable.json()["detail"]["code"] == "look_import_preview_invalid"
    assert _counts() == before

    portable_token_for_legacy = _commit(client, legacy_document, portable_plan)
    assert portable_token_for_legacy.status_code == 409
    assert portable_token_for_legacy.json()["detail"]["code"] == "look_import_preview_invalid"
    assert _counts() == before


def test_comma_containing_garment_key_is_remapped_before_outfit_storage(client):
    document = {
        "garments": [{"key": "shirt,inner", "wording": "a layered cotton shirt"}],
        "outfits": [{"key": "comma-outfit", "garments": ["shirt,inner"]}],
    }
    plan_response = _preview(client, document)
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    local_key = plan["mapping"]["garments"]["shirt,inner"]
    assert local_key != "shirt,inner"
    assert "," not in local_key
    assert plan["choices"] == ["save_copy"]

    committed = _commit(client, document, plan, "save_copy")
    assert committed.status_code == 200, committed.text
    assert db.one("SELECT COUNT(*) AS count FROM garment WHERE key='shirt,inner'")["count"] == 0
    assert db.one("SELECT garments FROM outfit WHERE key='comma-outfit'")["garments"] == local_key
    assert committed.json()["mapping"]["garments"]["shirt,inner"] == local_key

    before_replay = _counts()
    replay_plan = _preview(client, document)
    assert replay_plan.status_code == 200, replay_plan.text
    replay = _commit(client, document, replay_plan.json())
    assert replay.status_code == 200, replay.text
    assert replay.json()["no_op"] is True
    assert replay.json()["mapping"]["garments"]["shirt,inner"] == local_key
    assert _counts() == before_replay
