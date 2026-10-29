"""Raw-zero detector selection, including explicitly selected detector subsets."""

from __future__ import annotations

from typing import Any


def has_postselection(semantics: dict[str, Any]) -> bool:
    return bool(semantics["postselect_all_detectors"]
                or semantics.get("postselection_detector_indices"))


def detector_mask(semantics: dict[str, Any], num_detectors: int) -> list[int]:
    indices = semantics.get("postselection_detector_indices")
    if "postselection_detector_indices" in semantics:
        if semantics["postselect_all_detectors"]:
            raise ValueError("explicit detector selection cannot also select all detectors")
        if (not isinstance(indices, list) or not indices
                or any(type(i) is not int or not 0 <= i < num_detectors for i in indices)
                or indices != sorted(set(indices))):
            raise ValueError(
                "selected detector indices must be nonempty, sorted, unique, and in range"
            )
        selected = set(indices)
        return [int(i in selected) for i in range(num_detectors)]
    return [1] * num_detectors if semantics["postselect_all_detectors"] else []


def require_all_or_none(semantics: dict[str, Any], adapter: str) -> None:
    if "postselection_detector_indices" in semantics:
        raise ValueError(f"{adapter} does not support explicit detector subsets")
