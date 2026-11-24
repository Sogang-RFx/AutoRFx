"""Price-sheet rows read from the RFQ for pricing line by line."""

from pathlib import Path

import pymupdf

from autorfx.models import Locus, PackageManifest, Table
from autorfx.precost.lines import _from_pdf, _from_tables, rfq_lines


def _pdf(path: Path, pages: list[list[str]]) -> Path:
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        for i, text in enumerate(lines):
            # One block per line: rows of a printed table are separate blocks.
            page.insert_text((60, 100 + i * 28), text, fontsize=9)
    doc.save(str(path))
    return path


SHEET = [
    "FORM 3.02 PRICE SHEET",
    "Category 1 - Brakes",
    "ITEM # PART NUMBER DESCRIPTION UNIT PRICE YEARLY QUANTITY EXTENDED PRICE",
    "1 EU2Z-2V026-A ROTORS, REAR BRAKE $ 36 $",
    "2 GB5Z-2001-B PADS BRAKE FRONT $ 19 $",
    "3 8C2Z-17626-AACP",
    "PANEL, LOWER FRONT BUMPER VALANCE",
    "$ 2 $",
    "Category 1 - Brakes Subtotal: $",
    "Category 2 - Filters",
    "ITEM # PART NUMBER DESCRIPTION UNIT PRICE YEARLY QUANTITY EXTENDED PRICE",
    "1 FL-500-SB12 FILTER OIL FORD 5.0L $ 180 $",
]


def test_rows_of_a_price_sheet_are_read_with_their_group_and_page(tmp_path: Path) -> None:
    rows = _from_pdf("d01", _pdf(tmp_path / "a.pdf", [["The supplier delivers within 2 days.", *SHEET]]))
    assert [(r["group"], r["no"], r["part"], r["name"], r["quantity"]) for r in rows] == [
        ("Brakes", "1", "EU2Z-2V026-A", "ROTORS, REAR BRAKE", 36),
        ("Brakes", "2", "GB5Z-2001-B", "PADS BRAKE FRONT", 19),
        ("Brakes", "3", "8C2Z-17626-AACP", "PANEL, LOWER FRONT BUMPER VALANCE", 2),
        ("Filters", "1", "FL-500-SB12", "FILTER OIL FORD 5.0L", 180),
    ]
    assert all(r["yearly"] and r["page"] == 1 for r in rows)


def test_text_before_a_price_sheet_header_is_not_a_line(tmp_path: Path) -> None:
    rows = _from_pdf("d01", _pdf(tmp_path / "a.pdf", [["1 year with 3 extensions of 12", "2 copies of 30"]]))
    assert rows == []


def _manifest(*kinds: str) -> PackageManifest:
    files = [{"id": f"d0{i}", "kind": k} for i, k in enumerate(kinds, start=1)]
    return PackageManifest.model_construct(files=[type("F", (), f)() for f in files])


def test_a_revised_sheet_replaces_the_earlier_listing_of_a_group(tmp_path: Path) -> None:
    revised = [
        "FORM 3.02 PRICE SHEET *REVISED*",
        "Category 1 - Brakes",
        "ITEM # PART NUMBER DESCRIPTION UNIT PRICE YEARLY QUANTITY EXTENDED PRICE",
        "1 EU2Z-2V026-A ROTORS, REAR BRAKE $ 40 $",
    ]
    path = _pdf(tmp_path / "a.pdf", [SHEET, revised])
    found = rfq_lines(_manifest("pdf"), {}, {"d01": path})
    brakes = [r for r in found["items"] if r["group"] == "Brakes"]
    assert [(r["no"], r["quantity"], r["page"]) for r in brakes] == [("1", 40, 2)]
    assert found["total"] == 2  # the revised brake and the filter, which was not revised


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> Table:
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


def test_a_monthly_quantity_is_multiplied_by_the_months() -> None:
    table = _table(
        ("", "PART DESCRIPTION", "MAX QUANTITY Per month", "Months", "UNIT PRICE"),
        [("75545", "OIL 0W20", "5", "12", "0"), ("", "TOTAL", "", "", "0")],
    )
    rows = _from_tables("d01", [table])
    assert [(r["name"], r["quantity"], r["yearly"]) for r in rows] == [("OIL 0W20", 60, True)]


def test_a_sheet_without_quantities_has_no_lines() -> None:
    table = _table(("PART NO.", "DESCRIPTION", "Unit Price"), [("7237", "AIR FILTER", "0")])
    assert _from_tables("d01", [table]) == []
