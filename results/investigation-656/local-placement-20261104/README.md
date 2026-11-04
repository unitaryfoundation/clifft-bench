# Local d5 placement and kernel evidence

Evidence for [Clifft #656](https://github.com/unitaryfoundation/clifft/issues/656). This is an evidence branch; do not merge these one-off artifacts into mainline. The production change is isolated on Clifft's `codex/hoist-high-pivot-metadata` branch, commit `7e1099bfe4024c1bfb11b3dc3afabfeece5f60f8`, parent `7e83ec2823bf99c54aa1b90c54a092641c10c4b0`.

Start with [findings.md](findings.md). It contains the protocol, results, exceptions, validation and follow-up decision. [pair-summary.json](pair-summary.json), [history-summary.json](history-summary.json) and [kernel-summary.json](kernel-summary.json) expose the numerical summaries without extracting archives.

## What is retained

- `clifft-656-placement-evidence.tar.gz`: earlier whole-allocation investigation, preserved unchanged. SHA-256 `19845d0c26904c52757f1b29aad546d75450f45611ac5bff4191ef8cd27cc37b`.
- `clifft-656-page-followup-evidence.tar.gz`: earlier selective-page/prototype investigation, preserved unchanged. SHA-256 `d8c9f423a3a5abfedfa7c78dc9b3b0b7f4dd554fef72c91b0e613934b619eded`.
- `clifft-656-prediction-and-kernel-evidence.tar.gz`: the predeclared four-worker page-pair test plus reference, 16-worker preparation-history study, and 72-worker/144-pass normal-build matrix. Includes raw JSONL/perf CSV, debugger scripts/logs, PFNs, exact probe sources, corrected reproduction source, audit notes, analysis scripts, CSVs, summaries, build commands/settings, linker maps/assembly, test logs, frozen harness adapter source, input metadata/circuits and a per-file SHA-256 manifest.
- `clifft-656-normal-builds.tar.gz`: the six exact installed Python packages used in the normal-build checks. These are local Linux CMake installations, not release wheels. They require the matching Python/runtime dependencies, including NumPy and libomp.so.5; compiler and runtime identities are in the evidence bundle.
- `SHA256SUMS`: hashes for every published top-level artifact.

All three new studies' CSV/JSON summaries regenerate byte-for-byte. All six normal extension hashes are verified. Exact seeded aggregate counts are checked in every measured pass. Both audit notes are included: the pair probe's terminal restoration bookkeeping error, and recovery of an inspected normal-build artifact to its original hash before timing. No timing was replaced or discarded.

## Reproduce or audit

Verify `SHA256SUMS` first. The newer evidence/build archives use paths relative to `/tmp`, so extracting those two under `/tmp` recreates their expected directory structure. Preserve the archived result directories before rerunning; scripts refuse to overwrite them. The two older archives retain their original layouts, documented in the linked #656 comments.

For analysis only, run these scripts with system Python:

```bash
python3 /tmp/clifft-656-next/analyze_pair.py
python3 /tmp/clifft-656-next/analyze_history.py
python3 /tmp/clifft-656-next/analyze_normal.py
sha256sum -c /tmp/clifft-656-next/derived-sha256.txt
```

For the live page/history diagnostics, recreate `/tmp/clifft-rc-investigation/sys013` with Python 3.12, Clifft 0.13.0rc1 and NumPy 2.5.4. The scripts require extension SHA-256 `75b71fd3f1f6c2a22141f3f965bd02958f0a68a2d4d08be7b406e006ee2207b4` and abort on another wheel. They use the frozen harness snapshot associated with pinned harness commit `d2e711f61b4cad439a5f1752edefc51bb96905f1`; the exact source and inputs used here are included and hashed. Run on a compatible AVX-512 Linux host with debugger attachment and privileged pagemap access. The usable perf executable on this VM is `/usr/lib/linux-tools/6.8.0-106-generic/perf`, despite its version differing from the running kernel.

```bash
python3 /tmp/clifft-656-next/pair_prediction_r2.py --name pair-reproduction --workers 5 --no-aslr --hash-seed 0
python3 /tmp/clifft-656-next/history_frames.py --name history-reproduction --workers 16 --no-aslr --hash-seed 0
```

`pair_prediction.py` is the exact script used for the retained timings; `pair_prediction_r2.py` fixes the terminal-restoration list alias. The statistical rule and timed page configurations are unchanged. See `pair-audit-note.md` and the raw `restoration-audit.json` before interpreting the old terminal phase name.

For normal builds, `build_matched.sh` records the commands. Resolve its compiler and cached fast_float paths on a new host (the source CMake files contain the dependency pin). It starts from the full parent commit and applies the recorded kernel change. The six installed packages are retained to allow replay without rebuilding. `normal_matrix.py` uses `normal_worker.py` and the pinned adapter; `--correctness` checks full seeded output digests, while the default mode profiles fixed work. Recreate `/tmp/clifft-656-next/build-env` with Python 3.12 and the recorded dependencies. The original correctness versions of both scripts are also retained; later additions only check ISA and binary identity before performance sampling.

These diagnostics intentionally use fixed API-call counts and are not the unchanged release campaign. CPU affinity, one-thread execution, memory ceiling, seeds and output semantics are retained. Run timing alone, without compilation or test suites. The reported cycle changes are local evidence, not release-change percentages. No EC2 instance was launched or restarted for this follow-up.
