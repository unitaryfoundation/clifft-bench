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
            batch_size=None if self._batch is None else min(self._batch, shots),
            separate_observables=True,
            bit_packed=False,
            use_detector_reference_sample=False,
            use_observable_reference_sample=False,
            postselection_mask=self._mask,
        )
        accepted = len(observables)
        if (
            detectors.shape != (accepted, self.runtime_metadata["num_detectors"])
            or observables.shape != (accepted, self.runtime_metadata["num_observables"])
            or detectors.dtype != np.bool_ or observables.dtype != np.bool_
            or not 0 <= accepted <= shots
            or (not self._postselect and accepted != shots)
        ):
            raise RuntimeError("Tsim returned inconsistent detector/observable rows")
        # Native postselection returns survivors only, including an empty array
        # when every attempted shot is rejected. The denominator remains shots.
        errors = int(np.count_nonzero(observables[:, self._observable]))
        return Counts(shots, accepted, shots - accepted, errors)


class TsimAdapter(Adapter):
    name = "tsim"
    native_threads = True

    def verify_installation(self, *, expected_commit: str, source_url: str) -> dict[str, Any]:
        return verify_source_installation(
            "bloqade-tsim", expected_commit=expected_commit, source_url=source_url
        )

    def prepare(
        self, *, artifact_path: Path, workload: dict, execution: dict, seed: int = 0
    ) -> PreparedAdapter:
        self.validate_options(execution.get("options", {}))
        requested_threads = self.thread_budget(execution)
        native = execution["batch_size"] == "native"
        # NUMBA_NUM_THREADS must be set before import: limiting BLAS/OpenMP
        # alone does not constrain Numba's independent thread pool.
        if os.environ.get("NUMBA_NUM_THREADS") != str(requested_threads):
            raise ValueError(f"Tsim requires NUMBA_NUM_THREADS={requested_threads} before import")
        import numba
        import tsim

        threads = numba.get_num_threads()
        if threads != requested_threads:
            raise RuntimeError(
                f"Tsim effective Numba thread count {threads} != {requested_threads}"
            )
        semantics = workload["semantics"]
        batch = None if native else int(execution["batch_size"])
        if batch is not None and not 1 <= batch <= MAX_BATCH_SIZE:
            raise ValueError("Tsim requires batch_size in [1, 65536]")
        started = time.perf_counter()
        circuit = tsim.Circuit(artifact_path.read_text())
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
            "batch_enabled": None if native else batch > 1, "effective_batch_size": batch,
            **({"batch_control": "native-auto"} if native else {}),
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
