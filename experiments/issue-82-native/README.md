# Issue #82: native API isolation

Investigated 2026-10-29 on macOS arm64, Python 3.12.13. These are small
correctness probes, not throughput results. No release pins, adapters, workload
eligibility, or published measurements were changed.

## Findings

| Finding | Attribution | Resolution indicated by these probes |
| --- | --- | --- |
| SymFT discards detector-fired shots with `postselect_detectors=False` | Our adapter misinterprets the upstream counts API. The observed behavior is documented. | Use an API that retains all observable outcomes for unselected workloads with detectors, or reject that pairing explicitly. |
| xtim 3.1.4 counts depend incorrectly on public chunk size | Upstream continuation-stream bug at the benchmark's pinned commit. | xtim 3.2.2 passes the reproducer and distribution checks; upgrading also requires migrating our adapter to its changed API. |

Both reported failures occur without clifft-bench imports. All **36** pinned
native-versus-adapter aggregate comparisons match exactly. The reducers do not
introduce either discrepancy.

### SymFT: documented count semantics, not a demonstrated simulator defect

Tested `haoliri0/SOFT@c89b98514a919240b8afa53a271e08d926d3c987`, package 0.1.1.
The upstream [counts documentation](https://github.com/haoliri0/SOFT/blob/c89b98514a919240b8afa53a271e08d926d3c987/python/README.md#logical-error-statistics)
defines `accepted` as shots with no detector firing and counts observable ones
only among those shots. Its [postselection option](https://github.com/haoliri0/SOFT/blob/c89b98514a919240b8afa53a271e08d926d3c987/python/README.md#detector-postselection)
controls early termination, without changing the statistics' meaning.

This deterministic native-only example returns zero accepted shots and eight
discards for either value of `postselect_detectors`, in scalar and batch modes:

```python
import symft

c = symft.Circuit("X 0\nM 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n")
s = c.compile_counts_sampler(batch=False, postselect_detectors=False, threads=1)
print(s.sample(shots=8, stream_id=74))
```

The scalar [count accumulator](https://github.com/haoliri0/SOFT/blob/c89b98514a919240b8afa53a271e08d926d3c987/cpp/src/sampler/prepared_sampler.cpp#L147)
and the batch accumulator both enforce these semantics. The flag is passed
correctly by our adapter; its meaning is different from our
`postselect_all_detectors=False` contract. Relabelling `accepted` as `shots`
would not repair the missing observable ones from discarded shots.

SymFT's raw measurement API passes all four exact joint-probability checks for
three seeds in both scalar and batch-32 modes. For seed 74, the scalar raw joint
counts are `[122549, 4149, 2189, 2185]`. The counts sampler uses a different seed
interface; its two flag settings both return 126691 accepted, 4381 discarded,
and 4243 accepted observable ones. Raw and aggregate APIs need not return
identical samples, and this experiment does not assume that they do.

### xtim: a per-call shot index repeats the coin stream

Tested the pinned `ikim-quantum/xtim@29454071fb20fbcffd258559ad0644676e44a8f9`,
package 3.1.4. Its [API contract](https://github.com/ikim-quantum/xtim/blob/29454071fb20fbcffd258559ad0644676e44a8f9/docs/twirl_record_sampler.md)
says that `sample(..., seed=None)` continues the stream. The implementation
initializes [`long done = 0` inside each run](https://github.com/ikim-quantum/xtim/blob/29454071fb20fbcffd258559ad0644676e44a8f9/cpp/src/twirl_sampler.cpp#L2317)
and [seeds the shot RNG from `(seed_, done)`](https://github.com/ikim-quantum/xtim/blob/29454071fb20fbcffd258559ad0644676e44a8f9/cpp/src/twirl_sampler.cpp#L2374).
Noise events advance, but these per-shot coin draws repeat at the same positions
in each public call. Consequently, the bias can point either up or down with
different seeds; detector-flipped rows are not simply dropped or always zeroed.

The independent four-amplitude oracle for [circuit.stim](circuit.stim) gives
joint probabilities in `(D,O)` order `00,01,10,11`:

```text
[841/900, 29/900, 1/60, 1/60]
P(O=1) = 11/225
```

At 131072 shots, approximately 6408 observable ones are expected. Seed 74
reproduces the original issue's counts exactly:

| Public chunk size | Pinned xtim 3.1.4 observable ones | xtim 3.2.2 observable ones |
| ---: | ---: | ---: |
| 1 | 4352 | 6398 |
| 32 | 6916 | 6398 |
| 2048 | 6391 | 6398 |
| 131072 | 6294 | 6398 |

Passing at a larger chunk does not establish independent sampling in the old
version. [xtim_continuation.py](xtim_continuation.py) makes the failure clearer:
replace depolarization with `X_ERROR(1) 1`. Every shot then has `D=1`, while
`O` is a fair coin. With seed 74 and 4096 total shots, the old version produces
zero ones using one-shot calls, repeats the same 32-bit block using chunk 32,
and gives 2022 ones in one bulk call. The minimal regression fails on the old
pin. Turning on `selfcheck=2000` does not catch or repair this continuation bug.

The native exact measurement sampler passes the original joint-law check for
all three seeds. The failure is isolated to the old twirl continuation path,
not the aggregate reducer. This small circuit's selected statistics pass; this
does not establish immunity for other selected circuits using the same RNG.

### Current upstream release fixes the continuation failure

Also built `ikim-quantum/xtim@edf0dd0cff804ac0228d517889e49b0c286cb19a`, package
3.2.2, the upstream default-branch head inspected on 2026-10-29. Its
[cumulative shot counter](https://github.com/ikim-quantum/xtim/blob/edf0dd0cff804ac0228d517889e49b0c286cb19a/cpp/src/twirl_sampler_impl.inc.hpp#L550)
explicitly continues across unseeded calls and resets on an explicit reseed.

All **33 native distribution checks** pass: three seeds, four chunks on the
original circuit, three chunks with selfcheck both off/on on the forced-fault
circuit, and three exact-engine controls. The original circuit gives identical
joint counts across all four chunks for each seed. The forced-fault regression
also passes and no longer repeats its first chunk; seed 74 gives 2005 ones at
each chunk size.

This is not a drop-in pin change: 3.2.2 removes the
`skip_refused_observables` compile keyword and replaces `channel_report()` with
`record_report()` and a different schema. Our current adapter fails on the
removed keyword. The 3.2.2 evidence therefore tests its native APIs only.
Migrate and revalidate the adapter and eligible workloads before admitting
comparisons; the broader release's changed semantics were not audited here.

## Reproduce

From the repository root, install the existing pinned source environments:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  -r environments/symft-0.1.1-c89b985.txt \
  -r environments/xtim-3.1.4-2945407.txt
.venv/bin/python experiments/issue-82-native/repro.py \
  --output /tmp/issue82-native.json
```

For the optional adapter comparisons, install the harness and add
`--with-adapters`:

```sh
uv pip install --python .venv/bin/python -e .
.venv/bin/python experiments/issue-82-native/repro.py --with-adapters \
  --output /tmp/issue82-with-adapters.json
```

Check the current upstream fix in a separate environment:

```sh
uv venv --python 3.12 .campaign-envs/issue82-xtim-current
uv pip install --python .campaign-envs/issue82-xtim-current/bin/python \
  -r experiments/issue-82-native/requirements-xtim-3.2.2.txt
.campaign-envs/issue82-xtim-current/bin/python \
  experiments/issue-82-native/xtim_continuation.py
.campaign-envs/issue82-xtim-current/bin/python \
  experiments/issue-82-native/repro.py --tool xtim \
  --output /tmp/issue82-xtim-3.2.2.json
```

The matrix script reports failures as data and exits normally after a completed
diagnostic; it asserts native/adapter equality when comparisons are enabled.
The minimal continuation script is a regression gate and raises on failure.
All tests use only two qubits, one CPU thread, disk caching disabled for the
twirl sampler, and a temporary private cache for xtim's exact sampler. The
original tests use 131072 shots for each of seeds 1, 42, 74; the forced-fault
test uses 4096 shots. Joint marginals use the issue's six-sigma-plus-one-count
tolerance. This tolerance alone cannot establish independence; the repeated
block regression detects the underlying correlation directly.

## Evidence

- [pinned-results.json](pinned-results.json): 55 observations, including 36
  exact native/adapter aggregate comparisons and the deterministic SymFT control.
- [xtim-3.2.2-results.json](xtim-3.2.2-results.json): 33 native observations.

Both files record installation source commits, environment, source hashes,
seeds, joint counts, sampler reports, and a completion marker. The pinned file
also hashes the unchanged adapter sources. The original oracle/evidence were
recovered from local branch `codex/pr83-original-evidence` at
`0586aaf1f4b454064aec3bae093e065d4a0cfe9c`; this smaller probe reruns the relevant
case and does not restore the superseded code-switching experiment bundle.
