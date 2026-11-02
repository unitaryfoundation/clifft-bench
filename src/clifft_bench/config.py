"""Load TOML campaigns and validate their executable contracts."""

from __future__ import annotations

import hashlib
import math
import re
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from clifft_bench.adapters import load_adapter
from clifft_bench.schema import repository_root
from clifft_bench.selection import detector_mask


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
    root: Path = Path(".")

    @property
    def id(self) -> str:
        return str(self.definition["id"])

    @property
    def environment_path(self) -> Path:
        return self.root / ".campaign-envs" / self.id

    @property
    def requirements_path(self) -> Path:
        return self.root / "environments" / f"{self.id}.txt"

    def python_executable(self) -> str:
        return str(self.environment_path / "bin/python")


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
            raise ValueError(f"duplicate {label} id {identifier!r}")
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
    for variant in _unique_by_id(run["variants"], "variant").values():
        for workload in variant["workloads"]:
            definitions.append(
                {
                    "id": f"{workload['workload_id']}--{variant['id']}",
                    "variant_id": variant["id"],
                    "workload_id": workload["workload_id"],
                    "implementation_id": variant["implementation_id"],
                    "shots_per_call": workload["shots_per_call"],
                    "execution": dict(variant["execution"]),
                }
            )
    return definitions


def _resolve_selections(run: dict, workloads: dict, software: dict) -> None:
    defaults = {w["id"]: w.get("shots_per_call") for w in workloads["workloads"]}
    implementations = {i["id"]: i for i in software["implementations"]}
    for item in run.get("variants", run.get("cases", [])):
        if "workloads" in item:
            selections = item["workloads"]
            if isinstance(selections, str):
                if selections not in workloads.get("groups", {}):
                    raise ValueError(f"unknown workload group {selections!r}")
                selections = [{"workload_id": w} for w in workloads["groups"][selections]]
            item["workloads"] = selections
        else:
            selections = [item]
        for selection in selections:
            identifier = selection["workload_id"]
            if identifier not in defaults:
                raise ValueError(f"{item['id']!r} references unknown workload {identifier!r}")
            if "shots_per_call" not in selection:
                if defaults[identifier] is None:
                    raise ValueError(f"workload {identifier!r} needs an explicit shots_per_call")
                selection["shots_per_call"] = defaults[identifier]
        execution = item["execution"]
        if "options_preset" in execution:
            name = execution.pop("options_preset")
            preset = software.get("option_presets", {}).get(name)
            if preset is None:
                raise ValueError(f"unknown options preset {name!r}")
            implementation = implementations.get(item["implementation_id"])
            if implementation is None or implementation["adapter"] != preset["adapter"]:
                raise ValueError(f"options preset {name!r} requires adapter {preset['adapter']!r}")
            execution["options"] = deepcopy(preset["options"])


def load_suite(run_path: Path, *, verify_artifacts: bool = True) -> Suite:
    run_path = run_path.resolve()
    run = read_toml(run_path)
    _validate_run(run)
    workloads_path = (run_path.parent / run["workloads_file"]).resolve()
    software_path = (run_path.parent / run["software_file"]).resolve()
    workloads_document = read_toml(workloads_path)
    validate_workloads(workloads_document)
    software_document = read_toml(software_path)
    validate_software(software_document)
    suite_versions = {
        str(run["suite_version"]),
        str(workloads_document["suite_version"]),
        str(software_document["suite_version"]),
    }
    if len(suite_versions) != 1:
        raise ValueError("campaign, workloads, and software must share one suite_version")

    workload_definitions = _unique_by_id(workloads_document["workloads"], "workload")
    implementation_definitions = _unique_by_id(
        software_document["implementations"], "implementation"
    )
    _resolve_selections(run, workloads_document, software_document)
    case_definitions = _unique_by_id(_case_definitions(run), "case")

    workloads: dict[str, Workload] = {}
    for identifier, definition in workload_definitions.items():
        try:
            detector_mask(definition["semantics"], definition["expected_metadata"]["num_detectors"])
        except ValueError as error:
            raise ValueError(f"workload {identifier!r}: {error}") from error
        if "postselection_detector_indices" in definition["semantics"] and set(
            definition["compatible_adapters"]
        ) - {"clifft", "tsim"}:
            raise ValueError(
                f"workload {identifier!r} declares an adapter without detector-subset support"
            )
        observable_index = int(definition["semantics"]["observable_index"])
        num_observables = int(definition["expected_metadata"]["num_observables"])
        if observable_index >= num_observables:
            raise ValueError(
                f"workload {identifier!r} observable index {observable_index} is outside "
                f"the declared {num_observables} observables"
            )
        artifact_path = (workloads_path.parent / definition["artifact"]["path"]).resolve()
        expected_sha256 = str(definition["artifact"]["sha256"])
        if verify_artifacts:
            if not artifact_path.is_file():
                raise ValueError(
                    f"workload {identifier!r} artifact does not exist: {artifact_path}"
                )
            actual_sha256 = _artifact_digest(artifact_path)
            if actual_sha256 != expected_sha256:
                raise ValueError(
                    f"workload {identifier!r} artifact SHA-256 mismatch: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
        workloads[identifier] = Workload(definition, artifact_path, expected_sha256)

    implementations = {
        identifier: Implementation(definition, software_path.parent)
        for identifier, definition in implementation_definitions.items()
    }

    cases = []
    for identifier, definition in case_definitions.items():
        workload_id = str(definition["workload_id"])
        implementation_id = str(definition["implementation_id"])
        if workload_id not in workloads:
            raise ValueError(f"case {identifier!r} references unknown workload {workload_id!r}")
        if implementation_id not in implementations:
            raise ValueError(
                f"case {identifier!r} references unknown implementation {implementation_id!r}"
            )
        workload = workloads[workload_id]
        implementation = implementations[implementation_id]
        adapter = str(implementation.definition["adapter"])
        if adapter not in workload.definition["compatible_adapters"]:
            raise ValueError(
                f"case {identifier!r} forces incomparable adapter {adapter!r} onto "
                f"workload {workload_id!r}"
            )
        execution = definition["execution"]
        load_adapter(adapter).validate_options(execution["options"])
        if execution["batch_size"] == "calibrate":
            if execution["mode"] != "throughput":
                raise ValueError(
                    f"case {identifier!r} requests batch calibration outside throughput mode"
                )
            if not execution["batch_enabled"]:
                raise ValueError(
                    f"case {identifier!r} requests batch calibration with batching disabled"
                )
            if adapter not in {"clifft", "symft", "stim", "xtim", "tsim"}:
                raise ValueError(
                    f"case {identifier!r} requests unsupported batch calibration for "
                    f"adapter {adapter!r}"
                )
        cases.append(Case(definition, workload, implementation))

    variants = {str(case.definition["variant_id"]) for case in cases}
    for comparison in _unique_by_id(run.get("comparisons", []), "comparison").values():
        baseline = str(comparison["baseline_variant"])
        candidates = {str(item) for item in comparison["candidate_variants"]}
        if baseline in candidates:
            raise ValueError(
                f"comparison {comparison['id']!r} repeats its baseline as a candidate"
            )
        unknown = sorted({baseline, *candidates} - variants)
        if unknown:
            raise ValueError(
                f"comparison {comparison['id']!r} references unknown variants: "
                + ", ".join(unknown)
            )

    for comparison in run.get("comparisons", []):
        baseline = {
            c.workload.id: c.definition["shots_per_call"]
            for c in cases
            if c.definition["variant_id"] == comparison["baseline_variant"]
        }
        for case in cases:
            if (
                case.definition["variant_id"] in comparison["candidate_variants"]
                and case.workload.id in baseline
                and case.definition["shots_per_call"] != baseline[case.workload.id]
            ):
                raise ValueError(
                    f"comparison {comparison['id']!r} has unequal shots_per_call "
                    f"for workload {case.workload.id!r}"
                )

    if run["classification"] == "official":
        used_implementations = {case.implementation.id: case.implementation for case in cases}
        for implementation in used_implementations.values():
            if "environment" not in implementation.definition:
                raise ValueError(
                    f"official implementation {implementation.id!r} has no install environment"
                )
            if not implementation.requirements_path.is_file():
                raise ValueError(
                    f"implementation {implementation.id!r} requirements do not exist: "
                    f"{implementation.requirements_path}"
                )

    return Suite(
        run_path=run_path,
        run=run,
        workloads_path=workloads_path,
        workloads_document=workloads_document,
        software_path=software_path,
        software_document=software_document,
        cases=tuple(cases),
    )


def read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{path}: {error}") from error


def campaign_path(value: str | Path = "smoke") -> Path:
    path = Path(value)
    if path.is_file():
        return path.resolve()
    root = repository_root()
    if (root / path).is_file():
        return (root / path).resolve()
    return root / "campaigns" / f"{value}.toml"


def _fields(value: Any, required: str, optional: str = "") -> None:
    if not isinstance(value, dict):
        raise ValueError("expected a table")
    missing = set(required.split()) - value.keys()
    unknown = value.keys() - set((required + " " + optional).split())
    if missing or unknown:
        raise ValueError(
            f"missing required fields: {sorted(missing)}; unknown fields: {sorted(unknown)}"
        )


def _text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")


def _identifier(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]+", value):
        raise ValueError(f"invalid identifier {value!r}")


def _number(
    value: Any, label: str, *, integer: bool = False, minimum: float = 0, inclusive: bool = False
) -> None:
    if (
        type(value) not in ((int,) if integer else (int, float))
        or not math.isfinite(value)
        or value < minimum
        or (value == minimum and not inclusive)
    ):
        raise ValueError(
            f"{label} must be a finite {'integer' if integer else 'number'} "
            f"{'at least' if inclusive else 'greater than'} {minimum}"
        )


def _strings(value: Any, label: str, *, nonempty: bool = True) -> None:
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(x, str) or not x for x in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{label} must be a list of unique strings")


def _entries(document: dict, key: str) -> list[dict]:
    entries = document[key]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{key} must be a nonempty array of tables")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{key} must contain tables")
        _identifier(entry.get("id"))
    _unique_by_id(entries, key)
    return entries


def _version(document: dict) -> None:
    if not isinstance(document["suite_version"], str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", document["suite_version"]
    ):
        raise ValueError("invalid suite_version")


def _validate_execution(execution: dict) -> None:
    _fields(execution, "mode batch_enabled batch_size", "options options_preset")
    if execution["mode"] not in ("latency", "throughput"):
        raise ValueError("invalid execution mode")
    if type(execution["batch_enabled"]) is not bool:
        raise ValueError("batch_enabled must be boolean")
    if execution["batch_size"] != "calibrate":
        _number(execution["batch_size"], "batch_size", integer=True)
    if not execution["batch_enabled"] and execution["batch_size"] != 1:
        raise ValueError("batching disabled requires batch_size=1")
    if "options_preset" in execution:
        _identifier(execution["options_preset"])
        if "options" in execution:
            raise ValueError("declare options or options_preset, not both")
        return
    execution.setdefault("options", {})
    if not isinstance(execution["options"], dict):
        raise ValueError("options must be a table")


def _validate_run(run: dict) -> None:
    _fields(
        run,
        "suite_version profile_id classification seed "
        "workloads_file software_file resources measurement",
        "title hardware_epoch reference_host collection comparisons variants cases",
    )
    _version(run)
    _identifier(run["profile_id"])
    if "title" in run:
        _text(run["title"], "title")
    if "hardware_epoch" in run:
        _identifier(run["hardware_epoch"])
    for key in ("workloads_file", "software_file"):
        _text(run[key], key)
    if run["classification"] not in ("smoke", "official"):
        raise ValueError("invalid campaign classification")
    _number(run["seed"], "seed", integer=True)
    _fields(run["resources"], "", "logical_cpu")
    cpu = run["resources"].setdefault("logical_cpu", None)
    if cpu is not None:
        _number(cpu, "logical_cpu", integer=True, inclusive=True)
    _fields(
        run["measurement"],
        "setup_timeout_seconds request_timeout_seconds warmup_shots "
        "correctness_shots min_sample_seconds repetitions",
    )
    for key, value in run["measurement"].items():
        _number(value, key, integer=key in ("warmup_shots", "correctness_shots", "repetitions"))
    if ("cases" in run) == ("variants" in run):
        raise ValueError("declare exactly one of cases or variants")
    for item in _entries(run, "cases" if "cases" in run else "variants"):
        if "cases" in run:
            _fields(item, "id variant_id workload_id implementation_id execution", "shots_per_call")
            _identifier(item["variant_id"])
            _text(item["workload_id"], "workload_id")
            if "shots_per_call" in item:
                _number(item["shots_per_call"], "shots_per_call", integer=True)
        else:
            _fields(item, "id implementation_id execution workloads")
            if isinstance(item["workloads"], str):
                _identifier(item["workloads"])
            else:
                if not isinstance(item["workloads"], list) or not item["workloads"]:
                    raise ValueError("variant workloads must be nonempty")
                for selection in item["workloads"]:
                    _fields(selection, "workload_id", "shots_per_call")
                    _text(selection["workload_id"], "workload_id")
                    if "shots_per_call" in selection:
                        _number(selection["shots_per_call"], "shots_per_call", integer=True)
        _text(item["implementation_id"], "implementation_id")
        _validate_execution(item["execution"])
    if "comparisons" in run:
        for comparison in _entries(run, "comparisons"):
            _fields(comparison, "id baseline_variant candidate_variants")
            _text(comparison["baseline_variant"], "baseline_variant")
            _strings(comparison["candidate_variants"], "candidate_variants")
    if run["classification"] == "official":
        for key in ("title", "hardware_epoch", "reference_host", "collection", "comparisons"):
            if key not in run:
                raise ValueError(f"official campaign requires {key}")
    if "reference_host" in run:
        _fields(run["reference_host"], "provider instance_type architecture os_id os_version")
        for key, value in run["reference_host"].items():
            _text(value, key)
    if "collection" in run:
        _fields(
            run["collection"],
            "placements replicas_per_placement run_timeout_minutes memory_limit_gib",
        )
        for key, value in run["collection"].items():
            _number(value, key, integer=key != "memory_limit_gib")
        for key in ("placements", "replicas_per_placement"):
            if run["collection"][key] != 1:
                raise ValueError(f"collection.{key} must be 1; only one collection is supported")


def validate_workloads(document: dict) -> None:
    _fields(document, "suite_version workloads", "groups")
    _version(document)
    for w in _entries(document, "workloads"):
        _fields(
            w,
            "id family description artifact dialect "
        "angle_convention parameters semantics expected_metadata compatible_adapters provenance",
            "shots_per_call",
        )
        if "shots_per_call" in w:
            _number(w["shots_per_call"], "shots_per_call", integer=True)
        for key in ("family", "description"):
            _text(w[key], key)
        if w["dialect"] not in ("stim", "clifft-symft-extended-stim") or w[
            "angle_convention"
        ] not in ("none", "half-turns"):
            raise ValueError("invalid circuit dialect or angle convention")
        if not isinstance(w["parameters"], dict):
            raise ValueError("parameters must be a table")
        _fields(w["artifact"], "path sha256")
        _text(w["artifact"]["path"], "artifact path")
        if not isinstance(w["artifact"]["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", w["artifact"]["sha256"]
        ):
            raise ValueError("invalid artifact sha256")
        _fields(w["expected_metadata"], "num_qubits num_measurements num_detectors num_observables")
        for key, value in w["expected_metadata"].items():
            _number(value, key, integer=True, inclusive=True)
        sem = w["semantics"]
        _fields(
            sem,
            "logical_work output_contract observable_index "
        "postselect_all_detectors reference_convention throughput_numerator batch_semantics",
            "postselection_detector_indices",
        )
        for key in ("logical_work", "batch_semantics"):
            _text(sem[key], key)
        if (
            sem["output_contract"] != "aggregate detector-discard and logical-observable counts"
            or sem["reference_convention"] != "raw-record-parity"
            or sem["throughput_numerator"] != "attempted shots"
        ):
            raise ValueError("unsupported workload output semantics")
        _number(sem["observable_index"], "observable_index", integer=True, inclusive=True)
        if type(sem["postselect_all_detectors"]) is not bool:
            raise ValueError("postselect_all_detectors must be boolean")
        _strings(w["compatible_adapters"], "compatible_adapters")
        for name in w["compatible_adapters"]:
            load_adapter(name)
        _fields(w["provenance"], "source_url source_commit source_path license", "notes")
        for key, value in w["provenance"].items():
            _text(value, key)
        if not re.fullmatch(r"[0-9a-f]{40}", w["provenance"]["source_commit"]):
            raise ValueError("invalid source commit")

    groups = document.get("groups", {})
    if not isinstance(groups, dict):
        raise ValueError("groups must be a table")
    known = {w["id"] for w in document["workloads"]}
    for name, members in groups.items():
        _identifier(name)
        _strings(members, f"workload group {name!r}")
        unknown = set(members) - known
        if unknown:
            raise ValueError(
                f"workload group {name!r} references unknown workloads: {sorted(unknown)}"
            )


def validate_software(document: dict) -> None:
    _fields(document, "suite_version implementations", "option_presets")
    _version(document)
    presets = document.get("option_presets", {})
    if not isinstance(presets, dict):
        raise ValueError("option_presets must be a table")
    for name, preset in presets.items():
        _identifier(name)
        _fields(preset, "adapter options")
        _text(preset["adapter"], "adapter")
        if not isinstance(preset["options"], dict):
            raise ValueError("preset options must be a table")
        load_adapter(preset["adapter"]).validate_options(preset["options"])
    for imp in _entries(document, "implementations"):
        _fields(
            imp,
            "id name adapter distribution version "
        "commit_sha commit_datetime source_url dependency_distributions build",
            "display_version release_datetime source_tag environment",
        )
        for key in (
            "name",
            "adapter",
            "distribution",
            "version",
            "commit_sha",
            "commit_datetime",
            "source_url",
        ):
            _text(imp[key], key)
        load_adapter(imp["adapter"])
        if not re.fullmatch(r"[0-9a-f]{40}", imp["commit_sha"]):
            raise ValueError("invalid implementation commit")
        imp.setdefault("release_datetime", None)
        for key in ("commit_datetime", "release_datetime"):
            value = imp[key]
            if value is None and key == "release_datetime":
                continue
            _text(value, key)
            if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError(f"{key} must include a timezone")
        for key in ("display_version", "source_tag"):
            if key in imp:
                _text(imp[key], key)
        if not urlsplit(imp["source_url"]).scheme:
            raise ValueError("source_url must be an absolute URL")
        _strings(imp["dependency_distributions"], "dependency_distributions", nonempty=False)
        _fields(imp["build"], "precision compiler_flags features")
        _text(imp["build"]["precision"], "precision")
        for key in ("compiler_flags", "features"):
            _strings(imp["build"][key], key, nonempty=False)
        if "environment" in imp:
            _fields(imp["environment"], "import_modules", "install_environment")
            _strings(imp["environment"]["import_modules"], "import_modules")
            env = imp["environment"].get("install_environment", {})
            if not isinstance(env, dict) or any(
                not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or not isinstance(v, str)
                for k, v in env.items()
            ):
                raise ValueError("invalid install environment")
