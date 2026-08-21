"""Manifest and topology helpers for the manual QV multicore campaign."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from clifft_bench.schema import SchemaValidationError, validate_path


@dataclass(frozen=True)
class QVCampaign:
    path: Path
    document: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.document["id"])

    @property
    def environments(self) -> dict[str, dict[str, Any]]:
        return {str(item["id"]): item for item in self.document["environments"]}

    @property
    def runs(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.document["runs"])

    @property
    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def _unique(items: Iterable[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        identifier = str(item["id"])
        if identifier in indexed:
            raise SchemaValidationError(f"duplicate {label} id {identifier!r}")
        indexed[identifier] = item
    return indexed


def load_qv_campaign(path: Path) -> QVCampaign:
    path = path.resolve()
    document = validate_path(path)
    if document["schema_version"] != "clifft-bench/qv-campaign/v1":
        raise SchemaValidationError(f"{path} is not a QV multicore campaign")

    environments = _unique(document["environments"], "environment")
    runs = _unique(document["runs"], "run")
    circuit = document["circuit"]
    generator_environment = str(circuit["generator_environment"])
    if generator_environment not in environments:
        raise SchemaValidationError(
            f"circuit generator references unknown environment {generator_environment!r}"
        )
    if "qiskit" not in environments[generator_environment]["import_modules"]:
        raise SchemaValidationError("circuit generator environment must import qiskit")

    declared_qubits = set(int(value) for value in circuit["qubits"])
    physical_cores = int(document["reference_host"]["physical_cores"])
    for run in runs.values():
        environment_id = str(run["environment_id"])
        if environment_id not in environments:
            raise SchemaValidationError(
                f"run {run['id']!r} references unknown environment {environment_id!r}"
            )
        unknown_qubits = sorted(set(int(value) for value in run["qubits"]) - declared_qubits)
        if unknown_qubits:
            raise SchemaValidationError(
                f"run {run['id']!r} references undeclared qubit widths: {unknown_qubits}"
            )
        oversized = sorted(
            int(value) for value in run["threads"] if int(value) > physical_cores
        )
        if oversized:
            raise SchemaValidationError(
                f"run {run['id']!r} requests more than {physical_cores} physical cores: "
                f"{oversized}"
            )

    for identifier, environment in environments.items():
        requirements = (path.parent / str(environment["requirements"])).resolve()
        if not requirements.is_file():
            raise SchemaValidationError(
                f"campaign environment {identifier!r} requirements do not exist: {requirements}"
            )
    return QVCampaign(path=path, document=document)


def _linux_topology() -> list[tuple[int, int, int]]:
    """Return allowed (logical CPU, package, physical core) records."""
    if not hasattr(os, "sched_getaffinity"):
        return []
    records: list[tuple[int, int, int]] = []
    for logical_cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f"/sys/devices/system/cpu/cpu{logical_cpu}/topology")
        try:
            package = int((topology / "physical_package_id").read_text().strip())
            core = int((topology / "core_id").read_text().strip())
        except (OSError, ValueError):
            continue
        records.append((logical_cpu, package, core))
    return records


def select_physical_cpus(
    count: int, topology: Iterable[tuple[int, int, int]] | None = None
) -> list[int]:
    """Choose one allowed logical CPU for each of ``count`` physical cores."""
    if count < 1:
        raise ValueError("physical CPU count must be positive")
    records = list(_linux_topology() if topology is None else topology)
    selected: list[int] = []
    seen: set[tuple[int, int]] = set()
    for logical_cpu, package, core in sorted(records):
        identity = (package, core)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(logical_cpu)
        if len(selected) == count:
            return selected
    raise ValueError(
        f"requested {count} physical cores, but only {len(selected)} are available"
    )


def scheduled_cases(campaign: QVCampaign) -> list[dict[str, Any]]:
    """Expand the matrix with alternating order across circuit seeds."""
    seeds = [int(value) for value in campaign.document["circuit"]["seeds"]]
    phases: list[str] = []
    for run in campaign.runs:
        phase = str(run["phase"])
        if phase not in phases:
            phases.append(phase)

    expanded: list[dict[str, Any]] = []
    group_index = 0
    for phase in phases:
        phase_runs = [run for run in campaign.runs if run["phase"] == phase]
        widths = sorted({int(q) for run in phase_runs for q in run["qubits"]})
        for qubits in widths:
            for repetition, seed in enumerate(seeds):
                group: list[dict[str, Any]] = []
                for run in phase_runs:
                    if qubits not in run["qubits"]:
                        continue
                    for threads in run["threads"]:
                        group.append(
                            {
                                "run": run,
                                "qubits": qubits,
                                "seed": seed,
                                "repetition": repetition,
                                "threads": int(threads),
                            }
                        )
                if group_index % 2:
                    group.reverse()
                expanded.extend(group)
                group_index += 1
    return expanded
