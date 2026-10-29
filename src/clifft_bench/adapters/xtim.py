"""Exact xtim detector sampling with a fresh plan cache per repetition."""

from __future__ import annotations

import re
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from clifft_bench.adapters._shared import PackedCountsReducer, verify_source_installation
from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter


def _record_layout(text: str) -> Any:
    """Stim supplies only record indexing, never the non-Clifford physics."""
    import stim

    layout = re.sub(
        r"(?im)^([ \t]*)(?:T_DAG|T|CS_DAG|CS|CCZ_DAG|CCZ|CH)([ \t]+)",
        r"\1I\2",
        text,
    )
    return stim.Circuit(layout)


def _compile(text: str) -> Any:
    import xtim

    sampler = xtim.compile_twirl_sampler(text, selfcheck=0, disk_cache=False)
    if not hasattr(sampler, "record_report"):
        raise ValueError("xtim requires the pinned 3.2.2 record API with fixed stream continuation")
    report = sampler.record_report()
    declared_observables = {
        int(index) for index in re.findall(r"OBSERVABLE_INCLUDE\s*\(\s*(\d+)\s*\)", text, re.I)
    }
    if (
        report["nondeterministic_detectors"]
        or report["nondeterministic_observables"]
        or {
            int(index)
            for index, kind in report["classes"]["detectors"].items()
            if kind == "deterministic"
        }
        != set(range(sampler.num_detectors))
        or {
            int(index)
            for index, kind in report["classes"]["observables"].items()
            if kind == "deterministic"
        }
        != declared_observables
    ):
        raise ValueError(
            f"xtim requires deterministic noiseless detectors and observables: {report}"
        )
    return sampler


class _PreparedXtim(PreparedAdapter):
    def __init__(
        self, text, chunk, observable, postselect, det_ref, obs_ref, detector_mask, metadata
    ):
        self._text = text
        self._chunk = chunk
        self._detector_mask = detector_mask
        self._counts = PackedCountsReducer(
            det_ref, obs_ref, observable=observable,
            postselect=postselect and detector_mask is None,
        )
        self.runtime_metadata = metadata
        self._sampler = None

    def begin_sample(self, seed: int) -> None:
        # Release the previous cache before compiling outside the sampling timer.
        # Lazy plan construction remains timed as the cache warms within a sample.
        self._sampler = None
        self._sampler = _compile(self._text)

    def sample(self, shots: int, seed: int) -> Counts:
        if self._sampler is None:
            raise RuntimeError("xtim sampler must be initialized before timing")
        if shots < 1:
            raise ValueError("xtim shots per call must be positive")
        attempted = accepted = discarded = errors = 0
        for offset in range(0, shots, self._chunk):
            n = min(self._chunk, shots - offset)
            # Reseed once per harness call, then continue the stream for chunks.
            chunk_seed = seed if offset == 0 else None
            if self._detector_mask is None:
                dets, obs = self._sampler.sample(n, seed=chunk_seed)
                attempts = n
            else:
                dets, obs, native = self._sampler.sample(
                    n, seed=chunk_seed, postselect=self._detector_mask
                )
                attempts = int(native["attempts"])
                passed = int(native["passed"])
                rejected = int(native["discarded"])
                if (
                    attempts != n
                    or passed != len(dets)
                    or passed != len(obs)
                    or passed + rejected != attempts
                    or not 0 <= int(native["early"]) <= rejected
                ):
                    raise RuntimeError(f"xtim returned inconsistent postselection counts: {native}")
            kept, logical_errors = self._counts.count(dets, obs)
            attempted += attempts
            accepted += kept
            discarded += attempts - kept
            errors += logical_errors
        return Counts(attempted, accepted, discarded, errors)


class XtimAdapter(Adapter):
    name = "xtim"

    def verify_installation(self, *, expected_commit: str, source_url: str) -> dict[str, Any]:
        return verify_source_installation(
            self.name, expected_commit=expected_commit, source_url=source_url
        )

    def prepare(
        self, *, artifact_path: Path, workload: dict, execution: dict, seed: int = 0
    ) -> PreparedAdapter:
        import numpy as np
        import xtim

        semantics = workload["semantics"]
        if semantics["reference_convention"] != "raw-record-parity":
            raise ValueError("xtim adapter requires raw-record-parity")
        chunk = int(execution["batch_size"])
        if chunk < 1 or execution.get("sample_chunk_shots", 0) != 0:
            raise ValueError("xtim uses batch_size for chunking; sample_chunk_shots must be 0")
        started = time.perf_counter()
        text = artifact_path.read_text()
        circuit = xtim.Circuit(text)
        if circuit.num_expectations:
            raise ValueError("PAULI_EXPECTATION is outside the aggregate-count contract")
        layout = _record_layout(text)
        observable = int(semantics["observable_index"])
        if not 0 <= observable < layout.num_observables:
            raise ValueError("observable index is outside the circuit")
        parse_seconds = time.perf_counter() - started
        started = time.perf_counter()
        sampler = _compile(text)
        report = sampler.record_report()
        del sampler
        compile_seconds = time.perf_counter() - started

        # Certified deterministic channels have a unique noiseless raw parity.
        # Recover it from an exact raw trajectory, then use Stim ONLY to XOR the
        # declared record indices. A private cache prevents cross-case disk reuse.
        started = time.perf_counter()
        previous_cache = xtim.cache_dir
        try:
            with TemporaryDirectory(prefix="clifft-bench-xtim-reference-") as cache:
                xtim.cache_dir = cache
                records = circuit.without_noise().compile_sampler(seed=0).sample(1)
        finally:
            xtim.cache_dir = previous_cache
        det_ref, obs_ref = layout.compile_m2d_converter(skip_reference_sample=True).convert(
            measurements=records, separate_observables=True
        )
        postselect = bool(semantics["postselect_all_detectors"])
        # The native mask accepts zero reference-relative detector bits. It
        # implements raw-zero selection only when every raw reference is zero.
        detector_mask = (
            np.ones(layout.num_detectors, dtype=bool)
            if postselect and not np.any(det_ref) else None
        )
        metadata = {
            "name": "xtim",
            "version": xtim.__version__,
            "threads": 1,
            "precision": "complex-fp64",
            "reference_convention": "raw-record-parity",
            "batch_enabled": chunk > 1,
            "effective_batch_size": chunk,
            "batch_parameter": "public-twirl-sampler-chunk-shots",
            "seed_policy": "per-call-seed-with-continuous-chunks",
            "sampling_backend": "compile_twirl_sampler",
            "record_materialization": (
                "bit-packed-accepted-detectors-and-observables"
                if detector_mask is not None else "bit-packed-detectors-and-observables"
            ),
            "postselection_backend": (
                "native-twirl-sampler" if detector_mask is not None
                else "adapter-raw-record-parity" if postselect else "disabled"
            ),
            "cache_policy": "fresh-per-repetition-with-untimed-compilation",
            "disk_cache": False,
            "selfcheck": 0,
            "record_report": report,
            "parse_seconds": parse_seconds,
            "compile_seconds": compile_seconds,
            "reference_setup_seconds": time.perf_counter() - started,
            "raw_reference_detector_ones": int(det_ref.sum()),
            "raw_reference_observable_ones": int(obs_ref.sum()),
            "native_num_qubits": circuit.num_qubits,
            **{
                key: getattr(layout, key)
                for key in ("num_qubits", "num_measurements", "num_detectors", "num_observables")
            },
        }
        return _PreparedXtim(
            text,
            chunk,
            observable,
            postselect,
            np.packbits(det_ref, axis=1, bitorder="little"),
            np.packbits(obs_ref, axis=1, bitorder="little"),
            detector_mask,
            metadata,
        )
