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
    monkeypatch.setattr(system.os, "sched_getaffinity", lambda pid: {0, 1}, raising=False)
    monkeypatch.setattr(system, "cpu_topology", lambda cpus: [
        {"logical_cpu": cpu, "socket": 0, "core": cpu} for cpu in cpus
    ])
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


@pytest.mark.parametrize("threads", [1, 8])
def test_worker_caps_keep_blas_and_xla_serial(monkeypatch, threads):
    for name in ("OMP_NUM_THREADS", "NUMBA_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                 "MKL_NUM_THREADS", "BLIS_NUM_THREADS"):
        monkeypatch.setenv(name, "99")
    environment = system.restricted_environment(threads)
    assert environment["OMP_NUM_THREADS"] == environment["NUMBA_NUM_THREADS"] == str(threads)
    assert all(environment[name] == "1" for name in
               ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "BLIS_NUM_THREADS"))
    assert "intra_op_parallelism_threads=1" in environment["XLA_FLAGS"]


def test_cpu_topology_reads_socket_and_core_ids(monkeypatch):
    values = {
        "/sys/devices/system/cpu/cpu4/topology/physical_package_id": "1\n",
        "/sys/devices/system/cpu/cpu4/topology/core_id": "2\n",
    }
    monkeypatch.setattr(Path, "read_text", lambda path: values[str(path)])
    assert system.cpu_topology([4]) == [{"logical_cpu": 4, "socket": 1, "core": 2}]


def test_missing_topology_is_not_treated_as_distinct_cores(monkeypatch):
    def missing(path):
        raise FileNotFoundError(path)
    monkeypatch.setattr(Path, "read_text", missing)
    with pytest.raises(ValueError, match="cannot discover physical core"):
        system.cpu_topology([4])


def test_affinity_uses_distinct_physical_cores_within_allowed_mask(monkeypatch):
    # Two sockets reuse core IDs. CPUs 0..7 and 8..15 are SMT siblings.
    allowed = set(range(16)) - {0, 3}
    monkeypatch.setattr(system.os, "sched_getaffinity", lambda pid: set(allowed), raising=False)
    monkeypatch.setattr(system, "cpu_topology", lambda cpus: [
        {"logical_cpu": cpu, "socket": (cpu % 8) // 4, "core": cpu % 4}
        for cpu in cpus
    ])
    def set_affinity(pid, cpus):
        allowed.intersection_update(cpus)
    monkeypatch.setattr(system.os, "sched_setaffinity", set_affinity, raising=False)
    result = system.apply_affinity(9, 8)
    assert result["applied"]
    assert result["logical_cpus"][0] == 9
    assert len(allowed) == 8
    assert not ({0, 3} & allowed)
    assert len({(c["socket"], c["core"]) for c in result["physical_cores"]}) == 8
    assert result["available_after"] == sorted(allowed)


def test_single_core_is_subset_of_eight_core_allocation(monkeypatch):
    monkeypatch.setattr(system.os, "sched_getaffinity", lambda pid: set(range(16)), raising=False)
    monkeypatch.setattr(system, "cpu_topology", lambda cpus: [
        {"logical_cpu": cpu, "socket": 0, "core": cpu % 8} for cpu in cpus
    ])
    assert system.choose_cpus(12, 1) == system.choose_cpus(12, 8)[:1]


@pytest.mark.parametrize("anchor,match", [(99, "outside"), (0, "only 4")])
def test_cpu_allocation_rejects_unavailable_resources(monkeypatch, anchor, match):
    monkeypatch.setattr(system.os, "sched_getaffinity", lambda pid: set(range(8)), raising=False)
    monkeypatch.setattr(system, "cpu_topology", lambda cpus: [
        {"logical_cpu": cpu, "socket": 0, "core": cpu % 4} for cpu in cpus
    ])
    with pytest.raises(ValueError, match=match):
        system.choose_cpus(anchor, 8)


def test_unsupported_affinity_does_not_silently_run_eight_threads(monkeypatch):
    monkeypatch.delattr(system.os, "sched_getaffinity", raising=False)
    assert system.apply_affinity(None)["applied"] is False
    with pytest.raises(ValueError, match="requires Linux"):
        system.apply_affinity(None, 8)
