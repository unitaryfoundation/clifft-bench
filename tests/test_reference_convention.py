from __future__ import annotations

from pathlib import Path

import pytest

from clifft_bench.adapters import load_adapter
from clifft_bench.manifest import Case, Implementation, Workload
from clifft_bench.runner import WorkerClient


def _prepare(
    adapter_name: str,
    tmp_path: Path,
    *,
    circuit: str,
    postselect: bool,
):
    pytest.importorskip(adapter_name)
    artifact_path = tmp_path / f"{adapter_name}.stim"
    artifact_path.write_text(circuit)
    if adapter_name == "clifft":
        execution = {
            "batch_enabled": False,
            "batch_size": 1,
            "sample_chunk_shots": 0,
        }
    else:
        execution = {
            "batch_enabled": True,
            "batch_size": 32,
            "sample_chunk_shots": 32,
        }
    return load_adapter(adapter_name).prepare(
        artifact_path=artifact_path,
        workload={
            "semantics": {
                "observable_index": 0,
                "postselect_all_detectors": postselect,
                "reference_convention": "raw-record-parity",
            }
        },
        execution=execution,
    )


@pytest.mark.parametrize("adapter_name", ["clifft", "symft", "tsim"])
def test_detector_postselection_uses_raw_record_parity(
    adapter_name: str, tmp_path: Path
) -> None:
    prepared = _prepare(
        adapter_name,
        tmp_path,
        circuit="X 0\nM 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
        postselect=True,
    )

    counts = prepared.sample(shots=32, seed=7)

    assert prepared.runtime_metadata["reference_convention"] == "raw-record-parity"
    assert counts.attempted_shots == 32
    assert counts.accepted_shots == 0
    assert counts.discarded_shots == 32
    assert counts.logical_errors == 0


@pytest.mark.parametrize("adapter_name", ["clifft", "symft", "tsim"])
def test_logical_errors_use_raw_record_parity(
    adapter_name: str, tmp_path: Path
) -> None:
    prepared = _prepare(
        adapter_name,
        tmp_path,
        circuit="X 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
        postselect=False,
    )

    counts = prepared.sample(shots=32, seed=7)

    assert prepared.runtime_metadata["reference_convention"] == "raw-record-parity"
    assert counts.attempted_shots == 32
    assert counts.accepted_shots == 32
    assert counts.discarded_shots == 0
    assert counts.logical_errors == 32


def test_tsim_runs_through_the_isolated_worker_with_x64(tmp_path: Path) -> None:
    pytest.importorskip("tsim")
    artifact_path = tmp_path / "tsim-worker.stim"
    artifact_path.write_text("X 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
    workload = Workload(
        definition={
            "semantics": {
                "observable_index": 0,
                "postselect_all_detectors": False,
                "reference_convention": "raw-record-parity",
            }
        },
        artifact_path=artifact_path,
        artifact_sha256="0" * 64,
    )
    implementation = Implementation(
        {
            "adapter": "tsim",
            "version": "0.1.5",
            "dependency_distributions": ["bloqade-tsim", "jax", "jaxlib", "numpy"],
        }
    )
    case = Case(
        definition={
            "shots_per_call": 32,
            "execution": {
                "batch_enabled": True,
                "batch_size": 32,
                "sample_chunk_shots": 0,
            }
        },
        workload=workload,
        implementation=implementation,
    )
    client = WorkerClient(case, cpu=None)
    try:
        setup = client.prepare(timeout_seconds=60, seed=1)
        sample = client.request(
            {
                "command": "sample",
                "shots_per_call": 32,
                "min_seconds": 0.001,
                "seed": 7,
                "max_api_calls": 1_000,
            },
            timeout_seconds=60,
        )
    finally:
        client.close()

    assert setup["runtime_metadata"]["jax_x64_enabled"] is True
    assert setup["runtime_metadata"]["precision"] == "complex-fp64"
    assert sample["attempted_shots"] >= 32
    assert sample["accepted_shots"] == sample["attempted_shots"]
    assert sample["logical_errors"] == sample["attempted_shots"]
