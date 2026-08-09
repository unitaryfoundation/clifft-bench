"""Common adapter contracts and sampling invariants."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ADAPTER_API_VERSION = "1"


@dataclass(frozen=True)
class Counts:
    attempted_shots: int
    accepted_shots: int
    discarded_shots: int
    logical_errors: int
    adapter_timing: dict[str, float] | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "attempted_shots": self.attempted_shots,
            "accepted_shots": self.accepted_shots,
            "discarded_shots": self.discarded_shots,
            "logical_errors": self.logical_errors,
        }
        if self.adapter_timing is not None:
            value["adapter_timing"] = self.adapter_timing
        return value


class PreparedAdapter(ABC):
    runtime_metadata: dict[str, Any]

    @abstractmethod
    def sample(self, shots: int, seed: int) -> Counts:
        """Execute declared logical work and return aggregate counts."""


class Adapter(ABC):
    name: str
    adapter_version = ADAPTER_API_VERSION

    @abstractmethod
    def prepare(
        self,
        *,
        artifact_path: Path,
        workload: dict[str, Any],
        execution: dict[str, Any],
    ) -> PreparedAdapter:
        """Parse and compile the workload outside every timed sample."""


def validate_counts(counts: Counts, *, postselect: bool) -> list[str]:
    errors = []
    if counts.attempted_shots < 0:
        errors.append("attempted_shots is negative")
    if counts.accepted_shots < 0 or counts.discarded_shots < 0:
        errors.append("accepted_shots or discarded_shots is negative")
    if counts.accepted_shots + counts.discarded_shots != counts.attempted_shots:
        errors.append("accepted_shots + discarded_shots != attempted_shots")
    if counts.logical_errors < 0 or counts.logical_errors > counts.accepted_shots:
        errors.append("logical_errors is outside [0, accepted_shots]")
    if not postselect and counts.discarded_shots != 0:
        errors.append("non-postselected workload discarded shots")
    return errors
