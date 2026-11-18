"""Convert the pinned fold-transversal models into immutable sampling circuits."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from decimal import Decimal, localcontext
from pathlib import Path

SOURCE_COMMIT = "332bc0481843ebc9718e152e92f5ee10f8f0b148"
MODEL_SHA256 = {
    3: "3a872108fe6874071006d7e05c994b03c325ed3179efcd58c825ab7304bb9368",
    5: "d695e09475aaad6c6cb3506e5772aacb6517103cd4ecde6f2713378fdb128bff",
}


def depolarizing_pauli_probability(probability: float) -> float:
    """Independent probability q for each of the 63 nonidentity three-qubit Paulis.

    Each nontrivial Pauli character has eigenvalue (1-2q)^32 = 1-64p/63,
    preserving the joint distribution: identity 1-p, each nonidentity p/63.
    """
    if not 0 <= probability <= 63 / 64:
        raise ValueError("independent-Pauli decomposition requires p in [0, 63/64]")
    with localcontext() as context:
        context.prec = 60
        p = Decimal(str(probability))
        return float((1 - (1 - 64 * p / 63) ** (Decimal(1) / 32)) / 2)


def controlled_sx(control: int, target: int, *, dagger: bool) -> list[str]:
    """The source gate is controlled S X (or S† X), not controlled sqrt(X)."""
    phase, inverse = ("T_DAG", "T") if dagger else ("T", "T_DAG")
    return [f"CX {control} {target}", f"{phase} {control} {target}",
            f"CX {control} {target}", f"{inverse} {target}", f"CX {control} {target}"]


def circuit_text(model: dict, probability: float) -> str:
    sites = sorted({q for e in model["events"] for q in e.get("support", [])}
                   | {q for c in model["channels"] for q in c["support"]})
    qubits = {q: i for i, q in enumerate(sites)}
    lines, measured = [], 0
    q3 = depolarizing_pauli_probability(probability)
    for event in model["events"]:
        name = event["name"]
        support = [qubits[q] for q in event.get("support", [])]
        targets = " ".join(map(str, support))
        if name == "NOISE":
            if not probability:
                continue
            channel = model["channels"][event["location"]]
            support = [qubits[q] for q in channel["support"]]
            targets = " ".join(map(str, support))
            if channel["kind"] == "flip":
                lines.append(f"{channel.get('axis', 'X')}_ERROR({probability:.17g}) {targets}")
            elif len(support) <= 2:
                lines.append(f"DEPOLARIZE{len(support)}({probability:.17g}) {targets}")
            else:
                for axes in itertools.product("IXYZ", repeat=3):
                    paulis = " ".join(f"{a}{q}" for a, q in zip(axes, support) if a != "I")
                    if paulis:
                        lines.append(f"CORRELATED_ERROR({q3:.17g}) {paulis}")
        elif name == "INJECT_HXY":
            # Full source unitary T H T^3, including its action on a faulty |1> input.
            lines += [f"S {targets}", f"T {targets}", f"H {targets}", f"T {targets}"]
        elif name in {"CSX", "CS_DAG_X"}:
            lines += controlled_sx(*support, dagger=name == "CS_DAG_X")
        elif name == "POST_Z":
            lines += [f"M {targets}", "DETECTOR rec[-1]"]
            measured += 1
        elif name in {"MEASURE", "MEASURE_PRODUCT"}:
            if event["record"] != measured:
                raise ValueError("source measurement records are out of order")
            if name == "MEASURE_PRODUCT":
                lines.append("MPP " + "*".join(f"{event['axis']}{q}" for q in support))
            else:
                basis = "" if event["axis"] == "Z" else event["axis"]
                lines.append(f"M{basis} {'!' if event['inverted'] else ''}{targets}")
            measured += 1
        elif name == "DETECTOR":
            if event["reference_parity"]:
                raise ValueError("source detector requires a nonzero reference")
            lines.append("DETECTOR " + " ".join(
                f"rec[{r - measured}]" for r in event["records"]
            ))
        elif name in {"FEEDBACK_X", "FEEDBACK_Z"}:
            lines.append(f"C{name[-1]} rec[{event['record'] - measured}] {targets}")
        elif name in {"R", "H", "CX", "CCZ", "T", "T_DAG"}:
            lines.append(f"{name} {targets}")
        else:
            raise ValueError(f"unsupported model event: {name}")

    # Ideal logical T† then X readout measures H_XY=(X+Y)/sqrt(2), whose
    # wrong outcome is the source model's logical error. No readout noise is added.
    if "logical" in model:
        logical_x = logical_z = [qubits[model["logical"]]]
    else:
        logical_x = [qubits[int(q)] for q in model["logical_x"]]
        logical_z = [qubits[int(q)] for q in model["logical_z"]]
    anchor, *others = logical_z
    lines += [f"CX {q} {anchor}" for q in others]
    lines.append(f"T_DAG {anchor}")
    lines += [f"CX {q} {anchor}" for q in reversed(others)]
    lines += ["MPP " + "*".join(f"X{q}" for q in logical_x), "OBSERVABLE_INCLUDE(0) rec[-1]"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="exact_ler_for_msc checkout")
    parser.add_argument("--distance", required=True, type=int, choices=(3, 5))
    parser.add_argument("--noise", type=float, choices=(0.0, 0.001), default=0.001)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    path = args.source / f"data/alternatives/inputs/fold-d{args.distance}/model.json"
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != MODEL_SHA256[args.distance]:
        raise ValueError(f"expected the unmodified model from {SOURCE_COMMIT}")
    text = circuit_text(json.loads(content), args.noise)
    args.output.write_text(text)
    print(hashlib.sha256(text.encode()).hexdigest(), args.output)


if __name__ == "__main__":
    main()
