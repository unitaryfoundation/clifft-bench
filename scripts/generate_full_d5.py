"""Reproduce the immutable full-cultivation input from a pinned public checkout.

Run in the generator environment documented in workloads/coverage.md. The
benchmark itself reads the checked-in input and has no generator dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

SOURCE_COMMIT = "db7dc9f13a2c2854690e92390c779048a1ac1400"
ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="clifft-paper checkout")
    parser.add_argument("--noise", type=float, choices=(0.0, 0.001), default=0.001)
    parser.add_argument("--output", type=Path, help="optional generated circuit destination")
    args = parser.parse_args()
    source = args.source.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != SOURCE_COMMIT:
        raise ValueError(f"expected source revision {SOURCE_COMMIT}, got {commit}")
    subprocess.run(["git", "-C", str(source), "diff", "--exit-code", "HEAD", "--",
                    "magic_state_cultivation"], check=True, stdout=subprocess.DEVNULL)
    for package, expected in (("stim", "1.16.0"), ("numpy", "2.5.2")):
        if version(package) != expected:
            raise ValueError(f"generation requires {package}=={expected}")
    generator = source / "magic_state_cultivation"
    sys.path[:0] = [str(generator), str(generator / "vendor/src")]
    import stim
    from convert_s_to_t import make_d5_end2end

    text = make_d5_end2end(args.noise) + "\n"
    if re.search(r"^EXP_VAL\b", text, re.MULTILINE):
        raise ValueError("sampling input must not contain expectation probes")
    # Annotation indexing only. This substitution is NOT a physics oracle.
    layout = stim.Circuit(re.sub(r"^(T_DAG|T)\b", "I", text, flags=re.MULTILINE))
    indices = [d for d, coords in layout.get_detector_coordinates().items()
               if len(coords) <= 4 or coords[4] == -9]
    digest = hashlib.sha256(text.encode()).hexdigest()
    if args.noise == 0.001:
        manifest = json.loads((ROOT / "manifests/workloads.v1.json").read_text())
        workload = next(w for w in manifest["workloads"]
                        if w["id"] == "msc-full-d5-cultivation-selected-p1e-3")
        if digest != workload["artifact"]["sha256"]:
            raise ValueError("generated circuit differs from the immutable input")
        if indices != workload["semantics"]["postselection_detector_indices"]:
            raise ValueError("generated selection differs from the declared mask")
    if args.output:
        args.output.write_text(text)
    print(json.dumps({"sha256": digest, "postselection_detector_indices": indices,
                      **{key: getattr(layout, key) for key in (
                          "num_qubits", "num_measurements", "num_detectors", "num_observables"
                      )}}, indent=2))


if __name__ == "__main__":
    main()
