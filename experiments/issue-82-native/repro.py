"""Direct native API probes for #82; no clifft-bench imports unless requested."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from importlib.metadata import distribution
from pathlib import Path

for name in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMBA_NUM_THREADS",
):
    os.environ[name] = "1"
os.environ["XTIM_QUIET"] = "1"

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parent
TEXT = (ROOT / "circuit.stim").read_text()
FORCED = TEXT.replace("DEPOLARIZE1(0.05) 0 1", "X_ERROR(1) 1")
DETERMINISTIC = "X 0\nM 0\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-1]\n"


def oracle(forced=False):
    """Enumerate Pauli faults using four amplitudes; q0 is the low bit."""
    identity = np.eye(2, dtype=complex)
    x = np.array([[0, 1], [1, 0]], dtype=complex)
    y = np.array([[0, -1j], [1j, 0]])
    z = np.diag([1, -1])
    h = np.array([[1, 1], [1, -1]]) / math.sqrt(2)
    t = np.diag([1, np.exp(1j * math.pi / 4)])
    cx = np.eye(4)[[0, 1, 3, 2]]
    initial = np.kron(identity, h) @ np.array([1, 0, 0, 0])
    unitary = np.kron(identity, h) @ cx @ np.kron(identity, t.conj()) @ cx
    unitary = unitary @ np.kron(identity, t)
    faults = [(identity, 0.95), (x, 1 / 60), (y, 1 / 60), (z, 1 / 60)]
    pairs = [((identity, 1), (x, 1))] if forced else itertools.product(faults, repeat=2)
    probabilities = np.zeros(4)
    for (p0, w0), (p1, w1) in pairs:
        state = unitary @ np.kron(p1, p0) @ initial
        probabilities += w0 * w1 * np.abs(state) ** 2
    expected = [0, 0, 0.5, 0.5] if forced else [841 / 900, 29 / 900, 1 / 60, 1 / 60]
    np.testing.assert_allclose(probabilities, expected, atol=1e-14)
    return expected


def count_joint(detectors, observables):
    codes = 2 * np.asarray(detectors, dtype=np.int64) + np.asarray(observables, dtype=np.int64)
    return np.bincount(codes, minlength=4).tolist()


def checks(joint, expected):
    n = sum(joint)
    z_scores = []
    passes = []
    for actual, p in zip(joint, expected, strict=True):
        sigma = math.sqrt(n * p * (1 - p))
        z_scores.append((actual - n * p) / sigma if sigma else None)
        passes.append(abs(actual - n * p) <= 6 * sigma + 1)
    return {"six_sigma_pass": all(passes), "joint_z_scores": z_scores}


def aggregate(joint, postselect):
    n = sum(joint)
    kept = joint[0] + joint[1] if postselect else n
    ones = joint[1] if postselect else joint[1] + joint[3]
    return {
        "attempted_shots": n,
        "accepted_shots": kept,
        "discarded_shots": n - kept,
        "logical_errors": ones,
    }


def native_counts(result):
    return {
        "attempted_shots": int(result["shots"]),
        "accepted_shots": int(result["accepted"]),
        "discarded_shots": int(result["discarded"]),
        "logical_errors": int(result["logical_errors"]),
    }


def adapter_counts(tool, path, size, postselect, shots, seed):
    from clifft_bench.adapters import load_adapter

    prepared = load_adapter(tool).prepare(
        artifact_path=path,
        workload={
            "semantics": {
                "reference_convention": "raw-record-parity",
                "observable_index": 0,
                "postselect_all_detectors": postselect,
            }
        },
        execution={
            "batch_enabled": size > 1,
            "batch_size": size,
            "sample_chunk_shots": 2048 if tool == "symft" else 0,
        },
        seed=seed,
    )
    prepared.begin_sample(seed)
    result = prepared.sample(shots, seed).as_dict()
    result.pop("adapter_timing", None)
    return result


def symft_rows(args):
    import symft

    for size in [1, 32]:
        for postselect in [False, True]:
            sampler = symft.Circuit(DETERMINISTIC).compile_counts_sampler(
                batch=size > 1,
                batch_size=size,
                postselect_detectors=postselect,
                threads=1,
            )
            yield {
                "tool": "symft",
                "case": "deterministic-detector",
                "batch_size": size,
                "postselect_detectors": postselect,
                "native_counts": native_counts(sampler.sample(shots=8, stream_id=74)),
            }
        for seed in args.seeds:
            records = symft.Circuit(TEXT).sample(
                shots=args.shots,
                seed=seed,
                batch=size > 1,
                batch_size=size,
                sample_chunk_shots=2048,
            )
            joint = count_joint(records[:, 0], records[:, 1])
            yield {
                "tool": "symft",
                "case": "raw-measurements",
                "batch_size": size,
                "seed": seed,
                "joint": joint,
                **checks(joint, oracle()),
            }
            for postselect in [False, True]:
                sampler = symft.Circuit(TEXT).compile_counts_sampler(
                    batch=size > 1,
                    batch_size=size,
                    observable=0,
                    postselect_detectors=postselect,
                    sample_chunk_shots=2048,
                    threads=1,
                    cuda=False,
                )
                counts = native_counts(sampler.sample(shots=args.shots, stream_id=seed))
                row = {
                    "tool": "symft",
                    "case": "aggregate",
                    "seed": seed,
                    "batch_size": size,
                    "postselect_detectors": postselect,
                    "native_counts": counts,
                }
                if args.with_adapters:
                    adapted = adapter_counts(
                        "symft", ROOT / "circuit.stim", size, postselect, args.shots, seed
                    )
                    row.update(adapter_counts=adapted, adapter_matches_native=adapted == counts)
                    assert adapted == counts, row
                yield row


def xtim_sample(text, shots, chunk, seed, selfcheck=0):
    import xtim

    # The old release defaults skip_refused_observables to False; 3.2.2 removes it.
    sampler = xtim.compile_twirl_sampler(text, selfcheck=selfcheck, disk_cache=False)
    joint = np.zeros(4, dtype=np.int64)
    first_bits = None
    repeated = True
    calls = 0
    for offset in range(0, shots, chunk):
        n = min(chunk, shots - offset)
        detectors, observables = sampler.sample(n, seed=seed if offset == 0 else None)
        assert detectors.shape == observables.shape == (n, 1)
        d, o = detectors[:, 0] & 1, observables[:, 0] & 1
        joint += np.bincount(2 * d + o, minlength=4)
        if first_bits is None:
            first_bits = o.copy()
        else:
            repeated = repeated and np.array_equal(o, first_bits[:n])
        calls += 1
    return {
        "joint": joint.tolist(),
        "report": (
            sampler.record_report()
            if hasattr(sampler, "record_report")
            else sampler.channel_report()
        ),
        "first_observable_bits": first_bits[:32].tolist(),
        "calls": calls,
        "all_chunks_repeat_first": repeated if calls > 1 else None,
    }


def xtim_rows(args):
    import xtim

    for seed in args.seeds:
        for chunk in sorted({1, 32, 2048, args.shots}):
            result = xtim_sample(TEXT, args.shots, chunk, seed)
            row = {
                "tool": "xtim",
                "case": "noisy",
                "seed": seed,
                "chunk": chunk,
                **result,
                **checks(result["joint"], oracle()),
            }
            if args.with_adapters:
                comparisons = []
                for postselect in [False, True]:
                    adapted = adapter_counts(
                        "xtim", ROOT / "circuit.stim", chunk, postselect, args.shots, seed
                    )
                    native = aggregate(result["joint"], postselect)
                    comparisons.append(
                        {
                            "postselect": postselect,
                            "native_counts": native,
                            "adapter_counts": adapted,
                            "adapter_matches_native": adapted == native,
                        }
                    )
                    assert adapted == native, comparisons[-1]
                row["adapter_comparisons"] = comparisons
            yield row
        for selfcheck in [0, 2000]:
            for chunk in [1, 32, 4096]:
                result = xtim_sample(FORCED, 4096, chunk, seed, selfcheck)
                yield {
                    "tool": "xtim",
                    "case": "forced-X1",
                    "seed": seed,
                    "chunk": chunk,
                    "selfcheck": selfcheck,
                    **result,
                    **checks(result["joint"], oracle(forced=True)),
                }
        # Independent native exact engine, using a private cache scoped to this run.
        records = xtim.Circuit(TEXT).compile_sampler(seed=seed).sample(args.shots)
        joint = count_joint(records[:, 0], records[:, 1])
        yield {
            "tool": "xtim",
            "case": "exact-measurements",
            "seed": seed,
            "joint": joint,
            **checks(joint, oracle()),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", choices=["symft", "xtim", "both"], default="both")
    parser.add_argument("--shots", type=int, default=131072)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 42, 74])
    parser.add_argument("--with-adapters", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.shots <= 0:
        parser.error("--shots must be positive")
    tools = ["symft", "xtim"] if args.tool == "both" else [args.tool]
    packages = {}
    for tool in tools:
        dist = distribution(tool)
        packages[tool] = {
            "version": dist.version,
            "direct_url": json.loads(dist.read_text("direct_url.json") or "{}"),
        }
    evidence = {
        "complete": False,
        "classification": "correctness-diagnostic-not-throughput",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "packages": packages,
        "shots": args.shots,
        "seeds": args.seeds,
        "with_adapters": args.with_adapters,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ["repro.py", "circuit.stim"]
        },
        "joint_order": ["D0O0", "D0O1", "D1O0", "D1O1"],
        "expected_joint": oracle(),
        "expected_forced_joint": oracle(forced=True),
        "rows": [],
    }
    if args.with_adapters:
        adapter_root = ROOT.parents[1] / "src" / "clifft_bench" / "adapters"
        evidence["adapter_source_sha256"] = {
            name: hashlib.sha256((adapter_root / name).read_bytes()).hexdigest()
            for name in ["_shared.py", "base.py", "symft.py", "xtim.py"]
        }
    # Exact xtim sampler writes plans; avoid the user's shared cache.
    with tempfile.TemporaryDirectory(prefix="issue-82-native-") as cache:
        if "xtim" in tools:
            import xtim

            xtim.cache_dir = cache
        for tool in tools:
            for row in symft_rows(args) if tool == "symft" else xtim_rows(args):
                evidence["rows"].append(row)
                print(json.dumps(row), flush=True)
                if args.output:
                    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    evidence["complete"] = True
    if args.output:
        args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    else:
        print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
