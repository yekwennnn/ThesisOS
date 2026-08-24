"""Cross-object validation for the ThesisOS V0 domain contract."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Mapping, TypeVar

from .models import (
    Attribution,
    ChangeOperation,
    ChangeStatus,
    ChangeTargetType,
    Citation,
    ComparisonAssessment,
    Confidence,
    DocumentType,
    Evidence,
    EvidenceConfidence,
    EvidenceKind,
    LocatorKind,
    ManagementAssessment,
    MediaType,
    ProposedPatch,
    QuotationMode,
    ReportingPeriodKind,
    ResearchStatus,
    ReviewDecision,
    SCHEMA_VERSION,
    SourceClass,
    SourceDocument,
    StableEnum,
    ThesisCard,
    ThesisDiff,
    UserReview,
    ValuationStatus,
    VerificationStatus,
)
from .policy import (
    allowed_attributions_for_content_class,
    find_v0_policy_violations,
    is_evidence_attribution_allowed,
)
from .temporal import (
    TemporalValidationError,
    ensure_timezone_aware,
    is_timezone_aware,
    reject_future_documents,
)


ValidatedT = TypeVar("ValidatedT")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_ID_RE = re.compile(
    r"^(?!(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$))"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?$",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[+-]?(?:\d[\d,]*(?:\.\d+)?%?)(?![A-Za-z0-9_])")
class DomainValidationError(ValueError):
    """A domain failure that preserves every issue found in one pass."""

    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(issues) or ("domain validation failed",)
        super().__init__("; ".join(self.issues))


def _finish(value: ValidatedT, issues: list[str]) -> ValidatedT:
    if issues:
        raise DomainValidationError(issues)
    return value


def _required_text(value: object, field_name: str, issues: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(f"{field_name} must be non-empty")


def _stable_id(value: object, field_name: str, issues: list[str]) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or _STABLE_ID_RE.fullmatch(value) is None
    ):
        issues.append(f"{field_name} must be a stable machine ID")


def _schema_version(value: object, field_name: str, issues: list[str]) -> None:
    if value != SCHEMA_VERSION:
        issues.append(f"{field_name} must equal {SCHEMA_VERSION}")


def _aware(value: object, field_name: str, issues: list[str]) -> bool:
    if not isinstance(value, datetime):
        issues.append(f"{field_name} must be a datetime")
        return False
    try:
        ensure_timezone_aware(value, field_name)
    except TemporalValidationError as exc:
        issues.extend(exc.issues)
        return False
    return True


def _stable_enum(
    value: object, enum_type: type[StableEnum], field_name: str, issues: list[str]
) -> None:
    if not isinstance(value, enum_type):
        issues.append(f"{field_name} must be a {enum_type.__name__} machine enum")


def _unique(values: Iterable[object], field_name: str, issues: list[str]) -> None:
    materialized = tuple(values)
    try:
        unique_count = len(set(materialized))
    except TypeError:
        unique_count = len({repr(value) for value in materialized})
    if unique_count != len(materialized):
        issues.append(f"{field_name} must contain unique items")


def _ids(
    values: tuple[str, ...],
    field_name: str,
    issues: list[str],
    *,
    require_one: bool = False,
) -> None:
    if require_one and not values:
        issues.append(f"{field_name} must contain at least one item")
    _unique(values, field_name, issues)
    for index, value in enumerate(values):
        _stable_id(value, f"{field_name}[{index}]", issues)


def _merge_validation(prefix: str, issues: list[str], validator: Any, *args: Any) -> None:
    try:
        validator(*args)
    except DomainValidationError as exc:
        issues.extend(f"{prefix}: {issue}" for issue in exc.issues)


def validate_source_document(document: SourceDocument) -> SourceDocument:
    """Validate one immutable source-document record."""

    issues: list[str] = []
    label = f"document {document.source_document_id}"
    _schema_version(document.schema_version, f"{label}.schema_version", issues)
    _stable_id(document.source_document_id, f"{label}.source_document_id", issues)
    _stable_id(document.company_id, f"{label}.company_id", issues)
    _required_text(document.title, f"{label}.title", issues)
    _stable_enum(document.document_type, DocumentType, f"{label}.document_type", issues)
    _stable_enum(document.media_type, MediaType, f"{label}.media_type", issues)
    _stable_enum(document.source_class, SourceClass, f"{label}.source_class", issues)
    _required_text(document.language, f"{label}.language", issues)
    _stable_enum(
        document.reporting_period.kind,
        ReportingPeriodKind,
        f"{label}.reporting_period.kind",
        issues,
    )
    _required_text(document.reporting_period.label, f"{label}.reporting_period.label", issues)

    available_is_aware = _aware(
        document.publicly_available_at, f"{label}.publicly_available_at", issues
    )
    ingested_is_aware = _aware(document.ingested_at, f"{label}.ingested_at", issues)
    if (
        available_is_aware
        and ingested_is_aware
        and document.ingested_at < document.publicly_available_at
    ):
        issues.append(f"{label}.ingested_at cannot precede publicly_available_at")
    if available_is_aware and document.published_on > document.publicly_available_at.date():
        issues.append(f"{label}.published_on cannot follow publicly_available_at")

    start = document.reporting_period.start_on
    end = document.reporting_period.end_on
    if (start is None) != (end is None):
        issues.append(f"{label}.reporting_period must provide both start_on and end_on")
    if start is not None and end is not None and start > end:
        issues.append(f"{label}.reporting_period.start_on cannot follow end_on")

    if not _SHA256_RE.fullmatch(document.snapshot.sha256):
        issues.append(f"{label}.snapshot.sha256 must be 64 lowercase hexadecimal characters")
    _required_text(document.snapshot.storage_uri, f"{label}.snapshot.storage_uri", issues)
    if (
        not isinstance(document.snapshot.byte_size, int)
        or isinstance(document.snapshot.byte_size, bool)
        or document.snapshot.byte_size < 1
    ):
        issues.append(f"{label}.snapshot.byte_size must be a positive integer")
    if document.issuer_or_author is not None:
        _required_text(document.issuer_or_author, f"{label}.issuer_or_author", issues)
    if document.original_uri is not None:
        _required_text(document.original_uri, f"{label}.original_uri", issues)
    if document.page_count is not None and (
        not isinstance(document.page_count, int)
        or isinstance(document.page_count, bool)
        or document.page_count < 1
    ):
        issues.append(f"{label}.page_count must be a positive integer when provided")
    return _finish(document, issues)


def validate_citation(
    citation: Citation, documents: Mapping[str, SourceDocument]
) -> Citation:
    """Validate a snapshot-bound citation and its exact locator variant."""

    issues: list[str] = []
    label = f"citation {citation.citation_id}"
    _schema_version(citation.schema_version, f"{label}.schema_version", issues)
    _stable_id(citation.citation_id, f"{label}.citation_id", issues)
    _stable_id(citation.source_document_id, f"{label}.source_document_id", issues)
    _stable_enum(citation.quotation_mode, QuotationMode, f"{label}.quotation_mode", issues)
    _stable_enum(citation.locator.kind, LocatorKind, f"{label}.locator.kind", issues)
    if (
        citation.quotation_mode == QuotationMode.TABLE_VALUE
        and citation.locator.kind != LocatorKind.TABLE
    ):
        issues.append(f"{label}.locator.kind must be table for table_value quotations")
    _required_text(citation.quoted_text, f"{label}.quoted_text", issues)
    if not _SHA256_RE.fullmatch(citation.snapshot_sha256):
        issues.append(f"{label}.snapshot_sha256 must be 64 lowercase hexadecimal characters")

    document = documents.get(citation.source_document_id)
    if document is None:
        issues.append(f"{label} references unknown source document {citation.source_document_id}")
    elif citation.snapshot_sha256 != document.snapshot.sha256:
        issues.append(f"{label}.snapshot_sha256 does not match the source snapshot")

    locator = citation.locator
    optional_fields = {
        "page": locator.page,
        "page_label": locator.page_label,
        "section": locator.section,
        "paragraph_start": locator.paragraph_start,
        "paragraph_end": locator.paragraph_end,
        "table": locator.table,
        "row": locator.row,
        "column": locator.column,
        "subsection": locator.subsection,
        "line_start": locator.line_start,
        "line_end": locator.line_end,
    }
    allowed: dict[LocatorKind, set[str]] = {
        LocatorKind.PAGE: {"page", "page_label", "section"},
        LocatorKind.PARAGRAPH: {"paragraph_start", "paragraph_end", "section"},
        LocatorKind.TABLE: {"table", "page", "row", "column"},
        LocatorKind.SECTION: {"section", "subsection"},
        LocatorKind.LINE_RANGE: {"line_start", "line_end"},
    }
    if isinstance(locator.kind, LocatorKind):
        for field_name, value in optional_fields.items():
            if value is not None and field_name not in allowed[locator.kind]:
                issues.append(
                    f"{label}.locator.{field_name} is not allowed for kind {locator.kind.value}"
                )

    def positive(value: object, field_name: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            issues.append(f"{label}.locator.{field_name} must be a positive integer")

    if locator.kind == LocatorKind.PAGE:
        positive(locator.page, "page")
    elif locator.kind == LocatorKind.PARAGRAPH:
        positive(locator.paragraph_start, "paragraph_start")
        positive(locator.paragraph_end, "paragraph_end")
        if (
            isinstance(locator.paragraph_start, int)
            and isinstance(locator.paragraph_end, int)
            and locator.paragraph_start > locator.paragraph_end
        ):
            issues.append(f"{label}.locator.paragraph_start cannot exceed paragraph_end")
    elif locator.kind == LocatorKind.TABLE:
        _required_text(locator.table, f"{label}.locator.table", issues)
        if locator.page is not None:
            positive(locator.page, "page")
    elif locator.kind == LocatorKind.SECTION:
        _required_text(locator.section, f"{label}.locator.section", issues)
    elif locator.kind == LocatorKind.LINE_RANGE:
        positive(locator.line_start, "line_start")
        positive(locator.line_end, "line_end")
        if (
            isinstance(locator.line_start, int)
            and isinstance(locator.line_end, int)
            and locator.line_start > locator.line_end
        ):
            issues.append(f"{label}.locator.line_start cannot exceed line_end")
    if (
        document is not None
        and isinstance(document.page_count, int)
        and not isinstance(document.page_count, bool)
        and document.page_count > 0
        and isinstance(locator.page, int)
        and not isinstance(locator.page, bool)
        and locator.page > document.page_count
    ):
        issues.append(
            f"{label}.locator.page {locator.page} exceeds source document "
            f"page_count {document.page_count}"
        )
    return _finish(citation, issues)


def validate_evidence(
    evidence: Evidence,
    documents: Mapping[str, SourceDocument],
    analysis_cutoff_at: datetime | None = None,
) -> Evidence:
    """Validate evidence provenance, locators, and replay availability."""

    issues: list[str] = []
    label = f"evidence {evidence.evidence_id}"
    _schema_version(evidence.schema_version, f"{label}.schema_version", issues)
    _stable_id(evidence.evidence_id, f"{label}.evidence_id", issues)
    _stable_id(evidence.company_id, f"{label}.company_id", issues)
    _required_text(evidence.statement, f"{label}.statement", issues)
    _stable_enum(evidence.content_class, EvidenceKind, f"{label}.content_class", issues)
    _stable_enum(evidence.attribution, Attribution, f"{label}.attribution", issues)
    _stable_enum(evidence.confidence, EvidenceConfidence, f"{label}.confidence", issues)
    _stable_enum(
        evidence.verification_status,
        VerificationStatus,
        f"{label}.verification_status",
        issues,
    )
    available_is_aware = _aware(evidence.available_as_of, f"{label}.available_as_of", issues)
    created_is_aware = _aware(evidence.created_at, f"{label}.created_at", issues)
    if available_is_aware and created_is_aware and evidence.created_at < evidence.available_as_of:
        issues.append(f"{label}.created_at cannot precede available_as_of")

    if (
        isinstance(evidence.content_class, EvidenceKind)
        and isinstance(evidence.attribution, Attribution)
        and not is_evidence_attribution_allowed(evidence.content_class, evidence.attribution)
    ):
        allowed_values = ", ".join(
            sorted(allowed_attributions_for_content_class(evidence.content_class))
        )
        issues.append(
            f"{label}.attribution must be one of {allowed_values} "
            f"for {evidence.content_class.value}"
        )

    if not evidence.citations:
        issues.append(f"{label}.citations must contain at least one citation")
    citation_ids = tuple(citation.citation_id for citation in evidence.citations)
    _unique(citation_ids, f"{label}.citation IDs", issues)
    cited_documents: list[SourceDocument] = []
    for citation in evidence.citations:
        _merge_validation(
            f"{label}.{citation.citation_id}", issues, validate_citation, citation, documents
        )
        document = documents.get(citation.source_document_id)
        if document is not None:
            cited_documents.append(document)
            if document.company_id != evidence.company_id:
                issues.append(
                    f"{label} cites document {document.source_document_id} for another company"
                )
            if (
                available_is_aware
                and document.publicly_available_at.tzinfo is not None
                and document.publicly_available_at.utcoffset() is not None
                and evidence.available_as_of < document.publicly_available_at
            ):
                issues.append(
                    f"{label}.available_as_of cannot precede document "
                    f"{document.source_document_id}.publicly_available_at"
                )

    is_numeric = _NUMBER_RE.search(evidence.statement) is not None
    is_fact = evidence.content_class == EvidenceKind.SOURCE_FACT
    has_locatable = bool(evidence.citations) and all(
        isinstance(citation.locator.kind, LocatorKind) for citation in evidence.citations
    )
    if (is_fact or is_numeric) and not has_locatable:
        issues.append(f"{label} is factual or numeric and requires a locatable citation")

    _unique(evidence.tags, f"{label}.tags", issues)
    if analysis_cutoff_at is not None:
        cutoff_is_aware = _aware(analysis_cutoff_at, "analysis_cutoff_at", issues)
        if cutoff_is_aware and available_is_aware and evidence.available_as_of > analysis_cutoff_at:
            issues.append(f"{label}.available_as_of is after analysis_cutoff_at")
        try:
            reject_future_documents(cited_documents, analysis_cutoff_at)
        except TemporalValidationError as exc:
            issues.extend(f"{label}: {issue}" for issue in exc.issues)
    return _finish(evidence, issues)


def validate_thesis_card(card: ThesisCard) -> ThesisCard:
    """Validate a complete, falsifiable thesis and all internal references."""

    issues: list[str] = []
    label = f"thesis {card.thesis_id}"
    _schema_version(card.schema_version, f"{label}.schema_version", issues)
    _stable_id(card.thesis_id, f"{label}.thesis_id", issues)
    _stable_id(card.company.company_id, f"{label}.company.company_id", issues)
    _required_text(card.company.name, f"{label}.company.name", issues)
    _required_text(card.company.ticker, f"{label}.company.ticker", issues)
    _required_text(card.company.market, f"{label}.company.market", issues)
    _stable_enum(
        card.company.research_status,
        ResearchStatus,
        f"{label}.company.research_status",
        issues,
    )
    _required_text(card.one_sentence_thesis, f"{label}.one_sentence_thesis", issues)

    if not 3 <= len(card.assumptions) <= 7:
        issues.append(f"{label}.assumptions must contain between 3 and 7 items")
    assumption_ids = tuple(item.assumption_id for item in card.assumptions)
    _unique(assumption_ids, f"{label}.assumption IDs", issues)
    assumption_id_set = set(assumption_ids)
    for index, assumption in enumerate(card.assumptions):
        item_label = f"{label}.assumptions[{index}]"
        _stable_id(assumption.assumption_id, f"{item_label}.assumption_id", issues)
        _required_text(assumption.statement, f"{item_label}.statement", issues)
        _ids(assumption.indicator_ids, f"{item_label}.indicator_ids", issues, require_one=True)
        _ids(
            assumption.falsification_condition_ids,
            f"{item_label}.falsification_condition_ids",
            issues,
            require_one=True,
        )

    if not card.key_indicators:
        issues.append(f"{label}.key_indicators must contain at least one item")
    indicator_ids = tuple(item.indicator_id for item in card.key_indicators)
    _unique(indicator_ids, f"{label}.indicator IDs", issues)
    indicator_id_set = set(indicator_ids)
    for index, indicator in enumerate(card.key_indicators):
        item_label = f"{label}.key_indicators[{index}]"
        _stable_id(indicator.indicator_id, f"{item_label}.indicator_id", issues)
        _required_text(indicator.name, f"{item_label}.name", issues)
        _required_text(indicator.why_it_matters, f"{item_label}.why_it_matters", issues)
        _ids(
            indicator.linked_assumption_ids,
            f"{item_label}.linked_assumption_ids",
            issues,
            require_one=True,
        )
        for assumption_id in indicator.linked_assumption_ids:
            if assumption_id not in assumption_id_set:
                issues.append(f"{item_label} references unknown assumption {assumption_id}")

    if not card.falsification_conditions:
        issues.append(f"{label}.falsification_conditions must contain at least one item")
    condition_ids = tuple(item.condition_id for item in card.falsification_conditions)
    _unique(condition_ids, f"{label}.condition IDs", issues)
    condition_id_set = set(condition_ids)
    for index, condition in enumerate(card.falsification_conditions):
        item_label = f"{label}.falsification_conditions[{index}]"
        _stable_id(condition.condition_id, f"{item_label}.condition_id", issues)
        _required_text(condition.statement, f"{item_label}.statement", issues)
        _ids(
            condition.linked_assumption_ids,
            f"{item_label}.linked_assumption_ids",
            issues,
            require_one=True,
        )
        for assumption_id in condition.linked_assumption_ids:
            if assumption_id not in assumption_id_set:
                issues.append(f"{item_label} references unknown assumption {assumption_id}")

    for index, assumption in enumerate(card.assumptions):
        for indicator_id in assumption.indicator_ids:
            if indicator_id not in indicator_id_set:
                issues.append(
                    f"{label}.assumptions[{index}] references unknown indicator {indicator_id}"
                )
        for condition_id in assumption.falsification_condition_ids:
            if condition_id not in condition_id_set:
                issues.append(
                    f"{label}.assumptions[{index}] references unknown falsification condition "
                    f"{condition_id}"
                )

    counter = card.strongest_counter_case
    _required_text(counter.statement, f"{label}.strongest_counter_case.statement", issues)
    _required_text(counter.basis, f"{label}.strongest_counter_case.basis", issues)
    _ids(
        counter.attacked_assumption_ids,
        f"{label}.strongest_counter_case.attacked_assumption_ids",
        issues,
        require_one=True,
    )
    _ids(counter.evidence_ids, f"{label}.strongest_counter_case.evidence_ids", issues)
    for assumption_id in counter.attacked_assumption_ids:
        if assumption_id not in assumption_id_set:
            issues.append(
                f"{label}.strongest_counter_case references unknown assumption {assumption_id}"
            )

    anchor = card.valuation_anchor
    _stable_enum(anchor.status, ValuationStatus, f"{label}.valuation_anchor.status", issues)
    supplied = (
        anchor.valuation_basis,
        anchor.reasonable_range,
        anchor.market_implied_assumptions,
        anchor.sensitive_variables,
    )
    if anchor.status == ValuationStatus.PROVIDED:
        if any(value is None for value in supplied):
            issues.append(f"{label}.valuation_anchor provided status requires all valuation fields")
        if anchor.insufficiency_reason is not None:
            issues.append(
                f"{label}.valuation_anchor provided status forbids insufficiency_reason"
            )
    elif anchor.status == ValuationStatus.PARTIAL:
        _required_text(
            anchor.insufficiency_reason,
            f"{label}.valuation_anchor.insufficiency_reason",
            issues,
        )
        if all(value is None for value in supplied):
            issues.append(f"{label}.valuation_anchor partial status requires a valuation field")
    elif anchor.status == ValuationStatus.INSUFFICIENT_EVIDENCE:
        _required_text(
            anchor.insufficiency_reason,
            f"{label}.valuation_anchor.insufficiency_reason",
            issues,
        )
        if any(value is not None for value in supplied):
            issues.append(
                f"{label}.valuation_anchor insufficient_evidence status forbids valuation fields"
            )
    for field_name, values in (
        ("market_implied_assumptions", anchor.market_implied_assumptions),
        ("sensitive_variables", anchor.sensitive_variables),
    ):
        if values is not None:
            if not values:
                issues.append(f"{label}.valuation_anchor.{field_name} must not be empty")
            _unique(values, f"{label}.valuation_anchor.{field_name}", issues)
            for index, value in enumerate(values):
                _required_text(value, f"{label}.valuation_anchor.{field_name}[{index}]", issues)
    for field_name, value in (
        ("valuation_basis", anchor.valuation_basis),
        ("reasonable_range", anchor.reasonable_range),
    ):
        if value is not None:
            _required_text(value, f"{label}.valuation_anchor.{field_name}", issues)

    if not card.unknown_questions:
        issues.append(f"{label}.unknown_questions must contain at least one item")
    question_ids = tuple(item.question_id for item in card.unknown_questions)
    _unique(question_ids, f"{label}.unknown question IDs", issues)
    for index, question in enumerate(card.unknown_questions):
        item_label = f"{label}.unknown_questions[{index}]"
        _stable_id(question.question_id, f"{item_label}.question_id", issues)
        _required_text(question.question, f"{item_label}.question", issues)
        _ids(
            question.linked_assumption_ids,
            f"{item_label}.linked_assumption_ids",
            issues,
            require_one=True,
        )
        for assumption_id in question.linked_assumption_ids:
            if assumption_id not in assumption_id_set:
                issues.append(f"{item_label} references unknown assumption {assumption_id}")

    version = card.version
    _stable_id(version.version_id, f"{label}.version.version_id", issues)
    created_is_aware = _aware(version.created_at, f"{label}.version.created_at", issues)
    updated_is_aware = _aware(version.updated_at, f"{label}.version.updated_at", issues)
    if created_is_aware and updated_is_aware and version.updated_at < version.created_at:
        issues.append(f"{label}.version.updated_at cannot precede created_at")
    if version.supersedes is not None:
        _stable_id(version.supersedes, f"{label}.version.supersedes", issues)
    if version.supersedes == version.version_id:
        issues.append(f"{label}.version cannot supersede itself")
    if not isinstance(version.user_confirmed, bool):
        issues.append(f"{label}.version.user_confirmed must be boolean")
    _unique(card.tags, f"{label}.tags", issues)
    try:
        validate_v0_output(card.to_dict())
    except DomainValidationError as exc:
        issues.extend(exc.issues)
    return _finish(card, issues)


def validate_proposed_patch(
    patch: ProposedPatch,
    base_thesis: ThesisCard | None = None,
    evidence_by_id: Mapping[str, Evidence] | None = None,
) -> ProposedPatch:
    """Validate a complete AI draft without promoting it to user-owned state."""

    issues: list[str] = []
    _stable_id(patch.base_thesis_id, "proposed_patch.base_thesis_id", issues)
    _stable_id(patch.base_version_id, "proposed_patch.base_version_id", issues)
    if patch.patch_status != "pending_user_review":
        issues.append("proposed_patch.patch_status must equal pending_user_review")
    if not patch.change_items:
        issues.append("proposed_patch.change_items must contain at least one item")
    change_ids = tuple(item.change_id for item in patch.change_items)
    _unique(change_ids, "proposed_patch.change item IDs", issues)
    for index, item in enumerate(patch.change_items):
        label = f"proposed_patch.change_items[{index}]"
        _stable_id(item.change_id, f"{label}.change_id", issues)
        _stable_enum(item.operation, ChangeOperation, f"{label}.operation", issues)
        _stable_enum(item.target_type, ChangeTargetType, f"{label}.target_type", issues)
        if item.target_id is not None:
            _stable_id(item.target_id, f"{label}.target_id", issues)
        _required_text(item.summary, f"{label}.summary", issues)
        _required_text(item.rationale, f"{label}.rationale", issues)
        _ids(item.evidence_ids, f"{label}.evidence_ids", issues)
        if evidence_by_id is not None:
            for evidence_id in item.evidence_ids:
                if evidence_id not in evidence_by_id:
                    issues.append(f"{label} references unknown evidence {evidence_id}")

    _merge_validation(
        "proposed_patch.proposed_thesis", issues, validate_thesis_card, patch.proposed_thesis
    )
    proposed = patch.proposed_thesis
    if evidence_by_id is not None:
        for evidence_id in proposed.strongest_counter_case.evidence_ids:
            if evidence_id not in evidence_by_id:
                issues.append(
                    "proposed_patch.proposed_thesis.strongest_counter_case "
                    f"references unknown evidence {evidence_id}"
                )
    if proposed.version.user_confirmed:
        issues.append("pending AI proposed_thesis must have version.user_confirmed=false")
    if proposed.version.supersedes != patch.base_version_id:
        issues.append(
            "proposed_patch.proposed_thesis.version.supersedes must equal base_version_id"
        )
    if proposed.thesis_id != patch.base_thesis_id:
        issues.append("proposed_patch.proposed_thesis.thesis_id must equal base_thesis_id")
    if base_thesis is not None:
        if patch.base_thesis_id != base_thesis.thesis_id:
            issues.append("proposed_patch.base_thesis_id does not match the base thesis")
        if patch.base_version_id != base_thesis.version.version_id:
            issues.append("proposed_patch.base_version_id does not match the base version")
        if proposed.company.company_id != base_thesis.company.company_id:
            issues.append("proposed_patch must preserve company_id")
        if proposed.version.version_id == base_thesis.version.version_id:
            issues.append("proposed_patch.proposed_thesis must use a new version_id")
        _validate_proposed_change_coverage(patch, base_thesis, issues)
    return _finish(patch, issues)


def _validate_proposed_change_coverage(
    patch: ProposedPatch,
    base: ThesisCard,
    issues: list[str],
) -> None:
    """Reconcile every declared change against the complete proposed draft."""

    proposed = patch.proposed_thesis
    if proposed.company != base.company:
        issues.append(
            "proposed_patch.proposed_thesis must preserve company metadata; "
            "ChangeItem has no company target"
        )
    if proposed.tags != base.tags:
        issues.append(
            "proposed_patch.proposed_thesis must preserve tags; "
            "ChangeItem has no tags target"
        )

    singleton_targets = (
        (
            ChangeTargetType.ONE_SENTENCE_THESIS,
            base.one_sentence_thesis,
            proposed.one_sentence_thesis,
        ),
        (
            ChangeTargetType.STRONGEST_COUNTER_CASE,
            base.strongest_counter_case,
            proposed.strongest_counter_case,
        ),
        (
            ChangeTargetType.VALUATION_ANCHOR,
            base.valuation_anchor,
            proposed.valuation_anchor,
        ),
    )
    collection_targets = (
        (
            ChangeTargetType.ASSUMPTION,
            "assumption_id",
            base.assumptions,
            proposed.assumptions,
        ),
        (
            ChangeTargetType.KEY_INDICATOR,
            "indicator_id",
            base.key_indicators,
            proposed.key_indicators,
        ),
        (
            ChangeTargetType.FALSIFICATION_CONDITION,
            "condition_id",
            base.falsification_conditions,
            proposed.falsification_conditions,
        ),
        (
            ChangeTargetType.UNKNOWN_QUESTION,
            "question_id",
            base.unknown_questions,
            proposed.unknown_questions,
        ),
    )

    actual_changes: dict[
        tuple[ChangeTargetType, str | None], ChangeOperation
    ] = {}
    known_targets: dict[ChangeTargetType, set[str | None]] = {}
    for target_type, before, after in singleton_targets:
        known_targets[target_type] = {None}
        if before != after:
            actual_changes[(target_type, None)] = ChangeOperation.MODIFY

    for target_type, id_field, before_items, after_items in collection_targets:
        before_ids = tuple(getattr(item, id_field) for item in before_items)
        after_ids = tuple(getattr(item, id_field) for item in after_items)
        before_id_set = set(before_ids)
        after_id_set = set(after_ids)
        before_common_order = tuple(
            target_id for target_id in before_ids if target_id in after_id_set
        )
        after_common_order = tuple(
            target_id for target_id in after_ids if target_id in before_id_set
        )
        if before_common_order != after_common_order:
            issues.append(
                "proposed_patch.proposed_thesis must preserve the relative "
                f"{target_type.value} order; ChangeItem has no reorder operation"
            )
        before_by_id = {getattr(item, id_field): item for item in before_items}
        after_by_id = {getattr(item, id_field): item for item in after_items}
        known_targets[target_type] = set(before_by_id) | set(after_by_id)
        ordered_target_ids = before_ids + tuple(
            target_id for target_id in after_ids if target_id not in before_id_set
        )
        for target_id in ordered_target_ids:
            before = before_by_id.get(target_id)
            after = after_by_id.get(target_id)
            if before is None:
                operation = ChangeOperation.ADD
            elif after is None:
                operation = ChangeOperation.REMOVE
            elif before != after:
                operation = ChangeOperation.MODIFY
            else:
                continue
            actual_changes[(target_type, target_id)] = operation

    declared_targets: set[tuple[ChangeTargetType, str | None]] = set()
    matched_changes: set[tuple[ChangeTargetType, str | None]] = set()
    singleton_types = {target_type for target_type, _, _ in singleton_targets}
    collection_types = {target_type for target_type, _, _, _ in collection_targets}
    mutating_operations = {
        ChangeOperation.ADD,
        ChangeOperation.MODIFY,
        ChangeOperation.REMOVE,
    }
    known_target_keys = {
        (target_type, target_id)
        for target_type, target_ids in known_targets.items()
        for target_id in target_ids
    }
    for index, item in enumerate(patch.change_items):
        if not isinstance(item.target_type, ChangeTargetType) or not isinstance(
            item.operation, ChangeOperation
        ):
            continue
        label = f"proposed_patch.change_items[{index}]"
        target = (item.target_type, item.target_id)
        if item.target_type in singleton_types and item.target_id is not None:
            issues.append(
                f"{label}.target_id must be null for {item.target_type.value}"
            )
            continue
        if item.target_type in collection_types and item.target_id is None:
            issues.append(
                f"{label}.target_id is required for {item.target_type.value}"
            )
            continue
        if target in declared_targets:
            issues.append(
                f"{label} duplicates the {item.target_type.value} target "
                f"{item.target_id!r}"
            )
            continue
        declared_targets.add(target)

        expected = actual_changes.get(target)
        if item.operation in mutating_operations:
            if expected != item.operation:
                actual = "no change" if expected is None else expected.value
                issues.append(
                    f"{label}.operation {item.operation.value} does not match "
                    f"the actual {actual} for {item.target_type.value} "
                    f"target {item.target_id!r}"
                )
            else:
                matched_changes.add(target)
            continue

        if target not in known_target_keys:
            issues.append(
                f"{label} references an unknown {item.target_type.value} "
                f"target {item.target_id!r}"
            )
        elif expected is not None:
            issues.append(
                f"{label}.operation {item.operation.value} cannot describe the "
                f"actual {expected.value} for {item.target_type.value} "
                f"target {item.target_id!r}"
            )

    for target, operation in actual_changes.items():
        if target not in matched_changes:
            target_type, target_id = target
            issues.append(
                "proposed_patch.change_items must disclose the actual "
                f"{operation.value} for {target_type.value} target {target_id!r}"
            )


def validate_thesis_diff(
    diff: ThesisDiff,
    base_thesis: ThesisCard,
    evidence_by_id: Mapping[str, Evidence],
    documents_by_id: Mapping[str, SourceDocument],
) -> ThesisDiff:
    """Validate a replay-safe ThesisDiff against its complete dependency set."""

    issues: list[str] = []
    label = f"diff {diff.thesis_diff_id}"
    _schema_version(diff.schema_version, f"{label}.schema_version", issues)
    for field_name, value in (
        ("thesis_diff_id", diff.thesis_diff_id),
        ("company_id", diff.company_id),
        ("base_thesis_id", diff.base_thesis_id),
        ("base_version_id", diff.base_version_id),
    ):
        _stable_id(value, f"{label}.{field_name}", issues)
    _stable_enum(
        diff.overall_assessment, ChangeStatus, f"{label}.overall_assessment", issues
    )
    _required_text(diff.overall_rationale, f"{label}.overall_rationale", issues)
    cutoff_is_aware = _aware(diff.analysis_cutoff_at, f"{label}.analysis_cutoff_at", issues)
    generated_is_aware = _aware(diff.generated_at, f"{label}.generated_at", issues)
    if cutoff_is_aware and generated_is_aware and diff.generated_at < diff.analysis_cutoff_at:
        issues.append(f"{label}.generated_at cannot precede analysis_cutoff_at")

    _merge_validation("base_thesis", issues, validate_thesis_card, base_thesis)
    if not base_thesis.version.user_confirmed:
        issues.append("a ThesisDiff base thesis must be user-confirmed")
    if diff.base_thesis_id != base_thesis.thesis_id:
        issues.append("diff.base_thesis_id does not match the supplied thesis")
    if diff.base_version_id != base_thesis.version.version_id:
        issues.append("diff.base_version_id does not match the supplied thesis version")
    if diff.company_id != base_thesis.company.company_id:
        issues.append("diff.company_id does not match the supplied thesis")
    if cutoff_is_aware:
        if (
            is_timezone_aware(base_thesis.version.created_at)
            and base_thesis.version.created_at > diff.analysis_cutoff_at
        ):
            issues.append("base thesis created_at cannot follow analysis_cutoff_at")
        if (
            is_timezone_aware(base_thesis.version.updated_at)
            and base_thesis.version.updated_at > diff.analysis_cutoff_at
        ):
            issues.append("base thesis updated_at cannot follow analysis_cutoff_at")
        if base_thesis.version.as_of_date > diff.analysis_cutoff_at.date():
            issues.append("base thesis as_of_date cannot follow analysis_cutoff_at")

    _ids(diff.source_document_ids, f"{label}.source_document_ids", issues, require_one=True)
    source_documents: list[SourceDocument] = []
    for document_id in diff.source_document_ids:
        document = documents_by_id.get(document_id)
        if document is None:
            issues.append(f"{label} references unknown source document {document_id}")
            continue
        _merge_validation(f"document {document_id}", issues, validate_source_document, document)
        source_documents.append(document)
        if document.company_id != diff.company_id:
            issues.append(f"document {document_id} belongs to another company")
    if source_documents and diff.material_published_on not in {
        document.published_on for document in source_documents
    }:
        issues.append(f"{label}.material_published_on must match a supplied source document")
    if cutoff_is_aware and diff.material_published_on > diff.analysis_cutoff_at.date():
        issues.append(f"{label}.material_published_on cannot follow analysis_cutoff_at")
    try:
        reject_future_documents(source_documents, diff.analysis_cutoff_at)
    except TemporalValidationError as exc:
        issues.extend(f"{label}: {issue}" for issue in exc.issues)

    if not 3 <= len(diff.assumption_changes) <= 7:
        issues.append(f"{label}.assumption_changes must contain between 3 and 7 items")
    base_assumptions = {item.assumption_id: item for item in base_thesis.assumptions}
    changed_ids = tuple(item.assumption_id for item in diff.assumption_changes)
    _unique(changed_ids, f"{label}.assumption change IDs", issues)
    if set(changed_ids) != set(base_assumptions):
        issues.append(f"{label}.assumption_changes must cover every base assumption exactly once")

    referenced_evidence: set[str] = set()
    current_material_evidence: set[str] = set()
    for index, change in enumerate(diff.assumption_changes):
        item_label = f"{label}.assumption_changes[{index}]"
        _stable_id(change.assumption_id, f"{item_label}.assumption_id", issues)
        _required_text(change.prior_statement, f"{item_label}.prior_statement", issues)
        _stable_enum(change.impact, ChangeStatus, f"{item_label}.impact", issues)
        _stable_enum(change.confidence, Confidence, f"{item_label}.confidence", issues)
        _required_text(change.rationale, f"{item_label}.rationale", issues)
        _required_text(
            change.alternative_explanation, f"{item_label}.alternative_explanation", issues
        )
        _ids(change.evidence_ids, f"{item_label}.evidence_ids", issues)
        if change.impact != ChangeStatus.INSUFFICIENT_EVIDENCE and not change.evidence_ids:
            issues.append(f"{item_label}.evidence_ids must contain at least one item")
        assumption = base_assumptions.get(change.assumption_id)
        if assumption is None:
            issues.append(f"{item_label} references unknown assumption {change.assumption_id}")
        elif change.prior_statement != assumption.statement:
            issues.append(f"{item_label}.prior_statement must match the base thesis")
        triggered_condition_ids = getattr(
            change,
            "triggered_falsification_condition_ids",
            None,
        )
        if change.impact == ChangeStatus.INVALIDATED:
            if triggered_condition_ids is None:
                issues.append(
                    f"{item_label}.triggered_falsification_condition_ids is required "
                    "when impact is invalidated"
                )
            else:
                _ids(
                    triggered_condition_ids,
                    f"{item_label}.triggered_falsification_condition_ids",
                    issues,
                    require_one=True,
                )
                if assumption is not None:
                    allowed_condition_ids = set(assumption.falsification_condition_ids)
                    for condition_id in triggered_condition_ids:
                        if condition_id not in allowed_condition_ids:
                            issues.append(
                                f"{item_label}.triggered_falsification_condition_ids "
                                f"references {condition_id}, which is not linked to "
                                f"assumption {change.assumption_id}"
                            )
        elif triggered_condition_ids is not None:
            issues.append(
                f"{item_label}.triggered_falsification_condition_ids is only allowed "
                "when impact is invalidated"
            )
        referenced_evidence.update(change.evidence_ids)
        current_material_evidence.update(change.evidence_ids)

    action = diff.management_statement_action
    _stable_enum(
        action.assessment,
        ManagementAssessment,
        f"{label}.management_statement_action.assessment",
        issues,
    )
    _required_text(action.summary, f"{label}.management_statement_action.summary", issues)
    comparison_ids = tuple(item.comparison_id for item in action.comparisons)
    _unique(comparison_ids, f"{label}.management comparison IDs", issues)
    if action.assessment in {
        ManagementAssessment.ALIGNED,
        ManagementAssessment.PARTIALLY_ALIGNED,
        ManagementAssessment.MISALIGNED,
    } and not action.comparisons:
        issues.append(
            f"{label}.management_statement_action.comparisons must contain at least "
            "one item for a substantive assessment"
        )
    for index, comparison in enumerate(action.comparisons):
        item_label = f"{label}.management_statement_action.comparisons[{index}]"
        _stable_id(comparison.comparison_id, f"{item_label}.comparison_id", issues)
        _required_text(comparison.past_statement, f"{item_label}.past_statement", issues)
        _required_text(
            comparison.current_action_or_result,
            f"{item_label}.current_action_or_result",
            issues,
        )
        _required_text(comparison.unresolved_part, f"{item_label}.unresolved_part", issues)
        _stable_enum(
            comparison.assessment, ComparisonAssessment, f"{item_label}.assessment", issues
        )
        _ids(
            comparison.past_evidence_ids,
            f"{item_label}.past_evidence_ids",
            issues,
            require_one=True,
        )
        _ids(
            comparison.current_evidence_ids,
            f"{item_label}.current_evidence_ids",
            issues,
            require_one=True,
        )
        referenced_evidence.update(comparison.past_evidence_ids)
        referenced_evidence.update(comparison.current_evidence_ids)
        current_material_evidence.update(comparison.current_evidence_ids)

    counter = diff.targeted_counter_case
    _required_text(counter.argument, f"{label}.targeted_counter_case.argument", issues)
    _required_text(counter.why_plausible, f"{label}.targeted_counter_case.why_plausible", issues)
    _ids(
        counter.attacked_assumption_ids,
        f"{label}.targeted_counter_case.attacked_assumption_ids",
        issues,
        require_one=True,
    )
    _ids(
        counter.evidence_ids,
        f"{label}.targeted_counter_case.evidence_ids",
        issues,
        require_one=True,
    )
    for assumption_id in counter.attacked_assumption_ids:
        if assumption_id not in base_assumptions:
            issues.append(
                f"{label}.targeted_counter_case references unknown assumption {assumption_id}"
            )
    referenced_evidence.update(counter.evidence_ids)
    current_material_evidence.update(counter.evidence_ids)

    if not 1 <= len(diff.follow_up_questions) <= 3:
        issues.append(f"{label}.follow_up_questions must contain between 1 and 3 items")
    question_ids = tuple(item.question_id for item in diff.follow_up_questions)
    _unique(question_ids, f"{label}.follow-up question IDs", issues)
    for index, question in enumerate(diff.follow_up_questions):
        item_label = f"{label}.follow_up_questions[{index}]"
        _stable_id(question.question_id, f"{item_label}.question_id", issues)
        _required_text(question.question, f"{item_label}.question", issues)
        _required_text(question.information_value, f"{item_label}.information_value", issues)
        _required_text(question.evidence_needed, f"{item_label}.evidence_needed", issues)
        _ids(
            question.linked_assumption_ids,
            f"{item_label}.linked_assumption_ids",
            issues,
            require_one=True,
        )
        for assumption_id in question.linked_assumption_ids:
            if assumption_id not in base_assumptions:
                issues.append(f"{item_label} references unknown assumption {assumption_id}")

    _merge_validation(
        "proposed_patch",
        issues,
        validate_proposed_patch,
        diff.proposed_patch,
        base_thesis,
        evidence_by_id,
    )
    if diff.proposed_patch.base_thesis_id != diff.base_thesis_id:
        issues.append("diff.proposed_patch.base_thesis_id must match diff.base_thesis_id")
    if diff.proposed_patch.base_version_id != diff.base_version_id:
        issues.append("diff.proposed_patch.base_version_id must match diff.base_version_id")
    if (
        cutoff_is_aware
        and diff.proposed_patch.proposed_thesis.version.as_of_date
        > diff.analysis_cutoff_at.date()
    ):
        issues.append(
            "diff.proposed_patch.proposed_thesis.version.as_of_date cannot follow "
            "analysis_cutoff_at"
        )
    proposed_version = diff.proposed_patch.proposed_thesis.version
    if generated_is_aware and is_timezone_aware(proposed_version.created_at):
        if proposed_version.created_at > diff.generated_at:
            issues.append(
                "diff.proposed_patch.proposed_thesis.version.created_at cannot "
                "follow diff.generated_at"
            )
    if generated_is_aware and is_timezone_aware(proposed_version.updated_at):
        if proposed_version.updated_at > diff.generated_at:
            issues.append(
                "diff.proposed_patch.proposed_thesis.version.updated_at cannot "
                "follow diff.generated_at"
            )
    if proposed_version.as_of_date < base_thesis.version.as_of_date:
        issues.append("proposed thesis as_of_date cannot precede the base thesis")
    if (
        is_timezone_aware(proposed_version.created_at)
        and is_timezone_aware(base_thesis.version.updated_at)
        and proposed_version.created_at < base_thesis.version.updated_at
    ):
        issues.append("proposed thesis created_at cannot precede base thesis updated_at")
    if (
        is_timezone_aware(proposed_version.updated_at)
        and is_timezone_aware(base_thesis.version.updated_at)
        and proposed_version.updated_at < base_thesis.version.updated_at
    ):
        issues.append("proposed thesis updated_at cannot precede the base thesis")

    # Evidence references embedded in the complete proposed ThesisCard are part
    # of the pending state transition just as surely as change-item references.
    # References inherited unchanged from the confirmed base are audited at the
    # base's own knowledge boundary; newly introduced references are current
    # material and must therefore cite one of this Diff's source documents.
    base_counter_evidence = set(
        base_thesis.strongest_counter_case.evidence_ids
    )
    proposed_counter_evidence = set(
        diff.proposed_patch.proposed_thesis.strongest_counter_case.evidence_ids
    )
    referenced_evidence.update(proposed_counter_evidence)
    current_material_evidence.update(
        proposed_counter_evidence - base_counter_evidence
    )

    for evidence_id in sorted(base_counter_evidence):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            issues.append(f"base thesis references unknown evidence {evidence_id}")
            continue
        _merge_validation(
            f"base evidence {evidence_id}",
            issues,
            validate_evidence,
            evidence,
            documents_by_id,
            base_thesis.version.updated_at,
        )
        if evidence.verification_status != VerificationStatus.VERIFIED:
            issues.append(
                f"base evidence {evidence_id} must be verified before it can "
                "support a ThesisCard"
            )
        if evidence.company_id != base_thesis.company.company_id:
            issues.append(f"base evidence {evidence_id} belongs to another company")

    for item in diff.proposed_patch.change_items:
        referenced_evidence.update(item.evidence_ids)
        current_material_evidence.update(item.evidence_ids)

    source_id_set = set(diff.source_document_ids)
    for evidence_id in sorted(referenced_evidence):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            issues.append(f"{label} references unknown evidence {evidence_id}")
            continue
        _merge_validation(
            f"evidence {evidence_id}",
            issues,
            validate_evidence,
            evidence,
            documents_by_id,
            diff.analysis_cutoff_at,
        )
        if evidence.verification_status != VerificationStatus.VERIFIED:
            issues.append(
                f"evidence {evidence_id} must be verified before it can support a ThesisDiff"
            )
        if evidence.company_id != diff.company_id:
            issues.append(f"evidence {evidence_id} belongs to another company")
        for citation in evidence.citations:
            if (
                evidence_id in current_material_evidence
                and citation.source_document_id not in source_id_set
            ):
                issues.append(
                    f"evidence {evidence_id} cites source document "
                    f"{citation.source_document_id} not listed as current material by the diff"
                )

    try:
        validate_v0_output(diff.to_dict())
    except DomainValidationError as exc:
        issues.extend(exc.issues)
    return _finish(diff, issues)


def validate_user_review(review: UserReview, diff: ThesisDiff) -> UserReview:
    """Validate the human decision boundary around a pending proposed patch."""

    issues: list[str] = []
    label = f"review {review.user_review_id}"
    _schema_version(review.schema_version, f"{label}.schema_version", issues)
    for field_name, value in (
        ("user_review_id", review.user_review_id),
        ("thesis_diff_id", review.thesis_diff_id),
        ("company_id", review.company_id),
        ("base_thesis_id", review.base_thesis_id),
        ("base_version_id", review.base_version_id),
        ("reviewer_id", review.reviewer_id),
    ):
        _stable_id(value, f"{label}.{field_name}", issues)
    _stable_enum(review.decision, ReviewDecision, f"{label}.decision", issues)
    reviewed_is_aware = _aware(review.reviewed_at, f"{label}.reviewed_at", issues)
    if review.thesis_diff_id != diff.thesis_diff_id:
        issues.append("review.thesis_diff_id does not match the supplied ThesisDiff")
    if review.company_id != diff.company_id:
        issues.append("review.company_id does not match the supplied ThesisDiff")
    if review.base_thesis_id != diff.base_thesis_id:
        issues.append("review.base_thesis_id does not match the supplied ThesisDiff")
    if review.base_version_id != diff.base_version_id:
        issues.append("review.base_version_id does not match the supplied ThesisDiff")
    if (
        reviewed_is_aware
        and diff.generated_at.tzinfo is not None
        and diff.generated_at.utcoffset() is not None
        and review.reviewed_at < diff.generated_at
    ):
        issues.append("review.reviewed_at cannot precede diff.generated_at")

    if review.decision == ReviewDecision.ACCEPT_WITH_EDITS:
        if review.reviewed_thesis is None:
            issues.append("accept_with_edits requires a complete reviewed_thesis")
        if review.research_tasks is not None:
            issues.append("accept_with_edits cannot include research_tasks")
    elif review.decision == ReviewDecision.CREATE_RESEARCH_TASK:
        if not review.research_tasks:
            issues.append("create_research_task requires at least one research task")
        if review.reviewed_thesis is not None:
            issues.append("create_research_task cannot include reviewed_thesis")
    elif review.decision in {
        ReviewDecision.ACCEPT,
        ReviewDecision.REJECT,
        ReviewDecision.DEFER_INSUFFICIENT,
    }:
        if review.reviewed_thesis is not None:
            issues.append(f"{review.decision.value} cannot include reviewed_thesis")
        if review.research_tasks is not None:
            issues.append(f"{review.decision.value} cannot include research_tasks")

    if review.reviewed_thesis is not None:
        thesis = review.reviewed_thesis
        _merge_validation("reviewed_thesis", issues, validate_thesis_card, thesis)
        if not thesis.version.user_confirmed:
            issues.append("reviewed_thesis must have version.user_confirmed=true")
        if thesis.version.supersedes != diff.base_version_id:
            issues.append("reviewed_thesis.version.supersedes must equal diff.base_version_id")
        if thesis.thesis_id != diff.base_thesis_id:
            issues.append("reviewed_thesis must preserve thesis_id")
        if thesis.company.company_id != diff.company_id:
            issues.append("reviewed_thesis must preserve company_id")
        if thesis.version.version_id == diff.base_version_id:
            issues.append("reviewed_thesis must use a new version_id")
        if reviewed_is_aware:
            if thesis.version.as_of_date > review.reviewed_at.date():
                issues.append(
                    "reviewed_thesis.version.as_of_date cannot follow review.reviewed_at"
                )
            if (
                is_timezone_aware(thesis.version.created_at)
                and thesis.version.created_at > review.reviewed_at
            ):
                issues.append(
                    "reviewed_thesis.version.created_at cannot follow review.reviewed_at"
                )
            if (
                is_timezone_aware(thesis.version.updated_at)
                and thesis.version.updated_at > review.reviewed_at
            ):
                issues.append(
                    "reviewed_thesis.version.updated_at cannot follow review.reviewed_at"
                )
        draft_version = diff.proposed_patch.proposed_thesis.version
        if thesis.version.as_of_date < draft_version.as_of_date:
            issues.append(
                "reviewed_thesis.version.as_of_date cannot precede the reviewed draft"
            )
        if (
            is_timezone_aware(thesis.version.created_at)
            and is_timezone_aware(draft_version.created_at)
            and thesis.version.created_at < draft_version.created_at
        ):
            issues.append(
                "reviewed_thesis.version.created_at cannot precede the reviewed draft"
            )
        if (
            is_timezone_aware(thesis.version.updated_at)
            and is_timezone_aware(draft_version.updated_at)
            and thesis.version.updated_at < draft_version.updated_at
        ):
            issues.append(
                "reviewed_thesis.version.updated_at cannot precede the reviewed draft"
            )

    if review.research_tasks is not None:
        task_ids = tuple(item.research_task_id for item in review.research_tasks)
        _unique(task_ids, f"{label}.research task IDs", issues)
        assumption_ids = {
            item.assumption_id for item in diff.proposed_patch.proposed_thesis.assumptions
        }
        for index, task in enumerate(review.research_tasks):
            item_label = f"{label}.research_tasks[{index}]"
            _stable_id(task.research_task_id, f"{item_label}.research_task_id", issues)
            _required_text(task.question, f"{item_label}.question", issues)
            _required_text(task.why_it_matters, f"{item_label}.why_it_matters", issues)
            if task.status != "open":
                issues.append(f"{item_label}.status must equal open")
            _ids(
                task.linked_assumption_ids,
                f"{item_label}.linked_assumption_ids",
                issues,
                require_one=True,
            )
            for assumption_id in task.linked_assumption_ids:
                if assumption_id not in assumption_ids:
                    issues.append(f"{item_label} references unknown assumption {assumption_id}")
    return _finish(review, issues)


def validate_v0_output(payload: Any) -> Any:
    """Apply universal field policy and scoped AI-generated-text policy."""

    violations = find_v0_policy_violations(payload)
    if violations:
        raise DomainValidationError(str(violation) for violation in violations)
    return payload
