from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from thesisos.temporal import (
    TemporalValidationError,
    is_available_by,
    reject_future_documents,
)
from thesisos.validation import DomainValidationError, validate_thesis_diff

from tests.test_contracts import make_diff


UTC = timezone.utc


class TemporalGuardTests(unittest.TestCase):
    def test_availability_uses_public_availability_not_reporting_period(self) -> None:
        diff, _, _, document = make_diff()
        future_document = replace(
            document,
            reporting_period=replace(
                document.reporting_period,
                start_on=date(2024, 1, 1),
                end_on=date(2024, 12, 31),
            ),
            published_on=date(2025, 5, 21),
            publicly_available_at=datetime(2025, 5, 21, 8, 5, tzinfo=UTC),
            ingested_at=datetime(2025, 5, 21, 9, 0, tzinfo=UTC),
        )

        self.assertFalse(is_available_by(future_document, diff.analysis_cutoff_at))
        with self.assertRaisesRegex(TemporalValidationError, "after analysis_cutoff_at"):
            reject_future_documents((future_document,), diff.analysis_cutoff_at)

    def test_diff_rejects_future_source_document(self) -> None:
        diff, base, evidence, document = make_diff()
        future_document = replace(
            document,
            publicly_available_at=datetime(2025, 5, 21, 8, 5, tzinfo=UTC),
            ingested_at=datetime(2025, 5, 21, 9, 0, tzinfo=UTC),
        )
        with self.assertRaisesRegex(DomainValidationError, "after analysis_cutoff_at"):
            validate_thesis_diff(
                diff,
                base,
                {evidence.evidence_id: evidence},
                {future_document.source_document_id: future_document},
            )

    def test_evidence_available_after_cutoff_is_rejected(self) -> None:
        diff, base, evidence, document = make_diff()
        future_evidence = replace(
            evidence,
            available_as_of=datetime(2025, 5, 20, 11, 0, tzinfo=UTC),
            created_at=datetime(2025, 5, 20, 11, 1, tzinfo=UTC),
        )
        with self.assertRaisesRegex(DomainValidationError, "available_as_of is after"):
            validate_thesis_diff(
                diff,
                base,
                {future_evidence.evidence_id: future_evidence},
                {document.source_document_id: document},
            )

    def test_timezone_naive_cutoff_is_rejected(self) -> None:
        _, _, _, document = make_diff()
        naive_cutoff = datetime(2025, 5, 20, 10, 0)
        with self.assertRaisesRegex(TemporalValidationError, "timezone offset"):
            reject_future_documents((document,), naive_cutoff)

    def test_document_exactly_at_cutoff_is_allowed(self) -> None:
        diff, _, _, document = make_diff()
        at_cutoff = replace(
            document,
            publicly_available_at=diff.analysis_cutoff_at,
            ingested_at=diff.analysis_cutoff_at,
        )
        self.assertTrue(is_available_by(at_cutoff, diff.analysis_cutoff_at))
        reject_future_documents((at_cutoff,), diff.analysis_cutoff_at)


if __name__ == "__main__":
    unittest.main()
