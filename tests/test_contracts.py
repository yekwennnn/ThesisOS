from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from thesisos.models import (
    Assumption,
    AssumptionChange,
    Attribution,
    ChangeItem,
    ChangeOperation,
    ChangeStatus,
    ChangeTargetType,
    Citation,
    CitationLocator,
    Company,
    ComparisonAssessment,
    Confidence,
    CounterCase,
    DocumentType,
    Evidence,
    EvidenceConfidence,
    EvidenceKind,
    FalsificationCondition,
    FollowUpQuestion,
    KeyIndicator,
    LocatorKind,
    ManagementAssessment,
    ManagementComparison,
    ManagementStatementAction,
    MediaType,
    ProposedPatch,
    QuotationMode,
    ReportingPeriod,
    ReportingPeriodKind,
    ResearchStatus,
    ReviewDecision,
    Snapshot,
    SourceClass,
    SourceDocument,
    TargetedCounterCase,
    ThesisCard,
    ThesisDiff,
    UnknownQuestion,
    UserReview,
    ValuationAnchor,
    ValuationStatus,
    VerificationStatus,
    VersionMetadata,
)
from thesisos.policy import find_v0_policy_violations
from thesisos.validation import (
    DomainValidationError,
    validate_citation,
    validate_evidence,
    validate_proposed_patch,
    validate_source_document,
    validate_thesis_card,
    validate_thesis_diff,
    validate_user_review,
    validate_v0_output,
)


UTC = timezone.utc
NOW = datetime(2025, 5, 20, 12, 0, tzinfo=UTC)
AVAILABLE = datetime(2025, 5, 15, 8, 5, tzinfo=UTC)
BASE_TIME = datetime(2025, 5, 15, 10, 0, tzinfo=UTC)
SNAPSHOT_SHA = "a" * 64


def make_document() -> SourceDocument:
    return SourceDocument(
        source_document_id="DOC-001",
        company_id="COMPANY-001",
        title="Example issuer Q1 filing",
        document_type=DocumentType.QUARTERLY_REPORT,
        media_type=MediaType.PDF,
        source_class=SourceClass.PRIMARY,
        language="en",
        published_on=date(2025, 5, 15),
        publicly_available_at=AVAILABLE,
        reporting_period=ReportingPeriod(
            kind=ReportingPeriodKind.FISCAL_QUARTER,
            label="2025 Q1",
            start_on=date(2025, 1, 1),
            end_on=date(2025, 3, 31),
        ),
        snapshot=Snapshot(
            sha256=SNAPSHOT_SHA,
            storage_uri=f"urn:sha256:{SNAPSHOT_SHA}",
            byte_size=1024,
        ),
        ingested_at=datetime(2025, 5, 15, 9, 0, tzinfo=UTC),
        issuer_or_author="Example Co",
        original_uri="https://example.test/filing.pdf",
    )


def make_citation() -> Citation:
    return Citation(
        citation_id="CIT-001",
        source_document_id="DOC-001",
        snapshot_sha256=SNAPSHOT_SHA,
        quotation_mode=QuotationMode.EXACT_QUOTE,
        locator=CitationLocator(
            kind=LocatorKind.PAGE,
            page=12,
            section="Cash flow statement",
        ),
        quoted_text="Free cash flow increased 12% during the reporting period.",
    )


def make_evidence() -> Evidence:
    return Evidence(
        evidence_id="E-001",
        company_id="COMPANY-001",
        statement="Free cash flow increased 12% during the reporting period.",
        content_class=EvidenceKind.SOURCE_FACT,
        attribution=Attribution.SOURCE_DOCUMENT,
        confidence=EvidenceConfidence.HIGH,
        verification_status=VerificationStatus.VERIFIED,
        available_as_of=AVAILABLE,
        citations=(make_citation(),),
        created_at=datetime(2025, 5, 15, 9, 0, tzinfo=UTC),
        reported_for="2025 Q1",
        tags=("cash-flow",),
    )


def make_thesis(*, version_id: str = "V1", confirmed: bool = True) -> ThesisCard:
    assumptions = tuple(
        Assumption(
            assumption_id=f"A-{index:02d}",
            statement=f"Assumption {index}",
            indicator_ids=(f"K-{index:02d}",),
            falsification_condition_ids=(f"F-{index:02d}",),
        )
        for index in range(1, 4)
    )
    indicators = tuple(
        KeyIndicator(
            indicator_id=f"K-{index:02d}",
            name=f"Indicator {index}",
            why_it_matters=f"It tests assumption {index}.",
            linked_assumption_ids=(f"A-{index:02d}",),
            unit_or_definition="percent",
        )
        for index in range(1, 4)
    )
    conditions = tuple(
        FalsificationCondition(
            condition_id=f"F-{index:02d}",
            statement=f"Condition {index}",
            linked_assumption_ids=(f"A-{index:02d}",),
        )
        for index in range(1, 4)
    )
    supersedes = None if version_id == "V1" else "V1"
    return ThesisCard(
        thesis_id="THESIS-001",
        company=Company(
            company_id="COMPANY-001",
            name="Example Co",
            ticker="00000",
            market="XHKG",
            research_status=ResearchStatus.WATCHLIST,
        ),
        one_sentence_thesis="Durable service quality can support long-term free cash flow.",
        assumptions=assumptions,
        key_indicators=indicators,
        falsification_conditions=conditions,
        strongest_counter_case=CounterCase(
            statement="Service quality may not create pricing power.",
            attacked_assumption_ids=("A-01",),
            basis="Competitive intensity can transfer value to customers.",
        ),
        valuation_anchor=ValuationAnchor(
            status=ValuationStatus.PARTIAL,
            valuation_basis="Owner earnings under a conservative growth range.",
            insufficiency_reason="A full range needs normalized reinvestment data.",
        ),
        unknown_questions=(
            UnknownQuestion(
                question_id="UQ-01",
                question="How durable is the margin improvement?",
                linked_assumption_ids=("A-02",),
            ),
        ),
        version=VersionMetadata(
            as_of_date=date(2025, 5, 15),
            version_id=version_id,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
            supersedes=supersedes,
            user_confirmed=confirmed,
        ),
        tags=("quality",),
    )


def make_diff() -> tuple[ThesisDiff, ThesisCard, Evidence, SourceDocument]:
    base = make_thesis()
    proposed = replace(
        base,
        one_sentence_thesis="New evidence modestly strengthens durable cash generation.",
        version=replace(
            base.version,
            version_id="V2-DRAFT",
            supersedes="V1",
            user_confirmed=False,
            updated_at=NOW,
        ),
    )
    evidence = make_evidence()
    document = make_document()
    changes = tuple(
        AssumptionChange(
            assumption_id=assumption.assumption_id,
            prior_statement=assumption.statement,
            impact=(
                ChangeStatus.SLIGHTLY_STRENGTHENED
                if assumption.assumption_id == "A-01"
                else ChangeStatus.UNCHANGED
            ),
            confidence=Confidence.HIGH,
            evidence_ids=(evidence.evidence_id,),
            rationale="The filing reports stronger cash generation.",
            alternative_explanation="Working-capital timing may explain part of the change.",
        )
        for assumption in base.assumptions
    )
    comparison = ManagementComparison(
        comparison_id="MC-001",
        past_statement="Management committed to spending discipline.",
        past_evidence_ids=(evidence.evidence_id,),
        current_action_or_result="Reported cash generation improved.",
        current_evidence_ids=(evidence.evidence_id,),
        assessment=ComparisonAssessment.PARTIALLY_ALIGNED,
        unresolved_part="One period does not establish durability.",
    )
    patch = ProposedPatch(
        base_thesis_id=base.thesis_id,
        base_version_id=base.version.version_id,
        change_items=(
            ChangeItem(
                change_id="CH-001",
                operation=ChangeOperation.MODIFY,
                target_type=ChangeTargetType.ONE_SENTENCE_THESIS,
                target_id=None,
                summary="Record modest strengthening.",
                rationale="The cited filing is directionally supportive.",
                evidence_ids=(evidence.evidence_id,),
            ),
        ),
        proposed_thesis=proposed,
    )
    diff = ThesisDiff(
        thesis_diff_id="DIFF-001",
        company_id=base.company.company_id,
        base_thesis_id=base.thesis_id,
        base_version_id=base.version.version_id,
        source_document_ids=(document.source_document_id,),
        material_published_on=document.published_on,
        analysis_cutoff_at=datetime(2025, 5, 20, 10, 0, tzinfo=UTC),
        generated_at=NOW,
        overall_assessment=ChangeStatus.SLIGHTLY_STRENGTHENED,
        overall_rationale="The new evidence modestly strengthens one assumption.",
        assumption_changes=changes,
        management_statement_action=ManagementStatementAction(
            assessment=ManagementAssessment.PARTIALLY_ALIGNED,
            summary="Management's action is directionally consistent with its statement.",
            comparisons=(comparison,),
        ),
        targeted_counter_case=TargetedCounterCase(
            argument="The improvement may be temporary working-capital timing.",
            attacked_assumption_ids=("A-01",),
            evidence_ids=(evidence.evidence_id,),
            why_plausible="A single period cannot separate timing from structural gains.",
        ),
        follow_up_questions=(
            FollowUpQuestion(
                question_id="FU-001",
                question="How much improvement persists next quarter?",
                linked_assumption_ids=("A-01",),
                information_value="It distinguishes timing from durable improvement.",
                evidence_needed="A second period of cash-flow and working-capital data.",
            ),
        ),
        proposed_patch=patch,
    )
    return diff, base, evidence, document


class ContractTests(unittest.TestCase):
    def test_complete_contract_validates_and_round_trips(self) -> None:
        diff, base, evidence, document = make_diff()
        self.assertIs(
            validate_thesis_diff(
                diff,
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            ),
            diff,
        )

        for value in (document, evidence, base, diff):
            payload = value.to_dict()
            self.assertEqual(type(value).from_dict(payload), value)
        self.assertEqual(diff.to_dict()["overall_assessment"], "slightly_strengthened")
        self.assertEqual(diff.to_dict()["analysis_cutoff_at"], "2025-05-20T10:00:00Z")

    def test_proposed_thesis_as_of_date_cannot_leak_beyond_diff_cutoff(self) -> None:
        diff, base, evidence, document = make_diff()
        future_proposed = replace(
            diff.proposed_patch.proposed_thesis,
            version=replace(
                diff.proposed_patch.proposed_thesis.version,
                as_of_date=date(2025, 5, 21),
            ),
        )
        invalid = replace(
            diff,
            proposed_patch=replace(
                diff.proposed_patch,
                proposed_thesis=future_proposed,
            ),
        )

        with self.assertRaisesRegex(DomainValidationError, "as_of_date cannot follow"):
            validate_thesis_diff(
                invalid,
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            )

    def test_proposed_version_timestamps_cannot_follow_diff_generation(self) -> None:
        diff, base, evidence, document = make_diff()
        future_time = datetime(2025, 5, 20, 13, 0, tzinfo=UTC)
        future_proposed = replace(
            diff.proposed_patch.proposed_thesis,
            version=replace(
                diff.proposed_patch.proposed_thesis.version,
                created_at=future_time,
                updated_at=future_time,
            ),
        )
        invalid = replace(
            diff,
            proposed_patch=replace(diff.proposed_patch, proposed_thesis=future_proposed),
        )

        with self.assertRaisesRegex(DomainValidationError, "cannot follow diff.generated_at"):
            validate_thesis_diff(
                invalid,
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            )

    def test_base_thesis_cannot_postdate_diff_cutoff(self) -> None:
        diff, base, evidence, document = make_diff()
        future_base = replace(
            base,
            version=replace(
                base.version,
                updated_at=diff.analysis_cutoff_at.replace(hour=11),
            ),
        )

        with self.assertRaisesRegex(
            DomainValidationError,
            "base thesis updated_at cannot follow analysis_cutoff_at",
        ):
            validate_thesis_diff(
                diff,
                future_base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            )

    def test_proposed_created_at_cannot_predate_base_updated_at(self) -> None:
        diff, base, evidence, document = make_diff()
        later_base = replace(
            base,
            version=replace(
                base.version,
                updated_at=datetime(2025, 5, 16, 10, 0, tzinfo=UTC),
            ),
        )

        with self.assertRaisesRegex(
            DomainValidationError,
            "proposed thesis created_at cannot precede base thesis updated_at",
        ):
            validate_thesis_diff(
                diff,
                later_base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            )

    def test_inherited_counter_evidence_is_audited_at_base_cutoff(self) -> None:
        diff, base, evidence, document = make_diff()
        base_with_evidence = replace(
            base,
            strongest_counter_case=replace(
                base.strongest_counter_case,
                evidence_ids=(evidence.evidence_id,),
            ),
        )
        available_after_base = replace(
            evidence,
            available_as_of=datetime(2025, 5, 16, 8, 5, tzinfo=UTC),
            created_at=datetime(2025, 5, 16, 9, 0, tzinfo=UTC),
        )

        with self.assertRaisesRegex(
            DomainValidationError,
            "base evidence E-001: evidence E-001.available_as_of is after "
            "analysis_cutoff_at",
        ):
            validate_thesis_diff(
                diff,
                base_with_evidence,
                {evidence.evidence_id: available_after_base},
                {document.source_document_id: document},
            )

    def test_from_dict_rejects_unknown_fields_at_every_level(self) -> None:
        diff, _, _, _ = make_diff()
        payload = diff.to_dict()
        payload["proposed_patch"]["proposed_thesis"]["company"]["alias"] = "EX"
        with self.assertRaisesRegex(ValueError, "unknown fields: alias"):
            ThesisDiff.from_dict(payload)

        citation_payload = make_citation().to_dict()
        citation_payload["locator"]["table"] = "Not valid for a page locator"
        with self.assertRaisesRegex(ValueError, "unknown fields: table"):
            Citation.from_dict(citation_payload)

    def test_machine_enum_values_are_stable(self) -> None:
        self.assertEqual(
            [item.value for item in ChangeStatus],
            [
                "clearly_strengthened",
                "slightly_strengthened",
                "unchanged",
                "slightly_weakened",
                "clearly_weakened",
                "invalidated",
                "insufficient_evidence",
            ],
        )
        self.assertEqual(
            [item.value for item in ReviewDecision],
            [
                "accept",
                "accept_with_edits",
                "reject",
                "defer_insufficient",
                "create_research_task",
            ],
        )

    def test_thesis_requires_three_to_seven_unique_assumptions(self) -> None:
        base = make_thesis()
        with self.assertRaisesRegex(DomainValidationError, "between 3 and 7"):
            validate_thesis_card(replace(base, assumptions=base.assumptions[:2]))

        duplicate = replace(
            base.assumptions[1], assumption_id=base.assumptions[0].assumption_id
        )
        with self.assertRaisesRegex(DomainValidationError, "must contain unique items"):
            validate_thesis_card(
                replace(base, assumptions=(base.assumptions[0], duplicate, base.assumptions[2]))
            )

    def test_internal_references_must_exist(self) -> None:
        base = make_thesis()
        invalid = replace(
            base.assumptions[0],
            indicator_ids=("K-UNKNOWN",),
            falsification_condition_ids=("F-UNKNOWN",),
        )
        with self.assertRaises(DomainValidationError) as raised:
            validate_thesis_card(replace(base, assumptions=(invalid,) + base.assumptions[1:]))
        self.assertIn("unknown indicator K-UNKNOWN", str(raised.exception))
        self.assertIn("unknown falsification condition F-UNKNOWN", str(raised.exception))

    def test_factual_or_numeric_evidence_requires_locatable_citation(self) -> None:
        document = make_document()
        evidence = replace(make_evidence(), citations=())
        with self.assertRaises(DomainValidationError) as raised:
            validate_evidence(evidence, {document.source_document_id: document})
        self.assertIn("requires a locatable citation", str(raised.exception))

    def test_source_opinion_attribution_is_source_owned(self) -> None:
        document = make_document()
        documents = {document.source_document_id: document}
        for attribution in (Attribution.MANAGEMENT, Attribution.THIRD_PARTY_AUTHOR):
            opinion = replace(
                make_evidence(),
                content_class=EvidenceKind.SOURCE_OPINION,
                attribution=attribution,
            )
            self.assertIs(validate_evidence(opinion, documents), opinion)

        for attribution in (
            Attribution.AI,
            Attribution.USER,
            Attribution.SOURCE_DOCUMENT,
        ):
            disguised = replace(
                make_evidence(),
                content_class=EvidenceKind.SOURCE_OPINION,
                attribution=attribution,
            )
            with self.subTest(attribution=attribution):
                with self.assertRaisesRegex(
                    DomainValidationError,
                    "management, third_party_author",
                ):
                    validate_evidence(disguised, documents)

    def test_document_page_count_bounds_citations(self) -> None:
        document = replace(make_document(), page_count=20)
        validate_source_document(document)
        out_of_bounds = replace(
            make_citation(),
            locator=replace(make_citation().locator, page=999),
        )
        with self.assertRaisesRegex(DomainValidationError, "exceeds source document page_count"):
            validate_citation(out_of_bounds, {document.source_document_id: document})

        with self.assertRaisesRegex(DomainValidationError, "positive integer"):
            validate_source_document(replace(document, page_count=0))

    def test_table_value_requires_table_locator(self) -> None:
        document = make_document()
        invalid = replace(make_citation(), quotation_mode=QuotationMode.TABLE_VALUE)
        with self.assertRaisesRegex(DomainValidationError, "must be table for table_value"):
            validate_citation(invalid, {document.source_document_id: document})

    def test_diff_rejects_unknown_assumption_and_evidence_references(self) -> None:
        diff, base, evidence, document = make_diff()
        invalid_change = replace(
            diff.assumption_changes[0],
            assumption_id="A-UNKNOWN",
            evidence_ids=("E-UNKNOWN",),
        )
        with self.assertRaises(DomainValidationError) as raised:
            validate_thesis_diff(
                replace(diff, assumption_changes=(invalid_change,) + diff.assumption_changes[1:]),
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            )
        message = str(raised.exception)
        self.assertIn("unknown assumption A-UNKNOWN", message)
        self.assertIn("unknown evidence E-UNKNOWN", message)

    def test_diff_rejects_evidence_that_is_not_explicitly_verified(self) -> None:
        diff, base, evidence, document = make_diff()
        unreviewed = replace(
            evidence,
            verification_status=VerificationStatus.UNREVIEWED,
        )
        with self.assertRaisesRegex(
            DomainValidationError,
            "must be verified before it can support a ThesisDiff",
        ):
            validate_thesis_diff(
                diff,
                base,
                {unreviewed.evidence_id: unreviewed},
                {document.source_document_id: document},
            )

    def test_invalidated_assumption_names_its_triggered_falsification_condition(self) -> None:
        diff, base, evidence, document = make_diff()
        dependencies = (
            base,
            {evidence.evidence_id: evidence},
            {document.source_document_id: document},
        )
        invalidated = replace(
            diff.assumption_changes[0],
            impact=ChangeStatus.INVALIDATED,
        )
        with self.assertRaisesRegex(
            DomainValidationError,
            "triggered_falsification_condition_ids is required",
        ):
            validate_thesis_diff(
                replace(diff, assumption_changes=(invalidated,) + diff.assumption_changes[1:]),
                *dependencies,
            )

        unrelated = replace(
            invalidated,
            triggered_falsification_condition_ids=("F-02",),
        )
        with self.assertRaisesRegex(DomainValidationError, "not linked to assumption A-01"):
            validate_thesis_diff(
                replace(diff, assumption_changes=(unrelated,) + diff.assumption_changes[1:]),
                *dependencies,
            )

        valid_change = replace(
            invalidated,
            triggered_falsification_condition_ids=("F-01",),
        )
        valid = replace(
            diff,
            overall_assessment=ChangeStatus.INVALIDATED,
            assumption_changes=(valid_change,) + diff.assumption_changes[1:],
        )
        self.assertIs(validate_thesis_diff(valid, *dependencies), valid)
        self.assertEqual(
            valid.to_dict()["assumption_changes"][0][
                "triggered_falsification_condition_ids"
            ],
            ["F-01"],
        )

        not_invalidated = replace(
            diff.assumption_changes[0],
            triggered_falsification_condition_ids=("F-01",),
        )
        with self.assertRaisesRegex(DomainValidationError, "only allowed"):
            validate_thesis_diff(
                replace(
                    diff,
                    assumption_changes=(not_invalidated,) + diff.assumption_changes[1:],
                ),
                *dependencies,
            )

    def test_diff_limits_questions_and_requires_targeted_counter_case(self) -> None:
        diff, base, evidence, document = make_diff()
        blank_counter = replace(diff.targeted_counter_case, argument=" ")
        questions = tuple(
            replace(diff.follow_up_questions[0], question_id=f"FU-{index:03d}")
            for index in range(1, 5)
        )
        with self.assertRaises(DomainValidationError) as raised:
            validate_thesis_diff(
                replace(
                    diff,
                    targeted_counter_case=blank_counter,
                    follow_up_questions=questions,
                ),
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            )
        self.assertIn("targeted_counter_case.argument must be non-empty", str(raised.exception))
        self.assertIn("between 1 and 3", str(raised.exception))

    def test_substantive_management_assessment_requires_comparison(self) -> None:
        diff, base, evidence, document = make_diff()
        empty = replace(diff.management_statement_action, comparisons=())
        with self.assertRaisesRegex(DomainValidationError, "substantive assessment"):
            validate_thesis_diff(
                replace(diff, management_statement_action=empty),
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            )

        insufficient = replace(
            empty,
            assessment=ManagementAssessment.INSUFFICIENT_EVIDENCE,
            summary="There is not enough evidence to compare words with actions.",
        )
        valid = replace(diff, management_statement_action=insufficient)
        self.assertIs(
            validate_thesis_diff(
                valid,
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            ),
            valid,
        )

    def test_pending_ai_patch_cannot_be_user_confirmed(self) -> None:
        diff, base, _, _ = make_diff()
        confirmed = replace(
            diff.proposed_patch.proposed_thesis,
            version=replace(
                diff.proposed_patch.proposed_thesis.version,
                user_confirmed=True,
            ),
        )
        with self.assertRaisesRegex(DomainValidationError, "user_confirmed=false"):
            validate_proposed_patch(
                replace(diff.proposed_patch, proposed_thesis=confirmed), base
            )

    def test_proposed_patch_discloses_every_change_and_preserves_metadata(self) -> None:
        diff, base, _, _ = make_diff()
        proposed = diff.proposed_patch.proposed_thesis

        hidden_indicator = replace(
            proposed,
            key_indicators=(
                replace(
                    proposed.key_indicators[0],
                    why_it_matters="A concealed change to an indicator.",
                ),
                *proposed.key_indicators[1:],
            ),
        )
        with self.assertRaisesRegex(
            DomainValidationError,
            "must disclose the actual modify for key_indicator target",
        ):
            validate_proposed_patch(
                replace(diff.proposed_patch, proposed_thesis=hidden_indicator),
                base,
            )

        hidden_assumption = replace(
            proposed,
            assumptions=(
                replace(
                    proposed.assumptions[0],
                    statement="A concealed assumption rewrite.",
                ),
                *proposed.assumptions[1:],
            ),
        )
        decoy = ChangeItem(
            change_id="CH-DECOY",
            operation=ChangeOperation.KEEP,
            target_type=ChangeTargetType.ASSUMPTION,
            target_id="A-03",
            summary="An unrelated assumption remains unchanged.",
            rationale="This declaration must not cover a different target.",
            evidence_ids=(),
        )
        with self.assertRaisesRegex(
            DomainValidationError,
            "must disclose the actual modify for assumption target 'A-01'",
        ):
            validate_proposed_patch(
                replace(
                    diff.proposed_patch,
                    change_items=diff.proposed_patch.change_items + (decoy,),
                    proposed_thesis=hidden_assumption,
                ),
                base,
            )

        all_hidden_assumptions = replace(
            proposed,
            assumptions=tuple(
                replace(item, statement=f"{item.statement} changed")
                for item in proposed.assumptions
            ),
        )
        with self.assertRaises(DomainValidationError) as raised:
            validate_proposed_patch(
                replace(
                    diff.proposed_patch,
                    proposed_thesis=all_hidden_assumptions,
                ),
                base,
            )
        assumption_coverage_issues = [
            issue
            for issue in raised.exception.issues
            if "actual modify for assumption target" in issue
        ]
        self.assertEqual(
            assumption_coverage_issues,
            [
                "proposed_patch.change_items must disclose the actual modify "
                f"for assumption target 'A-{index:02d}'"
                for index in range(1, 4)
            ],
        )

        invalid_singleton_target = replace(
            diff.proposed_patch.change_items[0],
            target_id="A-01",
        )
        with self.assertRaisesRegex(
            DomainValidationError,
            "target_id must be null for one_sentence_thesis",
        ):
            validate_proposed_patch(
                replace(
                    diff.proposed_patch,
                    change_items=(invalid_singleton_target,),
                ),
                base,
            )

        for field_name, target_type in (
            ("assumptions", "assumption"),
            ("key_indicators", "key_indicator"),
            ("falsification_conditions", "falsification_condition"),
        ):
            with self.subTest(reordered=field_name):
                reordered = replace(
                    proposed,
                    **{field_name: tuple(reversed(getattr(proposed, field_name)))},
                )
                with self.assertRaisesRegex(
                    DomainValidationError,
                    f"must preserve the relative {target_type} order",
                ):
                    validate_proposed_patch(
                        replace(diff.proposed_patch, proposed_thesis=reordered),
                        base,
                    )

        changed_company = replace(
            proposed,
            company=replace(proposed.company, ticker="OTHER"),
        )
        with self.assertRaisesRegex(
            DomainValidationError,
            "must preserve company metadata",
        ):
            validate_proposed_patch(
                replace(diff.proposed_patch, proposed_thesis=changed_company),
                base,
            )

        changed_tags = replace(proposed, tags=("different-tag",))
        with self.assertRaisesRegex(
            DomainValidationError,
            "must preserve tags",
        ):
            validate_proposed_patch(
                replace(diff.proposed_patch, proposed_thesis=changed_tags),
                base,
            )

    def test_proposed_patch_reconciles_keyed_add_and_remove_operations(self) -> None:
        diff, base, _, _ = make_diff()
        proposed = replace(
            diff.proposed_patch.proposed_thesis,
            unknown_questions=(
                UnknownQuestion(
                    question_id="UQ-NEW",
                    question="What new evidence would change the thesis?",
                    linked_assumption_ids=("A-01",),
                ),
            ),
        )
        remove_item = ChangeItem(
            change_id="CH-REMOVE-QUESTION",
            operation=ChangeOperation.REMOVE,
            target_type=ChangeTargetType.UNKNOWN_QUESTION,
            target_id="UQ-01",
            summary="Remove the superseded question.",
            rationale="The replacement asks a more discriminating question.",
            evidence_ids=(),
        )
        add_item = ChangeItem(
            change_id="CH-ADD-QUESTION",
            operation=ChangeOperation.ADD,
            target_type=ChangeTargetType.UNKNOWN_QUESTION,
            target_id="UQ-NEW",
            summary="Add the replacement question.",
            rationale="The answer can change the linked assumption.",
            evidence_ids=(),
        )
        exact_patch = replace(
            diff.proposed_patch,
            change_items=diff.proposed_patch.change_items
            + (remove_item, add_item),
            proposed_thesis=proposed,
        )
        self.assertIs(validate_proposed_patch(exact_patch, base), exact_patch)

        wrong_operation = replace(add_item, operation=ChangeOperation.MODIFY)
        with self.assertRaisesRegex(
            DomainValidationError,
            "operation modify does not match the actual add",
        ):
            validate_proposed_patch(
                replace(
                    exact_patch,
                    change_items=diff.proposed_patch.change_items
                    + (remove_item, wrong_operation),
                ),
                base,
            )

    def test_accept_with_edits_requires_complete_confirmed_thesis(self) -> None:
        diff, _, _, _ = make_diff()
        missing = UserReview(
            user_review_id="REVIEW-001",
            thesis_diff_id=diff.thesis_diff_id,
            company_id=diff.company_id,
            base_thesis_id=diff.base_thesis_id,
            base_version_id=diff.base_version_id,
            decision=ReviewDecision.ACCEPT_WITH_EDITS,
            reviewer_id="USER-001",
            reviewed_at=datetime(2025, 5, 20, 13, 0, tzinfo=UTC),
        )
        with self.assertRaisesRegex(DomainValidationError, "requires a complete reviewed_thesis"):
            validate_user_review(missing, diff)

        reviewed = replace(
            diff.proposed_patch.proposed_thesis,
            version=replace(
                diff.proposed_patch.proposed_thesis.version,
                version_id="V2-USER",
                user_confirmed=True,
            ),
        )
        valid = replace(missing, reviewed_thesis=reviewed)
        self.assertIs(validate_user_review(valid, diff), valid)
        self.assertEqual(UserReview.from_dict(valid.to_dict()), valid)

        future_reviewed = replace(
            reviewed,
            version=replace(
                reviewed.version,
                created_at=datetime(2025, 5, 20, 14, 0, tzinfo=UTC),
                updated_at=datetime(2025, 5, 20, 14, 0, tzinfo=UTC),
            ),
        )
        with self.assertRaisesRegex(
            DomainValidationError,
            "reviewed_thesis.version.created_at cannot follow review.reviewed_at",
        ):
            validate_user_review(replace(missing, reviewed_thesis=future_reviewed), diff)

        rewound_reviewed = replace(
            reviewed,
            version=replace(
                reviewed.version,
                as_of_date=date(2020, 1, 1),
                created_at=datetime(2020, 1, 1, 0, 0, tzinfo=UTC),
                updated_at=datetime(2020, 1, 1, 0, 0, tzinfo=UTC),
            ),
        )
        with self.assertRaisesRegex(
            DomainValidationError,
            "reviewed_thesis.version.as_of_date cannot precede the reviewed draft",
        ):
            validate_user_review(replace(missing, reviewed_thesis=rewound_reviewed), diff)

    def test_v0_rejects_trade_and_target_price_fields(self) -> None:
        with self.assertRaises(DomainValidationError) as raised:
            validate_v0_output(
                {
                    "overall_assessment": "unchanged",
                    "trade_action": "buy",
                    "nested": {"target-price": 100},
                }
            )
        self.assertEqual(len(raised.exception.issues), 2)

        diff, _, _, _ = make_diff()
        payload = diff.to_dict()
        payload["target_price"] = 100
        with self.assertRaisesRegex(ValueError, "unknown fields: target_price"):
            ThesisDiff.from_dict(payload)

    def test_v0_scans_all_free_text_inside_ai_proposed_thesis(self) -> None:
        diff, _, _, _ = make_diff()
        for field_path in ("tags", "unit_or_definition"):
            with self.subTest(field=field_path):
                payload = diff.to_dict()
                proposed = payload["proposed_patch"]["proposed_thesis"]
                if field_path == "tags":
                    proposed["tags"] = ["BUY NOW"]
                else:
                    proposed["key_indicators"][0][field_path] = (
                        "Investors should buy the stock"
                    )
                with self.assertRaises(DomainValidationError):
                    validate_v0_output(payload)

    def test_v0_scans_only_ai_generated_diff_text(self) -> None:
        diff, base, evidence, document = make_diff()
        directed = replace(
            diff,
            overall_rationale="We rate the stock a Buy and set a target price of 100.",
        )
        with self.assertRaises(DomainValidationError) as raised:
            validate_thesis_diff(
                directed,
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            )
        self.assertIn("buy/sell/hold rating", str(raised.exception))
        self.assertIn("target price", str(raised.exception))

        chinese_directive = replace(
            diff.proposed_patch.proposed_thesis,
            one_sentence_thesis="基本面改善，因此建议加仓，并将建议仓位设为两成。",
        )
        bad_patch = replace(diff.proposed_patch, proposed_thesis=chinese_directive)
        with self.assertRaises(DomainValidationError) as raised:
            validate_thesis_diff(
                replace(diff, proposed_patch=bad_patch),
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            )
        self.assertIn("trading instruction", str(raised.exception))
        self.assertIn("position sizing", str(raised.exception))

    def test_v0_text_policy_avoids_source_and_research_false_positives(self) -> None:
        diff, base, evidence, document = make_diff()
        legitimate = replace(
            diff,
            overall_rationale=(
                "回购减少了股份数；持有理由仍是现金流韧性，目标经营利润率尚待验证。"
            ),
        )
        self.assertIs(
            validate_thesis_diff(
                legitimate,
                base,
                {evidence.evidence_id: evidence},
                {document.source_document_id: document},
            ),
            legitimate,
        )

        asset_disposals = (
            "管理层应该卖出长期亏损的非核心资产。",
            "Management should sell the loss-making subsidiary.",
        )
        for rationale in asset_disposals:
            asset_diff = replace(diff, overall_rationale=rationale)
            with self.subTest(rationale=rationale):
                self.assertIs(
                    validate_thesis_diff(
                        asset_diff,
                        base,
                        {evidence.evidence_id: evidence},
                        {document.source_document_id: document},
                    ),
                    asset_diff,
                )

        quoted_rating = replace(
            make_citation(),
            quoted_text="The source says: Buy rating; price target 100.",
        )
        source_evidence = replace(make_evidence(), citations=(quoted_rating,))
        self.assertEqual(
            find_v0_policy_violations(
                source_evidence.to_dict(),
                scan_generated_text=True,
            ),
            (),
        )

        # A user-owned Thesis Card may explain why an existing holding remains
        # under research; automatic text scanning begins only at the AI Diff.
        user_card = replace(
            base,
            one_sentence_thesis="Reasons to hold this company in the research watchlist.",
        )
        self.assertEqual(find_v0_policy_violations(user_card.to_dict()), ())


if __name__ == "__main__":
    unittest.main()
