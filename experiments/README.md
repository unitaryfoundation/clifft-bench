# One-off performance experiments

This directory is for questions that are useful but are not answered on every
Clifft release. An experiment should be a self-contained script or small folder
with its own environment, measurement notes, raw output, and plotting code.
It should not add commands, schemas, or branches to the recurring release
workflow unless at least two real experiments need the same mechanism.

## Quantum Volume and multicore scaling

The former integrated Quantum Volume campaign was removed with the 0.x result
archive. A future rerun should be built here around the question being asked at
that time, rather than preserving a general QV subsystem between infrequent
runs.

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
