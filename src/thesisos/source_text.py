"""Stable text views and executable quote checks for managed snapshots.

Snapshot identity remains byte based.  This module derives a read-only text
view only after :func:`verify_stored_snapshot` has rechecked those bytes.  The
view supplies deterministic, one-based page, line, and paragraph coordinates
for citation verification without becoming a second source of truth.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from pypdf import PdfReader

from .models import Citation, QuotationMode, SourceDocument
from .snapshots import verify_stored_snapshot


class SourceTextError(ValueError):
    """Base class for text extraction and citation-location failures."""


class SourceTextExtractionError(SourceTextError):
    """A verified snapshot cannot produce the declared stable text view."""


class CitationBindingError(SourceTextError):
    """A citation is not bound to the supplied document and snapshot."""


class LocatorResolutionError(SourceTextError):
    """A locator is out of range, ambiguous, or absent from the snapshot."""


class CitationTextStatus(str, Enum):
    VERIFIED = "verified"
    QUOTE_NOT_FOUND = "quote_not_found"
    SEMANTIC_REVIEW_REQUIRED = "semantic_review_required"


@dataclass(frozen=True)
class TextLine:
    """One physical extracted line, numbered globally and within its page."""

    number: int
    page_number: int
    page_line_number: int
    text: str


@dataclass(frozen=True)
class TextParagraph:
    """One non-empty paragraph delimited by blank extracted lines."""

    number: int
    page_number: int
    line_start: int
    line_end: int
    text: str


@dataclass(frozen=True)
class TextPage:
    """One physical page (PDF) or the sole logical page (UTF-8 text)."""

    number: int
    text: str
    lines: tuple[TextLine, ...]
    paragraphs: tuple[TextParagraph, ...]


@dataclass(frozen=True)
class SourceTextView:
    """A deterministic derived view of one immutable snapshot."""

    source_document_id: str
    snapshot_sha256: str
    media_type: str
    pages: tuple[TextPage, ...]
    lines: tuple[TextLine, ...]
    paragraphs: tuple[TextParagraph, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass(frozen=True)
class CitationTextVerification:
    """Auditable outcome for a quote check within one resolved locator."""

    citation_id: str
    source_document_id: str
    locator_kind: str
    status: CitationTextStatus
    normalized_quoted_text: str
    scope_reference: str
    detail: str

    @property
    def passed(self) -> bool:
        """Only literal exact/table matches are automatic passes."""

        return self.status is CitationTextStatus.VERIFIED

    @property
    def requires_human_review(self) -> bool:
        return self.status is CitationTextStatus.SEMANTIC_REVIEW_REQUIRED

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_id": self.citation_id,
            "source_document_id": self.source_document_id,
            "locator_kind": self.locator_kind,
            "status": self.status.value,
            "passed": self.passed,
            "requires_human_review": self.requires_human_review,
            "normalized_quoted_text": self.normalized_quoted_text,
            "scope_reference": self.scope_reference,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class _LocatedScope:
    text: str
    reference: str


_WHITESPACE = re.compile(r"\s+")
_ATX_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_SETEXT_MARKER = re.compile(r"^\s*(=+|-+)\s*$")
_TABLE_CAPTION = re.compile(r"^\s*(?:table\b|表(?:格)?\s*\d)", re.IGNORECASE)
_MARKDOWN_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


def normalize_quote_text(value: str) -> str:
    """Normalize Unicode compatibility forms and layout whitespace only.

    Digits, punctuation, and token order are deliberately untouched.  This
    permits PDF line wrapping and repeated spaces without turning a paraphrase
    into an apparent exact quote.
    """

    if not isinstance(value, str):
        raise TypeError("quote text must be a string")
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def extract_source_text(
    workspace: str | Path,
    source_document: Mapping[str, Any] | SourceDocument,
) -> SourceTextView:
    """Verify and extract a stable text view from a managed snapshot."""

    document = _model_mapping(source_document, "source_document")
    verified = verify_stored_snapshot(workspace, document)
    document_id = _required_text(
        document.get("source_document_id"), "source_document_id"
    )
    media_type = _enum_text(document.get("media_type"), "media_type")
    if media_type not in {"plain_text", "markdown", "pdf"}:
        raise SourceTextExtractionError(
            f"unsupported SourceDocument.media_type {media_type!r}"
        )

    if media_type in {"plain_text", "markdown"}:
        try:
            text = verified.object_path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SourceTextExtractionError(
                f"{media_type} snapshot must contain valid UTF-8: {exc}"
            ) from exc
        page_texts = (text,)
    else:
        page_texts = _extract_pdf_pages(verified.object_path)
        declared_page_count = document.get("page_count")
        if declared_page_count is not None:
            if (
                not isinstance(declared_page_count, int)
                or isinstance(declared_page_count, bool)
                or declared_page_count < 1
            ):
                raise SourceTextExtractionError(
                    "SourceDocument.page_count must be a positive integer"
                )
            if declared_page_count != len(page_texts):
                raise SourceTextExtractionError(
                    "SourceDocument.page_count does not match the immutable PDF "
                    f"({declared_page_count} declared, {len(page_texts)} extracted)"
                )

    pages, lines, paragraphs = _build_view(page_texts)
    return SourceTextView(
        source_document_id=document_id,
        snapshot_sha256=verified.sha256,
        media_type=media_type,
        pages=pages,
        lines=lines,
        paragraphs=paragraphs,
    )


def verify_managed_citation(
    workspace: str | Path,
    source_document: Mapping[str, Any] | SourceDocument,
    citation: Mapping[str, Any] | Citation,
) -> CitationTextVerification:
    """Convenience API that verifies bytes, extracts text, and checks a quote."""

    view = extract_source_text(workspace, source_document)
    return verify_citation_text(view, source_document, citation)


def verify_citation_text(
    view: SourceTextView,
    source_document: Mapping[str, Any] | SourceDocument,
    citation: Mapping[str, Any] | Citation,
) -> CitationTextVerification:
    """Check a citation's literal text inside its resolved locator.

    Invalid bindings and locators raise typed exceptions and therefore fail
    closed.  A well-formed locator with a missing literal returns
    ``QUOTE_NOT_FOUND``.  ``faithful_paraphrase`` always returns
    ``SEMANTIC_REVIEW_REQUIRED`` even if its words happen to occur verbatim.
    """

    document = _model_mapping(source_document, "source_document")
    citation_data = _model_mapping(citation, "citation")
    document_id = _required_text(
        document.get("source_document_id"), "source_document_id"
    )
    citation_id = _required_text(citation_data.get("citation_id"), "citation_id")
    cited_document_id = _required_text(
        citation_data.get("source_document_id"), "citation.source_document_id"
    )
    snapshot = document.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise CitationBindingError("SourceDocument.snapshot must be an object")
    document_sha = _required_text(snapshot.get("sha256"), "snapshot.sha256")
    citation_sha = _required_text(
        citation_data.get("snapshot_sha256"), "citation.snapshot_sha256"
    )
    if cited_document_id != document_id or view.source_document_id != document_id:
        raise CitationBindingError(
            "citation, SourceDocument, and text view source_document_id must match"
        )
    if citation_sha != document_sha or view.snapshot_sha256 != document_sha:
        raise CitationBindingError(
            "citation, SourceDocument, and text view snapshot SHA-256 must match"
        )

    locator = citation_data.get("locator")
    if not isinstance(locator, Mapping):
        raise LocatorResolutionError("citation.locator must be an object")
    locator_kind = _required_text(locator.get("kind"), "citation.locator.kind")
    scope = _resolve_locator(view, locator)
    quoted_text = _required_text(
        citation_data.get("quoted_text"), "citation.quoted_text"
    )
    normalized_quote = normalize_quote_text(quoted_text)
    if not normalized_quote:
        raise SourceTextError("citation.quoted_text is empty after normalization")

    quotation_mode = _enum_text(
        citation_data.get("quotation_mode"), "citation.quotation_mode"
    )
    if quotation_mode == QuotationMode.FAITHFUL_PARAPHRASE.value:
        return CitationTextVerification(
            citation_id=citation_id,
            source_document_id=document_id,
            locator_kind=locator_kind,
            status=CitationTextStatus.SEMANTIC_REVIEW_REQUIRED,
            normalized_quoted_text=normalized_quote,
            scope_reference=scope.reference,
            detail=(
                "locator resolved, but faithful_paraphrase semantics are not "
                "automatically verified"
            ),
        )
    if quotation_mode not in {
        QuotationMode.EXACT_QUOTE.value,
        QuotationMode.TABLE_VALUE.value,
    }:
        raise SourceTextError(f"unsupported quotation_mode {quotation_mode!r}")
    if quotation_mode == QuotationMode.TABLE_VALUE.value and locator_kind != "table":
        raise LocatorResolutionError("table_value requires a table locator")
    if quotation_mode == QuotationMode.TABLE_VALUE.value:
        for field in ("row", "column"):
            if not isinstance(locator.get(field), str) or not locator[field].strip():
                raise LocatorResolutionError(
                    f"automatically verified table_value requires locator.{field}"
                )

    normalized_scope = normalize_quote_text(scope.text)
    matched = (
        normalized_scope == normalized_quote
        if quotation_mode == QuotationMode.TABLE_VALUE.value
        else _contains_literal(normalized_scope, normalized_quote)
    )
    return CitationTextVerification(
        citation_id=citation_id,
        source_document_id=document_id,
        locator_kind=locator_kind,
        status=(
            CitationTextStatus.VERIFIED
            if matched
            else CitationTextStatus.QUOTE_NOT_FOUND
        ),
        normalized_quoted_text=normalized_quote,
        scope_reference=scope.reference,
        detail=(
            (
                "quoted_text exactly matched the normalized table cell"
                if quotation_mode == QuotationMode.TABLE_VALUE.value
                else "quoted_text matched the normalized locator scope"
            )
            if matched
            else (
                "quoted_text did not exactly match the normalized table cell"
                if quotation_mode == QuotationMode.TABLE_VALUE.value
                else "quoted_text was not found in the normalized locator scope"
            )
        ),
    )


def _extract_pdf_pages(path: Path) -> tuple[str, ...]:
    try:
        reader = PdfReader(str(path), strict=True)
        if reader.is_encrypted:
            try:
                decrypted = reader.decrypt("")
            except Exception as exc:  # pypdf exposes several crypto errors
                raise SourceTextExtractionError(
                    "encrypted PDF snapshot cannot be read without a password"
                ) from exc
            if not decrypted:
                raise SourceTextExtractionError(
                    "encrypted PDF snapshot cannot be read without a password"
                )
        pages: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                pages.append(page.extract_text(extraction_mode="plain") or "")
            except Exception as exc:
                raise SourceTextExtractionError(
                    f"cannot extract PDF page {index}: {exc}"
                ) from exc
    except SourceTextExtractionError:
        raise
    except Exception as exc:
        raise SourceTextExtractionError(f"cannot parse PDF snapshot: {exc}") from exc
    if not pages:
        raise SourceTextExtractionError("PDF snapshot contains no pages")
    return tuple(pages)


def _build_view(
    page_texts: Sequence[str],
) -> tuple[tuple[TextPage, ...], tuple[TextLine, ...], tuple[TextParagraph, ...]]:
    pages: list[TextPage] = []
    all_lines: list[TextLine] = []
    all_paragraphs: list[TextParagraph] = []
    for page_number, raw_text in enumerate(page_texts, start=1):
        stable_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        raw_lines = stable_text.split("\n")
        if raw_lines and raw_lines[-1] == "":
            raw_lines.pop()
        page_lines: list[TextLine] = []
        for page_line_number, text in enumerate(raw_lines, start=1):
            line = TextLine(
                number=len(all_lines) + 1,
                page_number=page_number,
                page_line_number=page_line_number,
                text=text,
            )
            page_lines.append(line)
            all_lines.append(line)

        page_paragraphs: list[TextParagraph] = []
        pending: list[TextLine] = []

        def flush_paragraph() -> None:
            if not pending:
                return
            paragraph = TextParagraph(
                number=len(all_paragraphs) + 1,
                page_number=page_number,
                line_start=pending[0].number,
                line_end=pending[-1].number,
                text="\n".join(item.text for item in pending),
            )
            page_paragraphs.append(paragraph)
            all_paragraphs.append(paragraph)
            pending.clear()

        for line in page_lines:
            if line.text.strip():
                pending.append(line)
            else:
                flush_paragraph()
        flush_paragraph()
        pages.append(
            TextPage(
                number=page_number,
                text="\n".join(raw_lines),
                lines=tuple(page_lines),
                paragraphs=tuple(page_paragraphs),
            )
        )
    return tuple(pages), tuple(all_lines), tuple(all_paragraphs)


def _resolve_locator(view: SourceTextView, locator: Mapping[str, Any]) -> _LocatedScope:
    kind = _required_text(locator.get("kind"), "citation.locator.kind")
    if kind == "page":
        page_number = _positive_integer(locator.get("page"), "locator.page")
        page = _page(view, page_number)
        section = locator.get("section")
        if section is not None:
            lines = _resolve_section(page.lines, _required_text(section, "locator.section"))
            return _scope_from_lines(lines, f"page {page_number}, section {section!r}")
        return _LocatedScope(page.text, f"page {page_number}")

    if kind == "line_range":
        start = _positive_integer(locator.get("line_start"), "locator.line_start")
        end = _positive_integer(locator.get("line_end"), "locator.line_end")
        _ordered_range(start, end, "line")
        if end > len(view.lines):
            raise LocatorResolutionError(
                f"line range {start}-{end} exceeds {len(view.lines)} extracted lines"
            )
        return _scope_from_lines(view.lines[start - 1 : end], f"lines {start}-{end}")

    if kind == "paragraph":
        start = _positive_integer(
            locator.get("paragraph_start"), "locator.paragraph_start"
        )
        end = _positive_integer(locator.get("paragraph_end"), "locator.paragraph_end")
        _ordered_range(start, end, "paragraph")
        if end > len(view.paragraphs):
            raise LocatorResolutionError(
                f"paragraph range {start}-{end} exceeds "
                f"{len(view.paragraphs)} extracted paragraphs"
            )
        selected = view.paragraphs[start - 1 : end]
        section = locator.get("section")
        if section is not None:
            section_lines = _resolve_section(
                view.lines, _required_text(section, "locator.section")
            )
            allowed = {line.number for line in section_lines}
            if any(
                line_number not in allowed
                for paragraph in selected
                for line_number in range(paragraph.line_start, paragraph.line_end + 1)
            ):
                raise LocatorResolutionError(
                    f"paragraph range {start}-{end} is outside section {section!r}"
                )
        return _LocatedScope(
            "\n\n".join(item.text for item in selected),
            f"paragraphs {start}-{end}",
        )

    if kind == "section":
        section = _required_text(locator.get("section"), "locator.section")
        subsection_value = locator.get("subsection")
        subsection = (
            None
            if subsection_value is None
            else _required_text(subsection_value, "locator.subsection")
        )
        lines = _resolve_section(view.lines, section, subsection)
        reference = f"section {section!r}"
        if subsection is not None:
            reference += f", subsection {subsection!r}"
        return _scope_from_lines(lines, reference)

    if kind == "table":
        table = _required_text(locator.get("table"), "locator.table")
        page_value = locator.get("page")
        if page_value is None:
            matching_pages = [
                page
                for page in view.pages
                if _contains_normalized(page.text, table)
            ]
            if len(matching_pages) != 1:
                raise LocatorResolutionError(
                    f"table label {table!r} resolves to {len(matching_pages)} pages"
                )
            page = matching_pages[0]
        else:
            page = _page(view, _positive_integer(page_value, "locator.page"))
            if not _contains_normalized(page.text, table):
                raise LocatorResolutionError(
                    f"table label {table!r} was not found on page {page.number}"
                )
        return _resolve_table_scope(page, locator, table)

    raise LocatorResolutionError(f"unsupported locator kind {kind!r}")


def _resolve_section(
    lines: Sequence[TextLine], section: str, subsection: str | None = None
) -> tuple[TextLine, ...]:
    start, end = _section_bounds(lines, section)
    selected = tuple(lines[start:end])
    if subsection is None:
        return selected
    nested_start, nested_end = _section_bounds(selected, subsection)
    return selected[nested_start:nested_end]


def _section_bounds(lines: Sequence[TextLine], label: str) -> tuple[int, int]:
    normalized_label = normalize_quote_text(label)
    headings = _headings(lines)
    heading_matches = [
        item for item in headings if normalize_quote_text(item[2]) == normalized_label
    ]
    if len(heading_matches) > 1:
        raise LocatorResolutionError(f"section label {label!r} is ambiguous")
    if heading_matches:
        start, level, _ = heading_matches[0]
        end = len(lines)
        for position, next_level, _ in headings:
            if position > start and next_level <= level:
                end = position
                break
        return start, end

    exact_matches = [
        index
        for index, line in enumerate(lines)
        if normalize_quote_text(line.text) == normalized_label
    ]
    if len(exact_matches) != 1:
        raise LocatorResolutionError(
            f"section label {label!r} resolves to {len(exact_matches)} lines"
        )
    start = exact_matches[0]
    # A bare line in plain text or PDF output is not enough to infer that the
    # rest of the document belongs to that section.  Only a blank-line
    # paragraph boundary is an executable fallback; otherwise callers must use
    # a page or explicit line range.  This fails closed instead of letting a
    # quote from a later section satisfy the locator.
    end = next(
        (position for position in range(start + 1, len(lines)) if not lines[position].text.strip()),
        None,
    )
    if end is None:
        raise LocatorResolutionError(
            f"section label {label!r} has no executable end boundary; "
            "use a Markdown heading or line_range"
        )
    return start, end


def _resolve_table_scope(
    page: TextPage,
    locator: Mapping[str, Any],
    table: str,
) -> _LocatedScope:
    """Resolve a table to a bounded block or one unambiguous row.

    PDF extraction does not retain a trustworthy cell grid.  Automatic
    verification therefore never searches the whole page: the table caption
    must resolve to one extracted line, the header and rows must expose the
    same recognizable column structure, and a supplied row is narrowed to its
    requested cell.  Ambiguous layouts fail closed and can be represented with
    an explicit page/line locator plus human review.
    """

    caption_matches = [
        index
        for index, line in enumerate(page.lines)
        if _TABLE_CAPTION.match(line.text)
        and _contains_normalized(line.text, table)
    ]
    if len(caption_matches) != 1:
        raise LocatorResolutionError(
            f"table label {table!r} resolves to {len(caption_matches)} lines "
            f"on page {page.number}"
        )
    start = caption_matches[0]
    header_position = start + 1
    while (
        header_position < len(page.lines)
        and not page.lines[header_position].text.strip()
    ):
        header_position += 1
    if header_position >= len(page.lines):
        raise LocatorResolutionError(f"table {table!r} has no extracted header")

    header_line = page.lines[header_position]
    structured_header = _structured_table_cells(header_line.text)
    if structured_header is None:
        raise LocatorResolutionError(
            f"table {table!r} header has no recognizable column structure"
        )
    header_style, header_cells = structured_header

    data_position = header_position + 1
    if data_position < len(page.lines):
        possible_separator = _structured_table_cells(page.lines[data_position].text)
        if (
            possible_separator is not None
            and possible_separator[0] == header_style
            and len(possible_separator[1]) == len(header_cells)
            and all(
                _MARKDOWN_TABLE_SEPARATOR.fullmatch(cell)
                for cell in possible_separator[1]
            )
        ):
            data_position += 1

    data_rows: list[tuple[TextLine, tuple[str, ...]]] = []
    for position in range(data_position, len(page.lines)):
        line = page.lines[position]
        structured_row = _structured_table_cells(line.text)
        if structured_row is None:
            break
        row_style, cells = structured_row
        if row_style != header_style or len(cells) != len(header_cells):
            break
        data_rows.append((line, cells))
    if not data_rows:
        raise LocatorResolutionError(
            f"table {table!r} has no rows with a recognizable column mapping"
        )

    row_value = locator.get("row")
    column_value = locator.get("column")
    row = None if row_value is None else _required_text(row_value, "locator.row")
    column = (
        None
        if column_value is None
        else _required_text(column_value, "locator.column")
    )
    if row is None and column is not None:
        raise LocatorResolutionError("table column requires a row anchor")
    if row is None:
        return _scope_from_lines(
            (page.lines[start], header_line, *(line for line, _ in data_rows)),
            f"table {table!r} on page {page.number}, lines "
            f"{page.lines[start].page_line_number}-{data_rows[-1][0].page_line_number}",
        )

    row_matches = [
        (line, cells)
        for line, cells in data_rows
        if normalize_quote_text(cells[0]) == normalize_quote_text(row)
    ]
    if len(row_matches) != 1:
        raise LocatorResolutionError(
            f"table row label {row!r} resolves to {len(row_matches)} lines "
            f"inside table {table!r}"
        )
    row_line, row_cells = row_matches[0]
    if column is not None:
        column_matches = [
            index
            for index, cell in enumerate(header_cells)
            if normalize_quote_text(cell) == normalize_quote_text(column)
        ]
        if len(column_matches) != 1:
            raise LocatorResolutionError(
                f"table column label {column!r} resolves to {len(column_matches)} "
                f"header cells before row {row!r}"
            )
        column_index = column_matches[0]
        return _LocatedScope(
            row_cells[column_index],
            f"table {table!r}, row {row!r}, column {column!r} on page "
            f"{page.number}, line {row_line.page_line_number}",
        )
    return _LocatedScope(
        row_line.text,
        f"table {table!r}, row {row!r} on page {page.number}, "
        f"line {row_line.page_line_number}",
    )


def _structured_table_cells(text: str) -> tuple[str, tuple[str, ...]] | None:
    """Return cells only for a line with an executable column structure.

    Markdown pipes and tabs are explicit enough to map a named header to the
    same-position data cell. Runs of layout spaces are deliberately rejected:
    PDF prose and table output cannot be distinguished safely from spacing
    alone, so that representation requires human review.
    """

    stripped = text.strip()
    if not stripped:
        return None
    if "|" in stripped:
        style = "pipe"
        cells = [cell.strip() for cell in stripped.split("|")]
        if cells and not cells[0]:
            cells.pop(0)
        if cells and not cells[-1]:
            cells.pop()
    elif "\t" in stripped:
        style = "tab"
        cells = [cell.strip() for cell in re.split(r"\t+", stripped)]
    else:
        return None
    if len(cells) < 2 or any(not cell for cell in cells):
        return None
    return style, tuple(cells)


def _headings(lines: Sequence[TextLine]) -> tuple[tuple[int, int, str], ...]:
    result: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        atx = _ATX_HEADING.match(line.text)
        if atx:
            result.append((index, len(atx.group(1)), atx.group(2)))
            continue
        if index + 1 < len(lines):
            marker = _SETEXT_MARKER.match(lines[index + 1].text)
            if marker and line.text.strip():
                result.append(
                    (index, 1 if marker.group(1).startswith("=") else 2, line.text)
                )
    return tuple(result)


def _scope_from_lines(lines: Sequence[TextLine], reference: str) -> _LocatedScope:
    return _LocatedScope("\n".join(line.text for line in lines), reference)


def _page(view: SourceTextView, page_number: int) -> TextPage:
    if page_number > len(view.pages):
        raise LocatorResolutionError(
            f"page {page_number} exceeds {len(view.pages)} extracted pages"
        )
    return view.pages[page_number - 1]


def _contains_normalized(haystack: str, needle: str) -> bool:
    normalized_needle = normalize_quote_text(needle)
    return bool(normalized_needle) and _contains_literal(
        normalize_quote_text(haystack), normalized_needle
    )


def _contains_literal(normalized_haystack: str, normalized_needle: str) -> bool:
    """Find a literal without accepting a partial ASCII word or number token."""

    start = 0
    while True:
        position = normalized_haystack.find(normalized_needle, start)
        if position < 0:
            return False
        end = position + len(normalized_needle)
        before = normalized_haystack[position - 1] if position else ""
        after = normalized_haystack[end] if end < len(normalized_haystack) else ""
        first = normalized_needle[0]
        last = normalized_needle[-1]
        starts_cleanly = not (
            _is_ascii_word(first) and _is_ascii_word(before)
        )
        ends_cleanly = not (_is_ascii_word(last) and _is_ascii_word(after))
        numeric_start_is_clean = not (
            first.isascii() and first.isdigit() and before in {".", ","}
        )
        numeric_end_is_clean = not (
            last.isascii() and last.isdigit() and after in {".", ","}
        )
        if (
            starts_cleanly
            and ends_cleanly
            and numeric_start_is_clean
            and numeric_end_is_clean
        ):
            return True
        start = position + 1


def _is_ascii_word(value: str) -> bool:
    return bool(value) and value.isascii() and (value.isalnum() or value == "_")


def _ordered_range(start: int, end: int, label: str) -> None:
    if end < start:
        raise LocatorResolutionError(f"{label}_end cannot precede {label}_start")


def _positive_integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise LocatorResolutionError(f"{label} must be a positive integer")
    return value


def _model_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return result
    raise TypeError(f"{label} must be a mapping or domain model")


def _enum_text(value: Any, label: str) -> str:
    enum_value = getattr(value, "value", value)
    return _required_text(enum_value, label)


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceTextError(f"{label} must be a non-empty string")
    return value
