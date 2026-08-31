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


@dataclass(frozen=True)
class Campaign:
    path: Path
    document: dict[str, Any]
    suites: tuple[Suite, ...]

    @property
    def id(self) -> str:
        return str(self.document["id"])


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


def _case_definitions(run: dict[str, Any]) -> list[dict[str, Any]]:
    if "cases" in run:
        return list(run["cases"])
    definitions = []
    for workload in run["matrix"]["workloads"]:
        for variant in run["matrix"]["variants"]:
            definitions.append(
                {
                    "id": f"{workload['workload_id']}--{variant['id']}",
                    "workload_id": workload["workload_id"],
                    "implementation_id": variant["implementation_id"],
                    "shots_per_call": workload["shots_per_call"],
                    "execution": dict(variant["execution"]),
                }
            )
    return definitions


def load_suite(run_path: Path, *, verify_artifacts: bool = True) -> Suite:
    run_path = run_path.resolve()
    run = validate_path(run_path)
    workloads_path = (run_path.parent / run["workloads_manifest"]).resolve()
    software_path = (run_path.parent / run["software_manifest"]).resolve()
    workloads_document = validate_path(workloads_path)
    software_document = validate_path(software_path)
    suite_versions = {
        str(run["suite_version"]),
        str(workloads_document["suite_version"]),
        str(software_document["suite_version"]),
    }
    if len(suite_versions) != 1:
        raise SchemaValidationError(
            "run, workload, and software manifests must share one suite_version"
        )

    workload_definitions = _unique_by_id(workloads_document["workloads"], "workload")
    implementation_definitions = _unique_by_id(
        software_document["implementations"], "implementation"
    )
    case_definitions = _unique_by_id(_case_definitions(run), "case")

    workloads: dict[str, Workload] = {}
    for identifier, definition in workload_definitions.items():
        observable_index = int(definition["semantics"]["observable_index"])
        num_observables = int(definition["expected_metadata"]["num_observables"])
        if observable_index >= num_observables:
            raise SchemaValidationError(
                f"workload {identifier!r} observable index {observable_index} is outside "
                f"the declared {num_observables} observables"
            )
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


def load_campaign(path: Path, *, verify_artifacts: bool = True) -> Campaign:
    path = path.resolve()
    document = validate_path(path)
    run_definitions = _unique_by_id(document["runs"], "campaign run")
    suites = tuple(
        load_suite(
            (path.parent / str(definition["run_manifest"])).resolve(),
            verify_artifacts=verify_artifacts,
        )
        for definition in run_definitions.values()
    )
    for suite in suites:
        if document["id"] != suite.run["profile_id"]:
            raise SchemaValidationError(
                f"campaign id {document['id']!r} does not match run profile "
                f"{suite.run['profile_id']!r}"
            )
    declared_run_ids = set(run_definitions)
    manifest_run_ids = {str(suite.run["run_id"]) for suite in suites}
    if declared_run_ids != manifest_run_ids:
        raise SchemaValidationError(
            "campaign run ids do not match their run manifests: "
            f"declared {sorted(declared_run_ids)}, manifests {sorted(manifest_run_ids)}"
        )
    suite_versions = {str(suite.run["suite_version"]) for suite in suites}
    if len(suite_versions) != 1:
        raise SchemaValidationError("campaign run manifests must share one suite_version")
    comparisons = _unique_by_id(document["comparisons"], "campaign comparison")
    for comparison in comparisons.values():
        if comparison["baseline_run"] in comparison["candidate_runs"]:
            raise SchemaValidationError(
                f"comparison {comparison['id']!r} repeats its baseline as a candidate"
            )
        referenced = {str(comparison["baseline_run"]), *comparison["candidate_runs"]}
        unknown = sorted(referenced - declared_run_ids)
        if unknown:
            raise SchemaValidationError(
                f"comparison {comparison['id']!r} references unknown runs: "
                + ", ".join(unknown)
            )

    environments = _unique_by_id(document["environments"], "campaign environment")
    variables: dict[str, str] = {}
    for identifier, environment in environments.items():
        variable = str(environment["python_executable_env"])
        if variable in variables:
            raise SchemaValidationError(
                f"campaign environments {variables[variable]!r} and {identifier!r} "
                f"share python executable variable {variable!r}"
            )
        variables[variable] = identifier
        requirements = (path.parent / str(environment["requirements"])).resolve()
        if not requirements.is_file():
            raise SchemaValidationError(
                f"campaign environment {identifier!r} requirements do not exist: "
                f"{requirements}"
            )

    required_variables = {
        str(case.implementation.definition["python_executable_env"])
        for suite in suites
        for case in suite.cases
        if case.implementation.definition.get("python_executable_env")
    }
    missing = sorted(required_variables - set(variables))
    if missing:
        raise SchemaValidationError(
            "campaign does not define environments for: " + ", ".join(missing)
        )
    unused = sorted(set(variables) - required_variables)
    if unused:
        raise SchemaValidationError(
            "campaign defines unused environment variables for: " + ", ".join(unused)
        )

    return Campaign(path=path, document=document, suites=suites)
