"""CPU Tsim active-statevector sampling with a continuous seeded stream."""

from __future__ import annotations

import os
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from clifft_bench.adapters._shared import verify_source_installation
from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter
from clifft_bench.selection import detector_mask, has_postselection

MAX_BATCH_SIZE = 65536


class _PreparedTsim(PreparedAdapter):
    def __init__(self, sampler, *, batch, postselect, observable, metadata, selection=None):
        import numpy as np

        self._sampler = sampler
        self._batch = batch
        self._observable = observable
        self._postselect = postselect
        self._mask = np.ones(sampler.circuit.num_detectors, dtype=bool) if postselect else None
        self._partial_selection = selection is not None
        if selection is not None:
            self._mask = np.asarray(selection, dtype=bool)
        self.runtime_metadata = metadata

    def sample(self, shots: int, seed: int) -> Counts:
        # Tsim exposes a seed at compilation, not per call. Never replay or
        # recompile the sampler inside a repetition; the call seed is an audit ID.
        del seed
        import numpy as np

        if shots < 1:
            raise ValueError("Tsim shots per call must be positive")
        detectors, observables = self._sampler.sample(
            shots=shots,
            batch_size=min(self._batch, shots),
            separate_observables=True,
            bit_packed=False,
            use_detector_reference_sample=False,
            use_observable_reference_sample=False,
            postselection_mask=self._mask,
        )
        if detectors.shape != (shots, self.runtime_metadata["num_detectors"]) or (
            observables.shape != (shots, self.runtime_metadata["num_observables"])
        ):
            raise RuntimeError("Tsim must return one detector/observable row per attempted shot")
        # Rejected rows retain the rejecting detector but later outputs can be
        # zero-filled. Only count the requested observable on surviving rows.
        checked = detectors[:, self._mask] if self._partial_selection else detectors
        keep = ~np.any(checked, axis=1) if self._postselect else np.ones(shots, dtype=bool)
        accepted = int(np.count_nonzero(keep))
        errors = int(np.count_nonzero(observables[keep, self._observable]))
        return Counts(shots, accepted, shots - accepted, errors)


class TsimAdapter(Adapter):
    name = "tsim"

    def verify_installation(self, *, expected_commit: str, source_url: str) -> dict[str, Any]:
        return verify_source_installation(
            "bloqade-tsim", expected_commit=expected_commit, source_url=source_url
        )

    def prepare(
        self, *, artifact_path: Path, workload: dict, execution: dict, seed: int = 0
    ) -> PreparedAdapter:
        # NUMBA_NUM_THREADS must be set before import: limiting BLAS/OpenMP
        # alone does not constrain Numba's independent thread pool.
        if os.environ.get("NUMBA_NUM_THREADS") != "1":
            raise ValueError("Tsim requires NUMBA_NUM_THREADS=1 before importing numba")
        import numba
        import tsim

        threads = numba.get_num_threads()
        if threads != 1:
            raise RuntimeError(f"Tsim requires one effective Numba thread, got {threads}")
        semantics = workload["semantics"]
        if semantics["reference_convention"] != "raw-record-parity":
            raise ValueError("Tsim adapter requires raw-record-parity")
        batch = int(execution["batch_size"])
        if not 1 <= batch <= MAX_BATCH_SIZE or execution.get("sample_chunk_shots", 0) != 0:
            raise ValueError("Tsim requires batch_size in [1, 65536] and sample_chunk_shots=0")
        sharing = execution.get("tsim_trajectory_sharing", True)
        if not isinstance(sharing, bool):
            raise ValueError("tsim_trajectory_sharing must be boolean")
        started = time.perf_counter()
        circuit = tsim.Circuit(artifact_path.read_text())
        selection = detector_mask(semantics, circuit.num_detectors)
        observable = int(semantics["observable_index"])
        if not 0 <= observable < circuit.num_observables:
            raise ValueError("observable index is outside the circuit")
        parse_seconds = time.perf_counter() - started
        started = time.perf_counter()
        sampler = circuit.compile_detector_sampler(
            strategy="active_statevector", trajectory_sharing=sharing, seed=seed
        )
        compile_seconds = time.perf_counter() - started
        # Read-only introspection of the pinned implementation for provenance.
        # Sampling and RNG initialization use the public API exclusively.
        widths = [component.width for component in sampler._program.components]
        metadata = {
            "name": "tsim", "version": version("bloqade-tsim"), "threads": threads,
            "precision": "complex-fp64", "reference_convention": "raw-record-parity",
            "sampling_backend": "active_statevector", "trajectory_sharing": sharing,
            "batch_enabled": batch > 1, "effective_batch_size": batch,
            "batch_parameter": "statevector-batch-shots",
            "batch_size_applicable": bool(sampler._program.components),
            "seed_policy": "continuous-stream-seeded-at-prepare",
            "initial_seed": seed,
            "record_materialization": "boolean-detectors-and-observables",
            "parse_seconds": parse_seconds, "compile_seconds": compile_seconds,
            "peak_active_width": max(widths, default=0),
            "statevector_component_widths": widths,
            "warmup_full_call": True,
            "jit_cache_policy": "upstream-numba-disk-cache; warm-before-timing",
            **{key: getattr(circuit, key) for key in (
                "num_qubits", "num_measurements", "num_detectors", "num_observables"
            )},
        }
        # Ensure the quantum kernels are compiled even if every shot in the
        # later postselected warmup is rejected by a direct detector. This is
        # an untimed one-shot execution, not a pure compiler-duration claim.
        started = time.perf_counter()
        sampler.sample(
            shots=1, batch_size=1, separate_observables=True,
            use_detector_reference_sample=False, use_observable_reference_sample=False,
        )
        metadata["kernel_warmup_seconds"] = time.perf_counter() - started
        metadata["kernel_warmup_shots"] = 1
        return _PreparedTsim(
            sampler, batch=batch, postselect=has_postselection(semantics),
            selection=selection if "postselection_detector_indices" in semantics else None,
            observable=observable, metadata=metadata,
        )
