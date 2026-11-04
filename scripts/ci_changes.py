"""Route CI to maintained code checks and changed self-contained executions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def classify_changes(paths: list[str]) -> tuple[bool, list[str]]:
    full = False
    executions: set[str] = set()
    for name in paths:
        path = Path(name)
        if path.parts[0] == "results":
            # Only the current self-contained layout has a raw.json. Historical
            # evidence remains untouched; do not add a legacy reader here.
            for directory in path.parents:
                if (directory / "raw.json").is_file():
                    executions.add(directory.as_posix())
                    break
                if directory == Path("results"):
                    break
        elif (
            path.suffix.lower() == ".md"
            or path.is_relative_to("reporting/figures")
            or path.is_relative_to("experiments")
        ):
            continue
        else:
            # Unknown paths run all checks, including deletions and renames.
            full = True
    return full, sorted(executions)


def main(base: str) -> None:
    changed = subprocess.check_output([
        "git", "diff", "--name-only", "--no-renames", "-z", base, "HEAD",
    ])
    paths = [os.fsdecode(path) for path in changed.split(b"\0") if path]
    full, executions = classify_changes(paths)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"full={str(full).lower()}\n")
        output.write(f"test={str(full or bool(executions)).lower()}\n")
        output.write(f"results={json.dumps(executions)}\n")


if __name__ == "__main__":
    main(sys.argv[1])
