import math

import pytest

from whole_machine.adapters import prepare
from whole_machine.common import (
    DIRECT,
    environment,
    file_digest,
    installed,
    matrix,
    validate_counts,
)


@pytest.fixture(autouse=True)
def cpu_environment(monkeypatch):
    for key, value in environment().items():
        monkeypatch.setenv(key, value)


def tiny_workload(tmp_path, circuit):
    import stim

    circuit = "R 0\n" + circuit
    path = tmp_path / "tiny.stim"
    path.write_text(circuit)
    parsed = stim.Circuit(circuit)
    return {
        "artifact_path": str(path),
        "artifact": {"sha256": file_digest(path)},
        "semantics": {
            "observable_index": 0,
            "postselect_all_detectors": True,
            "reference_convention": "raw-record-parity",
        },
        "expected_metadata": {
            k: getattr(parsed, k)
            for k in ("num_qubits", "num_measurements", "num_detectors", "num_observables")
        },
    }


@pytest.mark.integration
def test_installed_source_pins():
    installed()


@pytest.mark.integration
@pytest.mark.parametrize(
    "tool,batch",
    [("clifft", 1), ("clifft", 32), ("symft", 1), ("symft", 32), ("stim", 256)],
)
@pytest.mark.parametrize("scenario", ["discard", "logical-one", "noise"])
def test_raw_parity_and_aggregate_semantics(tmp_path, tool, batch, scenario):
    circuit = {
        "discard": "X 0\nM 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
        "logical-one": "X 0\nM 0\nDETECTOR rec[-1] rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
        "noise": "X_ERROR(0.125) 0\nM 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n",
    }[scenario]
    config = {
        "batch_size": batch,
        "sample_chunk_shots": 32 if tool == "symft" else 0,
        "shots_per_call": 32768,
    }
    prepared = prepare(
        tool,
        tiny_workload(tmp_path, circuit),
        config,
        2 if tool in {"clifft", "symft"} else 1,
        12345,
    )
    result = prepared.sample(32768)
    validate_counts(result)
    assert result["attempted_shots"] == 32768
    if scenario == "discard":
        assert result["accepted_shots"] == result["logical_errors"] == 0
    elif scenario == "logical-one":
        assert result["accepted_shots"] == result["logical_errors"] == 32768
    else:
        assert result["logical_errors"] == 0
        sigma = math.sqrt(32768 * 0.125 * 0.875)
        assert abs(result["discarded_shots"] - 32768 * 0.125) < 7 * sigma


@pytest.mark.integration
@pytest.mark.parametrize("identifier", DIRECT)
def test_actual_tsim_corpus_uses_direct_cpu(identifier):
    case = matrix(tools=["tsim"], workload_ids=[identifier])[0]
    config = {"batch_size": 0, "sample_chunk_shots": 0, "shots_per_call": 32768}
    p = prepare("tsim", case["workload"], config, 1, 92)
    assert p.metadata["execution_path"] == "direct-cpu"
    assert p.metadata["compiled_graph_count"] == 0
    observed = p.sample(32768)
    validate_counts(observed)
    reference = prepare("clifft", case["workload"], {**config, "batch_size": 32}, 2, 193)
    expected = reference.sample(32768)
    for key in ("accepted_shots", "logical_errors"):
        probability = (observed[key] + expected[key]) / (2 * 32768)
        sigma = math.sqrt(2 * 32768 * probability * (1 - probability))
        assert abs(observed[key] - expected[key]) <= 7 * sigma + 2


@pytest.mark.integration
def test_component_path_cannot_silently_enter_cpu_comparison(tmp_path):
    # Even some trivial Clifford circuits retain graph components in this Tsim
    # compiler. Check the compiled program, never infer its path from gate names.
    w = tiny_workload(tmp_path, "X 0\nM 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
    config = {"batch_size": 0, "sample_chunk_shots": 0, "shots_per_call": 256}
    with pytest.raises(ValueError, match="component evaluation"):
        prepare("tsim", w, config, 1, 92)
