# Native thread scaling validation evidence

Collected by [GitHub Actions run 42275862772](https://github.com/unitaryfoundation/clifft-bench/actions/runs/42275862772) for [PR #113](https://github.com/unitaryfoundation/clifft-bench/pull/113). All 18 cases passed: Clifft, SymFT, and Tsim on three existing circuits at one and two physical cores, with a fixed 12 GiB address-space ceiling. The runner exposes two physical cores; four- and eight-core points were not measured.

This is shared-CI smoke evidence, not an official performance campaign. The tested harness commit `c36eaf901d2da9900ed2fcea59afd5815d24a57f` is retained as an ancestor of this evidence branch. The raw file contains exact hardware, software, affinity, seeds, native tuning, and sample identities.

The distillation case scales with all three native recipes. For the 256-shot wide-state case, SymFT reports one active thread at both budgets: its native chunk is 2,048 shots, larger than the public call. One-shot controls stay near one-core throughput. No call sizes or per-tool chunk rules were changed to force utilization. Tsim retains its upstream Numba disk cache, so setup and kernel-warmup times are not uniformly cold compilation measurements.

From the root of this evidence checkout, regenerate the table, figure, and review:

```bash
uv run --extra report clifft-bench validate evidence/thread-scaling/raw.json
uv run --extra report clifft-bench report evidence/thread-scaling
```

See [the generated review](report/review.md) and [the scaling figure](report/thread-scaling.png). This evidence is intentionally retained on a separate branch and must not be merged into mainline.
