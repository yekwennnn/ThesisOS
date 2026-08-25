"""Application services shared by HTTP and future queue/worker entry points."""

from __future__ import annotations

from copy import deepcopy
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .models import Evidence, SourceDocument, ThesisCard, ThesisDiff, UserReview
from .providers import ModelProvider, ObjectStorageProvider
from .schema_validation import SchemaCatalog
from .source_text import extract_source_text
from .source_text import verify_managed_citation
from .validation import (
    validate_evidence,
    validate_source_document,
    validate_thesis_card,
    validate_thesis_diff,
    validate_user_review,
)
from .versioning import (
    apply_user_review,
    commit_thesis_version,
    object_sha256,
    read_company_artifact,
    read_current_thesis,
    read_thesis_version,
    save_company_artifact,
    save_company_artifact_bundle,
)


class ApplicationInputError(ValueError):
    """A request is well-formed JSON but violates an application invariant."""


class ArtifactNotFoundError(ApplicationInputError):
    pass


class ThesisOSService:
    def __init__(
        self,
        *,
        workspace: Path,
        model_provider: ModelProvider,
        object_storage: ObjectStorageProvider,
        max_upload_bytes: int,
        max_source_chars: int,
    ):
        self.workspace = workspace
        self.model_provider = model_provider
        self.object_storage = object_storage
        self.max_upload_bytes = max_upload_bytes
        self.max_source_chars = max_source_chars
        self.schemas = SchemaCatalog()

    def ingest_source(self, metadata: dict[str, Any], content: bytes) -> dict[str, Any]:
        self.schemas.validate("SourceDocument", metadata)
        document = SourceDocument.from_dict(metadata)
        validate_source_document(document)
        if not content:
            raise ApplicationInputError("uploaded source must not be empty")
        if len(content) > self.max_upload_bytes:
            raise ApplicationInputError("uploaded source exceeds configured byte limit")
        existing = read_company_artifact(
            self.workspace, document.company_id, "documents", document.source_document_id
        )
        if existing is not None and existing != metadata:
            raise ApplicationInputError("immutable SourceDocument already exists with different metadata")
        suffix = {"pdf": ".pdf", "markdown": ".md", "plain_text": ".txt"}.get(
            document.media_type.value, ".bin"
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(content)
                temporary_path = Path(handle.name)
            snapshot = self.object_storage.ingest(self.workspace, temporary_path, metadata)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        if existing is None:
            save_company_artifact(
                self.workspace, document.company_id, "documents", document.source_document_id, metadata
            )
        return {
            "source_document": metadata,
            "record_sha256": object_sha256(metadata),
            "snapshot_sha256": snapshot.sha256,
            "byte_size": snapshot.byte_size,
            "object_created": snapshot.created,
        }

    def extract_evidence(
        self,
        company_id: str,
        source_document_id: str,
        run_id: str,
        request_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        _require_exact_metadata(
            request_metadata,
            {
                "analysis_cutoff_at",
                "evidence_id_prefix",
                "citation_id_prefix",
                "created_at",
                "extraction_scope",
            },
            "evidence extraction",
        )
        self._require_new_run(company_id, run_id)
        document_payload = self._artifact(company_id, "documents", source_document_id)
        document = SourceDocument.from_dict(document_payload)
        validate_source_document(document)
        cutoff = _aware_datetime(request_metadata.get("analysis_cutoff_at"), "analysis_cutoff_at")
        if document.publicly_available_at > cutoff:
            raise ApplicationInputError("source document was not public at analysis cutoff")
        view = extract_source_text(self.workspace, document_payload)
        source_text = "\n".join(line.text for line in view.lines)
        if len(source_text) > self.max_source_chars:
            raise ApplicationInputError("source text exceeds configured extraction limit")
        current = read_current_thesis(self.workspace, company_id)
        run = self.model_provider.run(
            task="evidence-extraction",
            request_metadata=request_metadata,
            inputs={
                "source_document": document_payload,
                "document_content": {
                    "media_type": view.media_type,
                    "page_count": view.page_count,
                    "text": source_text,
                },
                "existing_thesis_context": current,
            },
        )
        if not isinstance(run.output, list):
            raise ApplicationInputError("evidence provider must return an object array")
        citation_checks: list[dict[str, Any]] = []
        evidence_ids: set[str] = set()
        citation_ids: set[str] = set()
        for payload in run.output:
            self.schemas.validate("Evidence", payload)
            evidence = Evidence.from_dict(payload)
            validate_evidence(evidence, {document.source_document_id: document}, cutoff)
            if evidence.company_id != company_id:
                raise ApplicationInputError("provider returned Evidence for another company")
            if evidence.verification_status.value != "unreviewed":
                raise ApplicationInputError("extracted Evidence must be unreviewed")
            if not evidence.evidence_id.startswith(request_metadata["evidence_id_prefix"]):
                raise ApplicationInputError("Evidence does not use the requested ID prefix")
            if evidence.evidence_id in evidence_ids:
                raise ApplicationInputError("provider returned duplicate Evidence IDs")
            evidence_ids.add(evidence.evidence_id)
            if payload.get("created_at") != request_metadata["created_at"]:
                raise ApplicationInputError("Evidence must copy request created_at")
            if {item.source_document_id for item in evidence.citations} != {source_document_id}:
                raise ApplicationInputError("extracted Evidence must cite only the selected source")
            for citation in evidence.citations:
                if not citation.citation_id.startswith(request_metadata["citation_id_prefix"]):
                    raise ApplicationInputError("Citation does not use the requested ID prefix")
                if citation.citation_id in citation_ids:
                    raise ApplicationInputError("provider returned duplicate Citation IDs")
                citation_ids.add(citation.citation_id)
                check = verify_managed_citation(
                    self.workspace, document_payload, citation.to_dict()
                )
                if not check.passed and not check.requires_human_review:
                    raise ApplicationInputError(
                        f"citation {citation.citation_id} does not match the immutable source"
                    )
                citation_checks.append(check.to_dict())
        run_record = _model_run_record(
            run_id,
            company_id,
            "evidence-extraction",
            request_metadata,
            {"source_document_ids": [source_document_id]},
            run,
        )
        save_company_artifact(
            self.workspace, company_id, "model_runs", run_id, run_record
        )
        return {
            "model_run": run_record,
            "evidence": run.output,
            "citation_text_checks": citation_checks,
        }

    def review_evidence(
        self,
        company_id: str,
        run_id: str,
        evidence_id: str,
        review_payload: dict[str, Any],
    ) -> dict[str, Any]:
        required = {
            "evidence_review_id",
            "model_run_id",
            "evidence_id",
            "decision",
            "reviewer_id",
            "reviewed_at",
        }
        allowed = required | {"corrected_statement"}
        unknown = set(review_payload) - allowed
        missing = required - set(review_payload)
        if missing or unknown:
            raise ApplicationInputError("evidence review fields are invalid")
        for key in required:
            if not isinstance(review_payload[key], str) or not review_payload[key].strip():
                raise ApplicationInputError(f"evidence review.{key} must be a non-empty string")
        if review_payload["model_run_id"] != run_id or review_payload["evidence_id"] != evidence_id:
            raise ApplicationInputError("evidence review identity does not match the route")
        _aware_datetime(review_payload["reviewed_at"], "reviewed_at")
        decision = review_payload["decision"]
        if decision not in {"confirm", "reject", "correct_statement"}:
            raise ApplicationInputError("unsupported evidence review decision")
        corrected = review_payload.get("corrected_statement")
        if decision == "correct_statement":
            if not isinstance(corrected, str) or not corrected.strip():
                raise ApplicationInputError("correct_statement requires corrected_statement")
        elif corrected is not None:
            raise ApplicationInputError("corrected_statement is only allowed when correcting")

        run_record = self._artifact(company_id, "model_runs", run_id)
        if run_record.get("task") != "evidence-extraction":
            raise ApplicationInputError("model run is not an evidence extraction run")
        output = run_record.get("output")
        if not isinstance(output, list):
            raise ApplicationInputError("model run does not contain evidence output")
        matches = [item for item in output if isinstance(item, dict) and item.get("evidence_id") == evidence_id]
        if len(matches) != 1:
            raise ArtifactNotFoundError("Evidence draft not found in model run")
        admitted = deepcopy(matches[0])
        if admitted.get("verification_status") != "unreviewed":
            raise ApplicationInputError("model-run Evidence draft is not unreviewed")
        if decision == "correct_statement":
            admitted["statement"] = corrected.strip()
        admitted["verification_status"] = "rejected" if decision == "reject" else "verified"
        self.schemas.validate("Evidence", admitted)
        evidence = Evidence.from_dict(admitted)
        document_ids = {citation.source_document_id for citation in evidence.citations}
        documents: dict[str, SourceDocument] = {}
        citation_checks: list[dict[str, Any]] = []
        for document_id in document_ids:
            document_payload = self._artifact(company_id, "documents", document_id)
            documents[document_id] = SourceDocument.from_dict(document_payload)
            for citation in evidence.citations:
                if citation.source_document_id != document_id:
                    continue
                check = verify_managed_citation(
                    self.workspace, document_payload, citation.to_dict()
                )
                if not check.passed and not check.requires_human_review:
                    raise ApplicationInputError(
                        f"citation {citation.citation_id} does not match the immutable source"
                    )
                citation_checks.append(check.to_dict())
        validate_evidence(evidence, documents)
        persisted_review = {
            **review_payload,
            "company_id": company_id,
            "source_model_run_provenance": run_record.get("provenance"),
            "admitted_evidence_sha256": object_sha256(admitted),
        }
        save_company_artifact_bundle(
            self.workspace,
            company_id,
            [
                ("reviews", review_payload["evidence_review_id"], persisted_review),
                ("evidence", evidence_id, admitted),
            ],
        )
        return {
            "review": persisted_review,
            "evidence": admitted,
            "citation_text_checks": citation_checks,
        }

    def generate_diff(
        self,
        company_id: str,
        run_id: str,
        request_metadata: dict[str, Any],
        source_document_ids: list[str],
        evidence_ids: list[str],
        prior_evidence_ids: list[str],
    ) -> dict[str, Any]:
        _require_exact_metadata(
            request_metadata,
            {
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
            },
            "thesis diff",
        )
        self._require_new_run(company_id, run_id)
        if set(evidence_ids) & set(prior_evidence_ids):
            raise ApplicationInputError("new and prior Evidence sets must not overlap")
        current_payload = read_current_thesis(self.workspace, company_id)
        if current_payload is None:
            raise ArtifactNotFoundError("company has no current ThesisCard")
        base = ThesisCard.from_dict(current_payload)
        validate_thesis_card(base)
        document_payloads = {
            item: self._artifact(company_id, "documents", item) for item in source_document_ids
        }
        documents = {key: SourceDocument.from_dict(value) for key, value in document_payloads.items()}
        selected_evidence = list(dict.fromkeys([*prior_evidence_ids, *evidence_ids]))
        validation_evidence = list(
            dict.fromkeys(
                [*selected_evidence, *base.strongest_counter_case.evidence_ids]
            )
        )
        evidence_payloads = {
            item: self._artifact(company_id, "evidence", item)
            for item in validation_evidence
        }
        evidences = {key: Evidence.from_dict(value) for key, value in evidence_payloads.items()}
        cited_document_ids = {
            citation.source_document_id
            for evidence in evidences.values()
            for citation in evidence.citations
        }
        for document_id in sorted(cited_document_ids - set(documents)):
            payload = self._artifact(company_id, "documents", document_id)
            document_payloads[document_id] = payload
            documents[document_id] = SourceDocument.from_dict(payload)
        cutoff = _aware_datetime(request_metadata.get("analysis_cutoff_at"), "analysis_cutoff_at")
        for evidence in evidences.values():
            validate_evidence(evidence, documents, cutoff)
            if evidence.verification_status.value != "verified":
                raise ApplicationInputError("diff generation requires verified Evidence")
        run = self.model_provider.run(
            task="thesis-diff",
            request_metadata=request_metadata,
            inputs={
                "base_thesis_card": current_payload,
                "prior_evidence_for_say_do_comparison": [
                    evidence_payloads[item] for item in prior_evidence_ids
                ],
                "new_source_documents": [document_payloads[item] for item in source_document_ids],
                "new_evidence": [evidence_payloads[item] for item in evidence_ids],
            },
        )
        if not isinstance(run.output, dict):
            raise ApplicationInputError("diff provider must return one object")
        self.schemas.validate("ThesisDiff", run.output)
        diff = ThesisDiff.from_dict(run.output)
        validate_thesis_diff(diff, base, evidences, documents)
        if diff.company_id != company_id:
            raise ApplicationInputError("provider returned ThesisDiff for another company")
        if diff.thesis_diff_id != request_metadata["thesis_diff_id"]:
            raise ApplicationInputError("ThesisDiff ID does not match request metadata")
        if set(diff.source_document_ids) != set(source_document_ids):
            raise ApplicationInputError("ThesisDiff source documents do not match the request")
        run_record = _model_run_record(
            run_id,
            company_id,
            "thesis-diff",
            request_metadata,
            {
                "base_version_id": base.version.version_id,
                "source_document_ids": source_document_ids,
                "prior_evidence_ids": prior_evidence_ids,
                "new_evidence_ids": evidence_ids,
            },
            run,
        )
        save_company_artifact_bundle(
            self.workspace,
            company_id,
            [("model_runs", run_id, run_record), ("diffs", diff.thesis_diff_id, run.output)],
        )
        return {"model_run": run_record, "thesis_diff": run.output}

    def review_diff(self, company_id: str, diff_id: str, review_payload: dict[str, Any]) -> dict[str, Any]:
        diff_payload = self._artifact(company_id, "diffs", diff_id)
        self.schemas.validate("UserReview", review_payload)
        diff = ThesisDiff.from_dict(diff_payload)
        review = UserReview.from_dict(review_payload)
        validate_user_review(review, diff)
        return apply_user_review(self.workspace, diff_payload, review_payload)

    def commit_thesis(self, thesis_payload: dict[str, Any]) -> dict[str, Any]:
        self.schemas.validate("ThesisCard", thesis_payload)
        thesis = ThesisCard.from_dict(thesis_payload)
        validate_thesis_card(thesis)
        if not thesis.version.user_confirmed:
            raise ApplicationInputError("committed thesis must be user confirmed")
        return commit_thesis_version(self.workspace, thesis_payload)

    def current_thesis(self, company_id: str) -> dict[str, Any]:
        payload = read_current_thesis(self.workspace, company_id)
        if payload is None:
            raise ArtifactNotFoundError("current ThesisCard not found")
        return payload

    def thesis_version(self, company_id: str, version_id: str) -> dict[str, Any]:
        payload = read_thesis_version(self.workspace, company_id, version_id)
        if payload is None:
            raise ArtifactNotFoundError("ThesisCard version not found")
        return payload

    def artifact(self, company_id: str, kind: str, artifact_id: str) -> dict[str, Any]:
        return self._artifact(company_id, kind, artifact_id)

    def _artifact(self, company_id: str, kind: str, artifact_id: str) -> dict[str, Any]:
        payload = read_company_artifact(self.workspace, company_id, kind, artifact_id)
        if payload is None:
            raise ArtifactNotFoundError(f"{kind}/{artifact_id} not found")
        return payload

    def _require_new_run(self, company_id: str, run_id: str) -> None:
        if read_company_artifact(
            self.workspace, company_id, "model_runs", run_id
        ) is not None:
            raise ApplicationInputError(f"model run {run_id!r} already exists")


def _aware_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ApplicationInputError(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ApplicationInputError(f"{name} must be an ISO-8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApplicationInputError(f"{name} must include a timezone")
    return parsed


def _require_exact_metadata(
    metadata: Mapping[str, Any], required: set[str], label: str
) -> None:
    actual = set(metadata)
    if actual != required:
        missing = sorted(required - actual)
        unknown = sorted(actual - required)
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if unknown:
            detail.append("unknown: " + ", ".join(unknown))
        raise ApplicationInputError(f"{label} metadata fields are invalid ({'; '.join(detail)})")
    for key in required - {"extraction_scope"}:
        if not isinstance(metadata[key], str) or not metadata[key].strip():
            raise ApplicationInputError(f"{label}.{key} must be a non-empty string")
    if "extraction_scope" in required:
        scope = metadata["extraction_scope"]
        if (
            not isinstance(scope, list)
            or not 1 <= len(scope) <= 100
            or any(
                not isinstance(item, str) or not item.strip() or len(item) > 1_000
                for item in scope
            )
        ):
            raise ApplicationInputError(
                "extraction_scope must contain 1-100 non-empty strings of at most 1000 characters"
            )


def _model_run_record(
    run_id: str,
    company_id: str,
    task: str,
    metadata: Mapping[str, Any],
    input_references: Mapping[str, Any],
    run: Any,
) -> dict[str, Any]:
    return {
        "format": "thesisos-model-run",
        "format_version": 1,
        "model_run_id": run_id,
        "company_id": company_id,
        "task": task,
        "request_metadata": dict(metadata),
        "input_references": dict(input_references),
        "output": run.output,
        "provenance": run.provenance.to_dict(),
    }
