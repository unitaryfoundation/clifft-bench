import json
import subprocess

import pytest

from scripts.ci_changes import classify_changes, main


@pytest.mark.parametrize("path", [
    "README.md", "docs/benchmark-contract.md", "AGENTS.md",
    "reporting/figures/web/chart.png", "experiments/qv/run.py",
    "results/historical/raw/old.json",
])
def test_non_core_changes_skip_simulators(tmp_path, monkeypatch, path):
    monkeypatch.chdir(tmp_path)
    assert classify_changes([path]) == (False, [])


@pytest.mark.parametrize("path", [
    "src/clifft_bench/runner.py", "tests/test_runner.py", "pyproject.toml",
    "uv.lock", "environments/tool.txt", "campaigns/smoke.toml", "software.toml",
    "workloads.toml", "workloads/circuits/example.stim", ".github/workflows/ci.yml",
    "scripts/ci_changes.py", "new-build-input",
])
def test_code_and_unknown_paths_run_simulators(tmp_path, monkeypatch, path):
    monkeypatch.chdir(tmp_path)
    assert classify_changes(["README.md", path]) == (True, [])


def test_changed_execution_companions_select_raw_evidence_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    execution = tmp_path / "results/release/example with spaces"
    execution.mkdir(parents=True)
    (execution / "raw.json").write_text("{}")
    assert classify_changes([
        "results/release/example with spaces/raw.json",
        "results/release/example with spaces/cases.csv",
        "results/release/example with spaces/report/review.md",
        "results/release/example with spaces/report/chart.png",
    ]) == (False, ["results/release/example with spaces"])
    assert classify_changes([
        "src/clifft_bench/report.py", "results/release/example with spaces/cases.csv",
    ]) == (True, ["results/release/example with spaces"])


def test_git_diff_includes_earlier_commits_and_renamed_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "CI test")
    git("config", "user.email", "ci@example.invalid")
    source = tmp_path / "source.py"
    source.write_text("pass\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    source.rename(tmp_path / "notes.md")
    git("add", "-A")
    git("commit", "-qm", "rename code to documentation")
    (tmp_path / "README.md").write_text("Documentation only follow-up\n")
    git("add", ".")
    git("commit", "-qm", "docs follow-up")
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    main(base)
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["full"] == "true"
    assert values["test"] == "true"
    assert json.loads(values["results"]) == []
