"""Command-line interface for manifest-driven benchmark runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from clifft_bench.manifest import load_campaign, load_suite
from clifft_bench.qv import load_qv_campaign
from clifft_bench.qv_results import finalize_qv_execution
from clifft_bench.qv_runner import run_qv_campaign
from clifft_bench.results import finalize_execution
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

    finalize = commands.add_parser(
        "finalize", help="generate an execution index and plot-ready tables"
    )
    finalize.add_argument("--campaign", required=True, type=Path)
    finalize.add_argument("--execution-id", required=True)
    finalize.add_argument("--output-dir", required=True, type=Path)
    finalize.add_argument("results", nargs="+", type=Path)

    qv_run = commands.add_parser("qv-run", help="execute a QV multicore campaign")
    qv_run.add_argument("--campaign", required=True, type=Path)
    qv_run.add_argument("--environment-root", required=True, type=Path)
    qv_run.add_argument("--circuit-dir", required=True, type=Path)
    qv_run.add_argument("--output", required=True, type=Path)
    qv_run.add_argument("--execution-id", required=True)
    qv_run.add_argument("--placement", required=True, type=int)
    qv_run.add_argument("--replica", required=True, type=int)

    qv_finalize = commands.add_parser(
        "qv-finalize", help="validate and derive tables from a QV multicore execution"
    )
    qv_finalize.add_argument("--campaign", required=True, type=Path)
    qv_finalize.add_argument("--execution-id", required=True)
    qv_finalize.add_argument("--circuit-dir", required=True, type=Path)
    qv_finalize.add_argument("--output-dir", required=True, type=Path)
    qv_finalize.add_argument("results", nargs="+", type=Path)
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
        root / "examples/result.v1.json",
        *sorted(root.glob("campaigns/*/campaign.v1.json")),
        *sorted(root.glob("campaigns/*/qv-campaign.v1.json")),
    ]


def _validate(paths: list[Path]) -> int:
    selected = paths or _default_validation_paths()
    for path in selected:
        resolved = _resolve(path)
        document = validate_path(resolved)
        if document["schema_version"] == "clifft-bench/run/v1":
            load_suite(resolved)
        if document["schema_version"] == "clifft-bench/campaign/v1":
            load_campaign(resolved)
        if document["schema_version"] == "clifft-bench/qv-campaign/v1":
            load_qv_campaign(resolved)
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
    for case in document["cases"]:
        if case["status"] == "success":
            continue
        error = case.get("error") or {}
        print(
            f"Failed {case['case_id']} during {error.get('phase', 'unknown')}: "
            f"{error.get('type', 'Error')}: {error.get('message', 'no details')}",
            file=sys.stderr,
        )
    print(f"Result: {output.resolve()} ({successes}/{len(document['cases'])} successful)")
    return 0 if successes == len(document["cases"]) else 1


def _finalize(args: argparse.Namespace) -> int:
    campaign = load_campaign(_resolve(args.campaign))
    index = finalize_execution(
        campaign,
        execution_id=args.execution_id,
        raw_paths=[_resolve(path) for path in args.results],
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(index, indent=2, sort_keys=True))
    return 0


def _qv_run(args: argparse.Namespace) -> int:
    campaign = load_qv_campaign(_resolve(args.campaign))
    document = run_qv_campaign(
        campaign,
        environment_root=args.environment_root.resolve(),
        circuit_dir=args.circuit_dir.resolve(),
        output_path=args.output.resolve(),
        execution_id=args.execution_id,
        placement=args.placement,
        replica=args.replica,
    )
    successes = sum(case["status"] == "success" for case in document["cases"])
    print(
        f"Result: {args.output.resolve()} ({successes}/{len(document['cases'])} successful)"
    )
    return 0 if successes == len(document["cases"]) else 1


def _qv_finalize(args: argparse.Namespace) -> int:
    campaign = load_qv_campaign(_resolve(args.campaign))
    index = finalize_qv_execution(
        campaign,
        execution_id=args.execution_id,
        raw_paths=[_resolve(path) for path in args.results],
        circuit_dir=args.circuit_dir.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(index, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            return _validate(args.paths)
        if args.command == "list":
            return _list(args.run_manifest, args.as_json)
        if args.command == "run":
            return _run(args)
        if args.command == "finalize":
            return _finalize(args)
        if args.command == "qv-run":
            return _qv_run(args)
        if args.command == "qv-finalize":
            return _qv_finalize(args)
        raise AssertionError(f"unhandled command {args.command!r}")
    except (SchemaValidationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
