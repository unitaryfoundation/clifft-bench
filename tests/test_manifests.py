import copy
import json
import re
from pathlib import Path

import pytest

from clifft_bench.calibration import BATCH_CALIBRATION_CANDIDATES
from clifft_bench.manifest import load_suite
from clifft_bench.schema import SchemaValidationError, repository_root

ROOT = repository_root()


SMOKE_MANIFESTS = sorted(ROOT.glob("manifests/run-*.v1.json"))
assert SMOKE_MANIFESTS, "no checked-in smoke manifests discovered"
RUN_MANIFESTS = sorted([
    *SMOKE_MANIFESTS,
    *ROOT.glob("campaigns/*/run.v1.json"),
])
assert RUN_MANIFESTS, "no checked-in run manifests discovered"


@pytest.mark.parametrize("path", RUN_MANIFESTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_checked_in_suites_resolve_artifacts_references_and_environments(path: Path) -> None:
    # Loading checks digests, detector masks, adapter compatibility, comparison
    # references and matched call sizes; official runs also require environment locks.
    suite = load_suite(path)
    assert suite.cases
    assert len({case.id for case in suite.cases}) == len(suite.cases)

    if "variants" in suite.run:
        variants = {variant["id"]: variant for variant in suite.run["variants"]}
        assert {
            (case.definition["variant_id"], case.workload.id, case.definition["shots_per_call"])
            for case in suite.cases
        } == {
            (variant["id"], workload["workload_id"], workload["shots_per_call"])
            for variant in variants.values() for workload in variant["workloads"]
        }
        for case in suite.cases:
            variant = variants[case.definition["variant_id"]]
            assert case.implementation.id == variant["implementation_id"]
            assert case.definition["execution"] == variant["execution"]

    for comparison in suite.run.get("comparisons", []):
        baseline = {case.workload.id: case for case in suite.cases
                    if case.definition["variant_id"] == comparison["baseline_variant"]}
        for variant in comparison["candidate_variants"]:
            candidate = {case.workload.id: case for case in suite.cases
                         if case.definition["variant_id"] == variant}
            shared = baseline.keys() & candidate.keys()
            assert shared, f"{comparison['id']}: {variant} has no baseline workloads"


def test_release_compares_distinct_clifft_implementations() -> None:
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    comparison = next(c for c in suite.run["comparisons"] if c["id"] == "current-vs-previous")
    variants = {variant["id"]: variant for variant in suite.run["variants"]}
    implementations = {case.implementation.id: case.implementation for case in suite.cases}
    baseline = implementations[variants[comparison["baseline_variant"]]["implementation_id"]]
    assert baseline.definition["adapter"] == "clifft"
    for variant in comparison["candidate_variants"]:
        candidate = implementations[variants[variant]["implementation_id"]]
        assert candidate.definition["adapter"] == "clifft"
        assert candidate.id != baseline.id
        identity_fields = ("version", "commit_sha")
        assert tuple(candidate.definition[k] for k in identity_fields) != tuple(
            baseline.definition[k] for k in identity_fields
        )


def test_release_current_variants_share_one_implementation_per_adapter() -> None:
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    comparison = next(c for c in suite.run["comparisons"] if c["id"] == "current-vs-previous")
    current: dict[str, set[str]] = {}
    for case in suite.cases:
        if case.definition["variant_id"] == comparison["baseline_variant"]:
            continue
        adapter = case.implementation.definition["adapter"]
        current.setdefault(adapter, set()).add(case.implementation.id)
    assert current
    for adapter, implementations in current.items():
        assert len(implementations) == 1, f"{adapter}: current variants disagree: {implementations}"


def test_release_bulk_calls_exceed_their_short_call_siblings() -> None:
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    variants = {variant["id"]: variant for variant in suite.run["variants"]}
    bulk = [variant for variant in variants.values() if "-bulk" in variant["id"]]
    assert bulk
    for variant in bulk:
        short_id = variant["id"].replace("-bulk", "-current")
        assert short_id in variants, f"{variant['id']}: missing short-call sibling {short_id}"
        short = {w["workload_id"]: w["shots_per_call"] for w in variants[short_id]["workloads"]}
        for workload in variant["workloads"]:
            workload_id = workload["workload_id"]
            assert workload_id in short, f"{variant['id']}: {workload_id} missing from {short_id}"
            assert workload["shots_per_call"] > short[workload_id], (variant["id"], workload_id)
        implementation = next(c.implementation for c in suite.cases
                              if c.implementation.id == variant["implementation_id"])
        if implementation.definition["adapter"] == "tsim":
            assert variant["execution"].get("tsim_trajectory_sharing", True) is True


@pytest.mark.parametrize("path", SMOKE_MANIFESTS, ids=lambda p: p.name)
def test_smoke_and_pilot_implementations_match_the_release(path: Path) -> None:
    release = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    variants = {variant["id"]: variant for variant in release.run["variants"]}
    smoke = load_suite(path)
    for case in smoke.cases:
        variant_id = case.definition["variant_id"]
        assert variant_id in variants, f"{path.name}: {variant_id} is absent from the release"
        variant = variants[variant_id]
        assert case.implementation.id == variant["implementation_id"]


@pytest.mark.parametrize("adapter", ["clifft", "symft"])
def test_smoke_suite_exercises_full_batch_calibration_sweep(adapter: str) -> None:
    smoke = load_suite(ROOT / "manifests/run-smoke.v1.json")
    calibrated = [
        case
        for case in smoke.cases
        if case.implementation.definition["adapter"] == adapter
        and case.definition["execution"]["mode"] == "throughput"
        and case.definition["execution"]["batch_size"] == "calibrate"
    ]

    assert calibrated
    assert any(
        case.definition["shots_per_call"] >= max(BATCH_CALIBRATION_CANDIDATES)
        for case in calibrated
    )


def test_batch_calibration_requires_batch_enabled_throughput_case(tmp_path: Path) -> None:
    suite = load_suite(ROOT / "manifests/run-smoke.v1.json")
    run = copy.deepcopy(suite.run)
    target = next(
        case
        for case in run["cases"]
        if case["execution"]["batch_size"] == "calibrate"
    )
    target["execution"]["batch_enabled"] = False
    run["workloads_manifest"] = str(suite.workloads_path)
    run["software_manifest"] = str(suite.software_path)
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="batching disabled"):
        load_suite(run_path)


@pytest.mark.parametrize("change, message", [
    ("shared-python", "share python executable variable"),
    ("missing-lock", "requirements do not exist"),
    ("missing-environment", "no install environment"),
])
def test_official_implementations_require_usable_isolated_environments(
    tmp_path: Path, change: str, message: str,
) -> None:
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    software = copy.deepcopy(suite.software_document)
    implementations = {item["id"]: item for item in software["implementations"]}
    variants = {variant["id"]: variant for variant in suite.run["variants"]}
    previous = implementations[variants["clifft-previous"]["implementation_id"]]
    current = implementations[variants["clifft-current"]["implementation_id"]]
    if change == "shared-python":
        previous["python_executable_env"] = current["python_executable_env"]
    elif change == "missing-lock":
        current["environment"]["requirements"] = str(tmp_path / "missing.txt")
    else:
        del current["environment"]
    for implementation in implementations.values():
        environment = implementation.get("environment")
        if environment is not None:
            environment["requirements"] = str(
                (suite.software_path.parent / environment["requirements"]).resolve()
            )
    software_path = tmp_path / "software.json"
    software_path.write_text(json.dumps(software))

    run = copy.deepcopy(suite.run)
    run["workloads_manifest"] = str(suite.workloads_path)
    run["software_manifest"] = str(software_path)
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match=message):
        load_suite(run_path)


def test_environment_locks_pin_every_requirement() -> None:
    for requirements_path in sorted((ROOT / "environments").glob("*.txt")):
        requirements = [
            line.strip()
            for line in requirements_path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert requirements
        for requirement in requirements:
            assert "==" in requirement or " @ git+https://" in requirement
            if " @ git+https://" in requirement:
                assert re.search(r"@[0-9a-f]{40}(?:#|$)", requirement)


def _suite_with_local_workloads(tmp_path: Path):
    suite = load_suite(ROOT / "manifests/run-smoke.v1.json")
    workloads = copy.deepcopy(suite.workloads_document)
    for workload in workloads["workloads"]:
        workload["artifact"]["path"] = str(
            suite.workloads_path.parent / workload["artifact"]["path"]
        )
    workloads_path = tmp_path / "workloads.json"
    workloads_path.write_text(json.dumps(workloads))
    run = copy.deepcopy(suite.run)
    run["workloads_manifest"] = "workloads.json"
    run["software_manifest"] = str(suite.software_path)
    return suite, workloads, workloads_path, run


@pytest.mark.parametrize("field", ["workload_id", "implementation_id"])
def test_unknown_case_references_are_rejected(tmp_path: Path, field: str) -> None:
    _, _, _, run = _suite_with_local_workloads(tmp_path)
    run["cases"][0][field] = "unknown-reference"
    path = tmp_path / "run.json"
    path.write_text(json.dumps(run))
    with pytest.raises(SchemaValidationError, match="references unknown"):
        load_suite(path)


def test_artifact_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    suite, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workloads["workloads"][0]["artifact"] = {
        "path": str(tmp_path / "changed.stim"),
        "sha256": workloads["workloads"][0]["artifact"]["sha256"],
    }
    (tmp_path / "changed.stim").write_text("M 0\n")
    workloads_path.write_text(json.dumps(workloads))
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="SHA-256 mismatch"):
        load_suite(run_path)


def test_incompatible_case_is_rejected_before_execution(tmp_path: Path) -> None:
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workloads["workloads"][0]["compatible_adapters"] = ["clifft"]
    workloads_path.write_text(json.dumps(workloads))
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="forces incomparable adapter"):
        load_suite(run_path)


def test_out_of_range_observable_is_rejected_before_execution(tmp_path: Path) -> None:
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workloads["workloads"][0]["semantics"]["observable_index"] = (
        workloads["workloads"][0]["expected_metadata"]["num_observables"]
    )
    workloads_path.write_text(json.dumps(workloads))
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))

    with pytest.raises(SchemaValidationError, match="observable index .* is outside"):
        load_suite(run_path)


@pytest.mark.parametrize("change", ["range", "order", "all", "adapter"])
def test_invalid_detector_subset_is_rejected_before_execution(tmp_path, change):
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workload = next(w for w in workloads["workloads"]
                    if "postselection_detector_indices" in w["semantics"])
    semantics = workload["semantics"]
    if change == "range":
        semantics["postselection_detector_indices"] = [
            workload["expected_metadata"]["num_detectors"]
        ]
    elif change == "order":
        semantics["postselection_detector_indices"] = [1, 0]
    elif change == "all":
        semantics["postselect_all_detectors"] = True
    else:
        workload["compatible_adapters"].append("symft")
    workloads_path.write_text(json.dumps(workloads))
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run))
    with pytest.raises(SchemaValidationError, match="indices|select all|detector-subset"):
        load_suite(run_path)


@pytest.mark.parametrize("change, message", [
    ("unknown", "references unknown variants"),
    ("self", "repeats its baseline"),
])
def test_comparisons_require_distinct_existing_variants(tmp_path, change, message):
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    run = copy.deepcopy(suite.run)
    comparison = run["comparisons"][0]
    comparison["candidate_variants"] = [
        "unknown-variant" if change == "unknown" else comparison["baseline_variant"]
    ]
    run["workloads_manifest"] = str(suite.workloads_path)
    run["software_manifest"] = str(suite.software_path)
    path = tmp_path / "run.json"
    path.write_text(json.dumps(run))
    with pytest.raises(SchemaValidationError, match=message):
        load_suite(path)


def test_comparison_rejects_unequal_public_call_sizes(tmp_path):
    suite = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    run = copy.deepcopy(suite.run)
    comparison = run["comparisons"][0]
    variant = next(v for v in run["variants"] if v["id"] == comparison["candidate_variants"][0])
    baseline = next(v for v in run["variants"] if v["id"] == comparison["baseline_variant"])
    baseline_workloads = {w["workload_id"] for w in baseline["workloads"]}
    workload = next(w for w in variant["workloads"] if w["workload_id"] in baseline_workloads)
    workload["shots_per_call"] += 1
    run["workloads_manifest"] = str(suite.workloads_path)
    run["software_manifest"] = str(suite.software_path)
    path = tmp_path / "mismatch.json"
    path.write_text(json.dumps(run))
    with pytest.raises(SchemaValidationError, match="unequal shots_per_call"):
        load_suite(path)


def test_tsim_pilot_reuses_exact_release_cases():
    release = load_suite(ROOT / "campaigns/release-v1/run.v1.json")
    pilot = load_suite(ROOT / "manifests/run-tsim-pilot.v1.json")
    expected = {case.id: case.definition for case in release.cases}
    assert pilot.run["classification"] == "smoke"
    assert pilot.cases
    assert all(case.definition == expected[case.id] for case in pilot.cases)
