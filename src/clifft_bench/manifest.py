"""Load the checked-in workload, software, and run manifests."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clifft_bench.schema import SchemaValidationError, validate_path


@dataclass(frozen=True)
class Workload:
    definition: dict[str, Any]
    artifact_path: Path
    artifact_sha256: str

    @property
    def id(self) -> str:
        return str(self.definition["id"])


@dataclass(frozen=True)
class Implementation:
    definition: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.definition["id"])

    def python_executable(self) -> str:
        variable = self.definition.get("python_executable_env")
        if variable:
            value = os.environ.get(str(variable))
            if value:
                return value
        return sys.executable


@dataclass(frozen=True)
class Case:
    definition: dict[str, Any]
    workload: Workload
    implementation: Implementation

    @property
    def id(self) -> str:
        return str(self.definition["id"])


@dataclass(frozen=True)
class Suite:
    run_path: Path
    run: dict[str, Any]
    workloads_path: Path
    workloads_document: dict[str, Any]
    software_path: Path
    software_document: dict[str, Any]
    cases: tuple[Case, ...]


def _unique_by_id(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        identifier = str(item["id"])
        if identifier in indexed:
            raise SchemaValidationError(f"duplicate {label} id {identifier!r}")
        indexed[identifier] = item
    return indexed


def _artifact_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_suite(run_path: Path, *, verify_artifacts: bool = True) -> Suite:
    run_path = run_path.resolve()
    run = validate_path(run_path)
    workloads_path = (run_path.parent / run["workloads_manifest"]).resolve()
    software_path = (run_path.parent / run["software_manifest"]).resolve()
    workloads_document = validate_path(workloads_path)
    software_document = validate_path(software_path)

    workload_definitions = _unique_by_id(workloads_document["workloads"], "workload")
    implementation_definitions = _unique_by_id(
        software_document["implementations"], "implementation"
    )
    case_definitions = _unique_by_id(run["cases"], "case")

    workloads: dict[str, Workload] = {}
    for identifier, definition in workload_definitions.items():
        artifact_path = (workloads_path.parent / definition["artifact"]["path"]).resolve()
        expected_sha256 = str(definition["artifact"]["sha256"])
        if verify_artifacts:
            if not artifact_path.is_file():
                raise SchemaValidationError(
                    f"workload {identifier!r} artifact does not exist: {artifact_path}"
                )
            actual_sha256 = _artifact_digest(artifact_path)
            if actual_sha256 != expected_sha256:
                raise SchemaValidationError(
                    f"workload {identifier!r} artifact SHA-256 mismatch: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
        workloads[identifier] = Workload(definition, artifact_path, expected_sha256)

    implementations = {
        identifier: Implementation(definition)
        for identifier, definition in implementation_definitions.items()
    }

    cases = []
    for identifier, definition in case_definitions.items():
        workload_id = str(definition["workload_id"])
        implementation_id = str(definition["implementation_id"])
        if workload_id not in workloads:
            raise SchemaValidationError(
                f"case {identifier!r} references unknown workload {workload_id!r}"
            )
        if implementation_id not in implementations:
            raise SchemaValidationError(
                f"case {identifier!r} references unknown implementation {implementation_id!r}"
            )
        workload = workloads[workload_id]
        implementation = implementations[implementation_id]
        adapter = str(implementation.definition["adapter"])
        if adapter not in workload.definition["compatible_adapters"]:
            raise SchemaValidationError(
                f"case {identifier!r} forces incomparable adapter {adapter!r} onto "
                f"workload {workload_id!r}"
            )
        cases.append(Case(definition, workload, implementation))

    return Suite(
        run_path=run_path,
        run=run,
        workloads_path=workloads_path,
        workloads_document=workloads_document,
        software_path=software_path,
        software_document=software_document,
        cases=tuple(cases),
    )
