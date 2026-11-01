"""Command-line interface for campaign-driven benchmark runs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from clifft_bench.bootstrap import bootstrap
from clifft_bench.config import (
    _validate_software,
    _validate_workloads,
    campaign_path,
    load_suite,
    read_toml,
)
from clifft_bench.results import finalize_execution
from clifft_bench.runner import run_suite
from clifft_bench.schema import SchemaValidationError, repository_root, validate_path

DEFAULT_CAMPAIGN = "smoke"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clifft-bench",
        description="Run reproducible single-CPU near-Clifford simulator benchmarks.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate TOML configuration or raw results")
    validate.add_argument("paths", nargs="*", type=Path)

    listing = commands.add_parser("list", help="list cases without importing simulators")
    listing.add_argument("campaign", nargs="?", default=DEFAULT_CAMPAIGN)
    listing.add_argument("--json", action="store_true", dest="as_json")

    run = commands.add_parser("run", help="execute a campaign serially")
    run.add_argument("campaign", nargs="?", default=DEFAULT_CAMPAIGN)
    run.add_argument("--python", dest="python_executable", help="local smoke interpreter override")
    run.add_argument("--require-ec2", action="store_true")
    run.add_argument("--output", type=Path)
    run.add_argument("--case", help="regular expression matched against case IDs")
    run.add_argument("--cpu", type=int, help="logical CPU to request on affinity-capable systems")
    run.add_argument("--memory-limit-gib", type=float)
    run.add_argument("--min-sample-seconds", type=float)
    run.add_argument("--repetitions", type=int)

    finalize = commands.add_parser(
        "finalize", help="generate an execution index and plot-ready tables"
    )
    finalize.add_argument("campaign")
    finalize.add_argument("--execution-id", required=True)
    finalize.add_argument("--output-dir", required=True, type=Path)
    finalize.add_argument("results", nargs="+", type=Path)

    setup = commands.add_parser("bootstrap", help="install selected isolated environments")
    setup.add_argument("campaign", nargs="?", default="release-v1")

    return parser


def _resolve(path: Path) -> Path:
    if path.is_absolute():
        return path
    direct = path.resolve()
    if direct.exists():
        return direct
    return (repository_root() / path).resolve()


def _validate(paths: list[Path]) -> int:
    selected = paths or sorted((repository_root() / "campaigns").glob("*.toml"))
    for path in selected:
        resolved = _resolve(path)
        if resolved.suffix == ".toml":
            document = read_toml(resolved)
            if "implementations" in document:
                _validate_software(document)
            elif "workloads" in document:
                _validate_workloads(document)
            else:
                load_suite(resolved)
        else:
            validate_path(resolved)
        print(f"valid: {resolved}")
    return 0


def _list(campaign: str, as_json: bool) -> int:
    suite = load_suite(campaign_path(campaign))
    rows = []
    for case in suite.cases:
        rows.append(
            {
                "case_id": case.id,
                "variant": case.definition["variant_id"],
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
                f"{row['case_id']}: variant={row['variant']} workload={row['workload']} "
                f"implementation={row['implementation']} mode={row['mode']} "
                f"batch={row['batch_size']} shots/call={row['shots_per_call']}"
            )
    return 0


def _run(args: argparse.Namespace) -> int:
    suite = load_suite(campaign_path(args.campaign))
    output = args.output
    if output is None:
        output = repository_root() / "results" / f"{suite.run['profile_id']}.json"
    document = run_suite(
        suite,
        output_path=output.resolve(),
        case_pattern=args.case,
        cpu=args.cpu,
        memory_limit_gib=args.memory_limit_gib,
        min_sample_seconds=args.min_sample_seconds,
        repetitions=args.repetitions,
        python_executable=args.python_executable,
        require_ec2=args.require_ec2,
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
    suite = load_suite(campaign_path(args.campaign))
    index = finalize_execution(
        suite,
        execution_id=args.execution_id,
        raw_paths=[_resolve(path) for path in args.results],
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(index, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "bootstrap":
            bootstrap(load_suite(campaign_path(args.campaign)))
            return 0
        if args.command == "validate":
            return _validate(args.paths)
        if args.command == "list":
            return _list(args.campaign, args.as_json)
        if args.command == "run":
            return _run(args)
        if args.command == "finalize":
            return _finalize(args)
        raise AssertionError(f"unhandled command {args.command!r}")
    except (SchemaValidationError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
