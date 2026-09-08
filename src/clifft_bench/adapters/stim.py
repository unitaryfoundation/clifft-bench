"""Stim's compiled circuit detector sampler, reduced to the aggregate contract."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter


class _PreparedStim(PreparedAdapter):
    def __init__(self, circuit: Any, *, chunk: int, postselect: bool, observable: int):
        import numpy as np
        import stim

        self._np = np
        self._circuit = circuit
        self._chunk = chunk
        self._postselect = postselect
        self._observable = observable
        self._sampler = None
        # Stim returns flips relative to its reference trajectory. Convert that
        # reference to *raw* parity once, including nonzero deterministic bits.
        reference = circuit.reference_sample()
        det_ref, obs_ref = circuit.compile_m2d_converter(skip_reference_sample=True).convert(
            measurements=reference[None, :], separate_observables=True
        )
        self._det_ref = np.packbits(det_ref, axis=1, bitorder="little")
        self._obs_ref = np.packbits(obs_ref, axis=1, bitorder="little")
        self._has_det_ref = bool(np.any(det_ref))
        self._has_obs_ref = bool(np.any(obs_ref))
        self.runtime_metadata = {
            "name": "stim",
            "version": stim.__version__,
            "threads": 1,
            "precision": "binary-stabilizer",
            "reference_convention": "raw-record-parity",
            "batch_enabled": chunk > 1,
            "effective_batch_size": chunk,
            "batch_parameter": "public-detector-sampler-chunk-shots",
            "seed_policy": "continuous-stream-seeded-once-per-phase-or-repetition",
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

    def begin_sample(self, seed: int) -> None:
        self._sampler = self._circuit.compile_detector_sampler(seed=seed)

    def sample(self, shots: int, seed: int) -> Counts:
        # The worker supplies seed+call as an audit identifier; Stim consumes a
        # persistent native RNG stream. Never recompile inside the timed call.
        del seed
        if self._sampler is None:
            raise RuntimeError("Stim random stream must be initialized before timing")
        np = self._np
        accepted = errors = 0
        for offset in range(0, shots, self._chunk):
            n = min(self._chunk, shots - offset)
            detectors, observables = self._sampler.sample(
                n, separate_observables=True, bit_packed=True
            )
            if self._has_det_ref:
                detectors ^= self._det_ref
            if self._has_obs_ref:
                observables ^= self._obs_ref
            keep = ~np.any(detectors, axis=1) if self._postselect else np.ones(n, dtype=bool)
            accepted += int(np.count_nonzero(keep))
            logical = (observables[:, self._observable // 8] >> (self._observable % 8)) & 1
            errors += int(np.count_nonzero(logical[keep]))
        return Counts(shots, accepted, shots - accepted, errors)


class StimAdapter(Adapter):
    name = "stim"

    def prepare(
        self, *, artifact_path: Path, workload: dict[str, Any], execution: dict[str, Any]
    ) -> PreparedAdapter:
        import stim

        if workload["semantics"]["reference_convention"] != "raw-record-parity":
            raise ValueError("Stim adapter requires raw-record-parity")
        started = time.perf_counter()
        circuit = stim.Circuit(artifact_path.read_text())
        parse_seconds = time.perf_counter() - started
        observable = int(workload["semantics"]["observable_index"])
        if not 0 <= observable < circuit.num_observables:
            raise ValueError("observable index is outside the circuit")
        chunk = int(execution["batch_size"])
        if chunk < 1 or execution.get("sample_chunk_shots", 0) != 0:
            raise ValueError("Stim uses batch_size for chunking; sample_chunk_shots must be 0")
        started = time.perf_counter()
        prepared = _PreparedStim(
            circuit,
            chunk=chunk,
            observable=observable,
            postselect=bool(workload["semantics"]["postselect_all_detectors"]),
        )
        prepared.runtime_metadata.update(
            parse_seconds=parse_seconds, reference_setup_seconds=time.perf_counter() - started
        )
        return prepared
