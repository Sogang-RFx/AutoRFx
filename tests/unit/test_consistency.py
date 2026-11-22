"""Value normalisation, entity mentions and cross-document checks. All values are invented."""

from __future__ import annotations

from decimal import Decimal

import pytest

from autorfx.chunker import apply_cost_entities
from autorfx.consistency import check_package, extract_mentions
from autorfx.consistency.normalize import dates_agree, multiplier_of, parse_dates, parse_number, plain
from autorfx.models import Clause, ParsedFile, SectionInfo, Table
from autorfx.rules import Ruleset
from factories import make_clause, make_file, make_locus, make_manifest


# ── normalisation ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("1.234,56", "de", "1234.56"),
        ("150.000", "de", "150000"),
        ("2,5", "de", "2.5"),
        ("2.5", "de", "2.5"),
        ("1,234.56", "en", "1234.56"),
        ("200,000", "en", "200000"),
        ("150.000", "en", "150"),
        ("2.50", "en", "2.5"),
        ("0,38", "de", "0.38"),
    ],
)
def test_numbers_follow_the_language_of_the_text(text: str, lang: str, expected: str):
    """CONSIST-02: "150.000" is 150 000 in German and 150 in English."""
    value = parse_number(text, lang)
    assert value is not None and plain(value) == expected


def test_what_is_not_a_number_is_not_guessed():
    assert parse_number("HC420LA", "en") is None and parse_number("", "en") is None


@pytest.mark.parametrize(
    ("label", "factor"),
    [("Volume [k pcs]", 1000), ("Menge (Tsd. Stück)", 1000), ("Mio. EUR", 1_000_000), ("Net weight [kg]", 1)],
)
def test_unit_multipliers_in_headers(label: str, factor: int):
    """CONSIST-02: "kg" is not a "k" multiplier; "[k pcs]" is."""
    assert multiplier_of(label) == Decimal(factor)


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("bis 04.12.2026 einzureichen", "de", ("2026-12-04",)),
        ("due 12/04/2026", "en", ("2026-12-04", "2026-04-12")),
        ("due 31/01/2027", "en", ("2027-01-31",)),
        ("SOP 06/2028", "en", ("2028-06",)),
        ("März 2028 (KW 10/2028)", "de", ("2028-03",)),
        ("KW 10/2028", "de", ("2028-03",)),
        ("in March 2028", "en", ("2028-03",)),
        ("2028-03-06", "en", ("2028-03-06",)),
        ("no date here", "en", ()),
    ],
)
def test_dates_are_canonical_and_ambiguous_ones_keep_both_readings(
    text: str, lang: str, expected: tuple[str, ...]
):
    assert parse_dates(text, lang) == expected


def test_dates_agree_at_the_coarser_precision():
    """CONSIST-03: German and US order of the same day agree; a month agrees with a day in it."""
    assert dates_agree("2026-12-04", "2026-12-04|2026-04-12")
    assert dates_agree("2028-03", "2028-03-06")
    assert not dates_agree("2028-03", "2028-06")


# ── a small invented package ────────────────────────────────────────────────
def _sheet(file_id: str) -> tuple[Clause, Table]:
    header = ("Part number", "Thickness [mm]", "Target price [EUR/pc]", "Quote due")
    rows = (("AB-100.200", "2", "1.2", "12/04/2026"),)
    locus = make_locus(2, source_doc=file_id, section_id="Part Data", anchor="Part Data!A2:D2")
    table = Table(
        source_doc=file_id,
        page_number=1,
        section_id="Part Data",
        title="Part Data",
        header=header,
        rows=rows,
        row_loci=(locus,),
        cell_anchors=(("Part Data!A2", "Part Data!B2", "Part Data!C2", "Part Data!D2"),),
    )
    clause = make_clause(
        "Part Data", [" | ".join(header), " | ".join(rows[0])], source_doc=file_id, kind="table"
    )
    return clause, table


def _package(extra_main: list[str] | None = None):
    main = [
        make_clause("1.1", ["1.1 Angebote sind bis 04.12.2026 einzureichen (Angebotsabgabe)."], lang="de"),
        make_clause("2.1", ["2.1 Lieferbedingung: FCA Werk Lieferant (Incoterms 2020)."], lang="de"),
        make_clause(
            "2.2", ["2.2 Siehe Abschnitt 2.7 dieser Anfrage und Anlage 9."], lang="de", first_sentence=3
        ),
        make_clause("9.9", extra_main or ["9.9 Kontakt: Einkauf (EK-KR)."], lang="de", first_sentence=4),
    ]
    spec = [
        make_clause("3.1", ["3.1 Sheet thickness 2.5 mm, material HC420LA."], source_doc="d02"),
        make_clause(
            "3.2",
            ["3.2 Delivery terms: DDP buyer plant (Incoterms 2020)."],
            source_doc="d02",
            first_sentence=2,
        ),
        make_clause(
            "3.3",
            ["3.3 Testing follows ISO 9227 and Annex 3; contact code EK-RK."],
            source_doc="d02",
            first_sentence=3,
        ),
    ]
    sheet_clause, table = _sheet("d03")
    files = (
        make_file("d01", "01_anfrage.docx"),
        make_file("d02", "02_spec.pdf"),
        make_file("d03", "03_data.xlsx"),
    )

    def parsed(file_id: str, clauses: list[Clause], tables: tuple[Table, ...] = ()) -> ParsedFile:
        return ParsedFile(
            file_id=file_id,
            lang="en",
            page_count=1,
            sentences=tuple(s for c in clauses for s in c.sentences),
            sections=tuple(SectionInfo(section_id=c.section_id, kind=c.kind) for c in clauses),
            tables=tables,
        )

    files_parsed = [parsed("d01", main), parsed("d02", spec), parsed("d03", [sheet_clause], (table,))]
    return make_manifest(files), files_parsed, [*main, *spec, sheet_clause]


def _run(ruleset: Ruleset, **kwargs: object):
    manifest, parsed, clauses = _package(**kwargs)  # type: ignore[arg-type]
    mentions = extract_mentions(parsed, clauses, ruleset)
    clauses = list(apply_cost_entities(clauses, mentions, ruleset))
    mentions = extract_mentions(parsed, clauses, ruleset)
    return check_package(manifest, parsed, clauses, mentions, ruleset), mentions, clauses


def test_mentions_come_from_text_and_from_table_cells(ruleset: Ruleset):
    """CONSIST-01: a table value is found under its header and keeps its cell anchor and part number."""
    _, mentions, _ = _run(ruleset)
    by_entity = {(m.entity, m.locus.source_doc): m for m in mentions}
    assert by_entity[("thickness", "d02")].normalized == "2.5"
    cell = by_entity[("thickness", "d03")]
    assert (cell.normalized, cell.locus.anchor, cell.scope) == ("2", "Part Data!B2", "AB-100.200")
    assert by_entity[("incoterm", "d01")].normalized == "FCA"
    assert by_entity[("material", "d02")].normalized == "hc420la"
    assert by_entity[("target_price", "d03")].normalized == "1.2"


def test_conflicts_are_found_and_same_values_are_not(ruleset: Ruleset):
    """CONSIST-03: different values conflict; the same day in German and US order does not."""
    report, _, _ = _run(ruleset)
    conflicts = {i.entity: i for i in report.issues if i.category == "conflict"}
    assert set(conflicts) == {"thickness", "incoterm"}
    thickness = conflicts["thickness"]
    assert [e.normalized for e in thickness.evidence] == ["2.5", "2"]
    assert thickness.affects_cost and thickness.cost_fields == ("thickness",)
    assert thickness.departments[0] == "development"
    assert "2.5 mm" in thickness.title and thickness.citation_status == "verified"
    assert conflicts["incoterm"].cost_fields == ("incoterm",)


def test_missing_attachment_is_reported_but_not_standards_or_files_in_the_package(ruleset: Ruleset):
    """CONSIST-04: "Anlage 9" is no file of the package; "Annex 3" is file 03; ISO 9227 is no attachment."""
    report, _, _ = _run(ruleset)
    missing = [i for i in report.issues if i.category == "missing_attachment"]
    assert [i.id for i in missing] == ["m-anlage-9"]
    assert missing[0].severity == "high" and missing[0].departments == ("sales",)
    assert missing[0].evidence[0].locus.section_id == "2.2"


def test_reference_to_a_missing_section_is_broken_only_when_its_chapter_was_read(ruleset: Ruleset):
    """CONSIST-04: "Abschnitt 2.7" is broken because 2.1 and 2.2 exist; an unread chapter proves nothing."""
    report, _, _ = _run(ruleset)
    assert [i.id for i in report.issues if i.category == "broken_reference"] == ["b-d01-2-7"]
    other, _, _ = _run(ruleset, extra_main=["9.9 Siehe Abschnitt 5.4 und Abschnitt 2.1."])
    assert [i.id for i in other.issues if i.category == "broken_reference"] == ["b-d01-2-7"]


def test_blank_reference_target_is_reported(ruleset: Ruleset):
    report, _, _ = _run(
        ruleset, extra_main=["9.9 Prices are linked to the categories contained in , and Annex 3."]
    )
    blank = [i for i in report.issues if i.id == "b-d01-blank-1"]
    assert len(blank) == 1 and blank[0].title == "참조 대상이 비어 있습니다"


def test_transposed_code_is_a_notation_issue(ruleset: Ruleset):
    """CONSIST-05: EK-KR and EK-RK are one identifier written two ways; different codes are left alone."""
    report, _, _ = _run(ruleset)
    notation = [i for i in report.issues if i.category == "notation"]
    assert len(notation) == 1 and sorted(e.value for e in notation[0].evidence) == ["EK-KR", "EK-RK"]
    clean, _, _ = _run(ruleset, extra_main=["9.9 Kontakt: Einkauf (EK-RK), Logistik (LOG-NORD)."])
    assert not [i for i in clean.issues if i.category == "notation"]


def test_issues_inherit_cost_sensitivity_from_their_evidence(ruleset: Ruleset):
    """INV-8: an issue that cites a cost-sensitive clause is itself hidden from restricted teams."""
    report, _, clauses = _run(ruleset)
    assert [c.section_id for c in clauses if c.is_cost_sensitive] == ["Part Data"]
    hidden = {i.id: i.is_cost_sensitive for i in report.issues}
    assert hidden["c-thickness"] is True
    assert hidden["c-incoterm"] is False and hidden["m-anlage-9"] is False


def test_report_says_what_was_checked_and_is_deterministic(ruleset: Ruleset):
    """CONSIST-03, INV-7: ordered by category then position; "none found" differs from "none checked"."""
    first, _, _ = _run(ruleset)
    second, _, _ = _run(ruleset)
    assert first == second
    assert [i.category for i in first.issues] == sorted(
        (i.category for i in first.issues),
        key=["conflict", "missing_attachment", "broken_reference", "notation"].index,
    )
    assert (first.files, first.entities > 0, first.references > 0) == (3, True, True)
    manifest = make_manifest()
    empty = check_package(manifest, [], [], [], ruleset)
    assert (empty.issues, empty.entities, empty.references) == ((), 0, 0)
