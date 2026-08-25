"""FastAPI transport for the auditable ThesisOS application service."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from .application import ApplicationInputError, ArtifactNotFoundError, ThesisOSService
from .providers import (
    AppSettings,
    DisabledFinanceProvider,
    FinanceProvider,
    FinanceProviderError,
    LocalObjectStorageProvider,
    ProviderConfigurationError,
    SubprocessModelProvider,
    WindCliFinanceProvider,
)


def create_app(
    settings: AppSettings | None = None,
    *,
    service: ThesisOSService | None = None,
    finance_provider: FinanceProvider | None = None,
) -> FastAPI:
    configured = settings or AppSettings.from_env()
    if configured.object_storage_provider != "local":
        raise ProviderConfigurationError(
            "unsupported object storage provider; inject an ObjectStorageProvider implementation"
        )
    model = SubprocessModelProvider(
        configured.model_adapter_argv,
        configured.model_identifier,
        configured.model_timeout_seconds,
    )
    storage = LocalObjectStorageProvider()
    finance = finance_provider or _configured_finance_provider(configured)
    app_service = service or ThesisOSService(
        workspace=configured.workspace,
        model_provider=model,
        object_storage=storage,
        max_upload_bytes=configured.max_upload_bytes,
        max_source_chars=configured.max_source_chars,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configured.workspace.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(
        title="ThesisOS API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = configured
    app.state.service = app_service
    app.state.finance_provider = finance

    @app.exception_handler(ArtifactNotFoundError)
    async def not_found(_: Request, exc: ArtifactNotFoundError) -> JSONResponse:
        return _error(404, "not_found", str(exc))

    async def bad_request(_: Request, exc: Exception) -> JSONResponse:
        return _error(422, "invalid_request", str(exc))

    app.add_exception_handler(ApplicationInputError, bad_request)
    app.add_exception_handler(ValueError, bad_request)
    app.add_exception_handler(TypeError, bad_request)

    @app.exception_handler(ProviderConfigurationError)
    async def provider_error(_: Request, exc: ProviderConfigurationError) -> JSONResponse:
        return _error(503, "provider_unavailable", str(exc))

    @app.exception_handler(FinanceProviderError)
    async def finance_error(_: Request, exc: FinanceProviderError) -> JSONResponse:
        return _error(502, "finance_provider_error", str(exc))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "thesisos",
            "providers": {
                "model": {"configured": model.ready, "available": model.ready},
                "finance": {"configured": finance.ready, "available": finance.ready},
                "object_storage": {
                    "configured": True,
                    "available": storage.ready,
                    "kind": "local",
                },
            },
        }

    @app.get("/v1/finance/instruments/resolve")
    async def resolve_instrument(symbol: str) -> dict[str, Any]:
        normalized = symbol.strip().upper()
        if not 1 <= len(normalized) <= 32 or _SYMBOL_RE.fullmatch(normalized) is None:
            raise ApplicationInputError(
                "symbol must be 1-32 letters, digits, dots, hyphens, colons, or carets"
            )
        if not finance.ready:
            raise ProviderConfigurationError("finance provider is not configured")
        result = finance.resolve_instrument(normalized)
        if not isinstance(result, dict):
            result = dict(result)
        return {"query": normalized, "instrument": result}

    @app.post("/v1/sources/ingest", status_code=201)
    async def ingest_source(
        metadata: str = Form(...), source: UploadFile = File(...)
    ) -> dict[str, Any]:
        try:
            payload = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise ApplicationInputError("metadata must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ApplicationInputError("metadata must be a JSON object")
        content = await source.read(configured.max_upload_bytes + 1)
        return app_service.ingest_source(payload, content)

    @app.post("/v1/companies/{company_id}/evidence/extract", status_code=201)
    async def extract_evidence(company_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return app_service.extract_evidence(
            company_id,
            _required_text(body, "source_document_id"),
            _required_text(body, "model_run_id"),
            _required_object(body, "request_metadata"),
        )

    @app.get("/v1/companies/{company_id}/evidence/{evidence_id}")
    async def get_evidence(company_id: str, evidence_id: str) -> dict[str, Any]:
        return app_service.artifact(company_id, "evidence", evidence_id)

    @app.post(
        "/v1/companies/{company_id}/model-runs/{run_id}/evidence/{evidence_id}/review",
        status_code=201,
    )
    async def review_evidence(
        company_id: str,
        run_id: str,
        evidence_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return app_service.review_evidence(company_id, run_id, evidence_id, body)

    @app.post("/v1/companies/{company_id}/diffs", status_code=201)
    async def generate_diff(company_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return app_service.generate_diff(
            company_id,
            _required_text(body, "model_run_id"),
            _required_object(body, "request_metadata"),
            _string_list(body, "source_document_ids"),
            _string_list(body, "evidence_ids"),
            _string_list(body, "prior_evidence_ids", required=False),
        )

    @app.get("/v1/companies/{company_id}/diffs/{diff_id}")
    async def get_diff(company_id: str, diff_id: str) -> dict[str, Any]:
        return app_service.artifact(company_id, "diffs", diff_id)

    @app.post("/v1/companies/{company_id}/diffs/{diff_id}/reviews", status_code=201)
    async def review_diff(company_id: str, diff_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return app_service.review_diff(company_id, diff_id, body)

    @app.post("/v1/theses", status_code=201)
    async def commit_thesis(body: dict[str, Any]) -> dict[str, Any]:
        return app_service.commit_thesis(body)

    @app.get("/v1/companies/{company_id}/theses/current")
    async def current_thesis(company_id: str) -> dict[str, Any]:
        return app_service.current_thesis(company_id)

    @app.get("/v1/companies/{company_id}/theses/versions/{version_id}")
    async def thesis_version(company_id: str, version_id: str) -> dict[str, Any]:
        return app_service.thesis_version(company_id, version_id)

    return app


def _configured_finance_provider(settings: AppSettings) -> FinanceProvider:
    if settings.finance_provider == "disabled":
        return DisabledFinanceProvider()
    if settings.finance_provider == "wind-cli":
        if not settings.wind_cli_argv:
            raise ProviderConfigurationError(
                "THESISOS_FINANCE_PROVIDER=wind-cli requires THESISOS_WIND_CLI_ARGV"
            )
        return WindCliFinanceProvider(
            settings.wind_cli_argv,
            timeout_seconds=settings.wind_timeout_seconds,
            max_stdout_bytes=settings.wind_max_stdout_bytes,
        )
    raise ProviderConfigurationError(
        "THESISOS_FINANCE_PROVIDER must be disabled or wind-cli"
    )


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-:^]*$")


def _required_text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApplicationInputError(f"{key} must be a non-empty string")
    return value


def _required_object(body: dict[str, Any], key: str) -> dict[str, Any]:
    value = body.get(key)
    if not isinstance(value, dict):
        raise ApplicationInputError(f"{key} must be an object")
    return value


def _string_list(body: dict[str, Any], key: str, *, required: bool = True) -> list[str]:
    value = body.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ApplicationInputError(f"{key} must be a string array")
    if len(value) != len(set(value)):
        raise ApplicationInputError(f"{key} must not contain duplicates")
    return value


def run() -> None:
    import uvicorn

    uvicorn.run("thesisos.api:create_app", factory=True, host="127.0.0.1", port=8000)
