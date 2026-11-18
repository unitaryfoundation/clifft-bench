"""Stim's compiled circuit detector sampler, returning native packed records."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from clifft_bench.adapters.base import Adapter, PreparedAdapter, Sample, UnsupportedConfiguration


class _PreparedStim(PreparedAdapter):
    def __init__(self, circuit: Any, *, seed: int):
        import numpy as np
        import stim

        # Stim returns flips relative to its reference trajectory. Convert that
        # reference to *raw* parity once, including nonzero deterministic bits.
        reference = circuit.reference_sample()
        det_ref, obs_ref = circuit.compile_m2d_converter(skip_reference_sample=True).convert(
            measurements=reference[None, :], separate_observables=True
        )
        if np.any(det_ref) or np.any(obs_ref):
            raise UnsupportedConfiguration(
                "Stim's reference-relative output differs from raw detector/observable parity"
            )
        started = time.perf_counter()
        self._sampler = circuit.compile_detector_sampler(seed=seed)
        compile_seconds = time.perf_counter() - started
        self.runtime_metadata = {
            "name": "stim",
            "version": stim.__version__,
            "threads": 1,
            "precision": "binary-stabilizer",
            "reference_convention": "raw-record-parity",
            "batch_enabled": None,
            "effective_batch_size": None,
            "batch_parameter": "native-detector-sampler",
            "seed_policy": "continuous-stream-seeded-at-prepare",
            "initial_seed": seed,
            "compile_seconds": compile_seconds,
            "record_materialization": "bit-packed-detectors-and-observables",
            "sampling_backend": "compiled-circuit-detector-sampler",
            "native_extensions": sorted(m for m in sys.modules if m.startswith("stim._stim_")),
            "num_qubits": circuit.num_qubits,
            "num_measurements": circuit.num_measurements,
            "num_detectors": circuit.num_detectors,
            "num_observables": circuit.num_observables,
            "raw_reference_detector_ones": int(det_ref.sum()),
            "raw_reference_observable_ones": int(obs_ref.sum()),
        }

    def sample(self, shots: int, seed: int) -> Sample:
        del seed  # The persistent sampler owns the RNG stream.
        return self._sampler.sample(shots, separate_observables=True, bit_packed=True)


class StimAdapter(Adapter):
    name = "stim"
    supported_postselect = ("off",)

    def prepare(
        self, *, artifact_path: Path, workload: dict[str, Any], execution: dict[str, Any],
        seed: int = 0,
    ) -> PreparedAdapter:
        self.validate_execution(execution, workload)
        import stim

        text = artifact_path.read_text()
        started = time.perf_counter()
        circuit = stim.Circuit(text)
        parse_seconds = time.perf_counter() - started
        observable = int(workload["semantics"]["observable_index"])
        if not 0 <= observable < circuit.num_observables:
            raise ValueError("observable index is outside the circuit")
        prepared = _PreparedStim(circuit, seed=seed)
        prepared.runtime_metadata["compile_seconds"] += parse_seconds
        return prepared
