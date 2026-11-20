"""Common adapter contracts and sampling invariants."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    import numpy as np

Sample: TypeAlias = "Counts | tuple[np.ndarray, np.ndarray]"

ADAPTER_API_VERSION = "2"


class UnsupportedConfiguration(ValueError):
    """The adapter has no native implementation of the requested execution."""


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

    def first_call_metadata(self) -> dict:
        """Native execution settings resolved by the first full sampling call."""
        return {}

    @abstractmethod
    def sample(self, shots: int, seed: int) -> Sample:
        """Return survivor counts (on) or native packed detector/observable arrays (off)."""


class Adapter(ABC):
    name: str
    adapter_version = ADAPTER_API_VERSION
    supported_threads = (1,)
    supported_postselect = ("on",)

    def validate_execution(self, execution: dict[str, Any], workload: dict[str, Any]) -> int:
        self.validate_options(execution.get("options", {}))
        mode = execution["postselect"]
        if mode not in ("on", "off"):
            raise ValueError("postselect must be on or off")
        if mode not in self.supported_postselect:
            raise UnsupportedConfiguration(f"{self.name} does not support postselect={mode}")
        batch_size = execution["batch_size"]
        if batch_size != "auto" and (type(batch_size) is not int or batch_size < 1):
            raise ValueError('batch_size must be "auto" or a positive integer')
        threads = execution.get("threads", 1)
        if type(threads) is not int or threads not in (1, 8):
            raise ValueError("threads must be 1 or 8")
        if threads not in self.supported_threads:
            raise UnsupportedConfiguration(f"{self.name} does not support threads={threads}")
        return threads

    def validate_options(self, options: dict[str, Any]) -> None:
        if options:
            raise ValueError(f"{self.name} does not accept options: {sorted(options)}")

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


def validate_counts(counts: Counts) -> list[str]:
    errors = []
    if counts.attempted_shots < 0:
        errors.append("attempted_shots is negative")
    if counts.accepted_shots < 0 or counts.discarded_shots < 0:
        errors.append("accepted_shots or discarded_shots is negative")
    if counts.accepted_shots + counts.discarded_shots != counts.attempted_shots:
        errors.append("accepted_shots + discarded_shots != attempted_shots")
    if counts.logical_errors < 0 or counts.logical_errors > counts.accepted_shots:
        errors.append("logical_errors is outside [0, accepted_shots]")
    return errors


def sample_evidence(sample: Sample, *, shots: int, postselect: bool, metadata: dict) -> dict:
    """Validate an endpoint outside timing and retain its serializable evidence."""
    if postselect:
        if not isinstance(sample, Counts):
            raise ValueError("postselect=on requires survivor counts")
        errors = validate_counts(sample)
        if sample.attempted_shots != shots:
            errors.append("attempted shots differ from requested shots")
        if errors:
            raise ValueError("; ".join(errors))
        return sample.as_dict()
    import numpy as np

    if not isinstance(sample, tuple) or len(sample) != 2:
        raise ValueError("postselect=off requires packed detector/observable arrays")
    shapes = {}
    for name, array in zip(("detectors", "observables"), sample, strict=True):
        expected = (shots, (metadata[f"num_{name}"] + 7) // 8)
        if not isinstance(array, np.ndarray) or array.dtype != np.uint8 or array.shape != expected:
            raise ValueError(f"{name} must be uint8 with shape {expected}")
        shapes[name] = list(array.shape)
    return {"attempted_shots": shots, "packed_output": shapes}
