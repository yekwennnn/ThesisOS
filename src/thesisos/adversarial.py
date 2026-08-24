"""Declarative adversarial suites for the historical ThesisDiff evaluator.

A suite is successful only when its immutable golden case still passes and
every declared malicious mutation is rejected by the check named in the suite
manifest.  Crashes, no-op mutations, and failures in unrelated checks do not
count as detecting the intended defect.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .evaluation import EvaluationReport, evaluate_case_file, evaluate_replay


class AdversarialSuiteError(ValueError):
    """Raised when a suite or mutation contract is malformed."""


@dataclass(frozen=True)
class MutationResult:
    mutation_id: str
    expected_failed_checks: tuple[str, ...]
    actual_failed_checks: tuple[str, ...]
    detected: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutation_id": self.mutation_id,
            "detected": self.detected,
            "expected_failed_checks": list(self.expected_failed_checks),
            "actual_failed_checks": list(self.actual_failed_checks),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AdversarialSuiteReport:
    suite_id: str
    golden_case: str
    golden_passed: bool
    golden_failed_checks: tuple[str, ...]
    mutations: tuple[MutationResult, ...]

    @property
    def passed(self) -> bool:
        return (
            self.golden_passed
            and len(self.mutations) >= 2
            and all(mutation.detected for mutation in self.mutations)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "passed": self.passed,
            "golden_case": self.golden_case,
            "golden_passed": self.golden_passed,
            "golden_failed_checks": list(self.golden_failed_checks),
            "mutations": [mutation.to_dict() for mutation in self.mutations],
        }


@dataclass
class _ReplayPayload:
    base_thesis: dict[str, Any]
    documents: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    thesis_diff: dict[str, Any]
    user_review: dict[str, Any]
    accepted_thesis: dict[str, Any]
    base_analysis_cutoff_at: str
    base_source_document_ids: tuple[str, ...]
    base_evidence_ids: tuple[str, ...]
    expected_assumption_evidence_ids: dict[str, tuple[str, ...]]
    expected_citation_text_sha256: dict[str, str]
    critical_financial_evidence_ids: tuple[str, ...]
    key_fact_evidence_ids: tuple[str, ...]


def evaluate_adversarial_suite(
    suite_file: str | Path,
) -> AdversarialSuiteReport:
    """Run one declarative suite against a complete historical replay case."""

    suite_path = Path(suite_file).resolve()
    manifest = _load_object(suite_path, "suite manifest")
    if manifest.get("schema_version") != "1.0.0":
        raise AdversarialSuiteError("suite schema_version must equal 1.0.0")
    suite_id = _required_text(manifest.get("suite_id"), "suite_id")
    golden_reference = _required_text(manifest.get("golden_case"), "golden_case")
    golden_path = _resolve_path(suite_path.parent, golden_reference)
    mutations = _validate_mutations(manifest.get("mutations"))

    golden_report = evaluate_case_file(golden_path)
    payload = _load_replay_payload(golden_path)
    results: list[MutationResult] = []
    for mutation in mutations:
        mutated_payload = deepcopy(payload)
        _apply_mutation(mutated_payload, mutation)
        report = _evaluate_payload(mutated_payload)
        failed_checks = _failed_check_names(report)
        expected = tuple(mutation["expected_failed_checks"])
        missing = sorted(set(expected) - set(failed_checks))
        detected = not report.passed and not missing
        detail = (
            "all expected checks rejected the mutation"
            if detected
            else (
                "mutation was not rejected"
                if report.passed
                else "expected checks did not fail: " + ", ".join(missing)
            )
        )
        results.append(
            MutationResult(
                mutation_id=mutation["mutation_id"],
                expected_failed_checks=expected,
                actual_failed_checks=failed_checks,
                detected=detected,
                detail=detail,
            )
        )

    return AdversarialSuiteReport(
        suite_id=suite_id,
        golden_case=str(golden_path),
        golden_passed=golden_report.passed,
        golden_failed_checks=_failed_check_names(golden_report),
        mutations=tuple(results),
    )


def evaluate_adversarial_suites(
    suite_files: Sequence[str | Path],
) -> tuple[AdversarialSuiteReport, ...]:
    """Run several suites in caller-provided deterministic order."""

    if not suite_files:
        raise AdversarialSuiteError("at least one suite file is required")
    return tuple(evaluate_adversarial_suite(path) for path in suite_files)


def _evaluate_payload(payload: _ReplayPayload) -> EvaluationReport:
    return evaluate_replay(
        base_thesis=payload.base_thesis,
        documents=payload.documents,
        evidence=payload.evidence,
        thesis_diff=payload.thesis_diff,
        base_analysis_cutoff_at=payload.base_analysis_cutoff_at,
        base_source_document_ids=payload.base_source_document_ids,
        base_evidence_ids=payload.base_evidence_ids,
        expected_assumption_evidence_ids=payload.expected_assumption_evidence_ids,
        expected_citation_text_sha256=payload.expected_citation_text_sha256,
        critical_financial_evidence_ids=payload.critical_financial_evidence_ids,
        key_fact_evidence_ids=payload.key_fact_evidence_ids,
        user_review=payload.user_review,
        accepted_thesis=payload.accepted_thesis,
    )


def _load_replay_payload(case_path: Path) -> _ReplayPayload:
    manifest = _load_object(case_path, "golden case manifest")
    paths = manifest.get("paths")
    if not isinstance(paths, Mapping):
        raise AdversarialSuiteError("golden case paths must be an object")
    base = case_path.parent
    return _ReplayPayload(
        base_thesis=_load_one(base, paths.get("base_thesis"), "base_thesis"),
        documents=_load_many(base, paths.get("documents"), "documents"),
        evidence=_load_many(base, paths.get("evidence"), "evidence"),
        thesis_diff=_load_one(base, paths.get("thesis_diff"), "thesis_diff"),
        user_review=_load_one(base, paths.get("user_review"), "user_review"),
        accepted_thesis=_load_one(
            base, paths.get("accepted_thesis"), "accepted_thesis"
        ),
        base_analysis_cutoff_at=_required_text(
            manifest.get("base_analysis_cutoff_at"), "base_analysis_cutoff_at"
        ),
        base_source_document_ids=_string_array(
            manifest.get("base_source_document_ids"), "base_source_document_ids"
        ),
        base_evidence_ids=_string_array(
            manifest.get("base_evidence_ids"), "base_evidence_ids"
        ),
        expected_assumption_evidence_ids=_string_array_mapping(
            manifest.get("expected_assumption_evidence_ids"),
            "expected_assumption_evidence_ids",
        ),
        expected_citation_text_sha256=_string_mapping(
            manifest.get("expected_citation_text_sha256"),
            "expected_citation_text_sha256",
        ),
        critical_financial_evidence_ids=_string_array(
            manifest.get("critical_financial_evidence_ids"),
            "critical_financial_evidence_ids",
        ),
        key_fact_evidence_ids=_string_array(
            manifest.get("key_fact_evidence_ids"), "key_fact_evidence_ids"
        ),
    )


def _validate_mutations(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or len(value) < 2:
        raise AdversarialSuiteError("suite mutations must contain at least two items")
    result: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise AdversarialSuiteError(f"mutations[{index}] must be an object")
        mutation_id = _required_text(
            raw.get("mutation_id"), f"mutations[{index}].mutation_id"
        )
        if mutation_id in identifiers:
            raise AdversarialSuiteError(f"duplicate mutation_id {mutation_id}")
        identifiers.add(mutation_id)
        operation = raw.get("operation")
        if operation not in {"set", "delete", "swap"}:
            raise AdversarialSuiteError(
                f"mutation {mutation_id} operation must be set, delete, or swap"
            )
        _validate_target(raw.get("target"), f"mutation {mutation_id}.target")
        _required_pointer(raw.get("pointer"), f"mutation {mutation_id}.pointer")
        if operation == "set" and "value" not in raw:
            raise AdversarialSuiteError(f"mutation {mutation_id} set requires value")
        if operation == "swap":
            _validate_target(
                raw.get("other_target"), f"mutation {mutation_id}.other_target"
            )
            _required_pointer(
                raw.get("other_pointer"), f"mutation {mutation_id}.other_pointer"
            )
        expected = _string_array(
            raw.get("expected_failed_checks"),
            f"mutation {mutation_id}.expected_failed_checks",
        )
        normalized = dict(raw)
        normalized["mutation_id"] = mutation_id
        normalized["expected_failed_checks"] = expected
        result.append(normalized)
    return tuple(result)


def _apply_mutation(payload: _ReplayPayload, mutation: Mapping[str, Any]) -> None:
    target = _select_target(payload, mutation["target"])
    pointer = str(mutation["pointer"])
    operation = mutation["operation"]
    if operation == "set":
        prior = _read_pointer(target, pointer)
        replacement = deepcopy(mutation["value"])
        if prior == replacement:
            raise AdversarialSuiteError(
                f"mutation {mutation['mutation_id']} is a no-op"
            )
        _write_pointer(target, pointer, replacement)
        return
    if operation == "delete":
        _delete_pointer(target, pointer)
        return

    other_target = _select_target(payload, mutation["other_target"])
    other_pointer = str(mutation["other_pointer"])
    left = deepcopy(_read_pointer(target, pointer))
    right = deepcopy(_read_pointer(other_target, other_pointer))
    if left == right:
        raise AdversarialSuiteError(
            f"mutation {mutation['mutation_id']} swaps equal values"
        )
    _write_pointer(target, pointer, right)
    _write_pointer(other_target, other_pointer, left)


def _select_target(
    payload: _ReplayPayload, specification: Any
) -> dict[str, Any] | list[Any]:
    if not isinstance(specification, Mapping):
        raise AdversarialSuiteError("mutation target must be an object")
    artifact = specification.get("artifact")
    allowed = {
        "base_thesis",
        "documents",
        "evidence",
        "thesis_diff",
        "user_review",
        "accepted_thesis",
    }
    if artifact not in allowed:
        raise AdversarialSuiteError(f"unknown mutation artifact {artifact!r}")
    value = getattr(payload, str(artifact))
    match = specification.get("match")
    if isinstance(value, list):
        if not isinstance(match, Mapping) or not match:
            raise AdversarialSuiteError(
                f"collection target {artifact} requires a non-empty match object"
            )
        candidates = [
            item
            for item in value
            if isinstance(item, dict)
            and all(item.get(key) == expected for key, expected in match.items())
        ]
        if len(candidates) != 1:
            raise AdversarialSuiteError(
                f"target {artifact} match resolved to {len(candidates)} objects"
            )
        return candidates[0]
    if match is not None:
        raise AdversarialSuiteError(
            f"singular target {artifact} must not declare match"
        )
    return value


def _validate_target(value: Any, label: str) -> None:
    if not isinstance(value, Mapping):
        raise AdversarialSuiteError(f"{label} must be an object")
    _required_text(value.get("artifact"), f"{label}.artifact")


def _required_pointer(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value == "/":
        raise AdversarialSuiteError(
            f"{label} must be a non-root JSON pointer beginning with /"
        )
    _pointer_tokens(value)
    return value


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    return tuple(
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer.split("/")[1:]
    )


def _read_pointer(root: dict[str, Any] | list[Any], pointer: str) -> Any:
    current: Any = root
    for token in _pointer_tokens(pointer):
        if isinstance(current, list):
            index = _list_index(token, len(current), pointer)
            current = current[index]
        elif isinstance(current, dict):
            if token not in current:
                raise AdversarialSuiteError(f"pointer {pointer} does not exist")
            current = current[token]
        else:
            raise AdversarialSuiteError(f"pointer {pointer} traverses a scalar")
    return current


def _write_pointer(
    root: dict[str, Any] | list[Any], pointer: str, value: Any
) -> None:
    parent, token = _pointer_parent(root, pointer)
    if isinstance(parent, list):
        parent[_list_index(token, len(parent), pointer)] = value
    elif isinstance(parent, dict):
        if token not in parent:
            raise AdversarialSuiteError(f"pointer {pointer} does not exist")
        parent[token] = value
    else:  # pragma: no cover - guarded by _pointer_parent
        raise AdversarialSuiteError(f"pointer {pointer} has no container parent")


def _delete_pointer(root: dict[str, Any] | list[Any], pointer: str) -> None:
    parent, token = _pointer_parent(root, pointer)
    if isinstance(parent, list):
        del parent[_list_index(token, len(parent), pointer)]
    elif isinstance(parent, dict):
        if token not in parent:
            raise AdversarialSuiteError(f"pointer {pointer} does not exist")
        del parent[token]
    else:  # pragma: no cover - guarded by _pointer_parent
        raise AdversarialSuiteError(f"pointer {pointer} has no container parent")


def _pointer_parent(
    root: dict[str, Any] | list[Any], pointer: str
) -> tuple[dict[str, Any] | list[Any], str]:
    tokens = _pointer_tokens(pointer)
    current: Any = root
    for token in tokens[:-1]:
        if isinstance(current, list):
            current = current[_list_index(token, len(current), pointer)]
        elif isinstance(current, dict):
            if token not in current:
                raise AdversarialSuiteError(f"pointer {pointer} does not exist")
            current = current[token]
        else:
            raise AdversarialSuiteError(f"pointer {pointer} traverses a scalar")
    if not isinstance(current, (dict, list)):
        raise AdversarialSuiteError(f"pointer {pointer} has no container parent")
    return current, tokens[-1]


def _list_index(token: str, length: int, pointer: str) -> int:
    if not token.isdigit():
        raise AdversarialSuiteError(
            f"pointer {pointer} uses non-numeric list index {token!r}"
        )
    index = int(token)
    if index >= length:
        raise AdversarialSuiteError(f"pointer {pointer} list index is out of range")
    return index


def _failed_check_names(report: EvaluationReport) -> tuple[str, ...]:
    return tuple(check.name for check in report.checks if not check.passed)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    value = _load_json(path, label)
    if not isinstance(value, dict):
        raise AdversarialSuiteError(f"{label} must be a JSON object: {path}")
    return value


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AdversarialSuiteError(f"cannot read {label} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AdversarialSuiteError(f"invalid JSON in {label} {path}: {exc}") from exc


def _resolve_path(base: Path, raw: str) -> Path:
    candidate = Path(raw)
    return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()


def _path_values(value: Any, label: str) -> tuple[str, ...]:
    raw_values = (value,) if isinstance(value, str) else value
    if not isinstance(raw_values, (list, tuple)) or not raw_values:
        raise AdversarialSuiteError(f"{label} must be a path or non-empty path array")
    if any(not isinstance(item, str) or not item for item in raw_values):
        raise AdversarialSuiteError(f"{label} contains an invalid path")
    return tuple(raw_values)


def _load_one(base: Path, value: Any, label: str) -> dict[str, Any]:
    paths = _path_values(value, f"paths.{label}")
    if len(paths) != 1:
        raise AdversarialSuiteError(f"paths.{label} must contain exactly one path")
    return _load_object(_resolve_path(base, paths[0]), label)


def _load_many(base: Path, value: Any, label: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw_path in _path_values(value, f"paths.{label}"):
        path = _resolve_path(base, raw_path)
        value_at_path = _load_json(path, label)
        items = value_at_path if isinstance(value_at_path, list) else [value_at_path]
        if any(not isinstance(item, dict) for item in items):
            raise AdversarialSuiteError(f"{label} must contain only objects: {path}")
        result.extend(items)
    return result


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdversarialSuiteError(f"{label} must be a non-empty string")
    return value


def _string_array(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise AdversarialSuiteError(f"{label} must be a non-empty string array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise AdversarialSuiteError(f"{label} contains an invalid string")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise AdversarialSuiteError(f"{label} contains duplicate values")
    return result


def _string_array_mapping(
    value: Any, label: str
) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping) or not value:
        raise AdversarialSuiteError(f"{label} must be a non-empty object")
    result: dict[str, tuple[str, ...]] = {}
    for key, items in value.items():
        if not isinstance(key, str) or not key.strip():
            raise AdversarialSuiteError(f"{label} contains an invalid key")
        result[key] = _string_array(items, f"{label}.{key}")
    return result


def _string_mapping(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise AdversarialSuiteError(f"{label} must be a non-empty string mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise AdversarialSuiteError(f"{label} contains an invalid key")
        if not isinstance(item, str) or not item.strip():
            raise AdversarialSuiteError(f"{label}.{key} must be a non-empty string")
        result[key] = item
    return result
