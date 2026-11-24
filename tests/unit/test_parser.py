"""Sentence splitting, section structure, PDF and sheet parsing, clause chunking."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from autorfx.chunker import apply_cost_entities, build_clauses
from autorfx.models import (
    EntityMention,
    FileLayout,
    ParsedFile,
    Rect,
    SectionInfo,
    SheetCell,
    SheetRow,
)
from autorfx.parser import ParseError, detect_language, split_sentences
from autorfx.parser.pdf import parse_pdf
from autorfx.parser.sheets import parse_sheets
from autorfx.parser.structure import Outliner, detect_mode, find_index_runs
from autorfx.rules import Ruleset
from factories import make_file, make_locus, make_sentence, pdf_from_html


# ── sentences ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("The supplier shall deliver. The buyer may inspect.", 2),
        ("Delivery is F.O.B. destination within 30 days. Payment follows.", 2),
        ("See No. 5 for details, e.g. the tolerance. Next sentence.", 2),
        ("Die Abgabe erfolgt am 4. Dezember 2026. Der Lieferant muss liefern.", 2),
        ("Der Preis beträgt 1.234,56 EUR bzw. 1,20 EUR je Stück. Zahlung nach 90 Tagen.", 2),
        ("Is this required? Yes! It is.", 3),
        ("Maß 2,5 mm ± 0,15 (siehe Abschnitt 4.6 dieser Anfrage).", 1),
        ("The value is 1,234.56 USD at rev. 2 of the drawing.", 1),
        ("A title without a period", 1),
        ("   ", 0),
        ("1. The bidder shall submit one copy. Late bids are rejected.", 2),
        ("a. Proof of insurance.", 1),
        ("ii. Pricing must be firm for the term.", 1),
        ("Die Haftung ist auf 5 Mio. EUR je Schadensfall begrenzt.", 1),
        ("2021. The year the contract began.", 2),
    ],
)
def test_sentence_boundaries(text: str, count: int):
    """PARSE-01: abbreviations, ordinals, decimals, clause numbers and list markers do not end a sentence."""
    assert len(split_sentences(text)) == count


def test_spans_cover_the_text_and_never_change_it():
    """INV-1: sentences are slices of the original; nothing is dropped or rewritten."""
    text = "  First one.  Second, with e.g. an abbreviation!   Third without end "
    spans = split_sentences(text)
    assert [text[a:b] for a, b in spans] == [
        "First one.",
        "Second, with e.g. an abbreviation!",
        "Third without end",
    ]
    covered = "".join(text[a:b] for a, b in spans)
    assert "".join(covered.split()) == "".join(text.split())


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("The supplier shall deliver the parts and the buyer will pay for all of them.", "en"),
        ("Der Lieferant muss die Teile liefern und die Prüfung ist nach der Zeichnung auszuführen.", "de"),
        ("Part X-1", "unknown"),
        ("공급사는 부품을 납품해야 하며 구매자는 검사를 수행할 수 있습니다. 결제는 90일입니다.", "ko"),
        ("The supplier und der Lieferant shall muss the die parts", "mixed"),
    ],
)
def test_language_detection(text: str, lang: str):
    assert detect_language(text) == lang


# ── structure ───────────────────────────────────────────────────────────────
def test_decimal_outline_accepts_clauses_and_rejects_values():
    """PARSE-02: a number opens a section only when it fits the outline and is not a value."""
    outline = Outliner("decimal")
    feed = lambda text: h.section_id if (h := outline.feed(text)) else None  # noqa: E731
    assert feed("1 Scope") == "1"
    assert feed("1.1 The supplier shall deliver the bracket.") == "1.1"
    assert feed("2.5 mm ± 0.15 mm is the permitted thickness.") is None
    assert feed("04.12.2026 is the due date for the quotation.") is None
    assert feed("2 Requirements") == "2"
    assert feed("5 Werktage vor SOP sind Muster zu liefern.") is None
    assert feed("2.1 Parts must be free of burrs.") == "2.1"
    assert feed("4.1 A clause from a chapter that was never opened.") is None
    assert feed("2.2 Coating thickness is at least 20 µm.") == "2.2"


def test_clause_title_is_its_own_or_its_chapters():
    """PARSE-02: a document may start at any chapter heading; a clause without a title takes its chapter's."""
    outline = Outliner("decimal")
    assert outline.feed("3.1 A clause before any chapter heading is not trusted.") is None
    chapter = outline.feed("3 Commercial terms")
    clause = outline.feed("3.1 Delivery is FCA supplier plant.")
    assert chapter is not None and chapter.title_like and chapter.title == "Commercial terms"
    assert clause is not None and not clause.title_like
    assert (clause.title, clause.path) == ("Commercial terms", ("3", "3.1"))
    assert clause.label_len == len("3.1 ")


def test_roman_chapters_with_letter_sections():
    """PARSE-02: "I." after section H is the letter I, not chapter I; contents lines are not headings."""
    texts = [
        "I. STATEMENT OF WORK",
        "A. INTENT",
        "The county intends to award a contract.",
        "H. PRICING",
        "I. INVOICING",
        "1. Invoices shall be sent monthly.",
        "II. TERMS AND CONDITIONS",
        "A. AWARD",
        "B. WARRANTY 12",
    ]
    assert detect_mode(texts) == "alpha"
    outline = Outliner("alpha")
    found = [h.section_id if (h := outline.feed(t)) else None for t in texts]
    assert found == ["I", "I.A", None, "I.H", "I.I", None, "II", "II.A", None]


def test_numbered_standard_clauses():
    """PARSE-02: "II.4 52.212-4 …" is a clause of section II; numbers may skip but never go back."""
    outline = Outliner("decimal")
    first = outline.feed("II.1 52.204-99 EXAMPLE CLAUSE TITLE (JAN 2099)")
    assert first is not None and first.section_id == "II.1" and first.path == ("II", "II.1")
    assert outline.feed("II.4 52.212-99 ANOTHER CLAUSE").section_id == "II.4"  # type: ignore[union-attr]
    assert outline.feed("II.2 52.200-1 OUT OF ORDER") is None
    plain = Outliner("decimal").feed("52.204-7 System for registration")
    assert plain is not None and plain.section_id == "52.204-7"


def test_repeated_section_ids_stay_unique():
    """INV-2: a section id names one section of a file, so a repeated number gets a suffix."""
    outline = Outliner("decimal")
    ids = [outline.feed(t).section_id for t in ("52.212-4 First", "52.212-4 Again")]  # type: ignore[union-attr]
    assert ids == ["52.212-4", "52.212-4#2"]


def test_index_of_clauses_is_recognised():
    """PARSE-02: a run of labelled titles with no text between them is a list of clauses, not the clauses."""
    texts = (
        ["Cover text."] + [f"II.{n} 52.200-{n} TITLE {n}" for n in range(1, 7)] + ["II.1 The clause itself."]
    )
    assert find_index_runs(texts) == set(range(1, 7))
    assert find_index_runs(["1 Scope", "1.1 Body.", "2 Terms", "2.1 Body."]) == set()


def test_a_short_numbered_list_that_is_the_content_is_kept():
    """INV-8, PARSE-02 (review CRITICAL): five price lines are not a table of contents, because none of
    them is stated again later; dropping them hid nothing and lost the target price."""
    texts = [
        "1.1 The supplier shall deliver stamped brackets.",
        "2 Commercial terms",
        "2.1 Target price EUR 1.20 per piece",
        "2.2 Annual price reduction 3 % from the second year",
        "2.3 Payment terms 60 days net",
        "2.4 Currency EUR",
        "2.5 Incoterm FCA Bremen",
        "3 Quality",
        "The supplier shall be certified to IATF 16949.",
    ]
    assert find_index_runs(texts) == set()


# ── PDF ─────────────────────────────────────────────────────────────────────
HTML = (
    "<p>Request for quotation RFQ-TEST-1 issued by the buyer.</p>"
    "<h2>1 Scope</h2>"
    "<p>1.1 The supplier shall deliver the bracket to the buyer's plant every week without any exception "
    "and shall keep a safety stock that covers ten working days of demand. The buyer may inspect it.</p>"
    "<p>1.2 Sheet thickness is 2.5 mm.</p>"
    "<h2>2 Quality</h2>"
    "<p>2.1 The supplier must be certified.</p>"
)


def test_pdf_is_parsed_into_sentences_sections_and_positions(tmp_path: Path):
    """PARSE-01, CITE-03, INV-2: every sentence has a position, a section and rectangles inside the page."""
    parsed = parse_pdf("d01", pdf_from_html(tmp_path / "a.pdf", HTML))
    assert parsed.lang == "en" and parsed.page_count == 1
    assert [(s.section_id, s.kind, s.heading_only) for s in parsed.sections] == [
        ("0", "front_matter", False),
        ("1", "clause", True),
        ("1.1", "clause", False),
        ("1.2", "clause", False),
        ("2", "clause", True),
        ("2.1", "clause", False),
    ]
    clause = [s for s in parsed.sentences if s.locus.section_id == "1.1"]
    assert [s.text.split()[0] for s in clause] == ["1.1", "The"]
    assert len(clause[0].rects) >= 2, "a wrapped sentence has one rectangle per line"
    assert [s.locus.sentence_index for s in parsed.sentences] == list(range(1, len(parsed.sentences) + 1))
    for sentence in parsed.sentences:
        assert sentence.rects
        for r in sentence.rects:
            assert r.w > 0 and r.h > 0 and r.x + r.w <= 1.0001 and r.y + r.h <= 1.0001


def test_parsing_is_deterministic(tmp_path: Path):
    """INV-7: the same file parsed twice gives identical output."""
    path = pdf_from_html(tmp_path / "a.pdf", HTML)
    assert parse_pdf("d01", path).model_dump_json() == parse_pdf("d01", path).model_dump_json()


def test_clauses_follow_sections(tmp_path: Path, ruleset: Ruleset):
    """CHUNK-01, CHUNK-02: one clause per section with text; bare headings are not clauses."""
    parsed = parse_pdf("d01", pdf_from_html(tmp_path / "a.pdf", HTML))
    clauses = build_clauses(parsed, make_file(), ruleset)
    assert [(c.clause_id, c.kind, c.title) for c in clauses] == [
        ("d01:0", "front_matter", ""),
        ("d01:1.1", "clause", "Scope"),
        ("d01:1.2", "clause", "Scope"),
        ("d01:2.1", "clause", "Quality"),
    ]
    assert clauses[1].section_path == ("1", "1.1")
    assert all(s.locus.section_id == c.section_id for c in clauses for s in c.sentences)


def _page_with(doc: pymupdf.Document, number: int, body: str) -> None:
    page = doc.new_page()
    page.insert_text((72, 28), "ACME RFQ-TEST-1 confidential", fontsize=8)
    page.insert_textbox(pymupdf.Rect(72, 100, 520, 300), body, fontsize=10)
    page.insert_text((280, 825), f"Page {number} of 3", fontsize=8)


def test_repeated_headers_and_footers_are_not_body_text(tmp_path: Path):
    """PARSE-01: page furniture is taken out and counted, the body stays."""
    doc = pymupdf.open()
    for number in (1, 2, 3):
        _page_with(
            doc, number, f"{number} Chapter\nThe supplier shall follow rule number {number} at all times."
        )
    path = tmp_path / "f.pdf"
    doc.save(str(path))
    doc.close()
    parsed = parse_pdf("d01", path)
    text = " ".join(s.text for s in parsed.sentences)
    assert "confidential" not in text and "Page 2" not in text
    assert "rule number 3" in text
    assert any("머리글·바닥글 6개" in w for w in parsed.warnings)


def test_file_without_a_text_layer_is_an_error(tmp_path: Path):
    """PARSE-04: a scanned document is refused; it never yields an empty result."""
    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(100, 100, 300, 300), color=(0, 0, 0))
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    with pytest.raises(ParseError, match="텍스트 레이어"):
        parse_pdf("d01", path)


# ── sheets ──────────────────────────────────────────────────────────────────
def _row(sheet: str, index: int, cells: list[tuple[str, str, bool]], page: int = 1) -> SheetRow:
    rect = Rect(x=0.05, y=min(0.05 + 0.03 * index, 0.9), w=0.9, h=0.02)
    return SheetRow(
        sheet=sheet,
        row_index=index,
        page_number=page,
        rect=rect,
        cells=tuple(
            SheetCell(anchor=f"{sheet}!{col}{index}", column=col, text=text, rect=rect, is_input=is_input)
            for col, text, is_input in cells
        ),
    )


def test_sheet_rows_become_citable_units_and_tables():
    """PARSE-03, CITE-03: a row is one unit with a cell anchor; blocks of rows are tables with headers."""
    layout = FileLayout(
        file_id="d03",
        rows=(
            _row("Part Data", 1, [("A", "Synthetic sheet", False)]),
            _row("Part Data", 5, [("A", "Part number", False), ("C", "Thickness [mm]", False)]),
            _row("Part Data", 6, [("A", "X-1", False), ("C", "2", False)]),
            _row("Part Data", 7, [("A", "X-2", False), ("C", "", True)]),
            _row("Form", 1, [("B", "", True)]),
        ),
    )
    parsed = parse_sheets("d03", layout, page_count=1)
    assert [(s.text, s.locus.anchor, s.role) for s in parsed.sentences] == [
        ("Synthetic sheet", "Part Data!A1", "table"),
        ("Part number | Thickness [mm]", "Part Data!A5:C5", "table"),
        ("X-1 | 2", "Part Data!A6:C6", "table"),
        ("X-2", "Part Data!A7", "table"),
    ]
    assert [s.section_id for s in parsed.sections] == ["Part Data"]
    table = parsed.tables[1]
    assert table.header == ("Part number", "Thickness [mm]")
    assert table.header_anchors == ("Part Data!A5", "Part Data!C5")
    assert table.rows == (("X-1", "2"), ("X-2", ""))
    assert table.cell_anchors[0] == ("Part Data!A6", "Part Data!C6")
    assert [locus.sentence_index for locus in table.row_loci] == [3, 4]


# ── cost sensitivity ────────────────────────────────────────────────────────
def _parsed(sections: list[tuple[str, str, list[tuple[str, str]]]]) -> ParsedFile:
    sentences, infos, index = [], [], 0
    for section_id, title, items in sections:
        infos.append(SectionInfo(section_id=section_id, title=title, path=(section_id,)))
        for role, text in items:
            index += 1
            sentences.append(make_sentence(text, index, role=role, section_id=section_id))  # type: ignore[arg-type]
    return ParsedFile(
        file_id="d01", lang="en", page_count=1, sentences=tuple(sentences), sections=tuple(infos)
    )


def test_cost_sensitivity_follows_the_rules_not_the_presence_of_an_amount(ruleset: Ruleset):
    """RBAC-02: a price clause is hidden; a liability cap or a delivery term in a "prices" chapter is not."""
    parsed = _parsed(
        [
            ("4.1", "Prices and terms", [("body", "4.1 Delivery is FCA supplier plant.")]),
            ("4.2", "Prices and terms", [("body", "4.2 The target price is 1.20 EUR per piece.")]),
            ("6.1", "Liability", [("body", "6.1 Liability is limited to EUR 5,000,000 per event.")]),
            ("7", "Pricing", [("heading", "7 Pricing"), ("body", "Quotes are valid for twelve months.")]),
            ("II.3", "", [("body", "II.3 52.212-99 EXAMPLE CLAUSE The contractor shall comply.")]),
        ]
    )
    clauses = {c.section_id: c for c in build_clauses(parsed, make_file(), ruleset)}
    assert [k for k, c in clauses.items() if c.is_cost_sensitive] == ["4.2", "7"]
    assert [k for k, c in clauses.items() if c.is_boilerplate] == ["II.3"]


def test_a_cost_entity_makes_its_clause_cost_sensitive(ruleset: Ruleset):
    """RBAC-02: a clause that states a cost entity is hidden even when no expression matched."""
    parsed = _parsed(
        [("1", "", [("body", "1 Row with a figure 1.2 in a column.")]), ("2", "", [("body", "2 Other row.")])]
    )
    clauses = build_clauses(parsed, make_file(), ruleset)
    mention = EntityMention(
        entity="target_price", raw="1.2", normalized="1.2", locus=make_locus(1, section_id="1"), text="row"
    )
    marked = apply_cost_entities(clauses, [mention], ruleset)
    assert [c.is_cost_sensitive for c in marked] == [True, False]
    assert not clauses[0].is_cost_sensitive, "the input is not mutated"
    other = mention.model_copy(update={"entity": "thickness"})
    assert apply_cost_entities(clauses, [other], ruleset) == clauses
