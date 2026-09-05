"""Command-line entry point for asset library import.

Invokes the single import implementation in backend.importer, ensuring the app
operation and the CLI produce identical seed content.

Usage:
    python scripts/import_assets.py <source_dir> <map_path> --config <config.json>
        [--data-dir <data_dir>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add repo root to sys.path if not present
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.importer import import_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import external asset libraries into seed files."
    )
    parser.add_argument(
        "source_dir",
        nargs="?",
        default=None,
        help="Path to source directory carrying asset JSON files",
    )
    parser.add_argument(
        "map_path",
        nargs="?",
        default=None,
        help="Path to translation map JSON file",
    )
    parser.add_argument(
        "--source-dir",
        dest="opt_source_dir",
        default=None,
        help="Path to source directory carrying asset JSON files",
    )
    parser.add_argument(
        "--map-path",
        dest="opt_map_path",
        default=None,
        help="Path to translation map JSON file",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Target data directory where seed files are written",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to an existing config.json. Required: the import registers "
            "each seed file it writes, and the two are one operation"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    source_dir = args.opt_source_dir or args.source_dir
    map_path = args.opt_map_path or args.map_path

    if not source_dir or not map_path:
        parser.error("Both source_dir and map_path are required")
    if not args.config:
        parser.error("--config is required: a seed file is written and registered together")

    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        parser.error(f"--config names no file: {cfg_path}")
    config_dict = json.loads(cfg_path.read_text(encoding="utf-8"))

    try:
        report = import_source(
            source_dir=source_dir,
            map_path=map_path,
            data_dir=args.data_dir,
            config=config_dict,
        )
    except Exception as exc:
        sys.stderr.write(f"Error during import: {exc}\n")
        return 1

    cfg_path.write_text(
        json.dumps(config_dict, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # Print summary report (counts and identifiers only, never entry text)
    print("Asset Import Summary:")
    print(f"  Accepted:  {report['accepted']}")
    print(f"  Refused:   {report['refused']}")
    print(f"  Written:   {report['written']}")
    print(f"  Created:   {report['created']}")
    print(f"  Updated:   {report['updated']}")
    print(f"  Unchanged: {report['unchanged']}")
    print(f"  Orphaned:  {report['orphaned']}")
    print(f"  Skipped empty theme: {report['skipped_empty']}")
    for refusal in report["refused_libraries"]:
        print(f"  Refused library: {refusal['library']} ({refusal['reason']})")
    if report["refused_identifiers"]:
        print(f"  Refused identifiers: {', '.join(report['refused_identifiers'])}")

    for dest, info in report.get("destinations", {}).items():
        print(f"  Destination {dest}: {info['written']} written ({info['created']} created, {info['updated']} updated, {info['unchanged']} unchanged, {info['orphaned']} orphaned)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
