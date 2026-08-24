from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from thesisos.evaluation import evaluate_case_file, evaluate_replay
from thesisos.models import Evidence, SourceDocument, ThesisCard, ThesisDiff, UserReview
from thesisos.schema_validation import SchemaCatalog
from thesisos.validation import (
    validate_evidence,
    validate_source_document,
    validate_thesis_card,
    validate_thesis_diff,
    validate_user_review,
)
from thesisos.versioning import (
    apply_user_review,
    commit_thesis_version,
    read_current_thesis,
    read_thesis_version,
    save_company_artifact,
)


REPOSITORY = Path(__file__).resolve().parents[1]
CASE_PATH = REPOSITORY / "evals" / "historical-replay" / "alibaba-2024-q4" / "case.json"
EXAMPLE = REPOSITORY / "examples" / "alibaba-2024-replay"


def load_object(name: str) -> dict:
    value = json.loads((EXAMPLE / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object in {name}")
    return value


def load_array(name: str) -> list[dict]:
    value = json.loads((EXAMPLE / name).read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise AssertionError(f"expected object array in {name}")
    return value


class AlibabaHistoricalReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = SchemaCatalog()
        self.case_manifest = json.loads(CASE_PATH.read_text(encoding="utf-8"))
        self.base_payload = load_object("thesis-v1-confirmed.json")
        self.diff_payload = load_object("thesis-diff-pending.json")
        self.review_payload = load_object("user-review.json")
        self.accepted_payload = load_object("thesis-v2-confirmed.json")
        self.document_payloads = [
            load_object("source-base-2024-02-07.json"),
            load_object("source-new-2024-05-14.json"),
        ]
        self.evidence_payloads = [
            *load_array("evidence-base.json"),
            *load_array("evidence-new.json"),
        ]

    def evaluate_payloads(
        self,
        *,
        base: dict | None = None,
        documents: list[dict] | None = None,
        evidence: list[dict] | None = None,
        diff: dict | None = None,
    ):
        return evaluate_replay(
            base_thesis=self.base_payload if base is None else base,
            documents=self.document_payloads if documents is None else documents,
            evidence=self.evidence_payloads if evidence is None else evidence,
            thesis_diff=self.diff_payload if diff is None else diff,
            base_analysis_cutoff_at=self.case_manifest["base_analysis_cutoff_at"],
            base_source_document_ids=self.case_manifest["base_source_document_ids"],
            base_evidence_ids=self.case_manifest["base_evidence_ids"],
            expected_assumption_evidence_ids=self.case_manifest[
                "expected_assumption_evidence_ids"
            ],
            critical_financial_evidence_ids=self.case_manifest[
                "critical_financial_evidence_ids"
            ],
            key_fact_evidence_ids=self.case_manifest["key_fact_evidence_ids"],
            user_review=self.review_payload,
            accepted_thesis=self.accepted_payload,
        )

    def test_real_replay_meets_readme_acceptance_metrics(self) -> None:
        report = evaluate_case_file(CASE_PATH)
        self.assertTrue(report.passed, report.to_dict())
        by_name = {check.name: check for check in report.checks}
        self.assertEqual(
            (by_name["critical_financial_source_coverage"].numerator,
             by_name["critical_financial_source_coverage"].denominator),
            (12, 12),
        )
        self.assertEqual(
            (by_name["key_fact_traceability"].numerator,
             by_name["key_fact_traceability"].denominator),
            (20, 20),
        )
        self.assertTrue(by_name["base_replay_cutoff_integrity"].passed)
        self.assertTrue(by_name["golden_assumption_evidence_mapping"].passed)
        self.assertTrue(by_name["future_information_leakage"].passed)

    def test_base_cutoff_rejects_future_baseline_evidence(self) -> None:
        evidence = copy.deepcopy(self.evidence_payloads)
        item = next(
            value for value in evidence if value["evidence_id"] == "baba-base-revenue"
        )
        item["available_as_of"] = "2024-02-08T00:00:00Z"
        report = self.evaluate_payloads(evidence=evidence)
        check = next(
            value
            for value in report.checks
            if value.name == "base_replay_cutoff_integrity"
        )
        self.assertFalse(check.passed)
        self.assertIn("was not available by the base cutoff", check.detail)

    def test_base_cutoff_rejects_future_baseline_document(self) -> None:
        documents = copy.deepcopy(self.document_payloads)
        item = next(
            value
            for value in documents
            if value["source_document_id"] == "baba-results-2024-02-07"
        )
        item["publicly_available_at"] = "2024-02-08T00:00:00Z"
        report = self.evaluate_payloads(documents=documents)
        check = next(
            value
            for value in report.checks
            if value.name == "base_replay_cutoff_integrity"
        )
        self.assertFalse(check.passed)
        self.assertIn("was not public by the base cutoff", check.detail)

    def test_base_cutoff_rejects_v1_created_after_cutoff(self) -> None:
        base = copy.deepcopy(self.base_payload)
        base["version"]["created_at"] = "2024-02-08T00:00:00Z"
        base["version"]["updated_at"] = "2024-02-08T00:00:00Z"
        report = self.evaluate_payloads(base=base)
        check = next(
            value
            for value in report.checks
            if value.name == "base_replay_cutoff_integrity"
        )
        self.assertFalse(check.passed)
        self.assertIn("created_at follows the base cutoff", check.detail)

    def test_base_thesis_rejects_unknown_counter_case_evidence(self) -> None:
        base = copy.deepcopy(self.base_payload)
        base["strongest_counter_case"]["evidence_ids"].append("missing-base-evidence")
        report = self.evaluate_payloads(base=base)
        check = next(
            value
            for value in report.checks
            if value.name == "base_replay_cutoff_integrity"
        )
        self.assertFalse(check.passed)
        self.assertIn("references unknown evidence missing-base-evidence", check.detail)

    def test_golden_mapping_rejects_evidence_swapped_between_assumptions(self) -> None:
        diff = copy.deepcopy(self.diff_payload)
        changes = {
            item["assumption_id"]: item for item in diff["assumption_changes"]
        }
        first = changes["baba-a1-commerce"]["evidence_ids"]
        third = changes["baba-a3-international"]["evidence_ids"]
        changes["baba-a1-commerce"]["evidence_ids"] = third
        changes["baba-a3-international"]["evidence_ids"] = first
        report = self.evaluate_payloads(diff=diff)
        check = next(
            value
            for value in report.checks
            if value.name == "golden_assumption_evidence_mapping"
        )
        self.assertFalse(check.passed)
        self.assertIn("differs from golden mapping", check.detail)

    def test_citation_page_must_fit_immutable_pdf_snapshot(self) -> None:
        evidence = copy.deepcopy(self.evidence_payloads)
        item = next(
            value for value in evidence if value["evidence_id"] == "baba-base-revenue"
        )
        item["citations"][0]["locator"]["page"] = 999
        report = self.evaluate_payloads(evidence=evidence)
        check = next(
            value
            for value in report.checks
            if value.name == "citation_snapshot_and_locator_integrity"
        )
        self.assertFalse(check.passed)
        self.assertIn("exceeds document", check.detail)

    def test_every_fixture_passes_schema_and_cross_object_validation(self) -> None:
        documents: dict[str, SourceDocument] = {}
        for payload in self.document_payloads:
            self.catalog.validate("SourceDocument", payload)
            document = validate_source_document(SourceDocument.from_dict(payload))
            documents[document.source_document_id] = document

        evidence: dict[str, Evidence] = {}
        for payload in self.evidence_payloads:
            self.catalog.validate("Evidence", payload)
            item = Evidence.from_dict(payload)
            evidence[item.evidence_id] = validate_evidence(item, documents)

        self.catalog.validate("ThesisCard", self.base_payload)
        base = validate_thesis_card(ThesisCard.from_dict(self.base_payload))
        self.catalog.validate("ThesisDiff", self.diff_payload)
        diff = validate_thesis_diff(
            ThesisDiff.from_dict(self.diff_payload), base, evidence, documents
        )
        self.catalog.validate("UserReview", self.review_payload)
        validate_user_review(UserReview.from_dict(self.review_payload), diff)
        self.catalog.validate("ThesisCard", self.accepted_payload)
        validate_thesis_card(ThesisCard.from_dict(self.accepted_payload))

    def test_real_review_replays_to_exact_expected_immutable_v2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            commit_thesis_version(directory, self.base_payload)
            for payload in self.document_payloads:
                save_company_artifact(
                    directory,
                    payload["company_id"],
                    "documents",
                    payload["source_document_id"],
                    payload,
                )
            for payload in self.evidence_payloads:
                save_company_artifact(
                    directory,
                    payload["company_id"],
                    "evidence",
                    payload["evidence_id"],
                    payload,
                )

            outcome = apply_user_review(directory, self.diff_payload, self.review_payload)
            self.assertEqual(outcome["promoted_version_id"], "baba-thesis-v2-2024-05-15")
            company_id = self.base_payload["company"]["company_id"]
            self.assertEqual(read_current_thesis(directory, company_id), self.accepted_payload)
            self.assertEqual(
                read_thesis_version(directory, company_id, "baba-thesis-v1-2024-02-07"),
                self.base_payload,
            )


if __name__ == "__main__":
    unittest.main()
