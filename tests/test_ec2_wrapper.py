import json
import os
import subprocess
import sys

import pytest

from clifft_bench.schema import repository_root


@pytest.mark.parametrize("condition", ["valid", "not-ec2", "dirty", "existing"])
def test_ec2_wrapper_preserves_guard_deadline_and_overwrite_boundary(tmp_path, condition):
    root = tmp_path / "checkout with spaces"
    binary = root / "bin"
    binary.mkdir(parents=True)
    log = root / "calls.jsonl"
    output = tmp_path / "evidence"
    if condition == "existing":
        output.mkdir()
        (output / "raw.json").write_text("existing evidence")
    recorder = f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TEST_LOG"], "a") as stream:
    stream.write(json.dumps([name, *args]) + "\\n")
if name == "git":
    if args[0] == "rev-parse": print(os.environ["TEST_ROOT"])
    elif os.environ["TEST_CONDITION"] == "dirty": print(" M file")
elif name == "uname": print("Linux")
elif name == "cat": print("Other" if os.environ["TEST_CONDITION"] == "not-ec2" else "Amazon EC2")
'''
    for command in ("git", "uname", "cat", "sudo", "python3.12", "timeout",
                    "../.venv/bin/python", "../.venv/bin/clifft-bench"):
        path = binary / command
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(recorder)
        path.chmod(0o755)
    environment = os.environ | {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "TEST_ROOT": str(root), "TEST_LOG": str(log), "TEST_CONDITION": condition,
    }
    result = subprocess.run([
        "bash", str(repository_root() / "scripts/ec2-run.sh"), "release-v1", str(output),
    ], env=environment, capture_output=True, text=True, timeout=10)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    if condition == "valid":
        assert result.returncode == 0, result.stderr
        assert ["sudo", "shutdown", "-h", "+480"] in calls
        assert ["clifft-bench", "bootstrap", "release-v1"] in calls
        assert calls[-1] == [
            "timeout", "--signal=TERM", "--kill-after=30s", "480m",
            ".venv/bin/clifft-bench", "run", "release-v1", "--require-ec2",
            "--output", str(output / "raw.json"),
        ]
    else:
        assert result.returncode != 0
        assert not any(call[0] == "timeout" for call in calls)
        if condition in ("not-ec2", "dirty"):
            assert not any(call[:2] == ["sudo", "apt-get"] for call in calls)
        if condition == "existing":
            assert (output / "raw.json").read_text() == "existing evidence"
