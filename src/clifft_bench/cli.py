"""Command-line interface for manifest-driven benchmark runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from clifft_bench.manifest import load_suite
from clifft_bench.runner import run_suite
from clifft_bench.schema import SchemaValidationError, repository_root, validate_path

DEFAULT_RUN_MANIFEST = Path("manifests/run-smoke.v1.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clifft-bench",
        description="Run reproducible single-CPU near-Clifford simulator benchmarks.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate manifests or raw results")
    validate.add_argument("paths", nargs="*", type=Path)

    listing = commands.add_parser("list", help="list cases without importing simulators")
    listing.add_argument("--run-manifest", type=Path, default=DEFAULT_RUN_MANIFEST)
    listing.add_argument("--json", action="store_true", dest="as_json")

    run = commands.add_parser("run", help="execute a manifest-defined suite serially")
    run.add_argument("--run-manifest", type=Path, default=DEFAULT_RUN_MANIFEST)
    run.add_argument("--output", type=Path)
    run.add_argument("--case", help="regular expression matched against case IDs")
    run.add_argument("--cpu", type=int, help="logical CPU to request on affinity-capable systems")
    run.add_argument("--min-sample-seconds", type=float)
    run.add_argument("--repetitions", type=int)
    return parser


def _resolve(path: Path) -> Path:
    if path.is_absolute():
        return path
    direct = path.resolve()
    if direct.exists():
        return direct
    return (repository_root() / path).resolve()


def _default_validation_paths() -> list[Path]:
    root = repository_root()
    return [
        root / "manifests/workloads.v1.json",
        root / "manifests/software.v1.json",
        root / "manifests/run-smoke.v1.json",
        root / "manifests/run-phase1.v1.json",
        root / "examples/result.v1.json",
    ]


def _validate(paths: list[Path]) -> int:
    selected = paths or _default_validation_paths()
    for path in selected:
        resolved = _resolve(path)
        document = validate_path(resolved)
        if document["schema_version"] == "clifft-bench/run/v1":
            load_suite(resolved)
        print(f"valid: {resolved}")
    return 0


def _list(run_manifest: Path, as_json: bool) -> int:
    suite = load_suite(_resolve(run_manifest))
    rows = []
    for case in suite.cases:
        rows.append(
            {
                "case_id": case.id,
                "workload": case.workload.id,
                "implementation": case.implementation.id,
                "adapter": case.implementation.definition["adapter"],
                "mode": case.definition["execution"]["mode"],
                "batch_size": case.definition["execution"]["batch_size"],
                "shots_per_call": case.definition["shots_per_call"],
            }
        )
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(
                f"{row['case_id']}: workload={row['workload']} "
                f"implementation={row['implementation']} mode={row['mode']} "
                f"batch={row['batch_size']} shots/call={row['shots_per_call']}"
            )
    return 0


def _run(args: argparse.Namespace) -> int:
    suite = load_suite(_resolve(args.run_manifest))
    output = args.output
    if output is None:
        output = repository_root() / "results" / f"{suite.run['profile_id']}.json"
    document = run_suite(
        suite,
        output_path=output.resolve(),
        case_pattern=args.case,
        cpu=args.cpu,
        min_sample_seconds=args.min_sample_seconds,
        repetitions=args.repetitions,
    )
    successes = sum(case["status"] == "success" for case in document["cases"])
    print(f"Result: {output.resolve()} ({successes}/{len(document['cases'])} successful)")
    return 0 if successes == len(document["cases"]) else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            return _validate(args.paths)
        if args.command == "list":
            return _list(args.run_manifest, args.as_json)
        if args.command == "run":
            return _run(args)
        raise AssertionError(f"unhandled command {args.command!r}")
    except (SchemaValidationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
