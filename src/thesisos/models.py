"""Immutable Python models that mirror the V0 JSON Schemas exactly."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Mapping, TypeVar


SCHEMA_VERSION = "1.0.0"


class StableEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class DocumentType(StableEnum):
    ANNUAL_REPORT = "annual_report"
    INTERIM_REPORT = "interim_report"
    QUARTERLY_REPORT = "quarterly_report"
    EARNINGS_RELEASE = "earnings_release"
    EARNINGS_CALL_TRANSCRIPT = "earnings_call_transcript"
    REGULATORY_FILING = "regulatory_filing"
    COMPANY_ANNOUNCEMENT = "company_announcement"
    RESEARCH_NOTE = "research_note"
    INVESTOR_NOTE = "investor_note"
    OTHER = "other"


class MediaType(StableEnum):
    PDF = "pdf"
    MARKDOWN = "markdown"
    PLAIN_TEXT = "plain_text"


class SourceClass(StableEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    USER_PROVIDED = "user_provided"


class ReportingPeriodKind(StableEnum):
    FISCAL_QUARTER = "fiscal_quarter"
    FISCAL_HALF = "fiscal_half"
    FISCAL_YEAR = "fiscal_year"
    POINT_IN_TIME = "point_in_time"
    NOT_APPLICABLE = "not_applicable"


class QuotationMode(StableEnum):
    EXACT_QUOTE = "exact_quote"
    TABLE_VALUE = "table_value"
    FAITHFUL_PARAPHRASE = "faithful_paraphrase"


class LocatorKind(StableEnum):
    PAGE = "page"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    SECTION = "section"
    LINE_RANGE = "line_range"


class EvidenceKind(StableEnum):
    SOURCE_FACT = "source_fact"
    SOURCE_OPINION = "source_opinion"
    USER_JUDGMENT = "user_judgment"
    AI_INFERENCE = "ai_inference"


class Attribution(StableEnum):
    SOURCE_DOCUMENT = "source_document"
    MANAGEMENT = "management"
    THIRD_PARTY_AUTHOR = "third_party_author"
    USER = "user"
    AI = "ai"


class EvidenceConfidence(StableEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Confidence(StableEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VerificationStatus(StableEnum):
    UNREVIEWED = "unreviewed"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    REJECTED = "rejected"


class ChangeStatus(StableEnum):
    CLEARLY_STRENGTHENED = "clearly_strengthened"
    SLIGHTLY_STRENGTHENED = "slightly_strengthened"
    UNCHANGED = "unchanged"
    SLIGHTLY_WEAKENED = "slightly_weakened"
    CLEARLY_WEAKENED = "clearly_weakened"
    INVALIDATED = "invalidated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ResearchStatus(StableEnum):
    HOLDING = "holding"
    WATCHLIST = "watchlist"
    RESEARCH = "research"


class ValuationStatus(StableEnum):
    PROVIDED = "provided"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ManagementAssessment(StableEnum):
    ALIGNED = "aligned"
    PARTIALLY_ALIGNED = "partially_aligned"
    MISALIGNED = "misaligned"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_APPLICABLE = "not_applicable"


class ComparisonAssessment(StableEnum):
    ALIGNED = "aligned"
    PARTIALLY_ALIGNED = "partially_aligned"
    MISALIGNED = "misaligned"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ChangeOperation(StableEnum):
    KEEP = "keep"
    MODIFY = "modify"
    ADD = "add"
    REMOVE = "remove"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ChangeTargetType(StableEnum):
    ONE_SENTENCE_THESIS = "one_sentence_thesis"
    ASSUMPTION = "assumption"
    KEY_INDICATOR = "key_indicator"
    FALSIFICATION_CONDITION = "falsification_condition"
    STRONGEST_COUNTER_CASE = "strongest_counter_case"
    VALUATION_ANCHOR = "valuation_anchor"
    UNKNOWN_QUESTION = "unknown_question"


class ReviewDecision(StableEnum):
    ACCEPT = "accept"
    ACCEPT_WITH_EDITS = "accept_with_edits"
    REJECT = "reject"
    DEFER_INSUFFICIENT = "defer_insufficient"
    CREATE_RESEARCH_TASK = "create_research_task"


ModelT = TypeVar("ModelT", bound="DomainModel")


class DomainModel:
    """Deterministic JSON serialization shared by all schema models."""

    _json_fields: ClassVar[frozenset[str]]
    _include_none_fields: ClassVar[frozenset[str]] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        if not is_dataclass(self):  # pragma: no cover
            raise TypeError("DomainModel subclasses must be dataclasses")
        result: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if value is None and item.name not in self._include_none_fields:
                continue
            result[item.name] = _to_json(value)
        return result


def _to_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, StableEnum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.isoformat()
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, DomainModel):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_to_json(item) for item in value]
    return value


def _object(payload: Mapping[str, Any], model: str, allowed: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{model} payload must be an object")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{model} contains unknown fields: {', '.join(unknown)}")
    return payload


def _required(data: Mapping[str, Any], key: str, model: str) -> Any:
    if key not in data:
        raise ValueError(f"{model} is missing required field: {key}")
    return data[key]


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    return None if value is None else _text(value, field_name)


def _integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an integer")
    return value


def _optional_integer(value: Any, field_name: str) -> int | None:
    return None if value is None else _integer(value, field_name)


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a boolean")
    return value


def _datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 datetime")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc


def _date(value: Any, field_name: str) -> date:
    if isinstance(value, datetime):
        raise TypeError(f"{field_name} must be an ISO-8601 date")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 date") from exc


def _optional_date(value: Any, field_name: str) -> date | None:
    return None if value is None else _date(value, field_name)


def _enum(enum_type: type[StableEnum], value: Any, field_name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(_text(value, field_name))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {allowed}") from exc


def _texts(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise TypeError(f"{field_name} must be an array")
    return tuple(_text(item, f"{field_name}[]") for item in value)


def _models(value: Any, model_type: type[ModelT], field_name: str) -> tuple[ModelT, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise TypeError(f"{field_name} must be an array")
    return tuple(model_type.from_dict(item) for item in value)  # type: ignore[attr-defined]


def _schema_version(data: Mapping[str, Any], model: str) -> str:
    value = _text(_required(data, "schema_version", model), "schema_version")
    if value != SCHEMA_VERSION:
        raise ValueError(f"{model}.schema_version must equal {SCHEMA_VERSION}")
    return value


@dataclass(frozen=True)
class ReportingPeriod(DomainModel):
    kind: ReportingPeriodKind
    label: str
    start_on: date | None
    end_on: date | None

    _json_fields = frozenset({"kind", "label", "start_on", "end_on"})
    _include_none_fields = frozenset({"start_on", "end_on"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ReportingPeriod:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            kind=_enum(ReportingPeriodKind, _required(data, "kind", cls.__name__), "kind"),
            label=_text(_required(data, "label", cls.__name__), "label"),
            start_on=_optional_date(_required(data, "start_on", cls.__name__), "start_on"),
            end_on=_optional_date(_required(data, "end_on", cls.__name__), "end_on"),
        )


@dataclass(frozen=True)
class Snapshot(DomainModel):
    sha256: str
    storage_uri: str
    byte_size: int

    _json_fields = frozenset({"sha256", "storage_uri", "byte_size"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Snapshot:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            sha256=_text(_required(data, "sha256", cls.__name__), "sha256"),
            storage_uri=_text(_required(data, "storage_uri", cls.__name__), "storage_uri"),
            byte_size=_integer(_required(data, "byte_size", cls.__name__), "byte_size"),
        )


@dataclass(frozen=True)
class SourceDocument(DomainModel):
    source_document_id: str
    company_id: str
    title: str
    document_type: DocumentType
    media_type: MediaType
    source_class: SourceClass
    language: str
    published_on: date
    publicly_available_at: datetime
    reporting_period: ReportingPeriod
    snapshot: Snapshot
    ingested_at: datetime
    issuer_or_author: str | None = None
    original_uri: str | None = None
    page_count: int | None = None
    schema_version: str = SCHEMA_VERSION

    _json_fields = frozenset(
        {
            "schema_version", "source_document_id", "company_id", "title", "document_type",
            "media_type", "source_class", "issuer_or_author", "language", "published_on",
            "publicly_available_at", "reporting_period", "original_uri", "page_count", "snapshot", "ingested_at",
        }
    )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SourceDocument:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            source_document_id=_text(_required(data, "source_document_id", cls.__name__), "source_document_id"),
            company_id=_text(_required(data, "company_id", cls.__name__), "company_id"),
            title=_text(_required(data, "title", cls.__name__), "title"),
            document_type=_enum(DocumentType, _required(data, "document_type", cls.__name__), "document_type"),
            media_type=_enum(MediaType, _required(data, "media_type", cls.__name__), "media_type"),
            source_class=_enum(SourceClass, _required(data, "source_class", cls.__name__), "source_class"),
            language=_text(_required(data, "language", cls.__name__), "language"),
            published_on=_date(_required(data, "published_on", cls.__name__), "published_on"),
            publicly_available_at=_datetime(_required(data, "publicly_available_at", cls.__name__), "publicly_available_at"),
            reporting_period=ReportingPeriod.from_dict(_required(data, "reporting_period", cls.__name__)),
            snapshot=Snapshot.from_dict(_required(data, "snapshot", cls.__name__)),
            ingested_at=_datetime(_required(data, "ingested_at", cls.__name__), "ingested_at"),
            issuer_or_author=_optional_text(data.get("issuer_or_author"), "issuer_or_author"),
            original_uri=_optional_text(data.get("original_uri"), "original_uri"),
            page_count=None if data.get("page_count") is None else _integer(data["page_count"], "page_count"),
            schema_version=_schema_version(data, cls.__name__),
        )


@dataclass(frozen=True)
class CitationLocator(DomainModel):
    kind: LocatorKind
    page: int | None = None
    page_label: str | None = None
    section: str | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    table: str | None = None
    row: str | None = None
    column: str | None = None
    subsection: str | None = None
    line_start: int | None = None
    line_end: int | None = None

    _json_fields = frozenset(
        {"kind", "page", "page_label", "section", "paragraph_start", "paragraph_end", "table", "row", "column", "subsection", "line_start", "line_end"}
    )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CitationLocator:
        broad_data = _object(payload, cls.__name__, cls._json_fields)
        kind = _enum(
            LocatorKind,
            _required(broad_data, "kind", cls.__name__),
            "kind",
        )
        fields_by_kind = {
            LocatorKind.PAGE: frozenset({"kind", "page", "page_label", "section"}),
            LocatorKind.PARAGRAPH: frozenset(
                {"kind", "paragraph_start", "paragraph_end", "section"}
            ),
            LocatorKind.TABLE: frozenset({"kind", "table", "page", "row", "column"}),
            LocatorKind.SECTION: frozenset({"kind", "section", "subsection"}),
            LocatorKind.LINE_RANGE: frozenset({"kind", "line_start", "line_end"}),
        }
        required_by_kind = {
            LocatorKind.PAGE: ("page",),
            LocatorKind.PARAGRAPH: ("paragraph_start", "paragraph_end"),
            LocatorKind.TABLE: ("table",),
            LocatorKind.SECTION: ("section",),
            LocatorKind.LINE_RANGE: ("line_start", "line_end"),
        }
        data = _object(payload, cls.__name__, fields_by_kind[kind])
        for required_field in required_by_kind[kind]:
            _required(data, required_field, cls.__name__)
        return cls(
            kind=kind,
            page=_optional_integer(data.get("page"), "page"),
            page_label=_optional_text(data.get("page_label"), "page_label"),
            section=_optional_text(data.get("section"), "section"),
            paragraph_start=_optional_integer(data.get("paragraph_start"), "paragraph_start"),
            paragraph_end=_optional_integer(data.get("paragraph_end"), "paragraph_end"),
            table=_optional_text(data.get("table"), "table"),
            row=_optional_text(data.get("row"), "row"),
            column=_optional_text(data.get("column"), "column"),
            subsection=_optional_text(data.get("subsection"), "subsection"),
            line_start=_optional_integer(data.get("line_start"), "line_start"),
            line_end=_optional_integer(data.get("line_end"), "line_end"),
        )


@dataclass(frozen=True)
class Citation(DomainModel):
    citation_id: str
    source_document_id: str
    snapshot_sha256: str
    quotation_mode: QuotationMode
    locator: CitationLocator
    quoted_text: str
    schema_version: str = SCHEMA_VERSION

    _json_fields = frozenset({"schema_version", "citation_id", "source_document_id", "snapshot_sha256", "quotation_mode", "locator", "quoted_text"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Citation:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            citation_id=_text(_required(data, "citation_id", cls.__name__), "citation_id"),
            source_document_id=_text(_required(data, "source_document_id", cls.__name__), "source_document_id"),
            snapshot_sha256=_text(_required(data, "snapshot_sha256", cls.__name__), "snapshot_sha256"),
            quotation_mode=_enum(QuotationMode, _required(data, "quotation_mode", cls.__name__), "quotation_mode"),
            locator=CitationLocator.from_dict(_required(data, "locator", cls.__name__)),
            quoted_text=_text(_required(data, "quoted_text", cls.__name__), "quoted_text"),
            schema_version=_schema_version(data, cls.__name__),
        )


@dataclass(frozen=True)
class Evidence(DomainModel):
    evidence_id: str
    company_id: str
    statement: str
    content_class: EvidenceKind
    attribution: Attribution
    confidence: EvidenceConfidence
    verification_status: VerificationStatus
    available_as_of: datetime
    citations: tuple[Citation, ...]
    created_at: datetime
    reported_for: str | None = None
    tags: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    _json_fields = frozenset({"schema_version", "evidence_id", "company_id", "statement", "content_class", "attribution", "confidence", "verification_status", "available_as_of", "reported_for", "citations", "tags", "created_at"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Evidence:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            evidence_id=_text(_required(data, "evidence_id", cls.__name__), "evidence_id"),
            company_id=_text(_required(data, "company_id", cls.__name__), "company_id"),
            statement=_text(_required(data, "statement", cls.__name__), "statement"),
            content_class=_enum(EvidenceKind, _required(data, "content_class", cls.__name__), "content_class"),
            attribution=_enum(Attribution, _required(data, "attribution", cls.__name__), "attribution"),
            confidence=_enum(EvidenceConfidence, _required(data, "confidence", cls.__name__), "confidence"),
            verification_status=_enum(VerificationStatus, _required(data, "verification_status", cls.__name__), "verification_status"),
            available_as_of=_datetime(_required(data, "available_as_of", cls.__name__), "available_as_of"),
            citations=_models(_required(data, "citations", cls.__name__), Citation, "citations"),
            created_at=_datetime(_required(data, "created_at", cls.__name__), "created_at"),
            reported_for=_optional_text(data.get("reported_for"), "reported_for"),
            tags=_texts(data.get("tags", ()), "tags"),
            schema_version=_schema_version(data, cls.__name__),
        )


@dataclass(frozen=True)
class Company(DomainModel):
    company_id: str
    name: str
    ticker: str
    market: str
    research_status: ResearchStatus
    _json_fields = frozenset({"company_id", "name", "ticker", "market", "research_status"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Company:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            company_id=_text(_required(data, "company_id", cls.__name__), "company_id"),
            name=_text(_required(data, "name", cls.__name__), "name"),
            ticker=_text(_required(data, "ticker", cls.__name__), "ticker"),
            market=_text(_required(data, "market", cls.__name__), "market"),
            research_status=_enum(ResearchStatus, _required(data, "research_status", cls.__name__), "research_status"),
        )


@dataclass(frozen=True)
class Assumption(DomainModel):
    assumption_id: str
    statement: str
    indicator_ids: tuple[str, ...]
    falsification_condition_ids: tuple[str, ...]
    _json_fields = frozenset({"assumption_id", "statement", "indicator_ids", "falsification_condition_ids"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Assumption:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            assumption_id=_text(_required(data, "assumption_id", cls.__name__), "assumption_id"),
            statement=_text(_required(data, "statement", cls.__name__), "statement"),
            indicator_ids=_texts(_required(data, "indicator_ids", cls.__name__), "indicator_ids"),
            falsification_condition_ids=_texts(_required(data, "falsification_condition_ids", cls.__name__), "falsification_condition_ids"),
        )


@dataclass(frozen=True)
class KeyIndicator(DomainModel):
    indicator_id: str
    name: str
    why_it_matters: str
    linked_assumption_ids: tuple[str, ...]
    unit_or_definition: str | None = None
    _json_fields = frozenset({"indicator_id", "name", "why_it_matters", "unit_or_definition", "linked_assumption_ids"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> KeyIndicator:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            indicator_id=_text(_required(data, "indicator_id", cls.__name__), "indicator_id"),
            name=_text(_required(data, "name", cls.__name__), "name"),
            why_it_matters=_text(_required(data, "why_it_matters", cls.__name__), "why_it_matters"),
            linked_assumption_ids=_texts(_required(data, "linked_assumption_ids", cls.__name__), "linked_assumption_ids"),
            unit_or_definition=_optional_text(data.get("unit_or_definition"), "unit_or_definition"),
        )


@dataclass(frozen=True)
class FalsificationCondition(DomainModel):
    condition_id: str
    statement: str
    linked_assumption_ids: tuple[str, ...]
    _json_fields = frozenset({"condition_id", "statement", "linked_assumption_ids"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FalsificationCondition:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            condition_id=_text(_required(data, "condition_id", cls.__name__), "condition_id"),
            statement=_text(_required(data, "statement", cls.__name__), "statement"),
            linked_assumption_ids=_texts(_required(data, "linked_assumption_ids", cls.__name__), "linked_assumption_ids"),
        )


@dataclass(frozen=True)
class CounterCase(DomainModel):
    statement: str
    attacked_assumption_ids: tuple[str, ...]
    basis: str
    evidence_ids: tuple[str, ...] = ()
    _json_fields = frozenset({"statement", "attacked_assumption_ids", "basis", "evidence_ids"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CounterCase:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            statement=_text(_required(data, "statement", cls.__name__), "statement"),
            attacked_assumption_ids=_texts(_required(data, "attacked_assumption_ids", cls.__name__), "attacked_assumption_ids"),
            basis=_text(_required(data, "basis", cls.__name__), "basis"),
            evidence_ids=_texts(data.get("evidence_ids", ()), "evidence_ids"),
        )


@dataclass(frozen=True)
class ValuationAnchor(DomainModel):
    status: ValuationStatus
    valuation_basis: str | None = None
    reasonable_range: str | None = None
    market_implied_assumptions: tuple[str, ...] | None = None
    sensitive_variables: tuple[str, ...] | None = None
    insufficiency_reason: str | None = None
    _json_fields = frozenset({"status", "valuation_basis", "reasonable_range", "market_implied_assumptions", "sensitive_variables", "insufficiency_reason"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ValuationAnchor:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            status=_enum(ValuationStatus, _required(data, "status", cls.__name__), "status"),
            valuation_basis=_optional_text(data.get("valuation_basis"), "valuation_basis"),
            reasonable_range=_optional_text(data.get("reasonable_range"), "reasonable_range"),
            market_implied_assumptions=None if "market_implied_assumptions" not in data else _texts(data["market_implied_assumptions"], "market_implied_assumptions"),
            sensitive_variables=None if "sensitive_variables" not in data else _texts(data["sensitive_variables"], "sensitive_variables"),
            insufficiency_reason=_optional_text(data.get("insufficiency_reason"), "insufficiency_reason"),
        )


@dataclass(frozen=True)
class UnknownQuestion(DomainModel):
    question_id: str
    question: str
    linked_assumption_ids: tuple[str, ...]
    _json_fields = frozenset({"question_id", "question", "linked_assumption_ids"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> UnknownQuestion:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            question_id=_text(_required(data, "question_id", cls.__name__), "question_id"),
            question=_text(_required(data, "question", cls.__name__), "question"),
            linked_assumption_ids=_texts(_required(data, "linked_assumption_ids", cls.__name__), "linked_assumption_ids"),
        )


@dataclass(frozen=True)
class VersionMetadata(DomainModel):
    as_of_date: date
    version_id: str
    created_at: datetime
    updated_at: datetime
    supersedes: str | None
    user_confirmed: bool
    _json_fields = frozenset({"as_of_date", "version_id", "created_at", "updated_at", "supersedes", "user_confirmed"})
    _include_none_fields = frozenset({"supersedes"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> VersionMetadata:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            as_of_date=_date(_required(data, "as_of_date", cls.__name__), "as_of_date"),
            version_id=_text(_required(data, "version_id", cls.__name__), "version_id"),
            created_at=_datetime(_required(data, "created_at", cls.__name__), "created_at"),
            updated_at=_datetime(_required(data, "updated_at", cls.__name__), "updated_at"),
            supersedes=None if _required(data, "supersedes", cls.__name__) is None else _text(data["supersedes"], "supersedes"),
            user_confirmed=_boolean(_required(data, "user_confirmed", cls.__name__), "user_confirmed"),
        )


@dataclass(frozen=True)
class ThesisCard(DomainModel):
    thesis_id: str
    company: Company
    one_sentence_thesis: str
    assumptions: tuple[Assumption, ...]
    key_indicators: tuple[KeyIndicator, ...]
    falsification_conditions: tuple[FalsificationCondition, ...]
    strongest_counter_case: CounterCase
    valuation_anchor: ValuationAnchor
    unknown_questions: tuple[UnknownQuestion, ...]
    version: VersionMetadata
    tags: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    _json_fields = frozenset({"schema_version", "thesis_id", "company", "one_sentence_thesis", "assumptions", "key_indicators", "falsification_conditions", "strongest_counter_case", "valuation_anchor", "unknown_questions", "tags", "version"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ThesisCard:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            thesis_id=_text(_required(data, "thesis_id", cls.__name__), "thesis_id"),
            company=Company.from_dict(_required(data, "company", cls.__name__)),
            one_sentence_thesis=_text(_required(data, "one_sentence_thesis", cls.__name__), "one_sentence_thesis"),
            assumptions=_models(_required(data, "assumptions", cls.__name__), Assumption, "assumptions"),
            key_indicators=_models(_required(data, "key_indicators", cls.__name__), KeyIndicator, "key_indicators"),
            falsification_conditions=_models(_required(data, "falsification_conditions", cls.__name__), FalsificationCondition, "falsification_conditions"),
            strongest_counter_case=CounterCase.from_dict(_required(data, "strongest_counter_case", cls.__name__)),
            valuation_anchor=ValuationAnchor.from_dict(_required(data, "valuation_anchor", cls.__name__)),
            unknown_questions=_models(_required(data, "unknown_questions", cls.__name__), UnknownQuestion, "unknown_questions"),
            version=VersionMetadata.from_dict(_required(data, "version", cls.__name__)),
            tags=_texts(data.get("tags", ()), "tags"),
            schema_version=_schema_version(data, cls.__name__),
        )


@dataclass(frozen=True)
class AssumptionChange(DomainModel):
    assumption_id: str
    prior_statement: str
    impact: ChangeStatus
    confidence: Confidence
    evidence_ids: tuple[str, ...]
    rationale: str
    alternative_explanation: str
    triggered_falsification_condition_ids: tuple[str, ...] | None = None
    _json_fields = frozenset({"assumption_id", "prior_statement", "impact", "confidence", "evidence_ids", "rationale", "alternative_explanation", "triggered_falsification_condition_ids"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AssumptionChange:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            assumption_id=_text(_required(data, "assumption_id", cls.__name__), "assumption_id"),
            prior_statement=_text(_required(data, "prior_statement", cls.__name__), "prior_statement"),
            impact=_enum(ChangeStatus, _required(data, "impact", cls.__name__), "impact"),
            confidence=_enum(Confidence, _required(data, "confidence", cls.__name__), "confidence"),
            evidence_ids=_texts(_required(data, "evidence_ids", cls.__name__), "evidence_ids"),
            rationale=_text(_required(data, "rationale", cls.__name__), "rationale"),
            alternative_explanation=_text(_required(data, "alternative_explanation", cls.__name__), "alternative_explanation"),
            triggered_falsification_condition_ids=None
            if "triggered_falsification_condition_ids" not in data
            else _texts(
                data["triggered_falsification_condition_ids"],
                "triggered_falsification_condition_ids",
            ),
        )


@dataclass(frozen=True)
class ManagementComparison(DomainModel):
    comparison_id: str
    past_statement: str
    past_evidence_ids: tuple[str, ...]
    current_action_or_result: str
    current_evidence_ids: tuple[str, ...]
    assessment: ComparisonAssessment
    unresolved_part: str
    _json_fields = frozenset({"comparison_id", "past_statement", "past_evidence_ids", "current_action_or_result", "current_evidence_ids", "assessment", "unresolved_part"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ManagementComparison:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            comparison_id=_text(_required(data, "comparison_id", cls.__name__), "comparison_id"),
            past_statement=_text(_required(data, "past_statement", cls.__name__), "past_statement"),
            past_evidence_ids=_texts(_required(data, "past_evidence_ids", cls.__name__), "past_evidence_ids"),
            current_action_or_result=_text(_required(data, "current_action_or_result", cls.__name__), "current_action_or_result"),
            current_evidence_ids=_texts(_required(data, "current_evidence_ids", cls.__name__), "current_evidence_ids"),
            assessment=_enum(ComparisonAssessment, _required(data, "assessment", cls.__name__), "assessment"),
            unresolved_part=_text(_required(data, "unresolved_part", cls.__name__), "unresolved_part"),
        )


@dataclass(frozen=True)
class ManagementStatementAction(DomainModel):
    assessment: ManagementAssessment
    summary: str
    comparisons: tuple[ManagementComparison, ...]
    _json_fields = frozenset({"assessment", "summary", "comparisons"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ManagementStatementAction:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            assessment=_enum(ManagementAssessment, _required(data, "assessment", cls.__name__), "assessment"),
            summary=_text(_required(data, "summary", cls.__name__), "summary"),
            comparisons=_models(_required(data, "comparisons", cls.__name__), ManagementComparison, "comparisons"),
        )


@dataclass(frozen=True)
class TargetedCounterCase(DomainModel):
    argument: str
    attacked_assumption_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    why_plausible: str
    _json_fields = frozenset({"argument", "attacked_assumption_ids", "evidence_ids", "why_plausible"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TargetedCounterCase:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            argument=_text(_required(data, "argument", cls.__name__), "argument"),
            attacked_assumption_ids=_texts(_required(data, "attacked_assumption_ids", cls.__name__), "attacked_assumption_ids"),
            evidence_ids=_texts(_required(data, "evidence_ids", cls.__name__), "evidence_ids"),
            why_plausible=_text(_required(data, "why_plausible", cls.__name__), "why_plausible"),
        )


@dataclass(frozen=True)
class FollowUpQuestion(DomainModel):
    question_id: str
    question: str
    linked_assumption_ids: tuple[str, ...]
    information_value: str
    evidence_needed: str
    _json_fields = frozenset({"question_id", "question", "linked_assumption_ids", "information_value", "evidence_needed"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FollowUpQuestion:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            question_id=_text(_required(data, "question_id", cls.__name__), "question_id"),
            question=_text(_required(data, "question", cls.__name__), "question"),
            linked_assumption_ids=_texts(_required(data, "linked_assumption_ids", cls.__name__), "linked_assumption_ids"),
            information_value=_text(_required(data, "information_value", cls.__name__), "information_value"),
            evidence_needed=_text(_required(data, "evidence_needed", cls.__name__), "evidence_needed"),
        )


@dataclass(frozen=True)
class ChangeItem(DomainModel):
    change_id: str
    operation: ChangeOperation
    target_type: ChangeTargetType
    target_id: str | None
    summary: str
    rationale: str
    evidence_ids: tuple[str, ...]
    _json_fields = frozenset({"change_id", "operation", "target_type", "target_id", "summary", "rationale", "evidence_ids"})
    _include_none_fields = frozenset({"target_id"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ChangeItem:
        data = _object(payload, cls.__name__, cls._json_fields)
        raw_target = _required(data, "target_id", cls.__name__)
        return cls(
            change_id=_text(_required(data, "change_id", cls.__name__), "change_id"),
            operation=_enum(ChangeOperation, _required(data, "operation", cls.__name__), "operation"),
            target_type=_enum(ChangeTargetType, _required(data, "target_type", cls.__name__), "target_type"),
            target_id=None if raw_target is None else _text(raw_target, "target_id"),
            summary=_text(_required(data, "summary", cls.__name__), "summary"),
            rationale=_text(_required(data, "rationale", cls.__name__), "rationale"),
            evidence_ids=_texts(_required(data, "evidence_ids", cls.__name__), "evidence_ids"),
        )


@dataclass(frozen=True)
class ProposedPatch(DomainModel):
    base_thesis_id: str
    base_version_id: str
    change_items: tuple[ChangeItem, ...]
    proposed_thesis: ThesisCard
    patch_status: str = "pending_user_review"
    _json_fields = frozenset({"patch_status", "base_thesis_id", "base_version_id", "change_items", "proposed_thesis"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProposedPatch:
        data = _object(payload, cls.__name__, cls._json_fields)
        status = _text(_required(data, "patch_status", cls.__name__), "patch_status")
        if status != "pending_user_review":
            raise ValueError("patch_status must equal pending_user_review")
        return cls(
            base_thesis_id=_text(_required(data, "base_thesis_id", cls.__name__), "base_thesis_id"),
            base_version_id=_text(_required(data, "base_version_id", cls.__name__), "base_version_id"),
            change_items=_models(_required(data, "change_items", cls.__name__), ChangeItem, "change_items"),
            proposed_thesis=ThesisCard.from_dict(_required(data, "proposed_thesis", cls.__name__)),
            patch_status=status,
        )


@dataclass(frozen=True)
class ThesisDiff(DomainModel):
    thesis_diff_id: str
    company_id: str
    base_thesis_id: str
    base_version_id: str
    source_document_ids: tuple[str, ...]
    material_published_on: date
    analysis_cutoff_at: datetime
    generated_at: datetime
    overall_assessment: ChangeStatus
    overall_rationale: str
    assumption_changes: tuple[AssumptionChange, ...]
    management_statement_action: ManagementStatementAction
    targeted_counter_case: TargetedCounterCase
    follow_up_questions: tuple[FollowUpQuestion, ...]
    proposed_patch: ProposedPatch
    schema_version: str = SCHEMA_VERSION

    _json_fields = frozenset({"schema_version", "thesis_diff_id", "company_id", "base_thesis_id", "base_version_id", "source_document_ids", "material_published_on", "analysis_cutoff_at", "generated_at", "overall_assessment", "overall_rationale", "assumption_changes", "management_statement_action", "targeted_counter_case", "follow_up_questions", "proposed_patch"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ThesisDiff:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            thesis_diff_id=_text(_required(data, "thesis_diff_id", cls.__name__), "thesis_diff_id"),
            company_id=_text(_required(data, "company_id", cls.__name__), "company_id"),
            base_thesis_id=_text(_required(data, "base_thesis_id", cls.__name__), "base_thesis_id"),
            base_version_id=_text(_required(data, "base_version_id", cls.__name__), "base_version_id"),
            source_document_ids=_texts(_required(data, "source_document_ids", cls.__name__), "source_document_ids"),
            material_published_on=_date(_required(data, "material_published_on", cls.__name__), "material_published_on"),
            analysis_cutoff_at=_datetime(_required(data, "analysis_cutoff_at", cls.__name__), "analysis_cutoff_at"),
            generated_at=_datetime(_required(data, "generated_at", cls.__name__), "generated_at"),
            overall_assessment=_enum(ChangeStatus, _required(data, "overall_assessment", cls.__name__), "overall_assessment"),
            overall_rationale=_text(_required(data, "overall_rationale", cls.__name__), "overall_rationale"),
            assumption_changes=_models(_required(data, "assumption_changes", cls.__name__), AssumptionChange, "assumption_changes"),
            management_statement_action=ManagementStatementAction.from_dict(_required(data, "management_statement_action", cls.__name__)),
            targeted_counter_case=TargetedCounterCase.from_dict(_required(data, "targeted_counter_case", cls.__name__)),
            follow_up_questions=_models(_required(data, "follow_up_questions", cls.__name__), FollowUpQuestion, "follow_up_questions"),
            proposed_patch=ProposedPatch.from_dict(_required(data, "proposed_patch", cls.__name__)),
            schema_version=_schema_version(data, cls.__name__),
        )


@dataclass(frozen=True)
class ResearchTask(DomainModel):
    research_task_id: str
    question: str
    why_it_matters: str
    linked_assumption_ids: tuple[str, ...]
    status: str = "open"
    _json_fields = frozenset({"research_task_id", "question", "why_it_matters", "linked_assumption_ids", "status"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ResearchTask:
        data = _object(payload, cls.__name__, cls._json_fields)
        status = _text(_required(data, "status", cls.__name__), "status")
        if status != "open":
            raise ValueError("research task status must equal open")
        return cls(
            research_task_id=_text(_required(data, "research_task_id", cls.__name__), "research_task_id"),
            question=_text(_required(data, "question", cls.__name__), "question"),
            why_it_matters=_text(_required(data, "why_it_matters", cls.__name__), "why_it_matters"),
            linked_assumption_ids=_texts(_required(data, "linked_assumption_ids", cls.__name__), "linked_assumption_ids"),
            status=status,
        )


@dataclass(frozen=True)
class UserReview(DomainModel):
    user_review_id: str
    thesis_diff_id: str
    company_id: str
    base_thesis_id: str
    base_version_id: str
    decision: ReviewDecision
    reviewer_id: str
    reviewed_at: datetime
    comment: str | None = None
    reviewed_thesis: ThesisCard | None = None
    research_tasks: tuple[ResearchTask, ...] | None = None
    schema_version: str = SCHEMA_VERSION

    _json_fields = frozenset({"schema_version", "user_review_id", "thesis_diff_id", "company_id", "base_thesis_id", "base_version_id", "decision", "reviewer_id", "reviewed_at", "comment", "reviewed_thesis", "research_tasks"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> UserReview:
        data = _object(payload, cls.__name__, cls._json_fields)
        return cls(
            user_review_id=_text(_required(data, "user_review_id", cls.__name__), "user_review_id"),
            thesis_diff_id=_text(_required(data, "thesis_diff_id", cls.__name__), "thesis_diff_id"),
            company_id=_text(_required(data, "company_id", cls.__name__), "company_id"),
            base_thesis_id=_text(_required(data, "base_thesis_id", cls.__name__), "base_thesis_id"),
            base_version_id=_text(_required(data, "base_version_id", cls.__name__), "base_version_id"),
            decision=_enum(ReviewDecision, _required(data, "decision", cls.__name__), "decision"),
            reviewer_id=_text(_required(data, "reviewer_id", cls.__name__), "reviewer_id"),
            reviewed_at=_datetime(_required(data, "reviewed_at", cls.__name__), "reviewed_at"),
            comment=_optional_text(data.get("comment"), "comment"),
            reviewed_thesis=None if "reviewed_thesis" not in data else ThesisCard.from_dict(data["reviewed_thesis"]),
            research_tasks=None if "research_tasks" not in data else _models(data["research_tasks"], ResearchTask, "research_tasks"),
            schema_version=_schema_version(data, cls.__name__),
        )
