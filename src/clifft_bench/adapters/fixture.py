"""Small deterministic adapter used only by harness tests."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter, Sample
from clifft_bench.selection import require_all_or_none


class _PreparedFixture(PreparedAdapter):
    def __init__(
        self,
        postselect: bool,
        runtime_metadata: dict[str, Any],
        parameters: dict[str, Any],
    ) -> None:
        self._postselect = postselect
        self._parameters = parameters
        self.runtime_metadata = runtime_metadata

    def sample(self, shots: int, seed: int) -> Sample:
        if self._parameters.get("write_native_stdout"):
            os.write(1, b"fixture native sample output\n")
        if seed == self._parameters.get("sleep_on_seed"):
            time.sleep(float(self._parameters.get("sleep_seconds", 0)))
        if not self._postselect:
            import numpy as np

            return tuple(np.zeros((shots, (self.runtime_metadata[f"num_{name}"] + 7) // 8),
                                  dtype=np.uint8) for name in ("detectors", "observables"))
        discarded = shots // 10 if self._postselect else 0
        accepted = shots - discarded
        errors = min(accepted, (shots + seed % 3) // 20)
        return Counts(shots, accepted, discarded, errors)


class FixtureAdapter(Adapter):
    name = "fixture"
    supported_threads = (1, 8)
    supported_postselect = ("on", "off")

    def prepare(
        self,
        *,
        artifact_path: Path,
        workload: dict[str, Any],
        execution: dict[str, Any],
        seed: int = 0,
    ) -> PreparedAdapter:
        require_all_or_none(workload["semantics"], self.name)
        threads = self.validate_execution(execution, workload)
        parameters = dict(workload["parameters"])
        if parameters.get("write_native_stdout"):
            os.write(1, b"fixture native prepare output\n")
        batch = execution["batch_size"]
        metadata = {
            "name": "fixture",
            "version": "1.0.0",
            "threads": threads,
            "precision": "integer",
            "batch_enabled": None if batch == "auto" else batch > 1,
            "effective_batch_size": None if batch == "auto" else batch,
            "compile_seconds": 0.0,
            **workload["expected_metadata"],
        }
        return _PreparedFixture(
            execution["postselect"] == "on",
            metadata,
            parameters,
        )
