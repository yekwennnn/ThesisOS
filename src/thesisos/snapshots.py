"""Content-addressed, write-once source snapshots for ThesisOS workspaces.

The snapshot store deliberately treats every supported source format as opaque
bytes.  PDF parsing, Markdown rendering, text decoding, and OCR belong to later
derived-artifact stages; none of them may change the identity of the source the
user actually supplied.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping

from .versioning import initialize_workspace, resolve_workspace_path


CHUNK_SIZE = 1024 * 1024
SHA256_URI_PREFIX = "thesisos://sha256/"


class SnapshotError(ValueError):
    """Base class for a rejected or unsafe snapshot ingestion."""


class SnapshotMetadataMismatchError(SnapshotError):
    """The declared snapshot identity does not match the supplied bytes."""


class SnapshotCollisionError(SnapshotError):
    """An object path already exists but does not contain the same bytes."""


class SnapshotSourceChangedError(SnapshotError):
    """The source changed between verification and the immutable copy."""


@dataclass(frozen=True)
class SnapshotIngestResult:
    """The canonical identity and local path of an ingested object."""

    object_path: Path
    sha256: str
    byte_size: int
    created: bool


def storage_uri_for_sha256(digest: str) -> str:
    """Return the canonical, location-independent URI for a SHA-256 object."""

    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise SnapshotError("snapshot SHA-256 must be 64 lowercase hexadecimal characters")
    return f"{SHA256_URI_PREFIX}{digest}"


def object_path_for_sha256(workspace: str | Path, digest: str) -> Path:
    """Resolve a digest to its canonical path inside ``workspace``."""

    storage_uri_for_sha256(digest)
    return resolve_workspace_path(workspace, "objects", "sha256", digest[:2], digest)


def calculate_file_identity(source_file: str | Path) -> tuple[str, int]:
    """Stream a file once and return its SHA-256 digest and byte size."""

    source = _require_regular_file(source_file)
    with source.open("rb") as handle:
        return _stream_identity(handle)


def ingest_snapshot(
    workspace: str | Path,
    source_file: str | Path,
    source_document: Mapping[str, Any],
) -> SnapshotIngestResult:
    """Verify and atomically ingest the bytes declared by a SourceDocument.

    The caller is expected to run canonical JSON Schema and domain validation
    first.  This function independently verifies the three snapshot identity
    fields before creating the workspace or any object-store path.
    """

    source = _require_regular_file(source_file)
    declared_digest, declared_size, declared_uri = _declared_snapshot(source_document)

    actual_digest, actual_size = calculate_file_identity(source)
    expected_uri = storage_uri_for_sha256(actual_digest)
    mismatches: list[str] = []
    if declared_digest != actual_digest:
        mismatches.append(
            f"snapshot.sha256 declares {declared_digest!r}, actual SHA-256 is {actual_digest!r}"
        )
    if declared_size != actual_size:
        mismatches.append(
            f"snapshot.byte_size declares {declared_size!r}, actual byte size is {actual_size}"
        )
    if declared_uri != expected_uri:
        mismatches.append(
            f"snapshot.storage_uri must be {expected_uri!r}, got {declared_uri!r}"
        )
    if mismatches:
        raise SnapshotMetadataMismatchError("; ".join(mismatches))

    root = initialize_workspace(workspace)
    target = object_path_for_sha256(root, actual_digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = object_path_for_sha256(root, actual_digest)
    temporary_path: Path | None = None
    try:
        descriptor, raw_temporary_path = tempfile.mkstemp(
            prefix=f".{actual_digest}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary_path = Path(raw_temporary_path)
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_handle:
            copied_digest, copied_size = _copy_and_hash(input_handle, output)
            output.flush()
            os.fsync(output.fileno())
        if copied_digest != actual_digest or copied_size != actual_size:
            raise SnapshotSourceChangedError(
                "source file changed while it was being ingested; retry with stable source bytes"
            )

        created = _link_exclusively_or_verify(temporary_path, target)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    return SnapshotIngestResult(
        object_path=target,
        sha256=actual_digest,
        byte_size=actual_size,
        created=created,
    )


def verify_stored_snapshot(
    workspace: str | Path,
    source_document: Mapping[str, Any],
) -> SnapshotIngestResult:
    """Verify that a declared object exists and still matches its identity."""

    digest, size, storage_uri = _declared_snapshot(source_document)
    expected_uri = storage_uri_for_sha256(digest)
    if storage_uri != expected_uri:
        raise SnapshotMetadataMismatchError(
            f"snapshot.storage_uri must be {expected_uri!r}, got {storage_uri!r}"
        )
    target = object_path_for_sha256(workspace, digest)
    if not target.is_file():
        raise SnapshotError(f"snapshot object is missing: {target}")
    actual_digest, actual_size = calculate_file_identity(target)
    if actual_digest != digest or actual_size != size:
        raise SnapshotCollisionError(
            f"stored snapshot {target} does not match declared SHA-256 and byte size"
        )
    return SnapshotIngestResult(target, digest, size, created=False)


def _declared_snapshot(source_document: Mapping[str, Any]) -> tuple[str, int, str]:
    snapshot = source_document.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise SnapshotError("SourceDocument.snapshot must be an object")
    digest = snapshot.get("sha256")
    size = snapshot.get("byte_size")
    storage_uri = snapshot.get("storage_uri")
    if not isinstance(digest, str):
        raise SnapshotError("SourceDocument.snapshot.sha256 must be a string")
    if not isinstance(size, int) or isinstance(size, bool):
        raise SnapshotError("SourceDocument.snapshot.byte_size must be an integer")
    if not isinstance(storage_uri, str):
        raise SnapshotError("SourceDocument.snapshot.storage_uri must be a string")
    return digest, size, storage_uri


def _require_regular_file(source_file: str | Path) -> Path:
    source = Path(source_file)
    if not source.is_file():
        raise SnapshotError(f"source file is not a readable regular file: {source}")
    return source


def _stream_identity(handle: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_size = 0
    while True:
        chunk = handle.read(CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
        byte_size += len(chunk)
    return digest.hexdigest(), byte_size


def _copy_and_hash(input_handle: BinaryIO, output: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_size = 0
    while True:
        chunk = input_handle.read(CHUNK_SIZE)
        if not chunk:
            break
        output.write(chunk)
        digest.update(chunk)
        byte_size += len(chunk)
    return digest.hexdigest(), byte_size


def _link_exclusively_or_verify(temporary_path: Path, target: Path) -> bool:
    try:
        os.link(temporary_path, target)
    except FileExistsError:
        if not _files_equal(temporary_path, target):
            raise SnapshotCollisionError(
                f"object path {target} already exists with different bytes"
            )
        return False
    return True


def _files_equal(left: Path, right: Path) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open("rb") as left_handle, right.open("rb") as right_handle:
            while True:
                left_chunk = left_handle.read(CHUNK_SIZE)
                right_chunk = right_handle.read(CHUNK_SIZE)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError as exc:
        raise SnapshotCollisionError(
            f"cannot verify existing object path {right}: {exc}"
        ) from exc
