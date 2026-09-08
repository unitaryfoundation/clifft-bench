"""One circuit/strategy per process: compile once, tune warm shapes, sample many."""

from __future__ import annotations

import json
import statistics
import sys
import time
import traceback
from pathlib import Path

from .common import environment_metadata, now, require_gpu, write_json


def sample_counts(
    sampler,
    shots: int,
    batch_size: int,
    semantics: dict,
    num_detectors: int,
    *,
    short_circuit: bool = True,
):
    import numpy as np

    postselect = semantics["postselect_all_detectors"]
    detectors, observables = sampler.sample(
        shots,
        batch_size=batch_size,
        separate_observables=True,
        use_detector_reference_sample=False,
        use_observable_reference_sample=False,
        postselection_mask=(
            np.ones(num_detectors, dtype=bool) if postselect and short_circuit else None
        ),
    )
    # The public API returns NumPy arrays: device execution and transfer have
    # completed. Discarded placeholders retain the firing direct detector bits.
    if detectors.shape != (shots, num_detectors) or observables.shape[0] != shots:
        raise ValueError("Tsim did not return one detector/observable row per attempted shot")
    keep = ~np.any(detectors, axis=1) if postselect else np.ones(shots, dtype=bool)
    accepted = int(np.count_nonzero(keep))
    errors = int(np.count_nonzero(observables[keep, semantics["observable_index"]]))
    return {
        "attempted_shots": shots,
        "accepted_shots": accepted,
        "discarded_shots": shots - accepted,
        "logical_errors": errors,
    }


def summarize(samples: list[dict]) -> dict:
    rates = [s["throughput_attempted_shots_per_second"] for s in samples]
    median = statistics.median(rates)
    return {
        "median_attempted_shots_per_second": median,
        "mad_attempted_shots_per_second": statistics.median(abs(r - median) for r in rates),
    }


def run(request: dict, checkpoint: Path) -> dict:
    record = {
        "schema_version": "clifft-bench/tsim-gpu-case/v1",
        "status": "running",
        "workload": request["workload"],
        "strategy": request["strategy"],
        "seed": request["seed"],
        "started_at": now(),
        "tuning": [],
        "samples": [],
        "phase": "import",
        "phase_started": time.monotonic(),
    }

    def save(**changes):
        record.update(changes)
        write_json(checkpoint, record)

    def phase(name):
        save(phase=name, phase_started=time.monotonic(), call_started=None)

    save()
    try:
        import jax
        import tsim

        device = require_gpu(jax)[0]
        workload, options = request["workload"], request["options"]
        semantics = workload["semantics"]
        phase("compile")
        started = time.perf_counter()
        circuit = tsim.Circuit(Path(workload["artifact_path"]).read_text())
        for key, expected in workload["expected_metadata"].items():
            if getattr(circuit, key) != expected:
                raise ValueError(f"{key}: circuit metadata differs from the workload manifest")
        sampler = circuit.compile_detector_sampler(
            strategy=request["strategy"], seed=request["seed"]
        )
        compile_seconds = time.perf_counter() - started
        # Diagnostic use of pinned upstream internals only; sampling is public API.
        graphs = [
            g for component in sampler._program.components for g in component.compiled_scalar_graphs
        ]
        runtime = {
            "execution_path": "gpu-components" if sampler._program.components else "direct-cpu",
            "compiled_graph_count": sum(g.num_graphs for g in graphs),
            "compiled_phase_dtypes": sorted({str(g.node_phases.phases.dtype) for g in graphs}),
            "compiled_array_dtypes": sorted(
                {
                    str(leaf.dtype)
                    for g in graphs
                    for leaf in jax.tree_util.tree_leaves(g)
                    if hasattr(leaf, "dtype")
                }
            ),
            "jax_x64_enabled": bool(jax.config.jax_enable_x64),
            "jax_default_matmul_precision": jax.config.jax_default_matmul_precision,
            "record_dtype": "bool",
            "postselection_short_circuit": semantics["postselect_all_detectors"],
            "reference_convention": "raw-record-parity",
            "seed_policy": "continuous Tsim stream across tuning and final repetitions",
        }
        save(compile_seconds=compile_seconds, runtime_metadata=runtime)
        phase("tune")
        tune_started = time.perf_counter()
        shots = options["shots_per_call"]

        def one_call(batch, *, short_circuit=True):
            save(call_started=time.monotonic())
            before = time.perf_counter()
            counts = sample_counts(
                sampler, shots, batch, semantics, circuit.num_detectors, short_circuit=short_circuit
            )
            duration = time.perf_counter() - before
            save(call_started=None)
            return counts, duration

        def measure(batch, seconds):
            total = dict.fromkeys(
                ("attempted_shots", "accepted_shots", "discarded_shots", "logical_errors"), 0
            )
            elapsed = 0.0
            calls = 0
            while calls == 0 or elapsed < seconds:
                counts, duration = one_call(batch)
                for key, value in counts.items():
                    total[key] += value
                elapsed += duration
                calls += 1
            return {
                **total,
                "duration_seconds": elapsed,
                "api_calls": calls,
                "throughput_attempted_shots_per_second": total["attempted_shots"] / elapsed,
            }

        # Skip shapes exceeding Tsim's own conservative device-memory estimate.
        # This is an estimate, not a promise; the controller also monitors RSS.
        estimated_capacity = int(sampler._estimate_batch_size())
        candidates = sorted(
            set(min(b, shots) for b in options["batch_sizes"]) | {min(shots, estimated_capacity)}
        )
        if "recovery_batch_sizes" in request:
            # Do not reintroduce the automatic large shape that killed tuning.
            candidates = request["recovery_batch_sizes"]
        if not graphs:  # Direct Clifford path ignores batch_size; no duplicate tuning.
            candidates = [shots]
        save(batch_candidates=candidates, estimated_batch_capacity=estimated_capacity)
        for batch in candidates:
            if graphs and batch > estimated_capacity:
                record["tuning"].append({"batch_size": batch, "status": "memory-estimate-skip"})
                save()
                continue
            candidate = {"batch_size": batch, "status": "running"}
            record["tuning"].append(candidate)
            save(active_batch_size=batch)
            try:
                # Force the component path once even when all direct detectors
                # happen to reject this warmup's shots. Then warm the masked path.
                _, warmup = one_call(batch, short_circuit=False)
                if graphs and semantics["postselect_all_detectors"]:
                    _, masked_warmup = one_call(batch)
                    warmup += masked_warmup
                probes = [
                    measure(batch, options["probe_seconds"])
                    for _ in range(options["probe_repetitions"])
                ]
                candidate.update(
                    status="success", warmup_seconds=warmup, probes=probes, **summarize(probes)
                )
            except Exception as error:  # Preserve successful smaller candidates on Python errors.
                candidate.update(
                    status="error", error={"type": type(error).__name__, "message": str(error)}
                )
            save()
        successful = [c for c in record["tuning"] if c["status"] == "success"]
        if not successful:
            raise RuntimeError("no batch configuration completed warm throughput probes")
        selected = max(
            successful, key=lambda c: (c["median_attempted_shots_per_second"], -c["batch_size"])
        )
        save(
            selected_batch_size=selected["batch_size"],
            selection_probe_rate=selected["median_attempted_shots_per_second"],
            tune_seconds=time.perf_counter() - tune_started,
        )
        phase("measure")
        for repetition in range(options["repetitions"]):
            sample = measure(selected["batch_size"], options["sample_seconds"])
            record["samples"].append({"repetition": repetition, **sample})
            save()
        save(
            status="success",
            summary=summarize(record["samples"]),
            device_memory_stats=device.memory_stats(),
        )
    except Exception as error:
        save(
            status="error",
            error={
                "type": type(error).__name__,
                "message": str(error),
                "phase": record["phase"],
                "traceback": traceback.format_exc(),
            },
        )
    save(finished_at=now())
    return record


def main() -> int:
    if sys.argv[1] == "preflight":
        write_json(Path(sys.argv[2]), environment_metadata(allow_dirty="--allow-dirty" in sys.argv))
        return 0
    request_path, checkpoint = map(Path, sys.argv[1:])
    result = run(json.loads(request_path.read_text()), checkpoint)
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
