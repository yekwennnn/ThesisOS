from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from thesisos.snapshots import (
    SnapshotCollisionError,
    ingest_snapshot,
    storage_uri_for_sha256,
)
from thesisos.source_text import (
    CitationBindingError,
    CitationTextStatus,
    LocatorResolutionError,
    SourceTextExtractionError,
    extract_source_text,
    verify_citation_text,
    verify_managed_citation,
)


def pdf_with_text_pages(*page_texts: str) -> bytes:
    """Build a tiny real PDF whose text is extractable by pypdf."""

    output = BytesIO()
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_reference = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        content = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content.set_data(
            f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(output)
    return output.getvalue()


class SourceTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def ingest(
        self,
        content: bytes,
        media_type: str,
        *,
        document_id: str = "doc-1",
        page_count: int | None = None,
    ) -> dict[str, object]:
        digest = hashlib.sha256(content).hexdigest()
        document: dict[str, object] = {
            "source_document_id": document_id,
            "company_id": "company-1",
            "media_type": media_type,
            "snapshot": {
                "sha256": digest,
                "storage_uri": storage_uri_for_sha256(digest),
                "byte_size": len(content),
            },
        }
        if page_count is not None:
            document["page_count"] = page_count
        suffix = {"plain_text": ".txt", "markdown": ".md", "pdf": ".pdf"}[
            media_type
        ]
        source = self.root / f"{document_id}{suffix}"
        source.write_bytes(content)
        ingest_snapshot(self.workspace, source, document)
        return document

    def citation(
        self,
        document: dict[str, object],
        *,
        quoted_text: str,
        locator: dict[str, object],
        quotation_mode: str = "exact_quote",
    ) -> dict[str, object]:
        snapshot = document["snapshot"]
        assert isinstance(snapshot, dict)
        return {
            "citation_id": "citation-1",
            "source_document_id": document["source_document_id"],
            "snapshot_sha256": snapshot["sha256"],
            "quotation_mode": quotation_mode,
            "locator": locator,
            "quoted_text": quoted_text,
        }

    def test_plain_text_utf8_has_stable_one_based_view_and_exact_quote(self) -> None:
        content = "Overview\n\nRevenue   was 42％.\n经营稳定。\n".encode("utf-8")
        document = self.ingest(content, "plain_text")
        view = extract_source_text(self.workspace, document)

        self.assertEqual(view.page_count, 1)
        self.assertEqual([line.number for line in view.lines], [1, 2, 3, 4])
        self.assertEqual([line.page_line_number for line in view.lines], [1, 2, 3, 4])
        self.assertEqual([item.number for item in view.paragraphs], [1, 2])
        result = verify_citation_text(
            view,
            document,
            self.citation(
                document,
                quoted_text="Revenue was 42%. 经营稳定。",
                locator={"kind": "page", "page": 1},
            ),
        )

        self.assertTrue(result.passed, result.to_dict())
        self.assertEqual(result.status, CitationTextStatus.VERIFIED)
        self.assertEqual(result.scope_reference, "page 1")

    def test_markdown_line_paragraph_section_and_table_scopes(self) -> None:
        markdown = (
            "# Overview\n"
            "General context.\n\n"
            "# Metrics\n"
            "Revenue   increased.\n\n"
            "Table 1 Revenue\n"
            "| Metric | FY2024 |\n"
            "| Revenue | RMB 941,168 million |\n\n"
            "# Risks\n"
            "Demand slowed.\n"
        ).encode("utf-8")
        document = self.ingest(markdown, "markdown")
        view = extract_source_text(self.workspace, document)

        checks = (
            (
                "line range",
                "Revenue increased.",
                {"kind": "line_range", "line_start": 5, "line_end": 5},
                "exact_quote",
            ),
            (
                "paragraph",
                "Table 1 Revenue | Metric | FY2024 |",
                {"kind": "paragraph", "paragraph_start": 3, "paragraph_end": 3},
                "exact_quote",
            ),
            (
                "section",
                "Revenue increased.",
                {"kind": "section", "section": "Metrics"},
                "exact_quote",
            ),
            (
                "table",
                "RMB 941,168 million",
                {
                    "kind": "table",
                    "table": "Table 1 Revenue",
                    "page": 1,
                    "row": "Revenue",
                    "column": "FY2024",
                },
                "table_value",
            ),
        )
        for label, quote, locator, mode in checks:
            with self.subTest(locator=label):
                result = verify_citation_text(
                    view,
                    document,
                    self.citation(
                        document,
                        quoted_text=quote,
                        locator=locator,
                        quotation_mode=mode,
                    ),
                )
                self.assertTrue(result.passed, result.to_dict())

        wrong_section = verify_citation_text(
            view,
            document,
            self.citation(
                document,
                quoted_text="Demand slowed.",
                locator={"kind": "section", "section": "Metrics"},
            ),
        )
        self.assertEqual(wrong_section.status, CitationTextStatus.QUOTE_NOT_FOUND)
        self.assertFalse(wrong_section.passed)

    def test_missing_literal_does_not_pass_after_normalization(self) -> None:
        document = self.ingest(b"Revenue was 42%.\n", "plain_text")
        result = verify_managed_citation(
            self.workspace,
            document,
            self.citation(
                document,
                quoted_text="Revenue was 43%.",
                locator={"kind": "page", "page": 1},
            ),
        )
        self.assertEqual(result.status, CitationTextStatus.QUOTE_NOT_FOUND)
        self.assertFalse(result.passed)

    def test_table_value_requires_the_complete_normalized_cell(self) -> None:
        content = (
            "Table 1 Margins\n"
            "| Metric | FY2024 |\n"
            "| Margin | -19.0% |\n"
        ).encode("utf-8")
        document = self.ingest(content, "markdown")
        view = extract_source_text(self.workspace, document)
        locator = {
            "kind": "table",
            "table": "Table 1 Margins",
            "page": 1,
            "row": "Margin",
            "column": "FY2024",
        }

        for quote in ("19.0", "-19.0", "19.0%"):
            with self.subTest(quote=quote):
                result = verify_citation_text(
                    view,
                    document,
                    self.citation(
                        document,
                        quoted_text=quote,
                        locator=locator,
                        quotation_mode="table_value",
                    ),
                )
                self.assertEqual(result.status, CitationTextStatus.QUOTE_NOT_FOUND)
        exact = verify_citation_text(
            view,
            document,
            self.citation(
                document,
                quoted_text="-19.0%",
                locator=locator,
                quotation_mode="table_value",
            ),
        )
        self.assertTrue(exact.passed, exact.to_dict())

    def test_numeric_quote_cannot_match_inside_a_different_value(self) -> None:
        document = self.ingest(b"Margin was 18% and revenue was 142.5.\n", "plain_text")
        view = extract_source_text(self.workspace, document)
        for quote in ("8%", "42", "142"):
            with self.subTest(quote=quote):
                result = verify_citation_text(
                    view,
                    document,
                    self.citation(
                        document,
                        quoted_text=quote,
                        locator={"kind": "page", "page": 1},
                    ),
                )
                self.assertEqual(result.status, CitationTextStatus.QUOTE_NOT_FOUND)

    def test_citation_snapshot_binding_must_match_the_text_view(self) -> None:
        document = self.ingest(b"Revenue was 42%.\n", "plain_text")
        view = extract_source_text(self.workspace, document)
        citation = self.citation(
            document,
            quoted_text="Revenue was 42%.",
            locator={"kind": "page", "page": 1},
        )
        citation["snapshot_sha256"] = "0" * 64
        with self.assertRaisesRegex(CitationBindingError, "SHA-256"):
            verify_citation_text(view, document, citation)

    def test_invalid_page_line_paragraph_section_and_table_fail_closed(self) -> None:
        document = self.ingest(b"Heading\n\nOnly paragraph.\n", "plain_text")
        view = extract_source_text(self.workspace, document)
        locators = (
            {"kind": "page", "page": 2},
            {"kind": "line_range", "line_start": 2, "line_end": 99},
            {"kind": "line_range", "line_start": 3, "line_end": 2},
            {"kind": "paragraph", "paragraph_start": 1, "paragraph_end": 99},
            {"kind": "section", "section": "Missing"},
            {"kind": "table", "table": "Missing table"},
        )
        for locator in locators:
            with self.subTest(locator=locator):
                with self.assertRaises(LocatorResolutionError):
                    verify_citation_text(
                        view,
                        document,
                        self.citation(
                            document,
                            quoted_text="Only paragraph.",
                            locator=locator,
                        ),
                    )

    def test_table_row_and_column_are_executable_anchors(self) -> None:
        content = b"Table A\nMetric | FY2024\nRevenue | 100\n"
        document = self.ingest(content, "plain_text")
        view = extract_source_text(self.workspace, document)
        citation = self.citation(
            document,
            quoted_text="100",
            quotation_mode="table_value",
            locator={
                "kind": "table",
                "table": "Table A",
                "row": "Missing row",
                "column": "FY2024",
            },
        )
        with self.assertRaisesRegex(LocatorResolutionError, "row label"):
            verify_citation_text(view, document, citation)

    def test_table_value_cannot_match_a_different_table_on_the_same_page(self) -> None:
        content = (
            b"Table A\nMetric | FY2024\nRevenue | 100\n"
            b"Table B\nMetric | FY2024\nCosts | 999\n"
        )
        document = self.ingest(content, "plain_text")
        view = extract_source_text(self.workspace, document)
        result = verify_citation_text(
            view,
            document,
            self.citation(
                document,
                quoted_text="999",
                quotation_mode="table_value",
                locator={
                    "kind": "table",
                    "table": "Table A",
                    "row": "Revenue",
                    "column": "FY2024",
                },
            ),
        )
        self.assertEqual(result.status, CitationTextStatus.QUOTE_NOT_FOUND)
        self.assertFalse(result.passed)

        with self.assertRaisesRegex(LocatorResolutionError, "row label"):
            verify_citation_text(
                view,
                document,
                self.citation(
                    document,
                    quoted_text="999",
                    quotation_mode="table_value",
                    locator={
                        "kind": "table",
                        "table": "Table A",
                        "row": "Costs",
                        "column": "FY2024",
                    },
                ),
            )

    def test_table_value_does_not_consume_trailing_page_prose(self) -> None:
        document = self.ingest(
            (
                b"Table A\nMetric | FY2024\nRevenue | 100\n"
                b"Narrative costs  were 999\n"
            ),
            "plain_text",
        )
        view = extract_source_text(self.workspace, document)

        with self.assertRaisesRegex(LocatorResolutionError, "row label"):
            verify_citation_text(
                view,
                document,
                self.citation(
                    document,
                    quoted_text="999",
                    quotation_mode="table_value",
                    locator={
                        "kind": "table",
                        "table": "Table A",
                        "row": "Narrative costs",
                        "column": "FY2024",
                    },
                ),
            )

    def test_table_value_requires_prior_header_and_structured_data_row(self) -> None:
        structured = self.ingest(
            b"Table A\nMetric | FY2024\nRevenue | 100\n",
            "plain_text",
            document_id="structured-table",
        )
        structured_view = extract_source_text(self.workspace, structured)
        with self.assertRaisesRegex(LocatorResolutionError, "header cells before row"):
            verify_citation_text(
                structured_view,
                structured,
                self.citation(
                    structured,
                    quoted_text="100",
                    quotation_mode="table_value",
                    locator={
                        "kind": "table",
                        "table": "Table A",
                        "row": "Revenue",
                        "column": "100",
                    },
                ),
            )

        malformed = self.ingest(
            b"Table B\nMetric | FY2024\nRevenue 100\n",
            "plain_text",
            document_id="malformed-table",
        )
        malformed_view = extract_source_text(self.workspace, malformed)
        with self.assertRaisesRegex(LocatorResolutionError, "recognizable column mapping"):
            verify_citation_text(
                malformed_view,
                malformed,
                self.citation(
                    malformed,
                    quoted_text="100",
                    quotation_mode="table_value",
                    locator={
                        "kind": "table",
                        "table": "Table B",
                        "row": "Revenue",
                        "column": "FY2024",
                    },
                ),
            )

    def test_unstructured_section_without_end_boundary_fails_closed(self) -> None:
        document = self.ingest(
            b"Operations\nRevenue was 100.\nRisks\nDemand slowed.\n",
            "plain_text",
        )
        view = extract_source_text(self.workspace, document)
        with self.assertRaisesRegex(LocatorResolutionError, "no executable end boundary"):
            verify_citation_text(
                view,
                document,
                self.citation(
                    document,
                    quoted_text="Demand slowed.",
                    locator={"kind": "section", "section": "Operations"},
                ),
            )

    def test_table_value_requires_row_and_column_for_automatic_verification(self) -> None:
        document = self.ingest(
            b"Table A\nMetric | FY2024\nRevenue | 100\n",
            "plain_text",
        )
        view = extract_source_text(self.workspace, document)
        for locator in (
            {"kind": "table", "table": "Table A"},
            {"kind": "table", "table": "Table A", "row": "Revenue"},
        ):
            with self.subTest(locator=locator):
                with self.assertRaisesRegex(
                    LocatorResolutionError,
                    "automatically verified table_value requires",
                ):
                    verify_citation_text(
                        view,
                        document,
                        self.citation(
                            document,
                            quoted_text="100",
                            quotation_mode="table_value",
                            locator=locator,
                        ),
                    )

    def test_faithful_paraphrase_never_masquerades_as_automatic_pass(self) -> None:
        document = self.ingest(b"Revenue was 42%.\n", "plain_text")
        result = verify_managed_citation(
            self.workspace,
            document,
            self.citation(
                document,
                quoted_text="Revenue was 42%.",
                locator={"kind": "page", "page": 1},
                quotation_mode="faithful_paraphrase",
            ),
        )
        self.assertEqual(
            result.status,
            CitationTextStatus.SEMANTIC_REVIEW_REQUIRED,
        )
        self.assertFalse(result.passed)
        self.assertTrue(result.requires_human_review)
        self.assertIn("not automatically verified", result.detail)

    def test_real_pdf_text_uses_physical_pages_and_rejects_bad_bounds(self) -> None:
        content = pdf_with_text_pages(
            "First page revenue 42%.",
            "Second page cloud growth 3%.",
        )
        document = self.ingest(content, "pdf", page_count=2)
        view = extract_source_text(self.workspace, document)

        self.assertEqual(view.page_count, 2)
        result = verify_citation_text(
            view,
            document,
            self.citation(
                document,
                quoted_text="Second page cloud growth 3%.",
                locator={"kind": "page", "page": 2},
            ),
        )
        self.assertTrue(result.passed, result.to_dict())

        with self.assertRaisesRegex(LocatorResolutionError, "exceeds 2"):
            verify_citation_text(
                view,
                document,
                self.citation(
                    document,
                    quoted_text="Second page cloud growth 3%.",
                    locator={"kind": "page", "page": 3},
                ),
            )

        wrong_count = copy.deepcopy(document)
        wrong_count["page_count"] = 3
        with self.assertRaisesRegex(SourceTextExtractionError, "does not match"):
            extract_source_text(self.workspace, wrong_count)

    def test_invalid_utf8_is_rejected(self) -> None:
        document = self.ingest(b"valid prefix\xff", "markdown")
        with self.assertRaisesRegex(SourceTextExtractionError, "valid UTF-8"):
            extract_source_text(self.workspace, document)

    def test_snapshot_tampering_is_rejected_by_existing_snapshot_layer(self) -> None:
        document = self.ingest(b"original source", "plain_text")
        snapshot = document["snapshot"]
        assert isinstance(snapshot, dict)
        digest = snapshot["sha256"]
        assert isinstance(digest, str)
        object_path = self.workspace / "objects" / "sha256" / digest[:2] / digest
        object_path.write_bytes(b"tampered source")

        with self.assertRaises(SnapshotCollisionError):
            extract_source_text(self.workspace, document)


if __name__ == "__main__":
    unittest.main()
