from __future__ import annotations

import json
import unittest
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

from thesisos.model_runtime import PROTOCOL_NAME, PROTOCOL_VERSION
from thesisos.openai_adapter import (
    AdapterConfig,
    OpenAIAdapterResponseError,
    execute,
    structured_output_schema,
)
from thesisos.schema_validation import SchemaCatalog


def envelope(task: str = "evidence-extraction") -> dict[str, object]:
    return {
        "protocol": PROTOCOL_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "task": task,
        "contract": {"content": "Canonical prompt text"},
        "model_identifier": "gpt-test",
        "request_metadata": {"analysis_cutoff_at": "2024-05-14T12:00:00Z"},
        "inputs": {"source_document": {"source_document_id": "doc-1"}},
    }


def evidence() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "evidence_id": "evidence-1",
        "company_id": "company-1",
        "statement": "Revenue increased year over year.",
        "content_class": "source_fact",
        "attribution": "source_document",
        "confidence": "high",
        "verification_status": "unreviewed",
        "available_as_of": "2024-05-14T08:00:00Z",
        "citations": [
            {
                "schema_version": "1.0.0",
                "citation_id": "citation-1",
                "source_document_id": "doc-1",
                "snapshot_sha256": "a" * 64,
                "quotation_mode": "exact_quote",
                "locator": {"kind": "page", "page": 1},
                "quoted_text": "Revenue increased year over year.",
            }
        ],
        "created_at": "2024-05-14T12:00:00Z",
    }


class FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _: int) -> bytes:
        return self.payload


class OpenAIAdapterTest(unittest.TestCase):
    def config(self) -> AdapterConfig:
        return AdapterConfig(
            api_key="server-secret",
            base_url="https://api.openai.test/v1",
            timeout_seconds=2,
            max_retries=2,
            retry_base_seconds=0,
            max_output_tokens=4000,
        )

    def test_completed_response_uses_structured_schema_and_unwraps_evidence(self) -> None:
        captured = {}

        def opener(request, *, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers["Authorization"]
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data)
            return FakeResponse(
                {
                    "status": "completed",
                    "output_text": json.dumps({"evidence": [evidence()]}),
                    "output": [],
                }
            )

        result = execute(envelope(), config=self.config(), opener=opener)
        self.assertEqual(result, [evidence()])
        self.assertEqual(captured["url"], "https://api.openai.test/v1/responses")
        self.assertEqual(captured["authorization"], "Bearer server-secret")
        self.assertEqual(captured["timeout"], 2)
        body = captured["body"]
        self.assertFalse(body["store"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertNotIn("server-secret", json.dumps(body))

    def test_retryable_http_failure_retries_with_bound(self) -> None:
        attempts = []
        headers = Message()
        headers["Retry-After"] = "0"

        def opener(request, *, timeout):
            attempts.append(1)
            if len(attempts) == 1:
                raise HTTPError(request.full_url, 429, "rate limited", headers, BytesIO())
            return FakeResponse(
                {
                    "status": "completed",
                    "output_text": json.dumps({"evidence": []}),
                    "output": [],
                }
            )

        self.assertEqual(execute(envelope(), config=self.config(), opener=opener), [])
        self.assertEqual(len(attempts), 2)

    def test_refusal_and_incomplete_fail_closed(self) -> None:
        refusal = FakeResponse(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "no"}],
                    }
                ],
            }
        )
        incomplete = FakeResponse(
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
            }
        )
        with self.assertRaisesRegex(OpenAIAdapterResponseError, "refused"):
            execute(envelope(), config=self.config(), opener=lambda *args, **kwargs: refusal)
        with self.assertRaisesRegex(OpenAIAdapterResponseError, "max_output_tokens"):
            execute(envelope(), config=self.config(), opener=lambda *args, **kwargs: incomplete)

    def test_canonical_schemas_are_bundled_without_remote_refs(self) -> None:
        catalog = SchemaCatalog()
        evidence_schema = structured_output_schema("evidence-extraction", catalog)
        diff_schema = structured_output_schema("thesis-diff", catalog)
        self.assertNotIn('"citation.schema.json"', json.dumps(evidence_schema))
        self.assertNotIn('"thesis-card.schema.json"', json.dumps(diff_schema))
        self.assertIn("citation__pageLocator", evidence_schema["$defs"])
        self.assertIn("thesis_card", diff_schema["$defs"])


if __name__ == "__main__":
    unittest.main()
