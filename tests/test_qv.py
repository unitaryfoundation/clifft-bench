from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from clifft_bench.qv import load_qv_campaign, scheduled_cases, select_physical_cpus
from clifft_bench.qv_adapters import parse_qasm, to_clifft_stim
from clifft_bench.qv_results import finalize_qv_execution
from clifft_bench.qv_worker import _clifft_compile_arguments, _configure_clifft_threads
from clifft_bench.schema import SchemaValidationError, repository_root, validate_document
from clifft_bench.system import collect_runner_metadata

ROOT = repository_root()
CAMPAIGN_PATH = ROOT / "campaigns/qv-multicore-v1/qv-campaign.v1.json"


def test_qv_campaign_has_deliberately_bounded_matrix() -> None:
    campaign = load_qv_campaign(CAMPAIGN_PATH)
    cases = scheduled_cases(campaign)

    current_clifft = [run for run in campaign.document["runs"] if run["adapter"] == "clifft"]
    assert campaign.document["classification"] == "official"
    assert {run["version"] for run in current_clifft} == {"0.9.0"}
    assert {run["expected_distribution_version"] for run in current_clifft} == {"0.9.0"}
    assert {run["adapter"] for run in campaign.document["runs"]} == {
        "clifft",
        "qiskit",
        "qulacs",
        "qsim",
    }
    assert campaign.document["collection"]["placements"] == 1
    assert len(cases) == 234
    assert sum(case["run"]["phase"] == "current-tools" for case in cases) == 144
    assert sum(case["run"]["phase"] == "clifft-scaling" for case in cases) == 90


def test_qv_schedule_reverses_tool_order_between_seeds() -> None:
    cases = scheduled_cases(load_qv_campaign(CAMPAIGN_PATH))
    first = [case["run"]["id"] for case in cases[:4]]
    second = [case["run"]["id"] for case in cases[4:8]]

    assert second == list(reversed(first))
    assert {case["qubits"] for case in cases[:8]} == {6}
    assert [case["seed"] for case in cases[:8]] == [42] * 4 + [43] * 4


def test_physical_cpu_selection_uses_one_sibling_per_core() -> None:
    topology = [
        (0, 0, 0),
        (1, 0, 1),
        (2, 0, 0),
        (3, 0, 1),
        (4, 0, 2),
    ]
    assert select_physical_cpus(3, topology) == [0, 1, 4]
    with pytest.raises(ValueError, match="only 3"):
        select_physical_cpus(4, topology)


def test_qv_parser_preserves_original_clifft_gate_mapping() -> None:
    qasm = """OPENQASM 2.0;
include \"qelib1.inc\";
qreg q[2];
creg c[2];
u3(pi/2,0,-pi) q[0];
cx q[0],q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
"""
    operations, qubits = parse_qasm(qasm)
    converted = to_clifft_stim(qasm)

    assert qubits == 2
    assert [operation.name for operation in operations] == ["u3", "cx", "measure", "measure"]
    assert "U3(0.5,0.0,-1.0) 0" in converted
    assert converted.endswith("M 1\n")


def test_clifft_thread_configuration_supports_released_and_openmp_apis() -> None:
    configured: list[int] = []
    released = SimpleNamespace(
        set_num_threads=configured.append,
        get_num_threads=lambda: configured[-1],
    )

    assert _configure_clifft_threads(released, 8) == (8, {}, "module-setter")
    assert configured == [8]
    assert _configure_clifft_threads(SimpleNamespace(), 16) == (
        16,
        {"threads": 16},
        "sample-argument",
    )


def test_clifft_compile_configuration_supports_released_and_openmp_apis() -> None:
    hir_passes = object()
    bytecode_passes = object()
    released = SimpleNamespace(
        default_hir_pass_manager=lambda: hir_passes,
        default_bytecode_pass_manager=lambda: bytecode_passes,
    )
    candidate = SimpleNamespace(default_hir_pass_manager=lambda: hir_passes)

    assert _clifft_compile_arguments(released) == {
        "hir_passes": hir_passes,
        "bytecode_passes": bytecode_passes,
    }
    assert _clifft_compile_arguments(candidate) == {"hir_passes": hir_passes}


def test_qv_manifest_rejects_more_threads_than_physical_cores(tmp_path) -> None:
    campaign = load_qv_campaign(CAMPAIGN_PATH)
    document = copy.deepcopy(campaign.document)
    for environment in document["environments"]:
        environment["requirements"] = str(
            (CAMPAIGN_PATH.parent / environment["requirements"]).resolve()
        )
    document["runs"][0]["threads"] = [17]
    path = tmp_path / "qv.json"
    path.write_text(json.dumps(document))

    with pytest.raises(SchemaValidationError, match="more than 16 physical cores"):
        load_qv_campaign(path)


def _small_campaign(tmp_path: Path):  # type: ignore[no-untyped-def]
    document = copy.deepcopy(load_qv_campaign(CAMPAIGN_PATH).document)
    document["reference_host"]["physical_cores"] = 1
    document["reference_host"]["logical_cpus"] = 1
    document["collection"]["placements"] = 1
    document["circuit"]["qubits"] = [6]
    document["circuit"]["seeds"] = [42]
    generator = next(
        item
        for item in document["environments"]
        if item["id"] == document["circuit"]["generator_environment"]
    )
    run = copy.deepcopy(document["runs"][0])
    run["threads"] = [1]
    run["qubits"] = [6]
    environment = next(
        item for item in document["environments"] if item["id"] == run["environment_id"]
    )
    for item in (generator, environment):
        item["requirements"] = str((CAMPAIGN_PATH.parent / item["requirements"]).resolve())
    document["environments"] = [generator, environment]
    document["runs"] = [run]
    path = tmp_path / "qv-campaign.json"
    path.write_text(json.dumps(document))
    return load_qv_campaign(path), run


def _small_result(tmp_path: Path):  # type: ignore[no-untyped-def]
    campaign, run = _small_campaign(tmp_path)
    circuit_name = "qv-q6-seed42.qasm"
    digest = hashlib.sha256(b"OPENQASM 2.0;\nqreg q[6];\n").hexdigest()
    runner = collect_runner_metadata(ROOT)
    runner["physical_cores"] = 1
    runner["logical_cpus"] = 1
    runner["suite_source"] = {"commit": "a" * 40, "dirty": False}
    runner["cloud"] = {
        "provider": "aws",
        "instance_id": "i-example",
        "instance_type": campaign.document["reference_host"]["instance_type"],
        "image_id": "ami-example",
        "region": "us-east-1",
        "availability_zone": "us-east-1c",
        "lifecycle": "on-demand",
        "boot_id": "boot-example",
    }
    case_id = f"{run['id']}--q6-seed42-t1"
    result = {
        "schema_version": "clifft-bench/qv-result/v1",
        "campaign": {
            "id": campaign.id,
            "classification": campaign.document["classification"],
            "hardware_epoch": campaign.document["hardware_epoch"],
            "manifest": str(campaign.path),
            "manifest_sha256": campaign.manifest_sha256,
            "circuit_generator_dependencies": {"qiskit": "2.3.1"},
        },
        "run": {
            "id": str(uuid.uuid4()),
            "profile_id": campaign.id,
            "execution_id": "qv-test",
            "started_at": "2026-08-21T00:00:00Z",
            "finished_at": "2026-08-21T00:00:01Z",
            "placement": 1,
            "replica": 1,
            "schedule_policy": "serial-alternating-forward-reverse",
            "workflow": {},
        },
        "runner": runner,
        "cases": [
            {
                "case_id": case_id,
                "sequence_index": 0,
                "status": "success",
                "phase": run["phase"],
                "simulator": {
                    "run_id": run["id"],
                    "name": run["name"],
                    "version": run["version"],
                    "distribution": run["distribution"],
                    "expected_distribution_version": run["expected_distribution_version"],
                    "commit_sha": run["commit_sha"],
                    "source_url": run["source_url"],
                    "adapter": run["adapter"],
                    "environment_id": run["environment_id"],
                    "dependencies": {"clifft": "0.9.0"},
                },
                "circuit": {
                    "family": "quantum-volume",
                    "qubits": 6,
                    "depth": 6,
                    "seed": 42,
                    "basis_gates": ["cx", "u3"],
                    "path": circuit_name,
                    "sha256": digest,
                },
                "threads": {
                    "requested": 1,
                    "effective": 1,
                    "cpu_set": [0],
                    "policy": "one-logical-cpu-per-physical-core",
                },
                "timing": {
                    "execution_seconds": 0.5,
                    "compile_seconds": 0.2,
                    "sample_seconds": 0.3,
                    "timed_region": "original-clifft-paper-qv-v1",
                },
                "peak_rss_bytes": 1024,
                "runtime_metadata": {},
                "error": None,
            }
        ],
    }
    validate_document(result)
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(json.dumps(result))
    return campaign, raw_path


def test_qv_finalization_validates_and_creates_plot_ready_table(tmp_path) -> None:
    campaign, raw_path = _small_result(tmp_path)
    output = tmp_path / "derived"
    output.mkdir()

    index = finalize_qv_execution(
        campaign,
        execution_id="qv-test",
        raw_paths=[raw_path],
        output_dir=output,
    )

    assert index["case_rows"] == 1
    assert index["classification"] == "official"
    assert index["files"] == {"raw": "raw/", "cases": "cases.csv"}
    assert "circuits" not in index
    assert (output / "cases.csv").read_text().count("\n") == 2
    assert not (output / "summary.json").exists()


def test_qv_curated_result_is_traceable_and_exploratory(tmp_path) -> None:
    campaign, raw_path = _small_result(tmp_path)
    result = json.loads(raw_path.read_text())
    result["curation"] = {
        "source_result_commit": "b" * 40,
        "source_result_sha256": "c" * 64,
        "source_manifest_sha256": "d" * 64,
        "excluded_run_ids": ["removed-run"],
        "reason": "Keep only the final comparison matrix.",
    }
    validate_document(result)
    raw_path.write_text(json.dumps(result))
    output = tmp_path / "derived"
    output.mkdir()

    index = finalize_qv_execution(
        campaign,
        execution_id="qv-test",
        raw_paths=[raw_path],
        output_dir=output,
    )

    assert index["classification"] == "exploratory"
    assert index["curation"] == result["curation"]
