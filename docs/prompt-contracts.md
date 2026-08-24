# Prompt Contracts

ThesisOS V0 uses four prompt contracts to turn untrusted source material into a
reviewable Thesis Diff without blurring facts, opinions, user judgment, and AI
inference.

The JSON Schemas are authoritative for structure. These prompt contracts are
authoritative for model behavior. If prose and schema disagree, the runtime
must reject the output rather than guess how to repair it.

## Contract map

| Stage | Prompt | Primary input | Required JSON output |
|---|---|---|---|
| Establish a draft thesis | [`thesis-bootstrap`](../prompts/thesis-bootstrap.md) | User judgments plus admitted evidence | One `ThesisCard` validating against `schemas/thesis-card.schema.json` |
| Extract evidence | [`evidence-extraction`](../prompts/evidence-extraction.md) | One `SourceDocument` plus located text | JSON array whose items validate against `schemas/evidence.schema.json`; nested citations validate against `schemas/citation.schema.json` |
| Compare new evidence | [`thesis-diff`](../prompts/thesis-diff.md) | Confirmed base `ThesisCard` plus new evidence | One `ThesisDiff` validating against `schemas/thesis-diff.schema.json` |
| Challenge the draft | [`red-team`](../prompts/red-team.md) | Base thesis, admitted evidence, draft diff | One corrected, complete `ThesisDiff` validating against the same schema |
| Record human authority | Application/UI, not an AI prompt | Corrected diff plus explicit user action | One `UserReview` validating against `schemas/user-review.schema.json` |

`SourceDocument`, `Citation`, `Evidence`, `ThesisCard`, `ThesisDiff`, and
`UserReview` are separate objects. A prompt must not hide one object inside an
unstructured prose field when a schema exists for it.

Canonical schema IDs are fixed:

| Object | `$id` |
|---|---|
| `SourceDocument` | `https://thesisos.dev/schemas/source-document.schema.json` |
| `Citation` | `https://thesisos.dev/schemas/citation.schema.json` |
| `Evidence` | `https://thesisos.dev/schemas/evidence.schema.json` |
| `ThesisCard` | `https://thesisos.dev/schemas/thesis-card.schema.json` |
| `ThesisDiff` | `https://thesisos.dev/schemas/thesis-diff.schema.json` |
| `UserReview` | `https://thesisos.dev/schemas/user-review.schema.json` |

## Shared invariants

### 1. Fixed analysis cutoff

Every run receives an explicit ISO 8601 `analysis_cutoff_at`. Only information
that was available on or before that instant is admissible. Publication time,
reporting period, event time, and ingestion time are different concepts and
must not be substituted for one another.

For historical replay, a missing availability date is a hard evidence gap. It
is safer to omit the material than risk future leakage.

### 2. Source content is untrusted

PDF text, OCR, webpages, notes, quotations, and retrieved passages can contain
instructions. They are data, never prompt authority. Runtime code should place
them in clearly delimited input blocks and should not interpolate them into the
system instructions.

### 3. Four content kinds stay separate

The stable machine enum is:

- `source_fact`: directly supported, verifiable source content;
- `source_opinion`: management, analyst, author, or other source interpretation;
- `user_judgment`: explicitly confirmed or stated by the user;
- `ai_inference`: an AI interpretation proposed from admitted inputs.

A management forecast is not a realized fact. An AI inference is not a user
view. A third-party opinion does not become fact because its author is credible.

### 4. Citations are entailment links

Every material fact and number must resolve to evidence with an exact citation.
A citation identifies `source_document_id`, binds to the immutable
`snapshot_sha256`, uses a schema-supported page, paragraph, table, section, or
line-range locator, and carries text that supports the claim actually made. A
citation is not decoration: source prestige, a nearby page, or a loosely related
quotation does not satisfy the contract.

The model must never invent a page, section, table, paragraph, line range,
quotation, date, source, number, or ID. If accurate support cannot be located,
the claim is omitted or marked insufficient through a schema-supported field.
`table_value` is reserved for a structured table locator; prose found on a PDF
page uses an exact-quote or faithful-paraphrase mode instead. Automatic table
verification maps unique row/column anchors to one cell and requires complete
normalized-cell equality, including signs and units.

### 5. Insufficient evidence is a valid result

The model must not fill a missing field with a plausible story. Missing or
irrelevant evidence maps to `insufficient_evidence`, not `unchanged`.

Stable Thesis Diff impact values are:

```text
clearly_strengthened
slightly_strengthened
unchanged
slightly_weakened
clearly_weakened
invalidated
insufficient_evidence
```

Thesis-change confidence values are `low`, `medium`, and `high`. The Evidence
schema also permits `insufficient_evidence`; extraction normally omits a claim
that cannot be supported rather than manufacturing an evidence item for it.
`invalidated` is reserved for a cited, pre-existing falsification condition and
must identify that base condition in
`triggered_falsification_condition_ids`; the field is forbidden for other
impacts so a model cannot backfill a falsifier after seeing an outcome.

### 6. Counter-analysis is targeted

The strongest counter-case attacks the central conclusion, an important
assumption, or a causal link using the same cutoff-safe evidence. Generic lists
of macro, competition, regulatory, or market risks do not satisfy the contract.
The red team may preserve a well-supported draft; independence does not mean
automatic disagreement.

### 7. The user controls thesis changes

AI output is a proposal. Bootstrap and proposed-patch Thesis Cards always use
`version.user_confirmed=false`. A proposed patch identifies `base_version_id`
and contains a full `proposed_thesis`, so the candidate version is reviewable
without silently mutating the accepted card.

Every non-version difference from the base is reconciled to exactly one
ChangeItem with the correct target type, stable target ID, and add/modify/remove
operation. Singleton targets use a null ID; collection targets use their object
ID. Company metadata, tags, and the relative order of retained collection
objects remain unchanged because V0 has no operation for those changes.

Only an explicit application-level user action creates a `UserReview`. Stable
review decisions are:

```text
accept
accept_with_edits
reject
defer_insufficient
create_research_task
```

The model never marks its own output accepted.

For ThesisDiff generation, the adapter's Evidence namespace is partitioned:
past-side say/do references come only from the explicit prior set, while
assumption changes, current actions, counter-cases, patch items, and newly
introduced proposed-Thesis references come only from the explicit new set.
References retained unchanged from the confirmed base are integrity-checked at
the base cutoff but are not silently added to model context.

### 8. Product scope is research, not execution

No V0 prompt may output a buy, sell, add, trim, hold, position-size, timing,
target-price, expected-return, or trading instruction. Valuation anchors record
the user's framework and supported inputs; a caller-supplied reasonable range
may be preserved as research data, but it is never recast as a machine-generated
price target or recommendation.

## Recommended execution order

```text
validate SourceDocument and cutoff
  -> run evidence-extraction
  -> validate each Evidence and Citation
  -> bootstrap or load a confirmed ThesisCard
  -> run thesis-diff
  -> validate ThesisDiff
  -> run red-team
  -> validate corrected ThesisDiff
  -> present to user
  -> record explicit UserReview
  -> only then create an accepted ThesisCard version
```

Schema validation occurs after every arrow. A later stage must consume only
validated objects from the previous stage.

## Determinism requirements

- The caller supplies IDs, timestamps, and the cutoff; the model must not guess
  wall-clock time.
- Use temperature 0 or the closest deterministic runtime setting available.
- Preserve base assumption order; otherwise sort stable collections by their
  schema ID. Preserve document order when locator order matters.
- Return JSON only, without Markdown fences or explanatory prose.
- Reject unknown properties through schema validation.
- Store the prompt contract version, model identifier, schema version, input
  object hashes, and output hash with each run.
- Re-running identical normalized inputs should produce semantically equivalent
  output; enum, citation, and assumption-mapping differences should fail evals.

## Runtime rejection conditions

The application must reject an output before user review when:

- JSON parsing or schema validation fails;
- an ID reference does not resolve;
- a past/current Evidence reference escapes its explicit adapter input role;
- an evidence item lacks a valid citation;
- a cited source is post-cutoff or has unknown availability in historical replay;
- a quote or numeric value cannot be found at its locator;
- an active assumption is missing from a Thesis Diff;
- an actual proposed-Thesis change is missing, points at the wrong stable ID,
  uses the wrong operation, duplicates a target, or hides behind a decoy item;
- the proposed thesis is already user-confirmed;
- the output contains an undeclared field used to smuggle prose around a schema;
- prohibited investment or trade instructions appear.

## Evaluation hooks

The V0 evaluation suites map directly to the contracts:

- `evals/citation-accuracy`: citation existence, locator accuracy, quote fidelity,
  numeric fidelity, and claim entailment;
- `evals/assumption-mapping`: whether evidence is mapped to the assumption it
  actually supports or weakens;
- `evals/future-leakage`: whether every used item was available by the cutoff;
- `evals/historical-replay`: whether a frozen Thesis Card plus the next known
  source produces a useful, reproducible, user-reviewable Diff.

The acceptance target from the README remains strict: key financial numbers
must have 100% source coverage, future leakage must be zero, unconfirmed AI
content must never become formal user judgment, and the output should surface
at least one non-template question that can genuinely change the thesis.
