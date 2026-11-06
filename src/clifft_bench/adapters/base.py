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

    def prepare_sampling(self, shots: int, seed: int) -> None:
        """Resolve native tuning once for the public call shape, outside timing."""

    def sampling_metadata(self) -> dict[str, Any]:
        """Return observable native execution choices for a completed sample."""
        return {}

    def begin_sample(self, seed: int) -> None:
        """Initialize a repetition's random stream outside the sampling timer."""

    @abstractmethod
    def sample(self, shots: int, seed: int) -> Counts:
        """Execute declared logical work and return aggregate counts."""


class Adapter(ABC):
    name: str
    adapter_version = ADAPTER_API_VERSION
    native_threads = False

    def thread_budget(self, execution: dict[str, Any]) -> int:
        threads = execution.get("threads", 1)
        if type(threads) is not int or threads < 1:
            raise ValueError("threads must be a positive integer")
        if execution["batch_size"] == "native":
            if not self.native_threads:
                raise ValueError(f"{self.name} has no native threading recipe")
        elif threads != 1:
            raise ValueError("multiple threads require native configuration")
        return threads

    def validate_options(self, options: dict[str, Any]) -> None:
        if options:
            raise ValueError(f"{self.name} does not accept options: {sorted(options)}")

    def validate_native_options(self, options: dict[str, Any]) -> None:
        self.validate_options(options)

    def verify_installation(self, *, expected_commit: str, source_url: str) -> dict[str, Any]:
        """Verify source identity when the adapter requires a source-built package."""
        return {}

    @abstractmethod
    def prepare(
        self,
        *,
        artifact_path: Path,
        workload: dict[str, Any],
        execution: dict[str, Any],
        seed: int = 0,
    ) -> PreparedAdapter:
        """Parse and compile outside timing; seed compilation-only RNG APIs here."""


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
