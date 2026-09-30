# Compiler configuration

Use the standard opt-in `ActiveWidthSchedulePass` after Clifft's default
pipeline. The release configuration is checked in as
[`clifft-scheduled-execution.json`](../campaigns/release-v1/clifft-scheduled-execution.json).
When pinning the scheduler-capable RC, use this object for `clifft-current`'s
`execution` in the release manifest. It pins the scheduler options and retains
the existing batch calibration and workload shot counts.

The equivalent public Python configuration is:

```python
manager = clifft.default_hir_pass_manager()
manager.add(clifft.ActiveWidthSchedulePass(
    beam_width=8,
    search_budget=16.0,
    noise_transparent=True,
    sink_neutral_rotations=True,
))
manager.run(hir)
```

Use this configuration across the release workloads. Add a per-workload
`execution.clifft_scheduler` override only for a clear, repeatable throughput
improvement. Keep the standard search when timings are close. Compilation cost
is reported separately from steady-state throughput.

To check the configuration, run copies of the same manifest with scheduling
enabled and with `clifft_scheduler: {"enabled": false}` using `clifft-bench run`.
Both runs use the existing batch calibration. Compare repeated sampling
measurements on the same host, then confirm the configuration on the published
RC wheel before the official campaign. A calibrated scheduler-off run is a
comparison baseline, not an out-of-box measurement.

Omitting `clifft_scheduler` disables scheduling and supports older Clifft
releases. Explicitly enabling it on an unsupported installation fails. The
current release manifest retains its last measured version until the RC is
pinned. Raw results and finalized CSVs record the actual compiler settings,
compile time, and peak width.
