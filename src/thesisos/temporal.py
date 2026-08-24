"""Time-cutoff rules used by historical replay and ThesisDiff validation."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .models import SourceDocument


class TemporalValidationError(ValueError):
    """Raised when a timestamp is ambiguous or future information leaks in."""

    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(issues)
        super().__init__("; ".join(self.issues))


def is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def ensure_timezone_aware(value: datetime, field_name: str) -> None:
    if not is_timezone_aware(value):
        raise TemporalValidationError((f"{field_name} must include a timezone offset",))


def is_available_by(document: SourceDocument, analysis_cutoff: datetime) -> bool:
    """Return whether a document was actually available by the replay cutoff.

    The reporting period is intentionally ignored: a report about an old period
    can still be future information when it was published after the cutoff.
    """

    ensure_timezone_aware(
        document.publicly_available_at,
        f"document {document.source_document_id} publicly_available_at",
    )
    ensure_timezone_aware(analysis_cutoff, "analysis_cutoff_at")
    return document.publicly_available_at <= analysis_cutoff


def reject_future_documents(
    documents: Iterable[SourceDocument], analysis_cutoff: datetime
) -> None:
    """Reject every source that was not available at the analysis cutoff."""

    issues: list[str] = []
    try:
        ensure_timezone_aware(analysis_cutoff, "analysis_cutoff_at")
    except TemporalValidationError as exc:
        issues.extend(exc.issues)
        raise TemporalValidationError(issues) from exc

    for document in documents:
        try:
            ensure_timezone_aware(
                document.publicly_available_at,
                f"document {document.source_document_id} publicly_available_at",
            )
        except TemporalValidationError as exc:
            issues.extend(exc.issues)
            continue
        if document.publicly_available_at > analysis_cutoff:
            issues.append(
                f"document {document.source_document_id} became available at "
                f"{document.publicly_available_at.isoformat()}, after analysis_cutoff_at "
                f"{analysis_cutoff.isoformat()}"
            )
    if issues:
        raise TemporalValidationError(issues)
