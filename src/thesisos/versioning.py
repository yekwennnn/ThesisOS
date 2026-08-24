from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:  # POSIX advisory locks.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows advisory byte-range locks.
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    msvcrt = None  # type: ignore[assignment]


REVIEW_DECISIONS = {
    "accept",
    "accept_with_edits",
    "reject",
    "defer_insufficient",
    "create_research_task",
}
COMPANY_ARTIFACT_KINDS = frozenset(
    {"documents", "evidence", "diffs", "reviews", "research_tasks", "model_runs"}
)

_PORTABLE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class VersioningError(ValueError):
    """Base class for an invalid or unsafe version transition."""


class ImmutableRecordError(VersioningError):
    """Raised when an existing immutable record would be changed."""


class VersionConflictError(VersioningError):
    """Raised when a transition no longer starts from the current version."""


def initialize_workspace(workspace: str | Path) -> Path:
    root = Path(workspace).resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    manifest_path = resolve_workspace_path(root, "manifest.json")
    existing = _read_json(manifest_path)
    if existing is not None:
        if existing.get("format") != "thesisos-workspace" or existing.get("format_version") != 1:
            raise VersioningError(f"unsupported workspace manifest: {manifest_path}")
    else:
        manifest = {
            "format": "thesisos-workspace",
            "format_version": 1,
            "created_at": utc_now(),
        }
        _write_once_json(manifest_path, manifest)
    for relative in ("companies", "audit"):
        directory = resolve_workspace_path(root, relative)
        directory.mkdir(parents=True, exist_ok=True)
        resolve_workspace_path(root, relative)
    return root


def commit_thesis_version(workspace: str | Path, thesis: dict[str, Any]) -> dict[str, Any]:
    company = _required_object(thesis, "company")
    company_id = _required_id(company, "company_id")
    root = initialize_workspace(workspace)
    with _company_advisory_lock(root, company_id):
        return _commit_thesis_version_locked(root, thesis)


def _commit_thesis_version_locked(
    root: Path,
    thesis: dict[str, Any],
    *,
    audit_target: tuple[int, Path] | None = None,
) -> dict[str, Any]:
    """Commit a version while the caller holds its company advisory lock."""

    company_id, version_id, supersedes, version_path = _preflight_thesis_commit(root, thesis)
    if audit_target is None:
        with _open_audit_append_target(root) as opened_target:
            return _publish_thesis_version_locked(
                root,
                thesis,
                company_id,
                version_id,
                supersedes,
                version_path,
                opened_target,
            )
    return _publish_thesis_version_locked(
        root,
        thesis,
        company_id,
        version_id,
        supersedes,
        version_path,
        audit_target,
    )


def _publish_thesis_version_locked(
    root: Path,
    thesis: dict[str, Any],
    company_id: str,
    version_id: str,
    supersedes: str | None,
    version_path: Path,
    audit_target: tuple[int, Path],
) -> dict[str, Any]:
    """Publish a preflighted version with a validated audit target held open."""

    version_path.parent.mkdir(parents=True, exist_ok=True)
    _write_once_json(version_path, thesis)
    pointer = {
        "company_id": company_id,
        "thesis_id": _required_id(thesis, "thesis_id"),
        "version_id": version_id,
        "record_sha256": object_sha256(thesis),
        "updated_at": utc_now(),
    }
    pointer_path = resolve_workspace_path(root, "companies", company_id, "current_thesis.json")
    _atomic_write_json(pointer_path, pointer)
    _write_audit_event(
        *audit_target,
        {
            "event": "thesis_version_committed",
            "company_id": company_id,
            "version_id": version_id,
            "supersedes": supersedes,
            "record_sha256": pointer["record_sha256"],
        },
    )
    return deepcopy(thesis)


def read_thesis_version(workspace: str | Path, company_id: str, version_id: str) -> dict[str, Any] | None:
    root = Path(workspace).resolve(strict=False)
    safe_company_id = _safe_component(company_id)
    path = resolve_workspace_path(
        root,
        "companies",
        safe_company_id,
        "thesis_versions",
        f"{_safe_component(version_id)}.json",
    )
    return _read_json(path)


def read_current_thesis(workspace: str | Path, company_id: str) -> dict[str, Any] | None:
    root = Path(workspace).resolve(strict=False)
    safe_company_id = _safe_component(company_id)
    pointer_path = resolve_workspace_path(root, "companies", safe_company_id, "current_thesis.json")
    pointer = _read_json(pointer_path)
    if pointer is None:
        return None
    pointer_company_id = _required_id(pointer, "company_id")
    pointer_thesis_id = _required_id(pointer, "thesis_id")
    version_id = _required_id(pointer, "version_id")
    expected_hash = pointer.get("record_sha256")
    if not isinstance(expected_hash, str) or _SHA256_RE.fullmatch(expected_hash) is None:
        raise ImmutableRecordError(
            f"current thesis pointer has missing or invalid record_sha256: {pointer_path}"
        )
    if pointer_company_id != safe_company_id:
        raise ImmutableRecordError(
            f"current thesis pointer company_id {pointer_company_id!r} does not match {safe_company_id!r}"
        )
    thesis = read_thesis_version(root, company_id, version_id)
    if thesis is None:
        raise VersioningError(f"current thesis pointer references missing version {version_id!r}")
    if object_sha256(thesis) != expected_hash:
        raise ImmutableRecordError(f"thesis version {version_id!r} no longer matches its recorded hash")
    thesis_company = _required_object(thesis, "company")
    thesis_version = _required_object(thesis, "version")
    if _required_id(thesis_company, "company_id") != safe_company_id:
        raise ImmutableRecordError("current thesis record company_id does not match its workspace path")
    if _required_id(thesis, "thesis_id") != pointer_thesis_id:
        raise ImmutableRecordError("current thesis pointer thesis_id does not match its record")
    if _required_id(thesis_version, "version_id") != version_id:
        raise ImmutableRecordError("current thesis pointer version_id does not match its record")
    return thesis


def save_company_artifact(
    workspace: str | Path,
    company_id: str,
    kind: str,
    artifact_id: str,
    payload: dict[str, Any],
) -> Path:
    safe_company_id = _safe_component(company_id)
    root = initialize_workspace(workspace)
    with _company_advisory_lock(root, safe_company_id):
        return _save_company_artifact_locked(
            root,
            safe_company_id,
            kind,
            artifact_id,
            payload,
        )


def _save_company_artifact_locked(
    root: Path,
    company_id: str,
    kind: str,
    artifact_id: str,
    payload: dict[str, Any],
    *,
    audit_target: tuple[int, Path] | None = None,
) -> Path:
    """Save one artifact while the caller holds its company mutation lock."""

    if kind not in COMPANY_ARTIFACT_KINDS:
        raise VersioningError(f"unsupported artifact kind: {kind}")
    safe_company_id = _safe_component(company_id)
    safe_artifact_id = _safe_component(artifact_id)
    directory = company_artifact_directory(root, safe_company_id, kind)
    directory.mkdir(parents=True, exist_ok=True)
    directory = company_artifact_directory(root, safe_company_id, kind)
    path = resolve_workspace_path(
        root,
        "companies",
        safe_company_id,
        kind,
        f"{safe_artifact_id}.json",
    )
    _assert_write_once_json(path, payload)
    if audit_target is None:
        with _open_audit_append_target(root) as opened_target:
            return _publish_company_artifact_locked(
                path,
                safe_company_id,
                kind,
                safe_artifact_id,
                payload,
                opened_target,
            )
    return _publish_company_artifact_locked(
        path,
        safe_company_id,
        kind,
        safe_artifact_id,
        payload,
        audit_target,
    )


def _publish_company_artifact_locked(
    path: Path,
    company_id: str,
    kind: str,
    artifact_id: str,
    payload: dict[str, Any],
    audit_target: tuple[int, Path],
) -> Path:
    """Publish a preflighted artifact with a validated audit target held open."""

    _write_once_json(path, payload)
    _write_audit_event(
        *audit_target,
        {
            "event": "artifact_saved",
            "company_id": company_id,
            "kind": kind,
            "artifact_id": artifact_id,
            "record_sha256": object_sha256(payload),
        },
    )
    return path


def save_company_artifact_bundle(
    workspace: str | Path,
    company_id: str,
    artifacts: list[tuple[str, str, dict[str, Any]]],
) -> tuple[Path, ...]:
    """Preflight and publish a small related artifact bundle under one lock.

    This is used when one successful command must not leave an ordinary
    validation/path conflict after its first record has already been written.
    It is not a filesystem transaction across power loss; callers can recover
    immutable records by their IDs, and that durability boundary is documented
    in the workspace format.
    """

    safe_company_id = _safe_component(company_id)
    if not artifacts:
        raise VersioningError("artifact bundle must not be empty")
    normalized: list[tuple[str, str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for kind, artifact_id, payload in artifacts:
        if kind not in COMPANY_ARTIFACT_KINDS:
            raise VersioningError(f"unsupported artifact kind: {kind}")
        safe_artifact_id = _safe_component(artifact_id)
        if not isinstance(payload, dict):
            raise VersioningError("artifact bundle payloads must be JSON objects")
        identity = (kind, safe_artifact_id)
        if identity in seen:
            raise VersioningError(
                f"duplicate artifact in bundle: {kind}/{safe_artifact_id}"
            )
        seen.add(identity)
        normalized.append((kind, safe_artifact_id, payload))

    root = initialize_workspace(workspace)
    with _company_advisory_lock(root, safe_company_id):
        # Keep the global audit lock and its validated append descriptor open
        # across publication.  An ordinary broken audit target therefore
        # fails before the first immutable artifact becomes visible, while
        # preserving the existing company -> audit lock order.
        with _open_audit_append_target(root) as (audit_descriptor, audit_path):
            paths: list[Path] = []
            # Establish and re-resolve every directory before publishing any
            # file. A regular file or symlink in one later path therefore
            # cannot leave an earlier member behind as an avoidable
            # half-bundle.
            for kind, artifact_id, _ in normalized:
                directory = company_artifact_directory(root, safe_company_id, kind)
                if directory.exists() and not directory.is_dir():
                    raise VersioningError(
                        f"artifact directory path is not a directory: {directory}"
                    )
                directory.mkdir(parents=True, exist_ok=True)
                company_artifact_directory(root, safe_company_id, kind)
                path = resolve_workspace_path(
                    root,
                    "companies",
                    safe_company_id,
                    kind,
                    f"{artifact_id}.json",
                )
                paths.append(path)
            for path, (_, _, payload) in zip(paths, normalized):
                _assert_write_once_json(path, payload)
            for path, (_, _, payload) in zip(paths, normalized):
                _write_once_json(path, payload)
            _write_audit_event(
                audit_descriptor,
                audit_path,
                {
                    "event": "artifact_bundle_saved",
                    "company_id": safe_company_id,
                    "artifacts": [
                        {
                            "kind": kind,
                            "artifact_id": artifact_id,
                            "record_sha256": object_sha256(payload),
                        }
                        for kind, artifact_id, payload in normalized
                    ],
                },
            )
            return tuple(paths)


def read_company_artifact(
    workspace: str | Path,
    company_id: str,
    kind: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    if kind not in COMPANY_ARTIFACT_KINDS:
        raise VersioningError(f"unsupported artifact kind: {kind}")
    root = Path(workspace).resolve(strict=False)
    safe_company_id = _safe_component(company_id)
    path = resolve_workspace_path(
        root,
        "companies",
        safe_company_id,
        kind,
        f"{_safe_component(artifact_id)}.json",
    )
    return _read_json(path)


def company_artifact_directory(
    workspace: str | Path,
    company_id: str,
    kind: str,
) -> Path:
    """Return a contained, non-symlinked company artifact directory."""

    if kind not in COMPANY_ARTIFACT_KINDS:
        raise VersioningError(f"unsupported artifact kind: {kind}")
    root = Path(workspace).resolve(strict=False)
    return resolve_workspace_path(root, "companies", _safe_component(company_id), kind)


def apply_user_review(
    workspace: str | Path,
    thesis_diff: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Persist a review and, only when approved, promote a proposed thesis.

    The operation rejects stale diffs before any review artifact is written, so
    a failed transition cannot leave a misleading accepted review in history.
    """

    company_id = _required_id(thesis_diff, "company_id")
    root = initialize_workspace(workspace)
    with _company_advisory_lock(root, company_id):
        return _apply_user_review_locked(root, thesis_diff, review)


def _apply_user_review_locked(
    root: Path,
    thesis_diff: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Apply a review while the caller holds its company advisory lock."""

    company_id = _required_id(thesis_diff, "company_id")
    diff_id = _required_id(thesis_diff, "thesis_diff_id")
    review_id = _required_id(review, "user_review_id")
    if review.get("thesis_diff_id") != diff_id:
        raise VersioningError("review.thesis_diff_id must reference the reviewed Thesis Diff")
    if review.get("company_id") != company_id:
        raise VersioningError("review.company_id must match the Thesis Diff company")

    decision = str(review.get("decision") or "")
    if decision not in REVIEW_DECISIONS:
        raise VersioningError(f"unsupported review decision: {decision!r}")
    base_version_id = _required_id(thesis_diff, "base_version_id")
    base_thesis_id = _required_id(thesis_diff, "base_thesis_id")
    if review.get("base_version_id") != base_version_id:
        raise VersionConflictError("review.base_version_id must match the reviewed Diff base")
    if review.get("base_thesis_id") != base_thesis_id:
        raise VersionConflictError("review.base_thesis_id must match the reviewed thesis")
    current = read_current_thesis(root, company_id)
    current_version = {} if current is None else current.get("version")
    current_id = current_version.get("version_id") if isinstance(current_version, dict) else None
    if current is None or current_id != base_version_id:
        raise VersionConflictError(
            f"diff base {base_version_id!r} is stale; current version is {current_id!r}"
        )
    if current.get("thesis_id") != base_thesis_id:
        raise VersionConflictError("diff base_thesis_id does not match the current thesis")

    promoted: dict[str, Any] | None = None
    if decision == "accept":
        patch = thesis_diff.get("proposed_patch")
        if not isinstance(patch, dict) or not isinstance(patch.get("proposed_thesis"), dict):
            raise VersioningError("accepted diff must contain proposed_patch.proposed_thesis")
        promoted = deepcopy(patch["proposed_thesis"])
        proposed_version = promoted.get("version")
        if not isinstance(proposed_version, dict) or proposed_version.get("user_confirmed") is not False:
            raise VersioningError("accepted AI proposal must be pending with user_confirmed=false")
    elif decision == "accept_with_edits":
        edited = review.get("reviewed_thesis")
        if not isinstance(edited, dict):
            raise VersioningError("accept_with_edits requires review.reviewed_thesis")
        promoted = deepcopy(edited)

    if promoted is not None:
        promoted_company = promoted.get("company")
        promoted_version = promoted.get("version")
        if not isinstance(promoted_company, dict) or promoted_company.get("company_id") != company_id:
            raise VersioningError("promoted thesis company_id must match the reviewed diff")
        if promoted.get("thesis_id") != base_thesis_id:
            raise VersioningError("promoted thesis_id must preserve the reviewed thesis identity")
        if not isinstance(promoted_version, dict) or promoted_version.get("supersedes") != base_version_id:
            raise VersionConflictError("promoted thesis must supersede the reviewed base version")
        if decision == "accept_with_edits" and promoted_version.get("user_confirmed") is not True:
            raise VersioningError("user-edited accepted thesis must have user_confirmed=true")
        promoted_version["user_confirmed"] = True
        if decision == "accept":
            promoted_version["updated_at"] = str(review.get("reviewed_at") or utc_now())

    tasks: list[tuple[str, dict[str, Any]]] = []
    task_ids: set[str] = set()
    raw_tasks = review.get("research_tasks")
    if decision == "create_research_task":
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise VersioningError("create_research_task requires non-empty review.research_tasks")
        for task in raw_tasks:
            if not isinstance(task, dict):
                raise VersioningError("each research task must be an object")
            task_id = _required_id(task, "research_task_id")
            if task_id in task_ids:
                raise VersioningError(f"duplicate research_task_id: {task_id}")
            task_ids.add(task_id)
            tasks.append((task_id, task))
    elif raw_tasks is not None:
        raise VersioningError("research_tasks are only allowed for create_research_task")
    if decision != "accept_with_edits" and review.get("reviewed_thesis") is not None:
        raise VersioningError("reviewed_thesis is only allowed for accept_with_edits")

    patch = thesis_diff.get("proposed_patch")
    if not isinstance(patch, dict):
        raise VersioningError("Thesis Diff requires proposed_patch")
    if patch.get("base_version_id") != base_version_id:
        raise VersionConflictError("proposed_patch.base_version_id must match the Thesis Diff base")
    if patch.get("base_thesis_id") != base_thesis_id:
        raise VersionConflictError("proposed_patch.base_thesis_id must match the Thesis Diff base")
    if patch.get("patch_status") != "pending_user_review":
        raise VersioningError("proposed_patch must be pending_user_review")
    patch_thesis = patch.get("proposed_thesis")
    if not isinstance(patch_thesis, dict):
        raise VersioningError("proposed_patch must contain a complete proposed_thesis")
    patch_version = patch_thesis.get("version")
    if not isinstance(patch_version, dict) or patch_version.get("user_confirmed") is not False:
        raise VersioningError("proposed_patch.proposed_thesis must have user_confirmed=false")

    # Complete semantic and immutable-path preflight before writing any of the
    # review artifacts. This prevents ordinary validation failures from leaving
    # a half-applied review trail.
    required_directories = {"diffs", "reviews"}
    if tasks:
        required_directories.add("research_tasks")
    for kind in sorted(required_directories):
        _prepare_workspace_directory(root, "companies", company_id, kind)
    diff_path = resolve_workspace_path(
        root, "companies", company_id, "diffs", f"{_safe_component(diff_id)}.json"
    )
    review_path = resolve_workspace_path(
        root, "companies", company_id, "reviews", f"{_safe_component(review_id)}.json"
    )
    _assert_write_once_json(diff_path, thesis_diff)
    _assert_write_once_json(review_path, review)
    for task_id, task in tasks:
        task_path = resolve_workspace_path(
            root,
            "companies",
            company_id,
            "research_tasks",
            f"{_safe_component(task_id)}.json",
        )
        _assert_write_once_json(task_path, task)
    if promoted is not None:
        _preflight_thesis_commit(root, promoted)

    # Keep one validated audit descriptor open across the complete review
    # publication. A deterministic audit-open failure therefore occurs before
    # any immutable review record or promoted version becomes visible.
    with _open_audit_append_target(root) as audit_target:
        _save_company_artifact_locked(
            root,
            company_id,
            "diffs",
            diff_id,
            thesis_diff,
            audit_target=audit_target,
        )
        _save_company_artifact_locked(
            root,
            company_id,
            "reviews",
            review_id,
            review,
            audit_target=audit_target,
        )
        for task_id, task in tasks:
            _save_company_artifact_locked(
                root,
                company_id,
                "research_tasks",
                task_id,
                task,
                audit_target=audit_target,
            )
        if promoted is not None:
            _commit_thesis_version_locked(
                root,
                promoted,
                audit_target=audit_target,
            )

        _write_audit_event(
            *audit_target,
            {
                "event": "thesis_diff_reviewed",
                "company_id": company_id,
                "diff_id": diff_id,
                "review_id": review_id,
                "decision": decision,
                "promoted_version_id": (
                    None
                    if promoted is None
                    else promoted["version"].get("version_id")
                ),
            },
        )
    return {
        "company_id": company_id,
        "diff_id": diff_id,
        "review_id": review_id,
        "decision": decision,
        "promoted_version_id": None if promoted is None else promoted["version"].get("version_id"),
    }


def object_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_once_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _pretty_bytes(payload)
    if os.path.lexists(path):
        if path.is_symlink():
            raise VersioningError(f"refusing symbolic-link record path: {path}")
        existing = path.read_bytes()
        if existing != encoded:
            raise ImmutableRecordError(f"refusing to overwrite immutable record: {path}")
        return
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            if path.is_symlink():
                raise VersioningError(f"refusing symbolic-link record path: {path}")
            if path.read_bytes() != encoded:
                raise ImmutableRecordError(f"refusing to overwrite immutable record: {path}")
    finally:
        temporary_path.unlink(missing_ok=True)


def _assert_write_once_json(path: Path, payload: dict[str, Any]) -> None:
    """Check whether a write-once record can be stored without mutating it."""

    if os.path.lexists(path):
        if path.is_symlink():
            raise VersioningError(f"refusing symbolic-link record path: {path}")
        if path.read_bytes() != _pretty_bytes(payload):
            raise ImmutableRecordError(f"refusing to overwrite immutable record: {path}")


def _prepare_workspace_directory(root: Path, *components: str) -> Path:
    """Create and re-resolve one contained real directory before publication."""

    directory = resolve_workspace_path(root, *components)
    if directory.exists() and not directory.is_dir():
        raise VersioningError(
            f"workspace directory path is not a directory: {directory}"
        )
    directory.mkdir(parents=True, exist_ok=True)
    return resolve_workspace_path(root, *components)


def _preflight_thesis_commit(
    root: Path, thesis: dict[str, Any]
) -> tuple[str, str, str | None, Path]:
    _required_id(thesis, "thesis_id")
    company = _required_object(thesis, "company")
    version = _required_object(thesis, "version")
    company_id = _required_id(company, "company_id")
    version_id = _required_id(version, "version_id")
    if version.get("user_confirmed") is not True:
        raise VersioningError("a formal thesis version must be explicitly user_confirmed")

    current = read_current_thesis(root, company_id)
    supersedes = version.get("supersedes") or None
    if current is None:
        if supersedes is not None:
            raise VersionConflictError("the first thesis version cannot supersede another version")
    else:
        current_version = _required_object(current, "version")
        current_id = _required_id(current_version, "version_id")
        if current.get("thesis_id") != thesis.get("thesis_id"):
            raise VersionConflictError("new version must preserve the current thesis_id")
        if supersedes != current_id:
            raise VersionConflictError(
                f"new thesis must supersede current version {current_id!r}, got {supersedes!r}"
            )
        if version_id == current_id:
            raise VersionConflictError("a thesis version cannot supersede itself")
        _validate_version_chronology(current_version, version)

    _prepare_workspace_directory(
        root,
        "companies",
        company_id,
        "thesis_versions",
    )
    version_path = resolve_workspace_path(
        root,
        "companies",
        company_id,
        "thesis_versions",
        f"{_safe_component(version_id)}.json",
    )
    _assert_write_once_json(version_path, thesis)
    return company_id, version_id, supersedes, version_path


def _validate_version_chronology(
    current: dict[str, Any],
    proposed: dict[str, Any],
) -> None:
    """Reject a new formal version that rewinds the confirmed belief state."""

    current_as_of = _optional_iso_date(current, "as_of_date")
    proposed_as_of = _optional_iso_date(proposed, "as_of_date")
    if (
        current_as_of is not None
        and proposed_as_of is not None
        and proposed_as_of < current_as_of
    ):
        raise VersionConflictError(
            "new thesis version.as_of_date cannot precede the current version"
        )

    current_created = _optional_iso_datetime(current, "created_at")
    current_updated = _optional_iso_datetime(current, "updated_at")
    proposed_created = _optional_iso_datetime(proposed, "created_at")
    proposed_updated = _optional_iso_datetime(proposed, "updated_at")
    current_boundary = current_updated or current_created
    if (
        current_boundary is not None
        and proposed_created is not None
        and proposed_created < current_boundary
    ):
        raise VersionConflictError(
            "new thesis version.created_at cannot precede the current version updated_at"
        )
    if (
        current_boundary is not None
        and proposed_updated is not None
        and proposed_updated < current_boundary
    ):
        raise VersionConflictError(
            "new thesis version.updated_at cannot precede the current version"
        )
    if (
        proposed_created is not None
        and proposed_updated is not None
        and proposed_updated < proposed_created
    ):
        raise VersionConflictError(
            "new thesis version.updated_at cannot precede its created_at"
        )


def _optional_iso_date(payload: dict[str, Any], key: str) -> date | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise VersioningError(f"version.{key} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise VersioningError(f"version.{key} must be a valid ISO date") from exc


def _optional_iso_datetime(
    payload: dict[str, Any],
    key: str,
) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise VersioningError(f"version.{key} must be an ISO date-time string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VersioningError(f"version.{key} must be a valid ISO date-time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise VersioningError(f"version.{key} must include a timezone offset")
    return parsed


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise VersioningError(f"refusing symbolic-link record path: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_pretty_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


@contextmanager
def _open_audit_append_target(root: Path):
    """Lock, open, and validate the append-only audit target."""

    # Company locks do not serialize different companies.  A dedicated audit
    # lock keeps each JSONL event intact across processes and platforms.
    with _named_advisory_lock(root, "audit.events.lock", "audit log"):
        path = resolve_workspace_path(root, "audit", "events.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path = resolve_workspace_path(root, "audit", "events.jsonl")
        flags = (
            os.O_APPEND
            | os.O_CREAT
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise VersioningError(f"audit log must be a regular file: {path}")
            if path.is_symlink():
                raise VersioningError(f"refusing symbolic-link audit log: {path}")
            yield descriptor, path
        finally:
            os.close(descriptor)


def _write_audit_event(descriptor: int, path: Path, event: dict[str, Any]) -> None:
    payload = {"recorded_at": utc_now(), **event}
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    offset = 0
    while offset < len(encoded):
        written = os.write(descriptor, encoded[offset:])
        if written <= 0:
            raise VersioningError(f"short audit-log write: {path}")
        offset += written
    os.fsync(descriptor)


def _safe_component(value: str) -> str:
    if not isinstance(value, str):
        raise VersioningError(f"unsafe identifier: {value!r}")
    text = value
    basename = text.split(".", 1)[0].upper()
    if (
        not 1 <= len(text) <= 128
        or _PORTABLE_IDENTIFIER_RE.fullmatch(text) is None
        or basename in _WINDOWS_RESERVED_NAMES
    ):
        raise VersioningError(f"unsafe identifier: {value!r}")
    return text


@contextmanager
def reserve_model_run(
    workspace: str | Path,
    company_id: str,
    model_run_id: str,
):
    """Serialize one model-run identity across its adapter and publication.

    The lock is narrower than the company mutation lock, so unrelated model
    runs can execute concurrently.  A duplicate waits for the first command to
    publish or fail and can then re-check immutable state without invoking the
    adapter twice.
    """

    safe_company_id = _safe_component(company_id)
    safe_run_id = _safe_component(model_run_id)
    root = initialize_workspace(workspace)
    identity = f"{safe_company_id}\0{safe_run_id}".encode("utf-8")
    lock_token = hashlib.sha256(identity).hexdigest()
    with _named_advisory_lock(
        root,
        f"model-run.{lock_token}.lock",
        f"model run {safe_company_id!r}/{safe_run_id!r}",
    ):
        yield


@contextmanager
def _company_advisory_lock(root: Path, company_id: str):
    """Serialize mutations for one company across processes.

    The persistent lock file is deliberately separate from immutable records.
    Advisory locks are released by the operating system if a process exits.
    """

    safe_company_id = _safe_component(company_id)
    lock_token = hashlib.sha256(safe_company_id.encode("utf-8")).hexdigest()
    with _named_advisory_lock(
        root,
        f"company.{lock_token}.lock",
        f"company {safe_company_id!r}",
    ):
        yield


@contextmanager
def _named_advisory_lock(root: Path, filename: str, label: str):
    """Acquire one contained persistent advisory lock file."""

    safe_filename = _safe_component(filename)
    lock_directory = resolve_workspace_path(root, "locks")
    lock_directory.mkdir(parents=True, exist_ok=True)
    resolve_workspace_path(root, "locks")
    lock_path = resolve_workspace_path(root, "locks", safe_filename)

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise VersioningError(
            f"unable to open {label} lock safely: {lock_path}"
        ) from exc

    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise VersioningError(f"{label} lock must be a regular file: {lock_path}")
        if lock_path.is_symlink():
            raise VersioningError(f"refusing symbolic-link {label} lock: {lock_path}")
        _acquire_advisory_lock(descriptor, lock_path)
        try:
            yield
        finally:
            _release_advisory_lock(descriptor, lock_path)
    finally:
        os.close(descriptor)


def _acquire_advisory_lock(descriptor: int, lock_path: Path) -> None:
    if os.name == "posix":
        if fcntl is None:  # pragma: no cover - import is guaranteed on POSIX CPython
            raise VersioningError("POSIX advisory locking support is unavailable")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            raise VersioningError(f"unable to acquire company lock: {lock_path}") from exc
        return

    if os.name == "nt":  # pragma: no cover - exercised on Windows
        if msvcrt is None:
            raise VersioningError("Windows advisory locking support is unavailable")
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            while True:
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    return
                except OSError:
                    time.sleep(0.05)
        except OSError as exc:
            raise VersioningError(f"unable to acquire company lock: {lock_path}") from exc

    raise VersioningError(f"advisory locking is unsupported on platform {os.name!r}")


def _release_advisory_lock(descriptor: int, lock_path: Path) -> None:
    if os.name == "posix":
        if fcntl is None:  # pragma: no cover - import is guaranteed on POSIX CPython
            raise VersioningError("POSIX advisory locking support is unavailable")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as exc:
            raise VersioningError(f"unable to release company lock: {lock_path}") from exc
        return

    if os.name == "nt":  # pragma: no cover - exercised on Windows
        if msvcrt is None:
            raise VersioningError("Windows advisory locking support is unavailable")
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError as exc:
            raise VersioningError(f"unable to release company lock: {lock_path}") from exc
        return

    raise VersioningError(f"advisory locking is unsupported on platform {os.name!r}")


def resolve_workspace_path(workspace: str | Path, *components: str) -> Path:
    """Build a path that remains inside the workspace and has no symlink child."""

    root = Path(workspace).resolve(strict=False)
    candidate = root
    for raw_component in components:
        component = Path(raw_component)
        if component.is_absolute() or component.drive or ".." in component.parts:
            raise VersioningError(f"unsafe workspace path component: {raw_component!r}")
        candidate = candidate / component

    root_absolute = Path(os.path.abspath(root))
    candidate_absolute = Path(os.path.abspath(candidate))
    try:
        relative = candidate_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise VersioningError(f"path escapes workspace: {candidate_absolute}") from exc

    cursor = root_absolute
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise VersioningError(f"symbolic-link path is not allowed inside workspace: {cursor}")

    resolved_candidate = candidate_absolute.resolve(strict=False)
    try:
        resolved_candidate.relative_to(root_absolute.resolve(strict=False))
    except ValueError as exc:
        raise VersioningError(f"path escapes workspace: {candidate_absolute}") from exc
    return candidate_absolute


def _required_id(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VersioningError(f"{key} must be a non-empty string")
    return _safe_component(value)


def _required_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise VersioningError(f"{key} must be an object")
    return value


def _read_json(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        raise VersioningError(f"refusing symbolic-link record path: {path}")
    if not path.exists():
        return None
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise VersioningError(f"expected JSON object: {path}")
    return parsed


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
