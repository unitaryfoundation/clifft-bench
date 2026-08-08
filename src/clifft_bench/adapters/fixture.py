"""Small deterministic adapter used only by harness tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clifft_bench.adapters.base import Adapter, Counts, PreparedAdapter


class _PreparedFixture(PreparedAdapter):
    def __init__(self, postselect: bool, runtime_metadata: dict[str, Any]) -> None:
        self._postselect = postselect
        self.runtime_metadata = runtime_metadata

    def sample(self, shots: int, seed: int) -> Counts:
        discarded = shots // 10 if self._postselect else 0
        accepted = shots - discarded
        errors = min(accepted, (shots + seed % 3) // 20)
        return Counts(shots, accepted, discarded, errors)


class FixtureAdapter(Adapter):
    name = "fixture"

    def prepare(
        self,
        *,
        artifact_path: Path,
        workload: dict[str, Any],
        execution: dict[str, Any],
    ) -> PreparedAdapter:
        metadata = {
            "name": "fixture",
            "version": "1.0.0",
            "threads": 1,
            "precision": "integer",
            "batch_enabled": bool(execution["batch_enabled"]),
            "effective_batch_size": int(execution["batch_size"]),
            **workload["expected_metadata"],
        }
        return _PreparedFixture(bool(workload["semantics"]["postselect_all_detectors"]), metadata)
