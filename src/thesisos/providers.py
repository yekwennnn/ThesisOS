"""Provider boundaries and environment-only application configuration.

Provider credentials deliberately do not appear in this module. Deployments
may supply credential-bearing environment variables to their adapter process,
finance implementation, or remote object-store implementation.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .model_runtime import ModelRunResult, run_model_adapter
from .snapshots import SnapshotIngestResult, ingest_snapshot


class ProviderConfigurationError(RuntimeError):
    """A configured application provider cannot be constructed safely."""


class FinanceProviderError(RuntimeError):
    """A finance provider failed without exposing its command or credentials."""


@runtime_checkable
class ModelProvider(Protocol):
    @property
    def ready(self) -> bool: ...

    def run(
        self,
        *,
        task: str,
        request_metadata: Mapping[str, Any],
        inputs: Mapping[str, Any],
    ) -> ModelRunResult: ...


@runtime_checkable
class FinanceProvider(Protocol):
    @property
    def ready(self) -> bool: ...

    def resolve_instrument(self, symbol: str) -> Mapping[str, Any]: ...


@runtime_checkable
class ObjectStorageProvider(Protocol):
    @property
    def ready(self) -> bool: ...

    def ingest(
        self,
        workspace: Path,
        source_file: Path,
        source_document: Mapping[str, Any],
    ) -> SnapshotIngestResult: ...


@dataclass(frozen=True)
class AppSettings:
    workspace: Path
    model_identifier: str
    model_adapter_argv: tuple[str, ...]
    model_timeout_seconds: float = 120.0
    max_upload_bytes: int = 50 * 1024 * 1024
    max_source_chars: int = 500_000
    object_storage_provider: str = "local"
    finance_provider: str = "disabled"
    wind_cli_argv: tuple[str, ...] = ()
    wind_timeout_seconds: float = 60.0
    wind_max_stdout_bytes: int = 2 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "AppSettings":
        return cls(
            workspace=Path(os.environ.get("THESISOS_WORKSPACE", ".thesisos-workspace")),
            model_identifier=os.environ.get("THESISOS_MODEL_IDENTIFIER", "unconfigured"),
            model_adapter_argv=_string_array_env("THESISOS_MODEL_ADAPTER_ARGV"),
            model_timeout_seconds=_positive_float("THESISOS_MODEL_TIMEOUT_SECONDS", 120.0),
            max_upload_bytes=_positive_int("THESISOS_MAX_UPLOAD_BYTES", 50 * 1024 * 1024),
            max_source_chars=_positive_int("THESISOS_MAX_SOURCE_CHARS", 500_000),
            object_storage_provider=os.environ.get(
                "THESISOS_OBJECT_STORAGE_PROVIDER", "local"
            ),
            finance_provider=os.environ.get(
                "THESISOS_FINANCE_PROVIDER", "disabled"
            ).strip().lower(),
            wind_cli_argv=_string_array_env("THESISOS_WIND_CLI_ARGV"),
            wind_timeout_seconds=_positive_float(
                "THESISOS_WIND_TIMEOUT_SECONDS", 60.0
            ),
            wind_max_stdout_bytes=_positive_int(
                "THESISOS_WIND_MAX_STDOUT_BYTES", 2 * 1024 * 1024
            ),
        )


@dataclass(frozen=True)
class SubprocessModelProvider:
    adapter_argv: Sequence[str]
    model_identifier: str
    timeout_seconds: float = 120.0

    @property
    def ready(self) -> bool:
        return bool(self.adapter_argv) and self.model_identifier != "unconfigured"

    def run(
        self,
        *,
        task: str,
        request_metadata: Mapping[str, Any],
        inputs: Mapping[str, Any],
    ) -> ModelRunResult:
        if not self.ready:
            raise ProviderConfigurationError("model provider is not configured")
        return run_model_adapter(
            self.adapter_argv,
            task=task,
            model_identifier=self.model_identifier,
            request_metadata=request_metadata,
            inputs=inputs,
            timeout_seconds=self.timeout_seconds,
        )


class DisabledFinanceProvider:
    @property
    def ready(self) -> bool:
        return False

    def resolve_instrument(self, symbol: str) -> Mapping[str, Any]:
        raise ProviderConfigurationError("finance provider is not configured")


@dataclass(frozen=True)
class WindCliFinanceProvider:
    """Resolve stock identities through Wind's bounded local CLI contract.

    ``cli_argv`` is the executable prefix only, for example
    ``("node", "/opt/wind-mcp-skill/scripts/cli.mjs")``. The provider appends
    the fixed ``call stock_data get_stock_basicinfo`` route and one canonical
    JSON parameter argument. It never invokes a shell and never returns the
    command, environment, stderr, or raw provider payload.
    """

    cli_argv: Sequence[str]
    timeout_seconds: float = 60.0
    max_stdout_bytes: int = 2 * 1024 * 1024

    def __post_init__(self) -> None:
        argv = tuple(self.cli_argv)
        if any(
            not isinstance(item, str) or not item or "\x00" in item
            for item in argv
        ):
            raise ProviderConfigurationError(
                "THESISOS_WIND_CLI_ARGV must contain non-empty strings"
            )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or float(self.timeout_seconds) <= 0
        ):
            raise ProviderConfigurationError(
                "THESISOS_WIND_TIMEOUT_SECONDS must be positive"
            )
        if (
            isinstance(self.max_stdout_bytes, bool)
            or not isinstance(self.max_stdout_bytes, int)
            or self.max_stdout_bytes < 1
        ):
            raise ProviderConfigurationError(
                "THESISOS_WIND_MAX_STDOUT_BYTES must be a positive integer"
            )
        object.__setattr__(self, "cli_argv", argv)

    @property
    def ready(self) -> bool:
        return bool(self.cli_argv)

    def resolve_instrument(self, symbol: str) -> Mapping[str, Any]:
        normalized = symbol.strip().upper() if isinstance(symbol, str) else ""
        if _WIND_SYMBOL_RE.fullmatch(normalized) is None:
            raise FinanceProviderError("finance provider symbol is invalid")
        if not self.ready:
            raise ProviderConfigurationError("Wind CLI provider is not configured")

        params = json.dumps(
            {"question": f"查询股票（{normalized}）的基本档案"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        raw = _run_bounded_wind_cli(
            (
                *self.cli_argv,
                "call",
                "stock_data",
                "get_stock_basicinfo",
                params,
            ),
            timeout_seconds=float(self.timeout_seconds),
            max_stdout_bytes=self.max_stdout_bytes,
        )
        payload = _strict_json_bytes(raw, "Wind CLI stdout")
        payload = _unwrap_wind_content(payload)
        instrument = _normalize_wind_instrument(payload, normalized)
        return {
            **instrument,
            "provider": "wind",
            "as_of": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }


class LocalObjectStorageProvider:
    @property
    def ready(self) -> bool:
        return True

    def ingest(
        self,
        workspace: Path,
        source_file: Path,
        source_document: Mapping[str, Any],
    ) -> SnapshotIngestResult:
        return ingest_snapshot(workspace, source_file, source_document)


_WIND_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-:^]{0,31}$")
_PROVIDER_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_READ_CHUNK_SIZE = 64 * 1024
_MAX_STDERR_BYTES = 64 * 1024


def _normalized_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(character for character in text if character.isalnum())


_INSTRUMENT_FIELD_ALIASES = {
    "symbol": {
        "windcode",
        "证券代码",
        "股票代码",
        "代码",
        "symbol",
        "ticker",
        "secucode",
        "sinfowindcode",
    },
    "name": {
        "中文简称",
        "证券简称",
        "股票简称",
        "公司简称",
        "公司名称",
        "名称",
        "name",
        "secname",
        "secuname",
        "shortname",
        "sname",
        "compname",
        "companyname",
        "sinfoname",
        "sinfocompname",
    },
    "exchange": {
        "交易所",
        "交易所名称",
        "上市交易所",
        "exchange",
        "exchangename",
        "exch",
        "sinfoexchmarket",
    },
    "market": {
        "市场",
        "上市板块",
        "板块",
        "market",
        "board",
        "sinfolistboardname",
    },
    "industry": {
        "行业",
        "wind行业",
        "wind行业名称",
        "所属行业",
        "industry",
        "industryname",
    },
    "status": {"上市状态", "当前状态", "交易状态", "status", "listingstatus"},
}
_NORMALIZED_FIELD_ALIASES = {
    field: {_normalized_key(alias) for alias in aliases}
    for field, aliases in _INSTRUMENT_FIELD_ALIASES.items()
}


def _run_bounded_wind_cli(
    argv: tuple[str, ...], *, timeout_seconds: float, max_stdout_bytes: int
) -> bytes:
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    stdout_overflow = threading.Event()
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=(os.name == "posix"),
        )
    except (OSError, ValueError):
        raise FinanceProviderError("finance provider process could not be started") from None

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_reader = threading.Thread(
        target=_drain_bounded_stream,
        args=(process.stdout, stdout_buffer, max_stdout_bytes, stdout_overflow),
        daemon=True,
    )
    stderr_reader = threading.Thread(
        target=_drain_bounded_stream,
        args=(process.stderr, stderr_buffer, _MAX_STDERR_BYTES, None),
        daemon=True,
    )
    stdout_reader.start()
    stderr_reader.start()

    deadline = time.monotonic() + timeout_seconds
    failure: str | None = None
    while process.poll() is None:
        if stdout_overflow.is_set():
            failure = "stdout_limit"
            _kill_process_tree(process)
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failure = "timeout"
            _kill_process_tree(process)
            break
        stdout_overflow.wait(min(0.05, remaining))

    try:
        return_code = process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        return_code = process.wait()
    _finish_stream_readers(process, stdout_reader, stderr_reader)

    if failure == "timeout":
        raise FinanceProviderError("finance provider request timed out")
    if failure == "stdout_limit" or stdout_overflow.is_set():
        raise FinanceProviderError("finance provider response exceeded its byte limit")
    if return_code != 0:
        code = _safe_provider_error_code(bytes(stdout_buffer))
        raise FinanceProviderError(f"finance provider request failed ({code})")
    return bytes(stdout_buffer)


def _drain_bounded_stream(
    stream: Any,
    destination: bytearray,
    limit: int,
    overflow: threading.Event | None,
) -> None:
    try:
        while True:
            chunk = stream.read(_READ_CHUNK_SIZE)
            if not chunk:
                return
            remaining = limit - len(destination)
            if remaining > 0:
                destination.extend(chunk[:remaining])
            if len(chunk) > remaining and overflow is not None:
                overflow.set()
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _finish_stream_readers(
    process: subprocess.Popen[bytes],
    stdout_reader: threading.Thread,
    stderr_reader: threading.Thread,
) -> None:
    stdout_reader.join(timeout=0.5)
    stderr_reader.join(timeout=0.5)
    if stdout_reader.is_alive() or stderr_reader.is_alive():
        _kill_process_tree(process)
        stdout_reader.join(timeout=0.5)
        stderr_reader.join(timeout=0.5)


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _strict_json_bytes(raw: bytes, label: str) -> Any:
    if not raw:
        raise FinanceProviderError(f"{label} was empty")
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise FinanceProviderError(f"{label} was not strict JSON") from None


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON numeric constant: {value}")


def _safe_provider_error_code(raw: bytes) -> str:
    try:
        payload = _strict_json_bytes(raw, "Wind CLI error")
    except FinanceProviderError:
        return "process_error"
    if isinstance(payload, dict):
        code = payload.get("code")
        if isinstance(code, str) and _PROVIDER_ERROR_CODE_RE.fullmatch(code):
            return code
    return "process_error"


def _unwrap_wind_content(payload: Any) -> Any:
    if isinstance(payload, dict) and payload.get("ok") is False:
        code = payload.get("code")
        safe_code = (
            code
            if isinstance(code, str) and _PROVIDER_ERROR_CODE_RE.fullmatch(code)
            else "provider_error"
        )
        raise FinanceProviderError(f"finance provider request failed ({safe_code})")
    if not isinstance(payload, dict):
        raise FinanceProviderError("finance provider returned an invalid result envelope")
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        raise FinanceProviderError("finance provider returned no structured content")
    first = content[0]
    if not isinstance(first, dict) or not isinstance(first.get("text"), str):
        raise FinanceProviderError("finance provider returned no structured text content")
    text = first["text"]
    try:
        value: Any = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if isinstance(value, str):
            value = json.loads(
                value,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        return value
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise FinanceProviderError(
            "finance provider structured content was not strict JSON"
        ) from None


def _normalize_wind_instrument(payload: Any, requested_symbol: str) -> dict[str, str]:
    records: list[dict[str, str]] = []
    for candidate in _instrument_candidates(payload):
        compact = _compact_instrument(candidate)
        if "symbol" in compact and "name" in compact and compact not in records:
            records.append(compact)
    exact = [
        item for item in records if item["symbol"].strip().upper() == requested_symbol
    ]
    selected = (
        exact[0]
        if len(exact) == 1
        else records[0]
        if not exact and len(records) == 1
        else None
    )
    if selected is None:
        raise FinanceProviderError(
            "finance provider did not return one unambiguous instrument"
        )
    selected["symbol"] = selected["symbol"].strip().upper()
    return selected


def _instrument_candidates(
    value: Any, *, depth: int = 0, budget: list[int] | None = None
) -> list[Mapping[str, Any]]:
    remaining = budget if budget is not None else [2_000]
    if depth > 12 or remaining[0] <= 0:
        return []
    remaining[0] -= 1
    result: list[Mapping[str, Any]] = []
    if isinstance(value, dict):
        if _mapping_has_instrument_field(value):
            result.append(value)
        result.extend(_table_candidates(value))
        for child in value.values():
            result.extend(
                _instrument_candidates(child, depth=depth + 1, budget=remaining)
            )
    elif isinstance(value, list):
        for child in value:
            result.extend(
                _instrument_candidates(child, depth=depth + 1, budget=remaining)
            )
    return result


def _table_candidates(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    columns = value.get("columns")
    rows = value.get("rows")
    if not isinstance(rows, list):
        rows = value.get("value")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return []
    column_specs = [_column_spec(item) for item in columns]
    result: list[Mapping[str, Any]] = []
    for row in rows[:100]:
        if isinstance(row, list) and len(row) == len(column_specs):
            result.append(
                {
                    label: cell
                    for (_, label), cell in zip(column_specs, row)
                    if label is not None
                }
            )
        elif isinstance(row, dict):
            mapped: dict[str, Any] = {}
            for source_key, label in column_specs:
                if source_key is not None and label is not None and source_key in row:
                    mapped[label] = row[source_key]
            if mapped:
                result.append(mapped)
            result.append(row)
    return result


def _column_spec(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, str) and value.strip():
        return value, value
    if not isinstance(value, dict):
        return None, None
    source = next(
        (
            value.get(key)
            for key in ("key", "field", "code", "id", "name")
            if isinstance(value.get(key), str) and value.get(key).strip()
        ),
        None,
    )
    label = next(
        (
            value.get(key)
            for key in ("name", "title", "label", "display_name", "field", "key")
            if isinstance(value.get(key), str) and value.get(key).strip()
        ),
        None,
    )
    return source, label


def _mapping_has_instrument_field(value: Mapping[str, Any]) -> bool:
    keys = {_normalized_key(key) for key in value}
    return bool(keys & (_NORMALIZED_FIELD_ALIASES["symbol"] | _NORMALIZED_FIELD_ALIASES["name"]))


def _compact_instrument(value: Mapping[str, Any]) -> dict[str, str]:
    normalized = {_normalized_key(key): item for key, item in value.items()}
    result: dict[str, str] = {}
    for field, aliases in _NORMALIZED_FIELD_ALIASES.items():
        for alias in aliases:
            item = normalized.get(alias)
            if isinstance(item, (str, int, float)) and not isinstance(item, bool):
                text = str(item).strip()
                if text:
                    result[field] = text[:500]
                    break
    return result


def _string_array_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "[]")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderConfigurationError(f"{name} must be a JSON string array") from exc
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) or not item or "\x00" in item for item in parsed
    ):
        raise ProviderConfigurationError(f"{name} must be a JSON string array")
    return tuple(parsed)


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProviderConfigurationError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ProviderConfigurationError(f"{name} must be a positive integer")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ProviderConfigurationError(f"{name} must be positive") from exc
    if not math.isfinite(value) or value <= 0:
        raise ProviderConfigurationError(f"{name} must be positive")
    return value
