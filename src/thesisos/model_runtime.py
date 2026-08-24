"""Provider-neutral subprocess runtime for ThesisOS model adapters.

The runtime deliberately knows nothing about a particular model provider.  It
passes one versioned JSON envelope to an adapter process on stdin and accepts
one task-shaped JSON value on stdout.  Schema and domain admission remain a
separate caller responsibility.

Security boundaries in this module are intentionally narrow and explicit:

* adapters are always launched from an argv sequence with ``shell=False``;
* prompt contracts are loaded verbatim from the canonical prompt catalog;
* stdout is retained only up to a caller-controlled byte limit;
* timeouts, process failures, malformed JSON, and wrong output shapes all fail
  closed; and
* provenance records hashes and a caller-owned model identifier, never the
  adapter argv (which may contain credentials or other secrets).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Sequence


PROTOCOL_NAME = "thesisos.model-adapter"
PROTOCOL_VERSION = "1.0.0"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_STDOUT_BYTES = 8 * 1024 * 1024
_MAX_CAPTURED_STDERR_BYTES = 64 * 1024
_READ_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class _TaskSpec:
    prompt_filename: str
    contract_version: str
    output_shape: str


_TASK_SPECS: dict[str, _TaskSpec] = {
    "evidence-extraction": _TaskSpec(
        prompt_filename="evidence-extraction.md",
        contract_version="0.1.0",
        output_shape="object_array",
    ),
    "thesis-diff": _TaskSpec(
        prompt_filename="thesis-diff.md",
        contract_version="0.1.2",
        output_shape="object",
    ),
}


class ModelRuntimeError(RuntimeError):
    """Base class for a model run rejected before admission."""


class ModelRuntimeInputError(ModelRuntimeError):
    """The caller supplied an invalid task, argv, or JSON-shaped input."""


class PromptCatalogError(ModelRuntimeError):
    """A required canonical prompt contract is unavailable or inconsistent."""


class AdapterLaunchError(ModelRuntimeError):
    """The adapter process could not be started safely."""


class AdapterTimeoutError(ModelRuntimeError):
    """The adapter exceeded its caller-supplied wall-clock deadline."""


class AdapterProcessError(ModelRuntimeError):
    """The adapter exited unsuccessfully."""


class AdapterOutputError(ModelRuntimeError):
    """The adapter returned malformed JSON or the wrong task output shape."""


class AdapterOutputTooLargeError(AdapterOutputError):
    """The adapter exceeded the bounded stdout protocol."""


@dataclass(frozen=True)
class PromptContract:
    """One verified prompt contract loaded verbatim from disk."""

    contract_id: str
    contract_version: str
    content: str
    sha256: str

    def envelope_value(self) -> dict[str, str]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "content": self.content,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ModelRunProvenance:
    """Secret-minimized identity of one successful adapter execution."""

    protocol: str
    protocol_version: str
    task: str
    contract_version: str
    contract_sha256: str
    model_identifier: str
    normalized_input_sha256: str
    normalized_output_sha256: str
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, str]:
        # Adapter argv is intentionally absent.  Do not add it here: provider
        # launch arguments commonly contain API keys or secret-bearing paths.
        return {
            "protocol": self.protocol,
            "protocol_version": self.protocol_version,
            "task": self.task,
            "contract_version": self.contract_version,
            "contract_sha256": self.contract_sha256,
            "model_identifier": self.model_identifier,
            "normalized_input_sha256": self.normalized_input_sha256,
            "normalized_output_sha256": self.normalized_output_sha256,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


ModelOutput = dict[str, Any] | list[dict[str, Any]]


@dataclass(frozen=True)
class ModelRunResult:
    """A task-shaped model value plus reproducibility provenance."""

    output: ModelOutput
    provenance: ModelRunProvenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "provenance": self.provenance.to_dict(),
        }


def discover_prompt_directory(explicit: str | Path | None = None) -> Path:
    """Locate all prompt contracts from a source tree or installed wheel."""

    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    else:
        configured = os.environ.get("THESISOS_PROMPT_DIR")
        if configured:
            candidates.append(Path(configured))
        else:
            packaged = _packaged_resource_directory("_prompts")
            if packaged is not None:
                candidates.append(packaged)
            candidates.extend(
                (
                    Path(__file__).resolve().parents[2] / "prompts",
                    Path.cwd() / "prompts",
                )
            )
    required = tuple(spec.prompt_filename for spec in _TASK_SPECS.values())
    for candidate in candidates:
        if candidate.is_dir() and all((candidate / name).is_file() for name in required):
            return candidate.resolve()
    rendered = ", ".join(str(candidate) for candidate in candidates)
    raise PromptCatalogError(
        "cannot locate the complete ThesisOS prompt catalog; "
        f"searched: {rendered}. Set THESISOS_PROMPT_DIR to the prompts directory."
    )


def _packaged_resource_directory(name: str) -> Path | None:
    """Return an installed package resource directory when it is filesystem-backed."""

    try:
        candidate = resources.files("thesisos").joinpath(name)
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - defensive import fallback
        return None
    return candidate if isinstance(candidate, Path) else None


class PromptCatalog:
    """Load and verify canonical prompt contracts for supported tasks."""

    def __init__(self, prompt_directory: str | Path | None = None):
        self.prompt_directory = discover_prompt_directory(prompt_directory)

    def load(self, task: str) -> PromptContract:
        spec = _task_spec(task)
        path = self.prompt_directory / spec.prompt_filename
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PromptCatalogError(f"cannot read prompt contract {path}") from exc
        if not content.strip():
            raise PromptCatalogError(f"prompt contract is empty: {path}")

        declared_id = _prompt_metadata(content, "Contract ID")
        declared_version = _prompt_metadata(content, "Contract version")
        if declared_id != task:
            raise PromptCatalogError(
                f"prompt contract {path.name} declares ID {declared_id!r}, expected {task!r}"
            )
        if declared_version != spec.contract_version:
            raise PromptCatalogError(
                f"prompt contract {path.name} declares version {declared_version!r}, "
                f"expected {spec.contract_version!r}"
            )
        return PromptContract(
            contract_id=declared_id,
            contract_version=declared_version,
            content=content,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )


def run_model_adapter(
    adapter_argv: Sequence[str],
    *,
    task: str,
    model_identifier: str,
    request_metadata: Mapping[str, Any],
    inputs: Mapping[str, Any],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES,
    prompt_directory: str | Path | None = None,
) -> ModelRunResult:
    """Run one external model adapter through the bounded JSON protocol.

    The adapter receives a canonical JSON object on stdin.  Its stdout must be
    a JSON array of objects for ``evidence-extraction`` or one JSON object for
    ``thesis-diff``.  This function validates only the transport shape; callers
    must still run the canonical Schema and domain admission gates.
    """

    spec = _task_spec(task)
    argv = _validated_argv(adapter_argv)
    model_id = _required_text(model_identifier, "model_identifier")
    timeout = _positive_finite_number(timeout_seconds, "timeout_seconds")
    stdout_limit = _positive_integer(max_stdout_bytes, "max_stdout_bytes")
    metadata_value = _normalized_json_object(request_metadata, "request_metadata")
    inputs_value = _normalized_json_object(inputs, "inputs")
    contract = PromptCatalog(prompt_directory).load(task)

    envelope: dict[str, Any] = {
        "protocol": PROTOCOL_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "task": task,
        "contract": contract.envelope_value(),
        "model_identifier": model_id,
        "request_metadata": metadata_value,
        "inputs": inputs_value,
    }
    envelope_bytes = _canonical_json_bytes(envelope, "model input envelope")
    input_sha256 = hashlib.sha256(envelope_bytes).hexdigest()

    started_at = _utc_timestamp()
    stdout_bytes = _execute_adapter(
        argv,
        envelope_bytes + b"\n",
        timeout_seconds=timeout,
        max_stdout_bytes=stdout_limit,
    )
    output = _parse_and_validate_output(stdout_bytes, task, spec.output_shape)
    output_bytes = _canonical_json_bytes(output, "model output")
    finished_at = _utc_timestamp()

    return ModelRunResult(
        output=output,
        provenance=ModelRunProvenance(
            protocol=PROTOCOL_NAME,
            protocol_version=PROTOCOL_VERSION,
            task=task,
            contract_version=contract.contract_version,
            contract_sha256=contract.sha256,
            model_identifier=model_id,
            normalized_input_sha256=input_sha256,
            normalized_output_sha256=hashlib.sha256(output_bytes).hexdigest(),
            started_at=started_at,
            finished_at=finished_at,
        ),
    )


def _execute_adapter(
    argv: tuple[str, ...],
    stdin_bytes: bytes,
    *,
    timeout_seconds: float,
    max_stdout_bytes: int,
) -> bytes:
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    stdout_overflow = threading.Event()

    with tempfile.TemporaryFile() as stdin_file:
        stdin_file.write(stdin_bytes)
        stdin_file.seek(0)
        try:
            process = subprocess.Popen(
                argv,
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=(os.name == "posix"),
            )
        except (OSError, ValueError):
            # Do not include argv or the platform exception: either may reveal
            # secret-bearing launch arguments.
            raise AdapterLaunchError("model adapter process could not be started") from None

        assert process.stdout is not None
        assert process.stderr is not None
        stdout_reader = threading.Thread(
            target=_drain_stream,
            args=(
                process.stdout,
                stdout_buffer,
                max_stdout_bytes,
                stdout_overflow,
            ),
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=_drain_stream,
            args=(
                process.stderr,
                stderr_buffer,
                _MAX_CAPTURED_STDERR_BYTES,
                None,
            ),
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader.start()

        deadline = time.monotonic() + timeout_seconds
        failure: str | None = None
        while process.poll() is None:
            if stdout_overflow.is_set():
                failure = "stdout_limit"
                _kill_adapter_tree(process)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "timeout"
                _kill_adapter_tree(process)
                break
            stdout_overflow.wait(min(0.05, remaining))

        try:
            return_code = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            _kill_adapter_tree(process)
            return_code = process.wait()

        _finish_readers(process, stdout_reader, stderr_reader)

    if failure == "timeout":
        raise AdapterTimeoutError(
            f"model adapter exceeded timeout of {timeout_seconds:g} seconds"
        )
    if failure == "stdout_limit" or stdout_overflow.is_set():
        raise AdapterOutputTooLargeError(
            f"model adapter stdout exceeded {max_stdout_bytes} bytes"
        )
    if return_code != 0:
        raise AdapterProcessError(f"model adapter exited with status {return_code}")
    return bytes(stdout_buffer)


def _drain_stream(
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


def _finish_readers(
    process: subprocess.Popen[bytes],
    stdout_reader: threading.Thread,
    stderr_reader: threading.Thread,
) -> None:
    stdout_reader.join(timeout=0.5)
    stderr_reader.join(timeout=0.5)
    if stdout_reader.is_alive() or stderr_reader.is_alive():
        # A descendant may have inherited the pipes after the direct adapter
        # exited.  Kill only the isolated session created for this launch.
        _kill_adapter_tree(process)
        stdout_reader.join(timeout=0.5)
        stderr_reader.join(timeout=0.5)


def _kill_adapter_tree(process: subprocess.Popen[bytes]) -> None:
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


def _parse_and_validate_output(
    raw: bytes,
    task: str,
    output_shape: str,
) -> ModelOutput:
    if not raw:
        raise AdapterOutputError("model adapter returned empty stdout")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AdapterOutputError("model adapter stdout is not valid UTF-8") from exc
    try:
        output = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ModelRuntimeInputError) as exc:
        raise AdapterOutputError("model adapter stdout is not strict JSON") from exc

    if output_shape == "object_array":
        if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
            raise AdapterOutputError(
                f"task {task!r} requires a JSON array containing only objects"
            )
        return output
    if output_shape == "object":
        if not isinstance(output, dict):
            raise AdapterOutputError(f"task {task!r} requires one JSON object")
        return output
    raise ModelRuntimeInputError(f"unsupported internal output shape: {output_shape}")


def _task_spec(task: object) -> _TaskSpec:
    if not isinstance(task, str) or task not in _TASK_SPECS:
        allowed = ", ".join(sorted(_TASK_SPECS))
        raise ModelRuntimeInputError(
            f"unsupported model task {task!r}; expected one of: {allowed}"
        )
    return _TASK_SPECS[task]


def _validated_argv(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ModelRuntimeInputError("adapter_argv must be a non-empty sequence of strings")
    argv = tuple(value)
    if not argv or not argv[0]:
        raise ModelRuntimeInputError("adapter_argv must contain a non-empty executable")
    if any(not isinstance(item, str) for item in argv):
        raise ModelRuntimeInputError("adapter_argv must contain only strings")
    if any("\x00" in item for item in argv):
        raise ModelRuntimeInputError("adapter_argv cannot contain NUL characters")
    return argv


def _normalized_json_object(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelRuntimeInputError(f"{label} must be a JSON object")
    encoded = _canonical_json_bytes(dict(value), label)
    parsed = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object)
    assert isinstance(parsed, dict)
    return parsed


def _canonical_json_bytes(value: Any, label: str) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ModelRuntimeInputError(f"{label} must contain only finite JSON values") from exc
    return rendered.encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelRuntimeInputError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ModelRuntimeInputError(f"non-finite JSON constant is forbidden: {value}")


def _prompt_metadata(content: str, field: str) -> str:
    match = re.search(
        rf"^\s*-\s*{re.escape(field)}\s*:\s*`?([^`\r\n]+?)`?\s*$",
        content,
        flags=re.MULTILINE,
    )
    if match is None or not match.group(1).strip():
        raise PromptCatalogError(f"prompt contract is missing {field}")
    return match.group(1).strip()


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelRuntimeInputError(f"{label} must be a non-empty string")
    return value.strip()


def _positive_finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelRuntimeInputError(f"{label} must be a positive finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ModelRuntimeInputError(f"{label} must be a positive finite number")
    return normalized


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ModelRuntimeInputError(f"{label} must be a positive integer")
    return value


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "PROTOCOL_NAME",
    "PROTOCOL_VERSION",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_STDOUT_BYTES",
    "AdapterLaunchError",
    "AdapterOutputError",
    "AdapterOutputTooLargeError",
    "AdapterProcessError",
    "AdapterTimeoutError",
    "ModelRunProvenance",
    "ModelRunResult",
    "ModelRuntimeError",
    "ModelRuntimeInputError",
    "PromptCatalog",
    "PromptCatalogError",
    "PromptContract",
    "discover_prompt_directory",
    "run_model_adapter",
]
