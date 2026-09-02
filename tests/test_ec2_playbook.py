from __future__ import annotations

import re
import shutil
import subprocess

import pytest

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
    assert "usage: $0 CAMPAIGN_ID EXECUTION_ID PLACEMENT" in placement
    assert "this boot ID is already represented" in placement
    assert "existing execution instance ID" in placement
    assert "AMI_ID REGION AVAILABILITY_ZONE" not in placement
    assert "require_clean_checkout" in bootstrap
    assert '"${VERSION_ID:-}" == "24.04"' in bootstrap
    assert "python3.12-dev" in bootstrap
    assert ".implementations[]" in bootstrap
    assert "pip install --no-deps" in bootstrap
    assert "pip check" in bootstrap
    assert ".environment.import_modules[]" in bootstrap
    assert "importlib.import_module" in bootstrap
    assert ".variants[].implementation_id" in placement
    assert "qv-run" not in placement
    assert "--memory-limit-gib" in placement
    assert placement.count("--kill-after=30s") == 1
    assert "qv-finalize" not in finalize
    assert "check_value \"lifecycle\" \"on-demand\"" in placement
    assert "instance-identity/document" in placement
    assert "clifft-bench finalize" in finalize
    assert "clifft_bench.release_audit" in finalize
    assert "arm_shutdown_guard" in finalize
    assert "results/$campaign_id/$execution_id" in finalize
    assert "run_status == 1" in placement
    assert "runner-study" not in bootstrap + placement + finalize


def test_bootstrap_extracts_the_declared_symft_install_environment() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    root = repository_root()
    bootstrap = (root / "scripts/ec2/bootstrap.sh").read_text()
    match = re.search(r"^install_environment_query='([^']+)'$", bootstrap, re.MULTILINE)
    assert match is not None

    completed = subprocess.run(
        [
            "jq",
            "-r",
            "--arg",
            "id",
            "symft-0.1.0-9ec5790",
            match.group(1),
            str(root / "manifests/software.v1.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(completed.stdout.splitlines()) == {
        "SYMFT_PY_ENABLE_CUDA\t0",
        "SYMFT_PY_NATIVE\t1",
    }


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
