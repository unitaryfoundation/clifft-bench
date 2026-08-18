from __future__ import annotations

from clifft_bench.schema import repository_root


def test_ec2_playbook_uses_the_fixed_study_profile() -> None:
    root = repository_root()
    bootstrap = (root / "scripts/ec2/bootstrap.sh").read_text()
    placement = (root / "scripts/ec2/run-placement.sh").read_text()
    finalize = (root / "scripts/ec2/finalize.sh").read_text()

    assert "shutdown -h +180" in bootstrap
    assert "pip install -e . -r requirements/runner-study.txt" in bootstrap
    assert "clifft-bench validate manifests/run-runner-aa.v1.json" in bootstrap
    assert "--min-sample-seconds 30" in placement
    assert "--repetitions 6" in placement
    assert "for replica in 1 2 3" in placement
    assert "check_value \"lifecycle\" \"on-demand\"" in placement
    assert "instance-identity/document" in placement
    assert "[.[].runner.cloud.boot_id] | unique | length) == 3" in finalize
    assert "clifft-bench analyze-aa" in finalize


def test_ec2_playbook_spools_before_preparing_the_commit() -> None:
    root = repository_root()
    placement = (root / "scripts/ec2/run-placement.sh").read_text()
    finalize = (root / "scripts/ec2/finalize.sh").read_text()

    assert "clifft-bench-ec2-results" in placement
    assert "results/runner-study/ec2" not in placement
    assert "results/runner-study/ec2" in finalize
    assert "refusing to overwrite existing result cohort" in finalize
