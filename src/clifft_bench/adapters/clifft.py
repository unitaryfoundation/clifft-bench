"""Clifft aggregate-count adapter."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter


class _PreparedClifft(PreparedAdapter):
    def __init__(
        self,
        clifft: Any,
        program: Any,
        observable_index: int,
        runtime_metadata: dict[str, Any],
    ) -> None:
        self._clifft = clifft
        self._program = program
        self._observable_index = observable_index
        self.runtime_metadata = runtime_metadata

    def sample(self, shots: int, seed: int) -> Counts:
        result = self._clifft.sample_survivors(
            self._program,
            shots,
            seed=seed,
            keep_records=False,
        )
        return Counts(
            attempted_shots=int(result.total_shots),
            accepted_shots=int(result.passed_shots),
            discarded_shots=int(result.discards),
            logical_errors=int(result.observable_ones[self._observable_index]),
        )


class ClifftAdapter(Adapter):
    name = "clifft"

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
                f"Clifft adapter does not support reference convention "
                f"{reference_convention!r}"
            )
        if execution["batch_size"] != 1 or execution["batch_enabled"]:
            raise ValueError(
                "Clifft does not expose an internal shot-batch setting; use batch_size=1"
            )

        import clifft

        clifft.set_num_threads(1)
        if int(clifft.get_num_threads()) != 1:
            raise RuntimeError("Clifft did not accept the one-thread setting")

        text = artifact_path.read_text()
        parse_started = time.perf_counter()
        circuit = clifft.parse(text)
        parse_seconds = time.perf_counter() - parse_started

        compile_started = time.perf_counter()
        hir = clifft.trace(circuit)
        clifft.default_hir_pass_manager().run(hir)
        postselect = bool(workload["semantics"]["postselect_all_detectors"])
        mask = [1] * int(hir.num_detectors) if postselect else []
        program = clifft.lower(hir, postselection_mask=mask)
        clifft.default_bytecode_pass_manager().run(program)
        compile_seconds = time.perf_counter() - compile_started

        active_history = list(getattr(program, "active_k_history", []))
        metadata = {
            "name": "clifft",
            "version": str(clifft.version()),
            "threads": int(clifft.get_num_threads()),
            "precision": "complex-fp64",
            "reference_convention": reference_convention,
            "batch_enabled": False,
            "effective_batch_size": 1,
            "num_qubits": int(program.num_qubits),
            "num_measurements": int(program.num_measurements),
            "num_detectors": int(program.num_detectors),
            "num_observables": int(program.num_observables),
            "peak_active_width": max(active_history) if active_history else 0,
            "parse_seconds": parse_seconds,
            "compile_seconds": compile_seconds,
            "cpu_baseline": str(getattr(clifft, "CPU_BASELINE", "unknown")),
            "svm_backend": str(clifft.svm_backend()),
        }
        return _PreparedClifft(
            clifft,
            program,
            int(workload["semantics"]["observable_index"]),
            metadata,
        )
