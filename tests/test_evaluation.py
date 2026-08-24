from __future__ import annotations

import copy
import unittest

from thesisos.evaluation import evaluate_replay


SNAPSHOT = "a" * 64


def replay_payloads() -> tuple[dict, list[dict], list[dict], dict]:
    base = {
        "schema_version": "1.0.0",
        "thesis_id": "THESIS-BABA",
        "company": {"company_id": "BABA"},
        "assumptions": [
            {"assumption_id": "A-01"},
            {"assumption_id": "A-02"},
            {"assumption_id": "A-03"},
        ],
        "version": {"version_id": "V1", "user_confirmed": True},
    }
    documents = [
        {
            "source_document_id": "DOC-NEW",
            "publicly_available_at": "2024-05-14T12:00:00Z",
            "snapshot": {"sha256": SNAPSHOT},
        }
    ]
    evidence = [
        {
            "evidence_id": "E-01",
            "content_class": "source_fact",
            "attribution": "source_document",
            "verification_status": "verified",
            "available_as_of": "2024-05-14T12:00:00Z",
            "citations": [
                {
                    "source_document_id": "DOC-NEW",
                    "snapshot_sha256": SNAPSHOT,
                    "locator": {"kind": "page", "page": 3},
                    "quoted_text": "Cloud adjusted EBITA increased year over year.",
                }
            ],
        }
    ]
    proposed = copy.deepcopy(base)
    proposed["version"] = {
        "version_id": "V2",
        "supersedes": "V1",
        "user_confirmed": False,
    }
    diff = {
        "thesis_diff_id": "DIFF-01",
        "company_id": "BABA",
        "base_thesis_id": "THESIS-BABA",
        "base_version_id": "V1",
        "source_document_ids": ["DOC-NEW"],
        "analysis_cutoff_at": "2024-05-15T00:00:00Z",
        "assumption_changes": [
            {"assumption_id": assumption_id, "impact": "unchanged", "evidence_ids": ["E-01"]}
            for assumption_id in ("A-01", "A-02", "A-03")
        ],
        "targeted_counter_case": {
            "argument": "The apparent gain may come from short-lived cost restraint.",
            "attacked_assumption_ids": ["A-02"],
            "evidence_ids": ["E-01"],
            "why_plausible": "Revenue growth remained modest.",
        },
        "follow_up_questions": [
            {
                "question_id": "Q-01",
                "question": "Can public-cloud growth accelerate without reversing margin gains?",
                "information_value": "It separates mix improvement from durable demand.",
                "evidence_needed": "Public-cloud growth and workload-retention disclosure.",
            }
        ],
        "proposed_patch": {
            "patch_status": "pending_user_review",
            "base_thesis_id": "THESIS-BABA",
            "base_version_id": "V1",
            "proposed_thesis": proposed,
        },
    }
    return base, documents, evidence, diff


class EvaluationTest(unittest.TestCase):
    def evaluate(self, base: dict, documents: list[dict], evidence: list[dict], diff: dict):
        return evaluate_replay(
            base_thesis=base,
            documents=documents,
            evidence=evidence,
            thesis_diff=diff,
            critical_financial_evidence_ids=("E-01",),
            key_fact_evidence_ids=("E-01",),
        )

    def test_complete_replay_passes(self) -> None:
        report = self.evaluate(*replay_payloads())
        self.assertTrue(report.passed, report.to_dict())
        names = {check.name for check in report.checks}
        self.assertIn("critical_financial_source_coverage", names)
        self.assertIn("future_information_leakage", names)

    def test_future_source_is_detected(self) -> None:
        base, documents, evidence, diff = replay_payloads()
        documents[0]["publicly_available_at"] = "2024-05-16T00:00:00Z"
        report = self.evaluate(base, documents, evidence, diff)
        check = next(item for item in report.checks if item.name == "future_information_leakage")
        self.assertFalse(check.passed)

    def test_evidence_cannot_predate_its_cited_document(self) -> None:
        base, documents, evidence, diff = replay_payloads()
        evidence[0]["available_as_of"] = "2024-05-14T11:59:59Z"
        report = self.evaluate(base, documents, evidence, diff)
        check = next(item for item in report.checks if item.name == "future_information_leakage")
        self.assertFalse(check.passed)
        self.assertIn("predates cited document", check.detail)

    def test_snapshot_mismatch_breaks_source_coverage(self) -> None:
        base, documents, evidence, diff = replay_payloads()
        evidence[0]["citations"][0]["snapshot_sha256"] = "b" * 64
        report = self.evaluate(base, documents, evidence, diff)
        coverage = next(
            item for item in report.checks if item.name == "critical_financial_source_coverage"
        )
        self.assertFalse(coverage.passed)
        self.assertEqual((coverage.numerator, coverage.denominator), (0, 1))

    def test_table_value_requires_a_table_locator(self) -> None:
        base, documents, evidence, diff = replay_payloads()
        evidence[0]["citations"][0]["quotation_mode"] = "table_value"
        report = self.evaluate(base, documents, evidence, diff)
        check = next(
            item
            for item in report.checks
            if item.name == "citation_snapshot_and_locator_integrity"
        )
        self.assertFalse(check.passed)
        self.assertIn("without a table locator", check.detail)

    def test_ai_inference_cannot_masquerade_as_source_fact(self) -> None:
        base, documents, evidence, diff = replay_payloads()
        evidence[0]["content_class"] = "ai_inference"
        report = self.evaluate(base, documents, evidence, diff)
        check = next(item for item in report.checks if item.name == "content_class_integrity")
        self.assertFalse(check.passed)

    def test_unreviewed_evidence_cannot_support_a_diff(self) -> None:
        base, documents, evidence, diff = replay_payloads()
        evidence[0]["verification_status"] = "unreviewed"
        report = self.evaluate(base, documents, evidence, diff)
        check = next(
            item for item in report.checks if item.name == "evidence_verification_gate"
        )
        self.assertFalse(check.passed)

    def test_trade_fields_are_forbidden_in_v0(self) -> None:
        base, documents, evidence, diff = replay_payloads()
        diff["target_price"] = 120
        report = self.evaluate(base, documents, evidence, diff)
        check = next(item for item in report.checks if item.name == "no_v0_trading_recommendation")
        self.assertFalse(check.passed)

    def test_trade_instruction_in_generated_text_is_forbidden_in_v0(self) -> None:
        base, documents, evidence, diff = replay_payloads()
        diff["overall_rationale"] = "We recommend buying the stock now."
        report = self.evaluate(base, documents, evidence, diff)
        check = next(
            item
            for item in report.checks
            if item.name == "no_v0_trading_recommendation"
        )
        self.assertFalse(check.passed)
        self.assertIn("v0_trade_instruction", check.detail)

    def test_user_acceptance_may_only_flip_confirmation_metadata(self) -> None:
        base, documents, evidence, diff = replay_payloads()
        review = {
            "user_review_id": "REVIEW-01",
            "thesis_diff_id": "DIFF-01",
            "company_id": "BABA",
            "base_thesis_id": "THESIS-BABA",
            "base_version_id": "V1",
            "decision": "accept",
            "reviewed_at": "2024-05-15T01:00:00Z",
        }
        accepted = copy.deepcopy(diff["proposed_patch"]["proposed_thesis"])
        accepted["version"]["user_confirmed"] = True
        accepted["version"]["updated_at"] = review["reviewed_at"]
        report = evaluate_replay(
            base_thesis=base,
            documents=documents,
            evidence=evidence,
            thesis_diff=diff,
            critical_financial_evidence_ids=("E-01",),
            key_fact_evidence_ids=("E-01",),
            user_review=review,
            accepted_thesis=accepted,
        )
        chain = next(item for item in report.checks if item.name == "user_review_version_chain")
        self.assertTrue(chain.passed, chain.detail)

        accepted["one_sentence_thesis"] = "Silently changed after review"
        tampered = evaluate_replay(
            base_thesis=base,
            documents=documents,
            evidence=evidence,
            thesis_diff=diff,
            user_review=review,
            accepted_thesis=accepted,
        )
        chain = next(item for item in tampered.checks if item.name == "user_review_version_chain")
        self.assertFalse(chain.passed)


if __name__ == "__main__":
    unittest.main()
