"""The bounded harness calibration policy used for new measurements."""

from __future__ import annotations

CALIBRATION_POLICY = "bounded-v3"
TUNING_BUDGET_SECONDS = 60.0
BATCH_CALIBRATION_CANDIDATES = (1, 32, 64, 256, 1024, 2048)
STIM_CHUNK_CANDIDATES = (256, 1024, 4096, 16384, 65536)


def calibration_candidates(adapter: str, shots_per_call: int) -> list[int]:
    if adapter == "tsim":
        return sorted({min(n, shots_per_call) for n in (1, 32, 256, 1024, 8192, 65536)})
    if adapter in {"stim", "xtim"}:
        # Chunk sizes are output-buffer capacities, not quantum lanes.
        return sorted({min(n, shots_per_call) for n in STIM_CHUNK_CANDIDATES}
                      | {shots_per_call})
    return [n for n in BATCH_CALIBRATION_CANDIDATES if n <= shots_per_call]
