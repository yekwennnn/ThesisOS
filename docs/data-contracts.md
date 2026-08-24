# ThesisOS V0 Data Contracts

This document turns the V0 ThesisDiff object model in the README into six
Draft 2020-12 JSON Schemas. The contracts are intentionally UI- and
model-provider-neutral: a CLI, local application, evaluation harness, or agent
runtime should exchange the same objects.

## Canonical schemas

| Object | File | Purpose |
|---|---|---|
| SourceDocument | `schemas/source-document.schema.json` | Immutable metadata, availability time, reporting period, optional PDF page count, and content snapshot for an uploaded source |
| Citation | `schemas/citation.schema.json` | Snapshot-bound quote and exact page, paragraph, table, section, or line locator |
| Evidence | `schemas/evidence.schema.json` | Provenance-classified statement with one or more embedded citations |
| ThesisCard | `schemas/thesis-card.schema.json` | User-owned thesis, 3-7 testable assumptions, indicators, falsifiers, counter-case, valuation anchor, unknowns, and version metadata |
| ThesisDiff | `schemas/thesis-diff.schema.json` | Assessment of every base assumption, management statement/action comparison, targeted counter-case, 1-3 follow-up questions, and a pending full-version patch |
| UserReview | `schemas/user-review.schema.json` | Human acceptance, edited acceptance, rejection, evidence-based deferral, or research-task decision |

Every top-level object carries `schema_version: "1.0.0"`. Schema `$id` values
use `https://thesisos.dev/schemas/<file-name>` and are identifiers, not a claim
that the host currently serves schema files.

## Object flow

```text
SourceDocument
  -> Citation (bound to the document snapshot)
  -> Evidence (fact/opinion/judgment/inference)
  -> ThesisCard V1 (user-confirmed)
  -> ThesisDiff (pending proposed ThesisCard V2)
  -> UserReview
  -> ThesisCard V2 (only after explicit human acceptance)
```

`Evidence.citations` embeds complete Citation objects. This keeps evidence
extraction output self-contained while preserving stable citation IDs for
deduplication or normalization by a storage layer.

## Stable machine enums

Human-facing Chinese labels should be rendered by the application. Persisted
objects use the following stable English values.

### Evidence content classes

| README concept | `content_class` |
|---|---|
| 原始事实 | `source_fact` |
| 作者观点或管理层解释 | `source_opinion` |
| 用户明确确认的判断 | `user_judgment` |
| AI 推断 | `ai_inference` |

The schema additionally constrains `user_judgment` to `attribution: "user"`,
`ai_inference` to `attribution: "ai"`, and `source_opinion` to a management or
third-party author attribution. A source opinion cannot be attributed to the
AI and silently promoted into source material. These structural checks do not
replace semantic or human verification.

### Thesis impact

| README label | Machine value |
|---|---|
| 明显增强 | `clearly_strengthened` |
| 小幅增强 | `slightly_strengthened` |
| 基本不变 | `unchanged` |
| 小幅削弱 | `slightly_weakened` |
| 明显削弱 | `clearly_weakened` |
| 已被证伪 | `invalidated` |
| 证据不足 | `insufficient_evidence` |

The same enum is used for `overall_assessment` and each assumption's `impact`.
An assumption with `insufficient_evidence` may honestly have an empty
`evidence_ids` array; every other impact requires at least one evidence ID.
An `invalidated` impact additionally requires one or more
`triggered_falsification_condition_ids` that resolve to the frozen base card;
that field is forbidden for all other impacts.

### Proposed patch operations

`keep`, `modify`, `add`, `remove`, and `insufficient_evidence` correspond to the
README's suggested retain/change/add/delete/not-enough-evidence review list.
The complete `proposed_patch.proposed_thesis` is authoritative, and the change
list is a reconciled audit summary rather than optional prose. Every actual
non-version change must have exactly one matching item. Singleton targets
(`one_sentence_thesis`, `strongest_counter_case`, `valuation_anchor`) use a null
`target_id`; collection targets use the exact assumption, indicator,
falsification-condition, or question ID. `add`, `modify`, and `remove` must
match the keyed before/after difference. Duplicate, wrong-ID, wrong-operation,
and decoy `keep` items fail closed. Retained collection objects must preserve
their relative order because V0 has no reorder operation. Company metadata and
tags are preserved because the V0 target enum has no operation for them.

### User decisions

| README choice | `decision` | Effect |
|---|---|---|
| 接受更新 | `accept` | Confirm the complete proposed thesis without edits |
| 修改后接受 | `accept_with_edits` | Confirm the required `reviewed_thesis` supplied by the user |
| 拒绝更新 | `reject` | Preserve the base thesis; create no new version |
| 证据不足，暂不更新 | `defer_insufficient` | Preserve the base thesis; create no new version |
| 创建后续研究任务 | `create_research_task` | Preserve the base thesis and create one or more open research tasks |

`reviewed_thesis` is allowed only for `accept_with_edits` and must have
`version.user_confirmed: true`. Research tasks are allowed only for
`create_research_task`.

### Valuation availability

The valuation anchor uses an explicit `status` instead of asking a model to
invent placeholder values:

- `provided`: all four README valuation fields are required;
- `partial`: at least one field plus `insufficiency_reason` is required;
- `insufficient_evidence`: only `insufficiency_reason` is required and the
  substantive valuation fields are forbidden.

## Guarantees encoded locally

The schemas reject unknown fields where the V0 contract is defined, require
non-empty identifiers and text, and encode the most important local rules:

- every ThesisCard has exactly 3-7 assumptions;
- every assumption links to at least one indicator and one falsification
  condition;
- every ThesisCard has a targeted strongest counter-case;
- every ThesisCard has complete version metadata, including nullable
  `supersedes` and explicit `user_confirmed`;
- every ThesisDiff assesses 3-7 assumptions and contains a targeted
  counter-case;
- an aligned, partially aligned, or misaligned management assessment includes
  at least one explicit past-statement/current-action comparison;
- every ThesisDiff asks 1-3 follow-up questions;
- every ThesisDiff carries a `pending_user_review` patch with the base version
  ID and a complete proposed ThesisCard whose `user_confirmed` value is false;
- every Evidence object has at least one exact Citation;
- every Citation names an immutable source snapshot and a structured locator;
- review decision-specific fields are conditionally required or forbidden.

`format` values such as `date`, `date-time`, and `uri` require a validator that
enables Draft 2020-12 format assertion. A parser that merely loads JSON does not
check them.

## Cross-object invariants

JSON Schema validates one instance at a time. The application and evaluation
harness must enforce all of the following before promoting any object.

### Identity and references

1. IDs are globally unique within their object type. `uniqueItems` cannot prove
   that two different objects do not reuse the same ID.
2. Every referenced company, thesis, version, assumption, indicator,
   falsification condition, evidence item, document, and diff exists.
3. A ThesisCard assumption's `indicator_ids` and
   `falsification_condition_ids` resolve inside that same card. Reverse
   `linked_assumption_ids` references agree with the forward references.
4. `ThesisDiff.assumption_changes` contains each base ThesisCard assumption
   exactly once and no unknown assumption IDs. The copied `prior_statement`
   equals the immutable base statement. If an assumption is `invalidated`,
   every triggered falsification-condition ID resolves inside that same base
   assumption.
5. All objects in one diff chain share the same `company_id`, `thesis_id`, and
   base version identity where applicable.

### Citation and evidence integrity

1. Every Citation's `source_document_id` resolves to a SourceDocument and its
   `snapshot_sha256` exactly equals `SourceDocument.snapshot.sha256`.
2. The locator resolves inside that exact snapshot. Page, paragraph, and line
   range starts do not exceed their ends, and `quoted_text` matches the located
   source text or table value. A `table_value` citation must use a table locator;
   automatic local verification additionally requires a unique table caption,
   a structured header before the data row, and unambiguous `row` and `column`
   anchors. Those anchors map to one cell; the quote is never searched across
   the rest of the row or page, and its normalized text must equal the complete
   normalized cell so a sign, unit, or percent suffix cannot be dropped. An
   unstructured section without a detectable end boundary is rejected and must
   use an explicit line range instead. When
   `SourceDocument.page_count` is present, page locators must not exceed it.
   JSON Schema cannot inspect the file itself. For locally ingested
   `thesisos://sha256/<digest>` snapshots, downstream Evidence and UserReview
   commands re-read the stored bytes and verify their digest and size before
   writing anything. External snapshot URIs remain metadata-only and therefore
   cannot receive this local byte-integrity check.
3. Every Evidence `available_as_of` is greater than or equal to the public
   availability time of all its cited documents.
4. Evidence IDs referenced by a ThesisDiff resolve, belong to the same company,
   are explicitly `verification_status: "verified"`, and support the associated
   rationale rather than merely mentioning the same topic. A model extraction
   run always emits `unreviewed` and cannot verify itself.
5. `source_fact`, `source_opinion`, `user_judgment`, and `ai_inference` are
   semantically classified correctly. An AI inference may not be rewritten as
   a fact or as a user judgment.
6. Key financial numbers have 100% citation coverage. Critical facts and key
   conclusions remain traceable to exact original text, not only to another AI
   summary.

### Time and historical replay

1. `analysis_cutoff_at`, document availability times, evidence availability
   times, publication dates, reporting periods, and creation times form a
   coherent chronology.
2. Every document and evidence item used by a historical replay was publicly
   available at or before `analysis_cutoff_at`. Retrieval indexes, prompts,
   caches, model context, and expected answers must also exclude future
   information.
   `Evidence.created_at` is the processing/audit time and may be later than the
   historical cutoff when a replay is run today; `available_as_of` is the field
   that gates admissible knowledge.
   The confirmed base ThesisCard's `as_of_date`, `created_at`, and `updated_at`
   must themselves exist by the Diff cutoff. Evidence references inherited
   from its strongest counter-case are separately audited against the base
   version's own `updated_at`, so later Evidence cannot be retrofitted into an
   earlier belief state.
3. Reporting-period start dates do not exceed end dates. Document publication
   and availability metadata describe the correct report period rather than the
   ingestion date.
4. `created_at` does not exceed `updated_at`; a new version's timestamps follow
   the base version and its `as_of_date` does not exceed the analysis cutoff.

### Versioning and review

1. `proposed_patch.base_thesis_id` equals the diff's `base_thesis_id`, and
   `proposed_patch.base_version_id` equals the diff's top-level
   `base_version_id`, which identifies the immutable version actually analyzed.
2. The proposed ThesisCard retains the same thesis and company identity,
   receives a new unique `version_id`, and has
   `version.supersedes == proposed_patch.base_version_id`.
3. Every actual non-version difference is reconciled by target type, stable
   target ID, and exact operation. Proposal company metadata and tags remain
   equal to the base, and retained collection objects preserve relative order,
   because V0 has no ChangeItem target/operation for those changes.
4. A pending proposal is never stored as a confirmed user position. The system
   may not silently mutate or overwrite any prior ThesisCard version.
5. A UserReview points to the exact diff and base version it reviewed. Only an
   authenticated human action may create it; model output must never be trusted
   as user confirmation.
6. `accept` copies the proposed thesis into an immutable confirmed version and
   flips only `version.user_confirmed` to true. `accept_with_edits` uses the
   complete user-supplied `reviewed_thesis`, which must supersede the same base
   version. Rejection, deferral, and research-task creation produce no new
   ThesisCard version.
7. A promoted or directly committed version cannot rewind `as_of_date`,
   `created_at`, or `updated_at` behind the current confirmed version.
8. A diff and review are append-only audit records. A new analysis creates new
   IDs rather than rewriting a previous judgment after outcomes are known.

For management say/do comparisons, `past_evidence_ids` may cite older
cutoff-safe SourceDocuments outside the Diff's current-material
`source_document_ids`. Those older documents must still resolve and validate;
only `current_evidence_ids` and evidence supporting current assumption changes,
counter-cases, or patches are required to cite the current-material list.
At the model-adapter boundary, every past ID must come from the explicit
`--prior-evidence` set, while all current-material roles must come from the
explicit `--evidence` set. A complete proposed ThesisCard may preserve an
unchanged base counter-case reference, but any newly introduced reference is a
current-material role and is subject to the same selection, cutoff, company,
verification, snapshot, and source-document gates.

### Semantic product guardrails

1. Overall and per-assumption impacts follow from the cited evidence and do not
   treat a changed metric as automatic thesis strengthening.
2. Alternative explanations and the targeted counter-case attack concrete
   assumptions; generic boilerplate does not satisfy the product requirement.
3. Follow-up questions are non-template, high-information questions capable of
   changing an investment judgment.
4. ThesisDiff V0 does not emit buy/sell instructions, target prices, or an
   autonomous investment decision. A schema cannot reliably prohibit those
   meanings inside arbitrary text, so evaluation and review must check them.
5. `insufficient_evidence` is used when appropriate instead of fabricated
   certainty or fabricated valuation content.

## Validation order

For a deterministic V0 pipeline:

1. validate SourceDocument metadata and freeze its content snapshot;
2. validate each Citation against the frozen snapshot;
3. validate Evidence JSON, verify citation text and temporal invariants, then
   require an explicit human verification step before Diff generation;
4. validate the immutable base ThesisCard;
5. validate ThesisDiff JSON, then resolve every cross-object reference and run
   future-leakage, citation-accuracy, and assumption-mapping checks;
6. persist the diff and proposed thesis as pending, never confirmed;
7. validate an authenticated UserReview;
8. create a new confirmed ThesisCard version only for an accepted review.

Schema changes that break stored instances require a new `schema_version` and a
migration. Existing `$id` values must not be repointed to incompatible
contracts.
