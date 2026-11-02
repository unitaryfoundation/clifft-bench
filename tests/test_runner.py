from __future__ import annotations

import csv
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from toml_helpers import write_toml

from clifft_bench.config import load_suite, read_toml
from clifft_bench.results import validate_result
from clifft_bench.runner import (
    SEED_REPETITION_STRIDE,
    _effective_batch_size,
    _record_runtime_execution,
    _simulator_record,
    run_suite,
)


def _write(path: Path, value: dict[str, Any]) -> None:
    write_toml(path, value)


def _write_fixture_suite(
    tmp_path: Path,
    *,
    parameters: dict[str, Any] | None = None,
    request_timeout_seconds: float = 5,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    artifact = tmp_path / "tiny.stim"
    artifact.write_text("M 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    source_commit = "0" * 40
    workloads = {
        "suite_version": "0.1.0",
        "workloads": [
            {
                "id": "tiny-fixture",
                "family": "test",
                "description": "Deterministic harness fixture.",
                "artifact": {"path": "tiny.stim", "sha256": digest},
                "parameters": parameters or {},
                "semantics": {
                    "observable_index": 0,
                    "postselect_all_detectors": False,
                },
                "expected_metadata": {
                    "num_qubits": 1,
                    "num_measurements": 1,
                    "num_detectors": 0,
                    "num_observables": 1,
                },
                "compatible_adapters": ["fixture"],
                "provenance": {
                    "source_url": "https://example.com/fixture",
                    "source_commit": source_commit,
                    "source_path": "tiny.stim",
                    "license": "CC0-1.0",
                },
            }
        ],
    }
    software = {
        "suite_version": "0.1.0",
        "implementations": [
            {
                "id": identifier,
                "name": "fixture",
                "adapter": "fixture",
                "distribution": "clifft-bench",
                "version": "1.0.0",
                "commit_sha": source_commit,
                "source_url": "https://example.com/fixture",
                "dependency_distributions": ["clifft-bench"],
                "build": {"precision": "integer", "compiler_flags": []},
            }
            for identifier in ["fixture-a", "fixture-b"]
        ],
    }
    software["implementations"][1]["display_version"] = "1.0"
    software["implementations"][1]["source_tag"] = "v1.0.0"
    cases = [
        {
            "id": identifier,
            "variant_id": identifier,
            "workload_id": "tiny-fixture",
            "implementation_id": identifier,
            "shots_per_call": 8,
            "execution": {
                "batch_size": 1,
                "options": {},
            },
        }
        for identifier in ["fixture-a", "fixture-b"]
    ]
    run = {
        "suite_version": "0.1.0",
        "profile_id": "fixture",
        "classification": "smoke",
        "seed": 10,
        "workloads_file": "workloads.toml",
        "software_file": "software.toml",
        "resources": {"logical_cpu": None},
        "measurement": {
            "setup_timeout_seconds": 5,
            "request_timeout_seconds": request_timeout_seconds,
            "warmup_shots": 1,
            "correctness_shots": 8,
            "min_sample_seconds": 0.0001,
            "repetitions": 2,
        },
        "cases": cases,
    }
    _write(tmp_path / "workloads.toml", workloads)
    _write(tmp_path / "software.toml", software)
    run_path = tmp_path / "run.toml"
    _write(run_path, run)
    return run_path, run, software


def test_effective_batch_size_is_capped_by_shots_per_call() -> None:
    metadata = {"effective_batch_size": 2048}
    assert _effective_batch_size(metadata, 8) == 8
    assert _effective_batch_size(metadata, 4096) == 2048


def test_calibrated_result_records_selected_numeric_batch_size() -> None:
    execution = {"batch_size": "calibrate"}
    metadata = {
        "threads": 1,
        "batch_enabled": False,
        "effective_batch_size": 1,
        "batch_calibration": {"selected_batch_size": 1},
    }

    _record_runtime_execution(execution, metadata, shots_per_call=8)

    assert execution == {
        "batch_enabled": False,
        "batch_size": 1,
        "threads_effective": 1,
        "batch_size_effective": 1,
    }


def test_isolated_workers_emit_valid_serial_raw_results(tmp_path: Path) -> None:
    run_path, run, software = _write_fixture_suite(
        tmp_path, parameters={"write_native_stdout": True}
    )
    output = tmp_path / "result"
    result = run_suite(
        load_suite(run_path),
        python_executable=sys.executable,
        output_dir=output,
        memory_limit_gib=1,
    )
    validate_result(result)
    assert [case["status"] for case in result["cases"]] == ["success", "success"]
    sequences = [
        [sample["sequence_index"] for sample in case["samples"]] for case in result["cases"]
    ]
    assert sequences == [[0, 1], [2, 3]]
    assert all(case["correctness"]["status"] == "passed" for case in result["cases"])
    assert [case["simulator"]["version"] for case in result["cases"]] == [
        "1.0.0",
        "1.0.0",
    ]
    assert [case["simulator"]["display_version"] for case in result["cases"]] == [
        "1.0.0",
        "1.0",
    ]
    assert "source_tag" not in result["cases"][0]["simulator"]
    assert result["cases"][1]["simulator"]["source_tag"] == "v1.0.0"
    for case in result["cases"]:
        assert case["simulator"]["python_executable"] == sys.executable
        assert case["execution"]["memory_limit_bytes"] == 1 << 30
        assert case["setup"]["runtime_metadata"]["address_space_limit_bytes"] in {
            None,
            1 << 30,
        }
        samples = sorted(case["samples"], key=lambda sample: sample["repetition"])
        assert (
            samples[1]["seed_first"] - samples[0]["seed_first"]
            == SEED_REPETITION_STRIDE
        )

    bad_software = json.loads(json.dumps(software))
    for implementation in bad_software["implementations"]:
        implementation["version"] = "2.0.0"
    bad_run = dict(run)
    bad_run["software_file"] = "software-bad.toml"
    _write(tmp_path / "software-bad.toml", bad_software)
    _write(tmp_path / "run-bad.toml", bad_run)
    failed = run_suite(
        load_suite(tmp_path / "run-bad.toml"),
        python_executable=sys.executable,
        output_dir=tmp_path / "failed",
    )
    validate_result(failed)
    assert [case["status"] for case in failed["cases"]] == ["error", "error"]
    assert all(case["error"]["phase"] == "setup" for case in failed["cases"])


def test_worker_timeout_is_recorded_and_run_completes(tmp_path: Path) -> None:
    run_path, _, _ = _write_fixture_suite(
        tmp_path,
        parameters={"sleep_on_seed": 10010, "sleep_seconds": 2},
        request_timeout_seconds=0.05,
    )
    result = run_suite(
        load_suite(run_path),
        python_executable=sys.executable,
        output_dir=tmp_path / "timeout",
    )
    validate_result(result)
    assert [case["status"] for case in result["cases"]] == ["error", "error"]
    assert all(case["error"]["phase"] == "sampling" for case in result["cases"])
    assert all(case["error"]["type"] == "WorkerTimeout" for case in result["cases"])


def test_warmup_failure_records_warmup_phase(tmp_path: Path) -> None:
    run_path, _, _ = _write_fixture_suite(
        tmp_path,
        parameters={"sleep_on_seed": 9, "sleep_seconds": 2},
        request_timeout_seconds=0.05,
    )
    result = run_suite(
        load_suite(run_path),
        python_executable=sys.executable,
        output_dir=tmp_path / "warmup-timeout",
    )
    validate_result(result)
    assert [case["status"] for case in result["cases"]] == ["error", "error"]
    assert all(case["error"]["phase"] == "warmup" for case in result["cases"])


def test_sample_interval_must_fit_inside_request_timeout(tmp_path: Path) -> None:
    run_path, _, _ = _write_fixture_suite(tmp_path, request_timeout_seconds=5)
    with pytest.raises(ValueError, match="must be greater than min_sample_seconds"):
        run_suite(load_suite(run_path), python_executable=sys.executable,
            output_dir=tmp_path / "invalid-timeout",
            min_sample_seconds=5,
        )


def test_symft_unselected_worker_calibrates_and_preserves_fired_shots(tmp_path):
    pytest.importorskip("symft")
    run_path, run, software = _write_fixture_suite(tmp_path)
    artifact = tmp_path / "tiny.stim"
    # Both raw parities are one. Native aggregate counts would discard every
    # shot even with early postselection disabled; the record path must keep all.
    artifact.write_text(
        "X 0 1\nT 1\nM 0\nDETECTOR rec[-1]\nM 1\nOBSERVABLE_INCLUDE(0) rec[-1]\n"
    )
    workloads = read_toml(tmp_path / "workloads.toml")
    workload = workloads["workloads"][0]
    workload["artifact"]["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    workload["compatible_adapters"] = ["symft"]
    workload["expected_metadata"].update(num_qubits=2, num_measurements=2, num_detectors=1)
    _write(tmp_path / "workloads.toml", workloads)
    implementation = software["implementations"][0]
    implementation.update(
        adapter="symft", name="SymFT", distribution="symft", version="0.1.1",
        commit_sha="c89b98514a919240b8afa53a271e08d926d3c987",
        source_url="https://github.com/haoliri0/SOFT",
    )
    _write(tmp_path / "software.toml", software)
    run["cases"] = run["cases"][:1]
    run["cases"][0]["shots_per_call"] = 67
    run["cases"][0]["execution"].update(
        batch_size="calibrate", options={"sample_chunk_shots": 2048},
    )
    run["measurement"]["setup_timeout_seconds"] = 60
    _write(run_path, run)

    output = tmp_path / "result"
    result = run_suite(
        load_suite(run_path),
        python_executable=sys.executable,
        output_dir=output,
        memory_limit_gib=1,
    )
    validate_result(json.loads((output / "raw.json").read_text()))
    case = result["cases"][0]
    assert case["status"] == "success", case["error"]
    metadata = case["setup"]["runtime_metadata"]
    assert metadata["installed_source_commit"] == implementation["commit_sha"]
    assert metadata["sampling_backend"] == "compile_sampler"
    assert metadata["record_materialization"] == "bit-packed-measurements"
    calibration = metadata["batch_calibration"]
    assert calibration["candidates"] == [1, 32, 64]
    assert all(trial["status"] == "success" for trial in calibration["results"])
    selected = calibration["selected_batch_size"]
    assert selected in (1, 32, 64)
    assert case["execution"]["batch_size"] == selected
    assert metadata["sample_chunk_shots"] == (2048 if selected == 1 else 0)
    assert case["correctness"]["status"] == "passed"
    for counts in [case["warmup"], case["correctness"]["details"], *case["samples"]]:
        assert counts["attempted_shots"] > 0
        assert counts["accepted_shots"] == counts["attempted_shots"]
        assert counts["logical_errors"] == counts["attempted_shots"]
        assert counts["discarded_shots"] == 0


def test_tsim_worker_records_full_call_warmup_and_manifest_seed(tmp_path):
    pytest.importorskip("tsim")
    run_path, run, software = _write_fixture_suite(tmp_path)
    workloads = read_toml(tmp_path / "workloads.toml")
    workloads["workloads"][0]["compatible_adapters"] = ["tsim"]
    _write(tmp_path / "workloads.toml", workloads)
    implementation = software["implementations"][0]
    implementation.update(
        adapter="tsim", name="Tsim", distribution="bloqade-tsim", version="0.1.5",
        commit_sha="e9406ed5bb49fcff1b04b65534cf56bbb756b3a6",
        source_url="https://github.com/QuEraComputing/tsim",
    )
    _write(tmp_path / "software.toml", software)
    run["cases"] = run["cases"][:1]
    run["measurement"]["setup_timeout_seconds"] = 120
    run["measurement"]["request_timeout_seconds"] = 120
    _write(run_path, run)
    result = run_suite(
        load_suite(run_path), python_executable=sys.executable, output_dir=tmp_path / "result"
    )
    validate_result(result)
    case = result["cases"][0]
    assert case["status"] == "success", case["error"]
    metadata = case["setup"]["runtime_metadata"]
    assert metadata["initial_seed"] == run["seed"] - 1
    assert metadata["threads"] == 1
    assert metadata["installed_source_commit"] == implementation["commit_sha"]
    assert case["warmup"]["attempted_shots"] == 8
    assert metadata["kernel_warmup_seconds"] >= 0
    assert metadata["compile_seconds"] >= 0


def test_existing_raw_evidence_is_never_overwritten(tmp_path):
    path, _, _ = _write_fixture_suite(tmp_path)
    output = tmp_path / "raw"
    output.mkdir()
    (output / "raw.json").write_text("previous evidence")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        run_suite(load_suite(path), output_dir=output, python_executable=sys.executable)
    assert (output / "raw.json").read_text() == "previous evidence"


def test_campaign_deadline_stops_remaining_work_and_keeps_evidence(tmp_path):
    path, _, _ = _write_fixture_suite(
        tmp_path, parameters={"sleep_on_seed": 10010, "sleep_seconds": 3},
    )
    suite = load_suite(path)
    # Shorten the campaign deadline after input validation to exercise the boundary quickly.
    suite.run["collection"] = {"run_timeout_minutes": 0.01}
    output = tmp_path / "deadline"
    result = run_suite(suite, output_dir=output, python_executable=sys.executable)
    assert result["cases"][0]["error"]["type"] == "WorkerTimeout"
    assert result["cases"][1]["error"]["type"] == "CampaignTimeout"
    assert result["run"]["finished_at"] is not None
    validate_result(json.loads((output / "raw.json").read_text()))


def test_official_collection_rejects_interpreter_override(tmp_path):
    path, _, _ = _write_fixture_suite(tmp_path)
    suite = load_suite(path)
    suite.run["classification"] = "official"
    with pytest.raises(ValueError, match="isolated implementation environments"):
        run_suite(suite, output_dir=tmp_path / "raw", python_executable=sys.executable)
    assert not (tmp_path / "raw").exists()


def test_missing_interpreter_is_preserved_as_a_setup_failure(tmp_path):
    path, _, _ = _write_fixture_suite(tmp_path)
    result = run_suite(load_suite(path), output_dir=tmp_path / "raw",
                       python_executable=str(tmp_path / "missing-python"))
    assert all(c["status"] == "error" and c["error"]["phase"] == "setup"
               and c["error"]["type"] == "FileNotFoundError" for c in result["cases"])
    validate_result(result)


@pytest.mark.parametrize("export_fails", [False, True])
def test_interruption_preserves_terminal_raw_evidence_and_failed_csv(
    tmp_path, monkeypatch, export_fails,
):
    from clifft_bench import runner

    class InterruptedClient:
        def __init__(self, *args, **kwargs):
            pass

        def prepare(self, *args, **kwargs):
            raise KeyboardInterrupt

        def close(self, *, interrupted=False):
            assert interrupted

    path, _, _ = _write_fixture_suite(tmp_path)
    output = tmp_path / "interrupted"
    monkeypatch.setattr(runner, "WorkerClient", InterruptedClient)
    previous_handler = signal.getsignal(signal.SIGTERM)
    if export_fails:
        def fail_export(*args):
            raise ValueError("export failure")
        monkeypatch.setattr(runner, "export_csv", fail_export)
    with pytest.raises(KeyboardInterrupt) as caught:
        run_suite(load_suite(path), output_dir=output, python_executable=sys.executable)
    assert signal.getsignal(signal.SIGTERM) is previous_handler
    raw = json.loads((output / "raw.json").read_text())
    validate_result(raw)
    if export_fails:
        assert any("export failure" in note for note in caught.value.__notes__)
        return
    assert all(c["status"] == "interrupted" and c["error"]["phase"] == "setup"
               for c in raw["cases"])
    with (output / "cases.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert all(r["status"] == "interrupted" and r["median_attempted_shots_per_second"] == ""
               for r in rows)


def test_default_interpreter_provenance(tmp_path):
    path, _, _ = _write_fixture_suite(tmp_path)
    case = load_suite(path).cases[0]
    assert _simulator_record(case)["python_executable"] == case.implementation.python_executable()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_process_signal_stops_busy_worker_and_finalizes_evidence(tmp_path, signum):
    from clifft_bench.config import repository_root
    from clifft_bench.report import report_execution

    path, _, _ = _write_fixture_suite(
        tmp_path, parameters={"write_native_stdout": True, "sleep_on_seed": 10010,
                              "sleep_seconds": 30}, request_timeout_seconds=60,
    )
    script = tmp_path / "collect.py"
    script.write_text("""
import sys
from pathlib import Path
from clifft_bench import runner
from clifft_bench.config import load_suite
class Client(runner.WorkerClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Path(sys.argv[3]).write_text(str(self.process.pid))
runner.WorkerClient = Client
runner.run_suite(load_suite(Path(sys.argv[1])), output_dir=Path(sys.argv[2]),
                 python_executable=sys.executable)
""")
    output, pid_path, log_path = tmp_path / "execution", tmp_path / "pid", tmp_path / "log"
    environment = {**os.environ, "PYTHONPATH": str(repository_root() / "src")}
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, str(script), str(path), str(output), str(pid_path)],
            stdout=log, stderr=log, env=environment,
        )
        worker_pid = None
        try:
            deadline = time.monotonic() + 10
            while log_path.read_text().count("fixture native sample output") < 3:
                assert process.poll() is None, log_path.read_text()
                assert time.monotonic() < deadline, log_path.read_text()
                time.sleep(0.01)
            worker_pid = int(pid_path.read_text())
            started = time.monotonic()
            process.send_signal(signum)
            process.wait(timeout=8)
            assert time.monotonic() - started < 8
            assert process.returncode != 0
            with pytest.raises(ProcessLookupError):
                os.kill(worker_pid, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if worker_pid is not None:
                try:
                    os.kill(worker_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    raw = json.loads((output / "raw.json").read_text())
    validate_result(raw)
    assert raw["run"]["finished_at"] is not None
    assert [c["status"] for c in raw["cases"]] == ["interrupted", "interrupted"]
    assert [c["error"]["phase"] for c in raw["cases"]] == ["sampling", "setup"]
    with (output / "cases.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert all(r["median_attempted_shots_per_second"] == "" for r in rows)
    assert "interrupted" in report_execution(output, check=True)
