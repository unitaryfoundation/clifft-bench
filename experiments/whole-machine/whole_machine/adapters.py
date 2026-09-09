"""Pinned public sampling APIs; imports happen after worker affinity is applied."""

from __future__ import annotations

from pathlib import Path

from .common import COUNT_KEYS, PINNED, file_digest


class Prepared:
    def begin(self, seed):
        self.seed = seed


class Clifft(Prepared):
    def __init__(self, workload, config, workers, seed):
        import clifft

        self.lib, self.workers, self.config = clifft, workers, config
        circuit = clifft.parse(Path(workload["artifact_path"]).read_text())
        hir = clifft.trace(circuit)
        clifft.default_hir_pass_manager().run(hir)
        mask = [1] * int(hir.num_detectors)
        self.program = clifft.lower(hir, postselection_mask=mask)
        self.observable = workload["semantics"]["observable_index"]
        self.metadata = {
            "tool": "clifft",
            "version": str(clifft.version()),
            "threads": workers,
            "thread_layout": [workers, 1],
            "batch_size": config["batch_size"],
            "keep_records": False,
            "cpu_baseline": clifft.CPU_BASELINE,
            "peak_active_width": int(self.program.peak_active_width),
            **{k: int(getattr(hir, k)) for k in workload["expected_metadata"]},
        }

    def sample(self, shots):
        r = self.lib.sample_survivors(
            self.program,
            shots,
            seed=self.seed,
            threads=self.workers,
            thread_layout=(self.workers, 1),
            batch_size=self.config["batch_size"],
            keep_records=False,
        )
        self.seed = (self.seed + 1) % (2**64)
        return dict(
            zip(
                COUNT_KEYS,
                map(
                    int,
                    (r.total_shots, r.passed_shots, r.discards, r.observable_ones[self.observable]),
                ),
            )
        )


class Symft(Prepared):
    def __init__(self, workload, config, workers, seed):
        import symft

        self.workers = workers
        circuit = symft.Circuit(path=Path(workload["artifact_path"]))
        self.sampler = circuit.compile_counts_sampler(
            batch=config["batch_size"] > 1,
            batch_size=config["batch_size"],
            sample_chunk_shots=config["sample_chunk_shots"],
            threads=workers,
            cuda=False,
            observable=workload["semantics"]["observable_index"],
            postselect_detectors=True,
        )
        info = dict(self.sampler.info)
        if info["threads"] != workers:
            raise ValueError("SymFT did not accept the native thread budget")
        expected_backend = "batch" if config["batch_size"] > 1 else "single"
        if (
            info["backend"] != expected_backend
            or info["batch_size"] != (config["batch_size"] if expected_backend == "batch" else 0)
            or info["sample_chunk_shots"] != config["sample_chunk_shots"]
        ):
            raise ValueError("SymFT did not accept the candidate batch/chunk settings")
        self.metadata = {
            "tool": "symft",
            "version": symft.__version__,
            "threads": workers,
            "info": info,
            "simd_backend": str(symft.simd_backend()),
            **{k: int(getattr(circuit, k)) for k in workload["expected_metadata"]},
        }

    def sample(self, shots):
        r = self.sampler.sample(shots=shots, stream_id=self.seed)
        self.seed = (self.seed + 1) % (2**64)
        if r["active_threads"] != self.workers:
            raise ValueError(
                f"SymFT used {r['active_threads']} of {self.workers} threads; "
                "public call needs more chunks"
            )
        return dict(
            zip(
                COUNT_KEYS,
                map(int, (r["shots"], r["accepted"], r["discarded"], r["logical_errors"])),
            )
        )


class Stim(Prepared):
    def __init__(self, workload, config, workers, seed):
        from clifft_bench.adapters.stim import StimAdapter

        self.workload, self.config = workload, config
        self.prepared = StimAdapter().prepare(
            artifact_path=Path(workload["artifact_path"]),
            workload=workload,
            execution={"batch_size": config["batch_size"] or config["shots_per_call"]},
        )
        self.metadata = self.prepared.runtime_metadata
        if config["batch_size"] == 0:
            # The calibrated call size is recorded in the result configuration.
            # Do not label its initial size as the effective final chunk size.
            self.metadata.update(
                effective_batch_size=None, chunk_policy="one chunk per public call"
            )
        else:
            self.metadata["chunk_policy"] = "fixed public-detector-sampler chunks"

    def begin(self, seed):
        self.prepared.begin_sample(seed)

    def sample(self, shots):
        # The unchunked candidate follows an adapted public-call shape.
        if self.config["batch_size"] == 0:
            self.prepared._chunk = shots
        counts = self.prepared.sample(shots, 0).as_dict()
        return {k: counts[k] for k in COUNT_KEYS}


class Tsim(Prepared):
    def __init__(self, workload, config, workers, seed):
        import jax
        import numpy as np
        import tsim

        if any(d.platform != "cpu" for d in jax.devices()):
            raise ValueError("Tsim CPU collection initialized a non-CPU device")
        self.np = np
        circuit = tsim.Circuit(Path(workload["artifact_path"]).read_text())
        self.sampler = circuit.compile_detector_sampler(strategy="cutting", seed=seed % 2**32)
        if self.sampler._program.components:
            raise ValueError("Tsim circuit requires component evaluation; use the GPU experiment")
        self.mask = np.ones(circuit.num_detectors, dtype=bool)
        self.observable = workload["semantics"]["observable_index"]
        self.metadata = {
            "tool": "tsim",
            "version": PINNED["bloqade-tsim"][0],
            "execution_path": "direct-cpu",
            "compiled_graph_count": 0,
            "threads": 1,
            "strategy": "cutting",
            "seed_policy": "continuous stream within one candidate or final run",
            "jax_devices": [str(d) for d in jax.devices()],
            **{k: int(getattr(circuit, k)) for k in workload["expected_metadata"]},
        }

    def sample(self, shots):
        np = self.np
        det, obs = self.sampler.sample(
            shots,
            separate_observables=True,
            use_detector_reference_sample=False,
            use_observable_reference_sample=False,
            postselection_mask=self.mask,
        )
        if det.shape != (shots, len(self.mask)) or obs.shape[0] != shots:
            raise ValueError("Tsim returned the wrong number of detector/observable records")
        keep = ~np.any(det, axis=1)
        accepted = int(np.count_nonzero(keep))
        return dict(
            zip(
                COUNT_KEYS,
                (
                    shots,
                    accepted,
                    shots - accepted,
                    int(np.count_nonzero(obs[keep, self.observable])),
                ),
            )
        )


def prepare(tool, workload, config, workers, seed):
    if file_digest(workload["artifact_path"]) != workload["artifact"]["sha256"]:
        raise ValueError("circuit changed between collection and worker setup")
    prepared = {"clifft": Clifft, "symft": Symft, "stim": Stim, "tsim": Tsim}[tool](
        workload, config, workers, seed
    )
    for key, value in workload["expected_metadata"].items():
        if prepared.metadata[key] != value:
            raise ValueError(f"{tool}: circuit metadata mismatch for {key}")
    prepared.begin(seed)
    return prepared
