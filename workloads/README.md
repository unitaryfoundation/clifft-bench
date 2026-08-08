# Phase 1 workload corpus

The checked-in `.stim` files are immutable benchmark inputs. Their SHA-256
digests, semantic contracts, compatible adapters, and source commits live in
[`manifests/workloads.v1.json`](../manifests/workloads.v1.json).

The Phase 1 corpus deliberately covers three distinct regimes:

- distance-3 magic-state injection and cultivation (small active width),
- distance-5 magic-state injection and cultivation (larger active width), and
- a distance-7 pure-Clifford surface-code memory workload.

The cultivation files use the extended Clifft/SymFT Stim-like dialect. The
surface-code file is a genuine Stim circuit. Rotation arguments use half-turns,
so `R_Z(0.02)` represents `0.02 * pi` radians.

These files are byte-for-byte copies from
[`haoliri0/SymFT_Test`](https://github.com/haoliri0/SymFT_Test) commit
`9ec5790322f93140e78bdb6d6620a2a43eceba0b`. That repository in turn records
the cultivation inputs as canonical copies from `clifft-paper`. The applicable
Apache-2.0 license is included as
[`circuits/LICENSE-Clifft-paper`](circuits/LICENSE-Clifft-paper).
