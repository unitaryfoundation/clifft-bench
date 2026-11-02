"""Small completed executions shared by persisted-result and report checks."""

import json
import sys

import pytest

from clifft_bench.config import load_suite
from clifft_bench.runner import run_suite


@pytest.fixture(scope="session")
def collected_result(tmp_path_factory):
    from test_runner import _write_fixture_suite

    root = tmp_path_factory.mktemp("recorded-fixture")
    path, _, _ = _write_fixture_suite(root)
    return run_suite(
        load_suite(path), output_dir=root / "execution", python_executable=sys.executable
    )


@pytest.fixture
def raw_result(collected_result):
    document = json.loads(json.dumps(collected_result))
    # Synthetic Clifft identities/rates exercise report arithmetic, not performance claims.
    for index, (case, expected) in enumerate(
        zip(document["cases"], document["campaign"]["cases"], strict=True)
    ):
        variant = "previous" if index == 0 else "current"
        expected.update(id=variant, variant_id=variant, implementation_id=variant)
        case.update(case_id=variant, variant_id=variant)
        case["simulator"].update(
            implementation_id=variant,
            adapter="clifft",
            name="Clifft",
            version=f"1.{index}.0rc1",
            display_version=f"1.{index}.0",
        )
        case["setup"]["runtime_metadata"]["version"] = case["simulator"]["version"]
        case["workload"]["compatible_adapters"] = ["clifft", "symft"]
        for sample in case["samples"]:
            rate = 100.0 if index == 0 else 125.0
            sample["throughput_attempted_shots_per_second"] = rate
            sample["duration_seconds"] = sample["attempted_shots"] / rate
    document["campaign"]["comparisons"] = [
        {
            "id": "current-vs-previous",
            "baseline_variant": "previous",
            "candidate_variants": ["current"],
        }
    ]
    return document
