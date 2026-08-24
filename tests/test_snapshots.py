from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from thesisos.snapshots import (
    SnapshotCollisionError,
    SnapshotMetadataMismatchError,
    ingest_snapshot,
    object_path_for_sha256,
    storage_uri_for_sha256,
    verify_stored_snapshot,
)
from thesisos.versioning import VersioningError, initialize_workspace


def document_for(content: bytes, *, document_id: str = "doc-1") -> dict[str, object]:
    digest = hashlib.sha256(content).hexdigest()
    return {
        "source_document_id": document_id,
        "company_id": "company-1",
        "snapshot": {
            "sha256": digest,
            "storage_uri": storage_uri_for_sha256(digest),
            "byte_size": len(content),
        },
    }


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_source(self, name: str, content: bytes) -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_pdf_markdown_and_plain_text_are_preserved_as_opaque_bytes(self) -> None:
        examples = (
            ("report.pdf", b"%PDF-1.7\n\x00\xffopaque-pdf-bytes\n%%EOF", "pdf"),
            ("notes.md", "# 原始笔记\n\n- 不改写换行\n".encode(), "markdown"),
            ("transcript.txt", b"line one\r\nline two\r\n", "plain_text"),
        )
        for index, (name, content, media_type) in enumerate(examples, start=1):
            with self.subTest(media_type=media_type):
                source = self.write_source(name, content)
                metadata = document_for(content, document_id=f"doc-{index}")
                result = ingest_snapshot(self.workspace, source, metadata)
                self.assertTrue(result.created)
                self.assertEqual(result.object_path.read_bytes(), content)
                self.assertEqual(result.byte_size, len(content))
                self.assertEqual(result.sha256, hashlib.sha256(content).hexdigest())

    def test_all_declared_identity_fields_must_match_before_any_workspace_write(self) -> None:
        content = b"immutable source bytes"
        source = self.write_source("source.txt", content)
        correct = document_for(content)
        mutations = {
            "sha256": "0" * 64,
            "byte_size": len(content) + 1,
            "storage_uri": "thesisos://sha256/" + "f" * 64,
        }
        for index, (field, bad_value) in enumerate(mutations.items(), start=1):
            with self.subTest(field=field):
                workspace = self.root / f"workspace-{index}"
                metadata = document_for(content)
                metadata["snapshot"][field] = bad_value
                with self.assertRaises(SnapshotMetadataMismatchError):
                    ingest_snapshot(workspace, source, metadata)
                self.assertFalse(workspace.exists())

        # The unmodified fixture remains valid and guards against a broken test.
        self.assertEqual(correct["snapshot"]["byte_size"], len(content))

    def test_reingesting_identical_bytes_is_idempotent(self) -> None:
        content = b"same bytes, same object"
        source = self.write_source("source.md", content)
        metadata = document_for(content)
        first = ingest_snapshot(self.workspace, source, metadata)
        original_stat = first.object_path.stat()
        second = ingest_snapshot(self.workspace, source, metadata)
        second_stat = second.object_path.stat()

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.object_path, second.object_path)
        self.assertEqual(original_stat.st_ino, second_stat.st_ino)
        self.assertEqual(first.object_path.read_bytes(), content)
        self.assertEqual(list(first.object_path.parent.glob("*.tmp")), [])

    def test_existing_hash_path_must_be_byte_for_byte_identical(self) -> None:
        content = b"declared source"
        source = self.write_source("source.txt", content)
        metadata = document_for(content)
        digest = metadata["snapshot"]["sha256"]
        target = object_path_for_sha256(self.workspace, digest)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"tampered object")

        with self.assertRaises(SnapshotCollisionError):
            ingest_snapshot(self.workspace, source, metadata)

        self.assertEqual(target.read_bytes(), b"tampered object")
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_symlinked_object_store_cannot_escape_workspace(self) -> None:
        content = b"must stay in workspace"
        source = self.write_source("source.txt", content)
        metadata = document_for(content)
        outside = self.root / "outside"
        outside.mkdir()
        initialize_workspace(self.workspace)
        try:
            (self.workspace / "objects").symlink_to(outside, target_is_directory=True)
        except OSError as exc:  # Windows may require Developer Mode/admin rights.
            self.skipTest(f"symbolic links unavailable: {exc}")

        with self.assertRaisesRegex(VersioningError, "symbolic-link"):
            ingest_snapshot(self.workspace, source, metadata)

        self.assertEqual(list(outside.rglob("*")), [])

    def test_verify_detects_object_tampering(self) -> None:
        content = b"original"
        source = self.write_source("source.txt", content)
        metadata = document_for(content)
        result = ingest_snapshot(self.workspace, source, metadata)
        result.object_path.write_bytes(b"tampered")

        with self.assertRaises(SnapshotCollisionError):
            verify_stored_snapshot(self.workspace, metadata)


if __name__ == "__main__":
    unittest.main()
