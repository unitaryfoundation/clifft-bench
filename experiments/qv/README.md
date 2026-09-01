# Quantum Volume paper refresh

This is a self-contained, one-off refresh of the
[`clifft-paper/qv_bench`](https://github.com/unitaryfoundation/clifft-paper/tree/db7dc9f13a2c2854690e92390c779048a1ac1400/qv_bench)
experiment. It compares the current pinned Clifft release with current pinned
Qiskit Aer, Qulacs, qsim, and Qrack releases on dense random Quantum Volume
circuits. It does not extend the recurring QEC campaign, its schemas, or the
`clifft-bench` CLI.

The adapted paper code remains under this repository's Apache-2.0 license; its
source repository, path, and exact commit are retained in code and run metadata.

The implementation preserves the paper question and timed regions:

- widths 6 through 28 in steps of two, with depth equal to width;
- deterministic seeds 42, 43, and 44;
- Qiskit generation transpiled to the `cx`/`u3` basis with optimization
  disabled;
- one generated QASM artifact reused byte-for-byte by every simulator;
- one fresh subprocess per simulator, width, and seed;
- Clifft compilation plus one sample timed; backend execution timed for the
  other simulators; and
- median single-shot execution time plotted by width.

The default matrix has 180 serial cases. Qrack runs CPU-only with OpenCL
disabled. This is simulator runtime scaling on QV circuits, not a measurement
of a quantum device's Quantum Volume score.

## Provenance and outputs

The paper source commit, exact dependency versions, Clifft build settings,
source commit, system identity, EC2 identity, CPU set, boot ID, circuit digests,
memory ceiling, worker metadata, and timing boundary are stored with every
execution.

Each run creates a new, non-overwriting directory:

```text
results/EXECUTION_ID/
  metadata.json
  cases.csv
  circuits/*.qasm
  raw/*.json
  qv-scaling.png
```

A failed or timed-out case is retained in `cases.csv` and its raw JSON. A
future Clifft refresh should update the pinned Clifft source and other current
tool pins as appropriate, then collect a new execution directory. Existing
results remain immutable; this experiment does not need to carry old Clifft
versions in the same run.

## Local validation

Python 3.12 or 3.13 is required. From this directory:

```bash
uv sync --locked --extra test --extra plot
uv run python -m qv_experiment.validate
uv run pytest
```

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

The expected Clifft values are version `0.9.0` and a native CPU baseline. The
locked project builds the pinned source commit with a 64-qubit limit and
OpenMP enabled; those settings are also recorded in every execution's metadata.

### 3. Collect the experiment

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

### 4. Plot, review, and publish

```bash
uv run python -m qv_experiment.plot "results/$CLIFFT_QV_EXECUTION"

git status --short
git add "results/$CLIFFT_QV_EXECUTION"
git commit --no-gpg-sign -m "data: add QV execution $CLIFFT_QV_EXECUTION"
git push -u origin HEAD
```

Review `metadata.json`, every non-successful row in `cases.csv`, the raw
worker records, circuit digests, and the plot before opening the data PR. Stop
the instance after the branch is visible on GitHub.
