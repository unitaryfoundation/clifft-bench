"""Clifft aggregate-count adapter."""

from __future__ import annotations

import json
import time
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

from clifft_bench.adapters._shared import verify_source_installation
from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter
from clifft_bench.selection import detector_mask


class _PreparedClifft(PreparedAdapter):
    def __init__(
        self,
        clifft: Any,
        program: Any,
        observable_index: int,
        runtime_metadata: dict[str, Any],
        batch_size: int,
    ) -> None:
        self._clifft = clifft
        self._program = program
        self._observable_index = observable_index
        self._batch_size = batch_size
        self.runtime_metadata = runtime_metadata

    def sample(self, shots: int, seed: int) -> Counts:
        result = self._clifft.sample_survivors(
            self._program, shots, seed=seed, keep_records=False,
            threads=1, batch_size=self._batch_size,
        )
        return Counts(
            attempted_shots=int(result.total_shots),
            accepted_shots=int(result.passed_shots),
            discarded_shots=int(result.discards),
            logical_errors=int(result.observable_ones[self._observable_index]),
        )


class ClifftAdapter(Adapter):
    name = "clifft"

    def verify_installation(self, *, expected_commit: str, source_url: str) -> dict[str, Any]:
        installed = distribution("clifft")
        direct_url = json.loads(installed.read_text("direct_url.json") or "{}")
        # Release wheels retain their existing version check. Development builds
        # and VCS installs must prove the full source identity, not just a short hash.
        if ".dev" in installed.version or "vcs_info" in direct_url:
            return verify_source_installation(
                "clifft", expected_commit=expected_commit, source_url=source_url
            )
        return {}

    def prepare(
        self,
        *,
        artifact_path: Path,
        workload: dict[str, Any],
        execution: dict[str, Any],
        seed: int = 0,
    ) -> PreparedAdapter:
        reference_convention = str(workload["semantics"]["reference_convention"])
        if reference_convention != "raw-record-parity":
            raise ValueError(
                f"Clifft adapter does not support reference convention {reference_convention!r}"
            )
        import clifft

        scheduler_config = execution.get("clifft_scheduler")
        scheduler = None
        if scheduler_config is not None:
            if not hasattr(clifft, "ActiveWidthSchedulePass"):
                raise ValueError(
                    "this Clifft installation does not support active-width scheduling"
                )
            scheduler = clifft.ActiveWidthSchedulePass(**scheduler_config)

        batch_enabled = bool(execution["batch_enabled"])
        requested_batch_size = execution["batch_size"]
        if type(requested_batch_size) is not int or requested_batch_size < 1:
            raise ValueError("Clifft requires a positive integer batch size")
        if not batch_enabled and requested_batch_size != 1:
            raise ValueError("Clifft requires batch_size=1 when batching is disabled")

        text = artifact_path.read_text()
        parse_started = time.perf_counter()
        circuit = clifft.parse(text)
        parse_seconds = time.perf_counter() - parse_started

        compile_started = time.perf_counter()
        hir = clifft.trace(circuit)
        mask = detector_mask(workload["semantics"], int(hir.num_detectors))
        early_postselection = hasattr(hir, "postselection_mask")
        if early_postselection:
            # New HIR passes specialize on accepted detector outcomes. Set the
            # same raw-parity contract before optimization and again at lowering.
            hir.postselection_mask = mask
        manager = clifft.default_hir_pass_manager()
        if scheduler is not None:
            manager.add(scheduler)
        manager.run(hir)
        program = clifft.lower(hir, postselection_mask=mask)
        compile_seconds = time.perf_counter() - compile_started

        metadata = {
            "name": "clifft",
            "version": str(clifft.version()),
            "threads": 1,
            "precision": "complex-fp64",
            "reference_convention": reference_convention,
            "batch_enabled": batch_enabled,
            "effective_batch_size": requested_batch_size,
            "batch_control": "explicit",
            "postselection_before_optimization": early_postselection,
            "num_qubits": int(program.num_qubits),
            "num_measurements": int(program.num_measurements),
            "num_detectors": int(program.num_detectors),
            "num_observables": int(program.num_observables),
            "peak_active_width": int(program.peak_active_width),
            "parse_seconds": parse_seconds,
            "compile_seconds": compile_seconds,
            "cpu_baseline": str(getattr(clifft, "CPU_BASELINE", "unknown")),
            "sampling_backend": "symbolic-coordinate",
        }
        if hasattr(hir, "num_t_gates"):
            metadata["optimized_t_gates"] = int(hir.num_t_gates)
        if scheduler is not None:
            metadata["scheduler_statistics"] = {
                name: getattr(scheduler, name)
                for name in (
                    "applied",
                    "incumbent_peak",
                    "result_peak",
                    "incumbent_dense_work",
                    "result_dense_work",
                    "swept_ops",
                    "classification_probes",
                )
            }
        return _PreparedClifft(
            clifft,
            program,
            int(workload["semantics"]["observable_index"]),
            metadata,
            requested_batch_size,
        )
