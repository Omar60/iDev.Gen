"""Independent serialization checks for Full Auto creation consent."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta, timezone
import json
import threading
import uuid

import pytest

import db
import main
import resource_store
from backend import guided_sessions
import test_task31_acceptance as task31


CREATE_PATH = "/api/sessions/guided"
PREFLIGHT_PATH = "/api/sessions/guided/full-auto/preflight"


def _prepare_authorized_request(client, seeded, monkeypatch):
    task31._forbid_external_work(monkeypatch)
    task31._install_t2i_workflow(client, seeded)
    saved, published, base = task31._full_auto_fixture(seeded)
    consent = task31._preflight(client, base)
    payload = deepcopy(base)
    payload["request_id"] = str(uuid.uuid4())
    payload["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    return saved, published, base, consent, payload


def _allocation_counts() -> dict[str, int]:
    return task31._allocation_counts()


def _all_table_snapshot() -> dict[str, list[str]]:
    tables = [row["name"] for row in db.q(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )]
    snapshot = {}
    for table in tables:
        quoted_table = table.replace('"', '""')
        snapshot[table] = sorted(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=repr)
            for row in db.q(f'SELECT * FROM "{quoted_table}"')
        )
    return snapshot


def _configure_test_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "CONFIG", dict(main.CONFIG))
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://provider-a.example/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-model-a")
    monkeypatch.setattr(main, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(main, "COMFY_OUTPUT", main.COMFY_OUTPUT)
    monkeypatch.setattr(main, "LORA_DIR", main.LORA_DIR)
    monkeypatch.setattr(main.comfy, "url", main.comfy.url)
    monkeypatch.setattr(main.runner, "comfy_output_dir", main.runner.comfy_output_dir)


def test_preflight_leaves_every_database_table_unchanged_and_calls_no_external_work(
    client, seeded, monkeypatch,
):
    task31._forbid_external_work(monkeypatch)
    task31._install_t2i_workflow(client, seeded)
    _saved, _published, base = task31._full_auto_fixture(seeded)
    before = _all_table_snapshot()

    response = client.post(PREFLIGHT_PATH, json=base)

    assert response.status_code == 200, response.text
    assert _all_table_snapshot() == before


def test_quote_is_valid_through_the_inclusive_300_second_boundary(
    client, seeded, monkeypatch,
):
    task31._forbid_external_work(monkeypatch)
    task31._install_t2i_workflow(client, seeded)
    real_datetime = guided_sessions.datetime
    instant = real_datetime.now(timezone.utc).replace(microsecond=0)

    class FrozenDateTime(real_datetime):
        current = instant

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(guided_sessions, "datetime", FrozenDateTime)
    _saved, _published, base = task31._full_auto_fixture(seeded)
    consent = task31._preflight(client, base)
    payload = deepcopy(base)
    payload["request_id"] = str(uuid.uuid4())
    payload["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    FrozenDateTime.current = instant + timedelta(seconds=300)

    response = client.post(CREATE_PATH, json=payload)

    assert response.status_code == 201, response.text
    receipt = db.one(
        "SELECT scope_issued_at, authorization_expires_at "
        "FROM full_auto_run_receipt WHERE run_id = ?",
        response.json()["full_auto_run_id"],
    )
    assert receipt["scope_issued_at"] == consent["scope_issued_at"]
    assert receipt["authorization_expires_at"] == consent["summary"]["authorization_expires_at"]


def _replace_provider_config(adoption_path: str) -> dict:
    replacement = dict(main.CONFIG)
    replacement.update({
        "llm_url": "https://provider-b.example/v1",
        "llm_model": "invented-model-b",
        "comfy_output_dir": "",
        "lora_dir": "",
    })
    if adoption_path == "save_config":
        main.save_config(main.ConfigIn(**replacement))
    else:
        assert adoption_path == "_adopt_config"
        main._adopt_config(replacement)
    return replacement


def _hold_creation_before_transaction(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    creator_thread = None
    original_transaction = db.transaction

    @contextmanager
    def gated_transaction():
        nonlocal creator_thread
        current_thread = threading.get_ident()
        if creator_thread is None:
            creator_thread = current_thread
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError("creation transaction gate was not released")
        with original_transaction():
            yield

    monkeypatch.setattr(db, "transaction", gated_transaction)
    return entered, release


@pytest.mark.parametrize("adoption_path", ["save_config", "_adopt_config"])
def test_creation_rejects_provider_config_replaced_before_serialized_allocation(
    client, seeded, monkeypatch, tmp_path, adoption_path,
):
    _configure_test_provider(monkeypatch, tmp_path)
    _saved, _published, _base, _consent, payload = _prepare_authorized_request(
        client, seeded, monkeypatch,
    )
    entered, release = _hold_creation_before_transaction(monkeypatch)

    def post_creation():
        return client.post(CREATE_PATH, json=payload)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post_creation)
        try:
            assert entered.wait(timeout=10), "creation did not reach its transaction boundary"
            old_config = main.CONFIG
            replacement = _replace_provider_config(adoption_path)
            assert main.CONFIG is not old_config
            assert main.CONFIG["llm_url"] == replacement["llm_url"]
            before = _all_table_snapshot()
        finally:
            release.set()
        response = future.result(timeout=20)

    task31._assert_error(response, 409, "authorization_scope_changed")
    assert _all_table_snapshot() == before


@pytest.mark.parametrize("adoption_path", ["save_config", "_adopt_config"])
def test_provider_config_write_after_creation_transaction_is_serialized_after_commit(
    client, seeded, monkeypatch, tmp_path, adoption_path,
):
    _configure_test_provider(monkeypatch, tmp_path)
    _saved, _published, _base, consent, payload = _prepare_authorized_request(
        client, seeded, monkeypatch,
    )
    before = _allocation_counts()

    resolve_entered = threading.Event()
    release_resolve = threading.Event()
    update_entered_transaction = threading.Event()
    creation_thread = None
    original_transaction = db.transaction
    original_resolve = guided_sessions._resolve_request

    @contextmanager
    def observe_transactions():
        nonlocal creation_thread
        current_thread = threading.get_ident()
        if creation_thread is None:
            creation_thread = current_thread
        elif current_thread != creation_thread:
            update_entered_transaction.set()
        with original_transaction():
            yield

    def pause_resolution(body, **kwargs):
        resolve_entered.set()
        if not release_resolve.wait(timeout=10):
            raise TimeoutError("creation resolution gate was not released")
        return original_resolve(body, **kwargs)

    monkeypatch.setattr(db, "transaction", observe_transactions)
    monkeypatch.setattr(guided_sessions, "_resolve_request", pause_resolution)

    def post_creation():
        return client.post(CREATE_PATH, json=payload)

    def update_config():
        return _replace_provider_config(adoption_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        creation = pool.submit(post_creation)
        try:
            assert resolve_entered.wait(timeout=10), "creation did not enter its serialized resolution"
            config_update = pool.submit(update_config)
            assert update_entered_transaction.wait(timeout=10), (
                "config adoption did not join the database serialization boundary"
            )
            assert not config_update.done(), "config adoption escaped the active creation transaction"
            assert main.CONFIG["llm_url"] == "https://provider-a.example/v1"
        finally:
            release_resolve.set()
        response = creation.result(timeout=20)
        replacement = config_update.result(timeout=20)

    assert response.status_code == 201, response.text
    assert _allocation_counts()["session"] == before["session"] + 1
    assert main.CONFIG["llm_url"] == replacement["llm_url"]
    assert main.CONFIG["llm_model"] == replacement["llm_model"]
    receipt = db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
        response.json()["full_auto_run_id"],
    )
    authorization = json.loads(receipt["authorization_json"])
    assert authorization["summary"] == consent["summary"]
    assert authorization["scope_issued_at"] == consent["scope_issued_at"]
    assert authorization["authorization_expires_at"] == consent["summary"]["authorization_expires_at"]


def test_room_translation_change_before_serialized_allocation_invalidates_quote(
    client, seeded, monkeypatch,
):
    _saved, _published, base, consent, payload = _prepare_authorized_request(
        client, seeded, monkeypatch,
    )
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        base["scene_anchor"]["library_key"],
    )
    revision_row = db.one(
        """SELECT id FROM asset_revision
           WHERE library_id = ? AND source_id = ? AND content_digest = ?""",
        library["id"],
        base["scene_anchor"]["source_id"],
        base["scene_anchor"]["content_digest"],
    )
    revision = resource_store.get_revision(revision_id=revision_row["id"])
    changed_translation = deepcopy(revision["translation"])
    changed_translation["scene_theme"] = "A later reviewed studio translation."

    entered, release = _hold_creation_before_transaction(monkeypatch)

    def post_creation():
        return client.post(CREATE_PATH, json=payload)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post_creation)
        try:
            assert entered.wait(timeout=10), "creation did not reach its transaction boundary"
            with db.transaction():
                resource_store.update_translation(revision_row["id"], changed_translation)
            before = _all_table_snapshot()
        finally:
            release.set()
        response = future.result(timeout=20)

    detail = task31._assert_error(response, 409, "authorization_scope_changed")
    assert _all_table_snapshot() == before
    assert detail["current"]["summary"]["room"]["translation_digest"] == (
        resource_store.canonical_digest(changed_translation)
    )
    assert detail["current"]["scope_digest"] != consent["scope_digest"]


def _make_alternate_workflow(client, seeded):
    source = db.one("SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"])
    created = client.post("/api/workflows", json={
        "name": f"full-auto-consent-alternate-{uuid.uuid4().hex}",
        "graph": json.loads(source["graph"]),
        "kind": "t2i",
    })
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _make_alternate_character(client, seeded):
    original = db.one("SELECT * FROM model WHERE id = ?", seeded["model_id"])
    created = client.post("/api/models", json={
        "name": f"full-auto-consent-alternate-{uuid.uuid4().hex}",
        "lora_name": original["lora_name"],
        "lora_strength": original["lora_strength"],
        "trigger": original["trigger"],
        "base_positive": original["base_positive"],
        "base_negative": original["base_negative"],
        "workflow_id": original["workflow_id"],
        "settings": json.loads(original["settings"]),
    })
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _change_valid_request_input(client, seeded, base, case):
    changed = deepcopy(base)
    if case == "character_id":
        changed["character_id"] = _make_alternate_character(client, seeded)
    elif case == "workflow_id":
        changed["workflow_id"] = _make_alternate_workflow(client, seeded)
    elif case == "scene_anchor":
        changed["scene_anchor"] = task31._room_anchor()
    elif case == "saved_look_selection":
        alternate_saved, alternate_published, _ = task31._full_auto_fixture(
            seeded, anchor=base["scene_anchor"],
        )
        changed["saved_look"] = task31._saved_look_wire(alternate_saved)
        changed["full_auto"]["profile_revision"] = alternate_published["profile_revision"]
        changed["full_auto"]["profile_digest"] = alternate_published["profile_digest"]
    elif case == "profile_selection":
        saved = task31.saved_looks.get_version(
            base["saved_look"]["look_key"], base["saved_look"]["version"],
        )
        latest = task31.saved_looks.publish_automation_profile(
            saved["key"], saved["version"], saved["content_digest"],
            base["full_auto"]["profile_revision"],
            task31._ready_profile(saved, revision=base["full_auto"]["profile_revision"] + 1),
        )
        changed["full_auto"]["profile_revision"] = latest["profile_revision"]
        changed["full_auto"]["profile_digest"] = latest["profile_digest"]
    elif case == "brief":
        changed["brief"] = "A newly authored request brief."
    elif case == "variation_policy":
        changed["variation_policy"] = deepcopy(task31.ALL_VARY)
        changed["variation_policy"]["camera"] = {
            "mode": "fixed",
            "value": "overhead angle",
            "value_origin": "user",
        }
    elif case == "framing_intent":
        changed["full_auto"]["framing_intent"] = "full_body"
    elif case == "photo_count":
        changed["photo_count"] += 1
    elif case == "canvas":
        changed["width"] = 840
        changed["height"] = 1216
    else:
        raise AssertionError(f"Unknown Full Auto request input: {case}")
    return changed


@pytest.mark.parametrize("case", [
    "character_id",
    "workflow_id",
    "scene_anchor",
    "saved_look_selection",
    "profile_selection",
    "brief",
    "variation_policy",
    "framing_intent",
    "photo_count",
    "canvas",
])
def test_changed_valid_request_input_cannot_reuse_displayed_consent(
    client, seeded, monkeypatch, case,
):
    _saved, _published, base, consent, _original_payload = _prepare_authorized_request(
        client, seeded, monkeypatch,
    )
    changed = _change_valid_request_input(client, seeded, base, case)

    # Confirm that the changed body remains independently eligible, so the stale
    # consent rejection is about binding rather than malformed input.
    before_preflight = _all_table_snapshot()
    changed_quote = client.post(PREFLIGHT_PATH, json=changed)
    assert changed_quote.status_code == 200, changed_quote.text
    assert changed_quote.json()["scope_digest"] != consent["scope_digest"]
    assert _all_table_snapshot() == before_preflight

    changed["request_id"] = str(uuid.uuid4())
    changed["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    before_rejected_create = _all_table_snapshot()

    rejected = client.post(CREATE_PATH, json=changed)

    task31._assert_error(rejected, 409, "authorization_scope_changed")
    assert _all_table_snapshot() == before_rejected_create


@pytest.mark.parametrize(
    ("setting", "replacement"),
    [("checkpoint", "models/full-auto-consent-checkpoint.safetensors"), ("seed", 97531)],
)
def test_changed_checkpoint_or_seed_setting_cannot_reuse_displayed_consent(
    client, seeded, monkeypatch, setting, replacement,
):
    _saved, _published, base, consent, payload = _prepare_authorized_request(
        client, seeded, monkeypatch,
    )
    model = db.one("SELECT settings FROM model WHERE id = ?", seeded["model_id"])
    settings = json.loads(model["settings"])
    settings[setting] = replacement
    db.run(
        "UPDATE model SET settings = ? WHERE id = ?",
        json.dumps(settings, sort_keys=True), seeded["model_id"],
    )
    before_preflight = _all_table_snapshot()

    current_quote = client.post(PREFLIGHT_PATH, json=base)

    assert current_quote.status_code == 200, current_quote.text
    assert current_quote.json()["scope_digest"] != consent["scope_digest"]
    assert _all_table_snapshot() == before_preflight
    before_rejected_create = _all_table_snapshot()

    rejected = client.post(CREATE_PATH, json=payload)

    task31._assert_error(rejected, 409, "authorization_scope_changed")
    assert _all_table_snapshot() == before_rejected_create


@pytest.mark.parametrize("writer", ["model", "workflow"])
def test_bound_source_updates_wait_until_serialized_creation_commits(
    client, seeded, monkeypatch, writer,
):
    _saved, _published, _base, consent, payload = _prepare_authorized_request(
        client, seeded, monkeypatch,
    )
    if writer == "model":
        model = db.one("SELECT * FROM model WHERE id = ?", seeded["model_id"])
        update_url = f"/api/models/{seeded['model_id']}"
        update_body = {
            "name": model["name"],
            "lora_name": model["lora_name"],
            "lora_strength": model["lora_strength"],
            "trigger": model["trigger"],
            "base_positive": "A reviewed model prompt changed after consent.",
            "base_negative": model["base_negative"],
            "workflow_id": model["workflow_id"],
            "settings": json.loads(model["settings"]),
            "notes": model["notes"],
        }
        update_statement = "UPDATE MODEL SET"
    else:
        workflow_id = consent["summary"]["workflow"]["workflow_id"]
        workflow = db.one("SELECT * FROM workflow WHERE id = ?", workflow_id)
        graph = json.loads(workflow["graph"])
        graph["8"]["inputs"]["filename_prefix"] = "changed-after-consent"
        update_url = f"/api/workflows/{workflow_id}"
        update_body = {
            "name": workflow["name"],
            "graph": graph,
            "node_map": json.loads(workflow["node_map"]),
            "kind": workflow["kind"],
        }
        update_statement = "UPDATE WORKFLOW SET"

    resolver_entered = threading.Event()
    release_resolver = threading.Event()
    writer_started = threading.Event()
    writer_transaction_entered = threading.Event()
    writer_statement_entered = threading.Event()
    creation_thread = None
    original_resolve = guided_sessions._resolve_request
    original_run = db.run
    original_transaction = db.transaction

    def pause_after_resolution(body, **kwargs):
        nonlocal creation_thread
        current = original_resolve(body, **kwargs)
        if creation_thread is None:
            creation_thread = threading.get_ident()
            resolver_entered.set()
            if not release_resolver.wait(timeout=10):
                raise TimeoutError("serialized source update gate was not released")
        return current

    def observe_update_statement(sql, *args):
        normalized_sql = " ".join(sql.upper().split())
        if normalized_sql.startswith(update_statement):
            writer_statement_entered.set()
        return original_run(sql, *args)

    @contextmanager
    def observe_transaction():
        if threading.get_ident() != creation_thread:
            writer_transaction_entered.set()
        with original_transaction():
            yield

    monkeypatch.setattr(guided_sessions, "_resolve_request", pause_after_resolution)
    monkeypatch.setattr(db, "run", observe_update_statement)
    monkeypatch.setattr(db, "transaction", observe_transaction)

    def create_session():
        return client.post(CREATE_PATH, json=payload)

    def update_bound_source():
        from fastapi.testclient import TestClient

        # An unentered TestClient uses a per-request portal, so this PATCH reaches
        # its handler while creation is paused in the other TestClient's portal.
        writer_client = TestClient(main.app)
        writer_started.set()
        try:
            return writer_client.patch(update_url, json=update_body)
        finally:
            writer_client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        creation = pool.submit(create_session)
        try:
            assert resolver_entered.wait(timeout=10), "creation did not resolve the quoted source"
            source_update = pool.submit(update_bound_source)
            assert writer_started.wait(timeout=5), "source update was not scheduled"
            assert writer_transaction_entered.wait(timeout=5), (
                f"{writer} route did not enter its serialization boundary"
            )
            assert not writer_statement_entered.wait(timeout=0.2), (
                f"{writer} update wrote through the active creation transaction"
            )
        finally:
            release_resolver.set()
        created = creation.result(timeout=20)
        assert writer_statement_entered.wait(timeout=10), (
            "bound source update did not proceed after creation committed"
        )
        updated = source_update.result(timeout=20)

    assert created.status_code == 201, created.text
    assert updated.status_code == 200, updated.text
    receipt = db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
        created.json()["full_auto_run_id"],
    )
    assert json.loads(receipt["authorization_json"])["summary"] == consent["summary"]
    if writer == "model":
        current_model = db.one("SELECT * FROM model WHERE id = ?", seeded["model_id"])
        assert current_model["base_positive"] == update_body["base_positive"]
        assert resource_store.canonical_digest(current_model["base_positive"]) != (
            consent["summary"]["model_binding"]["base_positive_digest"]
        )
    else:
        current_workflow = db.one(
            "SELECT graph FROM workflow WHERE id = ?",
            consent["summary"]["workflow"]["workflow_id"],
        )
        assert resource_store.canonical_digest(json.loads(current_workflow["graph"])) != (
            consent["summary"]["workflow"]["graph_digest"]
        )
