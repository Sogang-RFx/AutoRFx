"""Price-sheet rows read from the RFQ for pricing line by line, in any column layout.

Tables come from ruling lines (or text alignment) of a PDF and from a workbook; the columns
are named by data/rules/price_sheet.yaml, by a layout a user assigned, or by words a user
approved. Test PDFs draw their own cell borders, as a word processor's table would.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from autorfx.analysis import RuleOnlyProvider
from autorfx.api import create_app
from autorfx.config import Settings
from autorfx.models import FileRecord, Locus, PackageManifest, ParsedFile, Table
from autorfx.precost import lines as lines_mod
from autorfx.precost.lines import find_grid, pdf_grids, read_grids, rfq_lines, signature, table_grids
from autorfx.precost.sheet_store import (
    PriceSheetError,
    PriceSheetStore,
    check_columns,
    header_label,
    usable_header_word,
)
from autorfx.rules import PriceSheetRules, Ruleset, keyword_pattern
from contract_schema import assert_valid

Cells = Sequence[str]


def _table(
    page: pymupdf.Page, top: float, rows: Sequence[Cells], widths: Sequence[float], ruled: bool
) -> float:
    """Draw ``rows`` from ``top``; a row of one cell spans the table (a group heading)."""
    y = top
    for row in rows:
        cells = [(row[0], sum(widths))] if len(row) == 1 else list(zip(row, widths, strict=True))
        x = 40.0
        for text, width in cells:
            rect = pymupdf.Rect(x, y, x + width, y + 18)
            if ruled:
                page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            page.insert_text((x + 3, y + 12), text, fontsize=8)
            x += width
        y += 18
    return y


def _pdf(
    path: Path, pages: Sequence[Sequence[Sequence[Cells]]], widths: Sequence[float], ruled: bool = True
) -> Path:
    """Pages of tables; each table is a list of rows."""
    doc = pymupdf.open()
    for tables in pages:
        page = doc.new_page(width=792, height=612)
        page.insert_text((40, 40), "REQUEST FOR QUOTATION 2026-01", fontsize=10)
        top = 70.0
        for rows in tables:
            top = _table(page, top, rows, widths, ruled) + 30
    doc.save(str(path))
    return path


@pytest.fixture(scope="module")
def rules(ruleset: Ruleset) -> PriceSheetRules:
    return ruleset.price_sheet


def _read(
    path: Path, rules: PriceSheetRules, layouts: dict | None = None, words: dict | None = None
) -> list[dict]:
    grids = pdf_grids("d01", path, rules, words)
    return [line for r in read_grids(grids, rules.compiled(words), layouts or {}) for line in r.lines]


WIDE = (40, 110, 230, 60, 60, 80)
HEADER = ("ITEM #", "PART NUMBER", "DESCRIPTION", "UNIT PRICE", "YEARLY QUANTITY", "EXTENDED PRICE")


def test_a_ruled_sheet_is_read_with_its_group_and_page(tmp_path: Path, rules: PriceSheetRules) -> None:
    sheet = [
        ("Category 1 - Brakes",),
        HEADER,
        ("1", "EU2Z-2V026-A", "ROTORS, REAR BRAKE", "$", "36", "$"),
        ("2", "GB5Z-2001-B", "PADS BRAKE FRONT", "$", "19", "$"),
        ("Category 1 - Brakes Subtotal:",),
    ]
    rows = _read(_pdf(tmp_path / "a.pdf", [[sheet]], WIDE), rules)
    assert [(r["group"], r["no"], r["part"], r["name"], r["quantity"], r["yearly"]) for r in rows] == [
        ("Brakes", "1", "EU2Z-2V026-A", "ROTORS, REAR BRAKE", 36, True),
        ("Brakes", "2", "GB5Z-2001-B", "PADS BRAKE FRONT", 19, True),
    ]
    assert {r["page"] for r in rows} == {1}


def test_columns_are_found_in_any_order_and_language(tmp_path: Path, rules: PriceSheetRules) -> None:
    sheet = [
        ("Menge", "ME", "Pos.", "Bezeichnung", "Einzelpreis", "Gesamtpreis"),
        ("120", "Stk", "10", "Halter links", "", ""),
        ("80", "Stk", "20", "Halter rechts", "", ""),
    ]
    rows = _read(_pdf(tmp_path / "a.pdf", [[sheet]], (50, 40, 40, 230, 80, 80)), rules)
    assert [(r["no"], r["name"], r["quantity"], r["yearly"]) for r in rows] == [
        ("10", "Halter links", 120, False),
        ("20", "Halter rechts", 80, False),
    ]


def test_a_sheet_without_ruling_lines_is_read_by_alignment(tmp_path: Path, rules: PriceSheetRules) -> None:
    sheet = [
        ("Item", "Description", "Qty", "Unit Price"),
        ("1", "Brake pad set front", "40", ""),
        ("2", "Wiper blade 600 mm", "200", ""),
        ("3", "Cabin air filter", "75", ""),
    ]
    rows = _read(_pdf(tmp_path / "a.pdf", [[sheet]], (60, 260, 80, 100), ruled=False), rules)
    assert [(r["name"], r["quantity"]) for r in rows] == [
        ("Brake pad set front", 40),
        ("Wiper blade 600 mm", 200),
        ("Cabin air filter", 75),
    ]


def test_a_sheet_goes_on_over_a_page_break_without_its_header(tmp_path: Path, rules: PriceSheetRules) -> None:
    first = [("Category 2 - Filters",), HEADER, ("1", "FL-500-S", "FILTER OIL", "$", "180", "$")]
    second = [("2", "FA-1883", "FILTER AIR", "$", "40", "$")]
    rows = _read(_pdf(tmp_path / "a.pdf", [[first], [second]], WIDE), rules)
    assert [(r["group"], r["no"], r["page"]) for r in rows] == [("Filters", "1", 1), ("Filters", "2", 2)]


def _two_pages(path: Path, second: Sequence[float], rows: Sequence[Cells]) -> Path:
    """Page 1: the sheet with its header. Page 2: more rows, the table ruled into ``second`` widths."""
    doc = pymupdf.open()
    first = doc.new_page(width=792, height=612)
    _table(first, 70, [HEADER, ("1", "EU2Z-2V026-A", "ROTORS, REAR BRAKE", "$", "36", "$")], WIDE, True)
    following = doc.new_page(width=792, height=612)
    _table(following, 70, rows, second, True)
    doc.save(str(path))
    return path


def test_a_sheet_cut_into_other_columns_on_the_next_page_goes_on_by_column_position(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
    """Page 2 rules the description column in two (a sliver of the name, the rest): the columns
    no longer count the same, so they are matched by where they are printed."""
    split = (40, 110, 150, 80, 60, 60, 80)
    rows = [
        ("2", "GB5Z-2001-B", "PADS BRAKE FRONT", "", "$", "19", "$"),
        ("3", "FL-500-S", "FILTER OIL", "", "$", "180", "$"),
    ]
    path = _two_pages(tmp_path / "a.pdf", split, rows)
    grids = pdf_grids("d01", path, rules)
    assert [len(g.rows[0]) for g in grids] == [6, 7]
    readings = read_grids(grids, rules.compiled(), {})
    assert [r.how for r in readings] == ["rules", "continued"]
    assert [(r["no"], r["name"], r["quantity"], r["page"]) for x in readings for r in x.lines] == [
        ("1", "ROTORS, REAR BRAKE", 36, 1),
        ("2", "PADS BRAKE FRONT", 19, 2),
        ("3", "FILTER OIL", 180, 2),
    ]
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "pdf"})()])
    (only,) = rfq_lines(manifest, {}, {"d01": path}, rules)["lists"]
    assert (only["pages"], only["lines"]) == ([1, 2], 3)


def test_a_table_whose_columns_do_not_line_up_is_not_taken_as_going_on(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
    """Another table on the next page, with columns elsewhere, is not read with the sheet's roles."""
    other = (300, 120, 120)
    path = _two_pages(tmp_path / "a.pdf", other, [("Delivery address", "Plant 2", "Dock 4")] * 3)
    readings = read_grids(pdf_grids("d01", path, rules), rules.compiled(), {})
    assert [r.how for r in readings] == ["rules", "none"]
    assert len(readings[1].lines) == 0


def test_text_outside_a_price_sheet_is_not_a_line(tmp_path: Path, rules: PriceSheetRules) -> None:
    schedule = [("Event", "Date"), ("Issue date", "June 13, 2026"), ("Quote due", "July 13, 2026")]
    assert _read(_pdf(tmp_path / "a.pdf", [[schedule]], (200, 200)), rules) == []


UNKNOWN = ("Lfd", "Artikeltext", "Bedarf 2027", "Angebot")


def _unknown_sheet(tmp_path: Path) -> Path:
    sheet = [UNKNOWN, ("1", "Schraube M8", "5000", ""), ("2", "Mutter M8", "5000", "")]
    return _pdf(tmp_path / "u.pdf", [[sheet]], (40, 200, 90, 90))


def test_an_unknown_layout_is_listed_for_assignment_and_read_once_assigned(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
    path = _unknown_sheet(tmp_path)
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "pdf"})()])
    unread = rfq_lines(manifest, {}, {"d01": path}, rules)
    assert unread["items"] == []
    (table,) = unread["tables"]
    assert (table["how"], table["header_row"], table["rows"][0]) == ("none", 0, list(UNKNOWN))

    layout = {signature(UNKNOWN): {"no": 0, "description": 1, "quantity": 2, "price": 3}}
    read = rfq_lines(manifest, {}, {"d01": path}, rules, layouts=layout)
    assert [(r["name"], r["quantity"]) for r in read["items"]] == [("Schraube M8", 5000), ("Mutter M8", 5000)]
    assert read["tables"][0]["how"] == "layout"


def test_an_approved_header_word_reads_every_table_that_uses_it(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
    path = _unknown_sheet(tmp_path)
    assert _read(path, rules) == []
    rows = _read(path, rules, words={"description": ("artikeltext",), "quantity": ("bedarf 2027",)})
    assert [r["name"] for r in rows] == ["Schraube M8", "Mutter M8"]


def test_approved_words_find_an_unruled_pdf_table(tmp_path: Path, rules: PriceSheetRules) -> None:
    """Approved dictionary words must also drive the text-alignment table search."""
    sheet = [UNKNOWN, ("1", "Schraube M8", "5000", ""), ("2", "Mutter M8", "5000", "")]
    path = _pdf(tmp_path / "u.pdf", [[sheet]], (40, 200, 90, 90), ruled=False)
    assert _read(path, rules) == []
    rows = _read(path, rules, words={"description": ("artikeltext",), "quantity": ("bedarf 2027",)})
    assert [r["name"] for r in rows] == ["Schraube M8", "Mutter M8"]


def test_find_grid_forwards_approved_words_to_file_grids(
    tmp_path: Path, rules: PriceSheetRules, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-assign must pass store.approved into find_grid (unruled tables found via those words)."""
    sheet = [UNKNOWN, ("1", "Schraube M8", "5000", ""), ("2", "Mutter M8", "5000", "")]
    path = _pdf(tmp_path / "u.pdf", [[sheet]], (40, 200, 90, 90), ruled=False)
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "pdf"})()])
    words = {"description": ("artikeltext",), "quantity": ("bedarf 2027",)}
    listed = rfq_lines(manifest, {}, {"d01": path}, rules, words=words)
    (table,) = listed["tables"]
    assert find_grid(manifest, {}, {"d01": path}, rules, table["id"], words=words) is not None

    seen: dict = {}
    real = lines_mod.file_grids

    def wrap(*args, **kwargs):
        seen["words"] = kwargs["words"] if "words" in kwargs else (args[4] if len(args) > 4 else None)
        return real(*args, **kwargs)

    monkeypatch.setattr(lines_mod, "file_grids", wrap)
    find_grid(manifest, {}, {"d01": path}, rules, table["id"], words=words)
    assert seen["words"] == words


def test_header_label_skips_claimed_columns_and_reports_label_col() -> None:
    header = ("Description", "", "Qty", "")
    claimed = {1, 3}  # value columns for description and quantity
    assert header_label(header, 1, claimed) == ("Description", 0)
    assert header_label(header, 3, claimed) == ("Qty", 2)
    # Walking left must not reuse another role's value column as this role's label.
    assert header_label(("Name", "Qty", ""), 2, {1, 2}) == ("", 2)
    # Direct header cell: label_col is the value column itself.
    assert header_label(("Item", "Description", "Qty"), 1, {0, 1, 2}) == ("Description", 1)
    # known.get(role) != label_col: an already-mapped label column must not re-suggest.
    known = {"description": 0, "quantity": 2}
    label, label_col = header_label(header, 1, claimed)
    assert label == "Description" and known.get("description") == label_col


def test_a_revised_sheet_replaces_the_earlier_listing_of_a_group(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
    original = [("Category 1 - Brakes",), HEADER, ("1", "EU2Z-2V026-A", "ROTORS", "$", "36", "$")]
    filters = [("Category 2 - Filters",), HEADER, ("1", "FL-500-S", "FILTER OIL", "$", "180", "$")]
    revised = [("Category 1 - Brakes",), HEADER, ("1", "EU2Z-2V026-A", "ROTORS", "$", "40", "$")]
    path = _pdf(tmp_path / "a.pdf", [[original, filters], [revised]], WIDE)
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "pdf"})()])
    found = rfq_lines(manifest, {}, {"d01": path}, rules)
    assert sorted((r["group"], r["quantity"], r["page"]) for r in found["items"]) == [
        ("Brakes", 40, 2),
        ("Filters", 180, 1),
    ]
    # The same layout on the next page is one list, with the revision applied inside it.
    (only,) = found["lists"]
    assert (only["pages"], only["lines"], only["replaced_by"]) == ([1, 2], 2, None)


def _price_sheets(rules: PriceSheetRules, revised_caption: str) -> dict:
    """A price sheet, and the same sheet reprinted by an addendum with a column added."""
    rows = [("1", "EU2Z-2V026-A", "ROTORS", "", "36"), ("2", "FL-500-S", "FILTER OIL", "", "180")]
    original = _sheet_table(("ITEM #", "PART NUMBER", "DESCRIPTION", "UNIT PRICE", "YEARLY QUANTITY"), rows)
    reprint = _sheet_table(
        ("ITEM #", "MFG. PART NUMBER", "Updated Part Number", "DESCRIPTION", "UNIT PRICE", "YEARLY QUANTITY"),
        [(no, part, "", name, price, qty) for no, part, name, price, qty in rows],
    ).model_copy(update={"title": revised_caption, "page_number": 5, "section_id": "addendum"})
    original = original.model_copy(update={"title": "FORM 3.02 PRICE SHEET"})
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "xlsx"})()])
    parsed = {"d01": ParsedFile.model_construct(tables=(original, reprint))}
    return rfq_lines(manifest, parsed, {}, rules)


def test_a_sheet_reprinted_as_revised_replaces_the_original_even_with_a_column_more(
    rules: PriceSheetRules,
) -> None:
    """Everett: Addendum #2 reprints Form 3.02 as "*REVISED*" with an "Updated Part Number" column."""
    found = _price_sheets(rules, "FORM 3.02 PRICE SHEET *REVISED*")
    original, revised = found["lists"]
    assert (original["lines"], original["suggested"], original["replaced_by"]) == (0, False, revised["id"])
    assert (revised["lines"], revised["suggested"], revised["replaced_by"]) == (2, True, None)
    assert found["total"] == 2 and {r["list"] for r in found["items"]} == {revised["id"]}


def test_a_repeated_sheet_not_marked_as_revised_keeps_both_lists(rules: PriceSheetRules) -> None:
    found = _price_sheets(rules, "FORM 3.03 PRICE SHEET")
    assert [(lst["lines"], lst["replaced_by"]) for lst in found["lists"]] == [(2, None), (2, None)]


def test_a_group_title_repeated_on_the_next_page_keeps_earlier_rows(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
    """A multi-page sheet that reprints its group heading must not drop page-1 items."""
    page1 = [
        ("Category 1 - Brakes",),
        HEADER,
        ("1", "EU2Z-2V026-A", "ROTORS, REAR BRAKE", "$", "36", "$"),
    ]
    page2 = [
        ("Category 1 - Brakes",),
        HEADER,
        ("2", "GB5Z-2001-B", "PADS BRAKE FRONT", "$", "19", "$"),
    ]
    path = _pdf(tmp_path / "a.pdf", [[page1], [page2]], WIDE)
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "pdf"})()])
    found = rfq_lines(manifest, {}, {"d01": path}, rules)
    assert sorted((r["no"], r["part"], r["page"]) for r in found["items"]) == [
        ("1", "EU2Z-2V026-A", 1),
        ("2", "GB5Z-2001-B", 2),
    ]
    (only,) = found["lists"]
    assert only["lines"] == 2


def test_spec_and_bid_form_sharing_a_lot_name_keep_both_lists(tmp_path: Path, rules: PriceSheetRules) -> None:
    """Parallel lists that both say \"Lot 1\" must not treat the later as a revision."""
    spec = [
        ("Lot 1 - Electrical",),
        ("Items to be Supplied", "Quantity", "Description/Specifications", "", ""),
        ("Battery Charger", "2", "12V automotive charger", "", ""),
        ("Manifold Gauge", "3", "R134a set", "", ""),
    ]
    form = [
        ("Lot 1 - Electrical",),
        ("Item No.", "Description of Goods", "Quantity", "Unit Price", "Total Price"),
        ("1", "Battery Charger", "2", "", ""),
        ("2", "Manifold Gauge", "3", "", ""),
    ]
    path = _pdf(tmp_path / "a.pdf", [[spec], [form]], (120, 200, 60, 80, 80))
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "pdf"})()])
    found = rfq_lines(manifest, {}, {"d01": path}, rules)
    assert len(found["lists"]) == 2
    by_list = {lst["id"]: lst for lst in found["lists"]}
    assert all(lst["lines"] == 2 for lst in found["lists"])
    assert all(lst["replaced_by"] is None for lst in found["lists"])
    suggested = next(lst for lst in found["lists"] if lst["suggested"])
    other = next(lst for lst in found["lists"] if not lst["suggested"])
    assert suggested["has_price"] and not other["has_price"]
    assert {r["list"] for r in found["items"]} == set(by_list)


def test_suggested_lists_are_kept_when_items_exceed_max(
    tmp_path: Path, rules: PriceSheetRules, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long unpriced specification ahead of a bid form must not starve the suggested list."""
    monkeypatch.setattr(lines_mod, "MAX_ITEMS", 3)
    spec_rows = [("Items to be Supplied", "Quantity", "Description/Specifications", "", "")]
    form_rows = [("Item No.", "Description of Goods", "Quantity", "Unit Price", "Total Price")]
    for i in range(1, 6):
        spec_rows.append((f"Part {i}", "1", f"Spec of part {i}", "", ""))
        form_rows.append((str(i), f"Part {i}", "1", "", ""))
    path = _pdf(tmp_path / "a.pdf", [[spec_rows], [form_rows]], (120, 60, 200, 80, 80))
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "pdf"})()])
    found = rfq_lines(manifest, {}, {"d01": path}, rules)
    assert found["total"] == 10
    assert len(found["items"]) == 3
    suggested = next(lst for lst in found["lists"] if lst["suggested"])
    assert all(item["list"] == suggested["id"] for item in found["items"])
    assert suggested["lines"] == 5  # counts are before truncation


def test_box_coordinates_are_clamped_to_the_page(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=200)
    page_num, x, y, w, h = lines_mod._box(page, 1, (-10, -5, 150, 250))
    doc.close()
    assert (page_num, x, y, w, h) == (1, 0.0, 0.0, 1.0, 1.0)
    assert 0 <= x <= 1 and 0 <= y <= 1 and 0 <= w <= 1 and 0 <= h <= 1


def _annexes(path: Path) -> Path:
    """An RFQ that lists the goods twice: a specification (Annex 1) and the bid form (Annex 2)."""

    def table(page: pymupdf.Page, rows: Sequence[Sequence[str | None]], widths: Sequence[float]) -> None:
        y = 80.0
        for row in rows:
            x = 40.0
            if row[0] is None:  # a note merged across the specification column and the ones after it
                x += sum(widths[:2])
                page.draw_rect(pymupdf.Rect(x, y, 40 + sum(widths), y + 18), color=(0, 0, 0), width=0.6)
                page.insert_text((x + 3, y + 12), str(row[1]), fontsize=8)
            else:
                for text, width in zip(row, widths, strict=True):
                    page.draw_rect(pymupdf.Rect(x, y, x + width, y + 18), color=(0, 0, 0), width=0.6)
                    page.insert_text((x + 3, y + 12), str(text), fontsize=8)
                    x += width
            y += 18

    doc = pymupdf.open()
    page = doc.new_page(width=792, height=612)
    page.insert_text((40, 60), "Annex 1", fontsize=10)
    table(
        page,
        [
            ("Items to be Supplied", "Quantity", "Description/Specifications", "Latest Delivery Date"),
            ("Car washing", "1", "Car washing machine 250bar", "30 days"),
            (None, "Input current: 16A"),
            ("Compressor", "7", "Two piston compressor 70lt", "30 days"),
        ],
        (120, 60, 300, 120),
    )
    page = doc.new_page(width=792, height=612)
    page.insert_text((40, 60), "Annex 2", fontsize=10)
    table(
        page,
        [
            (
                "Item No.",
                "Description of Goods",
                "Size, model and technical specifications",
                "Quantity",
                "Unit Price",
                "Total Price",
            ),
            ("1", "Car washing", "Car washing machine 250bar", "1", "", ""),
            ("2", "Compressor", "Two piston compressor 70lt", "7", "", ""),
        ],
        (50, 120, 250, 60, 80, 80),
    )
    doc.save(str(path))
    return path


def test_a_specification_and_its_bid_form_are_two_lists_and_the_bid_form_is_suggested(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "pdf"})()])
    found = rfq_lines(manifest, {}, {"d01": _annexes(tmp_path / "a.pdf")}, rules)
    spec, form = found["lists"]
    assert (spec["caption"], spec["pages"], spec["lines"], spec["has_price"], spec["suggested"]) == (
        "Annex 1",
        [1],
        2,
        False,
        False,
    )
    assert (form["caption"], form["pages"], form["lines"], form["has_price"], form["suggested"]) == (
        "Annex 2",
        [2],
        2,
        True,
        True,
    )
    # Both lists are sent; nothing is merged, the overlap is only named.
    assert spec["overlaps"] == [{"list": form["id"], "items": 2}]
    assert form["overlaps"] == [{"list": spec["id"], "items": 2}]
    by_list = {lst["id"]: [r for r in found["items"] if r["list"] == lst["id"]] for lst in found["lists"]}
    # "Items to be Supplied" holds names, not numbers; a merged note belongs to the item above.
    assert [(r["name"], r["quantity"]) for r in by_list[spec["id"]]] == [
        ("Car washing – Car washing machine 250bar; Input current: 16A", 1),
        ("Compressor – Two piston compressor 70lt", 7),
    ]
    assert [(r["no"], r["name"]) for r in by_list[form["id"]]] == [
        ("1", "Car washing – Car washing machine 250bar"),
        ("2", "Compressor – Two piston compressor 70lt"),
    ]
    assert form["boxes"][0]["page"] == 2 and 0 < form["boxes"][0]["w"] <= 1


def test_a_sheet_whose_header_wording_changes_on_the_next_page_stays_one_list(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
    first = [HEADER, ("1", "FL-500-S", "FILTER OIL", "$", "180", "$")]
    reworded = (
        "ITEM #",
        "MFG. PART NUMBER",
        "DESCRIPTION",
        "UNIT PRICE",
        "YEARLY QUANTITY",
        "EXTENDED PRICE",
    )
    second = [reworded, ("2", "FA-1883", "FILTER AIR", "$", "40", "$")]
    manifest = PackageManifest.model_construct(files=[type("F", (), {"id": "d01", "kind": "pdf"})()])
    found = rfq_lines(manifest, {}, {"d01": _pdf(tmp_path / "a.pdf", [[first], [second]], WIDE)}, rules)
    (only,) = found["lists"]
    assert (only["pages"], only["lines"], only["suggested"]) == ([1, 2], 2, True)


def test_quantities_with_units_and_values_one_cell_off_their_header_are_read(
    rules: PriceSheetRules,
) -> None:
    """A PDF table may split a header cell from the values under it; quantities carry their unit."""
    header = ("Lot No.", "", "Description", "", "Quantity", "", "Total Price")
    rows = [
        ("", "1", "", "Refrigerant Recovery Machine", "", "1 pc.", ""),
        ("", "2", "", "Automotive Battery Charger", "", "2 units", ""),
        ("", "3", "", "Manifold Gauge", "", "3 sets", ""),
    ]
    # Cells as a PDF table gives them (a spreadsheet has no cells off their header).
    grid = lines_mod.Grid(
        id="d01:p1:t1", source_doc="d01", title="1쪽 표 1", rows=(header, *rows), pages=(1,) * 4
    )
    (reading,) = read_grids([grid], rules.compiled(), {})
    assert reading.priced
    assert [(r["no"], r["name"], r["quantity"]) for r in reading.lines] == [
        ("1", "Refrigerant Recovery Machine", 1),
        ("2", "Automotive Battery Charger", 2),
        ("3", "Manifold Gauge", 3),
    ]


def test_cells_follow_the_ruling_lines_not_the_shading_behind_each_line_of_text(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
    """A word processor shades each line of a cell; only the ruling lines make cells.

    "Approved Budget / for the Contract" is one cell over two header rows, and "Total Bid Price"
    one cell over "Unit Price | Total Amount". Borders are thin filled rectangles, as in the PDF
    a word processor writes.
    """
    xs = (40, 80, 230, 360, 420, 520, 620)
    ys = (60, 80, 100, 120, 140)
    doc = pymupdf.open()
    page = doc.new_page(width=792, height=612)

    def rule(x0: float, y0: float, x1: float, y1: float) -> None:
        page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=None, fill=(0, 0, 0))

    def text(x: float, y: float, words: str) -> None:
        page.draw_rect(pymupdf.Rect(x, y - 9, x + 90, y + 2), color=None, fill=(0.87, 0.92, 0.96))
        page.insert_text((x + 2, y), words, fontsize=8)

    for y in (ys[0], ys[2], ys[3], ys[4]):
        rule(xs[0], y - 0.4, xs[-1], y + 0.4)
    rule(xs[4], ys[1] - 0.4, xs[-1], ys[1] + 0.4)  # only under "Total Bid Price"
    for x in xs:
        if x == xs[5]:
            rule(x - 0.4, ys[1], x + 0.4, ys[-1])  # Unit | Total split starts below the heading
        else:
            rule(x - 0.4, ys[0], x + 0.4, ys[-1])
    text(43, 72, "Lot No.")
    text(83, 72, "Item Description")
    text(233, 72, "Approved Budget")
    text(233, 92, "for the Contract")
    text(363, 72, "Qty")
    text(423, 72, "Total Bid Price")
    text(423, 112 - 20, "Unit Price")
    text(523, 92, "Total Amount")
    for y, row in (
        (112, ("1", "Battery Charger", "168,000.00", "2 units")),
        (132, ("2", "Manifold Gauge", "28,500.00", "3 sets")),
    ):
        for x, words in zip(xs, row, strict=False):
            page.insert_text((x + 3, y), words, fontsize=8)
    path = tmp_path / "a.pdf"
    doc.save(str(path))

    (grid,) = pdf_grids("d01", path, rules)
    assert grid.rows[0] == (
        "Lot No.",
        "Item Description",
        "Approved Budget for the Contract",
        "Qty",
        "Total Bid Price",
        "",
    )
    assert grid.rows[1] == ("", "", "", "", "Unit Price", "Total Amount")
    rows = _read(path, rules)
    assert [(r["no"], r["name"], r["quantity"]) for r in rows] == [
        ("1", "Battery Charger", 2),
        ("2", "Manifold Gauge", 3),
    ]


def _sheet_table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> Table:
    locus = Locus(source_doc="d01", page_number=1, section_id="0", paragraph_index=1, sentence_index=1)
    return Table(
        source_doc="d01",
        page_number=1,
        section_id="0",
        title="Price schedule",
        header=header,
        rows=tuple(rows),
        row_loci=tuple(locus for _ in rows),
    )


def test_a_monthly_quantity_is_multiplied_by_the_months(rules: PriceSheetRules) -> None:
    table = _sheet_table(
        ("", "PART DESCRIPTION", "MAX QUANTITY Per month", "Months", "UNIT PRICE"),
        [("75545", "OIL 0W20", "5", "12", "0"), ("", "TOTAL", "", "", "0")],
    )
    (reading,) = read_grids(table_grids("d01", [table]), rules.compiled(), {})
    assert [(r["name"], r["quantity"], r["yearly"]) for r in reading.lines] == [("OIL 0W20", 60, True)]


def test_a_workbook_sheet_without_quantities_has_no_lines(rules: PriceSheetRules) -> None:
    table = _sheet_table(("PART NO.", "DESCRIPTION", "Unit Price"), [("7237", "AIR FILTER", "0")])
    (reading,) = read_grids(table_grids("d01", [table]), rules.compiled(), {})
    assert reading.lines == []


def _order_form() -> Table:
    """An order form on one sheet: contact fields, then two tables with their own headers.

    The quantity column is left for the buyer; the second table's name column has no known title.
    """
    rows = [
        ("Instructions: complete sections I to III", "", "", "", "", ""),
        ("I", "Contact Information", "", "", "", ""),
        ("", "Agency Name:", "", "", "", ""),
        ("", "Contact Name:", "", "", "", ""),
        ("", "Phone Number:", "", "E-mail:", "", ""),
        ("", "Program:", "", "State Purchasing", "230000001331", ""),
        ("II", "Base Vehicle Floor Plans", "", "", "", ""),
        ("Base Vehicles", "", "Qty", "Description", "Price Each", "Total Price"),
        ("", "A.", "", "Class I - 26 Foot Bus", "", ""),
        ("", "1", "", "24 passenger without lift", "153078", "0"),
        ("", "2", "", "14+2 passenger with lift", "164751", "0"),
        ("", "B.", "", "Class II - 29 Foot Bus", "", ""),
        ("", "1", "", "24 passenger without lift", "180261", "0"),
        ("", "Total", "0", "Base Vehicle Totals", "", "0"),
        ("III", "Contract Options", "", "", "", ""),
        ("Contract Options", "", "Qty", "Contract Options", "Price Each", "Total Price"),
        ("", "1", "", "Air Conditioning, Split unit", "19898", "0"),
        ("", "2", "", "Destination Sign - LED", "6357", "0"),
        ("", "3", "", "Donation box (in lieu of farebox)", "-1045", "0"),
        ("", "", "", "Contract Options Subtotal", "", "0"),
    ]
    locus = Locus(source_doc="d02", page_number=1, section_id="Form", paragraph_index=1, sentence_index=1)
    return Table(
        source_doc="d02",
        page_number=1,
        section_id="Form",
        title="Form",
        header=("NEW VEHICLE ORDER FORM", "", "", "", "", ""),
        rows=tuple(rows),
        row_loci=tuple(locus.model_copy(update={"anchor": f"Form!A{n}"}) for n in range(2, len(rows) + 2)),
    )


def test_an_order_form_sheet_is_cut_into_its_tables_and_read_without_quantities(
    rules: PriceSheetRules,
) -> None:
    compiled = rules.compiled()
    grids = table_grids("d02", [_order_form()], compiled)
    assert [(g.id, g.caption) for g in grids] == [
        ("d02:s1", "Form"),
        ("d02:s1:r9", "Base Vehicle Floor Plans"),
        ("d02:s1:r17", "Contract Options"),
    ]
    readings = read_grids(grids, compiled, {})
    assert [(r["group"], r["name"], r["quantity"]) for r in readings[1].lines] == [
        ("A. · Class I - 26 Foot Bus", "24 passenger without lift", None),
        ("A. · Class I - 26 Foot Bus", "14+2 passenger with lift", None),
        ("B. · Class II - 29 Foot Bus", "24 passenger without lift", None),
    ]
    # The name column is found by its words; the group of the table above does not carry over.
    assert [(r["group"], r["name"]) for r in readings[2].lines] == [
        (None, "Air Conditioning, Split unit"),
        (None, "Destination Sign - LED"),
        (None, "Donation box (in lieu of farebox)"),
    ]


def test_the_tables_of_one_sheet_are_separate_lists(rules: PriceSheetRules) -> None:
    sha = "0" * 64
    record = FileRecord(
        id="d02",
        name="form.xlsx",
        kind="xlsx",
        page_count=1,
        sha256=sha,
        canonical_tool="t",
        canonical_sha256=sha,
    )
    manifest = PackageManifest(
        id="p", name="p", files=(record,), page_count=1, sha256=sha, is_sample=False, created_at=0
    )
    parsed = {
        "d02": ParsedFile(file_id="d02", lang="en", page_count=1, sentences=(), tables=(_order_form(),))
    }
    got = rfq_lines(manifest, parsed, {}, rules)
    assert [(lst["caption"], lst["lines"], lst["suggested"]) for lst in got["lists"]] == [
        ("Base Vehicle Floor Plans", 3, True),
        ("Contract Options", 3, True),
    ]


def _block(header: tuple[str, ...], rows: list[tuple[str, ...]], first_row: int, column: str) -> Table:
    """A block of one sheet as the parser cuts it: its own header and the columns it uses."""
    letters = "ABCDEFGHIJ"
    start = letters.index(column)
    loci, anchors = [], []
    for n, row in enumerate(rows, start=first_row):
        loci.append(
            Locus(
                source_doc="d03",
                page_number=1,
                section_id="Hardware",
                paragraph_index=1,
                sentence_index=1,
                anchor=f"Hardware!{column}{n}",
            )
        )
        anchors.append(tuple(f"Hardware!{letters[start + i]}{n}" if c else None for i, c in enumerate(row)))
    return Table(
        source_doc="d03",
        page_number=1,
        section_id="Hardware",
        title="Hardware",
        header=header,
        rows=tuple(rows),
        row_loci=tuple(loci),
        cell_anchors=tuple(anchors),
    )


def test_the_blocks_of_one_sheet_are_read_as_one_table_with_their_categories(rules: PriceSheetRules) -> None:
    """The parser cuts a sheet at a category title and trims each block to its columns (Annexure M)."""
    notes = _block(("IMPORTANT NOTES",), [("Prices MUST be quoted in ZAR",)] * 6, 2, "C")
    first = _block(
        ("Category", "No.", "Part Number", "Description", "Total Estimated Quantity", "Unit Price"),
        [
            ("Cyber Security", "", "", "ISA-3000 Firewall", "", ""),
            ("", "1", "ISA-3000-2C2F", "Cisco Secure Firewall ISA3000", "400", "0"),
            ("", "", "", "FPR3105 Firewall", "", ""),
            ("", "2", "FPR3105-NGFW", "Cisco Secure Firewall 3105", "20", "0"),
        ],
        10,
        "A",
    )
    # The next block starts at a category title and leaves out the empty column F.
    second = _block(
        ("Routers", "", "", "IR8340 Router"),
        [("", "3", "IR8340-K9", "Cisco Catalyst IR8340", "300")],
        15,
        "A",
    )
    compiled = rules.compiled()
    grids = table_grids("d03", [notes, first, second], compiled)
    assert [g.id for g in grids] == ["d03:s1", "d03:s1:r9"]
    lines = [line for r in read_grids(grids, compiled, {}) for line in r.lines]
    assert [(r["part"], r["quantity"], r["group"]) for r in lines] == [
        ("ISA-3000-2C2F", 400, "Cyber Security · ISA-3000 Firewall"),
        ("FPR3105-NGFW", 20, "Cyber Security · FPR3105 Firewall"),
        ("IR8340-K9", 300, "Routers · IR8340 Router"),
    ]


def _worksheet(cells: list[tuple[int, tuple[tuple[str, str], ...]]]) -> Table:
    """A vehicle cost worksheet as the parser gives it: empty columns left out of each row,
    every cell with its own address ((sheet row, ((column, text), ...)))."""
    rows, loci, anchors = [], [], []
    for n, row in cells:
        rows.append(tuple(text for _, text in row))
        anchors.append(tuple(f"Van!{column}{n}" for column, _ in row))
        loci.append(
            Locus(
                source_doc="d04",
                page_number=1,
                section_id="Van",
                paragraph_index=1,
                sentence_index=1,
                anchor=f"Van!A{n}",
            )
        )
    return Table(
        source_doc="d04",
        page_number=1,
        section_id="Van",
        title="Van",
        header=(),
        rows=tuple(rows),
        row_loci=tuple(loci),
        cell_anchors=tuple(anchors),
    )


def _worksheet_lines(rules: PriceSheetRules, table: Table) -> list[dict]:
    sha = "0" * 64
    record = FileRecord(
        id="d04",
        name="vcw.xlsx",
        kind="xlsx",
        page_count=1,
        sha256=sha,
        canonical_tool="t",
        canonical_sha256=sha,
    )
    manifest = PackageManifest(
        id="p", name="p", files=(record,), page_count=1, sha256=sha, is_sample=False, created_at=0
    )
    parsed = {"d04": ParsedFile(file_id="d04", lang="en", page_count=1, sentences=(), tables=(table,))}
    return rfq_lines(manifest, parsed, {}, rules)["items"]


def test_a_worksheet_reads_numbered_options_beside_their_number(rules: PriceSheetRules) -> None:
    """ "Item | (blank) | Quantity | Unit Price": the base vehicle is named under "Item", each option
    by the unheaded cell beside its number. The counted base number does not erase option 1."""
    table = _worksheet(
        [
            (14, (("A", "Item"), ("C", "Quantity"), ("D", "Unit Price"), ("E", "Total"))),
            (15, (("A", "Modified Minivan"), ("D", "70993"), ("E", "0"))),
            (16, (("A", "Options"),)),
            (17, (("A", "1"), ("B", "Rear-Entry Ramp"), ("D", "-4223"), ("E", "0"))),
            (18, (("A", "2"), ("B", "Center Foldable Seat"), ("D", "1685"), ("E", "0"))),
            (19, (("A", "3"), ("B", "Power Door Kit INCLUDED"))),
            (20, (("C", "TOTAL COST"), ("E", "0"))),
        ]
    )
    assert [(r["no"], r["name"], r["quantity"], r["group"]) for r in _worksheet_lines(rules, table)] == [
        ("1", "Modified Minivan", None, None),
        ("1", "Rear-Entry Ramp", None, "Options"),
        ("2", "Center Foldable Seat", None, "Options"),
        ("3", "Power Door Kit INCLUDED", None, "Options"),
    ]


def test_a_worksheet_row_keeps_its_cells_in_their_own_columns(rules: PriceSheetRules) -> None:
    """The parser leaves out the empty columns B-D of an option row; the price stays under its header."""
    table = _worksheet(
        [
            (19, (("A", "Item"), ("D", "Quantity"), ("E", "Unit Price"), ("F", "Total"))),
            (20, (("A", "Accessible Van"), ("E", "74789"), ("F", "0"))),
            (22, (("A", "1000-lb lift"), ("B", ""), ("E", "1300"), ("F", "0"))),
            (23, (("A", "Solid Color Paint"), ("B", ""), ("E", "805"), ("F", "0"))),
        ]
    )
    assert [(r["name"], r["quantity"]) for r in _worksheet_lines(rules, table)] == [
        ("Accessible Van", None),
        ("1000-lb lift", None),
        ("Solid Color Paint", None),
    ]


def test_a_sheet_with_quantities_keeps_its_rows_strict(rules: PriceSheetRules) -> None:
    """Only a quantity column blank in every row makes an order form; else no quantity, no line."""
    table = _sheet_table(
        ("No", "Description", "Qty", "Unit Price"),
        [("1", "Brake pad", "4", ""), ("2", "Brake disc", "", "120"), ("", "Note: delivered", "", "")],
    )
    (reading,) = read_grids(table_grids("d01", [table], rules.compiled()), rules.compiled(), {})
    assert not reading.open
    assert [(r["name"], r["quantity"]) for r in reading.lines] == [("Brake pad", 4)]


# ── assignment store ────────────────────────────────────────────────────────
def test_an_assignment_needs_a_quantity_and_a_name_column() -> None:
    with pytest.raises(PriceSheetError, match="수량"):
        check_columns({"description": 1}, 4)
    with pytest.raises(PriceSheetError, match="품명"):
        check_columns({"quantity": 2}, 4)
    with pytest.raises(PriceSheetError, match="두 개"):
        check_columns({"quantity": 2, "description": 2}, 4)
    with pytest.raises(PriceSheetError, match="표 밖"):
        check_columns({"quantity": 9, "description": 1}, 4)


def test_punctuation_and_digit_only_header_words_are_rejected(tmp_path: Path) -> None:
    assert usable_header_word("Artikeltext")
    assert not usable_header_word("-")
    assert not usable_header_word("/")
    assert not usable_header_word("1")
    assert not usable_header_word("2027")
    store = PriceSheetStore(tmp_path)
    store.assign(
        "a|b",
        {"description": 1, "quantity": 2},
        {"description": "-", "quantity": "1", "no": "Lfd"},
        date(2026, 11, 24),
    )
    assert [(c.role, c.text) for c in store.candidates] == [("no", "lfd")]
    # Digit tokens that slip through still match only as whole words.
    assert re.search(keyword_pattern("1"), "1") and not re.search(keyword_pattern("1"), "2027")
    assert re.search(keyword_pattern("-"), " - ") and not re.search(keyword_pattern("-"), "Part-No")


def test_assigned_words_wait_for_approval_and_survive_a_restart(tmp_path: Path) -> None:
    store = PriceSheetStore(tmp_path)
    store.assign(
        "lfd|artikeltext", {"description": 1, "quantity": 2}, {"quantity": "Bedarf  2027"}, date(2026, 11, 24)
    )
    store.assign(
        "x|artikeltext", {"description": 1, "quantity": 2}, {"quantity": "Bedarf 2027"}, date(2026, 11, 25)
    )
    (candidate,) = store.candidates
    assert (candidate.role, candidate.text, candidate.count) == ("quantity", "bedarf 2027", 2)
    assert store.approved == {}

    store.decide("quantity", "bedarf 2027", "approve")
    again = PriceSheetStore(tmp_path)
    assert again.approved == {"quantity": ("bedarf 2027",)}
    assert again.candidates == ()
    assert set(again.layouts) == {"lfd|artikeltext", "x|artikeltext"}
    with pytest.raises(PriceSheetError):
        again.decide("quantity", "bedarf 2027", "reject")
    again.decide("quantity", "bedarf 2027", "remove")
    assert again.approved == {}


# ── API ─────────────────────────────────────────────────────────────────────
VISIBLE, RESTRICTED = "sales", "development"


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[tuple[TestClient, str]]:
    root = tmp_path_factory.mktemp("sheets")
    pdf = _unknown_sheet(root)
    settings = Settings(_env_file=None, data_dir=root / "data")
    with TestClient(create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())) as client:
        response = client.post("/rfqs", files=[("files", ("u.pdf", pdf.read_bytes(), "application/pdf"))])
        assert response.status_code == 200, response.text
        client.app.state.autorfx.wait()
        yield client, response.json()["data"]["id"]


def test_assigning_columns_reads_the_table_and_suggests_its_words(api: tuple[TestClient, str]) -> None:
    client, rfq_id = api
    setup = client.get(f"/rfqs/{rfq_id}/precost-setup", params={"team": VISIBLE}).json()
    assert_valid("precost-setup", setup)
    (table,) = setup["data"]["lines"]["tables"]
    assert table["how"] == "none"

    body = {"table": table["id"], "header_row": 0, "columns": {"no": 0, "description": 1, "quantity": 2}}
    denied = client.post(f"/rfqs/{rfq_id}/price-sheet/columns", params={"team": RESTRICTED}, json=body)
    assert denied.status_code == 403
    assigned = client.post(f"/rfqs/{rfq_id}/price-sheet/columns", params={"team": VISIBLE}, json=body)
    assert assigned.status_code == 200, assigned.text
    assert_valid("sheet-columns", assigned.json())
    assert [r["name"] for r in assigned.json()["data"]["lines"]["items"]] == ["Schraube M8", "Mutter M8"]

    words = client.get("/price-sheet/words", params={"team": VISIBLE}).json()
    assert_valid("sheet-words", words)
    found = {(c["role"], c["text"]) for c in words["data"]["candidates"]}
    assert found == {("no", "lfd"), ("description", "artikeltext"), ("quantity", "bedarf 2027")}

    decided = client.post(
        "/price-sheet/words",
        params={"team": VISIBLE},
        json={"role": "description", "text": "artikeltext", "action": "approve"},
    )
    assert_valid("sheet-words", decided.json())
    assert {"role": "description", "text": "artikeltext"} in decided.json()["data"]["approved"]


def test_a_wrong_assignment_is_refused(api: tuple[TestClient, str]) -> None:
    client, rfq_id = api
    table = client.get(f"/rfqs/{rfq_id}/precost-setup", params={"team": VISIBLE}).json()["data"]["lines"][
        "tables"
    ][0]
    body = {"table": table["id"], "header_row": 0, "columns": {"description": 1}}
    response = client.post(f"/rfqs/{rfq_id}/price-sheet/columns", params={"team": VISIBLE}, json=body)
    assert response.status_code == 400
    assert "수량" in response.json()["error"]["message"]
    missing = {**body, "table": "d01:p9:t9", "columns": {"description": 1, "quantity": 2}}
    assert (
        client.post(f"/rfqs/{rfq_id}/price-sheet/columns", params={"team": VISIBLE}, json=missing).status_code
        == 404
    )


def test_assign_columns_passes_approved_words_into_find_grid(
    api: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, rfq_id = api
    store = client.app.state.autorfx.price_sheets
    table = client.get(f"/rfqs/{rfq_id}/precost-setup", params={"team": VISIBLE}).json()["data"]["lines"][
        "tables"
    ][0]
    seen: dict = {}
    import autorfx.api.routes as routes

    real = routes.find_grid

    def wrap(*args, **kwargs):
        seen["words"] = kwargs.get("words")
        return real(*args, **kwargs)

    monkeypatch.setattr(routes, "find_grid", wrap)
    body = {"table": table["id"], "header_row": 0, "columns": {"no": 0, "description": 1, "quantity": 2}}
    assert (
        client.post(f"/rfqs/{rfq_id}/price-sheet/columns", params={"team": VISIBLE}, json=body).status_code
        == 200
    )
    assert "words" in seen and seen["words"] == store.approved


def test_offset_header_label_is_not_re_suggested_when_rules_already_mapped_it() -> None:
    """known compares against label_col, so a dictionary hit on the label cell is not a candidate."""
    header = ("Description", "", "Quantity", "")
    columns = {"description": 1, "quantity": 3}
    claimed = set(columns.values())
    # Pretend rules already mapped description/quantity to the label columns.
    known = {"description": 0, "quantity": 2}
    words = {}
    for role, index in columns.items():
        label, label_col = header_label(header, index, claimed)
        if label and known.get(role) != label_col:
            words[role] = label
    assert words == {}
    # Without the label_col check (comparing to value index), both would be re-suggested.
    naive = {
        role: header_label(header, i, claimed)[0]
        for role, i in columns.items()
        if known.get(role) != i and header_label(header, i, claimed)[0]
    }
    assert naive == {"description": "Description", "quantity": "Quantity"}
