"""Audit the recurring release campaign's finalized comparison contract."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from clifft_bench.calibration import BATCH_CALIBRATION_CANDIDATES
from clifft_bench.manifest import Suite, load_suite
from clifft_bench.schema import repository_root

CALIBRATED_VARIANTS = {
    "clifft-current-calibrated",
    "symft-calibrated",
}
EXPECTED_VARIANTS = {
    "clifft-previous",
    "clifft-current",
    *CALIBRATED_VARIANTS,
    "symft-single",
}
EXPECTED_COMPARISON_PAIRS = {
    "current-vs-previous": ("clifft-previous", "clifft-current"),
    "alternatives-vs-current": (
        "clifft-current-calibrated",
        "symft-calibrated",
    ),
    "scalar-alternatives-vs-current": ("clifft-current", "symft-single"),
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def _row_key(row: dict[str, str]) -> tuple[str, str, str]:
    return row["placement"], row["replica"], row["workload_id"]


def _require_fields(
    row: dict[str, str], expected: dict[str, str], *, context: str
) -> None:
    mismatches = [
        f"{field}={row.get(field)!r} (expected {value!r})"
        for field, value in expected.items()
        if row.get(field) != value
    ]
    if mismatches:
        raise ValueError(f"{context}: " + "; ".join(mismatches))


def _calibration_evidence(
    output_dir: Path,
    expected_raw_count: int,
) -> dict[tuple[str, str], int]:
    evidence: dict[tuple[str, str], int] = {}
    raw_paths = sorted((output_dir / "raw").glob("*-raw.json"))
    if len(raw_paths) != expected_raw_count:
        raise ValueError(
            f"release audit expected {expected_raw_count} raw results, "
            f"received {len(raw_paths)}"
        )

    for raw_path in raw_paths:
        document = json.loads(raw_path.read_text())
        result_id = str(document["run"]["id"])
        for case in document["cases"]:
            if case["variant_id"] not in CALIBRATED_VARIANTS:
                continue
            case_id = str(case["case_id"])
            setup = case.get("setup")
            calibration = (
                setup.get("runtime_metadata", {}).get("batch_calibration")
                if isinstance(setup, dict)
                else None
            )
            if not isinstance(calibration, dict):
                raise ValueError(f"{raw_path.name}:{case_id} has no batch calibration")
            if calibration.get("candidates") != list(BATCH_CALIBRATION_CANDIDATES):
                raise ValueError(
                    f"{raw_path.name}:{case_id} did not consider the full batch sweep"
                )
            selected = calibration.get("selected_batch_size")
            if type(selected) is not int or selected not in BATCH_CALIBRATION_CANDIDATES:
                raise ValueError(
                    f"{raw_path.name}:{case_id} has invalid selected batch size {selected!r}"
                )
            execution = case["execution"]
            expected_enabled = selected > 1
            if (
                execution.get("batch_size") != selected
                or execution.get("batch_size_effective") != selected
                or execution.get("batch_enabled") is not expected_enabled
            ):
                raise ValueError(
                    f"{raw_path.name}:{case_id} does not record its selected numeric "
                    "batch configuration"
                )
            key = (result_id, case_id)
            if key in evidence:
                raise ValueError(f"duplicate calibrated raw case {key!r}")
            evidence[key] = selected
    return evidence


def _load_indexed_suite(output_dir: Path) -> Suite:
    index = json.loads((output_dir / "index.json").read_text())
    if index["campaign_id"] != "release-v1":
        raise ValueError("release audit requires a release-v1 execution")
    run_manifest = (repository_root() / index["run_manifest"]).resolve()
    return load_suite(run_manifest)


def audit_release_execution(output_dir: Path) -> dict[str, int]:
    suite = _load_indexed_suite(output_dir)
    case_rows = _read_csv(output_dir / "cases.csv")
    comparison_rows = _read_csv(output_dir / "comparisons.csv")
    failed = [row["case_id"] for row in case_rows if row["status"] != "success"]
    if failed:
        raise ValueError(f"release audit found unsuccessful cases: {', '.join(failed)}")

    variants = {row["variant_id"] for row in case_rows}
    if variants != EXPECTED_VARIANTS:
        raise ValueError(
            f"release audit expected variants {sorted(EXPECTED_VARIANTS)!r}, "
            f"received {sorted(variants)!r}"
        )
    collection = suite.run["collection"]
    expected_placements = {
        (str(placement), str(replica))
        for placement in range(1, int(collection["placements"]) + 1)
        for replica in range(1, int(collection["replicas_per_placement"]) + 1)
    }
    placements = {(row["placement"], row["replica"]) for row in case_rows}
    if placements != expected_placements:
        raise ValueError(f"release audit found unexpected placement coverage {placements!r}")

    keys_by_variant = {
        variant: {_row_key(row) for row in case_rows if row["variant_id"] == variant}
        for variant in EXPECTED_VARIANTS
    }
    expected_workloads = {
        case.workload.id
        for case in suite.cases
        if case.definition["variant_id"] == "clifft-current"
    }
    expected_keys = {
        (placement, replica, workload)
        for placement, replica in expected_placements
        for workload in expected_workloads
    }
    for variant, keys in keys_by_variant.items():
        if keys != expected_keys:
            raise ValueError(f"release variant {variant!r} has incomplete workload coverage")

    variant_identity = {}
    for variant in EXPECTED_VARIANTS:
        identities = {
            (
                case.implementation.id,
                str(case.implementation.definition["version"]),
            )
            for case in suite.cases
            if case.definition["variant_id"] == variant
        }
        if len(identities) != 1:
            raise ValueError(f"release variant {variant!r} has ambiguous simulator identity")
        variant_identity[variant] = identities.pop()

    comparison_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in comparison_rows:
        comparison_groups[row["comparison_id"]].append(row)
    if set(comparison_groups) != set(EXPECTED_COMPARISON_PAIRS):
        raise ValueError(
            "release audit found unexpected comparison IDs: "
            f"{sorted(comparison_groups)!r}"
        )

    expected_raw_count = int(collection["placements"]) * int(
        collection["replicas_per_placement"]
    )
    calibration_evidence = _calibration_evidence(output_dir, expected_raw_count)
    calibrated_case_keys = {
        (row["result_id"], row["case_id"])
        for row in case_rows
        if row["variant_id"] in CALIBRATED_VARIANTS
    }
    if set(calibration_evidence) != calibrated_case_keys:
        raise ValueError("release audit found incomplete calibrated raw evidence")

    for comparison_id, (baseline_variant, candidate_variant) in (
        EXPECTED_COMPARISON_PAIRS.items()
    ):
        rows = comparison_groups[comparison_id]
        if {_row_key(row) for row in rows} != expected_keys:
            raise ValueError(
                f"comparison {comparison_id!r} has incomplete workload coverage"
            )
        for row in rows:
            _require_fields(
                row,
                {
                    "baseline_variant_id": baseline_variant,
                    "candidate_variant_id": candidate_variant,
                },
                context=comparison_id,
            )

            if comparison_id == "current-vs-previous":
                baseline_identity = variant_identity["clifft-previous"]
                candidate_identity = variant_identity["clifft-current"]
                _require_fields(
                    row,
                    {
                        "baseline_implementation_id": baseline_identity[0],
                        "baseline_simulator_version": baseline_identity[1],
                        "baseline_batch_enabled": "false",
                        "baseline_batch_size_effective": "1",
                        "candidate_implementation_id": candidate_identity[0],
                        "candidate_simulator_version": candidate_identity[1],
                        "candidate_batch_enabled": "false",
                        "candidate_batch_size_effective": "1",
                    },
                    context=comparison_id,
                )
            elif comparison_id == "alternatives-vs-current":
                baseline_identity = variant_identity["clifft-current-calibrated"]
                candidate_identity = variant_identity["symft-calibrated"]
                _require_fields(
                    row,
                    {
                        "baseline_implementation_id": baseline_identity[0],
                        "baseline_simulator_version": baseline_identity[1],
                        "candidate_implementation_id": candidate_identity[0],
                        "candidate_simulator_version": candidate_identity[1],
                        "baseline_shots_per_call": "2048",
                        "candidate_shots_per_call": "2048",
                    },
                    context=comparison_id,
                )
                selected_pairs = (
                    (
                        "baseline",
                        row["baseline_result_id"],
                        row["baseline_case_id"],
                    ),
                    (
                        "candidate",
                        row["candidate_result_id"],
                        row["candidate_case_id"],
                    ),
                )
                for side, result_id, case_id in selected_pairs:
                    selected = calibration_evidence[(result_id, case_id)]
                    if row[f"{side}_batch_size_effective"] != str(selected):
                        raise ValueError(
                            f"{comparison_id} {side} does not use its calibrated size"
                        )
            else:
                baseline_identity = variant_identity["clifft-current"]
                candidate_identity = variant_identity["symft-single"]
                _require_fields(
                    row,
                    {
                        "baseline_implementation_id": baseline_identity[0],
                        "baseline_simulator_version": baseline_identity[1],
                        "baseline_batch_enabled": "false",
                        "baseline_batch_size_effective": "1",
                        "candidate_implementation_id": candidate_identity[0],
                        "candidate_simulator_version": candidate_identity[1],
                        "candidate_batch_enabled": "false",
                        "candidate_batch_size_effective": "1",
                    },
                    context=comparison_id,
                )

    return {
        "cases": len(case_rows),
        "comparisons": len(comparison_rows),
        "calibrated_cases": len(calibration_evidence),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("execution_dir", type=Path)
    args = parser.parse_args(argv)
    summary = audit_release_execution(args.execution_dir.resolve())
    print(
        "Release audit passed: "
        f"{summary['cases']} cases, {summary['comparisons']} comparisons, "
        f"{summary['calibrated_cases']} calibrated cases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
