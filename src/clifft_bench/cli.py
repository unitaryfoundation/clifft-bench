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
    campaign_path,
    load_suite,
    read_toml,
    repository_root,
    validate_software,
    validate_workloads,
)
from clifft_bench.results import export_csv, read_result
from clifft_bench.runner import run_suite

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
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--case", help="regular expression matched against case IDs")
    run.add_argument("--cpu", type=int, help="logical CPU to request on affinity-capable systems")
    run.add_argument("--memory-limit-gib", type=float)
    run.add_argument("--min-sample-seconds", type=float)
    run.add_argument("--repetitions", type=int)

    csv_command = commands.add_parser("csv", help="regenerate the case table from raw evidence")
    csv_command.add_argument("raw_json", type=Path)
    csv_command.add_argument("--output", type=Path)

    report = commands.add_parser(
        "report", help="report one execution's release and tool comparisons"
    )
    report.add_argument("execution_dir", type=Path)
    report.add_argument("--output-dir", type=Path)
    report.add_argument("--check", action="store_true", help="validate without writing files")

    setup = commands.add_parser("bootstrap", help="install selected isolated environments")
    setup.add_argument("campaign", nargs="?", default=DEFAULT_CAMPAIGN)

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
                validate_software(document)
            elif "workloads" in document:
                validate_workloads(document)
            else:
                load_suite(resolved)
        else:
            read_result(resolved)
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
                f"implementation={row['implementation']} "
                f"batch={row['batch_size']} shots/call={row['shots_per_call']}"
            )
    return 0


def _run(args: argparse.Namespace) -> int:
    suite = load_suite(campaign_path(args.campaign))
    output = args.output_dir
    document = run_suite(
        suite,
        output_dir=output.resolve(),
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
        if args.command == "csv":
            document = read_result(args.raw_json)
            output = args.output or args.raw_json.with_name("cases.csv")
            if output.resolve() == args.raw_json.resolve():
                raise ValueError("CSV output cannot overwrite raw evidence")
            export_csv(document, output)
            return 0
        if args.command == "report":
            from clifft_bench.report import report_execution

            print(
                report_execution(args.execution_dir, output_dir=args.output_dir, check=args.check)
            )
            return 0
        raise AssertionError(f"unhandled command {args.command!r}")
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
