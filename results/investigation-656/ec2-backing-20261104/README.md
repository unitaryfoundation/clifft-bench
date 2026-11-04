# EC2 d5 backing comparison for PR #657

This evidence tests whether the metadata-hoisting patch reduces a controlled backing penalty on the existing clifft-bench reference instance. It uses normal builds and anonymous coefficient pages. It is separate from the recurring release campaign.

Read `findings.md` for results and limits, `summary.json` for the complete derived statistics, and `samples.csv` for all 40 measured passes. `activity-summary.json` summarizes the ten-second host activity log; short-lived work can fall between snapshots.

## Files

- `clifft-656-ec2-backing-evidence.tar.gz`: EC2 raw events, perf CSVs, diagnostic GDB scripts/logs, frame assignments, host activity, environment and shutdown records, complete validation/analysis code, and the retained failed-start/interrupted-local audit.
- `clifft-656-backing-inputs.tar.xz`: exact normal parent/PR packages, Python 3.12.14, NumPy 2.5.4, OpenMP shared library, pinned benchmark adapter/workload, page helper and the exact controller/worker scripts. All 5,512 input-file hashes were checked on EC2 before execution. Recompression from the transport gzip archive preserves those file hashes.
- `input-manifest.json`: SHA-256 for every input file.
- `SHA256SUMS`: hashes for the published files.

The input archive contains public software and benchmark material only. It contains no SSH keys or AWS credentials.

## Reproduction

Use an isolated Linux host with AVX-512, 4 KiB pages, GDB, perf, and permission to read guest PFNs. The measured host is the m7a.xlarge instance recorded in the evidence, with Ubuntu 24.04 and kernel 7.0.0-1014-aws. Other machines test generalization; their PFN digits do not identify the same host physical structures.

Extract the input archive into a fresh directory. The Python controller resolves inputs relative to its own directory; its perf path names the measured kernel tool. Read the retained protocol first. Record the original perf/ptrace settings, permit same-user perf and GDB attachment for the run, and restore the settings afterward. On the measured host the temporary values were `kernel.perf_event_paranoid=1` and `kernel.yama.ptrace_scope=0`; originals were 4 and 1.

Run `python3 run.py --smoke` first. It performs page reversals and full-output checks without perf measurements. Then `python3 run.py` creates the fixed cohort and collects all 40 passes. Output directories must be new; never overwrite prior evidence or reuse a failed cohort. Run `python3 analyze.py <experiment-directory>` and `python3 analyze_activity.py <experiment-directory>` using the analysis scripts in the evidence archive. The activity analysis requires the optional retained `monitor.py` log.

The retained measurement wrapper provides a bounded timeout, a monitor on core 3 and restoration of profiling settings. Its `cd` points at the original experiment directory; adjust that path for a fresh reproduction. Leave the controller's CPU mask unrestricted so workers can select CPU 0. The first launcher inherited an incompatible mask; its zero-sample failure is retained under `setup-failure-affinity`.

A shutdown timer is appropriate only on a dedicated host whose instance-initiated shutdown behavior has been confirmed to be Stop. For this run, the maintainer confirmed the setting, a two-hour fallback was armed, and the experiment's evidence was copied off before final shutdown. AWS stopped-state confirmation is delegated to the maintainer because only SSH access was provided.

This branch retains investigation evidence separately from mainline, as required by clifft-bench's instructions.
