import copy
import csv

import pytest

from clifft_bench.cli import main
from clifft_bench.results import (
    case_rows,
    comparison_pairs,
    export_csv,
    read_result,
    validate_result,
    write_json,
)


def test_round_trip_and_deterministic_csv_without_configuration(raw_result, tmp_path):
    raw = tmp_path / "raw.json"
    write_json(raw, raw_result)
    assert read_result(raw) == raw_result
    output = tmp_path / "cases.csv"
    export_csv(raw_result, output)
    original = output.read_bytes()
    # Table order comes from the resolved campaign, not JSON case ordering.
    raw_result["cases"].reverse()
    export_csv(raw_result, output)
    assert output.read_bytes() == original
    with output.open() as stream:
        rows = list(csv.DictReader(stream))
    assert [float(r["median_attempted_shots_per_second"]) for r in rows] == [100, 125]
    assert main(["csv", str(raw)]) == 0
    assert main(["validate", str(raw)]) == 0
    assert main(["csv", str(raw), "--output", str(raw)]) == 2
    assert read_result(raw) != raw_result  # Regeneration never rewrites authoritative evidence.


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "extra",
        "running",
        "empty-samples",
        "wrong-repetitions",
        "nan",
        "infinite",
        "no-compile-time",
        "no-first-call",
        "first-call-size",
        "negative-duration",
        "bad-counts",
        "wrong-rate",
        "version",
        "thread",
        "no-correctness",
        "call-size",
        "no-error",
        "unknown-format",
        "unfinished",
    ],
)
def test_invalid_persisted_evidence_is_rejected(raw_result, damage):
    case = raw_result["cases"][0]
    if damage == "missing":
        raw_result["cases"].pop()
    elif damage == "duplicate":
        raw_result["cases"].append(copy.deepcopy(case))
    elif damage == "extra":
        extra = copy.deepcopy(case)
        extra["case_id"] = "unexpected"
        raw_result["cases"].append(extra)
    elif damage == "running":
        case["status"] = "running"
    elif damage == "empty-samples":
        case["samples"] = []
    elif damage == "wrong-repetitions":
        case["samples"].pop()
    elif damage == "nan":
        case["samples"][0]["duration_seconds"] = float("nan")
    elif damage == "infinite":
        case["setup"]["runtime_metadata"]["compile_seconds"] = float("inf")
    elif damage == "no-compile-time":
        case["setup"]["runtime_metadata"].pop("compile_seconds")
    elif damage == "no-first-call":
        case["first_call_warmup"] = None
    elif damage == "first-call-size":
        case["first_call_warmup"]["attempted_shots"] += 1
        case["first_call_warmup"]["accepted_shots"] += 1
    elif damage == "negative-duration":
        case["samples"][0]["duration_seconds"] = -1
    elif damage == "bad-counts":
        case["samples"][0]["accepted_shots"] += 1
    elif damage == "wrong-rate":
        case["samples"][0]["throughput_attempted_shots_per_second"] *= 2
    elif damage == "version":
        case["simulator"]["version"] = "9.0"
    elif damage == "thread":
        case["execution"]["threads_effective"] = 2
    elif damage == "no-correctness":
        case["correctness"] = None
    elif damage == "call-size":
        case["execution"]["shots_per_call"] += 1
    elif damage == "no-error":
        case["status"] = "error"
    elif damage == "unknown-format":
        raw_result["format"] = "clifft-bench/result/v1"
    else:
        raw_result["run"]["finished_at"] = None
    with pytest.raises(ValueError):
        validate_result(raw_result)


@pytest.mark.parametrize("phase", ["setup", "first_call_warmup", "correctness", "sampling"])
def test_failed_cases_keep_evidence_without_exporting_throughput(raw_result, phase):
    case = raw_result["cases"][1]
    case.update(
        status="error", error={"phase": phase, "type": "WorkerTimeout", "message": "timeout"}
    )
    if phase == "setup":
        case.update(setup=None, first_call_warmup=None, correctness=None, samples=[])
        case["execution"].pop("batch_size_effective")
    validate_result(raw_result)
    row = case_rows(raw_result)[1]
    assert row["status"] == "error" and row["error_phase"] == phase
    assert row["median_attempted_shots_per_second"] == ""
    assert row["mad_attempted_shots_per_second"] == ""
    assert row["sample_count"] == len(case["samples"])
    assert comparison_pairs(raw_result)[0]["ratio"] is None


@pytest.mark.parametrize(
    "field", ["digest", "selection", "calls", "memory", "threads", "postselect"]
)
def test_comparisons_reject_incompatible_inputs(raw_result, field):
    case = raw_result["cases"][1]
    if field == "digest":
        case["workload"]["artifact"]["sha256"] = "f" * 64
    elif field == "selection":
        case["workload"]["semantics"]["observable_index"] += 1
    elif field == "calls":
        case["execution"]["shots_per_call"] += 1
    elif field == "threads":
        case["execution"]["threads_requested"] = 8
    elif field == "postselect":
        case["execution"]["postselect"] = "off"
    else:
        case["execution"]["memory_limit_bytes"] = 1 << 30
    with pytest.raises(ValueError, match="incompatible comparison"):
        comparison_pairs(raw_result)


@pytest.mark.parametrize("damage", ["budget", "environment", "affinity", "topology", "effective"])
def test_eight_core_resource_evidence_must_match_the_contract(raw_result, damage):
    from clifft_bench.report import build_report, throughput_data
    from clifft_bench.system import thread_environment

    raw_result["campaign"]["resources"]["logical_cpu"] = 0
    for case, contract in zip(raw_result["cases"], raw_result["campaign"]["cases"], strict=True):
        contract["execution"]["threads"] = 8
        case["execution"].update(threads=8, threads_requested=8, threads_effective=8)
        case["setup"]["runtime_metadata"].update(
            threads=8, thread_environment=thread_environment(8)
        )
        case["setup"]["affinity"].update(
            applied=True, logical_cpus=list(range(8)), available_before=list(range(16)),
            available_after=list(range(8)), physical_cores=[
                {"logical_cpu": cpu, "socket": 0, "core": cpu} for cpu in range(8)
            ],
        )
    validate_result(raw_result)
    # An unavailable native worker count stays unknown in the exported view.
    current = raw_result["cases"][1]
    current["execution"]["threads_effective"] = None
    current["setup"]["runtime_metadata"]["threads"] = None
    validate_result(raw_result)
    data = throughput_data(build_report(raw_result))
    row = data["panels"][0]["rows"][0]
    assert "8 cores" in data["panels"][0]["title"]
    assert row["values"][0]["threads_requested"] == 8
    assert row["values"][0]["threads_effective"] is None
    case = raw_result["cases"][0]
    if damage == "budget":
        case["execution"]["threads_requested"] = 1
    elif damage == "environment":
        case["setup"]["runtime_metadata"]["thread_environment"]["OPENBLAS_NUM_THREADS"] = "8"
    elif damage == "affinity":
        case["setup"]["affinity"]["available_after"] = [0]
    elif damage == "topology":
        case["setup"]["affinity"]["physical_cores"][1]["core"] = 0
    else:
        case["execution"]["threads_effective"] = 9
        case["setup"]["runtime_metadata"]["threads"] = 9
    with pytest.raises(ValueError):
        validate_result(raw_result)


def test_ratios_use_each_side_sample_median(raw_result):
    case = raw_result["cases"][1]
    case["samples"][1]["throughput_attempted_shots_per_second"] = 175
    case["samples"][1]["duration_seconds"] = case["samples"][1]["attempted_shots"] / 175
    validate_result(raw_result)
    assert comparison_pairs(raw_result)[0]["ratio"] == 1.5


@pytest.mark.parametrize("damage", [None, "source", "cloud", "memory", "affinity", "partial"])
def test_official_evidence_requires_applied_host_constraints(raw_result, damage):
    raw_result["run"].update(classification="official", overrides={}, selection=None)
    campaign = raw_result["campaign"]
    campaign.update(
        classification="official",
        collection={"memory_limit_gib": 1},
        reference_host={"provider": "aws", "instance_type": "m7a.xlarge", "architecture": "x86_64"},
    )
    campaign["resources"].update(logical_cpu=0, memory_limit_bytes=1 << 30)
    runner = raw_result["runner"]
    runner.update(
        os="Linux",
        machine="x86_64",
        suite_source={"commit": "a" * 40, "dirty": False},
        cloud={
            "provider": "aws",
            "instance_type": "m7a.xlarge",
            "instance_id": "i-test",
            "image_id": "ami-test",
            "region": "us-east-1",
            "availability_zone": "us-east-1a",
            "lifecycle": "on-demand",
            "boot_id": "boot-test",
        },
    )
    for case in raw_result["cases"]:
        case["execution"]["memory_limit_bytes"] = 1 << 30
        case["setup"]["runtime_metadata"]["address_space_limit_bytes"] = 1 << 30
        case["setup"]["affinity"].update(applied=True, available_after=[0])
    if damage == "source":
        runner["suite_source"]["dirty"] = True
    elif damage == "cloud":
        runner["cloud"]["instance_type"] = "different"
    elif damage == "memory":
        raw_result["cases"][0]["setup"]["runtime_metadata"]["address_space_limit_bytes"] = None
    elif damage == "affinity":
        raw_result["cases"][0]["setup"]["affinity"]["applied"] = False
    elif damage == "partial":
        raw_result["run"]["selection"] = "current"
    if damage:
        with pytest.raises(ValueError):
            validate_result(raw_result)
    else:
        validate_result(raw_result)


def test_invalid_json_is_reported_without_traceback(tmp_path, capsys):
    path = tmp_path / "raw.json"
    path.write_text("{")
    assert main(["validate", str(path)]) == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_first_call_failure_has_no_throughput(raw_result, tmp_path):
    case = raw_result["cases"][0]
    case["first_call_warmup"] = None
    case.update(status="error", samples=[], correctness=None,
                error={"phase": "first_call_warmup", "type": "RuntimeError",
                       "message": "inconsistent shot counts"})
    validate_result(raw_result)
    export_csv(raw_result, tmp_path / "cases.csv")
    assert case_rows(raw_result)[0]["median_attempted_shots_per_second"] == ""


@pytest.mark.parametrize("adapter,postselect,threads,status,exit_code", [
    ("clifft", "off", 1, "unsupported", 0),
    ("stim", "off", 8, "unsupported", 0),
    ("stim", "on", 1, "unsupported", 0),
    ("stim", "off", 1, "unsupported", 1),
    ("xtim", "on", 1, "unsupported", 1),
    ("symft", "on", 1, "unsupported", 1),
    ("clifft", "off", 1, "error", 1),
    ("clifft", "on", 1, "interrupted", 1),
])
def test_run_cli_only_accepts_declared_unsupported_capabilities(
    monkeypatch, tmp_path, capsys, adapter, postselect, threads, status, exit_code
):
    from clifft_bench import cli

    monkeypatch.setattr(cli, "run_suite", lambda *args, **kwargs: {"cases": [
        {"case_id": "measured", "status": "success"},
        {"case_id": "other", "status": status,
         "simulator": {"adapter": adapter, "version": "0.13.0"},
         "execution": {"postselect": postselect, "threads": threads},
         "error": {"phase": "setup", "type": "UnsupportedConfiguration",
                   "message": "endpoint unavailable"}},
    ]})
    assert main(["run", "--output-dir", str(tmp_path / "result")]) == exit_code
    if status == "unsupported" and exit_code:
        assert "Unexpected unsupported" in capsys.readouterr().err


@pytest.mark.parametrize("sources,exit_code", [({}, 0), ({"on": "clifft"}, 1),
                                              ({"off": "stim"}, 0)])
def test_xtim_reference_loss_fails_declared_endpoint(monkeypatch, tmp_path, sources, exit_code):
    from clifft_bench import cli

    case = {"case_id": "xtim-on", "status": "unsupported",
            "simulator": {"adapter": "xtim", "version": "4.1.2"},
            "execution": {"postselect": "on", "threads": 1},
            "workload": {"semantics": {"raw_zero_reference": sources} if sources else {}},
            "error": {"phase": "setup", "type": "UnsupportedReferenceCircuit",
                      "message": "raw reference parity unavailable"}}
    monkeypatch.setattr(cli, "run_suite", lambda *args, **kwargs: {"cases": [case]})
    assert main(["run", "--output-dir", str(tmp_path / "result")]) == exit_code
