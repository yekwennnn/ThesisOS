"""Draft 2020-12 validation for ThesisOS' canonical JSON contracts.

The files in ``schemas/`` are the serialization authority.  This module keeps
JSON Schema an optional *runtime* dependency so the stdlib-only storage core
can still be imported, while failing clearly whenever contract validation is
requested without ``jsonschema`` installed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any


SCHEMA_FILENAMES: dict[str, str] = {
    "SourceDocument": "source-document.schema.json",
    "Citation": "citation.schema.json",
    "Evidence": "evidence.schema.json",
    "ThesisCard": "thesis-card.schema.json",
    "ThesisDiff": "thesis-diff.schema.json",
    "UserReview": "user-review.schema.json",
}


class SchemaValidationRuntimeError(RuntimeError):
    """Raised when the JSON Schema engine or schema catalog is unavailable."""


class SchemaCatalogError(SchemaValidationRuntimeError):
    """Raised when a canonical schema file is missing or malformed."""


class SchemaInstanceError(ValueError):
    """Raised when a JSON value violates a canonical schema."""

    def __init__(self, kind: str, issues: list["SchemaIssue"]):
        self.kind = kind
        self.issues = tuple(issues)
        rendered = "; ".join(issue.render() for issue in issues)
        super().__init__(f"{kind} validation failed: {rendered}")


@dataclass(frozen=True)
class SchemaIssue:
    """A deterministic, machine-serializable JSON Schema failure."""

    instance_path: str
    schema_path: str
    message: str
    validator: str | None

    def render(self) -> str:
        location = self.instance_path or "$"
        return f"{location}: {self.message}"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "instance_path": self.instance_path,
            "schema_path": self.schema_path,
            "message": self.message,
            "validator": self.validator,
        }


def canonical_kind(value: str) -> str:
    """Return the canonical object kind, accepting filename-style aliases."""

    normalized = "".join(character for character in value if character.isalnum()).lower()
    by_normalized = {
        "sourcedocument": "SourceDocument",
        "citation": "Citation",
        "evidence": "Evidence",
        "thesiscard": "ThesisCard",
        "thesisdiff": "ThesisDiff",
        "userreview": "UserReview",
    }
    try:
        return by_normalized[normalized]
    except KeyError as exc:
        allowed = ", ".join(SCHEMA_FILENAMES)
        raise SchemaCatalogError(f"unknown schema kind {value!r}; expected one of: {allowed}") from exc


def discover_schema_directory(explicit: str | Path | None = None) -> Path:
    """Locate the repository schema catalog without depending on the CWD."""

    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    else:
        configured = os.environ.get("THESISOS_SCHEMA_DIR")
        if configured:
            candidates.append(Path(configured))
        else:
            packaged = _packaged_resource_directory("_schemas")
            if packaged is not None:
                candidates.append(packaged)
            candidates.extend(
                (
                    Path(__file__).resolve().parents[2] / "schemas",
                    Path.cwd() / "schemas",
                )
            )
    for candidate in candidates:
        if candidate.is_dir() and all((candidate / name).is_file() for name in SCHEMA_FILENAMES.values()):
            return candidate.resolve()
    rendered = ", ".join(str(candidate) for candidate in candidates)
    raise SchemaCatalogError(
        "cannot locate the complete ThesisOS schema catalog; "
        f"searched: {rendered}. Set THESISOS_SCHEMA_DIR to the schemas directory."
    )


def _packaged_resource_directory(name: str) -> Path | None:
    """Return an installed package resource directory when it is filesystem-backed."""

    try:
        candidate = resources.files("thesisos").joinpath(name)
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - defensive import fallback
        return None
    return candidate if isinstance(candidate, Path) else None


class SchemaCatalog:
    """Loaded and reference-registered canonical ThesisOS schemas."""

    def __init__(self, schema_directory: str | Path | None = None):
        try:
            from jsonschema import Draft202012Validator, FormatChecker
            from referencing import Registry, Resource
        except ImportError as exc:
            raise SchemaValidationRuntimeError(
                "JSON Schema validation requires the optional 'jsonschema' package "
                "with Draft 2020-12 support; install jsonschema and retry"
            ) from exc

        self.schema_directory = discover_schema_directory(schema_directory)
        self._validator_type = Draft202012Validator
        self._format_checker = FormatChecker()
        self.schemas: dict[str, dict[str, Any]] = {}
        self._store: dict[str, dict[str, Any]] = {}

        for kind, filename in SCHEMA_FILENAMES.items():
            path = self.schema_directory / filename
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SchemaCatalogError(f"cannot load schema {path}: {exc}") from exc
            if not isinstance(parsed, dict):
                raise SchemaCatalogError(f"schema must be a JSON object: {path}")
            schema_id = parsed.get("$id")
            if not isinstance(schema_id, str) or not schema_id:
                raise SchemaCatalogError(f"schema is missing a non-empty $id: {path}")
            try:
                Draft202012Validator.check_schema(parsed)
            except Exception as exc:
                raise SchemaCatalogError(f"invalid Draft 2020-12 schema {path}: {exc}") from exc
            if schema_id in self._store:
                raise SchemaCatalogError(f"duplicate schema $id: {schema_id}")
            self.schemas[kind] = parsed
            # Register both the declared absolute ID and useful local aliases.
            self._store[schema_id] = parsed
            self._store[filename] = parsed
            self._store[path.as_uri()] = parsed

        registry = Registry()
        for schema_id, schema in self._store.items():
            if "://" not in schema_id:
                continue
            registry = registry.with_resource(schema_id, Resource.from_contents(schema))
        self._registry = registry

    def validate(self, kind: str, instance: Any) -> Any:
        """Validate and return ``instance``, or raise ``SchemaInstanceError``."""

        canonical = canonical_kind(kind)
        schema = self.schemas[canonical]
        validator = self._validator_type(
            schema,
            registry=self._registry,
            format_checker=self._format_checker,
        )
        raw_errors = sorted(
            validator.iter_errors(instance),
            key=lambda error: (
                tuple(str(item) for item in error.absolute_path),
                tuple(str(item) for item in error.absolute_schema_path),
                error.message,
            ),
        )
        if raw_errors:
            issues = [
                SchemaIssue(
                    instance_path=_json_pointer(error.absolute_path),
                    schema_path=_json_pointer(error.absolute_schema_path),
                    message=error.message,
                    validator=None if error.validator is None else str(error.validator),
                )
                for error in raw_errors
            ]
            raise SchemaInstanceError(canonical, issues)
        return instance


def validate_instance(
    kind: str,
    instance: Any,
    *,
    schema_directory: str | Path | None = None,
) -> Any:
    """One-shot convenience wrapper around :class:`SchemaCatalog`."""

    return SchemaCatalog(schema_directory).validate(kind, instance)


def load_json_object(path: str | Path) -> dict[str, Any]:
    """Read one UTF-8 JSON object with concise, user-facing errors."""

    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read JSON file {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid JSON in {source} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {source}")
    return value


def schema_issue_payload(error: SchemaInstanceError) -> dict[str, Any]:
    """Return the stable JSON representation used by the CLI."""

    return {
        "ok": False,
        "error": {
            "code": "schema_validation_error",
            "message": str(error),
        },
        "kind": error.kind,
        "errors": [issue.to_dict() for issue in error.issues],
    }


def _json_pointer(parts: Any) -> str:
    encoded = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "" if not encoded else "/" + "/".join(encoded)
