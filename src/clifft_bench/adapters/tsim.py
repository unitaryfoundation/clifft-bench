"""CPU Tsim active-statevector sampling with a continuous seeded stream."""

from __future__ import annotations

import os
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from clifft_bench.adapters._shared import verify_source_installation
from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter, Sample
from clifft_bench.selection import detector_mask

MAX_BATCH_SIZE = 65536


class _PreparedTsim(PreparedAdapter):
    def __init__(self, sampler, *, batch, postselect, observable, metadata, selection=None):
        import numpy as np

        self._sampler = sampler
        self._batch = batch
        self._observable = observable
        self._postselect = postselect
        self._mask = np.ones(sampler.circuit.num_detectors, dtype=bool) if postselect else None
        if postselect and selection is not None:
            self._mask = np.asarray(selection, dtype=bool)
        self.runtime_metadata = metadata

    def sample(self, shots: int, seed: int) -> Sample:
        # Tsim exposes a seed at compilation, not per call. Never replay or
        # recompile the sampler inside a repetition; the call seed is an audit ID.
        del seed
        import numpy as np

        if shots < 1:
            raise ValueError("Tsim shots per call must be positive")
        detectors, observables = self._sampler.sample(
            shots=shots,
            batch_size=min(self._batch, shots) if self._batch is not None else None,
            separate_observables=True,
            bit_packed=True,
            use_detector_reference_sample=False,
            use_observable_reference_sample=False,
            postselection_mask=self._mask,
        )
        if not self._postselect:
            return detectors, observables
        accepted = len(observables)
        if (
            detectors.shape != (accepted, (self.runtime_metadata["num_detectors"] + 7) // 8)
            or observables.shape != (accepted, (self.runtime_metadata["num_observables"] + 7) // 8)
            or detectors.dtype != np.uint8 or observables.dtype != np.uint8
            or not 0 <= accepted <= shots
        ):
            raise RuntimeError("Tsim returned inconsistent detector/observable rows")
        # Native postselection returns survivors only, including an empty array
        # when every attempted shot is rejected. The denominator remains shots.
        errors = int(np.count_nonzero(
            observables[:, self._observable // 8] & (1 << (self._observable % 8))
        ))
        return Counts(shots, accepted, shots - accepted, errors)


class TsimAdapter(Adapter):
    name = "tsim"
    supported_threads = (1, 8)
    supported_postselect = ("on", "off")

    def verify_installation(self, *, expected_commit: str, source_url: str) -> dict[str, Any]:
        return verify_source_installation(
            "bloqade-tsim", expected_commit=expected_commit, source_url=source_url
        )

    def prepare(
        self, *, artifact_path: Path, workload: dict, execution: dict, seed: int = 0
    ) -> PreparedAdapter:
        requested_threads = self.validate_execution(execution, workload)
        # NUMBA_NUM_THREADS must be set before import: limiting BLAS/OpenMP
        # alone does not constrain Numba's independent thread pool.
        if os.environ.get("NUMBA_NUM_THREADS") != str(requested_threads):
            raise ValueError(
                f"Tsim requires NUMBA_NUM_THREADS={requested_threads} before importing numba"
            )
        import numba
        import tsim

        threads = numba.get_num_threads()
        if threads != requested_threads:
            raise RuntimeError(
                f"Tsim requires {requested_threads} effective Numba threads, got {threads}"
            )
        semantics = workload["semantics"]
        batch = None if execution["batch_size"] == "auto" else int(execution["batch_size"])
        if batch is not None and batch > MAX_BATCH_SIZE:
            raise ValueError("Tsim requires batch_size in [1, 65536]")
        text = artifact_path.read_text()
        started = time.perf_counter()
        circuit = tsim.Circuit(text)
        selection = detector_mask(semantics, circuit.num_detectors)
        observable = int(semantics["observable_index"])
        if not 0 <= observable < circuit.num_observables:
            raise ValueError("observable index is outside the circuit")
        parse_seconds = time.perf_counter() - started
        started = time.perf_counter()
        sampler = circuit.compile_detector_sampler(
            strategy="active_statevector", seed=seed
        )
        compile_seconds = time.perf_counter() - started
        # Read-only introspection of the pinned implementation for provenance.
        # Sampling and RNG initialization use the public API exclusively.
        widths = [component.width for component in sampler._program.components]
        metadata = {
            "name": "tsim", "version": version("bloqade-tsim"), "threads": threads,
            "precision": "complex-fp64", "reference_convention": "raw-record-parity",
            "sampling_backend": "active_statevector", "trajectory_sharing": True,
            "batch_enabled": None if batch is None else batch > 1, "effective_batch_size": batch,
            "batch_parameter": "statevector-batch-shots",
            "batch_size_applicable": bool(sampler._program.components),
            "seed_policy": "continuous-stream-seeded-at-prepare",
            "initial_seed": seed,
            "record_materialization": "bit-packed-detectors-and-observables",
            "parse_seconds": parse_seconds, "compile_seconds": parse_seconds + compile_seconds,
            "peak_active_width": max(widths, default=0),
            "statevector_component_widths": widths,
            "jit_cache_policy": "upstream-numba-disk-cache; no-harness-cache-clearing",
            **{key: getattr(circuit, key) for key in (
                "num_qubits", "num_measurements", "num_detectors", "num_observables"
            )},
        }
        return _PreparedTsim(
            sampler, batch=batch, postselect=execution["postselect"] == "on",
            selection=selection if "postselection_detector_indices" in semantics else None,
            observable=observable, metadata=metadata,
        )
