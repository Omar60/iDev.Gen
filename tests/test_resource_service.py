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
import resource_parser
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


def test_api_preview_and_commit_envelope_source_file(client, tmp_path):
    path = tmp_path / "envelope-scenes.json"
    envelope_payload = {
        "library": "invented_scenes",
        "items": [
            {
                "identifier": "invented_scene_01",
                "label": "Example scene 01",
                "theme": "Invented studio environment",
                "tags": ["interior", "studio"],
            },
            {
                "identifier": "invented_scene_02",
                "label": "Example scene 02",
                "theme": "Invented garden environment",
                "tags": ["exterior", "garden"],
            },
        ],
    }
    _write(path, envelope_payload)

    preview_response = client.post(
        "/api/resources/import/preview",
        json={"selections": [{"path": str(path), "library_key": "invented_scenes"}]},
    )
    assert preview_response.status_code == 200
    preview_body = preview_response.json()
    report = preview_body["report"]
    assert report["phase"] == "preview"
    assert report["summary"]["accepted"] == 2
    assert report["summary"]["unresolved"] == 0
    assert len(report["files"]) == 1
    file_rep = report["files"][0]
    assert file_rep["total_inputs"] == 2
    assert len(file_rep["accepted"]) == 2
    assert len(file_rep["unresolved"]) == 0

    raw_preview = preview_body["preview"]
    commit_response = client.post(
        "/api/resources/import/commit",
        json={"preview": raw_preview},
    )
    assert commit_response.status_code == 200
    commit_report = commit_response.json()["report"]
    assert commit_report["phase"] == "commit"
    assert commit_report["summary"]["recorded"] == 2

    libraries_resp = client.get("/api/resources/libraries")
    assert libraries_resp.status_code == 200
    libraries = libraries_resp.json()
    assert len(libraries) == 1
    assert libraries[0]["library_key"] == "invented_scenes"
    assert libraries[0]["revision_count"] == 2

    lib_detail_resp = client.get("/api/resources/libraries/invented_scenes")
    assert lib_detail_resp.status_code == 200
    lib_detail = lib_detail_resp.json()
    revisions_meta = {r["source_id"]: r["content_digest"] for r in lib_detail["revisions"]}
    assert "invented_scene_01" in revisions_meta
    assert "invented_scene_02" in revisions_meta

    digest1 = revisions_meta["invented_scene_01"]
    rev1 = resource_service.get_resource_revision("invented_scenes", "invented_scene_01", digest1)
    assert rev1 is not None
    assert rev1["source_id"] == "invented_scene_01"
    assert rev1["payload"]["identifier"] == "invented_scene_01"
    assert rev1["payload"]["label"] == "Example scene 01"
    assert rev1["payload"]["theme"] == "Invented studio environment"
    assert "library" not in rev1["payload"]
    assert "items" not in rev1["payload"]

    api_rev1_resp = client.get(
        f"/api/resources/revisions/invented_scenes/invented_scene_01/{digest1}"
    )
    assert api_rev1_resp.status_code == 200
    api_rev1 = api_rev1_resp.json()
    assert api_rev1["source_id"] == "invented_scene_01"
    assert api_rev1["payload"]["theme"] == "Invented studio environment"
    assert "library" not in api_rev1["payload"]

    digest2 = revisions_meta["invented_scene_02"]
    api_rev2_resp = client.get(
        f"/api/resources/revisions/invented_scenes/invented_scene_02/{digest2}"
    )
    assert api_rev2_resp.status_code == 200
    api_rev2 = api_rev2_resp.json()
    assert api_rev2["source_id"] == "invented_scene_02"
    assert api_rev2["payload"]["theme"] == "Invented garden environment"
    assert "library" not in api_rev2["payload"]


def test_validate_safe_report_valid_preview_and_commit(tmp_path):
    path = tmp_path / "resources.json"
    _write(path, [_scene("scene_valid", "invented room")])

    preview = resource_service.preview_import(_selection(path))
    preview_safe = resource_service.safe_report(preview)
    validated_preview = resource_service.validate_safe_report(preview_safe, expected_phase="preview")
    assert validated_preview["phase"] == "preview"
    assert validated_preview["summary"]["inputs"] == 1
    assert len(validated_preview["files"]) == 1

    commit_report = resource_service.commit_import(resource_service.preview_to_dict(preview))
    commit_safe = resource_service.safe_report(commit_report)
    validated_commit = resource_service.validate_safe_report(commit_safe, expected_phase="commit")
    assert validated_commit["phase"] == "commit"
    assert validated_commit["summary"]["recorded"] == 1


@pytest.mark.parametrize(
    "corrupt_report",
    [
        "not a dict",
        None,
        [],
        {"phase": "other", "summary": {}, "files": [], "missing_source_entries": []},
        {"phase": "preview", "summary": {"total_inputs": True}, "files": [], "missing_source_entries": []},
        {"phase": "preview", "summary": {"total_inputs": -1}, "files": [], "missing_source_entries": []},
        {"phase": "preview", "summary": {"total_inputs": "1"}, "files": [], "missing_source_entries": []},
        {"phase": "preview", "summary": {}, "files": "not a list", "missing_source_entries": []},
        {"phase": "preview", "summary": {}, "files": [{"file_name": "../../etc/passwd", "library_key": "k", "total_inputs": 1, "accepted": [], "unresolved": []}], "missing_source_entries": []},
        {"phase": "preview", "summary": {}, "files": [{"file_name": "f.json", "library_key": "k", "total_inputs": 1, "accepted": [{"source_id": "s", "library_key": "k", "new_content_digest": "INVALID_HEX"}], "unresolved": []}], "missing_source_entries": []},
        {"phase": "preview", "summary": {}, "files": [], "missing_source_entries": "not a list"},
        {"phase": "preview", "summary": {}, "files": [], "missing_source_entries": [{"source_id": "s", "library_key": "k", "content_digest": "not-64-hex"}]},
    ],
)
def test_validate_safe_report_rejects_corrupt_payloads(corrupt_report):
    with pytest.raises((ValueError, TypeError)):
        resource_service.validate_safe_report(corrupt_report)


# ---------------------------------------------------------------------------
# Repair 4B Tests: Canonical Safe Report Contract
# ---------------------------------------------------------------------------

class StringSubclass(str):
    """Subclass of str to verify exact type checks."""
    pass


class IntSubclass(int):
    """Subclass of int to verify exact type checks."""
    pass


def _valid_canonical_preview_dict() -> dict[str, Any]:
    """Helper returning a fully-populated, reconciled preview report dict."""
    digest_a = "a" * 64
    digest_b = "b" * 64
    digest_prev = "d" * 64
    digest_aux = "e" * 64
    digest_miss = "f" * 64
    return {
        "version": 1,
        "phase": "preview",
        "summary": {
            "files": 1,
            "inputs": 5,
            "accepted": 2,
            "auxiliary": 1,
            "duplicates": 1,
            "unresolved": 1,
            "new": 1,
            "unchanged": 0,
            "updated": 1,
            "missing": 1,
        },
        "files": [
            {
                "library_key": "canon_lib",
                "total_inputs": 5,
                "accepted": [
                    {
                        "source_id": "scene_new",
                        "library_key": "canon_lib",
                        "kind": "rooms",
                        "classification": "new",
                        "new_content_digest": digest_a,
                        "previous_content_digest": None,
                    },
                    {
                        "source_id": "scene_upd",
                        "library_key": "canon_lib",
                        "kind": "fused_scenes",
                        "classification": "updated",
                        "new_content_digest": digest_b,
                        "previous_content_digest": digest_prev,
                    },
                ],
                "auxiliary": [
                    {
                        "library_key": "canon_lib",
                        "kind": "translation_map",
                        "classification": "unchanged",
                        "new_content_digest": digest_aux,
                        "previous_content_digest": digest_aux,
                    }
                ],
                "duplicates": [
                    {
                        "source_id": "dup_01",
                        "occurrences": 2,
                    }
                ],
                "unresolved": [
                    {
                        "bucket": "malformed",
                        "index": 4,
                        "reason": "missing required prompt field",
                        "received_type": "dict",
                        "expected_kind": "fused_scenes",
                        "identifier_fields": ["id", "key"],
                    }
                ],
            }
        ],
        "missing_source_entries": [
            {
                "library_key": "canon_lib",
                "source_id": "scene_missing",
                "latest_content_digest": digest_miss,
            }
        ],
    }


def _valid_canonical_commit_dict() -> dict[str, Any]:
    """Helper returning a fully-populated, reconciled commit report dict."""
    d = _valid_canonical_preview_dict()
    d["phase"] = "commit"
    d["summary"].update({
        "recorded": 3,
        "new_scene_revisions": 2,
        "unchanged_scene_revisions": 0,
        "updated_scene_revisions": 1,
        "new_auxiliary_revisions": 0,
        "unchanged_auxiliary_revisions": 1,
    })
    return d


def test_repair_4b_producer_validator_equivalence(tmp_path):
    # 1. Empty reports
    empty_prev = resource_import.PreviewReport()
    safe_empty_prev = resource_service.safe_report(empty_prev)
    assert resource_service.validate_safe_report(safe_empty_prev, expected_phase="preview") == safe_empty_prev
    assert resource_service.validate_safe_report(json.dumps(safe_empty_prev), expected_phase="preview") == safe_empty_prev

    empty_commit = resource_import.CommitReport()
    safe_empty_commit = resource_service.safe_report(empty_commit)
    assert resource_service.validate_safe_report(safe_empty_commit, expected_phase="commit") == safe_empty_commit
    assert resource_service.validate_safe_report(json.dumps(safe_empty_commit), expected_phase="commit") == safe_empty_commit

    # 2. Genuine preview and commit from real files
    path1 = tmp_path / "scenes1.json"
    _write(path1, [
        _scene("sc_01", "room 1"),
        _scene("sc_02", "room 2"),
        {"translation_map": {"a": "b"}},
        {"invalid": "unsupported"},
    ])
    path2 = tmp_path / "scenes2.json"
    _write(path2, [
        _scene("sc_03", "room 3"),
    ])

    preview = resource_service.preview_import([(str(path1), "lib_1"), (str(path2), "lib_2")])
    safe_prev = resource_service.safe_report(preview)
    val_prev = resource_service.validate_safe_report(safe_prev, expected_phase="preview")
    assert val_prev == safe_prev
    assert resource_service.validate_safe_report(json.dumps(safe_prev), expected_phase="preview") == safe_prev

    # Commit the preview
    commit = resource_service.commit_import(resource_service.preview_to_dict(preview))
    safe_commit = resource_service.safe_report(commit)
    val_commit = resource_service.validate_safe_report(safe_commit, expected_phase="commit")
    assert val_commit == safe_commit
    assert resource_service.validate_safe_report(json.dumps(safe_commit), expected_phase="commit") == safe_commit


def test_repair_4b_enum_matrices():
    # 1. Accepted scene kinds
    for valid_kind in (resource_parser.KIND_ROOMS, resource_parser.KIND_FUSED_SCENES):
        d = _valid_canonical_preview_dict()
        d["files"][0]["accepted"][0]["kind"] = valid_kind
        assert resource_service.validate_safe_report(d)["files"][0]["accepted"][0]["kind"] == valid_kind

    for invalid_kind in ("ROOMS", "Rooms", "rooms ", "fused", "character", ""):
        d = _valid_canonical_preview_dict()
        d["files"][0]["accepted"][0]["kind"] = invalid_kind
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # 2. Auxiliary kinds
    for valid_aux in resource_parser.ALL_AUXILIARY_KINDS:
        d = _valid_canonical_preview_dict()
        d["files"][0]["auxiliary"][0]["kind"] = valid_aux
        assert resource_service.validate_safe_report(d)["files"][0]["auxiliary"][0]["kind"] == valid_aux

    for invalid_aux in ("TRANSLATION_MAP", "Cut_Map", "custom", "auxiliary", ""):
        d = _valid_canonical_preview_dict()
        d["files"][0]["auxiliary"][0]["kind"] = invalid_aux
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # 3. Classifications
    for invalid_clsf in ("NEW", "Unchanged", "updated_scene", "deleted", ""):
        d = _valid_canonical_preview_dict()
        d["files"][0]["accepted"][0]["classification"] = invalid_clsf
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # 4. Unresolved buckets
    for valid_bkt in resource_import.ALL_UNRESOLVED_BUCKETS:
        d = _valid_canonical_preview_dict()
        d["files"][0]["unresolved"][0]["bucket"] = valid_bkt
        if valid_bkt == resource_import.BUCKET_FILE_READ_ERROR:
            d["files"][0]["unresolved"][0]["reason"] = "source file could not be read or parsed"
        assert resource_service.validate_safe_report(d)["files"][0]["unresolved"][0]["bucket"] == valid_bkt

    for invalid_bkt in ("MALFORMED", "error", "missing", "unknown_bucket", ""):
        d = _valid_canonical_preview_dict()
        d["files"][0]["unresolved"][0]["bucket"] = invalid_bkt
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # 5. Expected kinds in unresolved
    for valid_exp in ("", resource_parser.KIND_ROOMS, resource_parser.KIND_FUSED_SCENES):
        d = _valid_canonical_preview_dict()
        d["files"][0]["unresolved"][0]["expected_kind"] = valid_exp
        assert resource_service.validate_safe_report(d)["files"][0]["unresolved"][0]["expected_kind"] == valid_exp

    for invalid_exp in ("ROOMS", "scene", "translation_map"):
        d = _valid_canonical_preview_dict()
        d["files"][0]["unresolved"][0]["expected_kind"] = invalid_exp
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # 6. Identifier fields
    valid_subsets = [
        [],
        ["id"],
        ["identifier"],
        ["key"],
        ["id", "identifier"],
        ["id", "key"],
        ["identifier", "key"],
        ["id", "identifier", "key"],
    ]
    for sub in valid_subsets:
        d = _valid_canonical_preview_dict()
        d["files"][0]["unresolved"][0]["identifier_fields"] = sub
        assert resource_service.validate_safe_report(d)["files"][0]["unresolved"][0]["identifier_fields"] == sub

    invalid_subsets = [
        ["key", "id"],
        ["identifier", "id"],
        ["id", "id"],
        ["id", "key", "key"],
        ["unknown"],
        ["ID"],
        ("id",),
    ]
    for sub in invalid_subsets:
        d = _valid_canonical_preview_dict()
        d["files"][0]["unresolved"][0]["identifier_fields"] = sub
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)


def test_repair_4b_digest_variants():
    invalid_digests = [
        "a" * 63,
        "a" * 65,
        "a" * 16,
        "a" * 128,
        "A" * 64,
        "a" * 63 + "F",
        "g" * 64,
        "",
        " " + "a" * 63,
        "a" * 64 + " ",
        "sha256:" + "a" * 64,
        123,
        True,
        StringSubclass("a" * 64),
    ]

    # Test new_content_digest in accepted
    for bad in invalid_digests:
        d = _valid_canonical_preview_dict()
        d["files"][0]["accepted"][0]["new_content_digest"] = bad
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # Test previous_content_digest in accepted (updated item)
    for bad in invalid_digests:
        d = _valid_canonical_preview_dict()
        d["files"][0]["accepted"][1]["previous_content_digest"] = bad
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # Test auxiliary new_content_digest
    for bad in invalid_digests:
        d = _valid_canonical_preview_dict()
        d["files"][0]["auxiliary"][0]["new_content_digest"] = bad
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # Test missing_source_entries latest_content_digest
    for bad in invalid_digests:
        d = _valid_canonical_preview_dict()
        d["missing_source_entries"][0]["latest_content_digest"] = bad
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)


def test_repair_4b_classification_relationships():
    # 1. Accepted items
    # new with previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["accepted"][0]["previous_content_digest"] = "d" * 64
    with pytest.raises(ValueError, match="classification 'new' requires previous_content_digest to be None"):
        resource_service.validate_safe_report(d)

    # unchanged with no previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["accepted"][0]["classification"] = "unchanged"
    d["files"][0]["accepted"][0]["previous_content_digest"] = None
    with pytest.raises(ValueError, match="classification 'unchanged' requires previous_content_digest to be non-None"):
        resource_service.validate_safe_report(d)

    # unchanged with different previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["accepted"][0]["classification"] = "unchanged"
    d["files"][0]["accepted"][0]["previous_content_digest"] = "z" * 64
    with pytest.raises(ValueError):
        resource_service.validate_safe_report(d)

    # updated with no previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["accepted"][1]["previous_content_digest"] = None
    with pytest.raises(ValueError, match="classification 'updated' requires previous_content_digest to be non-None"):
        resource_service.validate_safe_report(d)

    # updated with equal previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["accepted"][1]["previous_content_digest"] = d["files"][0]["accepted"][1]["new_content_digest"]
    with pytest.raises(ValueError, match="classification 'updated' requires previous_content_digest to differ"):
        resource_service.validate_safe_report(d)

    # 2. Auxiliary items
    # new with previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["auxiliary"][0]["classification"] = "new"
    d["files"][0]["auxiliary"][0]["previous_content_digest"] = "e" * 64
    with pytest.raises(ValueError, match="classification 'new' requires previous_content_digest to be None"):
        resource_service.validate_safe_report(d)

    # unchanged with no previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["auxiliary"][0]["classification"] = "unchanged"
    d["files"][0]["auxiliary"][0]["previous_content_digest"] = None
    with pytest.raises(ValueError, match="classification 'unchanged' requires previous_content_digest to be non-None"):
        resource_service.validate_safe_report(d)

    # unchanged with different previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["auxiliary"][0]["classification"] = "unchanged"
    d["files"][0]["auxiliary"][0]["previous_content_digest"] = "1" * 64
    with pytest.raises(ValueError):
        resource_service.validate_safe_report(d)

    # updated with no previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["auxiliary"][0]["classification"] = "updated"
    d["files"][0]["auxiliary"][0]["previous_content_digest"] = None
    with pytest.raises(ValueError, match="classification 'updated' requires previous_content_digest to be non-None"):
        resource_service.validate_safe_report(d)

    # updated with equal previous digest
    d = _valid_canonical_preview_dict()
    d["files"][0]["auxiliary"][0]["classification"] = "updated"
    d["files"][0]["auxiliary"][0]["previous_content_digest"] = d["files"][0]["auxiliary"][0]["new_content_digest"]
    with pytest.raises(ValueError, match="classification 'updated' requires previous_content_digest to differ"):
        resource_service.validate_safe_report(d)


def test_repair_4b_integer_and_counter_variants():
    bad_ints = [True, False, 1.0, "1", IntSubclass(1), -1, 9007199254740992]

    # Version rejected
    for bad in bad_ints:
        d = _valid_canonical_preview_dict()
        d["version"] = bad
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # Summary integer rejected
    for bad in bad_ints:
        d = _valid_canonical_preview_dict()
        d["summary"]["inputs"] = bad
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # File total_inputs rejected
    for bad in bad_ints:
        d = _valid_canonical_preview_dict()
        d["files"][0]["total_inputs"] = bad
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # Duplicate occurrences: 0 and 1 rejected, 2 and higher accepted
    for bad_occ in (0, 1, -1, True, 2.0, "2", IntSubclass(2)):
        d = _valid_canonical_preview_dict()
        d["files"][0]["duplicates"][0]["occurrences"] = bad_occ
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    d = _valid_canonical_preview_dict()
    d["files"][0]["duplicates"][0]["occurrences"] = 2
    assert resource_service.validate_safe_report(d)["files"][0]["duplicates"][0]["occurrences"] == 2

    d = _valid_canonical_preview_dict()
    d["files"][0]["duplicates"][0]["occurrences"] = 99
    assert resource_service.validate_safe_report(d)["files"][0]["duplicates"][0]["occurrences"] == 99

    # Unresolved index rejected
    for bad in (True, 1.0, -1, "0"):
        d = _valid_canonical_preview_dict()
        d["files"][0]["unresolved"][0]["index"] = bad
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # Summary counter mutations away from derived contents
    for key, bad_val in [
        ("files", 2),
        ("inputs", 6),
        ("accepted", 3),
        ("auxiliary", 0),
        ("duplicates", 0),
        ("unresolved", 0),
        ("missing", 0),
        ("new", 0),
        ("unchanged", 1),
        ("updated", 0),
    ]:
        d = _valid_canonical_preview_dict()
        d["summary"][key] = bad_val
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # File total_inputs mismatch with items
    d = _valid_canonical_preview_dict()
    d["files"][0]["total_inputs"] = 4  # items sum to 5
    with pytest.raises(ValueError, match="total_inputs"):
        resource_service.validate_safe_report(d)

    # Commit counters mutations
    for key, bad_val in [
        ("recorded", 4),
        ("new_scene_revisions", 1),
        ("unchanged_scene_revisions", 1),
        ("updated_scene_revisions", 0),
        ("new_auxiliary_revisions", 1),
        ("unchanged_auxiliary_revisions", 0),
    ]:
        d = _valid_canonical_commit_dict()
        d["summary"][key] = bad_val
        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d, expected_phase="commit")

    # Multi-file reconciliation test
    d = _valid_canonical_preview_dict()
    f2 = copy.deepcopy(d["files"][0])
    f2["library_key"] = "lib_2"
    f2["accepted"][0]["source_id"] = "f2_sc_new"
    f2["accepted"][1]["source_id"] = "f2_sc_upd"
    d["files"].append(f2)
    d["summary"]["files"] = 2
    d["summary"]["inputs"] = 10
    d["summary"]["accepted"] = 4
    d["summary"]["auxiliary"] = 2
    d["summary"]["duplicates"] = 2
    d["summary"]["unresolved"] = 2
    d["summary"]["new"] = 2
    d["summary"]["updated"] = 2
    assert resource_service.validate_safe_report(d)["summary"]["inputs"] == 10

    # If second file's total_inputs is wrong, fails
    d["files"][1]["total_inputs"] = 4
    with pytest.raises(ValueError):
        resource_service.validate_safe_report(d)


def test_repair_4b_semantic_path_like_identifiers(tmp_path):
    semantic_ids = [
        "../relative/path/id",
        "./local/scene",
        "folder/item_01",
        "folder\\subfolder\\item_02",
        "C:\\semantic\\drive\\scene",
        "file:resource_uri_spec",
        "staged_path",
        "fingerprint",
        "claim_commit_token",
        "attestation",
    ]

    scenes = [{"id": s_id, "label": f"label {s_id}", "theme": "t", "tags": []} for s_id in semantic_ids]
    json_path = tmp_path / "semantic.json"
    _write(json_path, scenes)

    preview = resource_service.preview_import([(str(json_path), "semantic_lib")])
    safe = resource_service.safe_report(preview)

    validated = resource_service.validate_safe_report(safe, expected_phase="preview")
    assert validated == safe

    # Verify all semantic source_ids survived unchanged
    emitted_ids = [acc["source_id"] for acc in validated["files"][0]["accepted"]]
    assert emitted_ids == semantic_ids

    # Verify physical file_path and fingerprint do NOT appear anywhere in the safe report
    safe_json = json.dumps(safe)
    assert str(tmp_path) not in safe_json
    assert "semantic.json" not in safe_json
    assert "fingerprint" not in safe["files"][0]
    assert "file_path" not in safe["files"][0]


def test_repair_4b_structural_private_field_injection():
    private_injections = [
        ("top", {"file_path": "/etc/passwd"}),
        ("top", {"staged_path": "/staged/test"}),
        ("top", {"fingerprint": {"mtime_ns": 123}}),
        ("top", {"mtime_ns": 123}),
        ("top", {"attestation": "token"}),
        ("top", {"unknown_key": "val"}),
        ("summary", {"mtime_ns": 123}),
        ("summary", {"fingerprint": "xyz"}),
        ("file", {"file_path": "/var/log"}),
        ("file", {"fingerprint": "xyz"}),
        ("file", {"staged_path": "/tmp/staged"}),
        ("file", {"mtime_ns": 100}),
        ("accepted", {"file_path": "/tmp/p"}),
        ("accepted", {"staged_path": "/tmp/s"}),
        ("accepted", {"fingerprint": "xyz"}),
        ("accepted", {"device": 1}),
        ("auxiliary", {"file_path": "/tmp/p"}),
        ("auxiliary", {"staged_path": "/tmp/s"}),
        ("duplicate", {"file_path": "/tmp/p"}),
        ("duplicate", {"reason": "dropped"}),
        ("unresolved", {"file_path": "/tmp/p"}),
        ("missing", {"file_path": "/tmp/p"}),
        ("missing", {"reason": "legacy"}),
    ]

    for target, injection in private_injections:
        d = _valid_canonical_preview_dict()
        if target == "top":
            d.update(injection)
        elif target == "summary":
            d["summary"].update(injection)
        elif target == "file":
            d["files"][0].update(injection)
        elif target == "accepted":
            d["files"][0]["accepted"][0].update(injection)
        elif target == "auxiliary":
            d["files"][0]["auxiliary"][0].update(injection)
        elif target == "duplicate":
            d["files"][0]["duplicates"][0].update(injection)
        elif target == "unresolved":
            d["files"][0]["unresolved"][0].update(injection)
        elif target == "missing":
            d["missing_source_entries"][0].update(injection)

        with pytest.raises(ValueError):
            resource_service.validate_safe_report(d)

    # Recursive check on valid reports
    def _assert_no_private_keys(obj):
        forbidden = {
            "file_path", "staged_path", "fingerprint", "mtime_ns",
            "device", "inode", "attestation", "attestation_token",
            "claim_commit_token", "staged_bytes",
        }
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k not in forbidden, f"Forbidden private key found: {k}"
                _assert_no_private_keys(v)
        elif isinstance(obj, list):
            for item in obj:
                _assert_no_private_keys(item)

    _assert_no_private_keys(_valid_canonical_preview_dict())
    _assert_no_private_keys(_valid_canonical_commit_dict())


def test_repair_4b_reason_safety_and_preservation():
    # Build PreviewReport with file read error and legitimate reasons
    unres_read_err = resource_import.UnresolvedItem(
        bucket=resource_import.BUCKET_FILE_READ_ERROR,
        index=0,
        reason="could not read /secret/private/path/to/file.json: Permission denied",
    )
    unres_malformed = resource_import.UnresolvedItem(
        bucket=resource_import.BUCKET_MALFORMED,
        index=1,
        reason="field 'prompt' must be a non-empty string",
    )
    unres_unsupported = resource_import.UnresolvedItem(
        bucket=resource_import.BUCKET_UNSUPPORTED,
        index=2,
        reason="unsupported root schema: expected array or object",
    )

    file_report = resource_import.FileReport(
        file_path="/secret/private/path/to/file.json",
        library_key="test_lib",
        total_inputs=3,
        unresolved=[unres_read_err, unres_malformed, unres_unsupported],
    )
    preview = resource_import.PreviewReport(files=[file_report])

    safe = resource_service.safe_report(preview)

    # 1. Verify BUCKET_FILE_READ_ERROR has sanitized reason
    assert safe["files"][0]["unresolved"][0]["reason"] == "source file could not be read or parsed"
    assert "/secret/private/path" not in json.dumps(safe)

    # 2. Verify legitimate reasons are preserved byte-for-byte
    assert safe["files"][0]["unresolved"][1]["reason"] == "field 'prompt' must be a non-empty string"
    assert safe["files"][0]["unresolved"][2]["reason"] == "unsupported root schema: expected array or object"

    # 3. Verify validate_safe_report round-trip preserves list order and reasons
    validated = resource_service.validate_safe_report(safe, expected_phase="preview")
    assert validated == safe
    json_validated = resource_service.validate_safe_report(json.dumps(safe), expected_phase="preview")
    assert json_validated == safe

    # 4. If someone tries to validate a file_read_error with unsanitized reason, it is rejected
    mutated = copy.deepcopy(safe)
    mutated["files"][0]["unresolved"][0]["reason"] = "could not read /secret/private/path"
    with pytest.raises(ValueError, match="file_read_error bucket must have sanitized reason"):
        resource_service.validate_safe_report(mutated)


def test_browser_scoped_attestation_rejected_by_path_commit(tmp_path):
    path = tmp_path / "resources.json"
    _write(path, [_scene("scene_browser_token", "invented room")])

    preview = resource_service.preview_import(_selection(path))
    token = resource_service.create_browser_attestation(
        preview=preview,
        selection_id="sel_browser_test_123",
        selection_revision=0,
        manifest_digest="0" * 64,
    )

    body = resource_service._preview_body(preview)
    serialized = {
        **body,
        "binding": resource_service._preview_binding(body),
        "attestation": token,
    }

    # Path commit must reject browser-scoped attestation
    with pytest.raises(ValueError, match="scoped for browser selection"):
        resource_service.commit_import(serialized)

    assert db.q("SELECT * FROM resource_library") == []
    assert db.q("SELECT * FROM asset_revision") == []
    claim_path = resource_service._attestation_directory() / f"{token}.claimed"
    assert not claim_path.exists()


def test_commit_selection_import_requires_active_transaction(tmp_path):
    path = tmp_path / "resources.json"
    _write(path, [_scene("scene_tx_test", "invented room")])

    preview = resource_service.preview_import(_selection(path))
    token = resource_service.create_browser_attestation(
        preview=preview,
        selection_id="sel_tx_test_123",
        selection_revision=0,
        manifest_digest="0" * 64,
    )

    with pytest.raises(RuntimeError, match="requires an active caller-owned transaction"):
        resource_service.commit_selection_import(
            preview=preview,
            preview_token=token,
            selection_id="sel_tx_test_123",
            selection_revision=0,
            manifest_digest="0" * 64,
        )


def test_legacy_preview_serialization_matches_baseline_keys_without_browser_fields(tmp_path):
    """Verify that legacy preview serialization matches baseline c2f3d2ea20f0b5e0915b1d22a24c50cde48a6445
    character-for-character in keys, containing no browser-specific fields, while browser preview body isolates them.
    """
    path = tmp_path / "resources.json"
    _write(path, [_scene("scene_legacy_check", "legacy test room")])

    preview = resource_service.preview_import(_selection(path))
    serialized = resource_service.preview_to_dict(preview)
    file_dict = serialized["files"][0]

    baseline_keys = {
        "file_path",
        "library_key",
        "total_inputs",
        "fingerprint",
        "accepted_outcomes",
        "auxiliary_outcomes",
        "duplicate_identifiers",
        "unresolved",
    }
    assert set(file_dict.keys()) == baseline_keys
    assert "effective_auxiliary_kind" not in file_dict
    assert "is_browser_mode" not in file_dict

    # Round trip via legacy preview_from_dict
    restored = resource_service.preview_from_dict(serialized)
    assert restored.files[0].effective_auxiliary_kind is None
    assert restored.files[0].is_browser_mode is False

    # Browser preview body isolates browser fields
    browser_body = resource_service._browser_preview_body(preview)
    browser_file_dict = browser_body["files"][0]
    assert set(browser_file_dict.keys()) == baseline_keys | {"effective_auxiliary_kind", "is_browser_mode"}

    # Attestation created with browser body verifies with browser body and fails with legacy body
    token = resource_service.create_browser_attestation(
        preview=preview,
        selection_id="sel_iso_123",
        selection_revision=0,
        manifest_digest="1" * 64,
    )
    verified = resource_service.verify_browser_attestation(
        body=browser_body,
        token=token,
        selection_id="sel_iso_123",
        selection_revision=0,
        manifest_digest="1" * 64,
    )
    assert verified is not None
    assert verified["selection_id"] == "sel_iso_123"

    # Verifying with legacy body (which lacks browser fields) must fail
    with pytest.raises(ValueError, match="serialized resource preview attestation is invalid"):
        resource_service.verify_browser_attestation(
            body=serialized,
            token=token,
            selection_id="sel_iso_123",
            selection_revision=0,
            manifest_digest="1" * 64,
        )


def test_actual_payload_free_libraries_response_matches_contract_and_fixture(client, tmp_path, monkeypatch):
    """Task 1.6 Item 9: /api/resources/libraries omits raw payload and matches the frontend fixture contract."""
    for table in ("auxiliary_resource", "asset_revision", "resource_library"):
        db.run(f"DELETE FROM {table}")

    fixed_now = "2026-09-17T12:00:00+00:00"
    monkeypatch.setattr(db, "now", lambda: fixed_now)

    lib1_id = resource_store.ensure_library(
        "rooms_studio_gallery",
        display_name="Studio Gallery Rooms",
        kind="rooms",
    )
    resource_store.record_revision(
        lib1_id,
        "room_grand_loft",
        {"name": "Grand Sunlight Loft", "theme": "High ceiling loft with warm natural light"},
        translation={"label": "Grand Sunlight Loft", "scene_theme": "High ceiling loft with warm natural light"},
        coverage={"status": "ready"},
    )
    resource_store.record_revision(
        lib1_id,
        "room_concrete_minimal",
        {"name": "Brutalist Concrete Space", "theme": "Raw grey concrete walls and architectural shadows"},
        translation={"label": "Brutalist Concrete Space", "scene_theme": "Raw grey concrete walls and architectural shadows"},
        coverage={"status": "ready"},
    )

    lib2_id = resource_store.ensure_library(
        "fused_scenes_couture",
        display_name="Summer Couture Scenes",
        kind="fused_scenes",
    )
    resource_store.record_revision(
        lib2_id,
        "scene_silk_slip",
        {"name": "Silk Slip Dress", "prompt": "Ivory Silk Slip Dress Scene"},
        translation={"label": "Silk Slip Dress", "prompt": "Ivory Silk Slip Dress Scene"},
        coverage={"status": "ready"},
    )
    resource_store.record_auxiliary(
        lib2_id,
        "mined_labels",
        payload={"labels": ["silk", "couture"]},
    )

    resp = client.get("/api/resources/libraries")
    assert resp.status_code == 200
    libraries = resp.json()
    assert len(libraries) == 2

    def _assert_no_payload(obj, current_path=""):
        if isinstance(obj, dict):
            assert "payload" not in obj, f"Forbidden 'payload' key leaked at {current_path}"
            for k, v in obj.items():
                _assert_no_payload(v, f"{current_path}.{k}")
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                _assert_no_payload(item, f"{current_path}[{idx}]")

    _assert_no_payload(libraries, "libraries")

    fixture_path = ROOT / "frontend" / "src" / "views" / "__fixtures__" / "actual_payload_free_libraries.json"
    assert fixture_path.is_file(), f"Fixture missing at {fixture_path}"
    fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert isinstance(fixture_data, list)
    assert len(fixture_data) == 2

    _assert_no_payload(fixture_data, "fixture")
    # Assert exact recursive structural equality between backend response and fixture
    assert libraries == fixture_data

    required_lib_keys = {
        "id", "library_key", "display_name", "kind", "created_at",
        "revision_count", "auxiliary_count", "revisions", "auxiliary",
    }
    required_rev_keys = {
        "revision_id", "library_key", "library_id", "source_id",
        "content_digest", "created_at", "translation", "coverage", "readiness",
    }
    required_aux_keys = {
        "auxiliary_id", "library_key", "kind", "content_digest", "created_at",
    }

    for item in libraries + fixture_data:
        assert required_lib_keys.issubset(set(item.keys()))
        for rev in item.get("revisions", []):
            assert required_rev_keys.issubset(set(rev.keys()))
            assert "payload" not in rev
        for aux in item.get("auxiliary", []):
            assert required_aux_keys.issubset(set(aux.keys()))
            assert "payload" not in aux
