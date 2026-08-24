from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from thesisos.model_runtime import (
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    AdapterLaunchError,
    AdapterOutputError,
    AdapterOutputTooLargeError,
    AdapterProcessError,
    AdapterTimeoutError,
    ModelRuntimeInputError,
    PromptCatalog,
    PromptCatalogError,
    discover_prompt_directory,
    run_model_adapter,
)


ROOT = Path(__file__).resolve().parents[1]
FAKE_ADAPTER = ROOT / "tests" / "fixtures" / "fake_model_adapter.py"


class ModelRuntimeTest(unittest.TestCase):
    def argv(self, mode: str = "echo", *extra: str) -> list[str]:
        return [sys.executable, str(FAKE_ADAPTER), mode, *extra]

    def run_task(self, task: str, mode: str = "echo", **overrides: object):
        arguments: dict[str, object] = {
            "task": task,
            "model_identifier": "fixture/provider-model-v1",
            "request_metadata": {
                "analysis_cutoff_at": "2024-05-14T12:00:00Z",
                "id_prefix": "fixture",
            },
            "inputs": {
                "document": {"source_document_id": "doc-1"},
                "located_text": [{"line": 1, "text": "收入增长。"}],
            },
        }
        arguments.update(overrides)
        return run_model_adapter(self.argv(mode), **arguments)

    def test_installed_package_prompt_catalog_precedes_repository_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resource_root = Path(temporary)
            packaged = resource_root / "_prompts"
            packaged.mkdir()
            for filename in ("evidence-extraction.md", "thesis-diff.md"):
                (packaged / filename).write_bytes((ROOT / "prompts" / filename).read_bytes())

            with patch(
                "thesisos.model_runtime.resources.files",
                return_value=resource_root,
            ):
                self.assertEqual(discover_prompt_directory(), packaged.resolve())

    def test_explicit_prompt_directory_never_silently_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(PromptCatalogError, str(Path(temporary))):
                discover_prompt_directory(temporary)

    def test_evidence_extraction_receives_complete_versioned_envelope(self) -> None:
        result = self.run_task("evidence-extraction")

        self.assertIsInstance(result.output, list)
        received = result.output[0]
        self.assertEqual(received["received_protocol"], PROTOCOL_NAME)
        self.assertEqual(received["received_protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(received["received_task"], "evidence-extraction")
        self.assertEqual(
            received["received_contract"]["content"],
            (ROOT / "prompts" / "evidence-extraction.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(received["received_contract"]["contract_version"], "0.1.0")
        self.assertEqual(
            received["received_request_metadata"]["analysis_cutoff_at"],
            "2024-05-14T12:00:00Z",
        )
        self.assertEqual(
            received["received_inputs"]["document"]["source_document_id"], "doc-1"
        )

    def test_thesis_diff_requires_and_returns_one_object(self) -> None:
        result = self.run_task(
            "thesis-diff",
            inputs={
                "base_thesis_card": {"thesis_id": "thesis-1"},
                "new_evidence": [{"evidence_id": "evidence-1"}],
            },
        )

        self.assertIsInstance(result.output, dict)
        self.assertEqual(result.output["received_task"], "thesis-diff")
        self.assertEqual(
            result.output["received_contract"]["content"],
            (ROOT / "prompts" / "thesis-diff.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(result.output["received_contract"]["contract_version"], "0.1.2")
        self.assertEqual(
            result.output["received_inputs"]["base_thesis_card"]["thesis_id"],
            "thesis-1",
        )

    def test_canonical_hashes_are_deterministic_and_provenance_omits_argv(self) -> None:
        first = self.run_task("evidence-extraction")
        second = self.run_task("evidence-extraction")

        self.assertEqual(
            first.provenance.normalized_input_sha256,
            second.provenance.normalized_input_sha256,
        )
        self.assertEqual(
            first.provenance.normalized_output_sha256,
            second.provenance.normalized_output_sha256,
        )
        provenance = first.provenance.to_dict()
        self.assertNotIn("argv", json.dumps(provenance, sort_keys=True))
        self.assertEqual(provenance["task"], "evidence-extraction")
        self.assertEqual(provenance["contract_version"], "0.1.0")
        self.assertEqual(provenance["model_identifier"], "fixture/provider-model-v1")
        self.assertEqual(len(provenance["normalized_input_sha256"]), 64)
        self.assertEqual(len(provenance["normalized_output_sha256"]), 64)
        self.assertLessEqual(
            datetime.fromisoformat(provenance["started_at"].replace("Z", "+00:00")),
            datetime.fromisoformat(provenance["finished_at"].replace("Z", "+00:00")),
        )

    def test_canonical_hash_ignores_mapping_insertion_order(self) -> None:
        first = self.run_task(
            "evidence-extraction",
            request_metadata={"b": 2, "a": 1},
            inputs={"z": [3, 2, 1], "a": {"y": 2, "x": 1}},
        )
        second = self.run_task(
            "evidence-extraction",
            request_metadata={"a": 1, "b": 2},
            inputs={"a": {"x": 1, "y": 2}, "z": [3, 2, 1]},
        )
        self.assertEqual(
            first.provenance.normalized_input_sha256,
            second.provenance.normalized_input_sha256,
        )
        self.assertEqual(
            first.provenance.normalized_output_sha256,
            second.provenance.normalized_output_sha256,
        )

    def test_nonzero_exit_fails_closed(self) -> None:
        with self.assertRaisesRegex(AdapterProcessError, "status 17"):
            self.run_task("evidence-extraction", "nonzero")

    def test_timeout_kills_adapter_and_fails_closed(self) -> None:
        with self.assertRaises(AdapterTimeoutError):
            self.run_task("evidence-extraction", "sleep", timeout_seconds=0.05)

    def test_invalid_json_and_duplicate_keys_fail_closed(self) -> None:
        with self.assertRaises(AdapterOutputError):
            self.run_task("evidence-extraction", "invalid-json")
        with self.assertRaises(AdapterOutputError):
            self.run_task("thesis-diff", "duplicate-key")

    def test_wrong_task_output_shapes_fail_closed(self) -> None:
        with self.assertRaisesRegex(AdapterOutputError, "array"):
            self.run_task("evidence-extraction", "wrong-shape")
        with self.assertRaisesRegex(AdapterOutputError, "one JSON object"):
            self.run_task("thesis-diff", "wrong-shape")

    def test_stdout_limit_fails_closed_without_retaining_unbounded_output(self) -> None:
        with self.assertRaisesRegex(AdapterOutputTooLargeError, "256 bytes"):
            self.run_task(
                "evidence-extraction",
                "oversize",
                max_stdout_bytes=256,
            )

    def test_argv_is_never_executed_through_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "shell-injection-marker"
            shell_text = f"; touch {marker}"
            result = run_model_adapter(
                self.argv("echo", shell_text),
                task="evidence-extraction",
                model_identifier="safe-model",
                request_metadata={"cutoff": "fixed"},
                inputs={"text": "safe"},
            )
            self.assertIsInstance(result.output, list)
            self.assertFalse(marker.exists())
            self.assertNotIn(shell_text, json.dumps(result.provenance.to_dict()))

    def test_command_string_and_invalid_json_inputs_are_rejected(self) -> None:
        with self.assertRaises(ModelRuntimeInputError):
            run_model_adapter(
                f"{sys.executable} {FAKE_ADAPTER}",
                task="evidence-extraction",
                model_identifier="model",
                request_metadata={},
                inputs={},
            )
        with self.assertRaises(ModelRuntimeInputError):
            self.run_task("evidence-extraction", inputs={"bad": float("nan")})

    def test_launch_failure_does_not_echo_secret_argv(self) -> None:
        secret = "top-secret-token"
        with self.assertRaises(AdapterLaunchError) as raised:
            run_model_adapter(
                ["/definitely/missing/thesisos-adapter", secret],
                task="thesis-diff",
                model_identifier="model",
                request_metadata={},
                inputs={},
            )
        self.assertNotIn(secret, str(raised.exception))

    def test_prompt_catalog_rejects_contract_metadata_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prompt_dir = Path(temporary)
            for name in ("evidence-extraction.md", "thesis-diff.md"):
                source = (ROOT / "prompts" / name).read_text(encoding="utf-8")
                (prompt_dir / name).write_text(source, encoding="utf-8")
            drifted = (prompt_dir / "evidence-extraction.md").read_text(encoding="utf-8")
            (prompt_dir / "evidence-extraction.md").write_text(
                drifted.replace("Contract version: `0.1.0`", "Contract version: `9.9.9`"),
                encoding="utf-8",
            )
            with self.assertRaises(PromptCatalogError):
                PromptCatalog(prompt_dir).load("evidence-extraction")

    def test_unknown_task_is_rejected_before_process_launch(self) -> None:
        with self.assertRaisesRegex(ModelRuntimeInputError, "unsupported model task"):
            run_model_adapter(
                self.argv("echo"),
                task="red-team",
                model_identifier="model",
                request_metadata={},
                inputs={},
            )


if __name__ == "__main__":
    unittest.main()
