"""Generate a private resource coverage and adoption report from source libraries.

Accounts for every discovered file and field observation in the operator-selected
source directory and auxiliary files, cross-referencing them against the preparation
contract in `backend.resource_prompts`.

Outputs a structural-only JSON report without publishing source prose, values,
external identity keys, or absolute paths.

Examples:
    python scripts/report_resource_coverage.py path/to/sources
    python scripts/report_resource_coverage.py path/to/sources --output data/resource-coverage-report.json
    python scripts/report_resource_coverage.py path/to/sources --aux-path path/to/translation_map.json:translation_map
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from resource_ledger import inventory_source_dir  # noqa: E402
from resource_coverage import (  # noqa: E402
    build_coverage_report,
    save_coverage_report,
)


def _parse_aux_path(arg: str) -> tuple[Path, str]:
    """Parse a PATH or PATH:KIND or PATH=KIND specification.

    Handles Windows drive letters (e.g. C:\\path\\to\\file.json) without
    misinterpreting the drive colon as a kind delimiter.
    """
    if "=" in arg:
        p_str, k_str = arg.rsplit("=", 1)
        return Path(p_str), k_str.strip()

    last_colon = arg.rfind(":")
    if last_colon != -1:
        has_drive_letter = len(arg) >= 2 and arg[1] == ":" and arg[0].isalpha()
        if not (has_drive_letter and last_colon == 1):
            p_str = arg[:last_colon]
            k_str = arg[last_colon + 1:]
            return Path(p_str), k_str.strip()

    return Path(arg), ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a private resource coverage and adoption report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source_dir",
        type=str,
        nargs="?",
        default=None,
        help="Path to operator-selected source directory containing library JSON files",
    )
    parser.add_argument(
        "--source-dir",
        dest="source_dir_opt",
        type=str,
        default=None,
        help="Alternative flag for source directory",
    )
    parser.add_argument(
        "--aux-path",
        action="append",
        default=[],
        help="Auxiliary JSON file path, optionally with kind (e.g. --aux-path path/to/map.json:translation_map)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=ROOT / "data" / "resource-coverage-report.json",
        help="Output JSON report path (defaults to data/resource-coverage-report.json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON report to standard output",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress terminal summary output",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    src_dir = args.source_dir_opt or args.source_dir
    if not src_dir:
        parser.error("source_dir is required (either as positional argument or via --source-dir)")

    aux_paths: list[Path] = []
    aux_kinds: dict[Path, str] = {}
    for item in args.aux_path:
        p, k = _parse_aux_path(item)
        aux_paths.append(p)
        if k:
            aux_kinds[p] = k

    ledger = inventory_source_dir(
        source_dir=src_dir,
        aux_paths=tuple(aux_paths),
        aux_kinds=aux_kinds if aux_kinds else None,
    )

    report = build_coverage_report(ledger)
    out_path = save_coverage_report(report, args.output)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=True, indent=2))

    if not args.quiet and not args.json:
        print(f"Resource Coverage Report saved to: {out_path}")
        print(f"Source Directory: {report.source_dir}")
        print(f"Total Discovered Files: {report.total_files}")
        print(f"  - Usable Scene Resources: {report.usable_files}")
        print(f"  - Auxiliary Resources:    {report.auxiliary_files}")
        print(f"  - Pending Adoption:       {report.pending_files}")
        print(f"Total Field Observations: {report.total_field_observations}")
        print(f"  - Mapped Fields:          {report.mapped_field_observations}")
        print(f"  - Unmapped Fields:        {report.unmapped_field_observations}")
        print(f"  - Intentionally Unused:   {report.intentionally_unused_field_observations}")
        print(f"Coverage Complete: {report.coverage_complete}")
        print(f"Adoption Complete: {report.adoption_complete}")

        if report.pending_files > 0:
            print("\nPending Resources:")
            for e in report.entries:
                if e.coverage_disposition == "pending":
                    print(f"  * {e.file_stem} ({e.declared_kind or 'undeclared'}): {e.disposition_reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
