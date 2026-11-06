from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from toml_helpers import write_toml

from clifft_bench import runner, system, worker
from clifft_bench.adapters.clifft import _PreparedClifft
from clifft_bench.adapters.symft import SymftAdapter, _PreparedSymft
from clifft_bench.config import campaign_path, load_suite, read_toml, repository_root
from clifft_bench.report import report_execution
from clifft_bench.results import validate_result, write_json
from clifft_bench.runner import run_suite
from clifft_bench.scaling_report import scaling_rows


def pilot_copy(tmp_path):
    path = campaign_path("thread-scaling")
    run = read_toml(path)
    for key in ("workloads_file", "software_file"):
        run[key] = str((path.parent / run[key]).resolve())
    target = tmp_path / "pilot.toml"
    write_toml(target, run)
    return target, run


def test_campaign_expansion_preserves_work_and_native_policy():
    suite = load_suite(campaign_path("thread-scaling"))
    groups = {}
    for case in suite.cases:
        assert case.definition["execution"]["batch_size"] == "native"
        group = groups.setdefault((case.implementation.id, case.workload.id), [])
        group.append(case)
    for cases in groups.values():
        assert ([c.definition["execution"]["threads"] for c in cases]
                == suite.run["scaling"]["threads"])
        assert len({c.workload.artifact_sha256 for c in cases}) == 1
        assert len({c.definition["shots_per_call"] for c in cases}) == 1
    for name in ("release-v1", "release-clifft", "smoke"):
        assert all(c.definition["execution"].get("threads", 1) == 1
                   for c in load_suite(campaign_path(name)).cases)


@pytest.mark.parametrize("damage", ["budgets", "official", "batch", "call_size", "chunk"])
def test_invalid_scaling_configuration_is_rejected(tmp_path, damage):
    path, run = pilot_copy(tmp_path)
    if damage == "budgets":
        run["scaling"]["threads"] = [1, 2, 2]
    elif damage == "official":
        run["classification"] = "official"
    elif damage == "batch":
        run["variants"][0]["execution"]["batch_size"] = "calibrate"
    elif damage == "call_size":
        run["variants"][1]["workloads"][0]["shots_per_call"] += 1
    else:
        run["variants"][1]["execution"]["options"] = {"sample_chunk_shots": 8}
    write_toml(path, run)
    with pytest.raises(ValueError):
        load_suite(path)


def test_topology_selects_one_allowed_smt_sibling_per_core(monkeypatch):
    monkeypatch.setattr(system.platform, "system", lambda: "Linux")
    monkeypatch.setattr(system, "_allowed_cpus", lambda: [1, 2, 3, 4])
    topology = {1: (0, 1), 2: (0, 1), 3: (0, 2), 4: (1, 1)}

    def read(path):
        cpu = int(path.parent.parent.name.removeprefix("cpu"))
        return str(topology[cpu][0 if path.name == "physical_package_id" else 1])

    monkeypatch.setattr(Path, "read_text", read)
    selected = system.physical_core_allocation()
    assert [c["logical_cpu"] for c in selected] == [1, 3, 4]


def test_native_environment_caps_nested_pools_and_records_affinity(monkeypatch):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "128")
    monkeypatch.setenv("OMP_THREAD_LIMIT", "1")
    env = system.restricted_environment(4)
    assert env["NUMBA_NUM_THREADS"] == env["OMP_NUM_THREADS"] == env["OMP_THREAD_LIMIT"] == "4"
    assert env["OPENBLAS_NUM_THREADS"] == "1"
    masks = []
    monkeypatch.setattr(system.os, "sched_getaffinity", lambda _: {1, 3} if masks else {1, 2, 3},
                        raising=False)
    monkeypatch.setattr(system.os, "sched_setaffinity", lambda _, mask: masks.append(mask),
                        raising=False)
    applied = system.apply_affinity([1, 3])
    assert applied["applied"] and masks == [{1, 3}]
    assert applied["logical_cpus"] == [1, 3]


def test_unavailable_core_budget_is_retained_as_resource_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "physical_core_allocation", lambda: [
        {"logical_cpu": 0, "package_id": 0, "core_id": 0}])
    metadata = system.collect_runner_metadata(repository_root())
    metadata.update(os="Linux", allowed_logical_cpus=[0])
    monkeypatch.setattr(runner, "collect_runner_metadata", lambda *a, **kw: metadata)
    monkeypatch.setattr(runner, "WorkerClient", lambda *a, **kw: pytest.fail("spawned a worker"))
    document = run_suite(load_suite(campaign_path("thread-scaling")),
                         output_dir=tmp_path / "run", case_pattern="--t8$")
    validate_result(document)
    assert all(c["status"] == "error" and c["error"]["type"] == "InsufficientPhysicalCores"
               for c in document["cases"])
    assert all(row["speedup"] is None for row in scaling_rows(document))


def test_clifft_native_tuning_runs_once_and_freezes_batch_and_layout():
    calls = []
    report = SimpleNamespace(
        batch_size=32, baseline_batch_size=1, shot_workers=2, intra_shot_workers=1,
        elapsed_seconds=0.2, trial_shots=100, sufficient_measurements=False,
        stop_reason="insufficient_measurements", trials=[],
    )

    def sample(program, shots, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(batch_tuning=report, total_shots=shots, passed_shots=shots,
                               discards=0, observable_ones=[0])

    prepared = _PreparedClifft(SimpleNamespace(sample_survivors=sample), object(), 0, {}, 1,
                              threads=4, native=True)
    prepared.prepare_sampling(1000, 987)
    prepared.sample(1000, 10)
    prepared.sample(1000, 11)
    assert calls[0]["batch_size"] == "tune" and "thread_layout" not in calls[0]
    assert all(c["batch_size"] == 32 and c["thread_layout"] == (2, 1) for c in calls[1:])
    assert prepared.runtime_metadata["threads"] == 2
    assert prepared.runtime_metadata["native_tuning"]["sufficient_measurements"] is False


def test_symft_observed_threads_can_be_below_the_budget(monkeypatch):
    sampler = SimpleNamespace(sample=lambda **kw: {
        "shots": kw["shots"], "accepted": kw["shots"], "discarded": 0,
        "logical_errors": 0, "active_threads": 1,
    })
    prepared = _PreparedSymft(sampler, {"threads": 8, "batch_control": "native-auto"})
    observed = worker.aggregate_sample(prepared, shots_per_call=8, min_seconds=0.001,
                                      seed=1, postselect=False, max_api_calls=100000)
    assert observed["active_threads_min"] == observed["active_threads_max"] == 1
    assert observed["process_cpu_seconds"] >= 0 and observed["sampling_wall_seconds"] > 0
    sampler.sample = lambda **kw: {"active_threads": 9}
    with pytest.raises(RuntimeError, match="active thread count"):
        prepared.sample(8, 2)


def test_symft_unselected_records_do_not_switch_semantics_for_threads(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "symft", SimpleNamespace(
        Circuit=lambda **kw: SimpleNamespace(num_detectors=1)))
    with pytest.raises(NotImplementedError, match="unselected-record"):
        SymftAdapter().prepare(artifact_path=tmp_path / "unused.stim",
                               workload={"semantics": {"observable_index": 0,
                                                       "postselect_all_detectors": False}},
                               execution={"batch_size": "native", "threads": 2})


@pytest.fixture
def scaling_result(raw_result):
    doc = raw_result
    doc["campaign"].pop("comparisons")
    doc["campaign"]["scaling"] = {"threads": [1, 2]}
    doc["campaign"]["resources"] = {
        "logical_cpu": None, "memory_limit_bytes": 1 << 30,
        "physical_cores": [{"logical_cpu": n, "package_id": 0, "core_id": n} for n in range(2)],
        "smt_policy": "one-logical-cpu-per-core",
    }
    doc["runner"].update(os="Linux", allowed_logical_cpus=[0, 1])
    for threads, (case, contract) in enumerate(zip(doc["cases"], doc["campaign"]["cases"]), 1):
        identity = f"symft--t{threads}"
        contract.update(id=identity, variant_id=identity, implementation_id="symft-native")
        contract["execution"] = {"batch_size": "native", "options": {}, "threads": threads}
        case.update(case_id=identity, variant_id=identity)
        case["simulator"].update(implementation_id="symft-native", adapter="symft",
                                 name="SymFT", version="1.0.0", display_version="1.0.0")
        case["simulator"].pop("source_tag", None)
        case["execution"].update(contract["execution"], threads_requested=threads,
                                  threads_effective=threads, batch_enabled=True,
                                  tuning_policy="native", memory_limit_bytes=1 << 30)
        case["setup"]["affinity"] = {
            "applied": True, "logical_cpus": list(range(threads)),
            "available_after": list(range(threads)),
        }
        case["setup"]["runtime_metadata"].update(
            threads=threads, version="1.0.0", batch_enabled=True,
            effective_batch_size=1, sample_chunk_shots=2048, batch_control="native-auto",
            thread_environment=system.native_thread_environment(threads),
            address_space_limit_bytes=1 << 30, peak_rss_bytes=123456,
        )
        for sample in case["samples"]:
            sample.update(process_cpu_seconds=sample["duration_seconds"] * threads,
                          sampling_wall_seconds=sample["duration_seconds"],
                          active_threads_min=1, active_threads_max=threads)
    validate_result(doc)
    return doc


@pytest.mark.parametrize("damage", ["smt", "affinity", "environment", "memory", "activity",
                                   "call_size", "options", "missing", "budget"])
def test_native_raw_evidence_enforces_resource_and_comparison_contract(scaling_result, damage):
    doc = scaling_result
    case = doc["cases"][1]
    meta = case["setup"]["runtime_metadata"]
    if damage == "smt":
        doc["campaign"]["resources"]["physical_cores"][1]["core_id"] = 0
    elif damage == "affinity":
        case["setup"]["affinity"]["available_after"] = [0]
    elif damage == "environment":
        meta["thread_environment"]["OPENBLAS_NUM_THREADS"] = "8"
    elif damage == "memory":
        meta["address_space_limit_bytes"] *= 2
    elif damage == "activity":
        case["samples"][0]["active_threads_max"] = 3
    elif damage == "call_size":
        doc["cases"][0]["workload"]["artifact"]["sha256"] = "a" * 64
    elif damage == "options":
        case["execution"]["options"] = {"sample_chunk_shots": 32}
        doc["campaign"]["cases"][1]["execution"]["options"] = {"sample_chunk_shots": 32}
    elif damage == "missing":
        doc["cases"].pop()
    else:
        case["execution"]["threads_requested"] = 8
    with pytest.raises(ValueError):
        validate_result(doc)


def test_scaling_ratios_use_same_run_baseline_and_preserve_failures(scaling_result, tmp_path):
    rows = scaling_rows(scaling_result)
    assert rows[0]["speedup"] == 1
    assert rows[1]["speedup"] == 1.25
    assert rows[1]["efficiency"] == 0.625
    assert rows[1]["cpu_cores"] == 2
    case = scaling_result["cases"][0]
    case.update(status="error", error={"phase": "sampling", "type": "WorkerTimeout",
                                       "message": "deadline"})
    assert all(row["speedup"] is None for row in scaling_rows(scaling_result))
    write_json(tmp_path / "raw.json", scaling_result)
    report_execution(tmp_path, check=True)
    assert not (tmp_path / "report").exists()
    pytest.importorskip("matplotlib")
    report_execution(tmp_path)
    assert (tmp_path / "report/thread-scaling.png").read_bytes().startswith(b"\x89PNG")


@pytest.mark.parametrize("adapter", ["clifft", "symft", "tsim"])
@pytest.mark.skipif(sys.platform != "linux", reason="requires enforced Linux affinity and memory")
def test_native_threads_in_fresh_workers(tmp_path, adapter):
    from test_runner import _write_fixture_suite

    if importlib.util.find_spec(adapter) is None:
        pytest.skip(f"{adapter} is not installed")
    if len(system.physical_core_allocation()) < 2:
        pytest.skip("requires two physical cores")
    path, run, _ = _write_fixture_suite(tmp_path)
    implementation = next(i for i in reversed(read_toml(repository_root() / "software.toml")[
        "implementations"]) if i["adapter"] == adapter)
    software = read_toml(tmp_path / "software.toml")
    software["implementations"] = [copy.deepcopy(implementation)]
    write_toml(tmp_path / "software.toml", software)
    workloads = read_toml(tmp_path / "workloads.toml")
    workloads["workloads"][0]["compatible_adapters"] = [adapter]
    write_toml(tmp_path / "workloads.toml", workloads)
    run["scaling"] = {"threads": [1, 2]}
    run["collection"] = {"memory_limit_gib": 12, "run_timeout_minutes": 10}
    run["measurement"].update(setup_timeout_seconds=180, request_timeout_seconds=60)
    run["cases"] = [run["cases"][0]]
    run["cases"][0].update(implementation_id=implementation["id"], shots_per_call=16384,
                            execution={"batch_size": "native", "options": {}})
    write_toml(path, run)
    document = run_suite(load_suite(path), output_dir=tmp_path / "run",
                         python_executable=sys.executable)
    assert all(c["status"] == "success" for c in document["cases"]), json.dumps(document, indent=2)
    for case in document["cases"]:
        assert case["samples"][0]["logical_errors"] == 0
        assert case["setup"]["affinity"]["applied"] is True
    assert len(scaling_rows(document)) == 2
