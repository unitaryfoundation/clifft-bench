# Clifft 0.14.0rc3 CPU benchmark results

The official Clifft-only campaign ran on November 24, 2026, on the same AWS
`m7a.4xlarge` used for RC1. All 74 measured cases passed correctness and produced
three timed samples each. The other 36 cases requested packed output from Clifft
0.13.0, which does not support that endpoint. [Case table](cases.csv) ·
[Raw samples](raw.json) · [Combined report and figures](report/review.md)

| Report input | Execution | Measurements used |
| --- | --- | --- |
| [RC3 Clifft-only campaign](https://github.com/unitaryfoundation/clifft-bench/tree/19e1601f3911a3053810b1ef5e132d7f30c32294/results/release-clifft/0.14.0rc3-20261124-0148) | `06b9e12f-1306-4659-a54e-0266b720465a` | Clifft 0.14.0rc3 and its paired 0.13.0 baseline |
| [RC1 full campaign](https://github.com/unitaryfoundation/clifft-bench/tree/1993504c9217da4d4f432bcf853614b29c315bec/results/release-v1/0.14.0rc1-20261120-2134) | `1b252b89-75d2-4881-ade9-114e09db6c9d` | Tsim 1.0.0, xtim 4.1.2, SymFT 2026.10.8, and Stim 1.16.0 |

The [throughput JSON](report/simulator-throughput.json) identifies the source of
each plotted value. To regenerate both static figures and the JSON from the
repository root, run the following command.

```bash
uv run --extra report clifft-bench report results/release-clifft/0.14.0rc3-20261124-0148 \
  --peer-results results/release-v1/0.14.0rc1-20261120-2134
```
