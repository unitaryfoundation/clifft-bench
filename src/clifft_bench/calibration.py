"""Shared batch-calibration contract."""

BATCH_CALIBRATION_CANDIDATES = (1, 32, 256, 1024, 2048)
STIM_CHUNK_CANDIDATES = (256, 1024, 4096, 16384, 65536)


def calibration_candidates(adapter: str, shots_per_call: int) -> list[int]:
    if adapter == "stim":
        # Also test an unchunked call; cache effects depend on the reference host.
        return sorted({min(n, shots_per_call) for n in STIM_CHUNK_CANDIDATES}
                      | {shots_per_call})
    return [n for n in BATCH_CALIBRATION_CANDIDATES if n <= shots_per_call]
