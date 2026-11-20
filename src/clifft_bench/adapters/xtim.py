"""Exact xtim detector sampling with a worker-local plan cache."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from clifft_bench.adapters._shared import verify_source_installation
from clifft_bench.adapters.base import (
    Adapter,
    Counts,
    PreparedAdapter,
    Sample,
    UnsupportedConfiguration,
    UnsupportedReferenceCircuit,
)
from clifft_bench.selection import detector_mask


def _compile(text: str) -> Any:
    import xtim

    return xtim.compile_twirl_sampler(text, p_factor=1.0, selfcheck=0)


def _reference_metadata(data, workload, mode, mask, observable):
    import numpy as np
    import stim

    source = workload["semantics"].get("raw_zero_reference", {}).get(mode)
    if source and hashlib.sha256(data).hexdigest() != workload["artifact"]["sha256"]:
        raise ValueError("xtim reference declaration does not match the circuit SHA-256")
    try:
        layout = stim.Circuit(data.decode())
    except ValueError as error:
        if source == "stim":
            raise ValueError("Stim could not parse the declared reference circuit") from error
        if source != "clifft":
            raise UnsupportedReferenceCircuit(
                "xtim raw reference parity is unavailable for this circuit: " + str(error)
            ) from error
        # xtim counts synthetic ancillas when it expands MPP. CI checks the input
        # qubit count and raw parities with Clifft for these exact circuit bytes.
        return {"num_qubits": workload["expected_metadata"]["num_qubits"]}

    det_ref, obs_ref = layout.compile_m2d_converter(skip_reference_sample=True).convert(
        measurements=layout.reference_sample()[None, :], separate_observables=True
    )
    selected_refs = det_ref[0, mask] if mask is not None else det_ref[0]
    observed_refs = obs_ref[0, observable] if mask is not None else obs_ref[0]
    if np.any(selected_refs) or np.any(observed_refs):
        raise UnsupportedConfiguration("xtim requires zero raw reference parity")
    return {
        "num_qubits": layout.num_qubits,
        "raw_reference_detector_ones": int(det_ref.sum()),
        "raw_reference_observable_ones": int(obs_ref.sum()),
    }


class _PreparedXtim(PreparedAdapter):
    def __init__(self, sampler, chunk, observable, mask, threads, metadata):
        self._chunk = chunk
        self._multiple_chunks = False
        self._detector_mask = mask
        self._threads = threads
        self._observable = observable
        self.runtime_metadata = metadata
        self._sampler = sampler

    def first_call_metadata(self) -> dict:
        # Explicit multi-chunk counts calls can use several worker widths.
        if self._multiple_chunks:
            return {"threads": None}
        plan = self._sampler.record_report()["threads"]
        if plan["requested"] != self._threads or not 1 <= plan["effective"] <= self._threads:
            raise ValueError(f"xtim returned an invalid thread plan: {plan}")
        return {"threads": plan["effective"]}

    def sample(self, shots: int, seed: int) -> Sample:
        import numpy as np

        if shots < 1:
            raise ValueError("xtim shots per call must be positive")
        self._multiple_chunks = self._chunk is not None and shots > self._chunk
        if self._detector_mask is None:
            return self._sampler.sample(shots, seed=seed, threads=self._threads)
        attempted = accepted = discarded = errors = 0
        chunk = self._chunk or shots
        for offset in range(0, shots, chunk):
            n = min(chunk, shots - offset)
            # Reseed once per harness call, then continue the stream for chunks.
            chunk_seed = seed if offset == 0 else None
            dets, obs, native = self._sampler.sample(
                n, seed=chunk_seed, postselect=self._detector_mask, threads=self._threads
            )
            attempts = int(native["attempts"])
            kept = int(native["passed"])
            rejected = int(native["discarded"])
            if (attempts != n or kept != len(dets) or kept != len(obs)
                    or kept + rejected != attempts or not 0 <= int(native["early"]) <= rejected):
                raise RuntimeError(f"xtim returned inconsistent postselection counts: {native}")
            logical_errors = int(np.count_nonzero(
                obs[:, self._observable // 8] & (1 << (self._observable % 8))
            ))
            attempted += attempts
            accepted += kept
            discarded += attempts - kept
            errors += logical_errors
        return Counts(attempted, accepted, discarded, errors)


class XtimAdapter(Adapter):
    name = "xtim"
    supported_postselect = ("on", "off")
    supported_threads = (1, 8)

    def validate_execution(self, execution, workload):
        threads = super().validate_execution(execution, workload)
        if execution["postselect"] == "off" and execution["batch_size"] != "auto":
            raise ValueError('packed xtim sampling requires batch_size="auto"')
        return threads

    def verify_installation(self, *, expected_commit: str, source_url: str) -> dict[str, Any]:
        return verify_source_installation(
            self.name, expected_commit=expected_commit, source_url=source_url
        )

    def prepare(
        self, *, artifact_path: Path, workload: dict, execution: dict, seed: int = 0
    ) -> PreparedAdapter:
        threads = self.validate_execution(execution, workload)
        import numpy as np
        import xtim

        semantics = workload["semantics"]
        postselect = execution["postselect"] == "on"
        chunk = None if execution["batch_size"] == "auto" else int(execution["batch_size"])
        data = artifact_path.read_bytes()
        text = data.decode()
        circuit = xtim.Circuit(text)
        if circuit.num_expectations:
            raise ValueError("PAULI_EXPECTATION is outside the detector/observable contract")
        observable = int(semantics["observable_index"])
        if not 0 <= observable < circuit.num_observables:
            raise ValueError("observable index is outside the circuit")
        mask = None
        if postselect:
            selected = detector_mask(semantics, circuit.num_detectors)
            mask = np.zeros(circuit.num_detectors, dtype=bool)
            if selected:
                mask[:] = selected
        reference = _reference_metadata(data, workload, execution["postselect"], mask, observable)

        started = time.perf_counter()
        sampler = _compile(text)
        compile_seconds = time.perf_counter() - started
        report = sampler.record_report()
        random_dets = report["nondeterministic_detectors"]
        random_obs = report["nondeterministic_observables"]
        random_required_detectors = (
            any(mask[i] for i in random_dets) if postselect else bool(random_dets)
        )
        random_required_observables = observable in random_obs if postselect else bool(random_obs)
        if random_required_detectors or random_required_observables:
            raise UnsupportedConfiguration("xtim requires deterministic reference channels")
        metadata = {
            "name": "xtim",
            "version": xtim.__version__,
            "threads": None,
            "precision": "complex-fp64",
            "reference_convention": "raw-record-parity",
            "batch_enabled": None if chunk is None else chunk > 1,
            "effective_batch_size": chunk,
            "batch_parameter": "public-twirl-sampler-chunk-shots",
            "seed_policy": "per-call-seed-with-continuous-chunks",
            "sampling_backend": "compile_twirl_sampler",
            "record_materialization": ("bit-packed-accepted-detectors-and-observables" if postselect
                                       else "bit-packed-detectors-and-observables"),
            "postselection_backend": "native-twirl-sampler" if postselect else None,
            "cache_policy": "worker-local; reused-after-first-call",
            "selfcheck": 0,
            "compile_seconds": compile_seconds,
            **reference,
            "native_num_qubits": circuit.num_qubits,
            "num_detectors": circuit.num_detectors,
            "num_observables": circuit.num_observables,
            "num_measurements": circuit.num_measurements,
        }
        return _PreparedXtim(
            sampler,
            chunk,
            observable,
            mask,
            threads,
            metadata,
        )
