from __future__ import annotations

from clifft_bench.schema import repository_root


def test_ec2_playbook_is_campaign_driven_and_keeps_safety_checks() -> None:
    root = repository_root()
    common = (root / "scripts/ec2/common.sh").read_text()
    bootstrap = (root / "scripts/ec2/bootstrap.sh").read_text()
    placement = (root / "scripts/ec2/run-placement.sh").read_text()
    finalize = (root / "scripts/ec2/finalize.sh").read_text()

    assert "shutdown -h +480" in common
    assert "Amazon EC2" in common
    assert "sanitize_remote_url" in common
    assert "git remote get-url origin" in placement
    assert "require_clean_checkout" in bootstrap
    assert '"${VERSION_ID:-}" == "24.04"' in bootstrap
    assert ".environments[]" in bootstrap
    assert "pip install --no-deps" in bootstrap
    assert "pip check" in bootstrap
    assert ".import_modules[]" in bootstrap
    assert "importlib.import_module" in bootstrap
    assert ".runs[]" in placement
    assert "check_value \"lifecycle\" \"on-demand\"" in placement
    assert "instance-identity/document" in placement
    assert "clifft-bench finalize" in finalize
    assert "arm_shutdown_guard" in finalize
    assert "results/$campaign_id/$execution_id" in finalize
    assert "run_status == 1" in placement
    assert "runner-study" not in bootstrap + placement + finalize


def test_ec2_playbook_documents_storage_security_and_manual_control() -> None:
    playbook = (repository_root() / "docs/manual-ec2.md").read_text()

    assert "verified Canonical" in playbook
    assert "Ubuntu Server 24.04 LTS (HVM), SSD Volume Type" in playbook
    assert "64-bit (x86)" in playbook
    assert "16 GiB `gp3`" in playbook
    assert "no S3 Files, EFS, FSx" in playbook
    assert "no IAM role" in playbook
    assert "eight-hour" in playbook


def test_ec2_results_spool_outside_checkout_until_finalization() -> None:
    root = repository_root()
    placement = (root / "scripts/ec2/run-placement.sh").read_text()
    finalize = (root / "scripts/ec2/finalize.sh").read_text()

    assert "clifft-bench-ec2-results" in placement
    assert "results/" not in placement
    assert "refusing to overwrite existing execution" in finalize
