"""Versioned batch-calibration contracts; omitted policy means the historical grid."""

from __future__ import annotations

from typing import Any

LEGACY_POLICY = "grid-v1"
BOUNDED_POLICY = "bounded-v2"
TUNING_BUDGET_SECONDS = 60.0
NATIVE_TUNING_BUDGET_SECONDS = 0.25
BATCH_CALIBRATION_CANDIDATES = (1, 32, 256, 1024, 2048)
STIM_CHUNK_CANDIDATES = (256, 1024, 4096, 16384, 65536)


def calibration_candidates(
    adapter: str,
    shots_per_call: int,
    policy: str = LEGACY_POLICY,
    native: dict[str, Any] | None = None,
) -> list[int]:
    if policy not in (LEGACY_POLICY, BOUNDED_POLICY):
        raise ValueError(f"unknown tuning policy {policy!r}")
    if adapter == "tsim":
        capacities = (256, 1024, 8192, 65536)
        if policy == BOUNDED_POLICY:
            capacities = (1, 32, *capacities)
        return sorted({min(n, shots_per_call) for n in capacities})
    if adapter in {"stim", "xtim"}:
        # Chunk sizes are output-buffer capacities, not quantum lanes.
        return sorted({min(n, shots_per_call) for n in STIM_CHUNK_CANDIDATES}
                      | {shots_per_call})
    fixed = [n for n in BATCH_CALIBRATION_CANDIDATES if n <= shots_per_call]
    if policy == BOUNDED_POLICY and native and native.get("status") == "success":
        if adapter != "clifft":
            raise ValueError("native tuning proposals require Clifft")
        report = native.get("report", {})
        choices = [report.get("batch_size"), report.get("baseline_batch_size")]
        if any(type(n) is not int or not 1 <= n <= shots_per_call for n in choices):
            raise ValueError("invalid native batch proposal")
        if report.get("shot_workers") != 1 or report.get("intra_shot_workers") != 1:
            raise ValueError("native tuning must retain one worker")
        # Test the native recommendation first; measure duplicate capacities once.
        return list(dict.fromkeys([*choices, *fixed]))
    return fixed
