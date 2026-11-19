"""Convert Cain et al.'s frozen S-state proxy into a T-state sampling circuit."""

from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path

SOURCE_URL = "https://zenodo.org/records/15702016"
SOURCE_SHA256 = {
    5: "afa0656ba71e7737ff41a4c3bcb3301ff3e40717844fbb52b05c15739593723d",
    9: "0172fcfe5ca9b2ede2d65ec572515b6989cbc32afd123b02d7af8cbe81b00353",
}


def circuit_text(source: bytes, distance: int) -> str:
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256[distance]:
        raise ValueError(f"expected unmodified distillation_{distance} from {SOURCE_URL}")
    lines = source.decode("ascii").splitlines()
    # Each rotated patch has 2*d*d-1 sites. A corner data qubit seeds
    # its magic state: patches 16..30 supply the factory, 31 supplies readout.
    patch_size = 2 * distance**2 - 1
    corner = 2 * distance * (distance - 1) + distance // 2
    expected = [patch * patch_size + corner for patch in range(16, 32)]
    replaced = []
    for index, line in enumerate(lines):
        if line.startswith("S "):
            replaced.extend(map(int, line.split()[1:]))
            lines[index] = "T " + line[2:]
    if replaced != expected:
        raise ValueError("source S gates do not match the magic-state injection sites")
    # After terminal H and Z measurements, the last MPP repeats patch 0's
    # logical-X parity. Retain its source detector and expose the same raw bit.
    lines.append("OBSERVABLE_INCLUDE(0) rec[-1]")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Zenodo distillation.zip")
    parser.add_argument("--distance", required=True, type=int, choices=(5, 9))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.source) as archive:
        source = archive.read(f"distillation/distillation_{args.distance}")
    output = circuit_text(source, args.distance).encode("ascii")
    args.output.write_bytes(output)
    print(hashlib.sha256(output).hexdigest(), args.output)


if __name__ == "__main__":
    main()
