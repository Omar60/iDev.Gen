from __future__ import annotations

import db
from backend import saved_looks
from test_task13_appearance_ownership import (
    _profile,
    _publish,
    _saved_bracelet_look,
)


def test_cross_version_copy_cannot_relabel_a_worn_fact_as_a_different_state(client):
    source = _saved_bracelet_look()
    garment = source["outfit"]["garments"][0]
    published = _publish(source, _profile(source, bracelet_owner="garment", revision=1))
    target = saved_looks.create_version(source["key"], source["version"], {
        "name": "Changed aside wording",
        "appearance": source["appearance"],
        "garments": [{
            "key": garment["key"],
            "wording": garment["wording"],
            # Adversarially, target aside now matches the source worn wording.
            "aside": garment["wording"],
        }],
    })
    before = db.q("SELECT * FROM saved_look_automation_profile ORDER BY rowid")

    preview = client.post("/api/looks/automation-profiles/copy-preview", json={
        "target_look_key": target["key"],
        "target_version": target["version"],
        "target_content_digest": target["content_digest"],
        "expected_profile_revision": 0,
        "source_look_key": source["key"],
        "source_version": source["version"],
        "source_content_digest": source["content_digest"],
        "source_profile_revision": published["profile_revision"],
        "source_profile_digest": published["profile_digest"],
        "selections": [{
            "garment_key": garment["key"], "source_state": "worn", "state": "aside",
        }],
    })

    assert preview.status_code == 200, preview.text
    assert preview.json()["source"]["profile_digest"] == published["profile_digest"]
    assert preview.json()["garment_coverage"] == {}
    assert preview.json()["skipped"] == [{
        "garment_key": garment["key"],
        "source_state": "worn",
        "state": "aside",
        "code": "state_changed",
    }]
    assert db.q("SELECT * FROM saved_look_automation_profile ORDER BY rowid") == before


def test_copy_preview_refuses_a_stale_source_profile_digest_without_writes(client):
    source = _saved_bracelet_look()
    garment = source["outfit"]["garments"][0]
    published = _publish(source, _profile(source, bracelet_owner="garment", revision=1))
    before = db.q("SELECT * FROM saved_look_automation_profile ORDER BY rowid")

    stale = client.post("/api/looks/automation-profiles/copy-preview", json={
        "target_look_key": source["key"],
        "target_version": source["version"],
        "target_content_digest": source["content_digest"],
        "expected_profile_revision": 1,
        "source_look_key": source["key"],
        "source_version": source["version"],
        "source_content_digest": source["content_digest"],
        "source_profile_revision": published["profile_revision"],
        "source_profile_digest": "0" * 64,
        "selections": [{"garment_key": garment["key"], "state": "worn"}],
    })

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "automation_profile_stale"
    assert db.q("SELECT * FROM saved_look_automation_profile ORDER BY rowid") == before
