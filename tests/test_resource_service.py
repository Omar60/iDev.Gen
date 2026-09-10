"""Task 2.5 tests for the shared resource application boundary."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import db
import resource_import
import resource_service
import resource_store


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_resource_tables(client):
    db.run("DELETE FROM auxiliary_resource")
    db.run("DELETE FROM asset_revision")
    db.run("DELETE FROM resource_library")
    yield
    db.run("DELETE FROM auxiliary_resource")
    db.run("DELETE FROM asset_revision")
    db.run("DELETE FROM resource_library")


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _scene(source_id: str, theme: str) -> dict:
    return {
        "id": source_id,
        "label": f"invented label {source_id}",
        "scene_theme": theme,
        "tags": ["invented", "scene"],
        "nested": {"safe": [1, None, "value"]},
    }


def _selection(path: Path) -> list[tuple[str, str]]:
    return [(str(path), "invented_library")]


def _recompute_public_binding(serialized: dict) -> None:
    body = {
        "version": serialized["version"],
        "files": serialized["files"],
        "missing_source_entries": serialized.get("missing_source_entries", []),
    }
    serialized["binding"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_preview_writes_no_resource_rows_and_commit_rehydrates(tmp_path):
    path = tmp_path / "resources.json"
    _write(path, [_scene("scene_one", "invented first room")])

    preview = resource_service.preview_import(_selection(path))
    assert db.q("SELECT * FROM resource_library") == []
    serialized = resource_service.preview_to_dict(preview)
    restored = resource_service.preview_from_dict(serialized)
    report = resource_service.commit_import(serialized)

    assert restored.counts_reconcile()
    assert report.counts_reconcile()
    assert len(resource_store.list_libraries()) == 1
    safe = json.dumps(resource_service.safe_report(report), ensure_ascii=True)
    assert str(tmp_path) not in safe
    assert "invented first room" not in safe


def test_detail_uses_exact_digest_and_refresh_preserves_old_revision(tmp_path):
    path = tmp_path / "resources.json"
    _write(path, [
        _scene("scene_one", "invented first room"),
        _scene("scene_two", "invented second room"),
    ])
    first = resource_service.commit_import(
        resource_service.preview_import(_selection(path))
    )
    first_outcome = first.files[0].accepted_outcomes[0]
    library_id = resource_store.list_libraries()[0]["id"]
    old = resource_store.get_revision(
        library_id=library_id,
        source_id="scene_one",
        content_digest=first_outcome.new_content_digest,
    )
    assert old is not None
    db.run(
        "UPDATE asset_revision SET translation=?, coverage=? WHERE id=?",
        json.dumps({"label": "invented translated label", "scene_theme": "invented translated theme"}),
        json.dumps({"evidence": "invented coverage"}),
        old["id"],
    )
    snapshot = resource_store.get_revision(revision_id=old["id"])

    _write(path, [_scene("scene_one", "invented refreshed room")])
    second = resource_service.commit_import(
        resource_service.preview_import(_selection(path))
    )
    assert second.total_updated == 1
    assert second.total_missing == 1
    assert second.missing_source_entries[0].source_id == "scene_two"

    unchanged = resource_store.get_revision(revision_id=old["id"])
    assert unchanged == snapshot
    new_digest = second.files[0].accepted_outcomes[0].new_content_digest
    assert new_digest != old["content_digest"]
    old_detail = resource_service.get_resource_revision(
        "invented_library", "scene_one", old["content_digest"]
    )
    new_detail = resource_service.get_resource_revision(
        "invented_library", "scene_one", new_digest
    )
    assert old_detail["payload"] == snapshot["payload"]
    assert old_detail["translation"] == snapshot["translation"]
    assert old_detail["coverage"] == snapshot["coverage"]
    assert new_detail["payload"]["scene_theme"] == "invented refreshed room"


def test_api_preview_commit_list_and_exact_detail(client, tmp_path):
    path = tmp_path / "api-resources.json"
    _write(path, [_scene("api_scene", "invented api room")])

    preview_response = client.post(
        "/api/resources/import/preview",
        json={"selections": [{"path": str(path), "library_key": "api_library"}]},
    )
    assert preview_response.status_code == 200
    preview_body = preview_response.json()
    assert preview_body["report"]["phase"] == "preview"
    assert db.q("SELECT * FROM resource_library") == []

    commit_response = client.post(
        "/api/resources/import/commit",
        json={"preview": preview_body["preview"]},
    )
    assert commit_response.status_code == 200
    assert commit_response.json()["report"]["phase"] == "commit"

    libraries = client.get("/api/resources/libraries").json()
    assert len(libraries) == 1
    digest = libraries[0]["revisions"][0]["content_digest"]
    detail = client.get(
        f"/api/resources/revisions/api_library/api_scene/{digest}"
    )
    assert detail.status_code == 200
    assert detail.json()["payload"]["scene_theme"] == "invented api room"
    assert detail.json()["readiness"]["status"] == "pending"
    assert detail.json()["coverage"]["missing_translations"] == ["label", "scene_theme"]


def test_api_refuses_serialized_preview_with_omitted_outcome(client, tmp_path):
    path = tmp_path / "api-two-entry.json"
    _write(path, [
        _scene("api_one", "invented first room"),
        _scene("api_two", "invented second room"),
    ])
    preview = client.post(
        "/api/resources/import/preview",
        json={"selections": [{"path": str(path), "library_key": "api_library"}]},
    ).json()["preview"]
    preview["files"][0]["accepted_outcomes"].pop()

    response = client.post(
        "/api/resources/import/commit",
        json={"preview": preview},
    )

    assert response.status_code == 422
    assert "preview attestation" in response.json()["detail"]
    assert db.q("SELECT * FROM resource_library") == []
    assert db.q("SELECT * FROM asset_revision") == []


def test_api_refuses_forged_public_binding_and_library_identity(client, tmp_path):
    path = tmp_path / "api-tampered.json"
    _write(path, [_scene("api_tampered", "invented tamper room")])
    preview = client.post(
        "/api/resources/import/preview",
        json={"selections": [{"path": str(path), "library_key": "api_library"}]},
    ).json()["preview"]
    preview["files"][0]["library_key"] = "another_library"
    preview["files"][0]["accepted_outcomes"][0]["library_key"] = "another_library"
    _recompute_public_binding(preview)

    response = client.post(
        "/api/resources/import/commit",
        json={"preview": preview},
    )

    assert response.status_code == 422
    assert "preview attestation" in response.json()["detail"]
    assert db.q("SELECT * FROM resource_library") == []
    assert db.q("SELECT * FROM asset_revision") == []


def test_app_and_cli_reports_are_equivalent(tmp_path, client):
    path = tmp_path / "same-input.json"
    _write(path, [_scene("same_scene", "invented shared room")])

    app_preview = client.post(
        "/api/resources/import/preview",
        json={"selections": [{"path": str(path), "library_key": "same_library"}]},
    ).json()
    app_commit = client.post(
        "/api/resources/import/commit",
        json={"preview": app_preview["preview"]},
    ).json()

    cli_data = tmp_path / "cli-data"
    cli_preview = tmp_path / "cli-preview.json"
    cli_report = tmp_path / "cli-report.json"
    env = dict(os.environ)
    env["IDEVGEN_DATA_DIR"] = str(cli_data)
    env["IDEVGEN_CONFIG"] = str(tmp_path / "cli-config.json")
    command = [
        sys.executable,
        str(ROOT / "scripts" / "import_resources.py"),
        "preview",
        "--selection", f"{path}=same_library",
        "--preview-out", str(cli_preview),
        "--report-out", str(cli_report),
    ]
    preview_run = subprocess.run(command, env=env, capture_output=True, text=True)
    assert preview_run.returncode == 0, preview_run.stderr
    assert json.loads(preview_run.stdout) == app_preview["report"]
    commit_run = subprocess.run([
        sys.executable,
        str(ROOT / "scripts" / "import_resources.py"),
        "commit",
        "--preview", str(cli_preview),
        "--report-out", str(cli_report),
    ], env=env, capture_output=True, text=True)
    assert commit_run.returncode == 0, commit_run.stderr

    cli_report_data = json.loads(cli_report.read_text(encoding="utf-8"))
    assert cli_report_data == app_commit["report"]
    app_revision = resource_store.list_revisions(resource_store.list_libraries()[0]["id"])[0]
    with sqlite3.connect(cli_data / "idevgen.db") as connection:
        cli_revision = connection.execute(
            "SELECT source_id, content_digest, payload FROM asset_revision"
        ).fetchone()
    assert cli_revision == (
        app_revision["source_id"],
        app_revision["content_digest"],
        json.dumps(app_revision["payload"], ensure_ascii=False, separators=(",", ":")),
    )
    assert str(tmp_path) not in cli_report.read_text(encoding="utf-8")
    assert "invented shared room" not in cli_report.read_text(encoding="utf-8")


def test_serialized_preview_cannot_be_reused_after_commit(tmp_path):
    path = tmp_path / "one-time.json"
    _write(path, [_scene("one_time", "invented one-time room")])
    serialized = resource_service.preview_to_dict(
        resource_service.preview_import(_selection(path))
    )

    resource_service.commit_import(serialized)
    with pytest.raises(ValueError, match="already been consumed"):
        resource_service.commit_import(serialized)
    assert len(resource_store.list_libraries()) == 1
    assert len(resource_store.list_revisions(resource_store.list_libraries()[0]["id"])) == 1


def test_cli_commit_refuses_stale_preview(tmp_path):
    path = tmp_path / "stale.json"
    _write(path, [_scene("stale_scene", "invented original room")])
    preview = tmp_path / "stale-preview.json"
    report = tmp_path / "stale-report.json"
    env = dict(os.environ)
    env["IDEVGEN_DATA_DIR"] = str(tmp_path / "stale-data")
    env["IDEVGEN_CONFIG"] = str(tmp_path / "stale-config.json")
    base = [
        sys.executable,
        str(ROOT / "scripts" / "import_resources.py"),
    ]
    preview_run = subprocess.run(base + [
        "preview", "--selection", f"{path}=stale_library",
        "--preview-out", str(preview), "--report-out", str(report),
    ], env=env, capture_output=True, text=True)
    assert preview_run.returncode == 0, preview_run.stderr
    _write(path, [_scene("stale_scene", "invented changed room")])
    commit_run = subprocess.run(base + [
        "commit", "--preview", str(preview), "--report-out", str(report),
    ], env=env, capture_output=True, text=True)
    assert commit_run.returncode == 2
    assert "fresh preview" in commit_run.stderr


def test_cli_refuses_serialized_preview_with_omitted_outcome(tmp_path):
    path = tmp_path / "cli-two-entry.json"
    _write(path, [
        _scene("cli_one", "invented first room"),
        _scene("cli_two", "invented second room"),
    ])
    preview = tmp_path / "cli-preview.json"
    report = tmp_path / "cli-report.json"
    data_dir = tmp_path / "cli-data"
    env = dict(os.environ)
    env["IDEVGEN_DATA_DIR"] = str(data_dir)
    env["IDEVGEN_CONFIG"] = str(tmp_path / "cli-config.json")
    base = [sys.executable, str(ROOT / "scripts" / "import_resources.py")]

    preview_run = subprocess.run(base + [
        "preview", "--selection", f"{path}=cli_library",
        "--preview-out", str(preview), "--report-out", str(report),
    ], env=env, capture_output=True, text=True)
    assert preview_run.returncode == 0, preview_run.stderr
    report_before = report.read_bytes()
    serialized = json.loads(preview.read_text(encoding="utf-8"))
    serialized["files"][0]["accepted_outcomes"].pop()
    preview.write_text(json.dumps(serialized), encoding="utf-8")

    commit_run = subprocess.run(base + [
        "commit", "--preview", str(preview), "--report-out", str(report),
    ], env=env, capture_output=True, text=True)

    assert commit_run.returncode == 1
    assert "preview attestation" in commit_run.stderr
    assert report.read_bytes() == report_before
    with sqlite3.connect(data_dir / "idevgen.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM resource_library").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM asset_revision").fetchone() == (0,)


def test_cli_refuses_forged_public_binding_and_library_identity(tmp_path):
    path = tmp_path / "cli-forged.json"
    _write(path, [_scene("cli_forged", "invented forged room")])
    preview = tmp_path / "cli-forged-preview.json"
    report = tmp_path / "cli-forged-report.json"
    data_dir = tmp_path / "cli-forged-data"
    env = dict(os.environ)
    env["IDEVGEN_DATA_DIR"] = str(data_dir)
    env["IDEVGEN_CONFIG"] = str(tmp_path / "cli-forged-config.json")
    base = [sys.executable, str(ROOT / "scripts" / "import_resources.py")]

    preview_run = subprocess.run(base + [
        "preview", "--selection", f"{path}=cli_library",
        "--preview-out", str(preview), "--report-out", str(report),
    ], env=env, capture_output=True, text=True)
    assert preview_run.returncode == 0, preview_run.stderr
    report_before = report.read_bytes()
    serialized = json.loads(preview.read_text(encoding="utf-8"))
    serialized["files"][0]["library_key"] = "forged_library"
    serialized["files"][0]["accepted_outcomes"][0]["library_key"] = "forged_library"
    _recompute_public_binding(serialized)
    preview.write_text(json.dumps(serialized), encoding="utf-8")

    commit_run = subprocess.run(base + [
        "commit", "--preview", str(preview), "--report-out", str(report),
    ], env=env, capture_output=True, text=True)

    assert commit_run.returncode == 1
    assert "preview attestation" in commit_run.stderr
    assert report.read_bytes() == report_before
    with sqlite3.connect(data_dir / "idevgen.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM resource_library").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM asset_revision").fetchone() == (0,)


def test_cli_concurrent_commit_has_one_atomic_attestation_claim(tmp_path):
    path = tmp_path / "empty.json"
    _write(path, [])
    preview = tmp_path / "empty-preview.json"
    data_dir = tmp_path / "empty-data"
    env = dict(os.environ)
    env["IDEVGEN_DATA_DIR"] = str(data_dir)
    env["IDEVGEN_CONFIG"] = str(tmp_path / "empty-config.json")
    base = [sys.executable, str(ROOT / "scripts" / "import_resources.py")]

    preview_run = subprocess.run(base + [
        "preview", "--selection", f"{path}=empty_library",
        "--preview-out", str(preview), "--report-out", str(tmp_path / "preview-report.json"),
    ], env=env, capture_output=True, text=True)
    assert preview_run.returncode == 0, preview_run.stderr

    processes = [
        subprocess.Popen(
            base + [
                "commit", "--preview", str(preview),
                "--report-out", str(tmp_path / f"commit-report-{index}.json"),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        results.append((process.returncode, stdout, stderr))
    codes = sorted(code for code, stdout, stderr in results)
    assert codes == [0, 1]

    loser = next(stderr for code, stdout, stderr in results if code == 1)
    assert "already been consumed" in loser
    assert all(
        word not in (stdout + stderr).lower()
        for code, stdout, stderr in results
        for word in ("sharing", "permission")
    )
    with sqlite3.connect(data_dir / "idevgen.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM resource_library").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM asset_revision").fetchone() == (0,)


def test_resource_docs_describe_the_delivered_boundary():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    getting_started = (ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    for document in (readme, getting_started):
        assert "/api/resources/libraries" in document
        assert "/api/resources/import/preview" in document
        assert "/api/resources/import/commit" in document
        assert "fresh preview" in document
        assert "source prose" in document
        assert "local attestation" in document
        assert "resource libraries" in document
        assert "revisions" in document


def test_resource_session_and_limitations_docs_describe_delivered_contracts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    getting_started = (ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    sessions = (ROOT / "docs" / "sessions.md").read_text(encoding="utf-8")
    limitations = (ROOT / "docs" / "known-limitations.md").read_text(encoding="utf-8")

    # Both README and getting-started distinguish the SQLite resource path and note original-language storage
    for document in (readme, getting_started):
        assert "original language" in document
        assert "pending" in document
        assert "fused" in document.lower()

    # README and sessions document the three workflow kinds for reference/t2i submission
    for document in (readme, sessions):
        assert "Text-to-image" in document
        assert "Reference edit" in document
        assert "Guided paint" in document
        assert "pixel-level continuity" in document

    # Sessions explicitly documents catalogue independence and cell uniqueness retirement for resource-v1
    assert "cell uniqueness" in sessions
    assert "resource-v1" in sessions

    # Known limitations documents lack of pixel continuity and pending untranslated resources
    assert "pixel-level continuity" in limitations
    assert "Untranslated resources remain pending" in limitations
    assert "AmazingDraw" in limitations


def test_preview_mtime_ns_serialized_as_decimal_string_and_deserialized_as_int(tmp_path):
    path = tmp_path / "resources.json"
    _write(path, [_scene("scene_test", "invented room")])

    preview = resource_service.preview_import(_selection(path))
    serialized = resource_service.serialize_preview(preview)
    mtime_wire = serialized["files"][0]["fingerprint"]["mtime_ns"]
    assert isinstance(mtime_wire, str)
    assert mtime_wire.isdigit() and mtime_wire.isascii()
    assert mtime_wire == "0" or not mtime_wire.startswith("0")

    restored = resource_service.deserialize_preview(serialized)
    restored_mtime = restored.files[0].fingerprint.mtime_ns
    assert isinstance(restored_mtime, int)
    assert not isinstance(restored_mtime, bool)
    assert restored_mtime == int(mtime_wire)
    assert restored.counts_reconcile()


def test_fingerprint_mtime_ns_exceeding_max_safe_integer_survives_exact():
    large_val = 9007199254740993
    assert large_val > 9007199254740991

    fp = resource_import.FileFingerprint(
        path="invented/test.json",
        size=123,
        mtime_ns=large_val,
        content_sha256="a" * 64,
    )
    serialized = resource_service._fingerprint_to_dict(fp)
    assert serialized["mtime_ns"] == "9007199254740993"
    assert isinstance(serialized["mtime_ns"], str)

    restored = resource_service._fingerprint_from_dict(serialized)
    assert isinstance(restored.mtime_ns, int)
    assert not isinstance(restored.mtime_ns, bool)
    assert restored.mtime_ns == large_val


def test_tampered_serialized_mtime_ns_invalidates_attestation(client, tmp_path):
    path = tmp_path / "api-tampered-mtime.json"
    _write(path, [_scene("scene_tamper", "invented room")])

    preview = client.post(
        "/api/resources/import/preview",
        json={"selections": [{"path": str(path), "library_key": "api_library"}]},
    ).json()["preview"]

    tampered = copy.deepcopy(preview)
    original_mtime = int(tampered["files"][0]["fingerprint"]["mtime_ns"])
    tampered["files"][0]["fingerprint"]["mtime_ns"] = str(original_mtime + 1)

    with pytest.raises(ValueError, match="serialized resource preview attestation is invalid"):
        resource_service.preview_from_dict(tampered)

    response = client.post(
        "/api/resources/import/commit",
        json={"preview": tampered},
    )
    assert response.status_code == 422
    assert "serialized resource preview attestation is invalid" in response.json()["detail"]
    assert db.q("SELECT * FROM resource_library") == []


def test_fingerprint_rejects_non_string_wire_types_for_mtime_ns():
    base = {
        "path": "invented/test.json",
        "size": 100,
        "content_sha256": "0" * 64,
    }
    non_string_values = [
        9007199254740993,
        9007199254740993.0,
        True,
        False,
        None,
        ["9007199254740993"],
        {"mtime_ns": "9007199254740993"},
    ]
    for val in non_string_values:
        with pytest.raises(ValueError, match="preview fingerprint mtime_ns must be a canonical decimal integer string"):
            resource_service._fingerprint_from_dict({**base, "mtime_ns": val})


def test_fingerprint_rejects_malformed_decimal_strings_for_mtime_ns():
    base = {
        "path": "invented/test.json",
        "size": 100,
        "content_sha256": "0" * 64,
    }
    malformed_strings = [
        "",
        " ",
        "  123  ",
        "not_a_number",
        "-1",
        "-9007199254740993",
        "+100",
        "01",
        "00",
        "007",
        "1.0",
        "1e9",
        "0x10",
        "١٢٣",
    ]
    for text in malformed_strings:
        with pytest.raises(ValueError, match="preview fingerprint mtime_ns must be a canonical decimal integer string"):
            resource_service._fingerprint_from_dict({**base, "mtime_ns": text})

    zero_fp = resource_service._fingerprint_from_dict({**base, "mtime_ns": "0"})
    assert zero_fp.mtime_ns == 0
    assert isinstance(zero_fp.mtime_ns, int)
    assert not isinstance(zero_fp.mtime_ns, bool)


def test_preview_from_dict_explicitly_refuses_previous_preview_version(client, tmp_path):
    path = tmp_path / "resources.json"
    _write(path, [_scene("v1_scene", "invented room")])

    preview = resource_service.preview_import(_selection(path))
    serialized = resource_service.preview_to_dict(preview)
    v1_preview = copy.deepcopy(serialized)
    v1_preview["version"] = 1

    with pytest.raises(ValueError, match="unsupported resource preview version: 1"):
        resource_service.preview_from_dict(v1_preview)

    response = client.post(
        "/api/resources/import/commit",
        json={"preview": v1_preview},
    )
    assert response.status_code == 422
    assert "unsupported resource preview version: 1" in response.json()["detail"]
    assert db.q("SELECT * FROM resource_library") == []


def test_node_javascript_corrupts_numeric_mtime_ns_exceeding_max_safe_integer():
    node = shutil.which("node")
    if not node:
        pytest.skip("needs node")

    unsafe_val = 9007199254740993
    numeric_json = f'{{"mtime_ns": {unsafe_val}}}'
    node_script = (
        "const fs = require('fs');\n"
        "const raw = fs.readFileSync(0, 'utf-8');\n"
        "const parsed = JSON.parse(raw);\n"
        "process.stdout.write(JSON.stringify(parsed));\n"
    )
    proc = subprocess.run(
        [node, "-e", node_script],
        input=numeric_json,
        capture_output=True,
        text=True,
        check=True,
    )
    js_output = json.loads(proc.stdout)
    assert js_output["mtime_ns"] != unsafe_val


def test_api_preview_commit_roundtrip_through_real_javascript_node(client, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("needs node")

    path = tmp_path / "api-resources.json"
    _write(path, [_scene("api_scene", "invented api room")])
    assert path.stat().st_mtime_ns > 9007199254740991

    preview_response = client.post(
        "/api/resources/import/preview",
        json={"selections": [{"path": str(path), "library_key": "api_library"}]},
    )
    assert preview_response.status_code == 200
    preview_body = preview_response.json()
    raw_preview = preview_body["preview"]

    mtime_wire = raw_preview["files"][0]["fingerprint"]["mtime_ns"]
    assert isinstance(mtime_wire, str)
    assert int(mtime_wire) > 9007199254740991

    node_script = (
        "const fs = require('fs');\n"
        "const raw = fs.readFileSync(0, 'utf-8');\n"
        "const parsed = JSON.parse(raw);\n"
        "process.stdout.write(JSON.stringify(parsed));\n"
    )
    node_proc = subprocess.run(
        [node, "-e", node_script],
        input=json.dumps(raw_preview),
        capture_output=True,
        text=True,
        check=True,
    )
    js_processed_preview = json.loads(node_proc.stdout)

    commit_response = client.post(
        "/api/resources/import/commit",
        json={"preview": js_processed_preview},
    )
    assert commit_response.status_code == 200
    assert commit_response.json()["report"]["phase"] == "commit"
    assert len(resource_store.list_libraries()) == 1
