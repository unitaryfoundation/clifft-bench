from __future__ import annotations

import json
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
        "collection": {"placements": 1},
        "variants": [
            {"id": "current", "implementation_id": "selected"},
            {"id": "bulk", "implementation_id": "selected"},
        ],
    }))
    (root / "requirements.txt").write_text("example==1.0\n")
    (root / "software.json").write_text(json.dumps({"implementations": [
        {"id": "selected", "environment": {
            "requirements": "requirements.txt", "import_modules": ["json", "math"],
            "install_environment": INSTALL_ENV,
        }},
        {"id": "unused"},
    ]}))
    # Interpreters/CLI record calls instead of installing packages or collecting data.
    recorder = f"#!{sys.executable}\n" + '''import json, os, sys
with open(os.environ["BENCH_TEST_LOG"], "a") as stream:
    stream.write(json.dumps({
        "executable": sys.argv[0], "args": sys.argv[1:],
        "install_environment": {k: os.environ.get(k) for k in
                                ("BENCH_TEST_FLAGS", "BENCH_TEST_CUDA")},
    }) + "\\n")
if os.environ.get("BENCH_TEST_FAIL_CHECK") and sys.argv[1:] == ["-m", "pip", "check"]:
    sys.exit(1)
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
    for variable in (*INSTALL_ENV, "BENCH_TEST_DIRTY", "BENCH_TEST_FAIL_CHECK"):
        monkeypatch.delenv(variable, raising=False)
    return root


def _run_script(root: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    # Run the checked-in script, substituting host/install operations at the shell
    # boundary. jq, manifest selection, environment passing and file guards are real.
    return subprocess.run([
        "bash", "-c", '''
set -euo pipefail
git() {
    case "$1" in
        rev-parse) printf '%s\\n' "$BENCH_TEST_ROOT" ;;
        status) printf '%s' "${BENCH_TEST_DIRTY:-}" ;;
        *) return 99 ;;
    esac
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


@pytest.mark.skipif(sys.platform != "linux", reason="bootstrap reads Linux /etc/os-release")
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


@pytest.mark.skipif(sys.platform != "linux", reason="bootstrap reads Linux /etc/os-release")
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
