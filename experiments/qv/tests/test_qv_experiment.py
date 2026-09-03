from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import pytest
from qv_experiment import run_benchmark
from qv_experiment.plot import clifft_display_name, load_samples, web_output_paths
from qv_experiment.qasm_adapter import _safe_eval, parse_qasm, to_clifft_stim
from qv_experiment.run_benchmark import (
    CLIFFT_SOURCE,
    parse_worker_output,
    schedule_cases,
    validate_official_clifft,
)
from qv_experiment.system import select_physical_cpus
from qv_experiment.worker import validate_timings


def test_qasm_adapter_preserves_paper_gate_mapping() -> None:
    qasm = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg meas[2];
u3(pi/2,0,-pi/4) q[0];
cx q[0],q[1];
measure q[0] -> meas[0];
measure q[1] -> meas[1];
"""
    operations, qubits = parse_qasm(qasm)

    assert qubits == 2
    assert len(operations) == 4
    assert to_clifft_stim(qasm).splitlines() == [
        "U3(0.5,0.0,-0.25) 0",
        "CX 0 1",
        "M 0",
        "M 1",
    ]


def test_qasm_expression_evaluator_is_restricted() -> None:
    assert _safe_eval("3*pi/4") == pytest.approx(3 * math.pi / 4)
    with pytest.raises(ValueError, match="unsupported"):
        _safe_eval("__import__('os').system('true')")


def test_qasm_adapter_rejects_unknown_statements() -> None:
    with pytest.raises(ValueError, match="unsupported QASM statement"):
        parse_qasm("OPENQASM 2.0;\nqreg q[1];\nh q[0];\n")


def test_schedule_alternates_tool_order_per_circuit() -> None:
    cases = schedule_cases([6], [42, 43], ["clifft", "qiskit", "qsim"])

    assert cases == [
        (6, 42, "clifft"),
        (6, 42, "qiskit"),
        (6, 42, "qsim"),
        (6, 43, "qsim"),
        (6, 43, "qiskit"),
        (6, 43, "clifft"),
    ]


def test_run_passes_qasm_over_stdin_without_retaining_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qasm = "OPENQASM 2.0;\nqreg q[1];\n"
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(
        run_benchmark,
        "git_metadata",
        lambda _root: {"commit": "test", "dirty": False},
    )
    monkeypatch.setattr(
        run_benchmark,
        "clifft_runtime_identity",
        lambda: {
            "distribution_version": "test",
            "runtime_version": "test",
            "cpu_baseline": "test",
        },
    )
    monkeypatch.setattr(run_benchmark, "ec2_identity", lambda **_kwargs: {})
    monkeypatch.setattr(run_benchmark, "select_physical_cpus", lambda _threads: [0])
    monkeypatch.setattr(run_benchmark, "generate_qv_qasm", lambda _width, _seed: qasm)
    monkeypatch.setattr(run_benchmark, "package_versions", lambda: {})
    monkeypatch.setattr(run_benchmark, "system_metadata", lambda: {})

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        response = {
            "status": "success",
            "timing": {
                "execution_seconds": 1.0,
                "compile_seconds": 0.5,
                "sample_seconds": 0.5,
            },
            "peak_rss_bytes": 1,
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(response),
            stderr="",
        )

    monkeypatch.setattr(run_benchmark.subprocess, "run", fake_run)

    assert (
        run_benchmark.main(
            [
                "--execution-id",
                "test-run",
                "--output-root",
                str(tmp_path),
                "--qubits",
                "1",
                "--seeds",
                "7",
                "--simulators",
                "clifft",
                "--threads",
                "1",
            ]
        )
        == 0
    )

    execution = tmp_path / "test-run"
    assert len(calls) == 1
    assert calls[0][1]["input"] == qasm
    assert not (execution / "circuits").exists()
    raw = json.loads((execution / "raw" / "clifft-q1-seed7-t1.json").read_text())
    assert raw["circuit"] == {"sha256": hashlib.sha256(qasm.encode()).hexdigest()}


def test_physical_cpu_selection_uses_one_logical_cpu_per_core() -> None:
    topology = [
        (0, 0, 0),
        (1, 0, 1),
        (2, 0, 0),
        (3, 0, 1),
    ]

    assert select_physical_cpus(2, topology) == [0, 1]
    with pytest.raises(ValueError, match="only 2"):
        select_physical_cpus(3, topology)


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
def test_worker_rejects_invalid_execution_times(value: float) -> None:
    with pytest.raises(ValueError, match="execution time"):
        validate_timings({"execution_seconds": value})


def test_plot_rejects_successful_zero_time(tmp_path: Path) -> None:
    (tmp_path / "cases.csv").write_text(
        "case_id,simulator,qubits,status,execution_seconds\n"
        "qulacs-q6-seed42,qulacs,6,success,0.0\n"
    )

    with pytest.raises(ValueError, match="qulacs-q6-seed42"):
        load_samples(tmp_path)


def test_plot_labels_clifft_with_release_version(tmp_path: Path) -> None:
    (tmp_path / "metadata.json").write_text(
        '{"clifft_source":{"release_version":"0.10.0",'
        '"artifact_version":"0.10.0rc1"}}\n'
    )

    assert clifft_display_name(tmp_path) == "Clifft 0.10.0"
    assert clifft_display_name(tmp_path, compact=True) == "Clifft 0.10"


def test_web_plot_output_paths_cover_both_themes(tmp_path: Path) -> None:
    assert {path.name for path in web_output_paths(tmp_path)} == {
        "quantum-volume-light.png",
        "quantum-volume-dark.png",
    }


def test_missing_worker_json_preserves_exit_code() -> None:
    response = parse_worker_output("process crashed\n", returncode=-9)

    assert response["status"] == "error"
    assert response["error"]["type"] == "WorkerOutputError"
    assert "exit code -9" in response["error"]["message"]


def test_official_clifft_identity_must_match_artifact_and_native_build() -> None:
    artifact_version = str(CLIFFT_SOURCE["artifact_version"])
    validate_official_clifft(
        {
            "distribution_version": artifact_version,
            "runtime_version": artifact_version,
            "cpu_baseline": "native",
        }
    )
    with pytest.raises(ValueError, match="wrong Clifft build"):
        validate_official_clifft(
            {
                "distribution_version": artifact_version,
                "runtime_version": artifact_version,
                "cpu_baseline": "x86-64-v2",
            }
        )
