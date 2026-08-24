"""Pure, JSON-shaped policy checks shared by validation and evaluation.

The V0 policy has two deliberately different layers:

* machine fields that would turn a research artifact into a trading output are
  forbidden everywhere; and
* natural-language trading instructions are inspected only in AI-generated
  artifacts (a ThesisDiff or its pending proposed patch).

Keeping those layers separate lets source quotations remain faithful.  An
earnings-call quote containing an analyst's rating, for example, is evidence,
not a ThesisOS recommendation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


@dataclass(frozen=True)
class PolicyViolation:
    """One deterministic policy finding with an exact JSON-style path."""

    path: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.path or '$'}: {self.message}"


_FORBIDDEN_MACHINE_FIELDS = frozenset(
    {
        "buy",
        "sell",
        "order",
        "recommendation",
        "trade_action",
        "trading_action",
        "buy_rating",
        "sell_rating",
        "hold_rating",
        "rating",
        "investment_rating",
        "analyst_rating",
        "target_price",
        "price_target",
        "position_size",
        "position_sizing",
        "portfolio_weight",
        "recommended_weight",
        "买入建议",
        "卖出建议",
        "投资评级",
        "目标价",
        "建议仓位",
    }
)

_GENERATED_TEXT_FIELDS = frozenset(
    {
        "overall_rationale",
        "rationale",
        "alternative_explanation",
        "summary",
        "current_action_or_result",
        "unresolved_part",
        "argument",
        "why_plausible",
        "question",
        "information_value",
        "evidence_needed",
        "one_sentence_thesis",
        "statement",
        "name",
        "ticker",
        "market",
        "why_it_matters",
        "unit_or_definition",
        "basis",
        "valuation_basis",
        "reasonable_range",
        "market_implied_assumptions",
        "sensitive_variables",
        "insufficiency_reason",
        "tags",
    }
)

_SOURCE_TEXT_FIELDS = frozenset(
    {
        "quoted_text",
        "past_statement",
        "prior_statement",
    }
)

_TRADE_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "v0_buy_sell_hold_rating",
        re.compile(
            r"\b(?:"
            r"(?:buy|sell|hold)\s+(?:rating|recommendation)|"
            r"(?:rating|recommendation)\s*(?::|-|is)?\s*(?:a\s+)?(?:buy|sell|hold)|"
            r"rat(?:e|ed|es|ing)\s+(?:the\s+)?(?:stock|shares?|company|it)?\s*"
            r"(?:as\s+)?(?:a\s+)?(?:buy|sell|hold)|"
            r"(?:upgrade|downgrade)(?:d|s|ing)?\s+(?:the\s+)?(?:stock|shares?|company|it)?\s*"
            r"to\s+(?:a\s+)?(?:buy|sell|hold)"
            r")\b",
            re.IGNORECASE,
        ),
        "V0 generated text forbids a buy/sell/hold rating",
    ),
    (
        "v0_trade_instruction",
        re.compile(
            r"\b(?:"
            r"(?:investors?|we|you)\s+(?:(?:should|must|can|could|may|might)\s+|"
            r"(?:recommend|suggest)\s+)"
            r"(?:buy(?:ing)?|sell(?:ing)?|hold(?:ing)?|add\s+to|trim|reduce|exit|liquidate)\b|"
            r"(?:recommend|suggest|should|must|consider|time\s+to)\s+"
            r"(?:that\s+(?:investors?|we|you)\s+)?"
            r"(?:buy(?:ing)?|sell(?:ing)?|hold(?:ing)?)\s+(?:the\s+)?"
            r"(?:stock|shares?|securit(?:y|ies)|position)\b|"
            r"(?:recommend|suggest|should|must|consider|time\s+to)\s+"
            r"(?:that\s+(?:investors?|we|you)\s+)?"
            r"(?:add\s+to|trim|reduce|exit|liquidate)\s+(?:the\s+)?"
            r"(?:stock|shares?|securit(?:y|ies)|position)\b|"
            r"(?:buy|sell)\s+(?:now|immediately)\b|"
            r"(?:increase|reduce|trim|exit|liquidate)\s+(?:the\s+)?position\b"
            r")",
            re.IGNORECASE,
        ),
        "V0 generated text forbids a trading instruction",
    ),
    (
        "v0_price_target",
        re.compile(r"\b(?:price\s+target|target\s+price)\b|目标价(?:格)?", re.IGNORECASE),
        "V0 generated text forbids a target price",
    ),
    (
        "v0_position_sizing",
        re.compile(
            r"\b(?:position\s+sizing|position\s+size|portfolio\s+weight|recommended\s+weight)\b|"
            r"(?:建议|应该|应当|应|调整|控制|维持|设为).{0,8}仓位|"
            r"仓位.{0,8}(?:控制在|调整为|增至|降至|设为)\s*[0-9一二三四五六七八九十]",
            re.IGNORECASE,
        ),
        "V0 generated text forbids position sizing",
    ),
    (
        "v0_buy_sell_hold_rating",
        re.compile(
            r"(?:买入|卖出|持有)\s*评级|"
            r"(?:维持|给予|上调(?:至|为)?|下调(?:至|为)?).{0,8}(?:买入|卖出|持有)(?:评级)?"
        ),
        "V0 generated text forbids a buy/sell/hold rating",
    ),
    (
        "v0_trade_instruction",
        re.compile(
            r"(?:投资者|股东|你|我们).{0,8}(?:买入|卖出|加仓|减仓|清仓|加减仓)|"
            r"(?:建议|应当|应该|可以|可考虑|立即|现在).{0,8}"
            r"(?:买入|卖出).{0,8}(?:股票|股份|持仓|仓位|该股|本股|证券)|"
            r"(?:建议|应当|应该|可以|可考虑|立即|现在).{0,8}"
            r"(?:股票|股份|持仓|仓位|该股|本股|证券).{0,8}(?:买入|卖出)|"
            r"(?:建议|应当|应该|可以|可考虑|立即|现在).{0,8}"
            r"(?:加仓|减仓|清仓|加减仓)|"
            r"(?:买入|卖出|加仓|减仓|清仓|加减仓).{0,6}(?:投资建议|交易操作)"
        ),
        "V0 generated text forbids a trading instruction",
    ),
)

_ATTRIBUTION_POLICY: dict[str, frozenset[str]] = {
    "source_fact": frozenset({"source_document", "management"}),
    "source_opinion": frozenset({"management", "third_party_author"}),
    "user_judgment": frozenset({"user"}),
    "ai_inference": frozenset({"ai"}),
}


def allowed_attributions_for_content_class(content_class: object) -> frozenset[str]:
    """Return the allowed stable attribution values for one content class."""

    normalized = _stable_value(content_class)
    return _ATTRIBUTION_POLICY.get(normalized, frozenset())


def is_evidence_attribution_allowed(content_class: object, attribution: object) -> bool:
    """Whether an Evidence attribution preserves the README's four classes."""

    return _stable_value(attribution) in allowed_attributions_for_content_class(content_class)


def find_v0_policy_violations(
    payload: Any,
    scan_generated_text: bool | None = None,
) -> tuple[PolicyViolation, ...]:
    """Return every deterministic V0 policy violation in a JSON-shaped value.

    ``scan_generated_text=None`` is the safe default: text is inspected only
    when ``payload`` looks like a ThesisDiff or a pending AI proposed patch.
    Passing ``False`` performs only the universal machine-field check, while
    ``True`` explicitly treats the supplied value as AI-generated text.
    Source quotation fields are never text-scanned.
    """

    violations: list[PolicyViolation] = []
    _find_forbidden_fields(payload, "", violations)

    should_scan_text = (
        _looks_like_generated_artifact(payload)
        if scan_generated_text is None
        else bool(scan_generated_text)
    )
    if should_scan_text:
        _find_generated_text_violations(payload, "", None, violations)

    # A mapping could expose the same finding through an unusual shared object.
    # Stable de-duplication keeps evaluator output predictable.
    unique: dict[tuple[str, str, str], PolicyViolation] = {}
    for violation in violations:
        unique[(violation.path, violation.code, violation.message)] = violation
    return tuple(unique.values())


def _stable_value(value: object) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value).strip().lower()


def _normalized_key(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", str(value).strip().lower()).strip("_")


def _child_path(path: str, key: object) -> str:
    return f"{path}.{key}" if path else str(key)


def _find_forbidden_fields(
    value: Any,
    path: str,
    violations: list[PolicyViolation],
) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            child_path = _child_path(path, raw_key)
            if _normalized_key(raw_key) in _FORBIDDEN_MACHINE_FIELDS:
                violations.append(
                    PolicyViolation(
                        path=child_path,
                        code="v0_forbidden_field",
                        message="V0 output forbids this trading or target-price field",
                    )
                )
            _find_forbidden_fields(child, child_path, violations)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _find_forbidden_fields(child, f"{path}[{index}]", violations)


def _looks_like_generated_artifact(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if (
        isinstance(payload.get("thesis_diff_id"), str)
        and isinstance(payload.get("proposed_patch"), Mapping)
    ):
        return True
    return (
        payload.get("patch_status") == "pending_user_review"
        and isinstance(payload.get("proposed_thesis"), Mapping)
    )


def _looks_like_evidence(value: Mapping[Any, Any]) -> bool:
    return (
        "evidence_id" in value
        and "content_class" in value
        and "citations" in value
    )


def _find_generated_text_violations(
    value: Any,
    path: str,
    parent_key: str | None,
    violations: list[PolicyViolation],
) -> None:
    if isinstance(value, Mapping):
        if _looks_like_evidence(value):
            return
        for raw_key, child in value.items():
            key = _normalized_key(raw_key)
            child_path = _child_path(path, raw_key)
            if key in _SOURCE_TEXT_FIELDS or key in {"citations", "source_documents", "documents"}:
                continue
            if isinstance(child, str) and key in _GENERATED_TEXT_FIELDS:
                _scan_text(child, child_path, violations)
            elif isinstance(child, (list, tuple)) and key in _GENERATED_TEXT_FIELDS:
                _scan_generated_text_sequence(child, child_path, violations)
            else:
                _find_generated_text_violations(child, child_path, key, violations)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _find_generated_text_violations(child, f"{path}[{index}]", parent_key, violations)
    elif isinstance(value, str) and parent_key in _GENERATED_TEXT_FIELDS:
        _scan_text(value, path, violations)


def _scan_generated_text_sequence(
    values: list[Any] | tuple[Any, ...],
    path: str,
    violations: list[PolicyViolation],
) -> None:
    for index, child in enumerate(values):
        child_path = f"{path}[{index}]"
        if isinstance(child, str):
            _scan_text(child, child_path, violations)
        else:
            _find_generated_text_violations(child, child_path, None, violations)


def _scan_text(text: str, path: str, violations: list[PolicyViolation]) -> None:
    for code, pattern, message in _TRADE_TEXT_PATTERNS:
        match = pattern.search(text)
        if match is None or _is_explicit_disclaimer(text, match.start()):
            continue
        violations.append(PolicyViolation(path=path, code=code, message=message))


def _is_explicit_disclaimer(text: str, match_start: int) -> bool:
    """Avoid treating a nearby explicit non-recommendation as an instruction."""

    prefix = text[max(0, match_start - 18) : match_start].lower()
    return bool(
        re.search(r"(?:not|isn't|is not|no)\s+(?:an?\s+)?$", prefix)
        or re.search(r"(?:不构成|不是|并非|无)\s*$", prefix)
    )
