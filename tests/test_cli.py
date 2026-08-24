from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from thesisos.cli import main


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


def source_document_for_bytes(
    content: bytes,
    *,
    media_type: str = "plain_text",
    document_id: str = "doc-ingested",
) -> dict[str, object]:
    payload = copy.deepcopy(source_document())
    digest = hashlib.sha256(content).hexdigest()
    payload["source_document_id"] = document_id
    payload["media_type"] = media_type
    payload["snapshot"] = {
        "sha256": digest,
        "storage_uri": f"thesisos://sha256/{digest}",
        "byte_size": len(content),
    }
    return payload


def evidence() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "evidence_id": "evidence-1",
        "company_id": "company-1",
        "statement": "Revenue increased year over year.",
        "content_class": "source_fact",
        "attribution": "source_document",
        "confidence": "high",
        "verification_status": "verified",
        "available_as_of": "2024-05-14T08:00:00Z",
        "reported_for": "FY2024 Q4",
        "citations": [
            {
                "schema_version": "1.0.0",
                "citation_id": "citation-1",
                "source_document_id": "doc-1",
                "snapshot_sha256": "a" * 64,
                "quotation_mode": "exact_quote",
                "locator": {"kind": "page", "page": 2, "section": "Highlights"},
                "quoted_text": "Revenue increased year over year.",
            }
        ],
        "created_at": "2024-05-14T08:05:00Z",
    }


def evidence_for_document(document: dict[str, object]) -> dict[str, object]:
    payload = copy.deepcopy(evidence())
    citation = payload["citations"][0]
    citation["source_document_id"] = document["source_document_id"]
    citation["snapshot_sha256"] = document["snapshot"]["sha256"]
    citation["locator"] = {"kind": "page", "page": 1}
    return payload


def thesis_card(
    version_id: str = "version-1",
    *,
    supersedes: str | None = None,
    confirmed: bool = True,
    as_of_date: str = "2024-02-07",
) -> dict[str, object]:
    assumptions = [
        {
            "assumption_id": f"assumption-{index}",
            "statement": f"Testable assumption {index}",
            "indicator_ids": [f"indicator-{index}"],
            "falsification_condition_ids": [f"condition-{index}"],
        }
        for index in range(1, 4)
    ]
    indicators = [
        {
            "indicator_id": f"indicator-{index}",
            "name": f"Indicator {index}",
            "why_it_matters": f"It tests assumption {index}.",
            "unit_or_definition": "percent",
            "linked_assumption_ids": [f"assumption-{index}"],
        }
        for index in range(1, 4)
    ]
    conditions = [
        {
            "condition_id": f"condition-{index}",
            "statement": f"Assumption {index} is falsified if the indicator deteriorates.",
            "linked_assumption_ids": [f"assumption-{index}"],
        }
        for index in range(1, 4)
    ]
    timestamp = "2024-02-07T10:00:00Z" if version_id == "version-1" else "2024-05-14T10:00:00Z"
    return {
        "schema_version": "1.0.0",
        "thesis_id": "thesis-1",
        "company": {
            "company_id": "company-1",
            "name": "Example Co",
            "ticker": "EXM",
            "market": "XNAS",
            "research_status": "research",
        },
        "one_sentence_thesis": "Durable growth depends on three testable operating assumptions.",
        "assumptions": assumptions,
        "key_indicators": indicators,
        "falsification_conditions": conditions,
        "strongest_counter_case": {
            "statement": "Growth could be bought with uneconomic spending.",
            "attacked_assumption_ids": ["assumption-1"],
            "basis": "Revenue alone does not establish durable economics.",
        },
        "valuation_anchor": {
            "status": "insufficient_evidence",
            "insufficiency_reason": "This replay intentionally excludes market-price inputs.",
        },
        "unknown_questions": [
            {
                "question_id": "unknown-1",
                "question": "Will engagement translate into durable unit economics?",
                "linked_assumption_ids": ["assumption-1"],
            }
        ],
        "version": {
            "as_of_date": as_of_date,
            "version_id": version_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "supersedes": supersedes,
            "user_confirmed": confirmed,
        },
    }


def thesis_diff() -> dict[str, object]:
    proposed = thesis_card(
        "version-2",
        supersedes="version-1",
        confirmed=False,
        as_of_date="2024-05-14",
    )
    proposed["one_sentence_thesis"] = "Evidence modestly strengthens the operating assumptions."
    return {
        "schema_version": "1.0.0",
        "thesis_diff_id": "diff-1",
        "company_id": "company-1",
        "base_thesis_id": "thesis-1",
        "base_version_id": "version-1",
        "source_document_ids": ["doc-1"],
        "material_published_on": "2024-05-14",
        "analysis_cutoff_at": "2024-05-14T12:00:00Z",
        "generated_at": "2024-05-14T12:05:00Z",
        "overall_assessment": "slightly_strengthened",
        "overall_rationale": "The new operating evidence is positive but incomplete.",
        "assumption_changes": [
            {
                "assumption_id": f"assumption-{index}",
                "prior_statement": f"Testable assumption {index}",
                "impact": "slightly_strengthened" if index == 1 else "unchanged",
                "confidence": "medium",
                "evidence_ids": ["evidence-1"],
                "rationale": "The cited result is directionally supportive.",
                "alternative_explanation": "Temporary promotion could explain the result.",
            }
            for index in range(1, 4)
        ],
        "management_statement_action": {
            "assessment": "partially_aligned",
            "summary": "Reported action is directionally consistent with the prior statement.",
            "comparisons": [
                {
                    "comparison_id": "comparison-1",
                    "past_statement": "Management expected growth.",
                    "past_evidence_ids": ["evidence-1"],
                    "current_action_or_result": "Revenue increased.",
                    "current_evidence_ids": ["evidence-1"],
                    "assessment": "partially_aligned",
                    "unresolved_part": "Durability is not yet established.",
                }
            ],
        },
        "targeted_counter_case": {
            "argument": "The observed growth may be promotion-driven and uneconomic.",
            "attacked_assumption_ids": ["assumption-1"],
            "evidence_ids": ["evidence-1"],
            "why_plausible": "The source does not isolate unit economics.",
        },
        "follow_up_questions": [
            {
                "question_id": "follow-up-1",
                "question": "What share of growth remains after promotions normalize?",
                "linked_assumption_ids": ["assumption-1"],
                "information_value": "It distinguishes durable demand from subsidized activity.",
                "evidence_needed": "Cohort retention and contribution-margin disclosure.",
            }
        ],
        "proposed_patch": {
            "patch_status": "pending_user_review",
            "base_thesis_id": "thesis-1",
            "base_version_id": "version-1",
            "change_items": [
                {
                    "change_id": "change-1",
                    "operation": "modify",
                    "target_type": "one_sentence_thesis",
                    "target_id": None,
                    "summary": "Record modest strengthening.",
                    "rationale": "New evidence is directionally positive.",
                    "evidence_ids": ["evidence-1"],
                }
            ],
            "proposed_thesis": proposed,
        },
    }


def user_review() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "user_review_id": "review-1",
        "thesis_diff_id": "diff-1",
        "company_id": "company-1",
        "base_thesis_id": "thesis-1",
        "base_version_id": "version-1",
        "decision": "accept",
        "reviewer_id": "user-1",
        "reviewed_at": "2024-05-14T13:00:00Z",
        "comment": "Accept after checking the cited source.",
    }


def extraction_request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "analysis_cutoff_at": "2024-05-14T12:00:00Z",
        "evidence_id_prefix": "evidence",
        "citation_id_prefix": "citation",
        "created_at": "2024-05-14T08:05:00Z",
        "extraction_scope": ["assumption-1", "assumption-2"],
    }
    payload.update(overrides)
    return payload


def generation_request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "analysis_cutoff_at": "2024-05-14T12:00:00Z",
        "generated_at": "2024-05-14T12:05:00Z",
        "thesis_diff_id": "diff-1",
        "material_published_on": "2024-05-14",
        "proposed_version_id": "version-2",
        "proposed_as_of_date": "2024-05-14",
        "proposed_created_at": "2024-05-14T10:00:00Z",
        "proposed_updated_at": "2024-05-14T10:00:00Z",
        "comparison_id_prefix": "comparison",
        "question_id_prefix": "follow-up",
        "change_id_prefix": "change",
    }
    payload.update(overrides)
    return payload


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, *arguments: str) -> tuple[int, dict[str, object] | None, dict[str, object] | None]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--workspace", str(self.workspace), *arguments])
        out = json.loads(stdout.getvalue()) if stdout.getvalue() else None
        err = json.loads(stderr.getvalue()) if stderr.getvalue() else None
        return code, out, err

    def write_json(self, name: str, payload: dict[str, object]) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def fake_adapter_arguments(
        self,
        output_path: Path,
        *,
        run_id: str,
    ) -> tuple[str, ...]:
        fake_adapter = (
            Path(__file__).resolve().parent / "fixtures" / "fake_model_adapter.py"
        )
        return (
            "--adapter",
            sys.executable,
            "--adapter-arg",
            str(fake_adapter),
            "--adapter-arg",
            "file-output",
            "--adapter-arg",
            str(output_path),
            "--model-id",
            "fixture/model-v1",
            "--run-id",
            run_id,
        )

    def seed_external_generation_context(
        self,
        *,
        base: dict[str, object] | None = None,
        documents: list[dict[str, object]] | None = None,
        evidence_payload: dict[str, object] | None = None,
    ) -> None:
        base_payload = thesis_card() if base is None else base
        document_payloads = [source_document()] if documents is None else documents
        evidence_value = evidence() if evidence_payload is None else evidence_payload
        base_path = self.write_json("seed-base.json", base_payload)
        code, _, error = self.run_cli("commit-thesis", str(base_path))
        self.assertEqual(code, 0, error)
        for index, document_payload in enumerate(document_payloads):
            path = self.write_json(f"seed-document-{index}.json", document_payload)
            code, _, error = self.run_cli("save-document", str(path))
            self.assertEqual(code, 0, error)
        evidence_path = self.write_json("seed-evidence.json", evidence_value)
        code, _, error = self.run_cli("save-evidence", str(evidence_path))
        self.assertEqual(code, 0, error)

    def test_status_does_not_initialize_missing_workspace(self) -> None:
        code, result, error = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertFalse(result["initialized"])
        self.assertFalse(self.workspace.exists())

    def test_status_rejects_symlinked_company_paths(self) -> None:
        self.assertEqual(self.run_cli("init")[0], 0)
        outside = self.root / "outside-company"
        outside.mkdir()
        link = self.workspace / "companies" / "linked-company"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")

        code, result, error = self.run_cli("status")

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("symbolic-link company path", error["error"]["message"])

    def test_init_is_idempotent_and_machine_readable(self) -> None:
        first, result, error = self.run_cli("init")
        second, _, second_error = self.run_cli("init")
        self.assertEqual((first, second), (0, 0))
        self.assertIsNone(error)
        self.assertIsNone(second_error)
        self.assertEqual(result["format_version"], 1)

    def test_unexpected_filesystem_failure_is_an_internal_io_error(self) -> None:
        with patch(
            "thesisos.cli.initialize_workspace",
            side_effect=OSError("simulated disk failure"),
        ):
            code, result, error = self.run_cli("init")

        self.assertEqual(code, 1)
        self.assertIsNone(result)
        self.assertEqual(error["error"]["code"], "io_error")
        self.assertIn("simulated disk failure", error["error"]["message"])

    def test_snapshot_info_is_read_only_and_machine_readable(self) -> None:
        content = b"snapshot identity\n"
        source = self.root / "snapshot.txt"
        source.write_bytes(content)
        code, result, error = self.run_cli("snapshot-info", str(source))
        self.assertEqual(code, 0, error)
        self.assertIsNone(error)
        digest = hashlib.sha256(content).hexdigest()
        self.assertEqual(result["sha256"], digest)
        self.assertEqual(result["byte_size"], len(content))
        self.assertEqual(result["storage_uri"], f"thesisos://sha256/{digest}")
        self.assertFalse(self.workspace.exists())

    def test_eval_replay_runs_without_initializing_a_workspace(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        case_file = (
            repository
            / "evals"
            / "historical-replay"
            / "alibaba-2024-q4"
            / "case.json"
        )
        code, result, error = self.run_cli("eval-replay", str(case_file))
        self.assertEqual(code, 0, error)
        self.assertIsNone(error)
        self.assertTrue(result["ok"])
        self.assertTrue(result["report"]["passed"])
        self.assertFalse(self.workspace.exists())

    def test_eval_suite_runs_all_readme_suites(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        suite_files = [
            repository / "evals" / name / "suite.json"
            for name in (
                "citation-accuracy",
                "assumption-mapping",
                "future-leakage",
            )
        ]
        code, result, error = self.run_cli(
            "eval-suite", *(str(path) for path in suite_files)
        )
        self.assertEqual(code, 0, error)
        self.assertIsNone(error)
        self.assertTrue(result["ok"])
        self.assertEqual(result["passed_suites"], 3)
        self.assertEqual(result["total_suites"], 3)
        self.assertTrue(
            all(
                mutation["detected"]
                for report in result["reports"]
                for mutation in report["mutations"]
            )
        )
        self.assertFalse(self.workspace.exists())

    def test_failed_eval_suite_returns_two_with_the_report(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        source = repository / "evals" / "citation-accuracy" / "suite.json"
        manifest = json.loads(source.read_text(encoding="utf-8"))
        manifest["golden_case"] = str(
            repository
            / "evals"
            / "historical-replay"
            / "alibaba-2024-q4"
            / "case.json"
        )
        manifest["mutations"][0]["expected_failed_checks"] = [
            "future_information_leakage"
        ]
        failing_suite = self.write_json("failing-suite.json", manifest)

        code, result, error = self.run_cli("eval-suite", str(failing_suite))
        self.assertEqual(code, 2)
        self.assertIsNone(error)
        self.assertFalse(result["ok"])
        self.assertFalse(result["reports"][0]["passed"])
        self.assertFalse(result["reports"][0]["mutations"][0]["detected"])

    def test_validate_returns_two_and_json_error_without_traceback(self) -> None:
        bad = self.write_json("bad-document.json", {"schema_version": "1.0.0"})
        code, result, error = self.run_cli("validate", "SourceDocument", str(bad))
        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertFalse(error["ok"])
        self.assertEqual(error["error"]["code"], "schema_validation_error")
        self.assertTrue(error["errors"])
        self.assertNotIn("Traceback", json.dumps(error))

    def test_validate_reports_optional_cross_object_check(self) -> None:
        document_path = self.write_json("document.json", source_document())
        citation_payload = copy.deepcopy(evidence()["citations"][0])
        citation_path = self.write_json("citation.json", citation_payload)
        code, result, error = self.run_cli(
            "validate",
            "Citation",
            str(citation_path),
            "--document",
            str(document_path),
        )
        self.assertEqual(code, 0, error)
        self.assertTrue(result["schema_validated"])
        self.assertTrue(result["cross_object_validated"])

    def test_ingest_document_writes_verified_object_and_is_idempotent(self) -> None:
        content = "原始内容不会被重新编码。\r\n".encode("utf-8")
        source = self.root / "source.txt"
        source.write_bytes(content)
        metadata = source_document_for_bytes(content)
        metadata_path = self.write_json("ingest-document.json", metadata)

        code, result, error = self.run_cli(
            "ingest-document",
            str(metadata_path),
            str(source),
        )
        self.assertEqual(code, 0, error)
        self.assertIsNone(error)
        self.assertTrue(result["object_created"])
        self.assertEqual(result["snapshot_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["byte_size"], len(content))
        self.assertEqual(Path(result["object_path"]).read_bytes(), content)
        self.assertTrue(Path(result["document_path"]).is_file())

        code, repeated, error = self.run_cli(
            "ingest-document",
            str(metadata_path),
            str(source),
        )
        self.assertEqual(code, 0, error)
        self.assertFalse(repeated["object_created"])
        self.assertEqual(repeated["object_path"], result["object_path"])
        code, saved, error = self.run_cli("save-document", str(metadata_path))
        self.assertEqual(code, 0, error)
        self.assertEqual(saved["source_document_id"], metadata["source_document_id"])

    def test_ingest_document_accepts_all_three_canonical_media_types(self) -> None:
        fixtures = (
            ("pdf", "report.pdf", b"%PDF-1.7\n\x00\xffraw\n%%EOF"),
            ("markdown", "notes.md", "# 标题\n\n正文\n".encode("utf-8")),
            ("plain_text", "notes.txt", b"line one\r\nline two\r\n"),
        )
        for index, (media_type, filename, content) in enumerate(fixtures, start=1):
            with self.subTest(media_type=media_type):
                source = self.root / filename
                source.write_bytes(content)
                metadata = source_document_for_bytes(
                    content,
                    media_type=media_type,
                    document_id=f"doc-media-{index}",
                )
                metadata_path = self.write_json(f"metadata-{index}.json", metadata)
                code, result, error = self.run_cli(
                    "ingest-document",
                    str(metadata_path),
                    str(source),
                )
                self.assertEqual(code, 0, error)
                self.assertEqual(Path(result["object_path"]).read_bytes(), content)

    def test_ingest_mismatch_exits_two_without_document_object_or_traceback(self) -> None:
        content = b"actual bytes"
        source = self.root / "source.txt"
        source.write_bytes(content)
        metadata = source_document_for_bytes(content)
        metadata["snapshot"]["byte_size"] = len(content) + 7
        metadata_path = self.write_json("bad-ingest.json", metadata)

        code, result, error = self.run_cli(
            "ingest-document",
            str(metadata_path),
            str(source),
        )

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertEqual(error["error"]["code"], "snapshot_error")
        self.assertNotIn("Traceback", json.dumps(error))
        self.assertFalse(self.workspace.exists())

    def test_existing_document_conflict_is_rejected_before_object_write(self) -> None:
        old_content = b"old externally managed content"
        existing = source_document_for_bytes(old_content, document_id="doc-conflict")
        existing["snapshot"]["storage_uri"] = "file:///externally-managed/doc-conflict"
        existing_path = self.write_json("existing.json", existing)
        self.assertEqual(self.run_cli("save-document", str(existing_path))[0], 0)

        new_content = b"different new content"
        source = self.root / "new-source.txt"
        source.write_bytes(new_content)
        changed = source_document_for_bytes(new_content, document_id="doc-conflict")
        changed_path = self.write_json("changed.json", changed)
        code, result, error = self.run_cli(
            "ingest-document",
            str(changed_path),
            str(source),
        )

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("already exists", error["error"]["message"])
        self.assertFalse((self.workspace / "objects").exists())

    def test_save_document_rejects_managed_uri_without_local_object(self) -> None:
        content = b"declared but never ingested"
        metadata = source_document_for_bytes(content, document_id="doc-missing-object")
        metadata_path = self.write_json("missing-object.json", metadata)

        code, result, error = self.run_cli("save-document", str(metadata_path))

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertEqual(error["error"]["code"], "snapshot_error")
        self.assertIn("missing", error["error"]["message"])
        self.assertFalse(self.workspace.exists())

    def test_save_document_cannot_bypass_managed_mode_with_uri_case(self) -> None:
        content = b"declared under a case-varied managed scheme"
        metadata = source_document_for_bytes(content, document_id="doc-uri-case")
        metadata["snapshot"]["storage_uri"] = metadata["snapshot"]["storage_uri"].replace(
            "thesisos:", "ThesisOS:"
        )
        metadata_path = self.write_json("managed-uri-case.json", metadata)

        code, result, error = self.run_cli("save-document", str(metadata_path))

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertEqual(error["error"]["code"], "snapshot_error")
        self.assertFalse(self.workspace.exists())

    def test_save_document_keeps_external_metadata_mode(self) -> None:
        metadata = source_document()
        metadata_path = self.write_json("external-document.json", metadata)

        code, result, error = self.run_cli("save-document", str(metadata_path))

        self.assertEqual(code, 0, error)
        self.assertTrue(Path(result["path"]).is_file())
        self.assertFalse((self.workspace / "objects").exists())

    def test_save_evidence_rejects_tampered_or_missing_managed_object_without_writing(self) -> None:
        for mode in ("tampered", "missing"):
            with self.subTest(mode=mode):
                self.workspace = self.root / f"workspace-{mode}"
                content = f"managed source for {mode}".encode("utf-8")
                source = self.root / f"source-{mode}.txt"
                source.write_bytes(content)
                document = source_document_for_bytes(content, document_id="doc-1")
                document_path = self.write_json(f"document-{mode}.json", document)
                code, ingest_result, error = self.run_cli(
                    "ingest-document",
                    str(document_path),
                    str(source),
                )
                self.assertEqual(code, 0, error)
                object_path = Path(ingest_result["object_path"])
                if mode == "tampered":
                    object_path.write_bytes(b"x" * len(content))
                else:
                    object_path.unlink()

                evidence_path = self.write_json(
                    f"evidence-{mode}.json",
                    evidence_for_document(document),
                )
                code, result, error = self.run_cli("save-evidence", str(evidence_path))

                self.assertEqual(code, 2)
                self.assertIsNone(result)
                self.assertEqual(error["error"]["code"], "snapshot_error")
                self.assertNotIn("Traceback", json.dumps(error))
                self.assertFalse(
                    (
                        self.workspace
                        / "companies"
                        / "company-1"
                        / "evidence"
                        / "evidence-1.json"
                    ).exists()
                )

    def test_save_evidence_checks_literal_text_inside_managed_snapshot(self) -> None:
        content = b"Revenue increased year over year.\n"
        source = self.root / "quoted-source.txt"
        source.write_bytes(content)
        document = source_document_for_bytes(content, document_id="doc-1")
        document_path = self.write_json("quoted-document.json", document)
        code, _, error = self.run_cli(
            "ingest-document", str(document_path), str(source)
        )
        self.assertEqual(code, 0, error)

        valid = evidence_for_document(document)
        valid_path = self.write_json("quoted-evidence.json", valid)
        code, result, error = self.run_cli("save-evidence", str(valid_path))
        self.assertEqual(code, 0, error)
        self.assertTrue(result["citation_text_checks"][0]["passed"])

        forged = copy.deepcopy(valid)
        forged["evidence_id"] = "evidence-forged"
        forged["citations"][0]["citation_id"] = "citation-forged"
        forged["citations"][0]["quoted_text"] = "Revenue declined year over year."
        forged_path = self.write_json("forged-evidence.json", forged)
        code, result, error = self.run_cli("save-evidence", str(forged_path))
        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("quoted_text was not found", error["error"]["message"])
        self.assertFalse(
            (
                self.workspace
                / "companies"
                / "company-1"
                / "evidence"
                / "evidence-forged.json"
            ).exists()
        )

    def test_review_rechecks_managed_object_and_cannot_promote_after_tampering(self) -> None:
        content = b"Revenue increased year over year.\n"
        source = self.root / "review-source.txt"
        source.write_bytes(content)
        document = source_document_for_bytes(content, document_id="doc-1")
        files = {
            "document": self.write_json("review-document.json", document),
            "thesis": self.write_json("review-thesis-v1.json", thesis_card()),
            "evidence": self.write_json(
                "review-evidence.json",
                evidence_for_document(document),
            ),
            "diff": self.write_json("review-diff.json", thesis_diff()),
            "review": self.write_json("review-decision.json", user_review()),
        }
        code, ingest_result, error = self.run_cli(
            "ingest-document",
            str(files["document"]),
            str(source),
        )
        self.assertEqual(code, 0, error)
        self.assertEqual(self.run_cli("commit-thesis", str(files["thesis"]))[0], 0)
        self.assertEqual(self.run_cli("save-evidence", str(files["evidence"]))[0], 0)

        object_path = Path(ingest_result["object_path"])
        object_path.write_bytes(b"z" * len(content))
        code, result, error = self.run_cli(
            "review",
            str(files["diff"]),
            str(files["review"]),
        )

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertEqual(error["error"]["code"], "snapshot_error")
        company = self.workspace / "companies" / "company-1"
        self.assertFalse((company / "thesis_versions" / "version-2.json").exists())
        self.assertFalse((company / "diffs" / "diff-1.json").exists())
        self.assertFalse((company / "reviews" / "review-1.json").exists())
        code, status, status_error = self.run_cli("status", "--company", "company-1")
        self.assertEqual(code, 0, status_error)
        self.assertEqual(status["companies"][0]["current_version_id"], "version-1")

    def test_end_to_end_commit_evidence_review_and_status(self) -> None:
        files = {
            "thesis": self.write_json("thesis-v1.json", thesis_card()),
            "document": self.write_json("document.json", source_document()),
            "evidence": self.write_json("evidence.json", evidence()),
            "diff": self.write_json("diff.json", thesis_diff()),
            "review": self.write_json("review.json", user_review()),
        }
        self.assertEqual(self.run_cli("init")[0], 0)
        for command, key in (
            ("commit-thesis", "thesis"),
            ("save-document", "document"),
            ("save-evidence", "evidence"),
        ):
            code, _, error = self.run_cli(command, str(files[key]))
            self.assertEqual(code, 0, f"{command}: {error}")
        code, review_result, error = self.run_cli(
            "review", str(files["diff"]), str(files["review"])
        )
        self.assertEqual(code, 0, error)
        self.assertEqual(review_result["promoted_version_id"], "version-2")

        code, status, error = self.run_cli("status", "--company", "company-1")
        self.assertEqual(code, 0, error)
        self.assertEqual(status["companies"][0]["current_version_id"], "version-2")
        self.assertEqual(status["companies"][0]["records"]["thesis_versions"], 2)
        self.assertEqual(status["companies"][0]["records"]["reviews"], 1)

    def test_formal_thesis_cannot_reference_unstored_evidence(self) -> None:
        payload = thesis_card()
        payload["strongest_counter_case"]["evidence_ids"] = ["evidence-missing"]
        path = self.write_json("thesis-missing-evidence.json", payload)

        code, result, error = self.run_cli("commit-thesis", str(path))

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("is not stored", error["error"]["message"])
        self.assertFalse(self.workspace.exists())

    def test_formal_version_cannot_rewind_current_chronology(self) -> None:
        first_path = self.write_json("chronology-v1.json", thesis_card())
        self.assertEqual(self.run_cli("commit-thesis", str(first_path))[0], 0)
        rewound = thesis_card(
            "version-2",
            supersedes="version-1",
            confirmed=True,
            as_of_date="2020-01-01",
        )
        rewound["version"]["created_at"] = "2020-01-01T00:00:00Z"
        rewound["version"]["updated_at"] = "2020-01-01T00:00:00Z"
        rewound_path = self.write_json("chronology-v2-rewound.json", rewound)

        code, result, error = self.run_cli("commit-thesis", str(rewound_path))

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("cannot precede the current version", error["error"]["message"])
        _, status, _ = self.run_cli("status", "--company", "company-1")
        self.assertEqual(status["companies"][0]["current_version_id"], "version-1")

    def test_accept_with_edits_cannot_add_unstored_evidence_reference(self) -> None:
        self.seed_external_generation_context()
        diff_payload = thesis_diff()
        diff_path = self.write_json("edited-evidence-diff.json", diff_payload)
        review_payload = user_review()
        review_payload["decision"] = "accept_with_edits"
        reviewed_thesis = copy.deepcopy(
            diff_payload["proposed_patch"]["proposed_thesis"]
        )
        reviewed_thesis["version"]["user_confirmed"] = True
        reviewed_thesis["strongest_counter_case"]["evidence_ids"] = [
            "evidence-missing"
        ]
        review_payload["reviewed_thesis"] = reviewed_thesis
        review_path = self.write_json("edited-evidence-review.json", review_payload)

        code, result, error = self.run_cli(
            "review",
            str(diff_path),
            str(review_path),
        )

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("is not stored", error["error"]["message"])
        self.assertFalse(
            (
                self.workspace
                / "companies"
                / "company-1"
                / "reviews"
                / "review-1.json"
            ).exists()
        )

    def test_raw_source_model_adapter_to_reviewed_v2_end_to_end(self) -> None:
        content = b"Revenue increased year over year.\n"
        source = self.root / "runtime-source.txt"
        source.write_bytes(content)
        document = source_document_for_bytes(content, document_id="doc-1")
        files = {
            "document": self.write_json("runtime-document.json", document),
            "thesis": self.write_json("runtime-thesis-v1.json", thesis_card()),
        }
        self.assertEqual(
            self.run_cli(
                "ingest-document", str(files["document"]), str(source)
            )[0],
            0,
        )
        self.assertEqual(self.run_cli("commit-thesis", str(files["thesis"]))[0], 0)

        extracted_evidence = evidence_for_document(document)
        extracted_evidence["verification_status"] = "unreviewed"
        adapter_evidence = self.write_json(
            "adapter-evidence-output.json", [extracted_evidence]
        )
        extraction_request = self.write_json(
            "extraction-request.json",
            {
                "analysis_cutoff_at": "2024-05-14T12:00:00Z",
                "evidence_id_prefix": "evidence",
                "citation_id_prefix": "citation",
                "created_at": "2024-05-14T08:05:00Z",
                "extraction_scope": ["assumption-1", "assumption-2"],
            },
        )
        fake_adapter = (
            Path(__file__).resolve().parent / "fixtures" / "fake_model_adapter.py"
        )
        adapter_arguments = (
            "--adapter",
            sys.executable,
            "--adapter-arg",
            str(fake_adapter),
            "--adapter-arg",
            "file-output",
            "--adapter-arg",
            str(adapter_evidence),
            "--model-id",
            "fixture/model-v1",
        )
        code, extraction, error = self.run_cli(
            "extract-evidence",
            "company-1",
            "doc-1",
            str(extraction_request),
            *adapter_arguments,
            "--run-id",
            "run-extract-1",
        )
        self.assertEqual(code, 0, error)
        self.assertEqual(extraction["evidence_count"], 1)
        self.assertTrue(extraction["citation_text_checks"][0]["passed"])
        self.assertFalse(
            (
                self.workspace
                / "companies"
                / "company-1"
                / "evidence"
                / "evidence-1.json"
            ).exists(),
            "model output must not silently become verified Evidence",
        )
        duplicate_code, duplicate_result, duplicate_error = self.run_cli(
            "extract-evidence",
            "company-1",
            "doc-1",
            str(extraction_request),
            "--adapter",
            "/definitely/missing/thesisos-adapter",
            "--model-id",
            "fixture/model-v1",
            "--run-id",
            "run-extract-1",
        )
        self.assertEqual(duplicate_code, 2)
        self.assertIsNone(duplicate_result)
        self.assertIn("already exists", duplicate_error["error"]["message"])
        self.assertNotIn("could not be started", duplicate_error["error"]["message"])

        reviewed_evidence = copy.deepcopy(extraction["output"][0])
        reviewed_evidence["verification_status"] = "verified"
        reviewed_evidence_path = self.write_json(
            "runtime-evidence-reviewed.json", reviewed_evidence
        )
        code, saved_evidence, error = self.run_cli(
            "save-evidence", str(reviewed_evidence_path)
        )
        self.assertEqual(code, 0, error)
        self.assertTrue(saved_evidence["citation_text_checks"][0]["passed"])

        generated_diff = thesis_diff()
        generated_diff["management_statement_action"] = {
            "assessment": "insufficient_evidence",
            "summary": "No prior management statement was selected for comparison.",
            "comparisons": [],
        }
        adapter_diff = self.write_json("adapter-diff-output.json", generated_diff)
        generation_request = self.write_json(
            "generation-request.json",
            {
                "analysis_cutoff_at": "2024-05-14T12:00:00Z",
                "generated_at": "2024-05-14T12:05:00Z",
                "thesis_diff_id": "diff-1",
                "material_published_on": "2024-05-14",
                "proposed_version_id": "version-2",
                "proposed_as_of_date": "2024-05-14",
                "proposed_created_at": "2024-05-14T10:00:00Z",
                "proposed_updated_at": "2024-05-14T10:00:00Z",
                "comparison_id_prefix": "comparison",
                "question_id_prefix": "follow-up",
                "change_id_prefix": "change",
            },
        )
        code, generation, error = self.run_cli(
            "generate-diff",
            "company-1",
            str(generation_request),
            "--document",
            "doc-1",
            "--evidence",
            "evidence-1",
            "--adapter",
            sys.executable,
            "--adapter-arg",
            str(fake_adapter),
            "--adapter-arg",
            "file-output",
            "--adapter-arg",
            str(adapter_diff),
            "--model-id",
            "fixture/model-v1",
            "--run-id",
            "run-diff-1",
        )
        self.assertEqual(code, 0, error)
        self.assertEqual(generation["thesis_diff_id"], "diff-1")
        self.assertTrue(Path(generation["diff_path"]).is_file())

        review_path = self.write_json("runtime-review.json", user_review())
        code, reviewed, error = self.run_cli(
            "review", generation["diff_path"], str(review_path)
        )
        self.assertEqual(code, 0, error)
        self.assertEqual(reviewed["promoted_version_id"], "version-2")
        code, status, error = self.run_cli("status", "--company", "company-1")
        self.assertEqual(code, 0, error)
        self.assertEqual(status["companies"][0]["current_version_id"], "version-2")
        self.assertEqual(status["companies"][0]["records"]["model_runs"], 2)

    def test_concurrent_duplicate_run_id_invokes_adapter_only_once(self) -> None:
        self.seed_external_generation_context()
        generated = thesis_diff()
        generated["management_statement_action"] = {
            "assessment": "insufficient_evidence",
            "summary": "No prior management statement was selected for comparison.",
            "comparisons": [],
        }
        output_path = self.write_json("concurrent-output.json", generated)
        request_path = self.write_json("concurrent-request.json", generation_request())
        call_log = self.root / "adapter-calls.log"
        repository = Path(__file__).resolve().parents[1]
        fake_adapter = repository / "tests" / "fixtures" / "fake_model_adapter.py"
        command = [
            sys.executable,
            "-m",
            "thesisos",
            "--workspace",
            str(self.workspace),
            "generate-diff",
            "company-1",
            str(request_path),
            "--document",
            "doc-1",
            "--evidence",
            "evidence-1",
            "--adapter",
            sys.executable,
            "--adapter-arg",
            str(fake_adapter),
            "--adapter-arg",
            "counted-file-output",
            "--adapter-arg",
            str(output_path),
            "--adapter-arg",
            str(call_log),
            "--adapter-arg",
            "0.75",
            "--model-id",
            "fixture/model-v1",
            "--run-id",
            "concurrent-run",
        ]
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = str(repository / "src") + (
            "" if not existing_pythonpath else os.pathsep + existing_pythonpath
        )
        first: subprocess.Popen[str] | None = None
        second: subprocess.Popen[str] | None = None
        try:
            first = subprocess.Popen(
                command,
                cwd=repository,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if call_log.exists() and call_log.stat().st_size:
                    break
                if first.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertTrue(call_log.exists(), "first adapter never started")
            second = subprocess.Popen(
                command,
                cwd=repository,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            first_stdout, first_stderr = first.communicate(timeout=15)
            second_stdout, second_stderr = second.communicate(timeout=15)
        finally:
            for process in (first, second):
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)

        self.assertEqual(first.returncode, 0, first_stderr)
        self.assertEqual(second.returncode, 2, second_stdout)
        self.assertIn("already exists", second_stderr)
        self.assertEqual(call_log.read_text(encoding="utf-8").splitlines(), ["adapter-called"])

    def test_generate_binds_past_and_current_evidence_to_cli_roles(self) -> None:
        self.seed_external_generation_context()
        prior = evidence()
        prior["evidence_id"] = "evidence-prior"
        prior["citations"][0]["citation_id"] = "citation-prior"
        prior_path = self.write_json("role-prior-evidence.json", prior)
        self.assertEqual(self.run_cli("save-evidence", str(prior_path))[0], 0)
        request_path = self.write_json("role-request.json", generation_request())

        past_output = self.write_json("role-past-output.json", thesis_diff())
        code, result, error = self.run_cli(
            "generate-diff",
            "company-1",
            str(request_path),
            "--document",
            "doc-1",
            "--evidence",
            "evidence-1",
            *self.fake_adapter_arguments(past_output, run_id="role-past-run"),
        )
        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("past_evidence_ids", error["error"]["message"])

        current_payload = thesis_diff()
        comparison = current_payload["management_statement_action"]["comparisons"][0]
        comparison["past_evidence_ids"] = ["evidence-prior"]
        current_payload["assumption_changes"][0]["evidence_ids"] = [
            "evidence-prior"
        ]
        current_output = self.write_json("role-current-output.json", current_payload)
        code, result, error = self.run_cli(
            "generate-diff",
            "company-1",
            str(request_path),
            "--document",
            "doc-1",
            "--evidence",
            "evidence-1",
            "--prior-evidence",
            "evidence-prior",
            *self.fake_adapter_arguments(current_output, run_id="role-current-run"),
        )
        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("current-material Evidence", error["error"]["message"])

    def test_review_rejects_future_evidence_hidden_in_proposed_thesis(self) -> None:
        self.seed_external_generation_context()
        future_document = source_document()
        future_document["source_document_id"] = "doc-future"
        future_document["title"] = "Future quarterly results"
        future_document["published_on"] = "2025-01-01"
        future_document["publicly_available_at"] = "2025-01-01T08:00:00Z"
        future_document["ingested_at"] = "2025-01-01T08:05:00Z"
        future_document["snapshot"]["sha256"] = "b" * 64
        future_document["snapshot"]["storage_uri"] = (
            "file:///immutable/doc-future.pdf"
        )
        future_document_path = self.write_json(
            "future-proposed-document.json",
            future_document,
        )
        self.assertEqual(
            self.run_cli("save-document", str(future_document_path))[0],
            0,
        )

        future_evidence = evidence()
        future_evidence["evidence_id"] = "evidence-future"
        future_evidence["available_as_of"] = "2025-01-01T08:00:00Z"
        future_evidence["created_at"] = "2025-01-01T08:05:00Z"
        future_evidence["citations"][0]["citation_id"] = "citation-future"
        future_evidence["citations"][0]["source_document_id"] = "doc-future"
        future_evidence["citations"][0]["snapshot_sha256"] = "b" * 64
        future_evidence_path = self.write_json(
            "future-proposed-evidence.json",
            future_evidence,
        )
        self.assertEqual(
            self.run_cli("save-evidence", str(future_evidence_path))[0],
            0,
        )

        malicious = thesis_diff()
        malicious["proposed_patch"]["proposed_thesis"][
            "strongest_counter_case"
        ]["evidence_ids"] = ["evidence-future"]
        diff_path = self.write_json("future-proposed-diff.json", malicious)
        review_path = self.write_json("future-proposed-review.json", user_review())
        code, result, error = self.run_cli(
            "review",
            str(diff_path),
            str(review_path),
        )

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("after analysis_cutoff_at", error["error"]["message"])
        current = json.loads(
            (
                self.workspace
                / "companies"
                / "company-1"
                / "current_thesis.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(current["version_id"], "version-1")

    def test_generate_preflights_bundle_paths_before_model_run_is_saved(self) -> None:
        self.seed_external_generation_context()
        company = self.workspace / "companies" / "company-1"
        (company / "diffs").write_text("blocks the artifact directory", encoding="utf-8")
        request_path = self.write_json("bundle-request.json", generation_request())
        output_path = self.write_json("bundle-output.json", thesis_diff())

        code, result, error = self.run_cli(
            "generate-diff",
            "company-1",
            str(request_path),
            "--document",
            "doc-1",
            "--evidence",
            "evidence-1",
            *self.fake_adapter_arguments(output_path, run_id="bundle-run"),
        )

        self.assertEqual(code, 2, error)
        self.assertIsNone(result)
        self.assertFalse((company / "model_runs" / "bundle-run.json").exists())

    def test_model_request_metadata_rejects_unknown_fields_before_adapter(self) -> None:
        secret = "SENTINEL-MUST-NOT-BE-PERSISTED"
        requests = (
            (
                "extract-evidence",
                extraction_request(api_key=secret),
                (
                    "company-1",
                    "doc-1",
                ),
                (),
            ),
            (
                "generate-diff",
                generation_request(api_key=secret),
                ("company-1",),
                ("--document", "doc-1", "--evidence", "evidence-1"),
            ),
        )
        for index, (command, request, prefix, context) in enumerate(requests):
            with self.subTest(command=command):
                request_path = self.write_json(f"unknown-metadata-{index}.json", request)
                code, result, error = self.run_cli(
                    command,
                    *prefix,
                    str(request_path),
                    *context,
                    "--adapter",
                    "/definitely/missing/thesisos-adapter",
                    "--model-id",
                    "fixture/model-v1",
                    "--run-id",
                    f"unknown-metadata-{index}",
                )
                self.assertEqual(code, 2)
                self.assertIsNone(result)
                self.assertIn("unknown field", error["error"]["message"])
                self.assertIn("api_key", error["error"]["message"])
                self.assertNotIn(secret, json.dumps(error))
        self.assertFalse(self.workspace.exists())

    def test_model_request_metadata_rejects_nested_or_non_string_secret_values(self) -> None:
        secret = "SENTINEL-NESTED-SECRET"
        requests = (
            (
                "extract-evidence",
                extraction_request(extraction_scope=[{"api_key": secret}]),
                ("company-1", "doc-1"),
                (),
            ),
            (
                "generate-diff",
                generation_request(comparison_id_prefix={"api_key": secret}),
                ("company-1",),
                ("--document", "doc-1", "--evidence", "evidence-1"),
            ),
        )
        for index, (command, request, prefix, context) in enumerate(requests):
            with self.subTest(command=command):
                request_path = self.write_json(f"typed-metadata-{index}.json", request)
                code, result, error = self.run_cli(
                    command,
                    *prefix,
                    str(request_path),
                    *context,
                    "--adapter",
                    "/definitely/missing/thesisos-adapter",
                    "--model-id",
                    "fixture/model-v1",
                    "--run-id",
                    f"typed-metadata-{index}",
                )
                self.assertEqual(code, 2)
                self.assertIsNone(result)
                self.assertNotIn(secret, json.dumps(error))
        self.assertFalse(self.workspace.exists())

    def test_extract_rejects_future_current_thesis_before_adapter(self) -> None:
        content = b"Revenue increased year over year.\n"
        source = self.root / "future-thesis-source.txt"
        source.write_bytes(content)
        document = source_document_for_bytes(content, document_id="doc-1")
        document_path = self.write_json("future-thesis-document.json", document)
        code, _, error = self.run_cli(
            "ingest-document", str(document_path), str(source)
        )
        self.assertEqual(code, 0, error)

        future_thesis = thesis_card(as_of_date="2025-01-01")
        future_thesis["version"]["created_at"] = "2025-01-01T00:00:00Z"
        future_thesis["version"]["updated_at"] = "2025-01-01T00:00:00Z"
        thesis_path = self.write_json("future-current-thesis.json", future_thesis)
        self.assertEqual(self.run_cli("commit-thesis", str(thesis_path))[0], 0)
        request_path = self.write_json("future-thesis-request.json", extraction_request())

        code, result, error = self.run_cli(
            "extract-evidence",
            "company-1",
            "doc-1",
            str(request_path),
            "--adapter",
            "/definitely/missing/thesisos-adapter",
            "--model-id",
            "fixture/model-v1",
            "--run-id",
            "future-thesis-run",
        )
        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("current ThesisCard did not exist", error["error"]["message"])
        self.assertFalse(
            (
                self.workspace
                / "companies"
                / "company-1"
                / "model_runs"
                / "future-thesis-run.json"
            ).exists()
        )

    def test_extract_rejects_future_document_before_adapter(self) -> None:
        document = source_document()
        document["published_on"] = "2025-01-01"
        document["publicly_available_at"] = "2025-01-01T00:00:00Z"
        document["ingested_at"] = "2025-01-01T00:05:00Z"
        document_path = self.write_json("future-extraction-document.json", document)
        code, _, error = self.run_cli("save-document", str(document_path))
        self.assertEqual(code, 0, error)
        request_path = self.write_json("future-document-request.json", extraction_request())

        code, result, error = self.run_cli(
            "extract-evidence",
            "company-1",
            "doc-1",
            str(request_path),
            "--adapter",
            "/definitely/missing/thesisos-adapter",
            "--model-id",
            "fixture/model-v1",
            "--run-id",
            "future-document-run",
        )
        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("not publicly available", error["error"]["message"])
        self.assertFalse(
            (
                self.workspace
                / "companies"
                / "company-1"
                / "model_runs"
                / "future-document-run.json"
            ).exists()
        )

    def test_extract_rejects_model_boundary_and_identity_violations(self) -> None:
        content = b"Revenue increased year over year.\n"
        source = self.root / "extraction-boundary-source.txt"
        source.write_bytes(content)
        document = source_document_for_bytes(content, document_id="doc-1")
        document_path = self.write_json("extraction-boundary-document.json", document)
        code, _, error = self.run_cli(
            "ingest-document", str(document_path), str(source)
        )
        self.assertEqual(code, 0, error)
        request = extraction_request(created_at="2024-05-14T10:00:00Z")
        request_path = self.write_json("extraction-boundary-request.json", request)

        base = evidence_for_document(document)
        base["verification_status"] = "unreviewed"
        base["created_at"] = request["created_at"]
        cases = (
            "self_verified",
            "evidence_prefix",
            "citation_prefix",
            "created_at_drift",
            "available_as_of_drift",
            "ai_inference",
            "user_judgment",
            "invalid_attribution",
            "duplicate_citation_id",
        )
        for index, case_name in enumerate(cases):
            with self.subTest(case=case_name):
                first = copy.deepcopy(base)
                output = [first]
                if case_name == "self_verified":
                    first["verification_status"] = "verified"
                elif case_name == "evidence_prefix":
                    first["evidence_id"] = "other-1"
                elif case_name == "citation_prefix":
                    first["citations"][0]["citation_id"] = "other-1"
                elif case_name == "created_at_drift":
                    first["created_at"] = "2024-05-14T10:01:00Z"
                elif case_name == "available_as_of_drift":
                    first["available_as_of"] = "2024-05-14T09:00:00Z"
                elif case_name == "ai_inference":
                    first["content_class"] = "ai_inference"
                    first["attribution"] = "ai"
                elif case_name == "user_judgment":
                    first["content_class"] = "user_judgment"
                    first["attribution"] = "user"
                elif case_name == "invalid_attribution":
                    first["content_class"] = "source_opinion"
                    first["attribution"] = "source_document"
                elif case_name == "duplicate_citation_id":
                    second = copy.deepcopy(first)
                    second["evidence_id"] = "evidence-2"
                    output.append(second)

                output_path = self.write_json(
                    f"extraction-boundary-output-{index}.json", output
                )
                run_id = f"boundary-run-{index}"
                code, result, error = self.run_cli(
                    "extract-evidence",
                    "company-1",
                    "doc-1",
                    str(request_path),
                    *self.fake_adapter_arguments(output_path, run_id=run_id),
                )
                self.assertEqual(code, 2, error)
                self.assertIsNone(result)
                self.assertFalse(
                    (
                        self.workspace
                        / "companies"
                        / "company-1"
                        / "model_runs"
                        / f"{run_id}.json"
                    ).exists()
                )

    def test_extract_allows_explicit_judgment_only_from_user_provided_source(self) -> None:
        content = b"I believe customer trust is the durable advantage.\n"
        source = self.root / "user-note.txt"
        source.write_bytes(content)
        document = source_document_for_bytes(content, document_id="user-note-1")
        document["source_class"] = "user_provided"
        document["document_type"] = "research_note"
        document_path = self.write_json("user-note-document.json", document)
        code, _, error = self.run_cli(
            "ingest-document", str(document_path), str(source)
        )
        self.assertEqual(code, 0, error)

        extracted = evidence_for_document(document)
        extracted["statement"] = "The user believes customer trust is the durable advantage."
        extracted["content_class"] = "user_judgment"
        extracted["attribution"] = "user"
        extracted["verification_status"] = "unreviewed"
        extracted["citations"][0]["quoted_text"] = (
            "I believe customer trust is the durable advantage."
        )
        output_path = self.write_json("user-note-output.json", [extracted])
        request_path = self.write_json("user-note-request.json", extraction_request())

        code, result, error = self.run_cli(
            "extract-evidence",
            "company-1",
            "user-note-1",
            str(request_path),
            *self.fake_adapter_arguments(output_path, run_id="user-note-run"),
        )

        self.assertEqual(code, 0, error)
        self.assertEqual(result["output"][0]["content_class"], "user_judgment")

    def test_generate_rejects_future_context_before_adapter(self) -> None:
        scenarios = (
            "future_thesis",
            "future_document",
            "future_evidence_available",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                self.workspace = self.root / f"workspace-{scenario}"
                base = thesis_card()
                documents = [source_document()]
                evidence_payload = evidence()
                selected_document_ids = ["doc-1"]
                expected_message = "after analysis_cutoff_at"
                if scenario == "future_thesis":
                    base = thesis_card(as_of_date="2025-01-01")
                    base["version"]["created_at"] = "2025-01-01T00:00:00Z"
                    base["version"]["updated_at"] = "2025-01-01T00:00:00Z"
                    expected_message = "current ThesisCard did not exist"
                elif scenario == "future_document":
                    future_document = copy.deepcopy(source_document())
                    future_document["source_document_id"] = "doc-future"
                    future_document["title"] = "Future results"
                    future_document["published_on"] = "2025-01-01"
                    future_document["publicly_available_at"] = "2025-01-01T00:00:00Z"
                    future_document["ingested_at"] = "2025-01-01T00:05:00Z"
                    future_document["snapshot"]["storage_uri"] = (
                        "file:///immutable/doc-future.pdf"
                    )
                    documents.append(future_document)
                    selected_document_ids.append("doc-future")
                    expected_message = "SourceDocuments were not public"
                elif scenario == "future_evidence_available":
                    evidence_payload["available_as_of"] = "2025-01-01T00:00:00Z"
                    evidence_payload["created_at"] = "2025-01-01T00:05:00Z"
                self.seed_external_generation_context(
                    base=base,
                    documents=documents,
                    evidence_payload=evidence_payload,
                )
                request_path = self.write_json(
                    f"generation-future-{scenario}.json", generation_request()
                )
                context_arguments: list[str] = []
                for document_id in selected_document_ids:
                    context_arguments.extend(("--document", document_id))
                code, result, error = self.run_cli(
                    "generate-diff",
                    "company-1",
                    str(request_path),
                    *context_arguments,
                    "--evidence",
                    "evidence-1",
                    "--adapter",
                    "/definitely/missing/thesisos-adapter",
                    "--model-id",
                    "fixture/model-v1",
                    "--run-id",
                    f"future-{scenario}",
                )
                self.assertEqual(code, 2)
                self.assertIsNone(result)
                self.assertIn(expected_message, error["error"]["message"])
                self.assertFalse(
                    (
                        self.workspace
                        / "companies"
                        / "company-1"
                        / "model_runs"
                        / f"future-{scenario}.json"
                    ).exists()
                )

    def test_generate_allows_post_cutoff_extraction_of_pre_cutoff_evidence(self) -> None:
        """Replay cutoffs bound knowledge availability, not processing time."""

        evidence_payload = evidence()
        evidence_payload["created_at"] = "2025-01-01T00:05:00Z"
        self.seed_external_generation_context(evidence_payload=evidence_payload)
        request_path = self.write_json(
            "generation-post-cutoff-processing.json",
            generation_request(),
        )
        generated = thesis_diff()
        generated["management_statement_action"] = {
            "assessment": "insufficient_evidence",
            "summary": "No prior management statement was selected for comparison.",
            "comparisons": [],
        }
        output_path = self.write_json(
            "generation-post-cutoff-output.json",
            generated,
        )

        code, result, error = self.run_cli(
            "generate-diff",
            "company-1",
            str(request_path),
            "--document",
            "doc-1",
            "--evidence",
            "evidence-1",
            *self.fake_adapter_arguments(output_path, run_id="post-cutoff-processing"),
        )

        self.assertEqual(code, 0, error)
        self.assertEqual(result["thesis_diff_id"], "diff-1")

    def test_generate_loads_documents_cited_only_by_prior_evidence(self) -> None:
        base_path = self.write_json("prior-base.json", thesis_card())
        self.assertEqual(self.run_cli("commit-thesis", str(base_path))[0], 0)

        new_document = source_document()
        old_document = copy.deepcopy(new_document)
        old_document["source_document_id"] = "doc-old"
        old_document["title"] = "Prior quarterly results"
        old_document["published_on"] = "2024-02-07"
        old_document["publicly_available_at"] = "2024-02-07T08:00:00Z"
        old_document["ingested_at"] = "2024-02-07T08:05:00Z"
        old_document["snapshot"]["sha256"] = "b" * 64
        old_document["snapshot"]["storage_uri"] = "file:///immutable/doc-old.pdf"
        for index, document in enumerate((old_document, new_document)):
            path = self.write_json(f"prior-document-{index}.json", document)
            code, _, error = self.run_cli("save-document", str(path))
            self.assertEqual(code, 0, error)

        old_evidence = evidence()
        old_evidence["evidence_id"] = "evidence-old"
        old_evidence["available_as_of"] = "2024-02-07T08:00:00Z"
        old_evidence["created_at"] = "2024-02-07T08:05:00Z"
        old_evidence["reported_for"] = "FY2024 Q3"
        old_evidence["citations"][0]["citation_id"] = "citation-old"
        old_evidence["citations"][0]["source_document_id"] = "doc-old"
        old_evidence["citations"][0]["snapshot_sha256"] = "b" * 64
        for index, evidence_payload in enumerate((old_evidence, evidence())):
            path = self.write_json(f"prior-evidence-{index}.json", evidence_payload)
            code, _, error = self.run_cli("save-evidence", str(path))
            self.assertEqual(code, 0, error)

        generated = thesis_diff()
        comparison = generated["management_statement_action"]["comparisons"][0]
        comparison["past_evidence_ids"] = ["evidence-old"]
        output_path = self.write_json("prior-generation-output.json", generated)
        request_path = self.write_json("prior-generation-request.json", generation_request())
        code, result, error = self.run_cli(
            "generate-diff",
            "company-1",
            str(request_path),
            "--document",
            "doc-1",
            "--evidence",
            "evidence-1",
            "--prior-evidence",
            "evidence-old",
            *self.fake_adapter_arguments(output_path, run_id="prior-generation-run"),
        )

        self.assertEqual(code, 0, error)
        run_record = json.loads(Path(result["model_run_path"]).read_text(encoding="utf-8"))
        self.assertEqual(
            run_record["input_references"]["prior_source_document_ids"],
            ["doc-old"],
        )

    def test_generate_rejects_output_identifier_prefix_drift(self) -> None:
        self.seed_external_generation_context()
        fields = (
            ("comparison_id_prefix", "required-comparison"),
            ("question_id_prefix", "required-question"),
            ("change_id_prefix", "required-change"),
        )
        for index, (prefix_field, required_prefix) in enumerate(fields):
            with self.subTest(prefix_field=prefix_field):
                request_path = self.write_json(
                    f"prefix-request-{index}.json",
                    generation_request(**{prefix_field: required_prefix}),
                )
                output_path = self.write_json(
                    f"prefix-output-{index}.json", thesis_diff()
                )
                run_id = f"prefix-run-{index}"
                code, result, error = self.run_cli(
                    "generate-diff",
                    "company-1",
                    str(request_path),
                    "--document",
                    "doc-1",
                    "--evidence",
                    "evidence-1",
                    *self.fake_adapter_arguments(output_path, run_id=run_id),
                )
                self.assertEqual(code, 2)
                self.assertIsNone(result)
                self.assertIn("does not use request_metadata", error["error"]["message"])
                company = self.workspace / "companies" / "company-1"
                self.assertFalse((company / "model_runs" / f"{run_id}.json").exists())
                self.assertFalse((company / "diffs" / "diff-1.json").exists())

    def test_review_rechecks_management_comparison_evidence_references(self) -> None:
        content = b"Revenue increased year over year.\n"
        source = self.root / "review-management-source.txt"
        source.write_bytes(content)
        document = source_document_for_bytes(content, document_id="doc-1")
        document_path = self.write_json("review-management-document.json", document)
        code, _, error = self.run_cli(
            "ingest-document", str(document_path), str(source)
        )
        self.assertEqual(code, 0, error)
        base_path = self.write_json("review-management-base.json", thesis_card())
        self.assertEqual(self.run_cli("commit-thesis", str(base_path))[0], 0)

        first = evidence_for_document(document)
        second = copy.deepcopy(first)
        second["evidence_id"] = "evidence-2"
        second["citations"][0]["citation_id"] = "citation-2"
        for index, payload in enumerate((first, second), start=1):
            path = self.write_json(f"review-management-evidence-{index}.json", payload)
            code, _, error = self.run_cli("save-evidence", str(path))
            self.assertEqual(code, 0, error)

        diff_payload = thesis_diff()
        comparison = diff_payload["management_statement_action"]["comparisons"][0]
        comparison["past_evidence_ids"] = ["evidence-2"]
        comparison["current_evidence_ids"] = ["evidence-2"]
        diff_path = self.write_json("review-management-diff.json", diff_payload)
        review_path = self.write_json("review-management-review.json", user_review())

        stored_second = (
            self.workspace
            / "companies"
            / "company-1"
            / "evidence"
            / "evidence-2.json"
        )
        tampered = json.loads(stored_second.read_text(encoding="utf-8"))
        tampered["citations"][0]["quoted_text"] = "A quote absent from the snapshot."
        stored_second.write_text(json.dumps(tampered), encoding="utf-8")

        code, result, error = self.run_cli(
            "review", str(diff_path), str(review_path)
        )
        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("quoted_text was not found", error["error"]["message"])

    def test_review_rejects_symlinked_artifact_files(self) -> None:
        self.seed_external_generation_context()
        outside = self.write_json("outside-evidence.json", evidence())
        link = (
            self.workspace
            / "companies"
            / "company-1"
            / "evidence"
            / "evidence-linked.json"
        )
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")
        diff_path = self.write_json("symlink-artifact-diff.json", thesis_diff())
        review_path = self.write_json("symlink-artifact-review.json", user_review())

        code, result, error = self.run_cli(
            "review",
            str(diff_path),
            str(review_path),
        )

        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("symbolic-link evidence artifact", error["error"]["message"])
        company = self.workspace / "companies" / "company-1"
        self.assertFalse((company / "thesis_versions" / "version-2.json").exists())
        self.assertFalse((company / "reviews" / "review-1.json").exists())


if __name__ == "__main__":
    unittest.main()
