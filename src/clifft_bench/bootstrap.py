"""Install and verify the isolated environments selected by a campaign."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from clifft_bench.config import Implementation, Suite
from clifft_bench.schema import repository_root
from clifft_bench.system import THREAD_LIMIT_ENVIRONMENT, require_clean_checkout


def selected_implementations(suite: Suite) -> list[Implementation]:
    selected = {case.implementation.id: case.implementation for case in suite.cases}
    return [selected[key] for key in sorted(selected)]


def verify_environment(implementation: Implementation) -> None:
    python = implementation.python_executable()
    environment = os.environ | implementation.definition["environment"].get(
        "install_environment", {}
    )
    environment.update(THREAD_LIMIT_ENVIRONMENT)
    environment["PYTHONPATH"] = str(repository_root() / "src")
    subprocess.run([python, "-m", "pip", "check"], env=environment, check=True, timeout=60)
    subprocess.run(
        [
            python,
            "-c",
            "import importlib, importlib.metadata, json, sys; "
            "from clifft_bench.adapters import load_adapter; d=json.loads(sys.argv[1]); "
            "[importlib.import_module(m) for m in d['environment']['import_modules']]; "
            "installed=importlib.metadata.version(d['distribution']); "
            "\nif installed != d['version']: "
            "raise ValueError(f'expected {d[\"version\"]}, got {installed}')\n"
            "load_adapter(d['adapter']).verify_installation(expected_commit=d['commit_sha'], "
            "source_url=d['source_url'])",
            json.dumps(implementation.definition),
        ],
        env=environment,
        check=True,
        timeout=120,
    )


def bootstrap(suite: Suite) -> None:
    if suite.run["classification"] == "official":
        require_clean_checkout(repository_root())
    for implementation in selected_implementations(suite):
        if "environment" not in implementation.definition:
            raise ValueError(f"{implementation.id}: no install environment")
        if not implementation.requirements_path.is_file():
            raise ValueError(f"missing requirements: {implementation.requirements_path}")
        environment = os.environ | implementation.definition["environment"].get(
            "install_environment", {}
        )
        print(f"Installing {implementation.id} in {implementation.environment_path}", flush=True)
        subprocess.run(
            [sys.executable, "-m", "venv", str(implementation.environment_path)],
            check=True,
            timeout=120,
        )
        python = implementation.python_executable()
        subprocess.run(
            [python, "-m", "pip", "install", "--upgrade", "pip"],
            env=environment,
            check=True,
            timeout=300,
        )
        subprocess.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "-r",
                str(implementation.requirements_path),
            ],
            env=environment,
            check=True,
            timeout=1800,
        )
        verify_environment(implementation)
