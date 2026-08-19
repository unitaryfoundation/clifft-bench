from __future__ import annotations

from pathlib import Path

import pytest

from clifft_bench.adapters import load_adapter


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
