from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from thesisos.adversarial import (
    AdversarialSuiteError,
    evaluate_adversarial_suite,
    evaluate_adversarial_suites,
)


REPOSITORY = Path(__file__).resolve().parents[1]
SUITES = (
    REPOSITORY / "evals" / "citation-accuracy" / "suite.json",
    REPOSITORY / "evals" / "assumption-mapping" / "suite.json",
    REPOSITORY / "evals" / "future-leakage" / "suite.json",
)
GOLDEN_CASE = (
    REPOSITORY
    / "evals"
    / "historical-replay"
    / "alibaba-2024-q4"
    / "case.json"
)


class AdversarialSuiteTest(unittest.TestCase):
    def test_all_readme_suites_detect_every_declared_mutation(self) -> None:
        reports = evaluate_adversarial_suites(SUITES)

        self.assertEqual(
            tuple(report.suite_id for report in reports),
            ("citation-accuracy", "assumption-mapping", "future-leakage"),
        )
        for report in reports:
            with self.subTest(suite=report.suite_id):
                self.assertTrue(report.golden_passed, report.to_dict())
                self.assertGreaterEqual(len(report.mutations), 2)
                self.assertTrue(report.passed, report.to_dict())
                self.assertTrue(all(item.detected for item in report.mutations))

    def test_runner_is_deterministic(self) -> None:
        first = evaluate_adversarial_suite(SUITES[0]).to_dict()
        second = evaluate_adversarial_suite(SUITES[0]).to_dict()
        self.assertEqual(first, second)

    def test_unrelated_failure_does_not_count_as_expected_detection(self) -> None:
        manifest = json.loads(SUITES[0].read_text(encoding="utf-8"))
        manifest["golden_case"] = str(GOLDEN_CASE)
        manifest["mutations"][0]["expected_failed_checks"] = [
            "future_information_leakage"
        ]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            report = evaluate_adversarial_suite(path)

        self.assertFalse(report.passed)
        mutation = report.mutations[0]
        self.assertFalse(mutation.detected)
        self.assertIn(
            "citation_snapshot_and_locator_integrity",
            mutation.actual_failed_checks,
        )
        self.assertNotIn(
            "future_information_leakage",
            mutation.actual_failed_checks,
        )

    def test_no_op_mutation_fails_closed(self) -> None:
        manifest = json.loads(SUITES[0].read_text(encoding="utf-8"))
        manifest["golden_case"] = str(GOLDEN_CASE)
        manifest["mutations"][0]["value"] = (
            "f2a35600ac20f5a34506fc3d7ec6b91f67f1f0d4a195be2420e83d3a8b1466e5"
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(AdversarialSuiteError, "no-op"):
                evaluate_adversarial_suite(path)


if __name__ == "__main__":
    unittest.main()
