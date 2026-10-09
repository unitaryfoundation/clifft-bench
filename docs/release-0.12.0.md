# Clifft 0.12.0 RC collection

The candidate is [`v0.12.0rc1`](https://github.com/unitaryfoundation/clifft/releases/tag/v0.12.0rc1)
at `847757e2bc8c4f7439b9e92011550fd61719eb85`, published October 9, 2026.
The [RC handoff](https://github.com/unitaryfoundation/clifft/pull/561) requests
the recurring release campaign including the fixed-input adder. The separate
triorthogonal collection study is handled elsewhere.

## What to collect

Use the existing [manual EC2 procedure](manual-ec2.md) on one `m7a.xlarge`
placement. Merge the preparation change before cloning `main`; keep the
checkout clean and fixed throughout collection.

| Variant | Exact implementation | Configuration | Cases |
| --- | --- | --- | ---: |
| Previous Clifft | `0.11.0rc1` | Default pipeline plus active-width scheduling; calibrated batching | 8 |
| Current Clifft | `0.12.0rc1` | New default pipeline plus the same scheduler; calibrated batching | 8 |
| SymFT | `0.1.1` at `c89b985` | Existing native CPU build; calibrated batching | 7 |
| xtim | `3.1.4` at `2945407` | Pinned portable build; calibrated chunks; fresh cache per repetition | 4 |
| Stim anchor | `1.16.0` | Existing wheel; calibrated packed-record chunks | 1 |

Both Clifft variants use the checked-in
[scheduler settings](../campaigns/release-v1/clifft-scheduled-execution.json).
The baseline retains the exact RC identity and configuration used by the
reviewed 0.11 QEC execution, continuing the paired history. Each release uses
its own default passes. In 0.12 those include `PhasePolynomialPass` and
`RotationSimplificationPass`; no adapter override is needed.

Workload files and shots per call retain the definitions merged in
[PR #71](https://github.com/unitaryfoundation/clifft-bench/pull/71). Both Clifft
releases include the six historical QEC workloads, the fixed-input 32-qubit
Draper adder, and the new 15-to-1 Reed–Muller distillation circuit. SymFT also
includes 15-to-1; xtim covers cultivation d3/d5, Clifford surface code, and
15-to-1. The existing historical figures still select the six-workload core;
plot selection for the additional results follows collection and review.
Keep the coherent d5/r5 workload at one shot per call on both
sides, even if the candidate becomes much faster.

The run retains five samples of at least 30 seconds for each of 28 cases:
**70 minutes minimum sampling**, plus compilation, calibration, and final-call
overruns. The placement timeout is six hours and each worker has a 12 GiB
address-space ceiling. Bootstrap installs the pinned RC wheel automatically.

After the launch, clone, and data-branch steps in the EC2 procedure:

```bash
export CLIFFT_BENCH_CAMPAIGN=release-v1
./scripts/ec2/bootstrap.sh "$CLIFFT_BENCH_CAMPAIGN"
.venv/bin/clifft-bench list --run-manifest campaigns/release-v1/run.v1.json
./scripts/ec2/run-placement.sh "$CLIFFT_BENCH_CAMPAIGN" "$CLIFFT_BENCH_EXECUTION" 1
./scripts/ec2/finalize.sh "$CLIFFT_BENCH_CAMPAIGN" "$CLIFFT_BENCH_EXECUTION"
```

Use `tmux`, review and push the finalized data branch, and stop the instance as
described in the playbook. Local smoke measurements are only correctness and
integration checks, not release performance evidence.

## Review the evidence

- Confirm eight `current-vs-previous` rows, seven `alternatives-vs-current` rows,
  four `xtim-vs-current` rows, and one `stim-anchor-vs-current` row, with equal
  shots per call within each pair. All tool comparisons reuse the
  current-Clifft measurements.
- Review every failure and calibrated batch selection, both Clifft scheduler
  configurations/statistics, and the exact package/commit identities.
- Report `compile_seconds`, `peak_active_width`, and
  `median_attempted_shots_per_second` from `cases.csv` separately. Full
  `setup_seconds` also includes calibration and should not be called compilation
  time.
- For the adder, report both versions' compilation costs and steady-state
  throughput. An optional estimate for N shots is
  `compile_seconds + N / median_attempted_shots_per_second`; label it as an
  estimate excluding parsing, calibration, and warmup.
- Retain the prepared-input qualification: the circuit preserves `a=37449`
  and produces `b=56173`. Its reduction does not establish performance for
  arbitrary superposition inputs. The docs' widths 15/14/0 describe a
  same-build pass ablation with scheduling disabled, not the scheduled
  release comparison collected here.
- Treat paired changes at or below 1.1% as inconclusive under the existing
  [reference-host convention](reference-host.md).
- For xtim, report fixed-duration averages beginning with an empty cache each
  repetition. Lazy plan construction remains timed; these are not established
  steady-state rates. Review peak RSS alongside throughput and retain the
  [xtim measurement policy](benchmark-contract.md#xtim-comparison).
- For 15-to-1, retain the shared logical-error readout: ideal inverse-T and
  logical-X verification are part of each timed shot. Review the acceptance
  and logical-error counts for both Clifft versions, SymFT, and xtim.

Keep the harness calibration for this release. The new public
`batch_size="tune"` API would answer a separate tuning question: repeatedly
using it inside timed calls would include calibration in sampling time.
The current adapter already reuses the numeric batch size selected during
setup.

## Publish after results review

1. Append the reviewed execution to `reporting/sources.json`; leave existing
   evidence intact. Follow [the reporting workflow](../reporting/README.md)
   to validate the chain and regenerate the QEC figures.
2. Update the benchmark README's version prose, reviewed-result link, image
   reference, and alt text. The next release-comparison pair is
   `v012-vs-v011-{light,dark}.png`; the plotter derives that stem from the
   selected evidence. The other QEC asset names stay the same.
3. Copy the refreshed QEC figures to Clifft's docs. Update its README,
   performance guide, and release update with the reviewed QEC ratios,
   separate adder compilation/sampling results, exact RC identity, and links
   to this execution. Review presentation of the new xtim and 15-to-1 results
   without silently changing the historical median's workload set. Update
   references to the release-specific image stem.
4. Incorporate the separately collected triorthogonal plot/results in the
   release update. Keep same-version pass ablations clearly distinct from
   release-over-release timing claims.
5. Confirm the changelog date and version references before the final release.
   Runtime, compiler, packaging, or build changes require another candidate
   and renewed validation, as specified in the RC handoff.

The QV and Tsim GPU studies have separate reviewed evidence; this campaign
does not refresh those measurements. Existing published figures and selected
sources remain at 0.11 until new results have been collected and reviewed.
