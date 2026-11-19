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


def prepare(tmp_path, text, *, batch=1, observable=0):
    pytest.importorskip("symft")
    path = tmp_path / "symft.stim"
    path.write_text(text)
    return SymftAdapter().prepare(
        artifact_path=path,
        workload={
            "semantics": {
                "observable_index": observable,
                "postselect_all_detectors": True,
            }
        },
        execution={
            "postselect": "on", "batch_size": batch,
            "options": {"sample_chunk_shots": 0 if batch == "auto" else 2048},
        },
    )


@pytest.mark.parametrize("batch", [1, 32, "auto"])
@pytest.mark.parametrize("seed", [1, 42, 74])
def test_survivor_counts_match_exact_noisy_law(
    tmp_path,
    batch,
    seed,
):
    p = prepare(tmp_path, NOISY, batch=batch)
    shots = 131072
    counts = p.sample(shots, seed)
    # Independent two-qubit Pauli enumeration: P(D,O) =
    # [841/900, 29/900, 1/60, 1/60] in 00,01,10,11 order.
    for actual, probability in [
        (counts.accepted_shots, 29 / 30),
        (counts.logical_errors, 29 / 900),
    ]:
        assert abs(actual - shots * probability) <= (
            6 * math.sqrt(shots * probability * (1 - probability)) + 1
        )
    assert counts.accepted_shots + counts.discarded_shots == shots
    assert p.runtime_metadata["sampling_backend"] == "compile_counts_sampler"


def test_packed_output_is_unsupported_before_import():
    from clifft_bench.adapters.base import UnsupportedConfiguration

    with pytest.raises(UnsupportedConfiguration, match="postselect=off"):
        SymftAdapter().validate_execution({"postselect": "off", "batch_size": "auto"}, {})
