"""Versioned, self-contained personal looks backed by the wardrobe catalogue."""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from uuid import uuid4

import db
from backend import resource_store


class SavedLookError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _invalid() -> None:
    raise SavedLookError(422, "invalid_look", "Look data is invalid.")


def _write_conflict() -> None:
    raise SavedLookError(409, "look_write_conflict", "Saved look could not be saved.")


def _stored_data_invalid() -> None:
    raise SavedLookError(500, "look_data_invalid", "Saved look data is invalid.")


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _valid_key(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and value == value.strip()
        and not _has_control(value)
    )


def _valid_name(value: object) -> bool:
    return _valid_key(value)


def _valid_wording(value: object, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and value == value.strip()
    )


def _validate_snapshot(snapshot: object, row: dict) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "look_id", "version", "content_digest", "appearance", "outfit",
    }:
        _stored_data_invalid()
    if (
        not _valid_key(row["look_key"])
        or not _valid_name(row["name"])
        or type(row["version"]) is not int
        or row["version"] < 1
        or snapshot["look_id"] != row["look_key"]
        or type(snapshot["version"]) is not int
        or snapshot["version"] != row["version"]
        or not isinstance(snapshot["appearance"], str)
    ):
        _stored_data_invalid()

    outfit = snapshot["outfit"]
    if outfit is not None:
        if (
            not isinstance(outfit, dict)
            or set(outfit) != {"outfit_key", "garments"}
            or not _valid_key(outfit["outfit_key"])
            or not isinstance(outfit["garments"], list)
            or not outfit["garments"]
        ):
            _stored_data_invalid()
        seen: set[str] = set()
        for garment in outfit["garments"]:
            if (
                not isinstance(garment, dict)
                or set(garment) != {"key", "wording", "aside"}
                or not _valid_key(garment["key"])
                or garment["key"] in seen
                or not _valid_wording(garment["wording"])
                or not _valid_wording(garment["aside"], allow_empty=True)
            ):
                _stored_data_invalid()
            seen.add(garment["key"])

    digest = snapshot["content_digest"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or digest != resource_store.canonical_digest({
            "appearance": snapshot["appearance"],
            "outfit": outfit,
        })
    ):
        _stored_data_invalid()
    return snapshot


def _read_row(row: dict) -> dict:
    try:
        snapshot = _validate_snapshot(json.loads(row["snapshot_json"]), row)
    except SavedLookError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        _stored_data_invalid()
    return {
        "key": row["look_key"],
        "version": row["version"],
        "name": row["name"],
        "content_digest": snapshot["content_digest"],
        "appearance": snapshot["appearance"],
        "outfit": snapshot["outfit"],
    }


def list_latest() -> list[dict]:
    rows = db.q(
        """SELECT v.* FROM saved_look_version AS v
           JOIN (SELECT look_key, MAX(version) AS version
                 FROM saved_look_version GROUP BY look_key) AS latest
             ON latest.look_key = v.look_key AND latest.version = v.version
           ORDER BY v.look_key"""
    )
    return [
        {
            "key": view["key"],
            "version": view["version"],
            "name": view["name"],
            "content_digest": view["content_digest"],
        }
        for view in (_read_row(row) for row in rows)
    ]


def get_version(look_key: str, version: int) -> dict:
    if not _valid_key(look_key) or type(version) is not int or version < 1:
        _invalid()
    row = db.one(
        "SELECT * FROM saved_look_version WHERE look_key = ? AND version = ?",
        look_key, version,
    )
    if row is None:
        raise SavedLookError(404, "look_not_found", "Saved look was not found.")
    return _read_row(row)


def _history(look_key: str) -> list[dict]:
    rows = db.q(
        "SELECT * FROM saved_look_version WHERE look_key = ? ORDER BY version DESC",
        look_key,
    )
    return [_read_row(row) for row in rows]


def _unique_key(prefix: str, table: str, reserved: set[str]) -> str:
    column = "look_key" if table == "saved_look_version" else "key"
    while True:
        key = f"{prefix}-{uuid4().hex}"
        if key not in reserved and db.one(f"SELECT 1 FROM {table} WHERE {column} = ?", key) is None:
            reserved.add(key)
            return key


def _known_garment(key: str, history: list[dict]) -> dict | None:
    for look in history:
        outfit = look["outfit"]
        if outfit is not None:
            for garment in outfit["garments"]:
                if garment["key"] == key:
                    return garment
    row = db.one("SELECT key, wording, aside FROM garment WHERE key = ?", key)
    if row is None:
        return None
    garment = {"key": row["key"], "wording": row["wording"], "aside": row["aside"]}
    if not _valid_wording(garment["wording"]) or not _valid_wording(garment["aside"], allow_empty=True):
        _stored_data_invalid()
    return garment


def _resolve_outfit_key(outfit_key: object, history: list[dict]) -> dict:
    if not _valid_key(outfit_key):
        _invalid()
    for look in history:
        outfit = look["outfit"]
        if outfit is not None and outfit["outfit_key"] == outfit_key:
            return outfit

    row = db.one("SELECT key, garments FROM outfit WHERE key = ?", outfit_key)
    if row is None:
        _invalid()
    keys = row["garments"].split(",")
    if not keys or any(not _valid_key(key) for key in keys) or len(set(keys)) != len(keys):
        _stored_data_invalid()
    garments = []
    for key in keys:
        garment = _known_garment(key, history)
        if garment is None:
            _invalid()
        garments.append(garment)
    return {"outfit_key": row["key"], "garments": garments}


def _resolve_garments(
    definitions: object,
    history: list[dict],
    pending: list[dict],
    reserved: set[str],
) -> list[dict]:
    if not isinstance(definitions, list):
        _invalid()
    garments = []
    seen: set[str] = set()
    for definition in definitions:
        if not isinstance(definition, dict) or set(definition) - {"key", "wording", "aside"}:
            _invalid()
        key = definition.get("key")
        has_wording = "wording" in definition
        has_aside = "aside" in definition
        if key is not None and not _valid_key(key):
            _invalid()
        if key is None:
            if not has_wording:
                _invalid()
            wording = definition["wording"]
            aside = definition.get("aside", "")
            if not _valid_wording(wording) or not _valid_wording(aside, allow_empty=True):
                _invalid()
            garment = {"key": None, "wording": wording, "aside": aside}
            pending.append(garment)
        else:
            original = _known_garment(key, history)
            if original is None:
                _invalid()
            if not has_wording and not has_aside:
                garment = original
            else:
                wording = definition["wording"] if has_wording else original["wording"]
                aside = definition["aside"] if has_aside else original["aside"]
                if not _valid_wording(wording) or not _valid_wording(aside, allow_empty=True):
                    _invalid()
                if wording == original["wording"] and aside == original["aside"]:
                    garment = original
                else:
                    garment = {"key": None, "wording": wording, "aside": aside}
                    pending.append(garment)
        if garment["key"] is not None and garment["key"] in seen:
            _invalid()
        if garment["key"] is not None:
            seen.add(garment["key"])
        garments.append(garment)
    for garment in pending:
        garment["key"] = _unique_key("garment", "garment", reserved)
        if garment["key"] in seen:
            _invalid()
        seen.add(garment["key"])
    return garments


def _reuse_outfit(garments: list[dict], history: list[dict]) -> dict | None:
    for look in history:
        outfit = look["outfit"]
        if outfit is not None and outfit["garments"] == garments:
            return outfit
    joined = ",".join(garment["key"] for garment in garments)
    row = db.one("SELECT key FROM outfit WHERE garments = ? ORDER BY key LIMIT 1", joined)
    if row is not None:
        if not _valid_key(row["key"]):
            _stored_data_invalid()
        return {"outfit_key": row["key"], "garments": garments}
    return None


def _save(
    payload: dict,
    *,
    look_key: str | None,
    version: int,
    history: list[dict],
    prior: dict | None,
) -> dict:
    name = payload.get("name")
    if not _valid_name(name):
        _invalid()
    appearance = payload.get("appearance", prior["appearance"] if prior else "")
    if not isinstance(appearance, str):
        _invalid()

    if "outfit_key" in payload and "garments" in payload:
        _invalid()
    pending_garments: list[dict] = []
    pending_outfit: dict | None = None
    reserved: set[str] = set()
    if "outfit_key" in payload:
        outfit_key = payload["outfit_key"]
        outfit = None if outfit_key is None else _resolve_outfit_key(outfit_key, history)
    elif "garments" in payload:
        definitions = payload["garments"]
        garments = _resolve_garments([] if definitions is None else definitions, history, pending_garments, reserved)
        if not garments:
            outfit = None
        else:
            outfit = _reuse_outfit(garments, history)
            if outfit is None:
                outfit_key = _unique_key("outfit", "outfit", reserved)
                outfit = {"outfit_key": outfit_key, "garments": garments}
                pending_outfit = {
                    "key": outfit_key,
                    "label": name,
                    "garments": ",".join(garment["key"] for garment in garments),
                }
    else:
        outfit = prior["outfit"] if prior is not None else None

    if look_key is None:
        look_key = _unique_key("look", "saved_look_version", reserved)
    digest = resource_store.canonical_digest({"appearance": appearance, "outfit": outfit})
    snapshot = {
        "look_id": look_key,
        "version": version,
        "content_digest": digest,
        "appearance": appearance,
        "outfit": outfit,
    }
    created_at = db.now()
    for garment in pending_garments:
        db.run(
            "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
            garment["key"], garment["wording"], garment["aside"], created_at,
        )
    if pending_outfit is not None:
        db.run(
            "INSERT INTO outfit (key, label, garments, created_at) VALUES (?, ?, ?, ?)",
            pending_outfit["key"], pending_outfit["label"], pending_outfit["garments"], created_at,
        )
    db.run(
        """INSERT INTO saved_look_version
           (look_key, version, name, snapshot_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        look_key, version, name,
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        created_at,
    )
    return {
        "key": look_key,
        "version": version,
        "name": name,
        "content_digest": digest,
        "appearance": appearance,
        "outfit": outfit,
    }


def create(payload: dict) -> dict:
    try:
        with db.transaction():
            return _save(payload, look_key=None, version=1, history=[], prior=None)
    except sqlite3.IntegrityError:
        _write_conflict()


def create_version(look_key: str, expected_version: int, payload: dict) -> dict:
    if (
        not _valid_key(look_key)
        or type(expected_version) is not int
        or expected_version < 1
    ):
        _invalid()
    try:
        with db.transaction():
            history = _history(look_key)
            if not history:
                raise SavedLookError(404, "look_not_found", "Saved look was not found.")
            prior = history[0]
            if prior["version"] != expected_version:
                raise SavedLookError(409, "look_version_stale", "Saved look changed; reload before saving.")
            return _save(
                payload,
                look_key=look_key,
                version=prior["version"] + 1,
                history=history,
                prior=prior,
            )
    except sqlite3.IntegrityError:
        _write_conflict()
