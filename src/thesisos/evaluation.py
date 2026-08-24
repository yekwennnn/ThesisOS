"""Deterministic acceptance checks for a ThesisDiff historical replay.

The evaluator intentionally operates on JSON-shaped mappings.  This keeps the
golden replay auditable even when domain model implementations evolve, while
the JSON Schemas remain the serialization contract.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .policy import find_v0_policy_violations, is_evidence_attribution_allowed
from .source_text import normalize_quote_text


@dataclass(frozen=True)
class EvaluationCheck:
    name: str
    passed: bool
    detail: str
    numerator: int | None = None
    denominator: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }
        if self.numerator is not None:
            payload["numerator"] = self.numerator
        if self.denominator is not None:
            payload["denominator"] = self.denominator
        return payload


@dataclass(frozen=True)
class EvaluationReport:
    checks: tuple[EvaluationCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [check.to_dict() for check in self.checks],
        }


class EvaluationCaseError(ValueError):
    """Raised when a replay case manifest cannot be loaded deterministically."""


def evaluate_case_file(case_file: str | Path) -> EvaluationReport:
    """Load a replay manifest and evaluate its complete review/version chain."""

    case_path = Path(case_file).resolve()
    manifest = _load_json_object(case_path)
    required_manifest_fields = (
        "base_analysis_cutoff_at",
        "analysis_cutoff_at",
        "base_source_document_ids",
        "base_evidence_ids",
        "expected_assumption_evidence_ids",
        "expected_citation_text_sha256",
        "critical_financial_evidence_ids",
        "key_fact_evidence_ids",
    )
    missing_manifest_fields = [
        name for name in required_manifest_fields if name not in manifest
    ]
    if missing_manifest_fields:
        raise EvaluationCaseError(
            f"case manifest fields missing: {', '.join(missing_manifest_fields)}"
        )
    paths = _object(manifest.get("paths"))
    required_paths = ("base_thesis", "documents", "evidence", "thesis_diff", "user_review", "accepted_thesis")
    missing = [name for name in required_paths if name not in paths]
    if missing:
        raise EvaluationCaseError(f"case paths missing: {', '.join(missing)}")

    base_thesis = _load_one_from_path_value(case_path.parent, paths["base_thesis"], "base_thesis")
    documents = _load_many_from_path_value(case_path.parent, paths["documents"], "documents")
    evidence = _load_many_from_path_value(case_path.parent, paths["evidence"], "evidence")
    thesis_diff = _load_one_from_path_value(case_path.parent, paths["thesis_diff"], "thesis_diff")
    user_review = _load_one_from_path_value(case_path.parent, paths["user_review"], "user_review")
    accepted_thesis = _load_one_from_path_value(
        case_path.parent, paths["accepted_thesis"], "accepted_thesis"
    )
    if manifest.get("analysis_cutoff_at") != thesis_diff.get("analysis_cutoff_at"):
        raise EvaluationCaseError("manifest analysis_cutoff_at does not match ThesisDiff")
    return evaluate_replay(
        base_thesis=base_thesis,
        documents=documents,
        evidence=evidence,
        thesis_diff=thesis_diff,
        base_analysis_cutoff_at=_required_text_value(
            manifest.get("base_analysis_cutoff_at"), "base_analysis_cutoff_at"
        ),
        base_source_document_ids=_required_string_array(
            manifest.get("base_source_document_ids"), "base_source_document_ids"
        ),
        base_evidence_ids=_required_string_array(
            manifest.get("base_evidence_ids"), "base_evidence_ids"
        ),
        expected_assumption_evidence_ids=_required_string_array_mapping(
            manifest.get("expected_assumption_evidence_ids"),
            "expected_assumption_evidence_ids",
        ),
        expected_citation_text_sha256=_required_string_mapping(
            manifest.get("expected_citation_text_sha256"),
            "expected_citation_text_sha256",
        ),
        critical_financial_evidence_ids=_required_string_array(
            manifest.get("critical_financial_evidence_ids"),
            "critical_financial_evidence_ids",
        ),
        key_fact_evidence_ids=_required_string_array(
            manifest.get("key_fact_evidence_ids"), "key_fact_evidence_ids"
        ),
        user_review=user_review,
        accepted_thesis=accepted_thesis,
    )


def evaluate_replay(
    *,
    base_thesis: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    thesis_diff: Mapping[str, Any],
    base_analysis_cutoff_at: str | None = None,
    base_source_document_ids: Iterable[str] = (),
    base_evidence_ids: Iterable[str] = (),
    expected_assumption_evidence_ids: Mapping[str, Iterable[str]] | None = None,
    expected_citation_text_sha256: Mapping[str, str] | None = None,
    critical_financial_evidence_ids: Iterable[str] = (),
    key_fact_evidence_ids: Iterable[str] = (),
    user_review: Mapping[str, Any] | None = None,
    accepted_thesis: Mapping[str, Any] | None = None,
) -> EvaluationReport:
    """Evaluate the README's machine-checkable V0 acceptance conditions.

    The two explicit ID sets come from the hand-reviewed golden case.  They
    prevent an implementation from inflating coverage by simply failing to
    label an inconvenient financial number or key fact in generated output.
    """

    base_source_ids = tuple(base_source_document_ids)
    base_evidence_id_values = tuple(base_evidence_ids)
    expected_mapping = (
        None
        if expected_assumption_evidence_ids is None
        else {
            str(assumption_id): tuple(evidence_ids)
            for assumption_id, evidence_ids in expected_assumption_evidence_ids.items()
        }
    )
    expected_citation_hashes = (
        None
        if expected_citation_text_sha256 is None
        else dict(expected_citation_text_sha256)
    )
    checks: list[EvaluationCheck] = []
    documents_by_id, duplicate_documents = _index_by(documents, "source_document_id")
    evidence_by_id, duplicate_evidence = _index_by(evidence, "evidence_id")
    checks.append(
        _boolean_check(
            "unique_artifact_ids",
            not duplicate_documents and not duplicate_evidence,
            _join_details(
                _duplicate_detail("source_document_id", duplicate_documents),
                _duplicate_detail("evidence_id", duplicate_evidence),
            )
            or "all source-document and evidence IDs are unique",
        )
    )

    base_version = _object(base_thesis.get("version"))
    base_company = _object(base_thesis.get("company"))
    base_assumption_ids = {
        str(item.get("assumption_id"))
        for item in _objects(base_thesis.get("assumptions"))
        if item.get("assumption_id")
    }
    identity_issues: list[str] = []
    if thesis_diff.get("base_thesis_id") != base_thesis.get("thesis_id"):
        identity_issues.append("base_thesis_id does not match the supplied Thesis Card")
    if thesis_diff.get("base_version_id") != base_version.get("version_id"):
        identity_issues.append("base_version_id does not match the supplied Thesis Card")
    if thesis_diff.get("company_id") != base_company.get("company_id"):
        identity_issues.append("company_id does not match the supplied Thesis Card")
    if base_version.get("user_confirmed") is not True:
        identity_issues.append("the replay base Thesis Card is not user-confirmed")
    checks.append(
        _issues_check("base_thesis_identity_and_ownership", identity_issues, "base identity is consistent")
    )
    if base_analysis_cutoff_at is not None or base_source_ids or base_evidence_id_values:
        checks.append(
            _evaluate_base_replay_boundary(
                base_thesis=base_thesis,
                documents_by_id=documents_by_id,
                evidence_by_id=evidence_by_id,
                base_analysis_cutoff_at=base_analysis_cutoff_at,
                analysis_cutoff_at=thesis_diff.get("analysis_cutoff_at"),
                base_source_document_ids=base_source_ids,
                base_evidence_ids=base_evidence_id_values,
            )
        )

    referenced_evidence_ids = _collect_id_values(thesis_diff, "evidence_ids")
    reference_issues: list[str] = []
    for evidence_id in sorted(referenced_evidence_ids):
        if evidence_id not in evidence_by_id:
            reference_issues.append(f"unknown evidence_id {evidence_id}")
    for document_id in _strings(thesis_diff.get("source_document_ids")):
        if document_id not in documents_by_id:
            reference_issues.append(f"unknown source_document_id {document_id}")
    checks.append(
        _issues_check("referential_integrity", reference_issues, "all Diff references resolve")
    )

    cutoff = _parse_datetime(thesis_diff.get("analysis_cutoff_at"))
    temporal_issues: list[str] = []
    if cutoff is None:
        temporal_issues.append("analysis_cutoff_at is missing, invalid, or timezone-naive")
    used_document_ids = set(documents_by_id)
    for evidence_id, item in evidence_by_id.items():
        available = _parse_datetime(item.get("available_as_of"))
        if cutoff is not None and (available is None or available > cutoff):
            temporal_issues.append(f"evidence {evidence_id} was not available by the analysis cutoff")
        for citation in _objects(item.get("citations")):
            document_id = citation.get("source_document_id")
            if isinstance(document_id, str):
                used_document_ids.add(document_id)
                document = documents_by_id.get(document_id)
                document_available = (
                    _parse_datetime(document.get("publicly_available_at"))
                    if document is not None
                    else None
                )
                if (
                    available is not None
                    and document_available is not None
                    and available < document_available
                ):
                    temporal_issues.append(
                        f"evidence {evidence_id} predates cited document {document_id}"
                    )
    for document_id in sorted(used_document_ids):
        document = documents_by_id.get(document_id)
        if document is None:
            continue
        available = _parse_datetime(document.get("publicly_available_at"))
        if cutoff is not None and (available is None or available > cutoff):
            temporal_issues.append(f"document {document_id} was not public by the analysis cutoff")
    checks.append(_issues_check("future_information_leakage", temporal_issues, "future leakage is zero"))

    provenance_issues: list[str] = []
    for evidence_id, item in sorted(evidence_by_id.items()):
        content_class = item.get("content_class")
        attribution = item.get("attribution")
        if not is_evidence_attribution_allowed(content_class, attribution):
            provenance_issues.append(
                f"evidence {evidence_id} has attribution {attribution!r} "
                f"incompatible with content class {content_class!r}"
            )
    checks.append(
        _issues_check("content_class_integrity", provenance_issues, "facts, opinions, judgments, and inferences stay distinct")
    )

    verification_issues = [
        f"evidence {evidence_id} has verification_status {item.get('verification_status')!r}"
        for evidence_id, item in sorted(evidence_by_id.items())
        if item.get("verification_status") != "verified"
    ]
    checks.append(
        _issues_check(
            "evidence_verification_gate",
            verification_issues,
            "every evidence item in the replay is explicitly verified",
        )
    )

    cited_and_locatable: set[str] = set()
    citation_issues: list[str] = []
    for evidence_id in sorted(
        set(evidence_by_id)
        | referenced_evidence_ids
        | set(key_fact_evidence_ids)
        | set(critical_financial_evidence_ids)
    ):
        item = evidence_by_id.get(evidence_id)
        if item is None:
            citation_issues.append(f"expected evidence {evidence_id} is missing")
            continue
        citations = _objects(item.get("citations"))
        valid_count = 0
        for citation in citations:
            document_id = citation.get("source_document_id")
            document = documents_by_id.get(str(document_id))
            locator = _object(citation.get("locator"))
            if document is None:
                citation_issues.append(f"evidence {evidence_id} cites unknown document {document_id}")
                continue
            snapshot = _object(document.get("snapshot"))
            if citation.get("snapshot_sha256") != snapshot.get("sha256"):
                citation_issues.append(f"evidence {evidence_id} citation snapshot does not match {document_id}")
                continue
            if not _locator_is_precise(locator):
                citation_issues.append(f"evidence {evidence_id} has an imprecise citation locator")
                continue
            if (
                citation.get("quotation_mode") == "table_value"
                and locator.get("kind") != "table"
            ):
                citation_issues.append(
                    f"evidence {evidence_id} uses table_value without a table locator"
                )
                continue
            locator_page = locator.get("page")
            if locator.get("kind") in {"page", "table"} and locator_page is not None:
                if (
                    not isinstance(locator_page, int)
                    or isinstance(locator_page, bool)
                    or locator_page < 1
                ):
                    citation_issues.append(
                        f"evidence {evidence_id} citation has an invalid PDF page"
                    )
                    continue
                page_count = document.get("page_count")
                if (
                    isinstance(page_count, int)
                    and not isinstance(page_count, bool)
                    and locator_page > page_count
                ):
                    citation_issues.append(
                        f"evidence {evidence_id} citation page {locator_page} exceeds "
                        f"document {document_id} page_count {page_count}"
                    )
                    continue
            if not str(citation.get("quoted_text") or "").strip():
                citation_issues.append(f"evidence {evidence_id} citation has no quoted text")
                continue
            valid_count += 1
        if valid_count:
            cited_and_locatable.add(evidence_id)
    checks.append(
        _issues_check("citation_snapshot_and_locator_integrity", citation_issues, "all used citations bind to an exact snapshot and locator")
    )

    if expected_citation_hashes is not None:
        citation_anchor_issues: list[str] = []
        actual_citations: dict[str, Mapping[str, Any]] = {}
        duplicate_citation_ids: set[str] = set()
        for evidence_item in evidence_by_id.values():
            for citation in _objects(evidence_item.get("citations")):
                citation_id = citation.get("citation_id")
                if not isinstance(citation_id, str) or not citation_id:
                    citation_anchor_issues.append("citation without a stable citation_id")
                    continue
                if citation_id in actual_citations:
                    duplicate_citation_ids.add(citation_id)
                actual_citations[citation_id] = citation
        if duplicate_citation_ids:
            citation_anchor_issues.append(
                "duplicate citation IDs: " + ", ".join(sorted(duplicate_citation_ids))
            )
        expected_ids = set(expected_citation_hashes)
        actual_ids = set(actual_citations)
        if expected_ids != actual_ids:
            missing = expected_ids - actual_ids
            unexpected = actual_ids - expected_ids
            if missing:
                citation_anchor_issues.append(
                    "missing curator-anchored citations: " + ", ".join(sorted(missing))
                )
            if unexpected:
                citation_anchor_issues.append(
                    "unanchored citations: " + ", ".join(sorted(unexpected))
                )
        for citation_id in sorted(expected_ids & actual_ids):
            quoted_text = actual_citations[citation_id].get("quoted_text")
            if not isinstance(quoted_text, str):
                citation_anchor_issues.append(
                    f"citation {citation_id} has no quoted_text to hash"
                )
                continue
            actual_hash = hashlib.sha256(
                normalize_quote_text(quoted_text).encode("utf-8")
            ).hexdigest()
            if actual_hash != expected_citation_hashes[citation_id]:
                citation_anchor_issues.append(
                    f"citation {citation_id} quoted_text differs from the curator-approved anchor"
                )
        checks.append(
            _issues_check(
                "curator_citation_text_anchor",
                citation_anchor_issues,
                "every normalized citation text matches its manually source-verified golden hash",
            )
        )

    critical_ids = set(critical_financial_evidence_ids)
    critical_covered = len(critical_ids & cited_and_locatable)
    checks.append(
        EvaluationCheck(
            name="critical_financial_source_coverage",
            passed=critical_covered == len(critical_ids),
            detail=f"{critical_covered}/{len(critical_ids)} hand-labelled critical financial facts are traceable",
            numerator=critical_covered,
            denominator=len(critical_ids),
        )
    )
    key_ids = set(key_fact_evidence_ids)
    key_covered = len(key_ids & cited_and_locatable)
    checks.append(
        EvaluationCheck(
            name="key_fact_traceability",
            passed=key_covered == len(key_ids),
            detail=f"{key_covered}/{len(key_ids)} hand-labelled key facts are traceable",
            numerator=key_covered,
            denominator=len(key_ids),
        )
    )

    mapping_issues: list[str] = []
    changes = _objects(thesis_diff.get("assumption_changes"))
    changed_ids = [str(item.get("assumption_id")) for item in changes if item.get("assumption_id")]
    if len(changed_ids) != len(set(changed_ids)):
        mapping_issues.append("assumption_changes contains duplicate assumption IDs")
    missing_assumptions = base_assumption_ids - set(changed_ids)
    unknown_assumptions = set(changed_ids) - base_assumption_ids
    if missing_assumptions:
        mapping_issues.append(f"assumptions not assessed: {', '.join(sorted(missing_assumptions))}")
    if unknown_assumptions:
        mapping_issues.append(f"unknown assumptions assessed: {', '.join(sorted(unknown_assumptions))}")
    for change in changes:
        impact = change.get("impact")
        ids = set(_strings(change.get("evidence_ids")))
        if impact != "insufficient_evidence" and not ids:
            mapping_issues.append(f"assumption {change.get('assumption_id')} has a conclusion without evidence")
    checks.append(
        _issues_check("assumption_mapping_coverage", mapping_issues, "every base assumption is assessed exactly once")
    )
    if expected_mapping is not None:
        golden_mapping_issues: list[str] = []
        expected_ids = set(expected_mapping)
        if expected_ids != base_assumption_ids:
            missing_expected = base_assumption_ids - expected_ids
            unknown_expected = expected_ids - base_assumption_ids
            if missing_expected:
                golden_mapping_issues.append(
                    "golden evidence mapping missing assumptions: "
                    + ", ".join(sorted(missing_expected))
                )
            if unknown_expected:
                golden_mapping_issues.append(
                    "golden evidence mapping has unknown assumptions: "
                    + ", ".join(sorted(unknown_expected))
                )
        actual_mapping = {
            str(item.get("assumption_id")): set(_strings(item.get("evidence_ids")))
            for item in changes
            if item.get("assumption_id")
        }
        for assumption_id in sorted(expected_ids | set(actual_mapping)):
            expected_evidence = set(expected_mapping.get(assumption_id, ()))
            actual_evidence = actual_mapping.get(assumption_id, set())
            if expected_evidence != actual_evidence:
                golden_mapping_issues.append(
                    f"assumption {assumption_id} evidence differs from golden mapping: "
                    f"expected {sorted(expected_evidence)}, got {sorted(actual_evidence)}"
                )
        checks.append(
            _issues_check(
                "golden_assumption_evidence_mapping",
                golden_mapping_issues,
                "every assumption uses exactly its curator-approved evidence set",
            )
        )

    counter = _object(thesis_diff.get("targeted_counter_case"))
    attacked_ids = set(_strings(counter.get("attacked_assumption_ids")))
    counter_issues: list[str] = []
    if not str(counter.get("argument") or "").strip():
        counter_issues.append("targeted counter-case has no argument")
    if not attacked_ids:
        counter_issues.append("targeted counter-case attacks no assumption")
    elif not attacked_ids <= base_assumption_ids:
        counter_issues.append("targeted counter-case attacks an unknown assumption")
    if not str(counter.get("why_plausible") or "").strip():
        counter_issues.append("targeted counter-case does not explain why it is plausible")
    checks.append(
        _issues_check("targeted_counter_case", counter_issues, "counter-case directly attacks the supplied thesis")
    )

    questions = _objects(thesis_diff.get("follow_up_questions"))
    question_issues: list[str] = []
    if not 1 <= len(questions) <= 3:
        question_issues.append("follow_up_questions must contain one to three questions")
    for item in questions:
        if not str(item.get("information_value") or "").strip() or not str(item.get("evidence_needed") or "").strip():
            question_issues.append(f"question {item.get('question_id')} is not decision-informative")
    checks.append(
        _issues_check("high_information_follow_up_questions", question_issues, "one to three decision-informative questions are present")
    )

    patch = _object(thesis_diff.get("proposed_patch"))
    proposed = _object(patch.get("proposed_thesis"))
    proposed_version = _object(proposed.get("version"))
    proposed_company = _object(proposed.get("company"))
    patch_issues: list[str] = []
    if patch.get("patch_status") != "pending_user_review":
        patch_issues.append("proposed patch is not pending user review")
    if patch.get("base_thesis_id") != base_thesis.get("thesis_id"):
        patch_issues.append("patch base_thesis_id does not match")
    if patch.get("base_version_id") != base_version.get("version_id"):
        patch_issues.append("patch base_version_id does not match")
    if proposed.get("thesis_id") != base_thesis.get("thesis_id"):
        patch_issues.append("proposed thesis does not preserve thesis_id")
    if proposed_company.get("company_id") != base_company.get("company_id"):
        patch_issues.append("proposed thesis does not preserve company_id")
    if proposed_version.get("supersedes") != base_version.get("version_id"):
        patch_issues.append("proposed thesis does not supersede the base version")
    if proposed_version.get("version_id") == base_version.get("version_id"):
        patch_issues.append("proposed thesis reuses the base version_id")
    if proposed_version.get("user_confirmed") is not False:
        patch_issues.append("AI-proposed thesis must remain unconfirmed")
    checks.append(
        _issues_check("pending_patch_user_control", patch_issues, "AI output remains a review-pending patch")
    )

    policy_violations = find_v0_policy_violations(
        thesis_diff, scan_generated_text=True
    )
    checks.append(
        _boolean_check(
            "no_v0_trading_recommendation",
            not policy_violations,
            "no trading instructions, ratings, target prices, or position sizing"
            if not policy_violations
            else "policy violations: "
            + "; ".join(
                f"{violation.code} at {violation.path or '$'}"
                for violation in policy_violations
            ),
        )
    )
    if user_review is not None or accepted_thesis is not None:
        checks.append(
            _evaluate_review_chain(
                base_thesis=base_thesis,
                thesis_diff=thesis_diff,
                user_review=user_review,
                accepted_thesis=accepted_thesis,
            )
        )
    return EvaluationReport(tuple(checks))


def _evaluate_review_chain(
    *,
    base_thesis: Mapping[str, Any],
    thesis_diff: Mapping[str, Any],
    user_review: Mapping[str, Any] | None,
    accepted_thesis: Mapping[str, Any] | None,
) -> EvaluationCheck:
    issues: list[str] = []
    if user_review is None:
        return EvaluationCheck("user_review_version_chain", False, "user review is missing")
    decision = user_review.get("decision")
    for review_key, diff_key in (
        ("thesis_diff_id", "thesis_diff_id"),
        ("company_id", "company_id"),
        ("base_thesis_id", "base_thesis_id"),
        ("base_version_id", "base_version_id"),
    ):
        if user_review.get(review_key) != thesis_diff.get(diff_key):
            issues.append(f"review {review_key} does not match ThesisDiff")

    promotes = decision in {"accept", "accept_with_edits"}
    if promotes and accepted_thesis is None:
        issues.append(f"decision {decision} requires an accepted Thesis Card")
    if not promotes and accepted_thesis is not None:
        issues.append(f"decision {decision} must not create a Thesis Card version")
    if accepted_thesis is not None:
        accepted_version = _object(accepted_thesis.get("version"))
        base_version = _object(base_thesis.get("version"))
        accepted_company = _object(accepted_thesis.get("company"))
        base_company = _object(base_thesis.get("company"))
        if accepted_thesis.get("thesis_id") != base_thesis.get("thesis_id"):
            issues.append("accepted thesis does not preserve thesis_id")
        if accepted_company.get("company_id") != base_company.get("company_id"):
            issues.append("accepted thesis does not preserve company_id")
        if accepted_version.get("supersedes") != base_version.get("version_id"):
            issues.append("accepted thesis does not supersede the base version")
        if accepted_version.get("user_confirmed") is not True:
            issues.append("accepted thesis is not user-confirmed")

        if decision == "accept":
            proposed = _object(_object(thesis_diff.get("proposed_patch")).get("proposed_thesis"))
            expected = deepcopy(dict(proposed))
            expected_version = expected.get("version")
            if isinstance(expected_version, dict):
                expected_version["user_confirmed"] = True
                expected_version["updated_at"] = user_review.get("reviewed_at")
            if accepted_thesis != expected:
                issues.append("accepted thesis differs from the proposal beyond confirmation metadata")
        elif decision == "accept_with_edits" and accepted_thesis != user_review.get("reviewed_thesis"):
            issues.append("accepted thesis is not the complete user-edited thesis")
    return _issues_check(
        "user_review_version_chain",
        issues,
        "the explicit user review produces exactly the expected immutable version",
    )


def _evaluate_base_replay_boundary(
    *,
    base_thesis: Mapping[str, Any],
    documents_by_id: Mapping[str, Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    base_analysis_cutoff_at: str | None,
    analysis_cutoff_at: Any,
    base_source_document_ids: Sequence[str],
    base_evidence_ids: Sequence[str],
) -> EvaluationCheck:
    """Prove that the immutable V1 could have existed at its own cutoff."""

    issues: list[str] = []
    base_cutoff = _parse_datetime(base_analysis_cutoff_at)
    analysis_cutoff = _parse_datetime(analysis_cutoff_at)
    if base_cutoff is None:
        issues.append(
            "base_analysis_cutoff_at is missing, invalid, or timezone-naive"
        )
    if (
        base_cutoff is not None
        and analysis_cutoff is not None
        and base_cutoff > analysis_cutoff
    ):
        issues.append("base_analysis_cutoff_at follows analysis_cutoff_at")

    declared_document_ids = set(base_source_document_ids)
    declared_evidence_ids = set(base_evidence_ids)
    if len(declared_document_ids) != len(base_source_document_ids):
        issues.append("base_source_document_ids contains duplicates")
    if len(declared_evidence_ids) != len(base_evidence_ids):
        issues.append("base_evidence_ids contains duplicates")
    if not declared_document_ids:
        issues.append("base_source_document_ids must not be empty")
    if not declared_evidence_ids:
        issues.append("base_evidence_ids must not be empty")

    for document_id in sorted(declared_document_ids):
        document = documents_by_id.get(document_id)
        if document is None:
            issues.append(f"unknown baseline source document {document_id}")
            continue
        available = _parse_datetime(document.get("publicly_available_at"))
        if available is None:
            issues.append(
                f"baseline document {document_id} has invalid publicly_available_at"
            )
        elif base_cutoff is not None and available > base_cutoff:
            issues.append(
                f"baseline document {document_id} was not public by the base cutoff"
            )

    thesis_evidence_ids = _collect_id_values(base_thesis, "evidence_ids")
    for evidence_id in sorted(thesis_evidence_ids):
        if evidence_id not in evidence_by_id:
            issues.append(f"base thesis references unknown evidence {evidence_id}")
        if evidence_id not in declared_evidence_ids:
            issues.append(
                f"base thesis evidence {evidence_id} is not declared in base_evidence_ids"
            )

    base_company_id = _object(base_thesis.get("company")).get("company_id")
    for evidence_id in sorted(declared_evidence_ids | thesis_evidence_ids):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            if evidence_id in declared_evidence_ids:
                issues.append(f"unknown baseline evidence {evidence_id}")
            continue
        if evidence.get("company_id") != base_company_id:
            issues.append(f"baseline evidence {evidence_id} belongs to another company")
        evidence_available = _parse_datetime(evidence.get("available_as_of"))
        if evidence_available is None:
            issues.append(f"baseline evidence {evidence_id} has invalid available_as_of")
        elif base_cutoff is not None and evidence_available > base_cutoff:
            issues.append(
                f"baseline evidence {evidence_id} was not available by the base cutoff"
            )
        for citation in _objects(evidence.get("citations")):
            document_id = citation.get("source_document_id")
            if not isinstance(document_id, str) or not document_id:
                issues.append(
                    f"baseline evidence {evidence_id} has a citation without a source document"
                )
                continue
            if document_id not in declared_document_ids:
                issues.append(
                    f"baseline evidence {evidence_id} cites non-baseline document {document_id}"
                )
            document = documents_by_id.get(document_id)
            if document is None:
                issues.append(
                    f"baseline evidence {evidence_id} cites unknown document {document_id}"
                )
                continue
            document_available = _parse_datetime(document.get("publicly_available_at"))
            if (
                base_cutoff is not None
                and (document_available is None or document_available > base_cutoff)
            ):
                issues.append(
                    f"document {document_id} cited by baseline evidence {evidence_id} "
                    "was not public by the base cutoff"
                )
            if (
                evidence_available is not None
                and document_available is not None
                and evidence_available < document_available
            ):
                issues.append(
                    f"baseline evidence {evidence_id} predates cited document {document_id}"
                )

    version = _object(base_thesis.get("version"))
    as_of_date = _parse_date(version.get("as_of_date"))
    created_at = _parse_datetime(version.get("created_at"))
    updated_at = _parse_datetime(version.get("updated_at"))
    if as_of_date is None:
        issues.append("base thesis version.as_of_date is missing or invalid")
    if created_at is None:
        issues.append("base thesis version.created_at is missing, invalid, or timezone-naive")
    if updated_at is None:
        issues.append("base thesis version.updated_at is missing, invalid, or timezone-naive")
    if base_cutoff is not None:
        if as_of_date is not None and as_of_date > base_cutoff.date():
            issues.append("base thesis as_of_date follows the base cutoff")
        if created_at is not None and created_at > base_cutoff:
            issues.append("base thesis created_at follows the base cutoff")
        if updated_at is not None and updated_at > base_cutoff:
            issues.append("base thesis updated_at follows the base cutoff")
    if as_of_date is not None and created_at is not None and as_of_date > created_at.date():
        issues.append("base thesis as_of_date follows created_at")
    if created_at is not None and updated_at is not None and created_at > updated_at:
        issues.append("base thesis created_at follows updated_at")

    return _issues_check(
        "base_replay_cutoff_integrity",
        issues,
        "the baseline sources, evidence, and confirmed V1 existed by the base cutoff",
    )


def _index_by(
    items: Sequence[Mapping[str, Any]], key: str
) -> tuple[dict[str, Mapping[str, Any]], set[str]]:
    result: dict[str, Mapping[str, Any]] = {}
    duplicates: set[str] = set()
    for item in items:
        value = item.get(key)
        if not isinstance(value, str) or not value:
            duplicates.add("<missing>")
            continue
        if value in result:
            duplicates.add(value)
        result[value] = item
    return result, duplicates


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvaluationCaseError(f"cannot read replay artifact {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EvaluationCaseError(f"invalid JSON replay artifact {path}: {exc}") from exc


def _load_json_object(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise EvaluationCaseError(f"expected JSON object: {path}")
    return value


def _required_text_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationCaseError(f"case {label} must be a non-empty string")
    return value


def _required_string_array(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise EvaluationCaseError(f"case {label} must be a non-empty string array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise EvaluationCaseError(f"case {label} contains an invalid string")
    if len(set(value)) != len(value):
        raise EvaluationCaseError(f"case {label} contains duplicate values")
    return tuple(value)


def _required_string_array_mapping(
    value: Any, label: str
) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping) or not value:
        raise EvaluationCaseError(
            f"case {label} must be a non-empty object of string arrays"
        )
    result: dict[str, tuple[str, ...]] = {}
    for key, items in value.items():
        if not isinstance(key, str) or not key.strip():
            raise EvaluationCaseError(f"case {label} contains an invalid key")
        result[key] = _required_string_array(items, f"{label}.{key}")
    return result


def _required_string_mapping(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise EvaluationCaseError(f"case {label} must be a non-empty string mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise EvaluationCaseError(f"case {label} contains an invalid key")
        if not isinstance(item, str) or not item.strip():
            raise EvaluationCaseError(f"case {label}.{key} must be a non-empty string")
        result[key] = item
    return result


def _artifact_paths(base: Path, value: Any, label: str) -> tuple[Path, ...]:
    raw_paths = (value,) if isinstance(value, str) else value
    if not isinstance(raw_paths, (list, tuple)) or not raw_paths:
        raise EvaluationCaseError(f"case paths.{label} must be a path or non-empty path array")
    paths: list[Path] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise EvaluationCaseError(f"case paths.{label} contains an invalid path")
        paths.append((base / raw_path).resolve())
    return tuple(paths)


def _load_one_from_path_value(base: Path, value: Any, label: str) -> dict[str, Any]:
    paths = _artifact_paths(base, value, label)
    if len(paths) != 1:
        raise EvaluationCaseError(f"case paths.{label} must resolve to exactly one artifact")
    return _load_json_object(paths[0])


def _load_many_from_path_value(base: Path, value: Any, label: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _artifact_paths(base, value, label):
        value_at_path = _load_json(path)
        raw_items = value_at_path if isinstance(value_at_path, list) else [value_at_path]
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise EvaluationCaseError(f"expected object at {path}[{index}]")
            items.append(item)
    return items


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _objects(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _collect_id_values(value: Any, key_name: str) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == key_name:
                found.update(_strings(child))
            else:
                found.update(_collect_id_values(child, key_name))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_collect_id_values(child, key_name))
    return found


def _locator_is_precise(locator: Mapping[str, Any]) -> bool:
    kind = locator.get("kind")
    if kind == "page":
        return isinstance(locator.get("page"), int) and not isinstance(locator.get("page"), bool) and locator["page"] > 0
    if kind == "paragraph":
        return _positive_range(locator, "paragraph_start", "paragraph_end")
    if kind == "line_range":
        return _positive_range(locator, "line_start", "line_end")
    if kind == "table":
        return bool(str(locator.get("table") or "").strip())
    if kind == "section":
        return bool(str(locator.get("section") or "").strip())
    return False


def _positive_range(locator: Mapping[str, Any], start_key: str, end_key: str) -> bool:
    start, end = locator.get(start_key), locator.get(end_key)
    return (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and 0 < start <= end
    )


def _issues_check(name: str, issues: list[str], success: str) -> EvaluationCheck:
    return EvaluationCheck(name=name, passed=not issues, detail=success if not issues else "; ".join(issues))


def _boolean_check(name: str, passed: bool, detail: str) -> EvaluationCheck:
    return EvaluationCheck(name=name, passed=passed, detail=detail)


def _duplicate_detail(label: str, duplicates: set[str]) -> str:
    return "" if not duplicates else f"duplicate or missing {label}: {', '.join(sorted(duplicates))}"


def _join_details(*parts: str) -> str:
    return "; ".join(part for part in parts if part)
