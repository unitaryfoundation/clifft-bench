# Local BT27/BT81 observable-0 validation

The exact full-circuit reference and Clifft 0.13.0rc2 agree for the raw,
unselected observable-0 marginal of both frozen workloads. All individual
samples and pooled samples pass the declared statistical check.

This is local correctness evidence for issue
[#69](https://github.com/unitaryfoundation/clifft-bench/issues/69).
It makes no throughput or full joint-distribution claim. Keep this evidence
branch separate from mainline.

## Source and input identities

- Maintained reference and regression tests: commit
  `15e8bfdcbd8ee9b46419f0de1dcdd9502fbf6b27`,
  [tests/test_code_switching.py](../../tests/test_code_switching.py).
- Committed evidence driver and entropy-generated seeds:
  `565798b1475f386b81af8eb016e121f555705def`.
- [Raw results](raw.json) retain the clean source commit, reference-file digest,
  full workload definitions and provenance, circuit SHA-256 values,
  implementation identity, scheduler settings, worker metadata, seeds and every
  sample's attempted/accepted/discarded counts and raw observable-0 ones.

| Input | SHA-256 |
|---|---|
| BT27 | `28339beeffe1b18f81b3be07c24210a77ba7f1edb2292ccd0afc47e8cb745a64` |
| BT81 | `12a3a86a310ff52dbcbc2525554fd3f1dd87b2da8a0c5a27ee84f9c5271b5abe` |

## Independent reference

The reference reads the complete checked-in instruction stream and imports no
Clifft implementation. It uses standard-library integer arithmetic and
`fractions.Fraction`.

Each initial RX introduces an independent binary variable. Each CNOT propagates
linear forms over GF(2); each T or T_DAG adds a signed linear parity to the phase
polynomial P modulo eight. The state has amplitudes
`2^(-m/2) exp(i*pi*P(x)/4)` on computational basis states `L*x`.

All intermediate measurements in these inputs are Z measurements before the
phase gates. The calculation defers them, retaining the measured qubits as
branch records and replacing record-controlled X gates with coherent CNOTs.
The reference explicitly rejects subsequent quantum reuse of those record
qubits. Keeping and tracing those records gives the same unconditional reduced
state as measurement and classical feedback; no branch is fixed or selected.

The observable declaration is resolved using its actual measurement-record
lookback. Observable 0 is a terminal X measurement. All other terminal
measurements are on distinct qubits and have no subsequent gates or
postselection, so summing over their outcomes leaves this marginal unchanged.

To calculate the X expectation, solve `L*d = e_q`. An inconsistent system gives
zero expectation. Otherwise average
`exp(i*pi*(P(x+d)-P(x))/4)` over the initial variables. For a T term with
linear form l that flips under d, its phase difference is
`sign*(1-2*l(x))`. Expanding the parity modulo eight leaves a quadratic
polynomial. The reference checks that its nonzero coefficients are four,
allowing exact enumeration of signs, with a hard bound of 16 active variables.

Both circuits yield **X expectation 1/4** and therefore
**P(observable 0 = 1) = (1 - 1/4)/2 = 3/8**. This value is derived from the full
physical instruction stream, including feedback and decoding.

Small explicit complex statevectors independently cross-check the arithmetic.
Those tests enumerate every measurement branch with its unnormalized amplitude,
cover correlated and independent early records, feedback lookbacks, terminal
record selection, decoding CNOTs, phase sign changes, and support that is not
preserved by the queried X operator. Separate tests reject unsupported gates,
measurement placement, record references, and reuse.

## Local sampling

Collected on 2026-11-04 on an Apple M4 Pro, macOS arm64, Python 3.14.5,
Clifft 0.13.0rc2 and NumPy 2.5.4.

[validate.py](validate.py) reuses the maintained `WorkerClient` and adapter.
Each circuit/mode gets a fresh worker, with the harness thread caps set before
simulator import and the release scheduler options unchanged. Each worker
receives four distinct seeds from [seeds.json](seeds.json), originally drawn
from operating-system entropy, and samples 65,537 shots per seed. The odd call
size exercises a final partial packed batch.

| Circuit | Batch | Attempted shots | Raw observable-0 ones | Fraction |
|---|---:|---:|---:|---:|
| BT27 | 1 | 262,148 | 98,100 | 0.37421609 |
| BT27 | 32 | 262,148 | 97,975 | 0.37373926 |
| BT81 | 1 | 262,148 | 98,194 | 0.37457467 |
| BT81 | 32 | 262,148 | 97,928 | 0.37355997 |

All 1,048,592 attempts were accepted, with zero discards. Each individual
sample and each pooled row satisfies
`abs(ones - N*p) <= 6*sqrt(N*p*(1-p)) + 1`, with exact reference `p = 3/8`.
The largest pooled deviation is about 1.53 binomial standard deviations.
These statistical checks support the scalar and packed marginals; they do not
prove independence of generated shots or the joint distribution of the nine
observables, detector parities, or all measurement records.

The driver sets a 900-second overall deadline, 300-second setup deadlines and
120-second request deadlines. A 12 GiB address-space limit is requested through
the existing worker; its Linux enforcement and CPU affinity are unavailable on
macOS and are recorded as unapplied. Thread caps are applied. The cumulative
child peak RSS was 57,114,624 bytes (macOS units); this is an observed peak, not
an enforced memory ceiling. No local timings are used as benchmark results.

## Reproduction

From a clean checkout of this evidence branch, use a new output path:

```sh
uv sync --extra test
uv pip install -r environments/clifft-0.13.0rc2.txt
.venv/bin/python evidence/bt69/validate.py /tmp/bt69-repeat.json
```

The committed seeds replay the same sampling streams. The driver refuses to
overwrite results or run from an uncommitted checkout.

The feature commit passed the required repository checks:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 BLIS_NUM_THREADS=1 NUMBA_NUM_THREADS=1 uv run --extra test pytest
uv run --extra test ruff check .
git diff --check
```

Result: **311 passed, 141 skipped**. Skips were optional peer-simulator,
Numba and plotting dependencies. Both full-circuit reference tests and all
four BT Clifft sampling tests ran and passed. Lint and whitespace checks passed.
The test environment used the project lock's NumPy 2.5.2; the larger evidence
run restored the exact Clifft environment pin, NumPy 2.5.4.
