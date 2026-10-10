"""Focused consent-boundary tests for guided Full Auto creation."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading

import pytest

import db
import main
import resource_store
from backend import guided_sessions, saved_looks
from test_guided_session_creation import _creation_snapshot
from test_task31_acceptance import (
    _allocation_counts,
    _assert_error,
    _create_from_preflight,
    _forbid_external_work,
    _full_auto_fixture,
    _install_t2i_workflow,
    _preflight,
)


def _assert_drift_quote_is_valid_then_stale_creation_is_refused(
    client, base: dict, consent: dict,
):
    before = _creation_snapshot()
    refreshed = _preflight(client, base)
    assert refreshed["scope_digest"] != consent["scope_digest"]
    assert _creation_snapshot() == before

    created, _payload = _create_from_preflight(client, base, consent)

    _assert_error(created, 409, "authorization_scope_changed")
    assert _creation_snapshot() == before


def test_quote_is_valid_at_300_seconds_and_keeps_its_seven_day_deadline(
    client, seeded, monkeypatch,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    real_datetime = guided_sessions.datetime
    instant = real_datetime.now(timezone.utc).replace(microsecond=0)

    class FrozenDateTime(real_datetime):
        current = instant

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(guided_sessions, "datetime", FrozenDateTime)
    _saved, _published, base = _full_auto_fixture(seeded)
    before_preflight = _allocation_counts()

    consent = _preflight(client, base)

    assert consent["scope_issued_at"] == instant.isoformat(timespec="seconds")
    assert _allocation_counts() == before_preflight
    FrozenDateTime.current = instant + timedelta(seconds=300)
    before_create = _allocation_counts()

    created, _payload = _create_from_preflight(client, base, consent)

    assert created.status_code == 201, created.text
    expected_expiry = (instant + timedelta(days=7)).isoformat(timespec="seconds")
    receipt = db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?",
        created.json()["full_auto_run_id"],
    )
    assert receipt["scope_issued_at"] == consent["scope_issued_at"]
    assert receipt["authorization_expires_at"] == expected_expiry
    assert json.loads(receipt["authorization_json"])["summary"][
        "authorization_expires_at"
    ] == expected_expiry
    assert _allocation_counts()["session"] == before_create["session"] + 1


def test_preflight_waits_for_a_rolled_back_local_source_write(
    client, seeded, monkeypatch,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    _saved, _published, base = _full_auto_fixture(seeded)
    anchor = base["scene_anchor"]
    library_id = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        anchor["library_key"],
    )["id"]
    revision = resource_store.get_revision(
        library_id=library_id,
        source_id=anchor["source_id"],
        content_digest=anchor["content_digest"],
    )
    original_translation = revision["translation"]
    original_digest = resource_store.canonical_digest(original_translation)
    changed_translation = dict(original_translation)
    changed_translation["scene_theme"] += " A temporary uncommitted change."
    allocations_before = _allocation_counts()
    writer_ready = threading.Event()
    release_writer = threading.Event()
    request_started = threading.Event()
    service_started = threading.Event()
    resolver_started = threading.Event()
    real_preflight = guided_sessions.preflight_full_auto
    real_resolve = guided_sessions._resolve_request

    class RollBackSourceWrite(Exception):
        pass

    def hold_uncommitted_translation():
        try:
            with db.transaction():
                resource_store.update_translation(revision["id"], changed_translation)
                writer_ready.set()
                assert release_writer.wait(timeout=10)
                raise RollBackSourceWrite
        except RollBackSourceWrite:
            return

    def observe_preflight(*args, **kwargs):
        service_started.set()
        return real_preflight(*args, **kwargs)

    def observe_resolution(*args, **kwargs):
        resolver_started.set()
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(guided_sessions, "preflight_full_auto", observe_preflight)
    monkeypatch.setattr(guided_sessions, "_resolve_request", observe_resolution)

    def request_preflight():
        request_started.set()
        return client.post(
            "/api/sessions/guided/full-auto/preflight", json=base,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(hold_uncommitted_translation)
        assert writer_ready.wait(timeout=5)
        preflight = pool.submit(request_preflight)
        try:
            assert request_started.wait(timeout=5)
            assert service_started.wait(timeout=5)
            assert not resolver_started.wait(timeout=0.2)
        finally:
            release_writer.set()
        writer.result(timeout=10)
        response = preflight.result(timeout=10)

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["room"]["translation_digest"] == original_digest
    assert _allocation_counts() == allocations_before


@pytest.mark.parametrize("dependency", ["model", "workflow"])
@pytest.mark.parametrize("creation_first", [True, False])
def test_bound_update_handlers_serialize_with_full_auto_creation(
    client, seeded, monkeypatch, dependency, creation_first,
):
    workflow_id = _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    if dependency == "model":
        model = db.one("SELECT * FROM model WHERE id = ?", seeded["model_id"])
        update_payload = main.ModelIn(
            name=model["name"],
            lora_name=model["lora_name"],
            trigger=model["trigger"],
            lora_strength=model["lora_strength"],
            base_positive="Updated after the quoted session is committed.",
            base_negative=model["base_negative"],
            workflow_id=model["workflow_id"],
            settings=json.loads(model["settings"]),
            notes=model["notes"],
        )
        update_url = f"/api/models/{seeded['model_id']}"

        def run_update():
            return main.update_model(seeded["model_id"], update_payload)
    else:
        workflow = db.one("SELECT * FROM workflow WHERE id = ?", workflow_id)
        graph = json.loads(workflow["graph"])
        graph["8"]["inputs"]["filename_prefix"] = "updated-after-consent"
        update_payload = main.WorkflowIn(
            name=workflow["name"],
            graph=graph,
            node_map=json.loads(workflow["node_map"]),
            kind=workflow["kind"],
        )
        update_url = f"/api/workflows/{workflow_id}"

        def run_update():
            return main.update_workflow(workflow_id, update_payload)

    creation_thread_ids: set[int] = set()
    update_thread_ids: set[int] = set()
    role_lock = threading.Lock()
    creation_transaction_attempted = threading.Event()
    creation_transaction_acquired = threading.Event()
    update_transaction_attempted = threading.Event()
    update_transaction_acquired = threading.Event()
    release_creation = threading.Event()
    release_update = threading.Event()
    original_transaction = db.transaction
    original_create = guided_sessions.create_or_replay

    def observe_create(*args, **kwargs):
        with role_lock:
            creation_thread_ids.add(threading.get_ident())
        return original_create(*args, **kwargs)

    @contextmanager
    def controlled_transaction():
        thread_id = threading.get_ident()
        with role_lock:
            is_creation = thread_id in creation_thread_ids
            is_update = thread_id in update_thread_ids
        if is_creation:
            creation_transaction_attempted.set()
            with original_transaction():
                creation_transaction_acquired.set()
                if creation_first:
                    assert release_creation.wait(timeout=10)
                yield
        elif is_update:
            update_transaction_attempted.set()
            with original_transaction():
                update_transaction_acquired.set()
                if not creation_first:
                    assert release_update.wait(timeout=10)
                yield
        else:
            with original_transaction():
                yield

    monkeypatch.setattr(guided_sessions, "create_or_replay", observe_create)
    monkeypatch.setattr(db, "transaction", controlled_transaction)

    def post_creation():
        return _create_from_preflight(client, base, consent)

    def invoke_update():
        with role_lock:
            update_thread_ids.add(threading.get_ident())
        return run_update()

    before = _creation_snapshot()
    with ThreadPoolExecutor(max_workers=2) as pool:
        if creation_first:
            creation = pool.submit(post_creation)
            try:
                assert creation_transaction_acquired.wait(timeout=10)
                update = pool.submit(invoke_update)
                assert update_transaction_attempted.wait(timeout=10)
                assert not update.done(), "bound update escaped the active creation transaction"
            finally:
                release_update.set()
                release_creation.set()
            created, _payload = creation.result(timeout=20)
            assert created.status_code == 201, created.text
            assert update.result(timeout=20) == {"ok": True}

            receipt = db.one(
                "SELECT scope_digest FROM full_auto_run_receipt WHERE run_id = ?",
                created.json()["full_auto_run_id"],
            )
            assert receipt["scope_digest"] == consent["scope_digest"]
        else:
            update = pool.submit(invoke_update)
            try:
                assert update_transaction_acquired.wait(timeout=10)
                creation = pool.submit(post_creation)
                assert creation_transaction_attempted.wait(timeout=10)
                assert not creation.done(), "creation escaped the active bound update transaction"
            finally:
                release_creation.set()
                release_update.set()
            assert update.result(timeout=20) == {"ok": True}
            created, _payload = creation.result(timeout=20)
            _assert_error(created, 409, "authorization_scope_changed")
            assert _creation_snapshot() == before

    http_update = client.patch(update_url, json=update_payload.model_dump())
    assert http_update.status_code == 200, http_update.text
    refreshed = _preflight(client, base)
    assert refreshed["scope_digest"] != consent["scope_digest"]
    created, _payload = _create_from_preflight(client, base, refreshed)
    assert created.status_code == 201, created.text


@pytest.mark.parametrize(
    ("config_key", "replacement"),
    [
        ("llm_url", "https://changed-provider.invalid/v1"),
        ("llm_model", "changed-provider-model"),
    ],
)
def test_creation_rejects_provider_url_or_model_drift_without_writes(
    client, seeded, monkeypatch, config_key, replacement,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    monkeypatch.setitem(main.CONFIG, config_key, replacement)
    _assert_drift_quote_is_valid_then_stale_creation_is_refused(client, base, consent)


def test_creation_rejects_changed_model_default_workflow_without_writes(
    client, seeded, monkeypatch,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    quoted_workflow_id = consent["summary"]["workflow"]["workflow_id"]

    replacement_workflow_id = _install_t2i_workflow(client, seeded)
    assert replacement_workflow_id != quoted_workflow_id
    _assert_drift_quote_is_valid_then_stale_creation_is_refused(client, base, consent)


@pytest.mark.parametrize(
    ("model_field", "replacement"),
    [
        ("trigger", "a changed model trigger"),
        ("base_negative", "a changed negative prompt"),
        ("lora_strength", 0.75),
    ],
)
def test_creation_rejects_individual_model_field_drift_without_writes(
    client, seeded, monkeypatch, model_field, replacement,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    db.run(
        f"UPDATE model SET {model_field} = ? WHERE id = ?",
        replacement, seeded["model_id"],
    )
    _assert_drift_quote_is_valid_then_stale_creation_is_refused(client, base, consent)


@pytest.mark.parametrize(
    ("setting", "quoted_value", "replacement"),
    [
        ("height", None, 1224),
        ("cfg", None, 1.5),
        ("sampler", "euler", "heun"),
        ("scheduler", "normal", "karras"),
    ],
)
def test_creation_rejects_individual_model_setting_drift_without_writes(
    client, seeded, monkeypatch, setting, quoted_value, replacement,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    _saved, _published, base = _full_auto_fixture(seeded)
    model = db.one("SELECT settings FROM model WHERE id = ?", seeded["model_id"])
    settings = json.loads(model["settings"])
    if quoted_value is not None:
        settings[setting] = quoted_value
        db.run(
            "UPDATE model SET settings = ? WHERE id = ?",
            json.dumps(settings, sort_keys=True), seeded["model_id"],
        )
    consent = _preflight(client, base)
    settings[setting] = replacement
    db.run(
        "UPDATE model SET settings = ? WHERE id = ?",
        json.dumps(settings, sort_keys=True), seeded["model_id"],
    )
    _assert_drift_quote_is_valid_then_stale_creation_is_refused(client, base, consent)


@pytest.mark.parametrize(
    ("module_name", "constant", "replacement"),
    [
        ("guided_sessions", "_FULL_AUTO_POLICY_VERSION", "resource-full-auto-v2"),
        ("saved_looks", "AUTOMATION_COVERAGE_COMPILER_VERSION", "compiler-v2"),
        ("guided_sessions", "_FULL_AUTO_ASSISTANT_CALLS_PER_TAKE", 3),
        ("guided_sessions", "_FULL_AUTO_IMAGE_ATTEMPTS_PER_TAKE", 2),
    ],
)
def test_creation_rejects_policy_compiler_or_budget_drift_without_writes(
    client, seeded, monkeypatch, module_name, constant, replacement,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    module = guided_sessions if module_name == "guided_sessions" else saved_looks
    monkeypatch.setattr(module, constant, replacement)
    _assert_drift_quote_is_valid_then_stale_creation_is_refused(client, base, consent)
