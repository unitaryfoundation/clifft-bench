"""Tsim detector/observable aggregate-count adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter


class _PreparedTsim(PreparedAdapter):
    def __init__(
        self,
        sampler: Any,
        numpy: Any,
        *,
        batch_size: int,
        observable_index: int,
        postselection_mask: Any,
        runtime_metadata: dict[str, Any],
    ) -> None:
        self._sampler = sampler
        self._numpy = numpy
        self._batch_size = batch_size
        self._observable_index = observable_index
        self._postselection_mask = postselection_mask
        self.runtime_metadata = runtime_metadata

    def sample(self, shots: int, seed: int) -> Counts:
        del seed  # Tsim owns a reproducible advancing stream on the prepared sampler.
        detectors, observables = self._sampler.sample(
            shots,
            batch_size=self._batch_size,
            separate_observables=True,
            use_detector_reference_sample=False,
            use_observable_reference_sample=False,
            postselection_mask=self._postselection_mask,
        )
        if self._postselection_mask is None:
            discarded_rows = self._numpy.zeros(shots, dtype=self._numpy.bool_)
        else:
            discarded_rows = detectors[:, self._postselection_mask].any(axis=1)
        accepted_rows = ~discarded_rows
        return Counts(
            attempted_shots=shots,
            accepted_shots=int(accepted_rows.sum()),
            discarded_shots=int(discarded_rows.sum()),
            logical_errors=int(observables[accepted_rows, self._observable_index].sum()),
        )


class TsimAdapter(Adapter):
    name = "tsim"

    def prepare(
        self,
        *,
        artifact_path: Path,
        workload: dict[str, Any],
        execution: dict[str, Any],
    ) -> PreparedAdapter:
        reference_convention = str(workload["semantics"]["reference_convention"])
        if reference_convention != "raw-record-parity":
            raise ValueError(
                f"Tsim adapter does not support reference convention "
                f"{reference_convention!r}"
            )

        import jax
        import numpy as np
        import tsim

        circuit = tsim.Circuit(artifact_path.read_text())
        sampler = circuit.compile_detector_sampler(strategy="cat5", seed=0)
        postselect = bool(workload["semantics"]["postselect_all_detectors"])
        postselection_mask = (
            np.ones(circuit.num_detectors, dtype=np.bool_) if postselect else None
        )
        batch_size = int(execution["batch_size"])
        metadata = {
            "name": "tsim",
            "version": str(tsim.__version__),
            "threads": 1,
            "precision": "complex-fp64",
            "reference_convention": reference_convention,
            "batch_enabled": True,
            "effective_batch_size": batch_size,
            "num_qubits": int(circuit.num_qubits),
            "num_measurements": int(circuit.num_measurements),
            "num_detectors": int(circuit.num_detectors),
            "num_observables": int(circuit.num_observables),
            "decomposition_strategy": "cat5",
            "jax_backend": str(jax.default_backend()),
            "jax_x64_enabled": bool(jax.config.x64_enabled),
            "seed_semantics": "fixed prepared stream advanced across calls",
        }
        return _PreparedTsim(
            sampler,
            np,
            batch_size=batch_size,
            observable_index=int(workload["semantics"]["observable_index"]),
            postselection_mask=postselection_mask,
            runtime_metadata=metadata,
        )
