"""Fresh-process worker for one deterministic QV circuit and simulator."""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Sequence

DEPENDENCIES = {
    "clifft": ["clifft", "numpy"],
    "qiskit": ["qiskit", "qiskit-aer", "numpy"],
    "qulacs": ["qulacs", "numpy"],
    "qsim": ["qsimcirq", "cirq-core", "numpy", "ply"],
    "qrack": [
        "qiskit-qrack-provider",
        "pyqrack",
        "pyqrack-cpu",
        "qiskit",
        "qiskit-aer",
        "numpy",
    ],
}
GENERATOR_DEPENDENCIES = [
    "dill",
    "numpy",
    "qiskit",
    "rustworkx",
    "scipy",
    "stevedore",
    "typing-extensions",
]


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content)
    os.replace(temporary, path)


def _generate(qubits: int, seed: int, output: Path) -> None:
    import qiskit.qasm2
    from qiskit.circuit.library import quantum_volume
    from qiskit.compiler import transpile

    circuit = quantum_volume(qubits, seed=seed)
    circuit = transpile(circuit, basis_gates=["cx", "u3"], optimization_level=0)
    circuit.measure_all()
    _atomic_text(output, str(qiskit.qasm2.dumps(circuit)))
    print(
        json.dumps(
            {
                "status": "success",
                "dependencies": {
                    distribution: version(distribution)
                    for distribution in GENERATOR_DEPENDENCIES
                },
            },
            separators=(",", ":"),
        )
    )


def _set_resource_limits(memory_limit_gib: float, cpu_set: list[int]) -> int | None:
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, set(cpu_set))
        if sorted(os.sched_getaffinity(0)) != sorted(cpu_set):
            raise RuntimeError("operating system did not retain the requested CPU affinity")
    if not sys.platform.startswith("linux"):
        return None
    limit = int(memory_limit_gib * (1 << 30))
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    soft = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
    resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
    return int(resource.getrlimit(resource.RLIMIT_AS)[0])


def _peak_rss_bytes() -> int:
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if sys.platform == "darwin" else peak * 1024


def _dependency_versions(adapter: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for distribution in DEPENDENCIES[adapter]:
        try:
            found[distribution] = version(distribution)
        except PackageNotFoundError:
            continue
    return found


def _configure_clifft_threads(
    clifft: Any, threads: int
) -> tuple[int, dict[str, int], str]:
    """Support both released Clifft and the post-OpenMP sampling API."""
    if hasattr(clifft, "set_num_threads"):
        clifft.set_num_threads(threads)
        return int(clifft.get_num_threads()), {}, "module-setter"
    return threads, {"threads": threads}, "sample-argument"


def _clifft_compile_arguments(clifft: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "hir_passes": clifft.default_hir_pass_manager(),
    }
    if hasattr(clifft, "default_bytecode_pass_manager"):
        arguments["bytecode_passes"] = clifft.default_bytecode_pass_manager()
    return arguments


def _run_clifft(qasm: str, threads: int) -> tuple[dict[str, float], int, dict[str, Any]]:
    import clifft

    from clifft_bench.qv_adapters import to_clifft_stim

    effective, sample_arguments, thread_interface = _configure_clifft_threads(
        clifft, threads
    )
    program_text = to_clifft_stim(qasm)
    started = time.perf_counter()
    program = clifft.compile(
        program_text,
        **_clifft_compile_arguments(clifft),
    )
    compile_seconds = time.perf_counter() - started
    started = time.perf_counter()
    clifft.sample(program, shots=1, **sample_arguments)
    sample_seconds = time.perf_counter() - started
    metadata: dict[str, Any] = {"thread_interface": thread_interface}
    if hasattr(clifft, "max_sim_qubits"):
        metadata["max_sim_qubits"] = int(clifft.max_sim_qubits())
    return (
        {
            "execution_seconds": compile_seconds + sample_seconds,
            "compile_seconds": compile_seconds,
            "sample_seconds": sample_seconds,
        },
        effective,
        metadata,
    )


def _run_qiskit(qasm: str, threads: int) -> tuple[dict[str, float], None, dict[str, Any]]:
    from qiskit.circuit import QuantumCircuit
    from qiskit.compiler import transpile
    from qiskit_aer import AerSimulator

    simulator = AerSimulator(method="statevector", max_parallel_threads=threads)
    circuit = transpile(QuantumCircuit.from_qasm_str(qasm), simulator)
    started = time.perf_counter()
    simulator.run(circuit, shots=1).result()
    elapsed = time.perf_counter() - started
    return ({"execution_seconds": elapsed, "compile_seconds": 0.0, "sample_seconds": 0.0}, None, {})


def _run_qulacs(qasm: str, _threads: int) -> tuple[dict[str, float], None, dict[str, Any]]:
    from qulacs import QuantumState

    from clifft_bench.qv_adapters import to_qulacs_circuit

    circuit, qubits = to_qulacs_circuit(qasm)
    state = QuantumState(qubits)
    started = time.perf_counter()
    circuit.update_quantum_state(state)
    elapsed = time.perf_counter() - started
    return ({"execution_seconds": elapsed, "compile_seconds": 0.0, "sample_seconds": 0.0}, None, {})


def _run_qsim(qasm: str, threads: int) -> tuple[dict[str, float], int, dict[str, Any]]:
    import qsimcirq

    from clifft_bench.qv_adapters import to_cirq_circuit

    circuit = to_cirq_circuit(qasm)
    simulator = qsimcirq.QSimSimulator(qsimcirq.QSimOptions(cpu_threads=threads))
    started = time.perf_counter()
    simulator.run(circuit, repetitions=1)
    elapsed = time.perf_counter() - started
    return (
        {"execution_seconds": elapsed, "compile_seconds": 0.0, "sample_seconds": 0.0},
        threads,
        {},
    )


def _run_qrack(qasm: str, _threads: int) -> tuple[dict[str, float], None, dict[str, Any]]:
    from qiskit.circuit import QuantumCircuit
    from qiskit.providers.qrack import QasmSimulator

    circuit = QuantumCircuit.from_qasm_str(qasm)
    simulator = QasmSimulator(shots=1)
    started = time.perf_counter()
    simulator.run(circuit, shots=1).result()
    elapsed = time.perf_counter() - started
    return ({"execution_seconds": elapsed, "compile_seconds": 0.0, "sample_seconds": 0.0}, None, {})


RUNNERS: dict[str, Callable[[str, int], tuple[dict[str, float], int | None, dict[str, Any]]]] = {
    "clifft": _run_clifft,
    "qiskit": _run_qiskit,
    "qulacs": _run_qulacs,
    "qsim": _run_qsim,
    "qrack": _run_qrack,
}


def _run(args: argparse.Namespace) -> int:
    threads = int(args.threads)
    cpu_set = [int(value) for value in args.cpu_set.split(",")]
    if len(cpu_set) != threads:
        raise ValueError("CPU set length must equal the requested thread count")
    address_space_limit = _set_resource_limits(float(args.memory_limit_gib), cpu_set)
    qasm = args.qasm.read_text()
    timing, effective, metadata = RUNNERS[args.adapter](qasm, threads)
    print(
        json.dumps(
            {
                "status": "success",
                "timing": {**timing, "timed_region": "original-clifft-paper-qv-v1"},
                "threads_effective": effective,
                "peak_rss_bytes": _peak_rss_bytes(),
                "runtime_metadata": {
                    **metadata,
                    "address_space_limit_bytes": address_space_limit,
                },
                "dependencies": _dependency_versions(args.adapter),
            },
            separators=(",", ":"),
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--qubits", required=True, type=int)
    generate.add_argument("--seed", required=True, type=int)
    generate.add_argument("--output", required=True, type=Path)
    run = commands.add_parser("run")
    run.add_argument("--adapter", required=True, choices=sorted(RUNNERS))
    run.add_argument("--qasm", required=True, type=Path)
    run.add_argument("--threads", required=True, type=int)
    run.add_argument("--cpu-set", required=True)
    run.add_argument("--memory-limit-gib", required=True, type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate":
            _generate(args.qubits, args.seed, args.output)
            return 0
        return _run(args)
    except Exception as error:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": {"type": type(error).__name__, "message": str(error)},
                    "peak_rss_bytes": _peak_rss_bytes(),
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
