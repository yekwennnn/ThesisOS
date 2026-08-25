"""OpenAI Responses API adapter for the provider-neutral ThesisOS protocol.

The executable reads exactly one model-runtime envelope from stdin and writes
only the task-shaped JSON value to stdout. Credentials are read exclusively
from ``OPENAI_API_KEY`` and are never included in errors, logs, or provenance.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .model_runtime import PROTOCOL_NAME, PROTOCOL_VERSION
from .schema_validation import SchemaCatalog


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BASE_SECONDS = 0.5
DEFAULT_MAX_OUTPUT_TOKENS = 32_000
MAX_HTTP_RESPONSE_BYTES = 16 * 1024 * 1024
RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})


class OpenAIAdapterError(RuntimeError):
    """A secret-safe failure emitted by the adapter executable."""


class OpenAIAdapterInputError(OpenAIAdapterError):
    pass


class OpenAIAdapterHTTPError(OpenAIAdapterError):
    pass


class OpenAIAdapterResponseError(OpenAIAdapterError):
    pass


@dataclass(frozen=True)
class AdapterConfig:
    api_key: str
    base_url: str
    timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    max_output_tokens: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "AdapterConfig":
        api_key = environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise OpenAIAdapterInputError("OPENAI_API_KEY is not configured")
        base_url = environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        if not base_url.startswith("https://") and not _allows_test_http(environ, base_url):
            raise OpenAIAdapterInputError("OPENAI_BASE_URL must use HTTPS")
        return cls(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=_positive_float(
                environ,
                "THESISOS_OPENAI_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
                maximum=600,
            ),
            max_retries=_nonnegative_int(
                environ,
                "THESISOS_OPENAI_MAX_RETRIES",
                DEFAULT_MAX_RETRIES,
                maximum=5,
            ),
            retry_base_seconds=_nonnegative_float(
                environ,
                "THESISOS_OPENAI_RETRY_BASE_SECONDS",
                DEFAULT_RETRY_BASE_SECONDS,
                maximum=30,
            ),
            max_output_tokens=_positive_int(
                environ,
                "THESISOS_OPENAI_MAX_OUTPUT_TOKENS",
                DEFAULT_MAX_OUTPUT_TOKENS,
                maximum=200_000,
            ),
        )


def execute(
    envelope: Mapping[str, Any],
    *,
    config: AdapterConfig,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    schema_directory: str | Path | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    task = _validate_envelope(envelope)
    catalog = SchemaCatalog(schema_directory)
    response_schema = structured_output_schema(task, catalog)
    request_body = {
        "model": envelope["model_identifier"],
        "instructions": envelope["contract"]["content"],
        "input": json.dumps(
            {
                "request_metadata": envelope["request_metadata"],
                "inputs": envelope["inputs"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": (
                    "thesisos_evidence_batch"
                    if task == "evidence-extraction"
                    else "thesisos_thesis_diff"
                ),
                "strict": True,
                "schema": response_schema,
            }
        },
        "max_output_tokens": config.max_output_tokens,
        "store": False,
    }
    response = _post_responses(request_body, config, opener, sleeper)
    output = _parse_response(response, task)
    if task == "evidence-extraction":
        assert isinstance(output, list)
        for item in output:
            catalog.validate("Evidence", item)
    else:
        assert isinstance(output, dict)
        catalog.validate("ThesisDiff", output)
    return output


def structured_output_schema(task: str, catalog: SchemaCatalog) -> dict[str, Any]:
    """Return one self-contained schema derived from canonical schema files."""

    if task == "evidence-extraction":
        evidence = _bundle_external_schema(
            catalog.schemas["Evidence"],
            "citation.schema.json",
            catalog.schemas["Citation"],
            "citation",
        )
        definitions = evidence.pop("$defs", {})
        definitions["evidenceItem"] = evidence
        return {
            "type": "object",
            "required": ["evidence"],
            "properties": {
                "evidence": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/evidenceItem"},
                }
            },
            "additionalProperties": False,
            "$defs": definitions,
        }
    if task == "thesis-diff":
        return _bundle_external_schema(
            catalog.schemas["ThesisDiff"],
            "thesis-card.schema.json",
            catalog.schemas["ThesisCard"],
            "thesis_card",
        )
    raise OpenAIAdapterInputError(f"unsupported task: {task!r}")


def _bundle_external_schema(
    root: Mapping[str, Any],
    external_reference: str,
    external: Mapping[str, Any],
    namespace: str,
) -> dict[str, Any]:
    bundled = deepcopy(dict(root))
    external_value = deepcopy(dict(external))
    for metadata_key in ("$schema", "$id"):
        bundled.pop(metadata_key, None)
        external_value.pop(metadata_key, None)
    external_defs = external_value.pop("$defs", {})
    root_defs = bundled.setdefault("$defs", {})
    if not isinstance(root_defs, dict) or not isinstance(external_defs, dict):
        raise OpenAIAdapterInputError("canonical schema definitions are malformed")
    external_value = _rewrite_refs(external_value, "#/$defs/", f"#/$defs/{namespace}__")
    for name, definition in external_defs.items():
        root_defs[f"{namespace}__{name}"] = _rewrite_refs(
            definition, "#/$defs/", f"#/$defs/{namespace}__"
        )
    root_defs[namespace] = external_value
    return _replace_exact_ref(bundled, external_reference, f"#/$defs/{namespace}")


def _rewrite_refs(value: Any, old_prefix: str, new_prefix: str) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                new_prefix + item[len(old_prefix) :]
                if key == "$ref" and isinstance(item, str) and item.startswith(old_prefix)
                else _rewrite_refs(item, old_prefix, new_prefix)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_refs(item, old_prefix, new_prefix) for item in value]
    return value


def _replace_exact_ref(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                new
                if key == "$ref" and item == old
                else _replace_exact_ref(item, old, new)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_exact_ref(item, old, new) for item in value]
    return value


def _post_responses(
    body: Mapping[str, Any],
    config: AdapterConfig,
    opener: Callable[..., Any],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        f"{config.base_url}/responses",
        data=encoded,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last_failure = "OpenAI request failed"
    for attempt in range(config.max_retries + 1):
        try:
            with opener(request, timeout=config.timeout_seconds) as response:
                raw = _bounded_read(response)
                return _json_object(raw, "OpenAI response")
        except HTTPError as exc:
            retryable = exc.code in RETRYABLE_STATUS_CODES
            last_failure = f"OpenAI API returned HTTP {exc.code}"
            retry_after = _retry_after(exc.headers)
            exc.close()
        except (URLError, TimeoutError, socket.timeout):
            retryable = True
            retry_after = None
            last_failure = "OpenAI API request failed"
        if not retryable or attempt >= config.max_retries:
            raise OpenAIAdapterHTTPError(last_failure)
        delay = retry_after
        if delay is None:
            delay = config.retry_base_seconds * (2**attempt)
        sleeper(min(delay, 30.0))
    raise OpenAIAdapterHTTPError(last_failure)  # pragma: no cover


def _parse_response(response: Mapping[str, Any], task: str) -> dict[str, Any] | list[dict[str, Any]]:
    status = response.get("status")
    if status == "incomplete":
        details = response.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, Mapping) else "unknown"
        raise OpenAIAdapterResponseError(f"OpenAI response was incomplete: {reason}")
    if status != "completed":
        raise OpenAIAdapterResponseError(f"OpenAI response status was {status!r}")
    refusal = _find_refusal(response.get("output"))
    if refusal is not None:
        raise OpenAIAdapterResponseError("OpenAI model refused the request")
    text = response.get("output_text")
    if not isinstance(text, str) or not text.strip():
        text = _collect_output_text(response.get("output"))
    parsed = _json_value(text.encode("utf-8"), "structured model output")
    if task == "evidence-extraction":
        if not isinstance(parsed, dict) or set(parsed) != {"evidence"}:
            raise OpenAIAdapterResponseError("evidence response wrapper is invalid")
        evidence = parsed["evidence"]
        if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
            raise OpenAIAdapterResponseError("evidence response must contain an object array")
        return evidence
    if not isinstance(parsed, dict):
        raise OpenAIAdapterResponseError("thesis-diff response must be an object")
    return parsed


def _find_refusal(output: Any) -> str | None:
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "refusal":
                return str(part.get("refusal") or "refused")
    return None


def _collect_output_text(output: Any) -> str:
    chunks: list[str] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, Mapping) and part.get("type") == "output_text":
                        value = part.get("text")
                        if isinstance(value, str):
                            chunks.append(value)
    if not chunks:
        raise OpenAIAdapterResponseError("OpenAI response contained no structured output text")
    return "".join(chunks)


def _validate_envelope(envelope: Mapping[str, Any]) -> str:
    if envelope.get("protocol") != PROTOCOL_NAME or envelope.get("protocol_version") != PROTOCOL_VERSION:
        raise OpenAIAdapterInputError("unsupported model adapter protocol")
    task = envelope.get("task")
    if task not in {"evidence-extraction", "thesis-diff"}:
        raise OpenAIAdapterInputError(f"unsupported task: {task!r}")
    if not isinstance(envelope.get("model_identifier"), str) or not envelope["model_identifier"]:
        raise OpenAIAdapterInputError("model_identifier must be a non-empty string")
    contract = envelope.get("contract")
    if not isinstance(contract, Mapping) or not isinstance(contract.get("content"), str):
        raise OpenAIAdapterInputError("prompt contract is missing")
    for key in ("request_metadata", "inputs"):
        if not isinstance(envelope.get(key), Mapping):
            raise OpenAIAdapterInputError(f"{key} must be an object")
    return task


def _bounded_read(response: Any) -> bytes:
    data = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
    if len(data) > MAX_HTTP_RESPONSE_BYTES:
        raise OpenAIAdapterResponseError("OpenAI response exceeded the byte limit")
    return data


def _json_value(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenAIAdapterResponseError(f"{label} was not valid JSON") from exc


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    value = _json_value(raw, label)
    if not isinstance(value, dict):
        raise OpenAIAdapterResponseError(f"{label} must be an object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise OpenAIAdapterResponseError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _retry_after(headers: Message | None) -> float | None:
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return max(0.0, parsed)


def _positive_float(
    environ: Mapping[str, str], name: str, default: float, *, maximum: float
) -> float:
    return _bounded_number(
        environ, name, default, allow_zero=False, integer=False, maximum=maximum
    )


def _nonnegative_float(
    environ: Mapping[str, str], name: str, default: float, *, maximum: float
) -> float:
    return _bounded_number(
        environ, name, default, allow_zero=True, integer=False, maximum=maximum
    )


def _positive_int(
    environ: Mapping[str, str], name: str, default: int, *, maximum: int
) -> int:
    return int(
        _bounded_number(
            environ, name, default, allow_zero=False, integer=True, maximum=maximum
        )
    )


def _nonnegative_int(
    environ: Mapping[str, str], name: str, default: int, *, maximum: int
) -> int:
    return int(
        _bounded_number(
            environ, name, default, allow_zero=True, integer=True, maximum=maximum
        )
    )


def _bounded_number(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    allow_zero: bool,
    integer: bool,
    maximum: float,
) -> float:
    raw = environ.get(name)
    if raw is None:
        return float(default)
    try:
        value = float(int(raw)) if integer else float(raw)
    except ValueError as exc:
        raise OpenAIAdapterInputError(f"{name} has an invalid value") from exc
    if value < 0 or (value == 0 and not allow_zero) or value > maximum:
        raise OpenAIAdapterInputError(f"{name} has an invalid value")
    return value


def _allows_test_http(environ: Mapping[str, str], base_url: str) -> bool:
    return environ.get("THESISOS_OPENAI_ALLOW_INSECURE_TEST_URL") == "1" and base_url.startswith(
        ("http://127.0.0.1", "http://localhost")
    )


def main() -> int:
    try:
        envelope = _json_object(sys.stdin.buffer.read(), "adapter input")
        result = execute(envelope, config=AdapterConfig.from_env())
        json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (OpenAIAdapterError, ValueError, TypeError) as exc:
        sys.stderr.write(f"openai adapter error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
