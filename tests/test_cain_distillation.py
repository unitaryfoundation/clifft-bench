from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import numpy as np
import pytest
import stim

from clifft_bench.adapters import load_adapter
from clifft_bench.config import read_toml
from workloads.generate_cain_distillation import SOURCE_SHA256, circuit_text

ROOT = Path(__file__).resolve().parents[1]


def _workload(distance):
    return next(w for w in read_toml(ROOT / "workloads.toml")["workloads"]
                if w["id"] == f"cain-15to1-d{distance}-p1e-3")


def _text(distance):
    return (ROOT / _workload(distance)["artifact"]["path"]).read_text()


def _layout(text):
    # Index records and annotations only; this is not a physics substitution.
    return stim.Circuit(re.sub(r"(?m)^T ", "I ", text))


@pytest.mark.parametrize("distance", [5, 9])
def test_conversion_preserves_the_published_source_except_injection_and_annotation(distance):
    text = _text(distance)
    annotation = "OBSERVABLE_INCLUDE(0) rec[-1]\n"
    assert text.endswith(annotation)
    source = re.sub(r"(?m)^T ", "S ", text.removesuffix(annotation)).encode("ascii")
    # Reversing only the declared edits recovers the exact published source,
    # including every noisy gate, measurement and detector record index.
    assert hashlib.sha256(source).hexdigest() == SOURCE_SHA256[distance]
    assert circuit_text(source, distance) == text
    assert _workload(distance)["provenance"]["source_sha256"] == SOURCE_SHA256[distance]
    with pytest.raises(ValueError, match="unmodified"):
        circuit_text(source + b"\n", distance)


@pytest.mark.parametrize("distance", [5, 9])
def test_metadata_and_terminal_logical_x_readout(distance):
    workload = _workload(distance)
    layout = _layout(_text(distance))
    assert {k: getattr(layout, k) for k in workload["expected_metadata"]} == (
        workload["expected_metadata"]
    )
    final_measurement = next(i for i in reversed(list(layout)) if i.name == "M")
    data = [t.value for t in final_measurement.targets_copy()]
    readout_boundary = data[-distance:]
    assert str(layout[-3]) == "MPP " + "*".join(f"Z{q}" for q in readout_boundary)
    assert str(layout[-2]) == "DETECTOR rec[-1]"
    # The final M follows transversal H on the output patch: this Z parity
    # is its logical-X readout, repeated by MPP without another basis change.
    gates = [i for i in layout if i.name not in {"DETECTOR", "OBSERVABLE_INCLUDE"}]
    assert gates[-3].name == "H"
    assert [t.value for t in gates[-3].targets_copy()] == data


def _encoded_logical_state(distance):
    # Collapse each patch to one qubit for an independent logical-level oracle.
    layout = _layout(_text(distance))
    patch_size = 2 * distance**2 - 1
    logical = stim.Circuit()
    for instruction in layout:
        if instruction.name == "R":
            # The first reset initializes the 16 factory patches.
            if not len(logical):
                logical.append("R", range(16))
        elif instruction.name == "H":
            patches = {t.value // patch_size for t in instruction.targets_copy()}
            if len(logical) == 1:
                logical.append("H", sorted(patches))
        elif instruction.name == "CX":
            targets = instruction.targets_copy()
            pairs = {(a.value // patch_size, b.value // patch_size)
                     for a, b in zip(targets[::2], targets[1::2])}
            inter_patch = sorted((a, b) for a, b in pairs if a != b)
            if any(b >= 16 for _, b in inter_patch):
                break  # Start of magic-state teleportation.
            for pair in inter_patch:
                logical.append("CX", pair)
    state = stim.TableauSimulator()
    state.do(logical)
    return state


@pytest.mark.parametrize("distance", [5, 9])
def test_unconditioned_ideal_output_is_maximally_mixed(distance):
    # Subsequent injections act only on patches 1..15, with no feedforward to 0.
    # Tracing their outcomes preserves patch 0's maximally mixed reduced state;
    # the final unconditioned gate teleportation also preserves that state.
    state = _encoded_logical_state(distance)
    assert state.peek_x(0) == state.peek_y(0) == state.peek_z(0) == 0


def _sample(adapter, distance, text, shots, tmp_path):
    module = pytest.importorskip(adapter)
    if adapter == "clifft" and not hasattr(module, "SamplingExecution"):
        if distance == 9:
            pytest.skip("d9 requires the new default compiler")
        # The 0.13 baseline exposes unpacked syndromes for conformance.
        program = module.compile(text)
        result = module.sample(program, shots, seed=91, threads=1, batch_size=1)
        return result.detectors, result.observables
    path = tmp_path / "cain.stim"
    path.write_text(text)
    prepared = load_adapter(adapter, module.version() if adapter == "clifft" else None).prepare(
        artifact_path=path, workload=_workload(distance),
        execution={"postselect": "off", "batch_size": "auto"}, seed=91,
    )
    layout = _layout(text)
    for key in _workload(distance)["expected_metadata"]:
        assert prepared.runtime_metadata[key] == getattr(layout, key)
    det, obs = prepared.sample(shots, 91)
    assert det.dtype == obs.dtype == np.uint8
    return (np.unpackbits(det, axis=1, bitorder="little")[:, :layout.num_detectors],
            np.unpackbits(obs, axis=1, bitorder="little")[:, :1])


@pytest.mark.parametrize("adapter,distance,noisy", [
    ("clifft", 5, False), ("clifft", 5, True), ("clifft", 9, False), ("clifft", 9, True),
    ("tsim", 5, False), ("tsim", 9, False),
    ("tsim", 5, True), ("tsim", 9, True),
])
def test_cain_syndromes_and_unconditioned_readout(adapter, distance, noisy, tmp_path):
    text = _text(distance)
    if not noisy:
        text = re.sub(r"(?m)^DEPOLARIZE[12].*\n", "", text)
    shots = 1024
    detectors, observables = _sample(adapter, distance, text, shots, tmp_path)
    assert detectors.shape == (shots, _workload(distance)["expected_metadata"]["num_detectors"])
    assert observables.shape == (shots, 1)
    np.testing.assert_array_equal(observables[:, 0], detectors[:, -1])
    assert abs(observables.sum() - shots / 2) < 6 * math.sqrt(shots / 4)
    checks = [i for i in _layout(text) if i.name == "DETECTOR"]
    qec = [k for k, check in enumerate(checks) if len(check.targets_copy()) != 1]
    if noisy:
        assert np.any(detectors[:, qec])
    else:
        assert not np.any(detectors[:, qec])


@pytest.mark.parametrize("adapter", ["clifft", "tsim"])
def test_terminal_readout_fault_flips_the_repeated_logical_bit(adapter, tmp_path):
    text = re.sub(r"(?m)^DEPOLARIZE[12].*\n", "", _text(5))
    readout = _layout(text)[-3]
    target = readout.targets_copy()[0].value
    # Measure both sides of the fault within each trajectory. Different compiled
    # circuits need not consume a fixed seed's random stream in the same way.
    text = text.removesuffix("OBSERVABLE_INCLUDE(0) rec[-1]\n")
    text += f"X {target}\n{readout}\nOBSERVABLE_INCLUDE(0) rec[-1]\n"
    detectors, observables = _sample(adapter, 5, text, 64, tmp_path)
    np.testing.assert_array_equal(detectors[:, -1], 1 - observables[:, 0])


@pytest.mark.parametrize("adapter", ["clifft", "tsim"])
def test_t_state_readout_correlations_match_the_logical_encoding(adapter, tmp_path):
    text = re.sub(r"(?m)^DEPOLARIZE[12].*\n", "", _text(5))
    shots = 32768
    detectors, _ = _sample(adapter, 5, text, shots, tmp_path)
    checks = [i for i in _layout(text) if i.name == "DETECTOR"]
    logical_readouts = [k for k, check in enumerate(checks) if len(check.targets_copy()) == 1]
    supports = [
        [q for q, axis in enumerate(pauli) if axis]
        for pauli in _encoded_logical_state(5).canonical_stabilizers()
        if set(pauli) <= {0, 1}
    ]
    assert supports
    for support in supports:
        # Ignoring each teleportation outcome sends X -> cos(pi/4)*X.
        # Thus an encoded X stabilizer of weight w has mean 2**(-w/2).
        # The S proxy instead gives zero: these correlations exercise the T gates.
        probability = (1 - 2 ** (-len(support) / 2)) / 2
        # Readouts 15..29 measure factory X's; 31 measures the output patch.
        indices = [logical_readouts[31 if q == 0 else 14 + q] for q in support]
        parity = np.bitwise_xor.reduce(detectors[:, indices], axis=1)
        tolerance = 6 * math.sqrt(shots * probability * (1 - probability))
        assert abs(int(parity.sum()) - shots * probability) < tolerance
