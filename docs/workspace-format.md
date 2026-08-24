# Workspace Format

ThesisOS V0 stores auditable artifacts as local JSON files. A workspace is
organized by company:

```text
workspace/
├── manifest.json
├── audit/events.jsonl
├── objects/sha256/{first_two_hex}/{sha256}
└── companies/{company_id}/
    ├── current_thesis.json
    ├── thesis_versions/{version_id}.json
    ├── documents/{document_id}.json
    ├── evidence/{evidence_id}.json
    ├── diffs/{diff_id}.json
    ├── reviews/{review_id}.json
    ├── research_tasks/{task_id}.json
    └── model_runs/{run_id}.json
```

## Source Snapshot Store

Use the high-level import command when the source bytes are available locally:

```console
./.venv/bin/python -m thesisos --workspace ./thesisos_workspace \
  ingest-document METADATA_JSON SOURCE_FILE
```

`METADATA_JSON` must be a valid `SourceDocument`. Before ThesisOS writes
anything, it streams `SOURCE_FILE` and requires all three declared snapshot
fields to match the source exactly:

- `snapshot.sha256`: lowercase SHA-256 of the original bytes;
- `snapshot.byte_size`: exact original byte length;
- `snapshot.storage_uri`: `thesisos://sha256/<sha256>`.

After verification, the source is copied without decoding or transformation to
`objects/sha256/<first-two-hex>/<sha256>`. The completed temporary file is
linked into place with exclusive-create semantics. If that hash path already
exists, import succeeds only when its contents are byte-for-byte identical.
Only then is the immutable SourceDocument saved under the company directory.
Repeated import of the same metadata and bytes is idempotent.

PDF, Markdown, and plain-text inputs all follow this same opaque-byte path.
Ingestion performs no OCR, Unicode normalization, newline conversion, PDF
rewriting, or Markdown rendering. `extract-evidence` derives a read-only text
view only after re-verifying those opaque bytes. The derived view has stable
1-based pages, lines, and paragraphs but is not a second source of truth; every
Evidence item still cites the immutable source identity.

`save-document` remains a low-level metadata command for snapshots managed
outside this workspace. For locally supplied files, `ingest-document` is the
safe default because it proves that the cited snapshot actually exists.
If `save-document` receives a `thesisos://sha256/...` URI, it now requires the
matching object to already exist and pass hash and size verification; otherwise
the command fails without saving metadata and directs the workflow back to
`ingest-document`. External URI schemes remain metadata-only.

### Downstream integrity gates

Content verification is not limited to ingestion. Whenever `save-evidence`
loads a cited SourceDocument, and whenever `review` loads SourceDocuments for a
Thesis Diff, a `thesisos://sha256/...` snapshot is streamed again from the
object store. The operation stops with exit code 2 if the object is missing or
if its current size or SHA-256 differs. This check happens before Evidence,
Review, Diff, research-task, or promoted ThesisVersion records are written.
Thus bytes that were deleted or modified after ingestion cannot silently enter
a later accepted thesis.

Other URI schemes remain metadata-only compatibility modes. For example,
`https://...` and `file://...` snapshots may describe externally managed
archives, but ThesisOS does not fetch or reopen those bytes during
`save-evidence` or `review`; their local byte integrity therefore cannot be
verified by the workspace. Use `ingest-document` and the
`thesisos://sha256/...` URI whenever replayable local evidence is required.

### Derived text and model runs

Managed plain text and Markdown must decode as UTF-8. Managed PDFs are parsed
with `pypdf`; a declared `page_count` must equal the physical page count. Exact
quotes and table values are matched inside the resolved locator after only
Unicode NFKC and whitespace normalization. Numeric and ASCII token boundaries
remain significant. Automatic `table_value` verification requires both a row
and column anchor, a unique table caption, and matching structured header/data
rows. The anchors map to one cell and the normalized quote must equal the
complete normalized cell, including sign and unit; prose after a table and the
rest of the page are never searched. A plain/PDF section without an executable end boundary is
rejected in favor of an explicit line range. `faithful_paraphrase` never
receives an automatic pass and requires explicit semantic review.

Provider-neutral model executions are written once under
`companies/{company_id}/model_runs/{run_id}.json`. A run records the canonical
prompt hash, normalized input/output hashes, model identifier, object
references, output, and timestamps; it does not record adapter argv. Evidence
extraction output remains `unreviewed` and does not populate `evidence/` until
the user explicitly saves a reviewed object. See
[`model-adapter-protocol.md`](model-adapter-protocol.md).

One advisory reservation keyed by company and model-run ID spans duplicate
checking, adapter execution, output admission, and publication. Concurrent or
sequential reuse therefore cannot silently pay for the same caller-owned ID
twice. A successful `generate-diff` preflights its model-run and Diff paths
together and publishes them under one company mutation lock.

### Concurrency and crash boundary

Every company mutation is serialized by a contained cross-process advisory
lock (`flock` on POSIX and `msvcrt` on Windows). Thesis commits and reviews
re-read the current pointer after acquiring that lock, so two children cannot
both advance the same base version. Audit JSONL appends use a separate global
lock and one append-mode byte write loop, preventing events from different
companies from interleaving. Thesis commits, single-artifact saves, reviews,
and artifact bundles acquire that audit lock and open/validate the append target
before publishing their first record, so a broken audit path cannot leave an
unlogged formal version or half review. Review parent directories and every
immutable destination are preflighted before publication. Immutable JSON
files are fsynced and published by exclusive hard link; current pointers are
replaced atomically.

This file-backed V0 is not a database transaction across sudden power loss.
The model-run/Diff bundle eliminates normal validation, path, and concurrent
conflicts before its first member is written, but an OS or power failure between
two immutable file publications can still leave a recoverable orphan. The
records retain stable IDs and hashes for inspection; production deployment
should add a journal/SQLite transaction and startup recovery before claiming
crash-atomic multi-file commits.

## Immutability Rules

- A formal thesis version must have `user_confirmed: true`.
- The first version has no `supersedes`; every later version must supersede the
  current version exactly.
- Existing version, diff, review, evidence, and document files are immutable.
  Writing identical content is idempotent; changing content under the same ID
  is rejected.
- `current_thesis.json` is only a pointer containing the current version ID and
  the version's SHA-256 digest. The version itself is never rewritten.
- An accepted review is rejected when its Diff was generated from a version
  that is no longer current.
- `reject`, `defer_insufficient`, and `create_research_task` never change the
  current thesis.
- AI output remains an unconfirmed proposal inside a Thesis Diff. Only
  `accept` or `accept_with_edits` can promote a user-confirmed version.
- Snapshot objects are content-addressed and write-once. A digest path can
  never be replaced with different bytes, even when document metadata has not
  yet been saved.
- Filesystem-backed identifiers use the portable ASCII set of letters, digits,
  `.`, `_`, and `-`; drive-qualified names, trailing dots, and Windows device
  names are rejected. Workspace storage also rejects symbolic-link descendants
  so company and object paths cannot resolve outside the configured workspace.

These rules keep the historical question answerable: what did the investor
believe at a given time, what evidence was then available, and which explicit
review created the next version?
