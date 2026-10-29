"""A native-only regression: unseeded calls must advance the random stream."""

import numpy as np
import xtim

CIRCUIT = """H 0
X_ERROR(1) 1
T 0
CX 1 0
T_DAG 0
CX 1 0
M 1
DETECTOR rec[-1]
H 0
M 0
OBSERVABLE_INCLUDE(0) rec[-1]
"""

# The certain X fault makes D=1 and O a fair coin. Without noise both are 0,
# so the compiler certifies both channels as deterministic, not gauge/refused.
for chunk in (1, 32, 4096):
    sampler = xtim.compile_twirl_sampler(CIRCUIT, disk_cache=False, selfcheck=0)
    blocks = []
    for offset in range(0, 4096, chunk):
        dets, obs = sampler.sample(chunk, seed=74 if offset == 0 else None)
        assert np.all(dets[:, 0] & 1)
        blocks.append(obs[:, 0] & 1)
    bits = np.concatenate(blocks)
    ones = int(bits.sum())
    print(f"chunk={chunk}: {ones}/4096 observable ones", flush=True)
    # A 6-sigma marginal check catches chunk=1's all-zero result on the old pin.
    assert abs(ones - 2048) <= 6 * np.sqrt(1024), (chunk, ones)
    if len(blocks) > 1:
        assert any(not np.array_equal(blocks[0], b) for b in blocks[1:])
