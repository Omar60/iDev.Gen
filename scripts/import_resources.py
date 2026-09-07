"""Preview and commit the SQLite resource import through one service boundary.

Examples:
    python scripts/import_resources.py preview \
        --selection PATH=LIBRARY_KEY --preview-out preview.json \
        --report-out report.json
    python scripts/import_resources.py commit \
        --preview preview.json --report-out report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import db  # noqa: E402
import resource_service  # noqa: E402
from resource_import import CommitAborted, StaleFingerprintError  # noqa: E402


def _data_dir(config_path: str | None, override: str | None) -> Path:
    if override:
        value = Path(override)
    else:
        configured = os.environ.get("IDEVGEN_DATA_DIR")
        if configured:
            value = Path(configured)
        else:
            path = Path(config_path or os.environ.get("IDEVGEN_CONFIG") or ROOT / "config.json")
            config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            value = Path(config.get("data_dir") or "data")
    return value if value.is_absolute() else ROOT / value


def _selection(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("selection must be PATH=LIBRARY_KEY")
    path, library_key = value.rsplit("=", 1)
    if not path or not library_key:
        raise argparse.ArgumentTypeError("selection must contain a path and library key")
    return path, library_key


def _connect(args: argparse.Namespace) -> None:
    db.connect(_data_dir(args.config, args.data_dir) / "idevgen.db")


def _print_report(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=True, indent=2))


def _preview(args: argparse.Namespace) -> int:
    _connect(args)
    preview = resource_service.preview_import(args.selection)
    args.preview_out.parent.mkdir(parents=True, exist_ok=True)
    args.preview_out.write_text(
        json.dumps(resource_service.preview_to_dict(preview), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    report = resource_service.safe_report(preview)
    resource_service.write_report_artifact(preview, args.report_out)
    _print_report(report)
    return 0


def _commit(args: argparse.Namespace) -> int:
    _connect(args)
    preview = json.loads(args.preview.read_text(encoding="utf-8"))
    try:
        result = resource_service.commit_import(preview)
    except StaleFingerprintError:
        print("error: resource source changed; create a fresh preview", file=sys.stderr)
        return 2
    except (CommitAborted, ValueError, OSError) as exc:
        print(f"error: resource commit failed: {exc}", file=sys.stderr)
        return 1
    report = resource_service.safe_report(result)
    resource_service.write_report_artifact(result, args.report_out)
    _print_report(report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview and commit SQLite resource imports.")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    preview = subparsers.add_parser("preview", help="preview selected source files")
    preview.add_argument("--selection", action="append", type=_selection, required=True,
                         help="source selection in PATH=LIBRARY_KEY form; repeatable")
    preview.add_argument("--preview-out", type=Path, required=True,
                         help="private serialized preview used by commit")
    preview.add_argument("--report-out", type=Path, required=True,
                         help="safe report artifact path")
    preview.add_argument("--data-dir", default=None)
    preview.add_argument("--config", default=None)
    preview.set_defaults(handler=_preview)

    commit = subparsers.add_parser("commit", help="commit a serialized preview")
    commit.add_argument("--preview", type=Path, required=True)
    commit.add_argument("--report-out", type=Path, required=True,
                         help="safe report artifact path")
    commit.add_argument("--data-dir", default=None)
    commit.add_argument("--config", default=None)
    commit.set_defaults(handler=_commit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
