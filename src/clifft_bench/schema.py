"""JSON Schema loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_FILES = {
    "clifft-bench/workloads/v1": "workloads-v1.schema.json",
    "clifft-bench/software/v1": "software-v1.schema.json",
    "clifft-bench/run/v1": "run-v1.schema.json",
    "clifft-bench/result/v1": "result-v1.schema.json",
    "clifft-bench/campaign/v1": "campaign-v1.schema.json",
    "clifft-bench/execution/v1": "execution-v1.schema.json",
    "clifft-bench/qv-campaign/v1": "qv-campaign-v1.schema.json",
    "clifft-bench/qv-result/v1": "qv-result-v1.schema.json",
}


class SchemaValidationError(ValueError):
    """Raised when a document does not satisfy its declared schema."""


def repository_root() -> Path:
    """Return the benchmark-suite checkout containing manifests and schemas."""
    source_candidate = Path(__file__).resolve().parents[2]
    if (source_candidate / "schemas").is_dir() and (source_candidate / "manifests").is_dir():
        return source_candidate
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "schemas").is_dir() and (candidate / "manifests").is_dir():
            return candidate
    raise SchemaValidationError(
        "cannot locate the clifft-bench checkout; run the CLI from this repository"
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise SchemaValidationError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise SchemaValidationError(f"{path}: the document root must be an object")
    return document


def schema_path(schema_version: str, schema_dir: Path | None = None) -> Path:
    try:
        filename = SCHEMA_FILES[schema_version]
    except KeyError as error:
        raise SchemaValidationError(f"unknown schema_version {schema_version!r}") from error
    return (schema_dir or repository_root() / "schemas") / filename


def validate_document(
    document: dict[str, Any], *, source: str = "document", schema_dir: Path | None = None
) -> None:
    version = document.get("schema_version")
    if not isinstance(version, str):
        raise SchemaValidationError(f"{source}: missing string schema_version")
    schema = read_json(schema_path(version, schema_dir))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    rendered = []
    for error in errors[:20]:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        rendered.append(f"  {location}: {error.message}")
    if len(errors) > 20:
        rendered.append(f"  ... and {len(errors) - 20} more error(s)")
    raise SchemaValidationError(f"{source} failed schema validation:\n" + "\n".join(rendered))


def validate_path(path: Path, *, schema_dir: Path | None = None) -> dict[str, Any]:
    document = read_json(path)
    validate_document(document, source=str(path), schema_dir=schema_dir)
    return document
