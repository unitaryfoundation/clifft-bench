import copy
import re
from pathlib import Path

import pytest
from toml_helpers import write_toml

from clifft_bench.calibration import BATCH_CALIBRATION_CANDIDATES
from clifft_bench.cli import main
from clifft_bench.config import load_suite, read_toml, repository_root

ROOT = repository_root()


SMOKE_MANIFESTS = sorted(ROOT.glob("campaigns/*smoke.toml"))
assert SMOKE_MANIFESTS, "no checked-in smoke manifests discovered"
RUN_MANIFESTS = sorted([
    *SMOKE_MANIFESTS,
    *ROOT.glob("campaigns/release-*.toml"), ROOT / "campaigns/tsim-pilot.toml",
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
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
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
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
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
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
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
            assert variant["execution"]["options"].get("trajectory_sharing", True) is True


@pytest.mark.parametrize("path", SMOKE_MANIFESTS, ids=lambda p: p.name)
def test_smoke_and_pilot_implementations_match_the_release(path: Path) -> None:
    release = load_suite(ROOT / "campaigns/release-v1.toml")
    variants = {variant["id"]: variant for variant in release.run["variants"]}
    smoke = load_suite(path)
    for case in smoke.cases:
        variant_id = case.definition["variant_id"]
        assert variant_id in variants, f"{path.name}: {variant_id} is absent from the release"
        variant = variants[variant_id]
        assert case.implementation.id == variant["implementation_id"]


@pytest.mark.parametrize("adapter", ["clifft", "symft"])
def test_smoke_suite_exercises_full_batch_calibration_sweep(adapter: str) -> None:
    smoke = load_suite(ROOT / "campaigns/smoke.toml")
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
    suite = load_suite(ROOT / "campaigns/smoke.toml")
    run = copy.deepcopy(suite.run)
    target = next(
        case
        for case in run["cases"]
        if case["execution"]["batch_size"] == "calibrate"
    )
    target["execution"]["batch_enabled"] = False
    run["workloads_file"] = str(suite.workloads_path)
    run["software_file"] = str(suite.software_path)
    run_path = tmp_path / "run.toml"
    write_toml(run_path, run)

    with pytest.raises(ValueError, match="batching disabled"):
        load_suite(run_path)


def test_official_implementations_require_environment_locks(tmp_path):
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    for implementation in {c.implementation.id: c.implementation for c in suite.cases}.values():
        assert implementation.requirements_path.is_file()
        assert implementation.environment_path == ROOT / ".campaign-envs" / implementation.id
        assert implementation.python_executable() == str(
            implementation.environment_path / "bin/python"
        )
    software_path = tmp_path / "software.toml"
    write_toml(software_path, suite.software_document)
    run = copy.deepcopy(suite.run)
    run["software_file"] = str(software_path)
    run["workloads_file"] = str(suite.workloads_path)
    path = tmp_path / "run.toml"
    write_toml(path, run)
    with pytest.raises(ValueError, match="requirements do not exist"):
        load_suite(path)


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
    suite = load_suite(ROOT / "campaigns/smoke.toml")
    workloads = copy.deepcopy(suite.workloads_document)
    for workload in workloads["workloads"]:
        workload["artifact"]["path"] = str(
            suite.workloads_path.parent / workload["artifact"]["path"]
        )
    workloads_path = tmp_path / "workloads.toml"
    write_toml(workloads_path, workloads)
    run = copy.deepcopy(suite.run)
    run["workloads_file"] = "workloads.toml"
    run["software_file"] = str(suite.software_path)
    return suite, workloads, workloads_path, run


@pytest.mark.parametrize("field", ["workload_id", "implementation_id"])
def test_unknown_case_references_are_rejected(tmp_path: Path, field: str) -> None:
    _, _, _, run = _suite_with_local_workloads(tmp_path)
    run["cases"][0][field] = "unknown-reference"
    path = tmp_path / "run.toml"
    write_toml(path, run)
    with pytest.raises(ValueError, match="references unknown"):
        load_suite(path)


def test_artifact_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    suite, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workloads["workloads"][0]["artifact"] = {
        "path": str(tmp_path / "changed.stim"),
        "sha256": workloads["workloads"][0]["artifact"]["sha256"],
    }
    (tmp_path / "changed.stim").write_text("M 0\n")
    write_toml(workloads_path, workloads)
    run_path = tmp_path / "run.toml"
    write_toml(run_path, run)

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_suite(run_path)


def test_incompatible_case_is_rejected_before_execution(tmp_path: Path) -> None:
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workloads["workloads"][0]["compatible_adapters"] = ["clifft"]
    write_toml(workloads_path, workloads)
    run_path = tmp_path / "run.toml"
    write_toml(run_path, run)

    with pytest.raises(ValueError, match="forces incomparable adapter"):
        load_suite(run_path)


def test_out_of_range_observable_is_rejected_before_execution(tmp_path: Path) -> None:
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    workloads["workloads"][0]["semantics"]["observable_index"] = (
        workloads["workloads"][0]["expected_metadata"]["num_observables"]
    )
    write_toml(workloads_path, workloads)
    run_path = tmp_path / "run.toml"
    write_toml(run_path, run)

    with pytest.raises(ValueError, match="observable index .* is outside"):
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
    write_toml(workloads_path, workloads)
    run_path = tmp_path / "run.toml"
    write_toml(run_path, run)
    with pytest.raises(ValueError, match="indices|select all|detector-subset"):
        load_suite(run_path)


@pytest.mark.parametrize("change, message", [
    ("unknown", "references unknown variants"),
    ("self", "repeats its baseline"),
])
def test_comparisons_require_distinct_existing_variants(tmp_path, change, message):
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    run = copy.deepcopy(suite.run)
    comparison = run["comparisons"][0]
    comparison["candidate_variants"] = [
        "unknown-variant" if change == "unknown" else comparison["baseline_variant"]
    ]
    run["workloads_file"] = str(suite.workloads_path)
    run["software_file"] = str(suite.software_path)
    path = tmp_path / "run.toml"
    write_toml(path, run)
    with pytest.raises(ValueError, match=message):
        load_suite(path)


def test_comparison_rejects_unequal_public_call_sizes(tmp_path):
    suite = load_suite(ROOT / "campaigns/release-v1.toml")
    run = copy.deepcopy(suite.run)
    comparison = run["comparisons"][0]
    variant = next(v for v in run["variants"] if v["id"] == comparison["candidate_variants"][0])
    baseline = next(v for v in run["variants"] if v["id"] == comparison["baseline_variant"])
    baseline_workloads = {w["workload_id"] for w in baseline["workloads"]}
    workload = next(w for w in variant["workloads"] if w["workload_id"] in baseline_workloads)
    workload["shots_per_call"] += 1
    run["workloads_file"] = str(suite.workloads_path)
    run["software_file"] = str(suite.software_path)
    path = tmp_path / "mismatch.toml"
    write_toml(path, run)
    with pytest.raises(ValueError, match="unequal shots_per_call"):
        load_suite(path)


def test_tsim_pilot_reuses_exact_release_cases():
    release = load_suite(ROOT / "campaigns/release-v1.toml")
    pilot = load_suite(ROOT / "campaigns/tsim-pilot.toml")
    expected = {case.id: case.definition for case in release.cases}
    assert pilot.run["classification"] == "smoke"
    assert pilot.cases
    assert all(case.definition == expected[case.id] for case in pilot.cases)


@pytest.mark.parametrize("damage", ["unknown", "type", "nonfinite", "duplicate"])
def test_campaign_rejects_malformed_inputs(tmp_path, damage):
    from clifft_bench.config import read_toml

    campaign = read_toml(ROOT / "campaigns/smoke.toml")
    campaign["workloads_file"] = str(ROOT / "workloads.toml")
    campaign["software_file"] = str(ROOT / "software.toml")
    if damage == "unknown":
        campaign["measurement"]["typo"] = 10
    elif damage == "type":
        campaign["measurement"]["repetitions"] = True
    elif damage == "nonfinite":
        campaign["measurement"]["min_sample_seconds"] = float("inf")
    else:
        campaign["cases"].append(copy.deepcopy(campaign["cases"][0]))
    path = tmp_path / "malformed.toml"
    write_toml(path, campaign)
    with pytest.raises(ValueError):
        load_suite(path)


def test_listing_does_not_import_simulator_packages():
    import subprocess
    import sys

    result = subprocess.run([
        sys.executable, "-c",
        "import sys; from clifft_bench.cli import main; "
        "assert main(['list', 'release-v1', '--json']) == 0; "
        "assert not {'clifft', 'symft', 'stim', 'xtim', 'tsim'} & sys.modules.keys()",
    ], capture_output=True, text=True, check=True, timeout=10)
    assert '"case_id"' in result.stdout


@pytest.mark.parametrize("call_size", [17, 31])
def test_groups_preserve_order_and_propagate_defaults_with_explicit_overrides(tmp_path, call_size):
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    clifft = [c for c in run["cases"] if c["variant_id"] == "clifft-current"]
    members = list(dict.fromkeys(c["workload_id"] for c in clifft))[:2][::-1]
    for workload in workloads["workloads"]:
        if workload["id"] in members:
            workload["shots_per_call"] = call_size
    workloads["groups"]["test-group"] = members
    execution = clifft[0]["execution"]
    implementation = clifft[0]["implementation_id"]
    run.pop("cases")
    run["variants"] = [
        {"id": variant, "implementation_id": implementation,
         "execution": execution, "workloads": "test-group"}
        for variant in ("previous", "current")
    ] + [{"id": "override", "implementation_id": implementation, "execution": execution,
          "workloads": [{"workload_id": members[0], "shots_per_call": 5}]}]
    write_toml(workloads_path, workloads)
    path = tmp_path / "campaign.toml"
    write_toml(path, run)
    suite = load_suite(path)
    assert [(c.workload.id, c.definition["shots_per_call"]) for c in suite.cases] == [
        *[(w, call_size) for w in members], *[(w, call_size) for w in members], (members[0], 5),
    ]


def test_explicit_cases_can_inherit_workload_call_sizes(tmp_path):
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    case = run["cases"][0]
    original = case.pop("shots_per_call")
    workload = next(w for w in workloads["workloads"] if w["id"] == case["workload_id"])
    workload["shots_per_call"] = original + 1
    write_toml(workloads_path, workloads)
    path = tmp_path / "campaign.toml"
    write_toml(path, run)
    suite = load_suite(path)
    assert suite.cases[0].definition["shots_per_call"] == original + 1
    for actual, expected in zip(suite.cases[1:], run["cases"][1:], strict=True):
        assert actual.definition["shots_per_call"] == expected["shots_per_call"]


@pytest.mark.parametrize("damage, message", [
    ("unknown-group", "unknown workload group"),
    ("unknown-member", "references unknown workloads"),
    ("duplicate-member", "unique strings"),
    ("empty-group", "unique strings"),
    ("bad-default", "shots_per_call"),
    ("missing-default", "explicit shots_per_call"),
])
def test_workload_groups_and_defaults_fail_at_input_boundary(tmp_path, damage, message):
    _, workloads, workloads_path, run = _suite_with_local_workloads(tmp_path)
    case = run.pop("cases")[0]
    workload = next(w for w in workloads["workloads"] if w["id"] == case["workload_id"])
    members = [workload["id"]]
    workloads["groups"]["test-group"] = members
    run["variants"] = [{"id": "test-variant", "implementation_id": case["implementation_id"],
                        "execution": case["execution"], "workloads": "test-group"}]
    if damage == "unknown-group":
        run["variants"][0]["workloads"] = "missing-group"
    elif damage == "unknown-member":
        members.append("missing-workload")
    elif damage == "duplicate-member":
        members.append(members[0])
    elif damage == "empty-group":
        members.clear()
    elif damage == "bad-default":
        workload["shots_per_call"] = True
    else:
        workload.pop("shots_per_call")
    write_toml(workloads_path, workloads)
    path = tmp_path / "campaign.toml"
    write_toml(path, run)
    with pytest.raises(ValueError, match=message):
        load_suite(path)


def test_setup_listing_and_collection_share_default_campaign(monkeypatch):
    from clifft_bench import cli

    campaigns = []
    monkeypatch.setattr(cli, "bootstrap", lambda suite: campaigns.append(suite.run_path))
    monkeypatch.setattr(cli, "_list", lambda campaign, as_json: campaigns.append(campaign) or 0)
    monkeypatch.setattr(cli, "_run", lambda args: campaigns.append(args.campaign) or 0)
    assert cli.main(["bootstrap"]) == 0
    assert cli.main(["list"]) == 0
    assert cli.main(["run", "--output-dir", "unused"]) == 0
    assert campaigns[0].stem == campaigns[1] == campaigns[2]


def test_seed_zero_is_rejected(tmp_path) -> None:
    document = read_toml(repository_root() / "campaigns/smoke.toml")
    document["seed"] = 0
    path = tmp_path / "zero.toml"
    write_toml(path, document)
    assert main(["validate", str(path)]) == 2



def test_validate_deep_checks_run_manifest_regardless_of_filename(tmp_path) -> None:
    root = repository_root()
    document = read_toml(root / "campaigns/smoke.toml")
    document["workloads_file"] = str(root / "workloads.toml")
    document["software_file"] = str(root / "software.toml")
    document["cases"][0]["workload_id"] = "missing-workload"
    path = tmp_path / "arbitrary-name.toml"
    write_toml(path, document)
    assert main(["validate", str(path)]) == 2


def test_duplicate_variant_workload_is_rejected_before_collection(tmp_path):
    _, _, _, run = _suite_with_local_workloads(tmp_path)
    duplicate = copy.deepcopy(run["cases"][0])
    duplicate["id"] += "-duplicate"
    run["cases"].append(duplicate)
    path = tmp_path / "duplicate.toml"
    write_toml(path, run)
    with pytest.raises(ValueError, match="duplicate variant/workload"):
        load_suite(path)
