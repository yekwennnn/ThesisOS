from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from thesisos.schema_validation import (
    SCHEMA_FILENAMES,
    SchemaCatalog,
    SchemaCatalogError,
    SchemaInstanceError,
    canonical_kind,
    discover_schema_directory,
    load_json_object,
)


ROOT = Path(__file__).resolve().parents[1]


def source_document() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "source_document_id": "doc-1",
        "company_id": "company-1",
        "title": "Quarterly results",
        "document_type": "earnings_release",
        "media_type": "pdf",
        "source_class": "primary",
        "language": "en",
        "published_on": "2024-05-14",
        "publicly_available_at": "2024-05-14T08:00:00Z",
        "reporting_period": {
            "kind": "fiscal_quarter",
            "label": "FY2024 Q4",
            "start_on": "2024-01-01",
            "end_on": "2024-03-31",
        },
        "original_uri": "https://example.test/results.pdf",
        "snapshot": {
            "sha256": "a" * 64,
            "storage_uri": "file:///immutable/doc-1.pdf",
            "byte_size": 128,
        },
        "ingested_at": "2024-05-14T08:05:00Z",
    }


def citation() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "citation_id": "citation-1",
        "source_document_id": "doc-1",
        "snapshot_sha256": "a" * 64,
        "quotation_mode": "exact_quote",
        "locator": {"kind": "page", "page": 2, "section": "Highlights"},
        "quoted_text": "Revenue increased.",
    }


class SchemaCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = SchemaCatalog()

    def test_loads_every_canonical_schema(self) -> None:
        self.assertEqual(set(self.catalog.schemas), set(SCHEMA_FILENAMES))

    def test_installed_package_schema_catalog_precedes_repository_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resource_root = Path(temporary)
            packaged = resource_root / "_schemas"
            packaged.mkdir()
            for filename in SCHEMA_FILENAMES.values():
                (packaged / filename).write_bytes((ROOT / "schemas" / filename).read_bytes())

            with patch(
                "thesisos.schema_validation.resources.files",
                return_value=resource_root,
            ):
                self.assertEqual(discover_schema_directory(), packaged.resolve())

    def test_explicit_schema_directory_never_silently_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(SchemaCatalogError, str(Path(temporary))):
                discover_schema_directory(temporary)

    def test_accepts_valid_source_document(self) -> None:
        payload = source_document()
        self.assertIs(self.catalog.validate("SourceDocument", payload), payload)

    def test_stable_ids_reject_nonportable_filesystem_names(self) -> None:
        for identifier in ("D:escape", "CON", "nul.txt", "trailing."):
            with self.subTest(identifier=identifier):
                payload = source_document()
                payload["company_id"] = identifier
                with self.assertRaises(SchemaInstanceError) as raised:
                    self.catalog.validate("SourceDocument", payload)
                self.assertTrue(
                    any(issue.instance_path == "/company_id" for issue in raised.exception.issues)
                )

    def test_resolves_external_citation_reference_in_evidence(self) -> None:
        payload = {
            "schema_version": "1.0.0",
            "evidence_id": "evidence-1",
            "company_id": "company-1",
            "statement": "Revenue increased.",
            "content_class": "source_fact",
            "attribution": "source_document",
            "confidence": "high",
            "verification_status": "verified",
            "available_as_of": "2024-05-14T08:00:00Z",
            "citations": [citation()],
            "created_at": "2024-05-14T08:05:00Z",
        }
        self.assertIs(self.catalog.validate("Evidence", payload), payload)

    def test_format_checker_rejects_impossible_date(self) -> None:
        payload = source_document()
        payload["published_on"] = "2024-02-31"
        with self.assertRaises(SchemaInstanceError) as raised:
            self.catalog.validate("SourceDocument", payload)
        self.assertTrue(any(issue.validator == "format" for issue in raised.exception.issues))
        self.assertTrue(any(issue.instance_path == "/published_on" for issue in raised.exception.issues))

    def test_errors_are_deterministic_and_machine_serializable(self) -> None:
        with self.assertRaises(SchemaInstanceError) as raised:
            self.catalog.validate("Citation", {"schema_version": "1.0.0"})
        issues = raised.exception.issues
        self.assertGreater(len(issues), 1)
        json.dumps([issue.to_dict() for issue in issues])

    def test_kind_aliases_are_canonicalized(self) -> None:
        self.assertEqual(canonical_kind("thesis-card"), "ThesisCard")
        self.assertEqual(canonical_kind("USER_REVIEW"), "UserReview")

    def test_change_item_target_identity_matches_its_target_type(self) -> None:
        fixture = json.loads(
            (
                ROOT
                / "examples"
                / "alibaba-2024-replay"
                / "thesis-diff-pending.json"
            ).read_text(encoding="utf-8")
        )
        singleton = fixture["proposed_patch"]["change_items"][0]
        singleton["target_id"] = "baba-a1-commerce"
        with self.assertRaises(SchemaInstanceError):
            self.catalog.validate("ThesisDiff", fixture)

        singleton["target_id"] = None
        collection = fixture["proposed_patch"]["change_items"][1]
        collection["target_id"] = None
        with self.assertRaises(SchemaInstanceError):
            self.catalog.validate("ThesisDiff", fixture)


class JsonLoadingTests(unittest.TestCase):
    def test_load_json_object_requires_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected a JSON object"):
                load_json_object(path)

    def test_load_json_object_reports_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text('{"x":', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1, column"):
                load_json_object(path)


if __name__ == "__main__":
    unittest.main()
