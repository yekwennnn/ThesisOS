from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from thesisos.api import create_app
from thesisos.application import ThesisOSService
from thesisos.model_runtime import ModelRunProvenance, ModelRunResult
from thesisos.providers import AppSettings, LocalObjectStorageProvider


class FakeFinanceProvider:
    @property
    def ready(self) -> bool:
        return True

    def resolve_instrument(self, symbol: str) -> dict[str, str]:
        return {"symbol": symbol, "name": "Example Co", "exchange": "XNAS"}


class FakeEvidenceModelProvider:
    @property
    def ready(self) -> bool:
        return True

    def run(self, *, task, request_metadata, inputs):
        self.assert_task = task
        document = inputs["source_document"]
        return ModelRunResult(
            output=[
                {
                    "schema_version": "1.0.0",
                    "evidence_id": "EV-1",
                    "company_id": document["company_id"],
                    "statement": "Revenue increased year over year.",
                    "content_class": "source_fact",
                    "attribution": "source_document",
                    "confidence": "high",
                    "verification_status": "unreviewed",
                    "available_as_of": document["publicly_available_at"],
                    "citations": [
                        {
                            "schema_version": "1.0.0",
                            "citation_id": "CIT-1",
                            "source_document_id": document["source_document_id"],
                            "snapshot_sha256": document["snapshot"]["sha256"],
                            "quotation_mode": "exact_quote",
                            "locator": {"kind": "line_range", "line_start": 1, "line_end": 1},
                            "quoted_text": "Revenue increased year over year.",
                        }
                    ],
                    "created_at": request_metadata["created_at"],
                }
            ],
            provenance=ModelRunProvenance(
                protocol="thesisos.model-adapter",
                protocol_version="1.0.0",
                task=task,
                contract_version="0.1.0",
                contract_sha256="a" * 64,
                model_identifier="fake-model",
                normalized_input_sha256="b" * 64,
                normalized_output_sha256="c" * 64,
                started_at="2024-05-14T12:00:00Z",
                finished_at="2024-05-14T12:00:01Z",
            ),
        )


def source_document(content: bytes) -> dict[str, object]:
    digest = hashlib.sha256(content).hexdigest()
    return {
        "schema_version": "1.0.0",
        "source_document_id": "doc-api-1",
        "company_id": "company-api-1",
        "title": "Quarterly update",
        "document_type": "earnings_release",
        "media_type": "plain_text",
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
        "snapshot": {
            "sha256": digest,
            "storage_uri": f"thesisos://sha256/{digest}",
            "byte_size": len(content),
        },
        "ingested_at": "2024-05-14T08:05:00Z",
    }


class ApiTest(unittest.TestCase):
    def settings(self, workspace: Path) -> AppSettings:
        return AppSettings(
            workspace=workspace,
            model_identifier="unconfigured",
            model_adapter_argv=(),
            max_upload_bytes=1024,
            max_source_chars=4096,
        )

    def test_health_reports_provider_readiness_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TestClient(create_app(self.settings(Path(tmp)))) as client:
                response = client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ok")
            self.assertFalse(response.json()["providers"]["model"]["configured"])
            self.assertNotIn("adapter_argv", response.text)

    def test_finance_symbol_resolution_is_bounded_and_provider_backed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                self.settings(Path(tmp)), finance_provider=FakeFinanceProvider()
            )
            with TestClient(app) as client:
                response = client.get(
                    "/v1/finance/instruments/resolve", params={"symbol": " exm "}
                )
                rejected = client.get(
                    "/v1/finance/instruments/resolve", params={"symbol": "x" * 33}
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["instrument"]["symbol"], "EXM")
            self.assertEqual(rejected.status_code, 422)

    def test_source_upload_ingests_and_is_idempotent(self) -> None:
        content = b"Revenue increased year over year.\n"
        metadata = source_document(content)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with TestClient(create_app(self.settings(workspace))) as client:
                first = client.post(
                    "/v1/sources/ingest",
                    data={"metadata": json.dumps(metadata)},
                    files={"source": ("results.txt", content, "text/plain")},
                )
                second = client.post(
                    "/v1/sources/ingest",
                    data={"metadata": json.dumps(metadata)},
                    files={"source": ("results.txt", content, "text/plain")},
                )
            self.assertEqual(first.status_code, 201, first.text)
            self.assertTrue(first.json()["object_created"])
            self.assertEqual(second.status_code, 201, second.text)
            self.assertFalse(second.json()["object_created"])
            self.assertTrue(
                (workspace / "companies" / "company-api-1" / "documents" / "doc-api-1.json").is_file()
            )

    def test_upload_limit_fails_without_creating_workspace_records(self) -> None:
        content = b"x" * 1025
        metadata = source_document(content)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with TestClient(create_app(self.settings(workspace))) as client:
                response = client.post(
                    "/v1/sources/ingest",
                    data={"metadata": json.dumps(metadata)},
                    files={"source": ("large.txt", content, "text/plain")},
                )
            self.assertEqual(response.status_code, 422)
            self.assertFalse((workspace / "companies").exists())

    def test_unreviewed_extraction_stays_in_model_run_until_explicit_review(self) -> None:
        content = b"Revenue increased year over year.\n"
        metadata = source_document(content)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            settings = self.settings(workspace)
            service = ThesisOSService(
                workspace=workspace,
                model_provider=FakeEvidenceModelProvider(),
                object_storage=LocalObjectStorageProvider(),
                max_upload_bytes=1024,
                max_source_chars=4096,
            )
            with TestClient(create_app(settings, service=service)) as client:
                ingest = client.post(
                    "/v1/sources/ingest",
                    data={"metadata": json.dumps(metadata)},
                    files={"source": ("results.txt", content, "text/plain")},
                )
                self.assertEqual(ingest.status_code, 201, ingest.text)
                extracted = client.post(
                    "/v1/companies/company-api-1/evidence/extract",
                    json={
                        "source_document_id": "doc-api-1",
                        "model_run_id": "run-1",
                        "request_metadata": {
                            "analysis_cutoff_at": "2024-05-14T12:00:00Z",
                            "evidence_id_prefix": "EV-",
                            "citation_id_prefix": "CIT-",
                            "created_at": "2024-05-14T12:00:00Z",
                            "extraction_scope": ["revenue durability"],
                        },
                    },
                )
                self.assertEqual(extracted.status_code, 201, extracted.text)
                evidence_path = workspace / "companies" / "company-api-1" / "evidence" / "EV-1.json"
                self.assertFalse(evidence_path.exists())
                run_path = workspace / "companies" / "company-api-1" / "model_runs" / "run-1.json"
                self.assertTrue(run_path.is_file())
                self.assertEqual(json.loads(run_path.read_text())["output"][0]["evidence_id"], "EV-1")

                forbidden_edit = client.post(
                    "/v1/companies/company-api-1/model-runs/run-1/evidence/EV-1/review",
                    json={
                        "evidence_review_id": "evidence-review-invalid",
                        "model_run_id": "run-1",
                        "evidence_id": "EV-1",
                        "decision": "correct_statement",
                        "reviewer_id": "user-1",
                        "reviewed_at": "2024-05-14T12:04:00Z",
                        "corrected_statement": "Revenue grew year over year.",
                        "citations": [],
                    },
                )
                self.assertEqual(forbidden_edit.status_code, 422)
                self.assertFalse(evidence_path.exists())

                reviewed = client.post(
                    "/v1/companies/company-api-1/model-runs/run-1/evidence/EV-1/review",
                    json={
                        "evidence_review_id": "evidence-review-1",
                        "model_run_id": "run-1",
                        "evidence_id": "EV-1",
                        "decision": "confirm",
                        "reviewer_id": "user-1",
                        "reviewed_at": "2024-05-14T12:05:00Z",
                    },
                )
                self.assertEqual(reviewed.status_code, 201, reviewed.text)
            self.assertTrue(evidence_path.is_file())
            admitted = json.loads(evidence_path.read_text())
            self.assertEqual(admitted["verification_status"], "verified")
            self.assertEqual(admitted["citations"], extracted.json()["evidence"][0]["citations"])


if __name__ == "__main__":
    unittest.main()
