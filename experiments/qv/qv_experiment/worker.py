"""Fresh-process worker for one simulator and one generated QV circuit."""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from qv_experiment.system import apply_resources, peak_rss_bytes

DEPENDENCIES = {
    "clifft": ["clifft", "numpy"],
    "qiskit": ["qiskit", "qiskit-aer", "numpy"],
    "qulacs": ["qulacs", "numpy"],
    "qsim": ["qsimcirq", "cirq-core", "numpy", "ply"],
}


def _versions(simulator: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for distribution in DEPENDENCIES[simulator]:
        try:
            found[distribution] = version(distribution)
        except PackageNotFoundError:
            continue
    return found


def _run_clifft(
    qasm: str, threads: int, seed: int
) -> tuple[dict[str, float], dict[str, Any]]:
    import clifft

    from qv_experiment.qasm_adapter import to_clifft_stim

    source = to_clifft_stim(qasm)
    started = time.perf_counter()
    program = clifft.compile(source)
    compile_seconds = time.perf_counter() - started
    started = time.perf_counter()
    clifft.sample(program, shots=1, seed=seed, threads=threads)
    sample_seconds = time.perf_counter() - started
    return (
        {
            "execution_seconds": compile_seconds + sample_seconds,
            "compile_seconds": compile_seconds,
            "sample_seconds": sample_seconds,
        },
        {
            "cpu_baseline": str(getattr(clifft, "CPU_BASELINE", "unknown")),
            "thread_interface": "sample-argument",
        },
    )


def _run_qiskit(
    qasm: str, threads: int, seed: int
) -> tuple[dict[str, float], dict[str, Any]]:
    from qiskit.circuit import QuantumCircuit
    from qiskit.compiler import transpile
    from qiskit_aer import AerSimulator

    simulator = AerSimulator(method="statevector", max_parallel_threads=threads)
    circuit = transpile(
        QuantumCircuit.from_qasm_str(qasm),
        simulator,
        seed_transpiler=seed,
    )
    started = time.perf_counter()
    simulator.run(circuit, shots=1, seed_simulator=seed).result()
    elapsed = time.perf_counter() - started
    return (
        {
            "execution_seconds": elapsed,
            "compile_seconds": 0.0,
            "sample_seconds": 0.0,
        },
        {},
    )


def _run_qulacs(
    qasm: str, _threads: int, _seed: int
) -> tuple[dict[str, float], dict[str, Any]]:
    from qulacs import QuantumState

    from qv_experiment.qasm_adapter import to_qulacs_circuit

    circuit, qubits = to_qulacs_circuit(qasm)
    state = QuantumState(qubits)
    started = time.perf_counter()
    circuit.update_quantum_state(state)
    elapsed = time.perf_counter() - started
    return (
        {
            "execution_seconds": elapsed,
            "compile_seconds": 0.0,
            "sample_seconds": 0.0,
        },
        {},
    )


def _run_qsim(
    qasm: str, threads: int, seed: int
) -> tuple[dict[str, float], dict[str, Any]]:
    import qsimcirq

    from qv_experiment.qasm_adapter import to_cirq_circuit

    circuit = to_cirq_circuit(qasm)
    simulator = qsimcirq.QSimSimulator(
        qsimcirq.QSimOptions(cpu_threads=threads),
        seed=seed,
    )
    started = time.perf_counter()
    simulator.run(circuit, repetitions=1)
    elapsed = time.perf_counter() - started
    return (
        {
            "execution_seconds": elapsed,
            "compile_seconds": 0.0,
            "sample_seconds": 0.0,
        },
        {},
    )


RUNNERS: dict[
    str,
    Callable[[str, int, int], tuple[dict[str, float], dict[str, Any]]],
] = {
    "clifft": _run_clifft,
    "qiskit": _run_qiskit,
    "qulacs": _run_qulacs,
    "qsim": _run_qsim,
}


def validate_timings(timings: dict[str, float]) -> None:
    execution_seconds = float(timings["execution_seconds"])
    if not math.isfinite(execution_seconds) or execution_seconds <= 0:
        raise ValueError(
            "simulator returned a non-positive or non-finite execution time: "
            f"{execution_seconds!r}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("simulator", choices=sorted(RUNNERS))
    parser.add_argument("qasm", type=Path)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--memory-limit-gib", type=float, required=True)
    parser.add_argument("--cpu-set", default="")
    return parser


def main() -> int:
    args = _parser().parse_args()
    cpu_set = [int(value) for value in args.cpu_set.split(",") if value]
    try:
        resources = apply_resources(cpu_set, args.memory_limit_gib)
        timings, runtime = RUNNERS[args.simulator](
            args.qasm.read_text(),
            args.threads,
            args.seed,
        )
        validate_timings(timings)
        print(
            json.dumps(
                {
                    "status": "success",
                    "timing": {
                        **timings,
                        "timed_region": "original-clifft-paper-qv-v1",
                    },
                    "threads_requested": args.threads,
                    "peak_rss_bytes": peak_rss_bytes(),
                    "resources": resources,
                    "runtime_metadata": runtime,
                    "dependencies": _versions(args.simulator),
                },
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as error:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                    "peak_rss_bytes": peak_rss_bytes(),
                    "dependencies": _versions(args.simulator),
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
