from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from clifft_bench import system


def test_run_text_distinguishes_empty_success_from_failure():
    assert system._run_text([sys.executable, "-c", "pass"]) == ""
    assert system._run_text(["command-that-does-not-exist-clifft-bench"]) is None


def test_imdsv2_discovery_uses_token_and_bounded_requests(monkeypatch):
    identity = {
        "instanceId": "i-test",
        "instanceType": "m7a.xlarge",
        "imageId": "ami-test",
        "region": "us-east-1",
        "availabilityZone": "us-east-1a",
    }
    opener = Mock()
    opener.open.side_effect = [
        io.BytesIO(b"token"),
        io.BytesIO(json.dumps(identity).encode()),
        io.BytesIO(b"on-demand"),
    ]
    monkeypatch.setattr(system, "build_opener", lambda *args: opener)
    monkeypatch.setattr(Path, "read_text", lambda self: "boot-test")
    cloud = system.discover_ec2()
    assert cloud["instance_id"] == "i-test"
    assert cloud["boot_id"] == "boot-test"
    requests = opener.open.call_args_list
    assert [c.args[0].method for c in requests] == ["PUT", "GET", "GET"]
    assert all(c.kwargs["timeout"] == 2 for c in requests)
    assert all(
        dict(c.args[0].header_items())["X-aws-ec2-metadata-token"] == "token" for c in requests[1:]
    )


def test_metadata_failure_does_not_fall_back_to_imdsv1(monkeypatch):
    opener = Mock()
    opener.open.side_effect = TimeoutError("metadata deadline")
    monkeypatch.setattr(system, "build_opener", lambda *args: opener)
    with pytest.raises(ValueError, match="IMDSv2"):
        system.discover_ec2()
    assert opener.open.call_count == 1


@pytest.fixture
def reference(monkeypatch):
    monkeypatch.setattr(system, "_git_metadata", lambda root: {"commit": "a" * 40, "dirty": False})
    monkeypatch.setattr(system.platform, "system", lambda: "Linux")
    monkeypatch.setattr(system.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        system.platform,
        "freedesktop_os_release",
        lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
        raising=False,
    )
    monkeypatch.setattr(system, "_memory_bytes", lambda: 16 << 30)
    monkeypatch.setattr(system, "_allowed_cpus", lambda: [0, 1])
    monkeypatch.setattr(
        system, "discover_ec2", lambda: {"instance_type": "m7a.xlarge", "lifecycle": "on-demand"}
    )
    return {
        "provider": "aws",
        "instance_type": "m7a.xlarge",
        "architecture": "x86_64",
        "os_id": "ubuntu",
        "os_version": "24.04",
    }


def test_reference_host_preserves_discovered_identity(reference):
    assert system.require_reference_host(Path("."), reference, 12, 0) == system.discover_ec2()


@pytest.mark.parametrize("damage", ["dirty", "architecture", "os", "memory", "cpu", "spot", "type"])
def test_reference_host_rejects_invalid_collection_conditions(reference, monkeypatch, damage):
    if damage == "dirty":
        monkeypatch.setattr(
            system, "_git_metadata", lambda root: {"commit": "a" * 40, "dirty": True}
        )
    elif damage == "architecture":
        reference["architecture"] = "aarch64"
    elif damage == "os":
        reference["os_version"] = "22.04"
    elif damage == "memory":
        monkeypatch.setattr(system, "_memory_bytes", lambda: 8 << 30)
    elif damage == "cpu":
        monkeypatch.setattr(system, "_allowed_cpus", lambda: [1])
    else:
        monkeypatch.setattr(
            system,
            "discover_ec2",
            lambda: {
                "instance_type": "m7a.xlarge" if damage == "spot" else "m7i.large",
                "lifecycle": "spot" if damage == "spot" else "on-demand",
            },
        )
    with pytest.raises(ValueError):
        system.require_reference_host(Path("."), reference, 12, 0)


def test_ec2_workflow_sanitizes_remote_credentials(reference, monkeypatch):
    monkeypatch.setattr(
        system,
        "_run_text",
        lambda command, root: (
            "https://user:token@example.com/repo.git?secret=1" if "origin" in command else "branch"
        ),
    )
    workflow = system.ec2_workflow(
        Path("."), {"instance_id": "i-test", "image_id": "ami-test"}, "run"
    )
    assert workflow["repository"] == "https://example.com/repo.git"
    assert workflow["sha"] == "a" * 40
    assert workflow["run_id"] == "run"


def test_restricted_environment_overrides_numba_thread_budget(monkeypatch):
    monkeypatch.setenv("NUMBA_NUM_THREADS", "8")
    assert system.restricted_environment()["NUMBA_NUM_THREADS"] == "1"
