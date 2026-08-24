# Model Adapter Protocol

ThesisOS does not bind V0 to one model vendor. `extract-evidence` and
`generate-diff` launch a caller-supplied executable with an argv array and
`shell=False`. The executable receives one versioned JSON envelope on stdin and
must write exactly one JSON value to stdout.

## Transport

The input envelope is:

```json
{
  "protocol": "thesisos.model-adapter",
  "protocol_version": "1.0.0",
  "task": "evidence-extraction",
  "contract": {
    "contract_id": "evidence-extraction",
    "contract_version": "0.1.0",
    "content": "the complete canonical prompt contract",
    "sha256": "..."
  },
  "model_identifier": "caller-owned/provider-model-version",
  "request_metadata": {},
  "inputs": {}
}
```

The adapter is responsible for translating this envelope into its provider's
API request. Source text and object JSON are untrusted data; the complete
`contract.content` remains the instruction boundary. Credentials should come
from the adapter's environment or secret store, not from JSON output. ThesisOS
never invokes a shell and deliberately omits the adapter argv from provenance
and error messages.

Output shapes are fixed:

- `evidence-extraction`: one JSON array containing only Evidence objects;
- `thesis-diff`: one complete ThesisDiff JSON object.

Stdout must contain JSON only. Logs belong on stderr. Non-zero exit, timeout,
invalid UTF-8 or JSON, duplicate JSON keys, the wrong output shape, and output
above the configured byte limit all fail closed.

## Minimal adapter skeleton

```python
#!/usr/bin/env python3
import json
import sys

request = json.load(sys.stdin)

# Call the provider here. Give it request["contract"]["content"] as the
# instruction contract and serialize request["request_metadata"] plus
# request["inputs"] as delimited, untrusted data. Ask for strict JSON output.
provider_text = call_your_model(request)

# Do not repair or silently complete invalid model output here. ThesisOS will
# apply canonical Schema, domain, time, source, policy, and human-authority
# admission after parsing it.
value = json.loads(provider_text)
json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True)
```

Invoke an adapter without shell quoting ambiguity:

```console
./.venv/bin/python -m thesisos --workspace ./workspace extract-evidence COMPANY_ID DOCUMENT_ID request.json \
  --adapter /absolute/path/to/adapter \
  --adapter-arg one-literal-argv-item \
  --model-id provider/model-version \
  --run-id unique-run-id
```

Repeat `--adapter-arg` for each argv item. A single command string is not
accepted. `--timeout`, `--max-stdout-bytes`, `--prompt-dir`, and
`--max-source-chars` expose bounded runtime controls.

## Evidence extraction admission

`extract-evidence` only accepts a workspace-managed
`thesisos://sha256/<digest>` source. It re-hashes the bytes, parses UTF-8 text,
Markdown, or PDF, builds stable 1-based page/line/paragraph views, and rejects a
source that was unavailable at `analysis_cutoff_at`. The adapter receives:

- the immutable SourceDocument;
- stable located source text;
- the current confirmed ThesisCard when one exists.

Required request metadata is:

```text
analysis_cutoff_at
evidence_id_prefix
citation_id_prefix
created_at
extraction_scope
```

The metadata object is an exact allowlist: unknown keys are rejected before
adapter execution or persistence. Do not put credentials in metadata. A
current ThesisCard is included only when its `as_of_date`, `created_at`, and
`updated_at` are all cutoff-safe; a later current version makes a historical
request fail closed instead of leaking future thesis context.

Every returned item is Schema- and domain-validated, must belong to the chosen
company, must cite only the selected snapshot, and must use
`verification_status=unreviewed`. This pass accepts `source_fact` and
`source_opinion`; it accepts `user_judgment` only from a
`source_class=user_provided` document, and never accepts `ai_inference`.
Exact quotes are checked inside their resolved locator. Automatic table values
require a unique table caption plus an explicit structured header/data-row
layout; the named row and column are mapped to one cell, and the normalized
quote must equal that complete normalized cell. A substring that drops a minus
sign, unit, or percent suffix fails. Unstructured page text fails closed.
Faithful paraphrases remain explicitly marked as requiring semantic human
review.

Extraction output is not silently saved as canonical Evidence. The complete
run is stored under `model_runs/<run-id>.json`; a human must inspect the output,
set an appropriate verification status, and call `save-evidence`. This is the
authority boundary that prevents a model from verifying itself.

## ThesisDiff generation admission

`generate-diff` takes explicit `--document`, `--evidence`, and optional
`--prior-evidence` IDs. It does not search the workspace for extra model
context.
`--document` identifies current materials. Documents cited by explicitly named
prior Evidence are resolved transitively from those citations for validation
and provenance, but are not sent as new material. Every selected Evidence
object must already be `verified`, cutoff-safe by `available_as_of`, and valid
against its source documents. Processing `created_at` may be later than a
historical cutoff; it is not treated as information availability. Managed exact
citations are rechecked against source bytes before model execution.

Evidence roles are admission-bound after model execution:

- every `past_evidence_ids` value must have been explicitly selected with
  `--prior-evidence`;
- Evidence used for assumption changes, the current side of a say/do
  comparison, the targeted counter-case, change items, or newly introduced
  references inside the proposed ThesisCard must have been selected with
  `--evidence`;
- Evidence references already present in the confirmed base ThesisCard are
  resolved from storage only as integrity dependencies, audited against the
  base version's own cutoff, and never added to the adapter's model context.

Required request metadata is:

```text
analysis_cutoff_at
generated_at
thesis_diff_id
material_published_on
proposed_version_id
proposed_as_of_date
proposed_created_at
proposed_updated_at
comparison_id_prefix
question_id_prefix
change_id_prefix
```

This metadata object is also an exact allowlist. The current confirmed thesis,
every current/prior source, and every Evidence availability timestamp are
checked before the adapter is launched.

The returned Diff must pass the canonical Schema, every cross-object invariant,
the future-information boundary, evidence verification gate, assumption
coverage, content-class rules, user-control rules, and V0 trading-policy scan.
Every actual non-version proposal difference is reconciled to one exact
`(target_type, target_id, operation)` ChangeItem; wrong-ID, aggregate, duplicate,
and decoy declarations are rejected. Company metadata, tags, and the relative
order of retained collection objects must remain unchanged. All free text the
adapter can write inside the proposal, including
tags and indicator units/definitions, is included in the trading-policy scan.
Caller-owned timestamps and IDs must be copied exactly. A successful Diff is
stored with its immutable model-run record after both paths pass one locked
bundle preflight. The audit append target is locked, opened, and validated
before either bundle member is published. The Diff still cannot update a
ThesisCard until a separate explicit `review` command accepts it.

## Provenance

Every successful run stores:

- protocol, task, prompt-contract version and prompt SHA-256;
- caller-owned model identifier;
- canonical normalized input and output SHA-256;
- UTC start and finish time;
- explicit source, evidence, and base-version references;
- the admitted raw model output.

The record intentionally contains no adapter argv. `run-id` is caller-owned and
write-once. A per-company/run-ID reservation spans duplicate checking, adapter
execution, validation, and publication. Concurrent commands using the same ID
therefore invoke the adapter only once; after the winner publishes, the waiter
is rejected before adapter launch. Callers should still allocate unique IDs for
distinct executions.
