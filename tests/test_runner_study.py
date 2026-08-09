from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest

from clifft_bench.runner_study import (
    analyze_runner_study,
    write_runner_study_csv,
    write_runner_study_json,
)
from clifft_bench.schema import repository_root, validate_path


def _aa_result(tmp_path: Path, *, rate_a: float = 100.0, rate_b: float = 102.0) -> Path:
    document = validate_path(repository_root() / "examples/result.v1.json")
    case_a = copy.deepcopy(document["cases"][0])
    case_b = copy.deepcopy(case_a)
    case_a["case_id"] = "example-aa-a"
    case_b["case_id"] = "example-aa-b"
    case_a["pair_id"] = "example-aa"
    case_b["pair_id"] = "example-aa"
    case_a["samples"][0]["throughput_attempted_shots_per_second"] = rate_a
    case_b["samples"][0]["throughput_attempted_shots_per_second"] = rate_b
    document["cases"] = [case_a, case_b]
    path = tmp_path / "aa.json"
    path.write_text(json.dumps(document))
    return path


def test_runner_study_reports_paired_ratios_and_writes_derived_files(
    tmp_path: Path,
) -> None:
    report, observations = analyze_runner_study([_aa_result(tmp_path)])

    assert report["result_count"] == 1
    assert report["observation_count"] == 1
    assert report["groups"][0]["ratio_b_over_a"]["median"] == pytest.approx(1.02)
    assert report["groups"][0]["absolute_delta_percent"]["p95"] == pytest.approx(
        200 * 2 / 202
    )

    json_path = tmp_path / "summary.json"
    csv_path = tmp_path / "pairs.csv"
    write_runner_study_json(json_path, report)
    write_runner_study_csv(csv_path, observations)
    assert json.loads(json_path.read_text())["observation_count"] == 1
    with csv_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["pair_id"] == "example-aa"
    assert float(rows[0]["ratio_b_over_a"]) == pytest.approx(1.02)


def test_runner_study_rejects_nonidentical_implementations(tmp_path: Path) -> None:
    path = _aa_result(tmp_path)
    document = json.loads(path.read_text())
    document["cases"][1]["simulator"]["implementation_id"] = "different"
    path.write_text(json.dumps(document))

    with pytest.raises(ValueError, match="different implementations"):
        analyze_runner_study([path])
