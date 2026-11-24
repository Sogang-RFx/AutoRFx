"""Price-sheet rows read from the RFQ for pricing line by line, in any column layout.

Tables come from ruling lines (or text alignment) of a PDF and from a workbook; the columns
are named by data/rules/price_sheet.yaml, by a layout a user assigned, or by words a user
approved. Test PDFs draw their own cell borders, as a word processor's table would.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from autorfx.analysis import RuleOnlyProvider
from autorfx.api import create_app
from autorfx.config import Settings
from autorfx.models import Locus, PackageManifest, Table
from autorfx.precost import lines as lines_mod
from autorfx.precost.lines import pdf_grids, read_grids, rfq_lines, signature, table_grids
from autorfx.precost.sheet_store import PriceSheetError, PriceSheetStore, check_columns
from autorfx.rules import PriceSheetRules, Ruleset
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
    grids = pdf_grids("d01", path, rules)
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


def test_spec_and_bid_form_sharing_a_lot_name_keep_both_lists(
    tmp_path: Path, rules: PriceSheetRules
) -> None:
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
    grids = table_grids("d01", [_sheet_table(header, rows)])
    (reading,) = read_grids(grids, rules.compiled(), {})
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
