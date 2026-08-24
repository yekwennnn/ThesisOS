"""Local, machine-readable command line interface for ThesisOS V0."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence, TextIO

from .adversarial import evaluate_adversarial_suites
from .evaluation import evaluate_case_file
from .model_runtime import (
    DEFAULT_MAX_STDOUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    ModelRunResult,
    ModelRuntimeError,
    run_model_adapter,
)
from .schema_validation import (
    SCHEMA_FILENAMES,
    SchemaCatalog,
    SchemaInstanceError,
    SchemaValidationRuntimeError,
    canonical_kind,
    load_json_object,
    schema_issue_payload,
)
from .versioning import (
    VersioningError,
    apply_user_review,
    company_artifact_directory,
    commit_thesis_version,
    initialize_workspace,
    object_sha256,
    read_company_artifact,
    read_current_thesis,
    reserve_model_run,
    resolve_workspace_path,
    save_company_artifact,
    save_company_artifact_bundle,
)


EXIT_OK = 0
EXIT_INPUT_ERROR = 2
EXIT_INTERNAL_ERROR = 1
DEFAULT_WORKSPACE = "thesisos_workspace"


class CliInputError(ValueError):
    """A concise user-correctable CLI error."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliInputError(message)


def _add_model_adapter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--adapter", required=True, metavar="EXECUTABLE")
    parser.add_argument(
        "--adapter-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="adapter argv item; repeat to preserve argument boundaries",
    )
    parser.add_argument("--model-id", required=True, metavar="IDENTIFIER")
    parser.add_argument("--run-id", required=True, metavar="ID")
    parser.add_argument("--prompt-dir", metavar="DIRECTORY")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
    )
    parser.add_argument(
        "--max-stdout-bytes",
        type=int,
        default=DEFAULT_MAX_STDOUT_BYTES,
        metavar="BYTES",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="thesisos",
        description="Auditable local ThesisDiff V0 workflow.",
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("THESISOS_WORKSPACE", DEFAULT_WORKSPACE),
        help=f"workspace directory (default: {DEFAULT_WORKSPACE})",
    )
    parser.add_argument(
        "--schema-dir",
        default=None,
        help="canonical schema directory (normally auto-discovered)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="initialize an append-only local workspace")
    init_parser.add_argument(
        "workspace_path",
        nargs="?",
        help="optional workspace path, overriding --workspace",
    )

    validate_parser = commands.add_parser("validate", help="validate one canonical JSON object")
    validate_parser.add_argument("kind", choices=tuple(SCHEMA_FILENAMES))
    validate_parser.add_argument("file")
    validate_parser.add_argument(
        "--document",
        action="append",
        default=[],
        metavar="FILE",
        help="SourceDocument context; repeat as needed",
    )
    validate_parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        metavar="FILE",
        help="Evidence context; repeat as needed",
    )
    validate_parser.add_argument("--base-thesis", metavar="FILE")
    validate_parser.add_argument("--diff", metavar="FILE")
    validate_parser.add_argument(
        "--analysis-cutoff",
        metavar="DATETIME",
        help="optional historical cutoff for Evidence cross-object validation",
    )

    commit_parser = commands.add_parser(
        "commit-thesis",
        help="validate and commit an immutable, user-confirmed ThesisCard",
    )
    commit_parser.add_argument("file")

    document_parser = commands.add_parser(
        "save-document",
        help="validate and store SourceDocument metadata for an externally managed snapshot",
    )
    document_parser.add_argument("file")

    ingest_parser = commands.add_parser(
        "ingest-document",
        help="verify source bytes, store a content-addressed snapshot, and save its metadata",
    )
    ingest_parser.add_argument("metadata_json", metavar="METADATA_JSON")
    ingest_parser.add_argument("source_file", metavar="SOURCE_FILE")

    snapshot_parser = commands.add_parser(
        "snapshot-info",
        help="calculate the immutable SHA-256, byte size, and managed URI for a file",
    )
    snapshot_parser.add_argument("source_file", metavar="SOURCE_FILE")

    evidence_parser = commands.add_parser(
        "save-evidence",
        help="validate citations against stored documents and save Evidence",
    )
    evidence_parser.add_argument("file")
    evidence_parser.add_argument(
        "--analysis-cutoff",
        metavar="DATETIME",
        help="optional historical replay cutoff",
    )

    extraction_parser = commands.add_parser(
        "extract-evidence",
        help="parse one managed source and run a provider-neutral Evidence model adapter",
    )
    extraction_parser.add_argument("company_id", metavar="COMPANY_ID")
    extraction_parser.add_argument("source_document_id", metavar="DOCUMENT_ID")
    extraction_parser.add_argument("request_metadata", metavar="REQUEST_JSON")
    extraction_parser.add_argument(
        "--max-source-chars",
        type=int,
        default=2_000_000,
        metavar="CHARS",
    )
    _add_model_adapter_arguments(extraction_parser)

    generation_parser = commands.add_parser(
        "generate-diff",
        help="run a provider-neutral adapter against a confirmed thesis and verified evidence",
    )
    generation_parser.add_argument("company_id", metavar="COMPANY_ID")
    generation_parser.add_argument("request_metadata", metavar="REQUEST_JSON")
    generation_parser.add_argument(
        "--document",
        action="append",
        required=True,
        dest="document_ids",
        metavar="DOCUMENT_ID",
    )
    generation_parser.add_argument(
        "--evidence",
        action="append",
        required=True,
        dest="evidence_ids",
        metavar="EVIDENCE_ID",
    )
    generation_parser.add_argument(
        "--prior-evidence",
        action="append",
        default=[],
        dest="prior_evidence_ids",
        metavar="EVIDENCE_ID",
    )
    _add_model_adapter_arguments(generation_parser)

    review_parser = commands.add_parser(
        "review",
        help="validate and apply a UserReview to a ThesisDiff",
    )
    review_parser.add_argument("diff_file")
    review_parser.add_argument("review_file")

    status_parser = commands.add_parser("status", help="show workspace state without mutating it")
    status_parser.add_argument("--company", dest="company_id")

    replay_parser = commands.add_parser(
        "eval-replay",
        help="run the deterministic acceptance checks for one historical replay case",
    )
    replay_parser.add_argument("case_file", metavar="CASE_JSON")

    suite_parser = commands.add_parser(
        "eval-suite",
        help="run one or more declarative adversarial evaluation suites",
    )
    suite_parser.add_argument("suite_files", nargs="+", metavar="SUITE_JSON")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return a process exit code without a traceback."""

    try:
        args = build_parser().parse_args(argv)
        try:
            result = _dispatch(args)
        finally:
            _release_model_run_reservation(args)
    except SchemaInstanceError as exc:
        _emit(schema_issue_payload(exc), sys.stderr)
        return EXIT_INPUT_ERROR
    except (
        CliInputError,
        ModelRuntimeError,
        SchemaValidationRuntimeError,
        VersioningError,
        ValueError,
        TypeError,
    ) as exc:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": _error_code(exc),
                    "message": str(exc),
                },
            },
            sys.stderr,
        )
        return EXIT_INPUT_ERROR
    except OSError as exc:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "io_error",
                    "message": str(exc) or type(exc).__name__,
                },
            },
            sys.stderr,
        )
        return EXIT_INTERNAL_ERROR
    except Exception as exc:  # keep the CLI contract traceback-free
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": f"{type(exc).__name__}: {exc}",
                },
            },
            sys.stderr,
        )
        return EXIT_INTERNAL_ERROR
    _emit(result, sys.stdout)
    return EXIT_OK if result.get("ok", True) else EXIT_INPUT_ERROR


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    if command == "init":
        return _command_init(args)
    if command == "status":
        return _command_status(args)
    if command == "eval-replay":
        return _command_eval_replay(args)
    if command == "eval-suite":
        return _command_eval_suite(args)
    if command == "snapshot-info":
        return _command_snapshot_info(args)

    # Every mutating/import command validates against the same loaded catalog.
    catalog = SchemaCatalog(args.schema_dir)
    if command == "validate":
        return _command_validate(args, catalog)
    if command == "commit-thesis":
        return _command_commit_thesis(args, catalog)
    if command == "save-document":
        return _command_save_document(args, catalog)
    if command == "ingest-document":
        return _command_ingest_document(args, catalog)
    if command == "save-evidence":
        return _command_save_evidence(args, catalog)
    if command == "extract-evidence":
        return _command_extract_evidence(args, catalog)
    if command == "generate-diff":
        return _command_generate_diff(args, catalog)
    if command == "review":
        return _command_review(args, catalog)
    raise CliInputError(f"unsupported command: {command}")


def _command_eval_replay(args: argparse.Namespace) -> dict[str, Any]:
    case_file = Path(args.case_file).resolve()
    report = evaluate_case_file(case_file)
    return {
        "ok": report.passed,
        "command": "eval-replay",
        "case_file": str(case_file),
        "report": report.to_dict(),
    }


def _command_eval_suite(args: argparse.Namespace) -> dict[str, Any]:
    suite_files = tuple(Path(path).resolve() for path in args.suite_files)
    reports = evaluate_adversarial_suites(suite_files)
    passed_count = sum(report.passed for report in reports)
    return {
        "ok": passed_count == len(reports),
        "command": "eval-suite",
        "suite_files": [str(path) for path in suite_files],
        "passed_suites": passed_count,
        "total_suites": len(reports),
        "reports": [report.to_dict() for report in reports],
    }


def _command_init(args: argparse.Namespace) -> dict[str, Any]:
    workspace = Path(args.workspace_path or args.workspace).resolve()
    initialize_workspace(workspace)
    return {
        "ok": True,
        "command": "init",
        "workspace": str(workspace),
        "format": "thesisos-workspace",
        "format_version": 1,
    }


def _command_snapshot_info(args: argparse.Namespace) -> dict[str, Any]:
    from .snapshots import calculate_file_identity, storage_uri_for_sha256

    source_file = Path(args.source_file).resolve()
    digest, byte_size = calculate_file_identity(source_file)
    return {
        "ok": True,
        "command": "snapshot-info",
        "source_file": str(source_file),
        "sha256": digest,
        "byte_size": byte_size,
        "storage_uri": storage_uri_for_sha256(digest),
    }


def _command_validate(args: argparse.Namespace, catalog: SchemaCatalog) -> dict[str, Any]:
    kind = canonical_kind(args.kind)
    payload = load_json_object(args.file)
    catalog.validate(kind, payload)
    model = _parse_model(kind, payload)
    cross_checks: list[str] = []

    allowed_context = {
        "SourceDocument": set(),
        "Citation": {"document"},
        "Evidence": {"document", "analysis_cutoff"},
        "ThesisCard": set(),
        "ThesisDiff": {"document", "evidence", "base_thesis"},
        "UserReview": {"diff"},
    }
    _reject_context_except(args, allowed=allowed_context[kind])

    documents = (
        _load_context_models(catalog, "SourceDocument", args.document)
        if "document" in allowed_context[kind]
        else {}
    )
    evidences = (
        _load_context_models(catalog, "Evidence", args.evidence)
        if "evidence" in allowed_context[kind]
        else {}
    )
    base_thesis = None
    if args.base_thesis:
        base_payload = load_json_object(args.base_thesis)
        catalog.validate("ThesisCard", base_payload)
        base_thesis = _parse_model("ThesisCard", base_payload)
        _validate_local_model("ThesisCard", base_thesis)
    supplied_diff = None
    if args.diff:
        diff_payload = load_json_object(args.diff)
        catalog.validate("ThesisDiff", diff_payload)
        supplied_diff = _parse_model("ThesisDiff", diff_payload)

    if kind in {"SourceDocument", "ThesisCard"}:
        _validate_local_model(kind, model)
        cross_checks.append("local_domain_invariants")
    elif kind == "Citation":
        if documents:
            _validate_citation_model(model, documents)
            cross_checks.append("document_snapshot_identity")
    elif kind == "Evidence":
        if documents:
            cutoff = _parse_datetime(args.analysis_cutoff) if args.analysis_cutoff else None
            _validate_evidence_model(model, documents, cutoff)
            cross_checks.extend(("citation_document_identity", "temporal_cutoff"))
        elif args.analysis_cutoff:
            raise CliInputError("--analysis-cutoff for Evidence also requires --document context")
    elif kind == "ThesisDiff":
        any_context = bool(documents or evidences or base_thesis)
        if any_context and not (documents and evidences and base_thesis is not None):
            raise CliInputError(
                "ThesisDiff cross-object validation requires --base-thesis, --document, and --evidence"
            )
        if any_context:
            _validate_thesis_diff_model(model, base_thesis, evidences, documents)
            cross_checks.extend(
                ("base_version_and_assumptions", "evidence_mapping", "future_leakage")
            )
    elif kind == "UserReview":
        if supplied_diff is not None:
            _validate_user_review_model(model, supplied_diff)
            cross_checks.append("review_diff_identity")

    return {
        "ok": True,
        "command": "validate",
        "kind": kind,
        "file": str(Path(args.file).resolve()),
        "schema_validated": True,
        "cross_object_validated": bool(cross_checks),
        "cross_checks": cross_checks,
    }


def _command_commit_thesis(args: argparse.Namespace, catalog: SchemaCatalog) -> dict[str, Any]:
    payload = load_json_object(args.file)
    catalog.validate("ThesisCard", payload)
    card = _parse_model("ThesisCard", payload)
    _validate_local_model("ThesisCard", card)
    if payload["version"].get("user_confirmed") is not True:
        raise CliInputError("commit-thesis requires version.user_confirmed=true")
    citation_text_checks = _validate_thesis_card_evidence_dependencies(
        args.workspace,
        payload,
        catalog,
        card.version.updated_at,
    )
    committed = commit_thesis_version(args.workspace, payload)
    return {
        "ok": True,
        "command": "commit-thesis",
        "workspace": str(Path(args.workspace).resolve()),
        "company_id": payload["company"]["company_id"],
        "thesis_id": payload["thesis_id"],
        "version_id": payload["version"]["version_id"],
        "record_sha256": object_sha256(committed),
        "citation_text_checks": citation_text_checks,
    }


def _command_save_document(args: argparse.Namespace, catalog: SchemaCatalog) -> dict[str, Any]:
    payload = load_json_object(args.file)
    catalog.validate("SourceDocument", payload)
    document = _parse_model("SourceDocument", payload)
    _validate_local_model("SourceDocument", document)
    company_id = payload["company_id"]
    artifact_id = payload["source_document_id"]
    _verify_managed_snapshot(args.workspace, payload)
    path = save_company_artifact(args.workspace, company_id, "documents", artifact_id, payload)
    return {
        "ok": True,
        "command": "save-document",
        "workspace": str(Path(args.workspace).resolve()),
        "company_id": company_id,
        "source_document_id": artifact_id,
        "path": str(path.resolve()),
        "record_sha256": object_sha256(payload),
    }


def _command_ingest_document(args: argparse.Namespace, catalog: SchemaCatalog) -> dict[str, Any]:
    """Validate metadata before atomically ingesting and recording source bytes."""

    from .snapshots import ingest_snapshot

    payload = load_json_object(args.metadata_json)

    # Ordering is a safety invariant: malformed metadata must not initialize a
    # workspace, create an object-store directory, or write a document record.
    catalog.validate("SourceDocument", payload)
    document = _parse_model("SourceDocument", payload)
    _validate_local_model("SourceDocument", document)

    company_id = payload["company_id"]
    artifact_id = payload["source_document_id"]
    existing = read_company_artifact(
        args.workspace,
        company_id,
        "documents",
        artifact_id,
    )
    if existing is not None and existing != payload:
        raise CliInputError(
            f"immutable SourceDocument {artifact_id!r} already exists with different metadata"
        )

    snapshot = ingest_snapshot(args.workspace, args.source_file, payload)
    if existing is None:
        document_path = save_company_artifact(
            args.workspace,
            company_id,
            "documents",
            artifact_id,
            payload,
        )
    else:
        document_path = (
            Path(args.workspace)
            / "companies"
            / company_id
            / "documents"
            / f"{artifact_id}.json"
        )
    return {
        "ok": True,
        "command": "ingest-document",
        "workspace": str(Path(args.workspace).resolve()),
        "company_id": company_id,
        "source_document_id": artifact_id,
        "document_path": str(document_path.resolve()),
        "record_sha256": object_sha256(payload),
        "object_path": str(snapshot.object_path.resolve()),
        "snapshot_sha256": snapshot.sha256,
        "byte_size": snapshot.byte_size,
        "object_created": snapshot.created,
    }


def _command_save_evidence(args: argparse.Namespace, catalog: SchemaCatalog) -> dict[str, Any]:
    payload = load_json_object(args.file)
    catalog.validate("Evidence", payload)
    evidence = _parse_model("Evidence", payload)
    company_id = payload["company_id"]
    documents = _stored_documents_for_evidence(args.workspace, company_id, payload, catalog)
    cutoff = _parse_datetime(args.analysis_cutoff) if args.analysis_cutoff else None
    _validate_evidence_model(evidence, documents, cutoff)
    citation_text_checks = _verify_managed_evidence_citations(
        args.workspace,
        company_id,
        payload,
    )
    artifact_id = payload["evidence_id"]
    path = save_company_artifact(args.workspace, company_id, "evidence", artifact_id, payload)
    return {
        "ok": True,
        "command": "save-evidence",
        "workspace": str(Path(args.workspace).resolve()),
        "company_id": company_id,
        "evidence_id": artifact_id,
        "path": str(path.resolve()),
        "record_sha256": object_sha256(payload),
        "citation_text_checks": citation_text_checks,
    }


def _command_extract_evidence(
    args: argparse.Namespace,
    catalog: SchemaCatalog,
) -> dict[str, Any]:
    from .source_text import extract_source_text

    metadata = load_json_object(args.request_metadata)
    _require_exact_request_fields(
        metadata,
        (
            "analysis_cutoff_at",
            "evidence_id_prefix",
            "citation_id_prefix",
            "created_at",
            "extraction_scope",
        ),
        "Evidence extraction request",
    )
    _require_string_request_fields(
        metadata,
        ("analysis_cutoff_at", "evidence_id_prefix", "citation_id_prefix", "created_at"),
        "Evidence extraction request",
    )
    scope = metadata["extraction_scope"]
    if (
        not isinstance(scope, list)
        or not 1 <= len(scope) <= 100
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 1_000
            for item in scope
        )
    ):
        raise CliInputError(
            "Evidence extraction request.extraction_scope must contain 1-100 "
            "non-empty strings of at most 1000 characters"
        )
    cutoff = _parse_datetime(metadata["analysis_cutoff_at"])
    _parse_datetime(metadata["created_at"])
    if not isinstance(args.max_source_chars, int) or args.max_source_chars < 1:
        raise CliInputError("--max-source-chars must be a positive integer")
    _assert_model_run_id_unused(args)

    document_payload = read_company_artifact(
        args.workspace,
        args.company_id,
        "documents",
        args.source_document_id,
    )
    if document_payload is None:
        raise CliInputError(
            f"SourceDocument {args.source_document_id!r} is not stored for "
            f"company {args.company_id!r}"
        )
    catalog.validate("SourceDocument", document_payload)
    document = _parse_model("SourceDocument", document_payload)
    _validate_local_model("SourceDocument", document)
    if document_payload.get("company_id") != args.company_id:
        raise CliInputError("SourceDocument company_id does not match the command")
    if document.publicly_available_at > cutoff:
        raise CliInputError(
            "SourceDocument was not publicly available by analysis_cutoff_at"
        )
    view = extract_source_text(args.workspace, document_payload)
    located_text = _source_text_envelope(view, args.max_source_chars)
    current_thesis = read_current_thesis(args.workspace, args.company_id)
    if current_thesis is not None:
        catalog.validate("ThesisCard", current_thesis)
        current_thesis_model = _parse_model("ThesisCard", current_thesis)
        _validate_local_model("ThesisCard", current_thesis_model)
        if (
            current_thesis_model.version.created_at > cutoff
            or current_thesis_model.version.updated_at > cutoff
            or current_thesis_model.version.as_of_date > cutoff.date()
        ):
            raise CliInputError(
                "current ThesisCard did not exist at the requested analysis cutoff"
            )

    run = _run_model_command(
        args,
        task="evidence-extraction",
        request_metadata=metadata,
        inputs={
            "source_document": document_payload,
            "document_content": located_text,
            "existing_thesis_context": current_thesis,
        },
    )
    if not isinstance(run.output, list):  # guarded by model_runtime
        raise CliInputError("Evidence adapter did not return an object array")

    evidence_ids: set[str] = set()
    citation_ids: set[str] = set()
    citation_checks: list[dict[str, Any]] = []
    for index, evidence_payload in enumerate(run.output):
        catalog.validate("Evidence", evidence_payload)
        content_class = evidence_payload.get("content_class")
        attribution = evidence_payload.get("attribution")
        allowed_attributions = {
            "source_fact": {"source_document", "management"},
            "source_opinion": {"management", "third_party_author"},
        }
        if document_payload.get("source_class") == "user_provided":
            allowed_attributions["user_judgment"] = {"user"}
        if content_class not in allowed_attributions:
            raise CliInputError(
                "extraction content_class is not allowed for the selected source_class"
            )
        if attribution not in allowed_attributions[content_class]:
            raise CliInputError(
                f"extraction attribution {attribution!r} is not allowed for "
                f"{content_class}"
            )
        evidence = _parse_model("Evidence", evidence_payload)
        _validate_evidence_model(
            evidence,
            {document.source_document_id: document},
            cutoff,
        )
        evidence_id = evidence_payload.get("evidence_id")
        if evidence_id in evidence_ids:
            raise CliInputError(f"adapter returned duplicate evidence_id {evidence_id!r}")
        evidence_ids.add(str(evidence_id))
        if evidence_payload.get("company_id") != args.company_id:
            raise CliInputError(
                f"adapter Evidence at index {index} has the wrong company_id"
            )
        if evidence_payload.get("verification_status") != "unreviewed":
            raise CliInputError(
                "an extraction adapter must emit verification_status=unreviewed"
            )
        if not str(evidence_id).startswith(str(metadata["evidence_id_prefix"])):
            raise CliInputError(
                f"adapter Evidence {evidence_id!r} does not use evidence_id_prefix"
            )
        if evidence_payload.get("created_at") != metadata["created_at"]:
            raise CliInputError(
                f"adapter Evidence {evidence_id!r} must copy request_metadata.created_at"
            )
        if evidence_payload.get("available_as_of") != document_payload.get(
            "publicly_available_at"
        ):
            raise CliInputError(
                f"adapter Evidence {evidence_id!r} must copy SourceDocument.publicly_available_at"
            )
        for citation in evidence_payload.get("citations", []):
            if not isinstance(citation, dict):
                continue
            citation_id = citation.get("citation_id")
            if citation_id in citation_ids:
                raise CliInputError(
                    f"adapter returned duplicate citation_id {citation_id!r}"
                )
            citation_ids.add(str(citation_id))
            if not str(citation_id).startswith(
                str(metadata["citation_id_prefix"])
            ):
                raise CliInputError(
                    f"adapter Citation {citation_id!r} does not use citation_id_prefix"
                )
        cited_ids = {
            citation.get("source_document_id")
            for citation in evidence_payload.get("citations", [])
            if isinstance(citation, dict)
        }
        if cited_ids != {args.source_document_id}:
            raise CliInputError(
                f"adapter Evidence {evidence_id!r} must cite only the selected SourceDocument"
            )
        citation_checks.extend(
            _verify_managed_evidence_citations(
                args.workspace,
                args.company_id,
                evidence_payload,
                require_human_paraphrase=False,
            )
        )

    run_record, run_path = _save_model_run(
        args,
        task="evidence-extraction",
        request_metadata=metadata,
        input_references={
            "source_document_ids": [args.source_document_id],
            "base_version_id": None
            if current_thesis is None
            else current_thesis.get("version", {}).get("version_id"),
        },
        run=run,
    )
    return {
        "ok": True,
        "command": "extract-evidence",
        "workspace": str(Path(args.workspace).resolve()),
        "company_id": args.company_id,
        "source_document_id": args.source_document_id,
        "model_run_id": args.run_id,
        "model_run_path": str(run_path.resolve()),
        "record_sha256": object_sha256(run_record),
        "evidence_count": len(run.output),
        "citation_text_checks": citation_checks,
        "output": run.output,
        "provenance": run.provenance.to_dict(),
    }


def _command_generate_diff(
    args: argparse.Namespace,
    catalog: SchemaCatalog,
) -> dict[str, Any]:
    metadata = load_json_object(args.request_metadata)
    _require_exact_request_fields(
        metadata,
        (
            "analysis_cutoff_at",
            "generated_at",
            "thesis_diff_id",
            "material_published_on",
            "proposed_version_id",
            "proposed_as_of_date",
            "proposed_created_at",
            "proposed_updated_at",
            "comparison_id_prefix",
            "question_id_prefix",
            "change_id_prefix",
        ),
        "ThesisDiff generation request",
    )
    _require_string_request_fields(
        metadata,
        (
            "analysis_cutoff_at",
            "generated_at",
            "thesis_diff_id",
            "material_published_on",
            "proposed_version_id",
            "proposed_as_of_date",
            "proposed_created_at",
            "proposed_updated_at",
            "comparison_id_prefix",
            "question_id_prefix",
            "change_id_prefix",
        ),
        "ThesisDiff generation request",
    )
    cutoff = _parse_datetime(metadata["analysis_cutoff_at"])
    generated_at = _parse_datetime(metadata["generated_at"])
    proposed_created_at = _parse_datetime(metadata["proposed_created_at"])
    proposed_updated_at = _parse_datetime(metadata["proposed_updated_at"])
    if generated_at < cutoff:
        raise CliInputError("generated_at cannot precede analysis_cutoff_at")
    if proposed_created_at > proposed_updated_at:
        raise CliInputError("proposed_created_at cannot follow proposed_updated_at")
    if proposed_updated_at > generated_at:
        raise CliInputError("proposed version timestamps cannot follow generated_at")
    material_published_on = _parse_date(metadata["material_published_on"])
    proposed_as_of_date = _parse_date(metadata["proposed_as_of_date"])
    if material_published_on > cutoff.date():
        raise CliInputError("material_published_on cannot follow analysis_cutoff_at")
    if proposed_as_of_date > cutoff.date():
        raise CliInputError("proposed_as_of_date cannot follow analysis_cutoff_at")
    _assert_model_run_id_unused(args)
    _require_unique_cli_ids(args.document_ids, "--document")
    _require_unique_cli_ids(args.evidence_ids, "--evidence")
    _require_unique_cli_ids(
        args.prior_evidence_ids,
        "--prior-evidence",
        require_one=False,
    )
    overlap = set(args.evidence_ids) & set(args.prior_evidence_ids)
    if overlap:
        raise CliInputError(
            "new and prior Evidence sets overlap: " + ", ".join(sorted(overlap))
        )

    current_payload = read_current_thesis(args.workspace, args.company_id)
    if current_payload is None:
        raise CliInputError(f"company {args.company_id!r} has no current ThesisCard")
    catalog.validate("ThesisCard", current_payload)
    base_thesis = _parse_model("ThesisCard", current_payload)
    _validate_local_model("ThesisCard", base_thesis)
    if (
        base_thesis.version.created_at > cutoff
        or base_thesis.version.updated_at > cutoff
        or base_thesis.version.as_of_date > cutoff.date()
    ):
        raise CliInputError(
            "current ThesisCard did not exist at the requested analysis cutoff"
        )
    if proposed_as_of_date < base_thesis.version.as_of_date:
        raise CliInputError("proposed_as_of_date cannot precede the current ThesisCard")
    if proposed_created_at < base_thesis.version.updated_at:
        raise CliInputError(
            "proposed_created_at cannot precede the current ThesisCard updated_at"
        )
    if proposed_updated_at < base_thesis.version.updated_at:
        raise CliInputError("proposed_updated_at cannot precede the current ThesisCard")

    new_document_payloads, new_documents = _load_artifacts_by_id(
        args.workspace,
        args.company_id,
        "documents",
        "SourceDocument",
        args.document_ids,
        catalog,
    )
    base_evidence_ids = _thesis_counter_evidence_ids(current_payload)
    selected_evidence_ids = list(args.prior_evidence_ids) + list(args.evidence_ids)
    # Base ThesisCard references are integrity dependencies, not hidden model
    # context.  Resolve and audit them even when they are not re-selected; only
    # the explicit prior/new lists below are sent to the adapter.
    all_evidence_ids = list(
        dict.fromkeys(selected_evidence_ids + sorted(base_evidence_ids))
    )
    evidence_payloads, evidences = _load_artifacts_by_id(
        args.workspace,
        args.company_id,
        "evidence",
        "Evidence",
        all_evidence_ids,
        catalog,
    )
    new_evidence_document_ids = {
        citation["source_document_id"]
        for evidence_id in args.evidence_ids
        for citation in evidence_payloads[evidence_id].get("citations", [])
        if isinstance(citation, dict)
        and isinstance(citation.get("source_document_id"), str)
    }
    unselected_new_documents = new_evidence_document_ids - set(args.document_ids)
    if unselected_new_documents:
        raise CliInputError(
            "new Evidence cites SourceDocuments not selected with --document: "
            + ", ".join(sorted(unselected_new_documents))
        )
    cited_document_ids = {
        citation["source_document_id"]
        for payload in evidence_payloads.values()
        for citation in payload.get("citations", [])
        if isinstance(citation, dict)
        and isinstance(citation.get("source_document_id"), str)
    }
    prior_document_ids = sorted(cited_document_ids - set(args.document_ids))
    _, prior_documents = _load_artifacts_by_id(
        args.workspace,
        args.company_id,
        "documents",
        "SourceDocument",
        prior_document_ids,
        catalog,
    )
    documents = {**prior_documents, **new_documents}
    future_documents = [
        document_id
        for document_id, document in documents.items()
        if document.publicly_available_at > cutoff
    ]
    if future_documents:
        raise CliInputError(
            "selected or cited SourceDocuments were not public by analysis_cutoff_at: "
            + ", ".join(sorted(future_documents))
        )
    citation_checks: list[dict[str, Any]] = []
    for evidence_id in all_evidence_ids:
        payload = evidence_payloads[evidence_id]
        if payload.get("verification_status") != "verified":
            raise CliInputError(
                f"Evidence {evidence_id!r} must be explicitly verified before generation"
            )
        evidence_cutoff = (
            base_thesis.version.updated_at
            if evidence_id in base_evidence_ids
            else cutoff
        )
        _validate_evidence_model(
            evidences[evidence_id], documents, evidence_cutoff
        )
        citation_checks.extend(
            _verify_managed_evidence_citations(
                args.workspace,
                args.company_id,
                payload,
            )
        )

    run = _run_model_command(
        args,
        task="thesis-diff",
        request_metadata=metadata,
        inputs={
            "base_thesis_card": current_payload,
            "prior_evidence_for_say_do_comparison": [
                evidence_payloads[item] for item in args.prior_evidence_ids
            ],
            "new_source_documents": [
                new_document_payloads[item] for item in args.document_ids
            ],
            "new_evidence": [evidence_payloads[item] for item in args.evidence_ids],
        },
    )
    if not isinstance(run.output, dict):  # guarded by model_runtime
        raise CliInputError("ThesisDiff adapter did not return one object")
    diff_payload = run.output
    catalog.validate("ThesisDiff", diff_payload)
    diff = _parse_model("ThesisDiff", diff_payload)
    _validate_thesis_diff_model(diff, base_thesis, evidences, documents)
    _validate_generated_diff_identity(
        diff_payload,
        metadata,
        expected_document_ids=set(args.document_ids),
        expected_prior_evidence_ids=set(args.prior_evidence_ids),
        expected_new_evidence_ids=set(args.evidence_ids),
        base_thesis=current_payload,
    )

    diff_id = str(diff_payload["thesis_diff_id"])
    existing_diff = read_company_artifact(
        args.workspace, args.company_id, "diffs", diff_id
    )
    if existing_diff is not None and existing_diff != diff_payload:
        raise CliInputError(
            f"immutable ThesisDiff {diff_id!r} already exists with different content"
        )
    run_record = _model_run_record(
        args,
        task="thesis-diff",
        request_metadata=metadata,
        input_references={
            "base_version_id": base_thesis.version.version_id,
            "source_document_ids": list(args.document_ids),
            "prior_source_document_ids": prior_document_ids,
            "prior_evidence_ids": list(args.prior_evidence_ids),
            "new_evidence_ids": list(args.evidence_ids),
        },
        run=run,
    )
    run_path, diff_path = save_company_artifact_bundle(
        args.workspace,
        args.company_id,
        [
            ("model_runs", args.run_id, run_record),
            ("diffs", diff_id, diff_payload),
        ],
    )
    return {
        "ok": True,
        "command": "generate-diff",
        "workspace": str(Path(args.workspace).resolve()),
        "company_id": args.company_id,
        "thesis_diff_id": diff_id,
        "diff_path": str(diff_path.resolve()),
        "model_run_id": args.run_id,
        "model_run_path": str(run_path.resolve()),
        "record_sha256": object_sha256(run_record),
        "citation_text_checks": citation_checks,
        "output": diff_payload,
        "provenance": run.provenance.to_dict(),
    }


def _command_review(args: argparse.Namespace, catalog: SchemaCatalog) -> dict[str, Any]:
    diff_payload = load_json_object(args.diff_file)
    review_payload = load_json_object(args.review_file)
    catalog.validate("ThesisDiff", diff_payload)
    catalog.validate("UserReview", review_payload)
    diff = _parse_model("ThesisDiff", diff_payload)
    review = _parse_model("UserReview", review_payload)
    company_id = diff_payload["company_id"]

    current_payload = read_current_thesis(args.workspace, company_id)
    if current_payload is None:
        raise CliInputError(f"company {company_id!r} has no current ThesisCard")
    catalog.validate("ThesisCard", current_payload)
    base = _parse_model("ThesisCard", current_payload)
    _validate_local_model("ThesisCard", base)
    documents = _stored_models(args.workspace, company_id, "documents", "SourceDocument", catalog)
    evidences = _stored_models(args.workspace, company_id, "evidence", "Evidence", catalog)
    _validate_thesis_diff_model(diff, base, evidences, documents)
    _validate_user_review_model(review, diff)
    citation_text_checks = _verify_diff_citation_text(
        args.workspace,
        company_id,
        diff_payload,
    )
    if review_payload.get("reviewed_thesis") is not None:
        citation_text_checks.extend(
            _validate_thesis_card_evidence_dependencies(
                args.workspace,
                review_payload["reviewed_thesis"],
                catalog,
                review.reviewed_thesis.version.updated_at,
            )
        )

    outcome = apply_user_review(args.workspace, diff_payload, review_payload)
    return {
        "ok": True,
        "command": "review",
        "workspace": str(Path(args.workspace).resolve()),
        "citation_text_checks": citation_text_checks,
        **outcome,
    }


def _command_status(args: argparse.Namespace) -> dict[str, Any]:
    workspace = Path(args.workspace).resolve()
    manifest_path = resolve_workspace_path(workspace, "manifest.json")
    if not manifest_path.exists():
        return {
            "ok": True,
            "command": "status",
            "workspace": str(workspace),
            "initialized": False,
            "companies": [],
            "audit_event_count": 0,
        }
    manifest = load_json_object(manifest_path)
    if manifest.get("format") != "thesisos-workspace" or manifest.get("format_version") != 1:
        raise CliInputError(f"unsupported workspace manifest: {manifest_path}")
    companies_root = resolve_workspace_path(workspace, "companies")
    companies: list[dict[str, Any]] = []
    if companies_root.is_dir():
        for company_path in sorted(companies_root.iterdir(), key=lambda item: item.name):
            if company_path.is_symlink():
                raise CliInputError(
                    f"symbolic-link company path is not allowed: {company_path}"
                )
            if not company_path.is_dir():
                continue
            if args.company_id and company_path.name != args.company_id:
                continue
            current = read_current_thesis(workspace, company_path.name)
            counts = {
                kind: _json_file_count(
                    resolve_workspace_path(
                        workspace,
                        "companies",
                        company_path.name,
                        "thesis_versions",
                    )
                    if kind == "thesis_versions"
                    else company_artifact_directory(
                        workspace, company_path.name, kind
                    )
                )
                for kind in (
                    "thesis_versions",
                    "documents",
                    "evidence",
                    "diffs",
                    "reviews",
                    "research_tasks",
                    "model_runs",
                )
            }
            companies.append(
                {
                    "company_id": company_path.name,
                    "current_thesis_id": None if current is None else current.get("thesis_id"),
                    "current_version_id": None
                    if current is None
                    else current.get("version", {}).get("version_id"),
                    "records": counts,
                }
            )
    audit_path = resolve_workspace_path(workspace, "audit", "events.jsonl")
    audit_count = 0
    if audit_path.is_file():
        with audit_path.open("r", encoding="utf-8") as handle:
            audit_count = sum(1 for line in handle if line.strip())
    return {
        "ok": True,
        "command": "status",
        "workspace": str(workspace),
        "initialized": True,
        "format": manifest["format"],
        "format_version": manifest["format_version"],
        "companies": companies,
        "audit_event_count": audit_count,
    }


def _parse_model(kind: str, payload: dict[str, Any]) -> Any:
    from .models import Citation, Evidence, SourceDocument, ThesisCard, ThesisDiff, UserReview

    model_types = {
        "SourceDocument": SourceDocument,
        "Citation": Citation,
        "Evidence": Evidence,
        "ThesisCard": ThesisCard,
        "ThesisDiff": ThesisDiff,
        "UserReview": UserReview,
    }
    return model_types[kind].from_dict(payload)


def _validate_local_model(kind: str, model: Any) -> None:
    from .validation import validate_source_document, validate_thesis_card

    if kind == "SourceDocument":
        validate_source_document(model)
    elif kind == "ThesisCard":
        validate_thesis_card(model)


def _validate_evidence_model(
    evidence: Any,
    documents: dict[str, Any],
    cutoff: datetime | None,
) -> None:
    from .validation import validate_evidence

    validate_evidence(evidence, documents, cutoff)


def _validate_thesis_diff_model(
    diff: Any,
    base_thesis: Any,
    evidences: dict[str, Any],
    documents: dict[str, Any],
) -> None:
    from .validation import validate_thesis_diff

    validate_thesis_diff(diff, base_thesis, evidences, documents)


def _validate_user_review_model(review: Any, diff: Any) -> None:
    from .validation import validate_user_review

    validate_user_review(review, diff)


def _load_context_models(
    catalog: SchemaCatalog,
    kind: str,
    paths: Iterable[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    id_field = "source_document_id" if kind == "SourceDocument" else "evidence_id"
    for raw_path in paths:
        payload = load_json_object(raw_path)
        catalog.validate(kind, payload)
        model = _parse_model(kind, payload)
        if kind == "SourceDocument":
            _validate_local_model(kind, model)
        identifier = payload[id_field]
        if identifier in result:
            raise CliInputError(f"duplicate {id_field} in context: {identifier}")
        result[identifier] = model
    return result


def _validate_thesis_card_evidence_dependencies(
    workspace: str | Path,
    thesis: dict[str, Any],
    catalog: SchemaCatalog,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Resolve and audit Evidence embedded in a formal ThesisCard."""

    company = thesis.get("company")
    company_id = company.get("company_id") if isinstance(company, dict) else None
    if not isinstance(company_id, str):
        raise CliInputError("ThesisCard company.company_id must be a string")
    evidence_ids = sorted(_thesis_counter_evidence_ids(thesis))
    if not evidence_ids:
        return []

    evidence_payloads, evidences = _load_artifacts_by_id(
        workspace,
        company_id,
        "evidence",
        "Evidence",
        evidence_ids,
        catalog,
    )
    document_ids = sorted(
        {
            citation["source_document_id"]
            for payload in evidence_payloads.values()
            for citation in payload.get("citations", [])
            if isinstance(citation, dict)
            and isinstance(citation.get("source_document_id"), str)
        }
    )
    _, documents = _load_artifacts_by_id(
        workspace,
        company_id,
        "documents",
        "SourceDocument",
        document_ids,
        catalog,
    )
    citation_checks: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        payload = evidence_payloads[evidence_id]
        if payload.get("verification_status") != "verified":
            raise CliInputError(
                f"Evidence {evidence_id!r} must be verified before it can "
                "support a formal ThesisCard"
            )
        _validate_evidence_model(evidences[evidence_id], documents, cutoff)
        citation_checks.extend(
            _verify_managed_evidence_citations(
                workspace,
                company_id,
                payload,
            )
        )
    return citation_checks


def _stored_documents_for_evidence(
    workspace: str | Path,
    company_id: str,
    evidence: dict[str, Any],
    catalog: SchemaCatalog,
) -> dict[str, Any]:
    document_ids = {
        citation["source_document_id"]
        for citation in evidence.get("citations", [])
        if isinstance(citation, dict) and isinstance(citation.get("source_document_id"), str)
    }
    result: dict[str, Any] = {}
    for document_id in sorted(document_ids):
        payload = read_company_artifact(workspace, company_id, "documents", document_id)
        if payload is None:
            raise CliInputError(
                f"evidence references SourceDocument {document_id!r}, but it is not stored for company {company_id!r}"
            )
        catalog.validate("SourceDocument", payload)
        model = _parse_model("SourceDocument", payload)
        _validate_local_model("SourceDocument", model)
        _verify_managed_snapshot(workspace, payload)
        result[document_id] = model
    return result


def _stored_models(
    workspace: str | Path,
    company_id: str,
    storage_kind: str,
    schema_kind: str,
    catalog: SchemaCatalog,
) -> dict[str, Any]:
    directory = company_artifact_directory(workspace, company_id, storage_kind)
    if not directory.is_dir():
        return {}
    id_field = "source_document_id" if schema_kind == "SourceDocument" else "evidence_id"
    result: dict[str, Any] = {}
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise CliInputError(
                f"symbolic-link {storage_kind} artifact is not allowed: {path}"
            )
        if not path.is_file():
            raise CliInputError(f"stored artifact path is not a regular file: {path}")
        path_identifier = path.name[: -len(".json")]
        payload = read_company_artifact(
            workspace,
            company_id,
            storage_kind,
            path_identifier,
        )
        if payload is None:
            raise CliInputError(f"stored artifact disappeared during read: {path}")
        catalog.validate(schema_kind, payload)
        model = _parse_model(schema_kind, payload)
        if schema_kind == "SourceDocument":
            _validate_local_model(schema_kind, model)
            _verify_managed_snapshot(workspace, payload)
        identifier = payload[id_field]
        if identifier != path_identifier:
            raise CliInputError(
                f"stored {id_field} {identifier!r} does not match path "
                f"{path_identifier!r}"
            )
        if identifier in result:
            raise CliInputError(f"duplicate stored {id_field}: {identifier}")
        result[identifier] = model
    return result


def _load_artifacts_by_id(
    workspace: str | Path,
    company_id: str,
    storage_kind: str,
    schema_kind: str,
    identifiers: Sequence[str],
    catalog: SchemaCatalog,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    models: dict[str, Any] = {}
    id_field = (
        "source_document_id" if schema_kind == "SourceDocument" else "evidence_id"
    )
    for identifier in identifiers:
        payload = read_company_artifact(
            workspace, company_id, storage_kind, identifier
        )
        if payload is None:
            raise CliInputError(
                f"{schema_kind} {identifier!r} is not stored for company {company_id!r}"
            )
        catalog.validate(schema_kind, payload)
        if payload.get(id_field) != identifier:
            raise CliInputError(
                f"stored {schema_kind} identity does not match path {identifier!r}"
            )
        if payload.get("company_id") != company_id:
            raise CliInputError(
                f"stored {schema_kind} {identifier!r} belongs to another company"
            )
        model = _parse_model(schema_kind, payload)
        if schema_kind == "SourceDocument":
            _validate_local_model(schema_kind, model)
            _verify_managed_snapshot(workspace, payload)
        payloads[identifier] = payload
        models[identifier] = model
    return payloads, models


def _source_text_envelope(view: Any, max_source_chars: int) -> dict[str, Any]:
    total_characters = sum(len(page.text) for page in view.pages)
    if total_characters > max_source_chars:
        raise CliInputError(
            f"extracted source contains {total_characters} characters, exceeding "
            f"--max-source-chars {max_source_chars}"
        )
    result: dict[str, Any] = {
        "source_document_id": view.source_document_id,
        "snapshot_sha256": view.snapshot_sha256,
        "media_type": view.media_type,
        "page_count": view.page_count,
        "pages": [
            {"page": page.number, "text": page.text} for page in view.pages
        ],
    }
    if view.media_type in {"plain_text", "markdown"}:
        result["lines"] = [
            {
                "line": line.number,
                "page": line.page_number,
                "page_line": line.page_line_number,
                "text": line.text,
            }
            for line in view.lines
        ]
        result["paragraphs"] = [
            {
                "paragraph": paragraph.number,
                "page": paragraph.page_number,
                "line_start": paragraph.line_start,
                "line_end": paragraph.line_end,
                "text": paragraph.text,
            }
            for paragraph in view.paragraphs
        ]
    return result


def _run_model_command(
    args: argparse.Namespace,
    *,
    task: str,
    request_metadata: dict[str, Any],
    inputs: dict[str, Any],
) -> ModelRunResult:
    return run_model_adapter(
        [args.adapter, *args.adapter_arg],
        task=task,
        model_identifier=args.model_id,
        request_metadata=request_metadata,
        inputs=inputs,
        timeout_seconds=args.timeout,
        max_stdout_bytes=args.max_stdout_bytes,
        prompt_directory=args.prompt_dir,
    )


def _save_model_run(
    args: argparse.Namespace,
    *,
    task: str,
    request_metadata: dict[str, Any],
    input_references: dict[str, Any],
    run: ModelRunResult,
) -> tuple[dict[str, Any], Path]:
    record = _model_run_record(
        args,
        task=task,
        request_metadata=request_metadata,
        input_references=input_references,
        run=run,
    )
    existing = read_company_artifact(
        args.workspace,
        args.company_id,
        "model_runs",
        args.run_id,
    )
    if existing is not None and existing != record:
        raise CliInputError(
            f"immutable model run {args.run_id!r} already exists with different content"
        )
    path = save_company_artifact(
        args.workspace,
        args.company_id,
        "model_runs",
        args.run_id,
        record,
    )
    return record, path


def _model_run_record(
    args: argparse.Namespace,
    *,
    task: str,
    request_metadata: dict[str, Any],
    input_references: dict[str, Any],
    run: ModelRunResult,
) -> dict[str, Any]:
    return {
        "format": "thesisos-model-run",
        "format_version": 1,
        "model_run_id": args.run_id,
        "company_id": args.company_id,
        "task": task,
        "request_metadata": request_metadata,
        "input_references": input_references,
        "output": run.output,
        "provenance": run.provenance.to_dict(),
    }


def _assert_model_run_id_unused(args: argparse.Namespace) -> None:
    """Reserve a run identity and reject duplicates before a paid adapter run."""

    if getattr(args, "_model_run_reservation", None) is not None:
        raise CliInputError("model run identity was reserved more than once")
    reservation = reserve_model_run(
        args.workspace,
        args.company_id,
        args.run_id,
    )
    reservation.__enter__()
    setattr(args, "_model_run_reservation", reservation)

    try:
        existing = read_company_artifact(
            args.workspace,
            args.company_id,
            "model_runs",
            args.run_id,
        )
        if existing is not None:
            raise CliInputError(
                f"model run {args.run_id!r} already exists; choose a new --run-id"
            )
    except Exception:
        _release_model_run_reservation(args)
        raise


def _release_model_run_reservation(args: argparse.Namespace) -> None:
    reservation = getattr(args, "_model_run_reservation", None)
    if reservation is None:
        return
    delattr(args, "_model_run_reservation")
    reservation.__exit__(None, None, None)


def _require_exact_request_fields(
    metadata: dict[str, Any],
    fields: Sequence[str],
    label: str,
) -> None:
    allowed = set(fields)
    unknown = sorted(set(metadata) - allowed)
    if unknown:
        raise CliInputError(
            f"{label} contains unknown field(s): {', '.join(unknown)}"
        )
    missing = [field for field in fields if field not in metadata]
    if missing:
        raise CliInputError(f"{label} is missing: {', '.join(missing)}")
    for field in fields:
        value = metadata[field]
        if value is None or (isinstance(value, str) and not value.strip()):
            raise CliInputError(f"{label}.{field} must not be empty")


def _require_string_request_fields(
    metadata: dict[str, Any],
    fields: Sequence[str],
    label: str,
) -> None:
    for field in fields:
        if not isinstance(metadata.get(field), str):
            raise CliInputError(f"{label}.{field} must be a string")


def _require_unique_cli_ids(
    values: Sequence[str],
    option: str,
    *,
    require_one: bool = True,
) -> None:
    if (require_one and not values) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise CliInputError(f"{option} requires one or more non-empty IDs")
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise CliInputError(f"{option} contains duplicate IDs: {', '.join(duplicates)}")


def _validate_generated_diff_identity(
    diff: dict[str, Any],
    metadata: dict[str, Any],
    *,
    expected_document_ids: set[str],
    expected_prior_evidence_ids: set[str],
    expected_new_evidence_ids: set[str],
    base_thesis: dict[str, Any],
) -> None:
    for field in (
        "analysis_cutoff_at",
        "generated_at",
        "thesis_diff_id",
        "material_published_on",
    ):
        if diff.get(field) != metadata.get(field):
            raise CliInputError(
                f"adapter output {field} must exactly copy request_metadata.{field}"
            )
    actual_document_ids = diff.get("source_document_ids")
    if not isinstance(actual_document_ids, list) or set(actual_document_ids) != expected_document_ids:
        raise CliInputError(
            "adapter output source_document_ids must exactly match selected --document IDs"
        )

    proposed = diff.get("proposed_patch", {}).get("proposed_thesis", {})
    version = proposed.get("version", {}) if isinstance(proposed, dict) else {}
    expected_version_fields = {
        "version_id": "proposed_version_id",
        "as_of_date": "proposed_as_of_date",
        "created_at": "proposed_created_at",
        "updated_at": "proposed_updated_at",
    }
    for version_field, metadata_field in expected_version_fields.items():
        if not isinstance(version, dict) or version.get(version_field) != metadata.get(
            metadata_field
        ):
            raise CliInputError(
                f"adapter proposed_thesis.version.{version_field} must exactly copy "
                f"request_metadata.{metadata_field}"
            )
    prefixed_collections = (
        (
            diff.get("management_statement_action", {}).get("comparisons", []),
            "comparison_id",
            "comparison_id_prefix",
        ),
        (
            diff.get("follow_up_questions", []),
            "question_id",
            "question_id_prefix",
        ),
        (
            diff.get("proposed_patch", {}).get("change_items", []),
            "change_id",
            "change_id_prefix",
        ),
    )
    for collection, id_field, prefix_field in prefixed_collections:
        if not isinstance(collection, list):
            continue
        prefix = str(metadata[prefix_field])
        for item in collection:
            identifier = item.get(id_field) if isinstance(item, dict) else None
            if not isinstance(identifier, str) or not identifier.startswith(prefix):
                raise CliInputError(
                    f"adapter output {id_field} {identifier!r} does not use "
                    f"request_metadata.{prefix_field}"
                )

    actual_past_evidence_ids, actual_current_evidence_ids = _diff_evidence_roles(
        diff,
        base_thesis,
    )
    unselected_past = actual_past_evidence_ids - expected_prior_evidence_ids
    if unselected_past:
        raise CliInputError(
            "adapter output past_evidence_ids must reference only selected "
            "--prior-evidence IDs: "
            + ", ".join(sorted(unselected_past))
        )
    unselected_current = actual_current_evidence_ids - expected_new_evidence_ids
    if unselected_current:
        raise CliInputError(
            "adapter output current-material Evidence references must use only "
            "selected --evidence IDs: "
            + ", ".join(sorted(unselected_current))
        )


def _thesis_counter_evidence_ids(thesis: dict[str, Any]) -> set[str]:
    counter = thesis.get("strongest_counter_case")
    if not isinstance(counter, dict):
        return set()
    values = counter.get("evidence_ids", [])
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str)}


def _diff_evidence_roles(
    diff: dict[str, Any],
    base_thesis: dict[str, Any],
) -> tuple[set[str], set[str]]:
    """Return explicit past and current-material Evidence references.

    The complete proposed ThesisCard may retain base counter-case references.
    Only references newly introduced there are current material.
    """

    past: set[str] = set()
    current: set[str] = set()

    for change in diff.get("assumption_changes", []):
        if isinstance(change, dict):
            values = change.get("evidence_ids", [])
            if isinstance(values, list):
                current.update(value for value in values if isinstance(value, str))

    management = diff.get("management_statement_action")
    if isinstance(management, dict):
        for comparison in management.get("comparisons", []):
            if not isinstance(comparison, dict):
                continue
            past_values = comparison.get("past_evidence_ids", [])
            if isinstance(past_values, list):
                past.update(
                    value for value in past_values if isinstance(value, str)
                )
            current_values = comparison.get("current_evidence_ids", [])
            if isinstance(current_values, list):
                current.update(
                    value for value in current_values if isinstance(value, str)
                )

    counter = diff.get("targeted_counter_case")
    if isinstance(counter, dict):
        values = counter.get("evidence_ids", [])
        if isinstance(values, list):
            current.update(value for value in values if isinstance(value, str))

    proposed_patch = diff.get("proposed_patch")
    if isinstance(proposed_patch, dict):
        for item in proposed_patch.get("change_items", []):
            if isinstance(item, dict):
                values = item.get("evidence_ids", [])
                if isinstance(values, list):
                    current.update(
                        value for value in values if isinstance(value, str)
                    )
        proposed = proposed_patch.get("proposed_thesis")
        if isinstance(proposed, dict):
            current.update(
                _thesis_counter_evidence_ids(proposed)
                - _thesis_counter_evidence_ids(base_thesis)
            )

    return past, current


def _verify_managed_snapshot(
    workspace: str | Path,
    source_document: dict[str, Any],
) -> None:
    """Revalidate workspace-managed bytes before a downstream write."""

    snapshot = source_document.get("snapshot")
    storage_uri = snapshot.get("storage_uri") if isinstance(snapshot, dict) else None
    if not isinstance(storage_uri, str) or not storage_uri.lower().startswith(
        "thesisos://sha256/"
    ):
        return
    from .snapshots import verify_stored_snapshot

    verify_stored_snapshot(workspace, source_document)


def _verify_managed_evidence_citations(
    workspace: str | Path,
    company_id: str,
    evidence: dict[str, Any],
    *,
    require_human_paraphrase: bool = True,
) -> list[dict[str, Any]]:
    """Verify literal citations against managed bytes before persistence.

    External snapshots remain an explicit metadata-only boundary. A faithful
    paraphrase can be admitted only when the Evidence has already been marked
    verified by the human workflow; it is never reported as an automatic text
    match.
    """

    from .source_text import extract_source_text, verify_citation_text

    results: list[dict[str, Any]] = []
    documents: dict[str, dict[str, Any]] = {}
    views: dict[str, Any] = {}
    for citation in evidence.get("citations", []):
        if not isinstance(citation, dict):
            continue
        document_id = citation.get("source_document_id")
        if not isinstance(document_id, str):
            continue
        document = documents.get(document_id)
        if document is None:
            document = read_company_artifact(
                workspace, company_id, "documents", document_id
            )
            if document is None:
                raise CliInputError(
                    f"citation references missing SourceDocument {document_id!r}"
                )
            documents[document_id] = document
        snapshot = document.get("snapshot")
        storage_uri = (
            snapshot.get("storage_uri") if isinstance(snapshot, dict) else None
        )
        if not isinstance(storage_uri, str) or not storage_uri.lower().startswith(
            "thesisos://sha256/"
        ):
            results.append(
                {
                    "citation_id": citation.get("citation_id"),
                    "source_document_id": document_id,
                    "status": "external_snapshot_not_automatically_verified",
                    "passed": False,
                    "requires_human_review": True,
                }
            )
            continue
        view = views.get(document_id)
        if view is None:
            view = extract_source_text(workspace, document)
            views[document_id] = view
        verification = verify_citation_text(view, document, citation)
        if verification.requires_human_review:
            if (
                require_human_paraphrase
                and evidence.get("verification_status") != "verified"
            ):
                raise CliInputError(
                    f"citation {citation.get('citation_id')!r} is a faithful paraphrase; "
                    "explicit human verification is required before saving it"
                )
        elif not verification.passed:
            raise CliInputError(
                f"citation {citation.get('citation_id')!r} quoted_text was not found "
                f"inside {verification.scope_reference} of managed snapshot {document_id!r}"
            )
        results.append(verification.to_dict())
    return results


def _verify_diff_citation_text(
    workspace: str | Path,
    company_id: str,
    thesis_diff: dict[str, Any],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    reference_fields = {
        "evidence_ids",
        "past_evidence_ids",
        "current_evidence_ids",
    }
    for evidence_id in sorted(
        _collect_string_values(thesis_diff, reference_fields)
    ):
        evidence = read_company_artifact(
            workspace, company_id, "evidence", evidence_id
        )
        if evidence is None:
            raise CliInputError(
                f"ThesisDiff references missing Evidence {evidence_id!r}"
            )
        results.extend(
            _verify_managed_evidence_citations(workspace, company_id, evidence)
        )
    return results


def _collect_string_values(value: Any, field_names: set[str]) -> set[str]:
    values: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in field_names and isinstance(nested, list):
                values.update(item for item in nested if isinstance(item, str))
            else:
                values.update(_collect_string_values(nested, field_names))
    elif isinstance(value, list):
        for nested in value:
            values.update(_collect_string_values(nested, field_names))
    return values


def _validate_citation_model(citation: Any, documents: dict[str, Any]) -> None:
    from .validation import validate_citation

    validate_citation(citation, documents)


def _reject_context_except(args: argparse.Namespace, allowed: set[str]) -> None:
    supplied = {
        "document": bool(args.document),
        "evidence": bool(args.evidence),
        "base_thesis": bool(args.base_thesis),
        "diff": bool(args.diff),
        "analysis_cutoff": bool(args.analysis_cutoff),
    }
    invalid = sorted(name for name, present in supplied.items() if present and name not in allowed)
    if invalid:
        rendered = ", ".join("--" + name.replace("_", "-") for name in invalid)
        raise CliInputError(f"context option(s) not applicable to {args.kind}: {rendered}")


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CliInputError(f"invalid ISO-8601 datetime: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CliInputError("analysis cutoff must include a UTC offset")
    return parsed


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CliInputError(f"invalid ISO-8601 date: {value!r}") from exc


def _json_file_count(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    count = 0
    for item in directory.iterdir():
        if item.is_symlink():
            raise CliInputError(f"symbolic-link artifact path is not allowed: {item}")
        if item.is_file() and item.suffix == ".json":
            count += 1
    return count


def _error_code(error: Exception) -> str:
    if isinstance(error, ModelRuntimeError):
        return "model_runtime_error"
    if isinstance(error, SchemaValidationRuntimeError):
        return "schema_runtime_error"
    if isinstance(error, VersioningError):
        return "versioning_error"
    if error.__class__.__name__ in {"DomainValidationError", "TemporalValidationError"}:
        return "domain_validation_error"
    if error.__class__.__name__.startswith("Snapshot"):
        return "snapshot_error"
    if error.__class__.__name__.startswith(("SourceText", "CitationBinding", "LocatorResolution")):
        return "source_text_error"
    return "input_error"


def _emit(payload: dict[str, Any], stream: TextIO) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
