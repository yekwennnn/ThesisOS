from __future__ import annotations

import json
import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from thesisos.versioning import (
    ImmutableRecordError,
    VersionConflictError,
    VersioningError,
    apply_user_review,
    commit_thesis_version,
    initialize_workspace,
    object_sha256,
    read_current_thesis,
    save_company_artifact,
    save_company_artifact_bundle,
)


def thesis(version_id: str, supersedes: str | None, claim: str = "Durable customer value") -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "thesis_id": "THESIS-ALIBABA",
        "company": {"company_id": "alibaba"},
        "one_sentence_thesis": claim,
        "version": {
            "version_id": version_id,
            "supersedes": supersedes,
            "user_confirmed": True,
            "updated_at": "2024-01-01T00:00:00Z",
        },
    }


def diff_with(proposed: dict[str, object]) -> dict[str, object]:
    draft = dict(proposed)
    draft["version"] = dict(proposed["version"])
    draft["version"]["user_confirmed"] = False
    return {
        "schema_version": "1.0.0",
        "company_id": "alibaba",
        "thesis_diff_id": "DIFF-001",
        "base_thesis_id": "THESIS-ALIBABA",
        "base_version_id": "V1",
        "proposed_patch": {
            "patch_status": "pending_user_review",
            "base_thesis_id": "THESIS-ALIBABA",
            "base_version_id": "V1",
            "proposed_thesis": draft,
        },
    }


def review(decision: str, **extra: object) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "company_id": "alibaba",
        "user_review_id": f"REVIEW-{decision}",
        "thesis_diff_id": "DIFF-001",
        "base_thesis_id": "THESIS-ALIBABA",
        "base_version_id": "V1",
        "decision": decision,
        "reviewer_id": "USER-001",
        "reviewed_at": "2024-05-15T00:00:00Z",
        **extra,
    }


def _competing_mutation_worker(
    workspace: str,
    action: str,
    payload: dict[str, object],
    review_payload: dict[str, object] | None,
    ready_queue: object,
    start_event: object,
    result_queue: object,
) -> None:
    """Run one mutation in a child process with a widened preflight race."""

    import thesisos.versioning as versioning

    original_preflight = versioning._preflight_thesis_commit

    def delayed_preflight(root: Path, candidate: dict[str, object]):
        result = original_preflight(root, candidate)
        time.sleep(0.2)
        return result

    versioning._preflight_thesis_commit = delayed_preflight
    ready_queue.put(action)
    try:
        if not start_event.wait(10):
            raise RuntimeError("timed out waiting for synchronized start")
        if action == "review":
            if review_payload is None:
                raise RuntimeError("review action requires a review payload")
            result = apply_user_review(workspace, payload, review_payload)
            version_id = result["promoted_version_id"]
        else:
            result = commit_thesis_version(workspace, payload)
            version_id = result["version"]["version_id"]
        result_queue.put(("ok", action, version_id))
    except Exception as exc:  # Report expected conflicts back to the parent test process.
        result_queue.put(("error", action, type(exc).__name__, str(exc)))


def _multiprocessing_context():
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("fork" if "fork" in methods else "spawn")


class VersioningTest(unittest.TestCase):
    def test_artifact_bundle_preflights_every_directory_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            initialize_workspace(workspace)
            company = workspace / "companies" / "alibaba"
            company.mkdir(parents=True)
            (company / "diffs").write_text("blocks the directory", encoding="utf-8")

            with self.assertRaisesRegex(VersioningError, "not a directory"):
                save_company_artifact_bundle(
                    workspace,
                    "alibaba",
                    [
                        ("model_runs", "run-1", {"model_run_id": "run-1"}),
                        ("diffs", "diff-1", {"thesis_diff_id": "diff-1"}),
                    ],
                )

            self.assertFalse((company / "model_runs" / "run-1.json").exists())

    def test_artifact_bundle_opens_audit_target_before_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            initialize_workspace(workspace)
            # A directory at the append target is an ordinary, deterministic
            # audit-open failure on every supported platform.
            (workspace / "audit" / "events.jsonl").mkdir()

            with self.assertRaises((OSError, VersioningError)):
                save_company_artifact_bundle(
                    workspace,
                    "alibaba",
                    [
                        ("model_runs", "run-1", {"model_run_id": "run-1"}),
                        ("diffs", "diff-1", {"thesis_diff_id": "diff-1"}),
                    ],
                )

            self.assertEqual(
                list((workspace / "companies").rglob("*.json")),
                [],
            )

    def test_commit_opens_audit_target_before_advancing_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            initialize_workspace(workspace)
            (workspace / "audit" / "events.jsonl").mkdir()

            with self.assertRaises((OSError, VersioningError)):
                commit_thesis_version(workspace, thesis("V1", None))

            company = workspace / "companies" / "alibaba"
            self.assertFalse((company / "thesis_versions" / "V1.json").exists())
            self.assertFalse((company / "current_thesis.json").exists())

    def test_artifact_opens_audit_target_before_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            initialize_workspace(workspace)
            (workspace / "audit" / "events.jsonl").mkdir()

            with self.assertRaises((OSError, VersioningError)):
                save_company_artifact(
                    workspace,
                    "alibaba",
                    "evidence",
                    "E-001",
                    {"evidence_id": "E-001"},
                )

            self.assertFalse(
                (
                    workspace
                    / "companies"
                    / "alibaba"
                    / "evidence"
                    / "E-001.json"
                ).exists()
            )

    def test_review_opens_audit_target_before_publishing_any_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            commit_thesis_version(workspace, thesis("V1", None))
            audit_path = workspace / "audit" / "events.jsonl"
            audit_path.unlink()
            audit_path.mkdir()
            proposed = thesis("V2", "V1", claim="Reviewed candidate")

            with self.assertRaises((OSError, VersioningError)):
                apply_user_review(
                    workspace,
                    diff_with(proposed),
                    review("accept"),
                )

            company = workspace / "companies" / "alibaba"
            self.assertFalse((company / "diffs" / "DIFF-001.json").exists())
            self.assertFalse(
                (company / "reviews" / "REVIEW-accept.json").exists()
            )
            self.assertFalse((company / "thesis_versions" / "V2.json").exists())
            self.assertEqual(
                read_current_thesis(workspace, "alibaba")["version"]["version_id"],
                "V1",
            )

    def test_review_preflights_all_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            commit_thesis_version(workspace, thesis("V1", None))
            company = workspace / "companies" / "alibaba"
            company.mkdir(parents=True, exist_ok=True)
            (company / "reviews").write_text("not a directory", encoding="utf-8")

            with self.assertRaisesRegex(VersioningError, "not a directory"):
                apply_user_review(
                    workspace,
                    diff_with(thesis("V2", "V1")),
                    review("accept"),
                )

            self.assertFalse((company / "diffs" / "DIFF-001.json").exists())
            self.assertFalse((company / "thesis_versions" / "V2.json").exists())

    def test_artifact_bundle_publishes_related_records_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            paths = save_company_artifact_bundle(
                workspace,
                "alibaba",
                [
                    ("model_runs", "run-1", {"model_run_id": "run-1"}),
                    ("diffs", "diff-1", {"thesis_diff_id": "diff-1"}),
                ],
            )

            self.assertEqual(len(paths), 2)
            self.assertTrue(all(path.is_file() for path in paths))
            events = [
                json.loads(line)
                for line in (workspace / "audit" / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(events[-1]["event"], "artifact_bundle_saved")
            self.assertEqual(len(events[-1]["artifacts"]), 2)

    def _run_competing_mutations(
        self,
        workspace: Path,
        operations: list[tuple[str, dict[str, object], dict[str, object] | None]],
    ) -> list[tuple[object, ...]]:
        context = _multiprocessing_context()
        ready_queue = context.Queue()
        result_queue = context.Queue()
        start_event = context.Event()
        processes = [
            context.Process(
                target=_competing_mutation_worker,
                args=(
                    str(workspace),
                    action,
                    payload,
                    review_payload,
                    ready_queue,
                    start_event,
                    result_queue,
                ),
            )
            for action, payload, review_payload in operations
        ]
        try:
            for process in processes:
                process.start()
            ready = {ready_queue.get(timeout=10) for _ in processes}
            self.assertEqual(ready, {operation[0] for operation in operations})
            start_event.set()
            for process in processes:
                process.join(timeout=20)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                    self.fail("competing mutation process did not finish")
                self.assertEqual(process.exitcode, 0)
            return [result_queue.get(timeout=5) for _ in processes]
        finally:
            start_event.set()
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)

    def test_company_lock_rejects_symlinked_lock_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            outside = root / "outside"
            initialize_workspace(workspace)
            outside.mkdir()
            try:
                (workspace / "locks").symlink_to(outside, target_is_directory=True)
            except OSError as exc:  # Windows may require Developer Mode/admin rights.
                self.skipTest(f"symbolic links unavailable: {exc}")

            with self.assertRaisesRegex(VersioningError, "symbolic-link"):
                commit_thesis_version(workspace, thesis("V1", None))
            self.assertEqual(list(outside.rglob("*")), [])

    def test_competing_children_from_one_base_are_linearized_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            commit_thesis_version(workspace, thesis("V1", None))
            operations = [
                ("commit-a", thesis("V2-A", "V1", claim="Candidate A"), None),
                ("commit-b", thesis("V2-B", "V1", claim="Candidate B"), None),
            ]

            results = self._run_competing_mutations(workspace, operations)

            successes = [result for result in results if result[0] == "ok"]
            failures = [result for result in results if result[0] == "error"]
            self.assertEqual(len(successes), 1, results)
            self.assertEqual(len(failures), 1, results)
            self.assertEqual(failures[0][2], "VersionConflictError")
            winner = successes[0][2]
            versions = {
                path.stem
                for path in (workspace / "companies" / "alibaba" / "thesis_versions").glob("*.json")
            }
            self.assertEqual(versions, {"V1", winner})
            self.assertEqual(
                read_current_thesis(workspace, "alibaba")["version"]["version_id"],
                winner,
            )
            audit_events = [
                json.loads(line)
                for line in (workspace / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            child_commits = [
                event
                for event in audit_events
                if event.get("event") == "thesis_version_committed"
                and event.get("supersedes") == "V1"
            ]
            self.assertEqual([event["version_id"] for event in child_commits], [winner])

    def test_review_and_direct_commit_cannot_both_advance_one_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            commit_thesis_version(workspace, thesis("V1", None))
            proposed = thesis("V2-review", "V1", claim="Reviewed candidate")
            operations = [
                ("review", diff_with(proposed), review("accept")),
                ("commit-direct", thesis("V2-direct", "V1", claim="Direct candidate"), None),
            ]

            results = self._run_competing_mutations(workspace, operations)

            successes = [result for result in results if result[0] == "ok"]
            failures = [result for result in results if result[0] == "error"]
            self.assertEqual(len(successes), 1, results)
            self.assertEqual(len(failures), 1, results)
            self.assertEqual(failures[0][2], "VersionConflictError")
            winner = successes[0][2]
            versions = {
                path.stem
                for path in (workspace / "companies" / "alibaba" / "thesis_versions").glob("*.json")
            }
            self.assertEqual(versions, {"V1", winner})
            self.assertEqual(
                read_current_thesis(workspace, "alibaba")["version"]["version_id"],
                winner,
            )
            company = workspace / "companies" / "alibaba"
            if winner == "V2-direct":
                self.assertFalse((company / "diffs").exists())
                self.assertFalse((company / "reviews").exists())
            else:
                self.assertEqual(winner, "V2-review")
                self.assertTrue((company / "diffs" / "DIFF-001.json").is_file())
                self.assertTrue((company / "reviews" / "REVIEW-accept.json").is_file())

    def test_portable_identifiers_reject_drive_device_and_trailing_dot_names(self) -> None:
        unsafe = ("D:escape", "CON", "nul.txt", "artifact.")
        for identifier in unsafe:
            with self.subTest(company_id=identifier), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(VersioningError, "unsafe identifier"):
                    save_company_artifact(tmp, identifier, "documents", "doc-1", {"ok": True})
            with self.subTest(artifact_id=identifier), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(VersioningError, "unsafe identifier"):
                    save_company_artifact(tmp, "company-1", "documents", identifier, {"ok": True})

    def test_symlinked_company_directory_cannot_escape_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            outside = root / "outside"
            initialize_workspace(workspace)
            outside.mkdir()
            try:
                (workspace / "companies" / "evil").symlink_to(
                    outside, target_is_directory=True
                )
            except OSError as exc:  # Windows may require Developer Mode/admin rights.
                self.skipTest(f"symbolic links unavailable: {exc}")

            with self.assertRaisesRegex(VersioningError, "symbolic-link"):
                save_company_artifact(
                    workspace,
                    "evil",
                    "documents",
                    "doc-1",
                    {"outside": False},
                )

            self.assertEqual(list(outside.rglob("*")), [])

    def test_write_once_publish_failure_leaves_no_partial_final(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            initialize_workspace(workspace)
            final_path = workspace / "companies" / "company-1" / "documents" / "doc-1.json"

            with patch("thesisos.versioning.os.fsync", side_effect=OSError("injected fsync failure")):
                with self.assertRaisesRegex(OSError, "injected fsync failure"):
                    save_company_artifact(
                        workspace,
                        "company-1",
                        "documents",
                        "doc-1",
                        {"complete": True},
                    )

            self.assertFalse(final_path.exists())
            self.assertEqual(list(final_path.parent.glob("*.tmp")), [])

    def test_current_pointer_requires_valid_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            commit_thesis_version(workspace, thesis("V1", None))
            pointer_path = workspace / "companies" / "alibaba" / "current_thesis.json"
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))

            for bad_hash in (None, "", "A" * 64, "0" * 63):
                with self.subTest(record_sha256=bad_hash):
                    changed = dict(pointer)
                    if bad_hash is None:
                        changed.pop("record_sha256")
                    else:
                        changed["record_sha256"] = bad_hash
                    pointer_path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(ImmutableRecordError, "record_sha256"):
                        read_current_thesis(workspace, "alibaba")

    def test_current_pointer_cross_checks_company_thesis_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            committed = thesis("V1", None)
            commit_thesis_version(workspace, committed)
            company = workspace / "companies" / "alibaba"
            pointer_path = company / "current_thesis.json"
            original_pointer = json.loads(pointer_path.read_text(encoding="utf-8"))

            for field, value in (("company_id", "other"), ("thesis_id", "OTHER-THESIS")):
                with self.subTest(field=field):
                    changed = dict(original_pointer)
                    changed[field] = value
                    pointer_path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(ImmutableRecordError):
                        read_current_thesis(workspace, "alibaba")

            version_path = company / "thesis_versions" / "V1.json"
            changed_record = json.loads(version_path.read_text(encoding="utf-8"))
            changed_record["version"]["version_id"] = "OTHER-VERSION"
            version_path.write_text(json.dumps(changed_record), encoding="utf-8")
            changed_pointer = dict(original_pointer)
            changed_pointer["record_sha256"] = object_sha256(changed_record)
            pointer_path.write_text(json.dumps(changed_pointer), encoding="utf-8")
            with self.assertRaisesRegex(ImmutableRecordError, "version_id"):
                read_current_thesis(workspace, "alibaba")

    def test_reopening_workspace_does_not_rewrite_creation_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "thesisos.versioning.utc_now",
                side_effect=["2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"],
            ):
                initialize_workspace(tmp)
                manifest_path = Path(tmp) / "manifest.json"
                original = manifest_path.read_bytes()
                initialize_workspace(tmp)
            self.assertEqual(manifest_path.read_bytes(), original)

    def test_versions_are_immutable_and_form_a_linear_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            v1 = thesis("V1", None)
            commit_thesis_version(workspace, v1)
            v1_path = workspace / "companies" / "alibaba" / "thesis_versions" / "V1.json"
            original_bytes = v1_path.read_bytes()

            v2 = thesis("V2", "V1", claim="Updated customer value thesis")
            commit_thesis_version(workspace, v2)
            self.assertEqual(read_current_thesis(workspace, "alibaba"), v2)
            self.assertEqual(v1_path.read_bytes(), original_bytes)

            changed_v1 = thesis("V1", None, claim="Retrospectively rewritten")
            with self.assertRaises((ImmutableRecordError, VersionConflictError)):
                commit_thesis_version(workspace, changed_v1)
            self.assertEqual(v1_path.read_bytes(), original_bytes)

    def test_ai_draft_cannot_become_a_formal_version_without_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft = thesis("V1", None)
            draft["version"]["user_confirmed"] = False
            with self.assertRaisesRegex(VersioningError, "user_confirmed"):
                commit_thesis_version(tmp, draft)

    def test_accept_promotes_patch_but_reject_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            commit_thesis_version(tmp, thesis("V1", None))
            proposed = thesis("V2", "V1", claim="Evidence-updated thesis")
            result = apply_user_review(tmp, diff_with(proposed), review("accept"))
            self.assertEqual(result["promoted_version_id"], "V2")
            self.assertEqual(read_current_thesis(tmp, "alibaba")["version"]["version_id"], "V2")

        with tempfile.TemporaryDirectory() as tmp:
            commit_thesis_version(tmp, thesis("V1", None))
            proposed = thesis("V2", "V1")
            result = apply_user_review(tmp, diff_with(proposed), review("reject"))
            self.assertIsNone(result["promoted_version_id"])
            self.assertEqual(read_current_thesis(tmp, "alibaba")["version"]["version_id"], "V1")

    def test_accept_with_edits_requires_reviewed_thesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            commit_thesis_version(tmp, thesis("V1", None))
            proposed = thesis("V2", "V1")
            with self.assertRaisesRegex(VersioningError, "reviewed_thesis"):
                apply_user_review(tmp, diff_with(proposed), review("accept_with_edits"))

            edited = thesis("V2-user", "V1", claim="User-edited thesis")
            result = apply_user_review(
                tmp,
                diff_with(proposed),
                review("accept_with_edits", reviewed_thesis=edited),
            )
            self.assertEqual(result["promoted_version_id"], "V2-user")
            self.assertEqual(read_current_thesis(tmp, "alibaba"), edited)

    def test_invalid_acceptance_is_rejected_before_review_artifacts_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            commit_thesis_version(tmp, thesis("V1", None))
            proposed = thesis("V2", "WRONG-BASE")
            with self.assertRaises(VersionConflictError):
                apply_user_review(tmp, diff_with(proposed), review("accept"))
            company = Path(tmp) / "companies" / "alibaba"
            self.assertFalse((company / "diffs").exists())
            self.assertFalse((company / "reviews").exists())

    def test_stale_diff_cannot_overwrite_a_newer_current_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            commit_thesis_version(tmp, thesis("V1", None))
            stale_diff = diff_with(thesis("V2-stale", "V1"))
            commit_thesis_version(tmp, thesis("V2", "V1"))
            with self.assertRaisesRegex(VersionConflictError, "stale"):
                apply_user_review(tmp, stale_diff, review("accept"))
            self.assertEqual(read_current_thesis(tmp, "alibaba")["version"]["version_id"], "V2")
            reviews_dir = Path(tmp) / "companies" / "alibaba" / "reviews"
            self.assertFalse(reviews_dir.exists())

    def test_reusing_an_artifact_id_with_changed_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            commit_thesis_version(tmp, thesis("V1", None))
            proposed = thesis("V2", "V1")
            apply_user_review(tmp, diff_with(proposed), review("reject"))
            changed = review("reject", note="changed after the fact")
            with self.assertRaises(ImmutableRecordError):
                apply_user_review(tmp, diff_with(proposed), changed)
            stored = json.loads(
                (Path(tmp) / "companies" / "alibaba" / "reviews" / "REVIEW-reject.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("note", stored)

    def test_research_tasks_are_stored_without_promoting_a_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            commit_thesis_version(tmp, thesis("V1", None))
            proposed = thesis("V2", "V1")
            task = {
                "research_task_id": "TASK-001",
                "question": "Can engagement improve without lower take rates?",
                "why_it_matters": "It tests the unit-economics assumption.",
                "linked_assumption_ids": ["A-01"],
                "status": "open",
            }
            result = apply_user_review(
                tmp,
                diff_with(proposed),
                review("create_research_task", research_tasks=[task]),
            )
            self.assertIsNone(result["promoted_version_id"])
            stored = json.loads(
                (Path(tmp) / "companies" / "alibaba" / "research_tasks" / "TASK-001.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(stored, task)


if __name__ == "__main__":
    unittest.main()
