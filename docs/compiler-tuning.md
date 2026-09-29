# Compiler tuning before a release

The release comparison measures tuned single-core steady-state throughput using
the capabilities available in each implementation. For Clifft releases with
`ActiveWidthSchedulePass`, compare compiler profiles before choosing the
per-workload configuration. Batch calibration runs independently for each
profile because scheduling can change the best scalar/packed choice.

Use the same circuits, raw-record-parity counts, FP64 precision, one CPU,
memory ceiling, and `shots_per_call` as the release comparison. Compilation and
calibration remain setup costs; retain both alongside throughput.

## Prepare the implementation

Follow [release preparation](../CONTRIBUTING.md#preparing-a-release-campaign)
to record the exact implementation in the software manifest and install its
environment. Set its `python_executable_env` to the interpreter containing that
installation. Runtime version checks remain enabled during tuning.

Before the RC exists, use a local **smoke** copy of the release manifest. Record
the development build's actual version and full source commit in a local
software manifest, set `release_datetime` to `null`, and describe its real build
settings. Remove any copied `source_tag`, release display label, and wheel
environment that do not describe that build. Point the local manifest's
`clifft-current` variant at this identity. Source-build pilot results are
provisional; they do not stand in for measurements of the published RC wheel.

## Generate and run trials

With the chosen implementation recorded in the input manifest:

```bash
uv run clifft-bench tuning-manifest \
  --run-manifest campaigns/release-v1/run.v1.json \
  --variant clifft-current \
  --output /tmp/clifft-tuning.json
uv run clifft-bench list --run-manifest /tmp/clifft-tuning.json --json
uv run clifft-bench run --run-manifest /tmp/clifft-tuning.json \
  --output /tmp/clifft-tuning-raw.json
uv run clifft-bench tuning-summary --run-manifest /tmp/clifft-tuning.json \
  /tmp/clifft-tuning-raw.json --output /tmp/clifft-tuning-summary.json
```

The currently checked-in release manifest still identifies the last measured
release. Update the implementation first, or pass the local development
manifest above. Requesting scheduling on an older installation fails explicitly.

The generated manifest has three profiles for each workload:

| Profile | Scheduler configuration |
|---|---|
| `scheduler-off` | Default compiler pipeline, with scheduling disabled |
| `scheduler-default` | Scheduling after the default pipeline; beam width 8, search budget 16 |
| `scheduler-deep` | The same pass with `search_budget: null`, disabling search narrowing |

Both enabled profiles explicitly use `noise_transparent: true` and
`sink_neutral_rotations: true`. These are pinned options, independent of any
future change in library defaults. `null` does not remove the harness's setup
timeout or memory limit. Each profile gets a fresh worker process. Profiles run
adjacently for each workload using the runner's serial manifest order.

For a bounded local pilot, lower `setup_timeout_seconds` in the generated
manifest and use `run --min-sample-seconds 0.25 --repetitions 3`. Existing batch
calibration still uses three one-second probes per eligible size. Keep the
longer release sampling intervals for configuration confirmation on the
reference host. Add further explicit profile cases to the generated manifest
only when earlier evidence warrants larger beams or other settings.

## Review the recommendation

The summary ranks profiles by the median of the sampling repetitions **after**
batch calibration. It retains all profile timings, median absolute deviations,
selected batch sizes, compilation costs, peak widths, scheduler statistics,
and calibration durations. Exact ties prefer lower compilation time, then a
smaller batch size, then the lexicographically last case ID. Enabled profiles
that report no schedule change remain visible but cannot win on timing noise.
Scheduling-off coverage is required for every workload.

Missing cases, failed profiles, changed circuit identities, mismatched compiler
metadata, and incompatible measurement conditions prevent a recommendation.
Investigate those failures and rerun; they are not evidence that another
configuration is faster. Review noise-sensitive results and repeat close
comparisons. The summary is a provisional proposal, not an automatic edit of
the official campaign.

The summary's `workloads` array can replace the selected release variant's
workload selections after review. Each entry preserves its shot count and
proposes a compiler override while retaining `batch_size: "calibrate"` for
fresh calibration on the reference host. For example:

```json
{
  "workload_id": "coherent-surface-d3-r3-p1e-3-rz2e-2",
  "shots_per_call": 100000,
  "execution": {
    "clifft_scheduler": {
      "enabled": true,
      "beam_width": 8,
      "search_budget": 16.0,
      "noise_transparent": true,
      "sink_neutral_rotations": true
    },
    "batch_enabled": true,
    "batch_size": "calibrate"
  }
}
```

This is a syntax example, not a claimed winning configuration. Per-workload
execution keys override variant keys. A nested `clifft_scheduler` object is
replaced as a whole, so `{"enabled": false}` cleanly disables an inherited
profile. Omission retains the default compiler pipeline; older releases remain
usable. Scheduler settings on another adapter are rejected.

## Confirm on the RC wheel

After publishing the RC, pin its exact version, source tag, commit, and locked
wheel environment. Repeat or confirm tuning against that artifact on the
reference host, then collect fresh official measurements with the reviewed
per-workload profiles. Apply the existing batching policy to the previous
Clifft release and the other tools. Retain the same measured current-Clifft
cases for release and cross-tool comparisons.

Compilation time, final peak width, compiler configuration, and calibration
time are exported in `cases.csv`; both sides' compiler settings, compilation
time, and peak width are carried into `comparisons.csv`. Include a per-circuit
configuration table with release figures and label the results as tuned
single-core throughput. Raw JSON retains the complete search and probe evidence.

The coherent d5/r5 historical workload uses one shot per call, so only scalar
batch size 1 is eligible. A pilot with larger calls must change that measurement
parameter equally for every implementation being compared. Preserve the
historical series and publish a distinct series or a reviewed methodology
transition: the existing history join deliberately rejects changed call sizes.
