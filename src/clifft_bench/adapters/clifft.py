"""Clifft aggregate-count adapter."""

from __future__ import annotations

import inspect
import json
import re
import time
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

from clifft_bench.adapters._shared import verify_source_installation
from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter


def _accepts_keyword(function: Any, name: str) -> bool:
    """Inspect Python and nanobind signatures without making a sampling call."""
    try:
        parameters = inspect.signature(function).parameters
        if name in parameters:
            return parameters[name].kind != inspect.Parameter.POSITIONAL_ONLY
        if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            return False
    except (TypeError, ValueError):
        pass
    # nanobind may expose (*args, **kwargs) rather than raising from signature().
    signature = (getattr(function, "__doc__", None) or "").split("\n\n", 1)[0]
    return re.search(rf"\b{re.escape(name)}\s*:", signature) is not None


class _PreparedClifft(PreparedAdapter):
    def __init__(
        self,
        clifft: Any,
        program: Any,
        observable_index: int,
        runtime_metadata: dict[str, Any],
        batch_size: int | None = None,
    ) -> None:
        self._clifft = clifft
        self._program = program
        self._observable_index = observable_index
        self._batch_size = batch_size
        self.runtime_metadata = runtime_metadata

    def sample(self, shots: int, seed: int) -> Counts:
        kwargs = {"seed": seed, "keep_records": False}
        if self._batch_size is not None:
            kwargs["batch_size"] = self._batch_size
        result = self._clifft.sample_survivors(self._program, shots, **kwargs)
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
        postselect = bool(workload["semantics"]["postselect_all_detectors"])
        call_batch_size: int | None = None
        if not batch_enabled and requested_batch_size == 1:
            # Early releases lack this argument. On newer releases omission
            # means auto, which can pack even a supposed scalar calibration trial.
            if _accepts_keyword(getattr(clifft, "sample_survivors", None), "batch_size"):
                call_batch_size = 1
            effective_batch_size = 1
        elif batch_enabled and type(requested_batch_size) is int and requested_batch_size >= 1:
            call_batch_size = requested_batch_size
            effective_batch_size = requested_batch_size
        else:
            raise ValueError(
                "Clifft supports batch_size=1 with batching disabled or "
                "a positive integer batch size with batching enabled"
            )

        # Clifft 0.1-0.7 use module-level thread controls. They were removed
        # in 0.8; later releases default to one sampling thread.
        if hasattr(clifft, "set_num_threads"):
            clifft.set_num_threads(1)
            if int(clifft.get_num_threads()) != 1:
                raise RuntimeError("Clifft did not accept the one-thread setting")

        text = artifact_path.read_text()
        parse_started = time.perf_counter()
        circuit = clifft.parse(text)
        parse_seconds = time.perf_counter() - parse_started

        compile_started = time.perf_counter()
        hir = clifft.trace(circuit)
        mask = [1] * int(hir.num_detectors) if postselect else []
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
        # The bytecode pass API exists through 0.7. Clifft 0.8 replaced that
        # backend with a symbolic program optimized during HIR lowering.
        if hasattr(clifft, "default_bytecode_pass_manager"):
            clifft.default_bytecode_pass_manager().run(program)
        compile_seconds = time.perf_counter() - compile_started

        # The symbolic backend reports the peak directly; older bytecode
        # programs expose the full active-width history instead.
        active_history = list(getattr(program, "active_k_history", []))
        peak_active_width = getattr(program, "peak_active_width", None)
        if peak_active_width is None:
            peak_active_width = max(active_history) if active_history else 0

        def circuit_size(name: str) -> int:
            value = getattr(program, name, None)
            if value is None:
                value = getattr(hir, name)
            return int(value)

        metadata = {
            "name": "clifft",
            "version": str(clifft.version()),
            "threads": int(clifft.get_num_threads()) if hasattr(clifft, "get_num_threads") else 1,
            "precision": "complex-fp64",
            "reference_convention": reference_convention,
            "batch_enabled": batch_enabled,
            "effective_batch_size": effective_batch_size,
            "batch_control": "explicit" if call_batch_size is not None else "legacy-scalar-default",
            "postselection_before_optimization": early_postselection,
            "num_qubits": circuit_size("num_qubits"),
            "num_measurements": circuit_size("num_measurements"),
            "num_detectors": circuit_size("num_detectors"),
            "num_observables": circuit_size("num_observables"),
            "peak_active_width": int(peak_active_width),
            "parse_seconds": parse_seconds,
            "compile_seconds": compile_seconds,
            "cpu_baseline": str(getattr(clifft, "CPU_BASELINE", "unknown")),
            "sampling_backend": str(clifft.svm_backend())
            if hasattr(clifft, "svm_backend")
            else "symbolic-coordinate",
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
            call_batch_size,
        )
