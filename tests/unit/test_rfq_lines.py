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
