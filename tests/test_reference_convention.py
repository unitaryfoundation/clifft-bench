from __future__ import annotations

import json
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
    if adapter_name in {"stim", "xtim"}:
        execution["sample_chunk_shots"] = 0
    prepared = load_adapter(adapter_name).prepare(
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
    prepared.begin_sample(7)
    return prepared


@pytest.mark.parametrize("adapter_name", ["clifft", "symft", "stim", "xtim"])
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


@pytest.mark.parametrize("adapter_name", ["clifft", "symft", "stim", "xtim"])
def test_logical_errors_use_raw_record_parity(
    adapter_name: str, tmp_path: Path
) -> None:
    prepared = _prepare(
        adapter_name,
        tmp_path,
        circuit=("X 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n"
                 + ("M 1\nDETECTOR rec[-1]\n" if adapter_name == "xtim" else "")),
        postselect=False,
    )

    counts = prepared.sample(shots=32, seed=7)

    assert prepared.runtime_metadata["reference_convention"] == "raw-record-parity"
    assert counts.attempted_shots == 32
    assert counts.accepted_shots == 32
    assert counts.discarded_shots == 0
    assert counts.logical_errors == 32


@pytest.mark.parametrize("batch_size", [1, 32])
def test_clifft_scheduled_counts_match_exact_noisy_branch_probabilities(
    tmp_path, batch_size,
):
    clifft = pytest.importorskip("clifft")
    if not hasattr(clifft, "ActiveWidthSchedulePass"):
        pytest.skip("this Clifft release predates active-width scheduling")
    source = (
        "R_PAULI(0.3) X0*X1\nZ_ERROR(0.3) 0\n"
        "R_PAULI(0.3) Z0*Y1\nMPP Y0*Y1\nMPP Y0\n"
    )
    expected = sum(
        weight * clifft.record_probabilities(
            clifft.compile(source.replace("Z_ERROR(0.3) 0", replacement)),
            ["00", "01", "10", "11"],
        )
        for weight, replacement in [(0.7, ""), (0.3, "Z 0")]
    )
    artifact = tmp_path / "noisy.stim"
    artifact.write_text(source + "DETECTOR rec[-2]\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
    config_path = (Path(__file__).resolve().parents[1] /
                   "campaigns/release-v1/clifft-scheduled-execution.json")
    options = json.loads(config_path.read_text())["clifft_scheduler"]
    prepared = load_adapter("clifft").prepare(
        artifact_path=artifact,
        workload={"semantics": {
            "observable_index": 0, "postselect_all_detectors": True,
            "reference_convention": "raw-record-parity",
        }},
        execution={
            "batch_enabled": batch_size > 1, "batch_size": batch_size,
            "sample_chunk_shots": 0,
            "clifft_scheduler": options,
        },
    )
    stats = prepared.runtime_metadata["scheduler_statistics"]
    assert stats["applied"]
    assert stats["result_peak"] < stats["incumbent_peak"]
    counts = prepared.sample(32768, 27)
    assert counts.attempted_shots == counts.accepted_shots + counts.discarded_shots
    assert counts.accepted_shots / counts.attempted_shots == pytest.approx(
        expected[0] + expected[1], abs=0.015
    )
    assert counts.logical_errors / counts.attempted_shots == pytest.approx(expected[1], abs=0.015)
