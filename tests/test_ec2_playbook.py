from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from clifft_bench.schema import repository_root

SCRIPTS = repository_root() / "scripts/ec2"
INSTALL_ENV = {"BENCH_TEST_FLAGS": "-O3 -march=native", "BENCH_TEST_CUDA": "0"}


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch) -> Path:
    if shutil.which("jq") is None:
        pytest.skip("the EC2 scripts require jq")
    root = tmp_path / "checkout with spaces"
    common = root / "scripts/ec2/common.sh"
    common.parent.mkdir(parents=True)
    common.symlink_to(SCRIPTS / "common.sh")
    campaign = root / "campaigns/test-campaign/run.v1.json"
    campaign.parent.mkdir(parents=True)
    campaign.write_text(json.dumps({
        "software_manifest": "../../software.json",
        "reference_host": {"instance_type": "m7i.large"},
        "collection": {
            "placements": 1, "replicas_per_placement": 2,
            "run_timeout_minutes": 7, "memory_limit_gib": 3,
        },
        "variants": [
            {"id": "current", "implementation_id": "selected"},
            {"id": "bulk", "implementation_id": "selected"},
        ],
    }))
    (root / "requirements.txt").write_text("example==1.0\n")
    (root / "software.json").write_text(json.dumps({"implementations": [
        {"id": "selected", "python_executable_env": "CLIFFT_BENCH_TEST_PYTHON",
         "environment": {
            "requirements": "requirements.txt", "import_modules": ["json", "math"],
            "install_environment": INSTALL_ENV,
        }},
        {"id": "unused"},
    ]}))
    # Interpreters/CLI record calls instead of installing packages or collecting data.
    recorder = f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
with open(os.environ["BENCH_TEST_LOG"], "a") as stream:
    stream.write(json.dumps({
        "executable": sys.argv[0], "args": sys.argv[1:],
        "environment": {k: v for k, v in os.environ.items()
                        if k.startswith("CLIFFT_BENCH_")},
        "install_environment": {k: os.environ.get(k) for k in
                                ("BENCH_TEST_FLAGS", "BENCH_TEST_CUDA")},
    }) + "\\n")
if os.environ.get("BENCH_TEST_FAIL_CHECK") and sys.argv[1:] == ["-m", "pip", "check"]:
    sys.exit(1)
if sys.argv[1] == "run":
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    output.write_text(json.dumps({
        "run": {"profile_id": "test-campaign"},
        "runner": {
            "suite_source": {"commit": os.environ["CLIFFT_BENCH_RUN_SHA"], "dirty": False},
            "cloud": {k.removeprefix("CLIFFT_BENCH_CLOUD_").lower(): v
                      for k, v in os.environ.items() if k.startswith("CLIFFT_BENCH_CLOUD_")},
        },
    }))
    sys.exit(int(os.environ.get("BENCH_TEST_RUN_STATUS", "0")))
if sys.argv[1] == "validate":
    json.loads(Path(sys.argv[2]).read_text())
'''
    for relative in (".venv/bin/python", ".venv/bin/clifft-bench",
                     ".campaign-envs/test-campaign/selected/bin/python"):
        command = root / relative
        command.parent.mkdir(parents=True, exist_ok=True)
        command.write_text(recorder)
        command.chmod(0o755)
    monkeypatch.setenv("BENCH_TEST_ROOT", str(root))
    monkeypatch.setenv("BENCH_TEST_LOG", str(root / "calls.jsonl"))
    monkeypatch.setenv("CLIFFT_BENCH_EC2_SPOOL_ROOT", str(root / "spool"))
    monkeypatch.setenv("BENCH_TEST_COMMIT", "a" * 40)
    for variable in (*INSTALL_ENV, "BENCH_TEST_DIRTY", "BENCH_TEST_FAIL_CHECK",
                     "BENCH_TEST_RUN_STATUS", "BENCH_TEST_INSTANCE_TYPE", "BENCH_TEST_LIFECYCLE"):
        monkeypatch.delenv(variable, raising=False)
    return root


def _run_script(root: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    # Run the checked-in script, substituting host/install operations at the shell
    # boundary. jq, manifest selection, environment passing and file guards are real.
    return subprocess.run([
        "bash", "-c", '''
set -euo pipefail
git() {
    case "$*" in
        "rev-parse --show-toplevel") printf '%s\\n' "$BENCH_TEST_ROOT" ;;
        "rev-parse HEAD") printf '%s\\n' "$BENCH_TEST_COMMIT" ;;
        "branch --show-current") printf 'test-branch\\n' ;;
        "remote get-url origin") printf 'https://user:token@example.org/org/repo.git\\n' ;;
        status*) printf '%s' "${BENCH_TEST_DIRTY:-}" ;;
        *) return 99 ;;
    esac
}
curl() {
    case "${@: -1}" in
        */api/token) printf 'test-token\\n' ;;
        */instance-identity/document)
            printf '{"instanceId":"i-test","instanceType":"%s","imageId":"ami-test",
                     "region":"us-east-1","availabilityZone":"us-east-1a"}\\n' \\
                "${BENCH_TEST_INSTANCE_TYPE:-m7i.large}" ;;
        */instance-life-cycle) printf '%s\\n' "${BENCH_TEST_LIFECYCLE:-on-demand}" ;;
        *) return 99 ;;
    esac
}
timeout() {
    .venv/bin/python timeout "$@"
    while [[ "$1" == --* ]]; do shift; done
    shift  # The duration; execute immediately after recording the deadline arguments.
    "$@"
}
sudo() { :; }
python3() { if [[ "$1" == -c ]]; then printf '3.12\\n'; fi; }
source() {
    if [[ "$1" == /etc/os-release ]]; then
        ID=ubuntu VERSION_ID=24.04
    else
        builtin source "$@"
        if [[ "$1" == */common.sh ]]; then
            require_ec2_linux() { :; }
            arm_shutdown_guard() { :; }
        fi
    fi
}
source "$@"
''', "ec2-test", str(SCRIPTS / script), *args,
    ], cwd=root, capture_output=True, text=True, timeout=10)


def _calls(root: Path) -> list[dict]:
    path = root / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.skipif(not os.access("/etc/os-release", os.R_OK),
                    reason="bootstrap requires readable /etc/os-release")
def test_bootstrap_installs_only_selected_environments_with_declared_settings(checkout):
    result = _run_script(checkout, "bootstrap.sh", "test-campaign")
    assert result.returncode == 0, result.stderr
    calls = [c for c in _calls(checkout) if ".campaign-envs" in Path(c["executable"]).parts]
    assert {Path(c["executable"]).parent.parent.name for c in calls} == {"selected"}
    installs = [c for c in calls if "-r" in c["args"]]
    assert len(installs) == 1  # Two variants share one installation.
    assert installs[0]["args"] == [
        "-m", "pip", "install", "--no-deps", "-r", str(checkout / "requirements.txt"),
    ]
    checks = [c for c in calls if c["args"] == ["-m", "pip", "check"]]
    assert len(checks) == 1
    imports = [c for c in calls if c["args"][0] == "-c"]
    assert [c["args"][-1] for c in imports] == ["json", "math"]
    assert all(c["install_environment"] == INSTALL_ENV for c in [*installs, *checks, *imports])


@pytest.mark.skipif(not os.access("/etc/os-release", os.R_OK),
                    reason="bootstrap requires readable /etc/os-release")
def test_bootstrap_stops_on_failed_dependency_check(checkout, monkeypatch):
    monkeypatch.setenv("BENCH_TEST_FAIL_CHECK", "1")
    result = _run_script(checkout, "bootstrap.sh", "test-campaign")
    assert result.returncode != 0
    assert _calls(checkout)[-1]["args"] == ["-m", "pip", "check"]


def test_bootstrap_rejects_dirty_source_before_installing(checkout, monkeypatch):
    monkeypatch.setenv("BENCH_TEST_DIRTY", " M tracked-file\n")
    result = _run_script(checkout, "bootstrap.sh", "test-campaign")
    assert result.returncode != 0
    assert "checkout must be clean" in result.stderr
    assert not _calls(checkout)


@pytest.fixture
def placement_checkout(checkout):
    if not os.access("/proc/sys/kernel/random/boot_id", os.R_OK):
        pytest.skip("run-placement reads the Linux boot ID")
    path = checkout / "campaigns/test-campaign/run.v1.json"
    campaign = json.loads(path.read_text())
    campaign["collection"]["placements"] = 2
    path.write_text(json.dumps(campaign))
    return checkout


@pytest.mark.parametrize("run_status", [0, 1])
def test_placement_records_replicas_with_limits_environments_and_provenance(
    placement_checkout, monkeypatch, run_status,
):
    root = placement_checkout
    monkeypatch.delenv("CLIFFT_BENCH_EC2_SPOOL_ROOT")
    monkeypatch.setenv("BENCH_TEST_RUN_STATUS", str(run_status))
    result = _run_script(root, "run-placement.sh", "test-campaign", "test-execution", "1")
    assert result.returncode == 0, result.stderr
    spool = root.parent / "clifft-bench-ec2-results/test-execution"
    assert not spool.is_relative_to(root)
    assert (spool / "campaign-id").read_text().strip() == "test-campaign"
    complete = spool / "placement-01"
    assert (complete / "COMPLETE").is_file()
    assert not list(spool.glob(".incomplete-*"))
    calls = _calls(root)
    assert [c["args"][0] for c in calls] == [
        "validate", "timeout", "run", "validate", "timeout", "run", "validate",
    ]
    assert calls[0]["args"] == ["validate", str(root / "campaigns/test-campaign/run.v1.json")]
    for replica in (1, 2):
        deadline, run, validation = calls[3 * replica - 2:3 * replica + 1]
        assert deadline["args"] == [
            "timeout", "--signal=TERM", "--kill-after=30s", "7m",
            ".venv/bin/clifft-bench", *run["args"],
        ]
        assert run["args"][:-1] == [
            "run", "--run-manifest", str(root / "campaigns/test-campaign/run.v1.json"),
            "--memory-limit-gib", "3", "--output",
        ]
        output = Path(run["args"][-1])
        label = f"test-campaign-p01-r{replica:02d}"
        assert output.name == f"{label}-raw.json"
        assert output.parent.parent.name.startswith(".incomplete-p01-")
        assert output.parent.parent.parent.resolve() == spool
        assert validation["args"] == ["validate", str(output)]
        assert (complete / "raw" / output.name).is_file()
        expected = {
            "CLOUD_PROVIDER": "aws", "CLOUD_INSTANCE_ID": "i-test",
            "CLOUD_INSTANCE_TYPE": "m7i.large", "CLOUD_IMAGE_ID": "ami-test",
            "CLOUD_REGION": "us-east-1", "CLOUD_AVAILABILITY_ZONE": "us-east-1a",
            "CLOUD_LIFECYCLE": "on-demand",
            "CLOUD_BOOT_ID": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "RUN_PROVIDER": "aws-ec2-manual", "RUN_REPOSITORY": "https://example.org/org/repo.git",
            "RUN_WORKFLOW": "scripts/ec2/run-placement.sh", "RUN_REF": "test-branch",
            "RUN_SHA": "a" * 40, "RUNNER_NAME": "i-test", "RUNNER_OS": "Linux",
            "IMAGE_OS": "ubuntu-24.04", "IMAGE_VERSION": "ami-test",
            "RUN_ID": f"test-execution/{label}", "RUN_ATTEMPT": f"1.{replica}",
            "TEST_PYTHON": str(root / ".campaign-envs/test-campaign/selected/bin/python"),
        }
        for key, value in expected.items():
            assert run["environment"][f"CLIFFT_BENCH_{key}"] == value, key


@pytest.mark.parametrize("run_status", [2, 124])
def test_placement_preserves_incomplete_evidence_after_abort(
    placement_checkout, monkeypatch, run_status,
):
    root = placement_checkout
    monkeypatch.setenv("BENCH_TEST_RUN_STATUS", str(run_status))
    result = _run_script(root, "run-placement.sh", "test-campaign", "test-execution", "1")
    assert result.returncode != 0
    assert f"terminated without a complete structured result (exit {run_status})" in result.stderr
    spool = root / "spool/test-execution"
    assert not (spool / "placement-01").exists()
    incomplete, = spool.glob(".incomplete-*")
    assert not (incomplete / "COMPLETE").exists()
    assert list(incomplete.glob("raw/*-raw.json"))
    assert [c["args"][0] for c in _calls(root)] == ["validate", "timeout", "run"]


@pytest.mark.parametrize("variable, value, message", [
    ("BENCH_TEST_INSTANCE_TYPE", "wrong.large", "instance type mismatch"),
    ("BENCH_TEST_LIFECYCLE", "spot", "lifecycle mismatch"),
])
def test_placement_rejects_incompatible_hosts(placement_checkout, monkeypatch,
                                            variable, value, message):
    root = placement_checkout
    monkeypatch.setenv(variable, value)
    result = _run_script(root, "run-placement.sh", "test-campaign", "test-execution", "1")
    assert result.returncode != 0
    assert message in result.stderr
    assert [c["args"][0] for c in _calls(root)] == ["validate"]
    assert not (root / "spool").exists()


@pytest.mark.parametrize("placement, message", [
    ("1", "placement 1 is already complete"),
    ("2", "this boot ID is already represented"),
])
def test_placement_rejects_overwrite_and_boot_reuse(placement_checkout, placement, message):
    root = placement_checkout
    first = _run_script(root, "run-placement.sh", "test-campaign", "test-execution", "1")
    assert first.returncode == 0, first.stderr
    spool = root / "spool/test-execution"
    before = {p.relative_to(spool): p.read_bytes() for p in spool.rglob("*") if p.is_file()}
    run_count = sum(c["args"][0] == "run" for c in _calls(root))
    result = _run_script(root, "run-placement.sh", "test-campaign", "test-execution", placement)
    assert result.returncode != 0
    assert message in result.stderr
    assert sum(c["args"][0] == "run" for c in _calls(root)) == run_count
    assert {p.relative_to(spool): p.read_bytes() for p in spool.rglob("*") if p.is_file()} == before
    assert not list(spool.glob(".incomplete-*"))


@pytest.mark.parametrize("problem, message", [
    ("campaign", "execution belongs to"),
    ("incomplete", "is not complete"),
    ("existing", "refusing to overwrite"),
])
def test_finalize_rejects_invalid_spools_and_existing_results(checkout, problem, message):
    spool = checkout / "spool/test-execution"
    placement = spool / "placement-01"
    raw = placement / "raw/result-raw.json"
    raw.parent.mkdir(parents=True)
    raw.write_text('{"evidence": "unchanged"}\n')
    (spool / "campaign-id").write_text("another-campaign" if problem == "campaign"
                                       else "test-campaign")
    if problem != "incomplete":
        (placement / "COMPLETE").touch()
    target = checkout / "results/test-campaign/test-execution"
    if problem == "existing":
        target.mkdir(parents=True)
        (target / "evidence.json").write_bytes(raw.read_bytes())
    result = _run_script(checkout, "finalize.sh", "test-campaign", "test-execution")
    assert result.returncode != 0
    assert message in result.stderr
    assert not _calls(checkout)
    assert raw.read_text() == '{"evidence": "unchanged"}\n'
    if problem == "existing":
        assert list(target.iterdir()) == [target / "evidence.json"]
        assert (target / "evidence.json").read_bytes() == raw.read_bytes()
    else:
        assert not target.exists()


@pytest.mark.parametrize("remote, expected", [
    ("https://user:token@example.org/org/repo.git", "https://example.org/org/repo.git"),
    ("https://example.org/org/repo.git", "https://example.org/org/repo.git"),
    ("git@example.org:org/repo.git", "git@example.org:org/repo.git"),
])
def test_source_repository_provenance_omits_http_credentials(remote, expected):
    result = subprocess.run([
        "bash", "-c", 'source "$1"; sanitize_remote_url "$2"',
        "ec2-test", str(SCRIPTS / "common.sh"), remote,
    ], cwd=repository_root(), capture_output=True, text=True, check=True, timeout=10)
    assert result.stdout.strip() == expected
