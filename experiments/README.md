# Optional performance studies

These studies answer questions outside recurring previous/current Clifft and
cross-tool release comparisons. Each owns its environment, timing contract,
correctness checks, raw evidence, and publication workflow:

- [Quantum Volume](qv/): the paper's setup-plus-one-shot comparison against
  Qiskit Aer, Qulacs, and qsim, including statevector validation and optional
  light/dark figures.
- [Tsim GPU](tsim-gpu/): GPU sampling on the QEC corpus with bounded preparation,
  independent warm probes, process deadlines, resumable failures, and raw samples.

Run and publish a study when its question is useful. Neither study is an input
to `clifft-bench report` or a prerequisite for release collection. Their
[workflow](../.github/workflows/experiments.yml) runs on relevant code/input
changes or manual dispatch. Study-specific validation commands live in each
README; no shared experiment framework is required.

Keep existing evidence tied to its source revision. Changing a study's code,
software, hardware, or measurement settings requires a new execution. The fixed
QV circuits in the [release corpus](../workloads/README.md) measure repeated
sampling and do not replace the independent Quantum Volume study.
