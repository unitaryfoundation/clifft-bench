from __future__ import annotations

import math

import pytest

from clifft_bench.adapters.symft import SymftAdapter

NOISY = """H 0
DEPOLARIZE1(0.05) 0 1
T 0
CX 1 0
T_DAG 0
CX 1 0
M 1
DETECTOR rec[-1]
H 0
M 0
OBSERVABLE_INCLUDE(0) rec[-1]
"""


def prepare(tmp_path, text, *, batch=1, observable=0, postselect=False):
    pytest.importorskip("symft")
    path = tmp_path / "symft.stim"
    path.write_text(text)
    return SymftAdapter().prepare(
        artifact_path=path,
        workload={
            "semantics": {
                "reference_convention": "raw-record-parity",
                "observable_index": observable,
                "postselect_all_detectors": postselect,
            }
        },
        execution={"batch_enabled": batch > 1, "batch_size": batch, "sample_chunk_shots": 2048},
    )


@pytest.mark.parametrize("batch", [1, 32])
@pytest.mark.parametrize("observable,ones", [(0, 0), (8, 0), (9, 67)])
def test_unselected_fired_detector_preserves_observable_parity(
    tmp_path,
    batch,
    observable,
    ones,
):
    # Ten records cross a packed-byte boundary. Repeated OBSERVABLE_INCLUDE
    # statements and repeated references within one statement must all XOR.
    text = (
        "X 0\nM 0\nDETECTOR rec[-1]\nM 1 2 3 4 5 6 7 8\nX 9\nM 9\n"
        "OBSERVABLE_INCLUDE(0) rec[-9]\n"
        "OBSERVABLE_INCLUDE(9) rec[-10] rec[-1]\n"
        "OBSERVABLE_INCLUDE(9) rec[-1]\n"
        "OBSERVABLE_INCLUDE(9) rec[-10] rec[-10]\n"
    )
    p = prepare(tmp_path, text, batch=batch, observable=observable)
    counts = p.sample(67, 74)
    assert counts.attempted_shots == counts.accepted_shots == 67
    assert counts.discarded_shots == 0
    assert counts.logical_errors == ones
    assert p.sample(67, 74) == counts
    assert p.runtime_metadata["sampling_backend"] == "compile_sampler"
    assert p.runtime_metadata["record_materialization"] == "bit-packed-measurements"
    assert p.runtime_metadata["effective_batch_size"] == batch
    assert p.runtime_metadata["sample_chunk_shots"] == (2048 if batch == 1 else 0)


@pytest.mark.parametrize("batch", [1, 32])
@pytest.mark.parametrize("postselect", [False, True])
@pytest.mark.parametrize("seed", [1, 42, 74])
def test_noisy_selected_and_unselected_counts_match_exact_law(
    tmp_path,
    batch,
    postselect,
    seed,
):
    p = prepare(tmp_path, NOISY, batch=batch, postselect=postselect)
    shots = 131072
    counts = p.sample(shots, seed)
    # Independent two-qubit Pauli enumeration: P(D,O) =
    # [841/900, 29/900, 1/60, 1/60] in 00,01,10,11 order.
    for actual, probability in [
        (counts.accepted_shots, 29 / 30 if postselect else 1),
        (counts.logical_errors, 29 / 900 if postselect else 11 / 225),
    ]:
        assert abs(actual - shots * probability) <= (
            6 * math.sqrt(shots * probability * (1 - probability)) + 1
        )
    assert counts.accepted_shots + counts.discarded_shots == shots
    if not postselect:
        assert counts.discarded_shots == 0
    assert p.runtime_metadata["sampling_backend"] == (
        "compile_counts_sampler" if postselect else "compile_sampler"
    )


def test_detector_free_unselected_work_keeps_native_counts(tmp_path):
    p = prepare(tmp_path, "X 0\nM 0\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
    assert p.runtime_metadata["sampling_backend"] == "compile_counts_sampler"
    counts = p.sample(17, 74)
    assert counts.accepted_shots == counts.logical_errors == 17
    assert counts.discarded_shots == 0


@pytest.mark.parametrize("observable", [-1, 1])
def test_unselected_records_reject_invalid_observable(tmp_path, observable):
    with pytest.raises(ValueError, match="observable index"):
        prepare(tmp_path, NOISY, observable=observable)
