"""Shared data models (SPEC §4).

Every value that crosses a module boundary is one of these frozen Pydantic
models. Field names of the models that reach the API match
``web/src/lib/rfq/contract.ts``; ``tests/contract`` validates real responses
against the JSON Schema files in ``/contract``.

Internal models (``ParsedSentence``, ``Clause``, ``ConsistencyIssue`` …) carry
judgements such as ``is_cost_sensitive`` that must never be serialised into a
response as they are. ``autorfx.access`` turns them into response models.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["must", "should", "optional", "info"]
CitationStatus = Literal["verified", "unverified"]
Group = Literal["cost_visible", "cost_restricted"]
Lang = Literal["en", "de", "ko", "mixed", "unknown"]
FileKind = Literal["pdf", "doc", "docx", "xlsx"]
Origin = Literal["fixture", "rule", "llm", "rule_fallback"]
Provenance = Literal["mock", "real"]
StageStatus = Literal["pending", "running", "done", "error", "skipped"]
IssueCategory = Literal["conflict", "missing_attachment", "broken_reference", "notation"]
Severity = Literal["high", "medium", "low"]
FindingKind = Literal["summary", "priority", "red_flag", "answer", "cost_input", "consistency"]
SentenceRole = Literal["heading", "body", "list", "table", "form"]
ClauseKind = Literal["clause", "table", "form", "front_matter"]
DecisionStatus = Literal["go", "no_go", "hold", "baseline_undefined"]

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class Model(BaseModel):
    """Immutable, closed model: unknown fields are an error (INV-9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# ── Location ────────────────────────────────────────────────────────────────
class Locus(Model):
    """One sentence of one file of the package (INV-2).

    ``page_number`` is a page of that file's canonical PDF. ``sentence_index``
    and ``paragraph_index`` count from 1 inside the page.
    """

    source_doc: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    section_id: str = Field(min_length=1)
    paragraph_index: int = Field(ge=1)
    sentence_index: int = Field(ge=1)
    anchor: str | None = None

    def key(self) -> tuple[str, int, int]:
        """Identity of the sentence, independent of the section it was assigned to."""
        return (self.source_doc, self.page_number, self.sentence_index)


class Rect(Model):
    """Rectangle as a fraction of the page, origin at the top-left."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0, le=1)
    h: float = Field(ge=0, le=1)


class Sentence(Model):
    """Response shape of a sentence."""

    locus: Locus
    text: str = Field(min_length=1)
    rects: tuple[Rect, ...]


class ParsedSentence(Model):
    """A sentence as the parser produced it. ``text`` is the original, untouched (INV-1)."""

    locus: Locus
    text: str = Field(min_length=1)
    rects: tuple[Rect, ...]
    role: SentenceRole = "body"

    def public(self) -> Sentence:
        return Sentence(locus=self.locus, text=self.text, rects=self.rects)


# ── Spreadsheets and tables ─────────────────────────────────────────────────
class SheetCell(Model):
    """One non-empty cell. ``anchor`` is ``"<sheet>!<A1 address>"``."""

    anchor: str
    column: str
    text: str
    rect: Rect
    is_input: bool = False
    """True when the cell is an empty or zero field the supplier is asked to fill in."""


class SheetRow(Model):
    """One rendered spreadsheet row and where it sits in the canonical PDF."""

    sheet: str
    row_index: int = Field(ge=1)
    page_number: int = Field(ge=1)
    rect: Rect
    cells: tuple[SheetCell, ...]


class PageRect(Model):
    page_number: int = Field(ge=1)
    rect: Rect


class LayoutBlock(Model):
    """One paragraph of a rendered word-processor file and where it sits in the canonical PDF.

    ``text`` is read from the source format, so it has no ligatures or line-break
    hyphens introduced by rendering (INV-1). Coordinates come from the rendering.
    """

    index: int = Field(ge=0)
    text: str
    role: SentenceRole = "body"
    level: int | None = None
    """Heading level, when the source marks the paragraph as a heading."""
    fragments: tuple[PageRect, ...]
    """One per page the paragraph touches."""


class FileLayout(Model):
    """Written by the converter next to the canonical PDF; read by the parser.

    ``rows`` for spreadsheets, ``blocks`` for word-processor files. PDF originals
    have no layout file: the parser reads them directly.
    """

    file_id: str
    blocks: tuple[LayoutBlock, ...] = ()
    rows: tuple[SheetRow, ...] = ()


class Table(Model):
    """A table of a PDF page or a spreadsheet sheet, with one locus per row."""

    source_doc: str
    page_number: int = Field(ge=1)
    section_id: str
    title: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    row_loci: tuple[Locus, ...]
    cell_anchors: tuple[tuple[str | None, ...], ...] = ()


class SectionInfo(Model):
    """A section the parser found. ``section_id`` is unique inside the file."""

    section_id: str = Field(min_length=1)
    title: str = ""
    path: tuple[str, ...] = ()
    kind: ClauseKind = "clause"
    heading_only: bool = False
    """True for a heading with no text of its own: it names its children and is not a clause."""


class ParsedFile(Model):
    """Everything the parser read from one file's canonical PDF."""

    file_id: str
    lang: Lang
    page_count: int = Field(ge=1)
    sentences: tuple[ParsedSentence, ...]
    sections: tuple[SectionInfo, ...] = ()
    tables: tuple[Table, ...] = ()
    warnings: tuple[str, ...] = ()


# ── Package ─────────────────────────────────────────────────────────────────
class FileRecord(Model):
    """One file of the package. The original is stored read-only and never changed (INV-1)."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: FileKind
    lang: Lang = "unknown"
    page_count: int = Field(ge=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    canonical_tool: str = Field(min_length=1)
    canonical_sha256: str = Field(pattern=SHA256_PATTERN)
    clause_count: int = Field(ge=0, default=0)
    warnings: tuple[str, ...] = ()


class PackageManifest(Model):
    """An RFQ: a set of files received together."""

    id: str
    name: str
    files: tuple[FileRecord, ...]
    page_count: int = Field(ge=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    is_sample: bool
    notice: str | None = None
    created_at: int
    """Milliseconds since the epoch; the only non-deterministic field of the manifest."""


# ── Clauses ─────────────────────────────────────────────────────────────────
class Clause(Model):
    """A clause chunk: the unit of analysis and retrieval (CHUNK-01, INV-2)."""

    clause_id: str = Field(min_length=1)
    source_doc: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    section_path: tuple[str, ...]
    title: str
    kind: ClauseKind = "clause"
    lang: Lang = "unknown"
    sentences: tuple[ParsedSentence, ...] = Field(min_length=1)
    is_cost_sensitive: bool = False
    is_boilerplate: bool = False

    @property
    def page_number(self) -> int:
        return self.sentences[0].locus.page_number

    @property
    def sentence_index(self) -> int:
        return self.sentences[0].locus.sentence_index

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)

    @property
    def own_title(self) -> str:
        """The title when it heads this clause itself. A title inherited from the chapter says
        nothing about the clause: "Prices and terms" also heads the delivery-term clause."""
        return self.title if self.sentences[0].role == "heading" or self.kind != "clause" else ""


# ── Analysis ────────────────────────────────────────────────────────────────
class RedFlag(Model):
    type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    reason: str


class Finding(Model):
    """A statement the system makes about the RFQ. It must cite sentences (CITE-01)."""

    kind: FindingKind
    departments: tuple[str, ...] = ()
    text: str
    priority: Priority | None = None
    red_flag_reason: str | None = None
    citations: tuple[Locus, ...] = ()


class VerifiedFinding(Model):
    finding: Finding
    citation_status: CitationStatus
    failed_citations: tuple[Locus, ...] = ()


class ClauseAnalysis(Model):
    """The judgement on one clause. The lead department comes first (ROUTE-01)."""

    clause_id: str
    primary_department: str = Field(min_length=1)
    reference_departments: tuple[str, ...] = ()
    priority: Priority
    red_flag: RedFlag | None = None
    summary: VerifiedFinding
    translation_ko: str | None = None
    origin: Origin


# ── Consistency ─────────────────────────────────────────────────────────────
class EntityMention(Model):
    """One value of one entity found in the package (CONSIST-01)."""

    entity: str
    scope: str | None = None
    """What the value belongs to, e.g. a part number; None when package-wide."""
    raw: str
    normalized: str
    unit: str | None = None
    locus: Locus
    text: str
    is_cost_sensitive: bool = False


class IssueEvidence(Model):
    locus: Locus
    text: str
    value: str | None = None
    normalized: str | None = None


class ConsistencyIssue(Model):
    """A cross-document finding. ``is_cost_sensitive`` is internal: see autorfx.access."""

    id: str = Field(min_length=1)
    category: IssueCategory
    severity: Severity
    entity: str | None = None
    title: str
    detail: str
    affects_cost: bool = False
    cost_fields: tuple[str, ...] = ()
    """Cost inputs this issue blocks, e.g. ("net_weight", "gross_weight")."""
    departments: tuple[str, ...] = ()
    evidence: tuple[IssueEvidence, ...]
    citation_status: CitationStatus = "verified"
    is_cost_sensitive: bool = False


# ── Pre-cost ────────────────────────────────────────────────────────────────
class PrecostInput(Model):
    """Top-level inputs of the unit-cost formula (SPEC §8.1)."""

    item: str = Field(min_length=1)
    min_quantity: int = Field(gt=0)
    fixed_costs: Decimal = Field(ge=0)
    variable_cost_per_unit: Decimal = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    target_price: Decimal | None = Field(default=None, ge=0)


class BreakdownInput(Model):
    """Line items of the cost breakdown (SPEC §8.2). Rates are fractions: 0.05 is 5 %."""

    gross_weight: Decimal = Field(ge=0)
    net_weight: Decimal = Field(ge=0)
    material_price: Decimal = Field(ge=0)
    scrap_credit: Decimal = Field(ge=0)
    machine_hour_rate: Decimal = Field(ge=0)
    strokes_per_minute: Decimal = Field(gt=0)
    parts_per_stroke: Decimal = Field(gt=0)
    oee: Decimal = Field(gt=0, le=1)
    surface_cost: Decimal = Field(ge=0)
    packaging_cost: Decimal = Field(ge=0)
    freight_cost: Decimal = Field(ge=0)
    warehousing_cost: Decimal = Field(ge=0)
    scrap_rate: Decimal = Field(ge=0, lt=1)
    sga_rate: Decimal = Field(ge=0)
    profit_rate: Decimal = Field(ge=0)


class CostAlternative(Model):
    value: str
    evidence: VerifiedFinding


class CostInputCandidate(Model):
    """A cost input found in the RFQ, shown with its citation before it is used (COST-05)."""

    field: str
    label: str
    unit: str | None = None
    value: str | None = None
    source: Literal["rfq", "assumption", "missing"]
    note: str = ""
    evidence: VerifiedFinding | None = None
    alternatives: tuple[CostAlternative, ...] = ()
    issue_id: str | None = None


# ── Job progress ────────────────────────────────────────────────────────────
class JobStage(Model):
    id: str
    label: str
    detail: str
    part: str
    status: StageStatus


class JobState(Model):
    started_at: int
    done: bool
    stages: tuple[JobStage, ...]
