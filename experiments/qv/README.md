# Quantum Volume paper refresh

This is a self-contained, one-off refresh of the
[`clifft-paper/qv_bench`](https://github.com/unitaryfoundation/clifft-paper/tree/db7dc9f13a2c2854690e92390c779048a1ac1400/qv_bench)
experiment. It compares the pinned Clifft `v0.10.0rc1` release candidate with
current pinned Qiskit Aer, Qulacs, and qsim releases on dense random Quantum
Volume circuits. Reader-facing output identifies the candidate with its target
release, Clifft 0.10.0. The experiment does not extend the recurring QEC
campaign, its schemas, or the `clifft-bench` CLI.

The adapted paper code remains under this repository's Apache-2.0 license; its
source repository, path, and exact commit are retained in code and run metadata.

The implementation preserves the paper question and timed regions:

- widths 6 through 28 in steps of two, with depth equal to width;
- deterministic seeds 42, 43, and 44;
- Qiskit generation transpiled to the `cx`/`u3` basis with optimization
  disabled;
- one generated QASM input reused byte-for-byte by every simulator;
- one fresh subprocess per simulator, width, and seed;
- Clifft compilation plus one sample timed; backend execution timed for the
  other simulators; and
- median single-shot execution time plotted by width.

These are intentionally the paper's asymmetric timing boundaries: Clifft is
charged for compilation, while Qiskit transpilation and Qulacs/qsim circuit
preparation occur before their timers. The plot is not an equal end-to-end
timing comparison.

The default matrix has 144 serial cases. This is simulator runtime scaling on
QV circuits, not a measurement of a quantum device's Quantum Volume score.

## Provenance and outputs

The paper source commit, target Clifft release, exact measured Clifft artifact,
source commit, requested build settings, observed runtime version and CPU
baseline, dependency versions, system identity, EC2 identity, CPU set, boot ID,
circuit digests, address-space ceiling, worker metadata, and timing boundary
are stored with every execution.

For release-candidate measurements, raw metadata retains the candidate version
and commit while plots use the target release version. Present the data as a
final release only when its tag points to the measured candidate commit;
otherwise rerun or label the result as release-candidate evidence.

Each run creates a new, non-overwriting directory:

```text
results/EXECUTION_ID/
  metadata.json
  cases.csv
  raw/*.json
  qv-scaling.png
```

Generated QASM is passed directly to each worker and is not retained. Width,
depth, seed, source commit, and locked environment describe how to regenerate
the input; its SHA-256 digest records the exact bytes shared by every simulator
during collection. Floating-point text may differ in insignificant final digits
when regenerated on a different platform.

A failed or timed-out case is retained in `cases.csv` and its raw JSON. If the
controller is interrupted, `metadata.json` remains at `status: "running"` and
the directory is incomplete evidence. A future Clifft refresh should update
the pinned Clifft source and other current tool pins as appropriate, then
collect a new execution directory. Existing results remain immutable; this
experiment does not need to carry old Clifft versions in the same run.

## Local validation

Python 3.12 or 3.13 is required. From this directory:

```bash
uv sync --locked --extra test --extra plot
uv run python -m qv_experiment.validate
uv run pytest
```

The validator compares the Clifft, Qulacs, and qsim statevectors with Qiskit's
reference statevector before any performance collection.

A small local smoke run can use fewer widths, seeds, tools, and threads:

```bash
uv run python -m qv_experiment \
  --execution-id local-smoke \
  --qubits 4,6 \
  --seeds 42 \
  --simulators clifft,qiskit \
  --threads 1 \
  --memory-limit-gib 4 \
  --timeout-seconds 120
```

Local output is diagnostic only. Choose a new execution ID before rerunning;
the runner deliberately refuses to overwrite evidence.

## Run on EC2

Use a dedicated stopped instance so this occasional experiment does not alter
the QEC reference host:

- Canonical Ubuntu Server 24.04 LTS, 64-bit x86;
- `c8i.8xlarge`, On-Demand, shared tenancy;
- one fixed region and availability zone;
- 30 GiB `gp3` root volume at default IOPS and throughput;
- IMDSv2 required and instance-initiated shutdown behavior set to **Stop**;
- no IAM role, extra data volume, Elastic IP, or persistent public IPv4; and
- SSH limited to the operator's current IP.

The run uses one logical CPU from each of the instance's 16 physical cores and
a 10 GiB address-space limit per worker. Keep this instance stopped when it is
not collecting data.

### 1. Clone the exact source and create a data branch

```bash
git clone https://github.com/unitaryfoundation/clifft-bench.git
cd clifft-bench
git switch -c data/qv-current-$(date -u +%Y%m%d)
cd experiments/qv
```

Do not pull or edit tracked files after starting collection.

### 2. Install the locked experiment

```bash
sudo apt-get update
sudo apt-get install --yes build-essential curl git python3.12-dev python3.12-venv
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"

uv sync --locked --python 3.12 --extra plot
```

Confirm the source build and correctness before the long run:

```bash
uv run python -c \
  'import clifft; print(clifft.version(), clifft.CPU_BASELINE)'
uv run python -m qv_experiment.validate
```

The expected Clifft values are artifact version `0.10.0rc1` and a native CPU
baseline. Official collection checks both values before creating its output
directory. The locked project builds tag `v0.10.0rc1` at commit
`4440ecb71ab9b4922ba2f543e392fbab640cd248` and requests a 64-qubit limit and
OpenMP; those requested settings and the observable runtime identity are
recorded separately. Plots label this candidate as Clifft 0.10.0.

### 3. Pilot the largest case

Before the official matrix, run one QV28 seed across all tools with the same
16 cores and 10 GiB virtual-address-space ceiling:

```bash
uv run python -m qv_experiment \
  --execution-id qv28-pilot \
  --output-root /tmp/clifft-qv-pilot \
  --qubits 28 \
  --seeds 42 \
  --require-ec2 \
  --require-clean \
  --threads 16 \
  --memory-limit-gib 10 \
  --timeout-seconds 600
```

Inspect every raw result and its `peak_rss_bytes`. If a tool fails because of
virtual address-space reservations despite adequate resident-memory headroom,
stop and revise the declared ceiling before collecting official evidence.

### 4. Collect the experiment

Run inside `tmux` so a dropped SSH connection does not stop collection:

```bash
export CLIFFT_QV_EXECUTION=qv-current-$(date -u +%Y%m%d)
uv run python -m qv_experiment \
  --execution-id "$CLIFFT_QV_EXECUTION" \
  --require-ec2 \
  --require-clean \
  --threads 16 \
  --memory-limit-gib 10 \
  --timeout-seconds 600
```

The run is serial and writes each raw case immediately. It returns nonzero
after completing the matrix if any simulator failed or timed out; inspect the
stored evidence instead of deleting it.

### 5. Plot, review, and publish

```bash
uv run python -m qv_experiment.plot "results/$CLIFFT_QV_EXECUTION"

git status --short
git add "results/$CLIFFT_QV_EXECUTION"
git commit --no-gpg-sign -m "data: add QV execution $CLIFFT_QV_EXECUTION"
git push -u origin HEAD
```

The downstream documentation-sized light/dark pair is selected and regenerated
through [`../../reporting/README.md`](../../reporting/README.md); do not copy QV
rows into a separate reporting data file.

Require `metadata.json` to say `complete` or `complete-with-failures`, never
`running`. Review every non-successful row in `cases.csv`, worker exit codes and
stderr tails, raw worker records, circuit digests, peak RSS values, and the plot
before opening the data PR. Stop the instance after the branch is visible on
GitHub.
