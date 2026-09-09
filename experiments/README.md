# One-off performance experiments

This directory is for questions that are useful but are not answered on every
Clifft release. An experiment should be a self-contained script or small folder
with its own environment, measurement notes, raw output, and plotting code.
It should not add commands, schemas, or branches to the recurring release
workflow unless at least two real experiments need the same mechanism.

[`qv/`](qv/) contains the standalone Quantum Volume paper refresh. It carries
its own locked environment, runner, validator, results directory, plotter, and
EC2 instructions without adding experiment concepts to the core CLI.

[`tsim-gpu/`](tsim-gpu/) contains the maintained Tsim GPU comparison on the
immutable QEC corpus. It has a pinned source/dependency lock, hard preparation
budgets, warm throughput tuning, cached failure evidence, and its own GPU-host
instructions.

[`whole-machine/`](whole-machine/) collects the QEC comparison on an
`m8a.16xlarge` using all 64 physical cores. It uses native Clifft/SymFT threads,
persistent Stim/Tsim CPU processes, full-load batch tuning, and an aggregate
memory limit, while reusing the successful Tsim GPU-component measurements.

## Quantum Volume and multicore scaling

The former integrated Quantum Volume campaign was removed with the 0.x result
archive. The replacement under [`qv/`](qv/) is deliberately a purpose-built
refresh of the paper experiment rather than a general QV subsystem.

A QV study should record at least:

- circuit generator, version, seed, width, depth, and circuit digest;
- simulator version, build configuration, precision, and thread count;
- physical-core selection and affinity policy;
- setup and execution timing boundaries;
- memory limit and peak memory; and
- the exact reference host and source commit.

The two fixed QV circuits under `workloads/circuits/` remain available for
quick adapter checks. A new cross-tool study should normally generate a fresh,
versioned circuit matrix appropriate to its question.
