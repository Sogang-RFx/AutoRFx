"""A package in which every department owns a word nobody else may read.

All text is invented. A *sentinel* is a made-up word that stands in exactly one place, so finding it in a
response says which clause, heading, name or hidden part of a file leaked. Sentinels share no prefix or
suffix with each other or with a rule-file word: the search would treat those as inflections.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf
from openpyxl import Workbook

# In the body of a clause of one department.
ZANDER, MOKTH, BRELF, CUVIN = "zanderquill", "mokthvane", "brelfosk", "cuvindrax"
DWELM, FYRN, GHOLT, HUXB = "dwelmptor", "fyrnogash", "gholtwick", "huxbrimle"
SHARED = "wurzelpand"
"""In a clause with a lead and a reference department: both see it."""
COST = "vextholm"
"""In a cost-sensitive clause that is routed to quality too."""
OWNED_HEADING = "kwondrelt"
"""The title of a clause of one department, inherited by a child of another."""
BARE_HEADING = "jesplovar"
"""A heading that belongs to no clause, above a visible child."""
FIFTH = "96187 h"
"""The fifth value of a conflict: only four pieces of evidence are kept, but the detail names all."""
ACTUAL_TEXT = "quenlithor"
"""Only in /ActualText, which is what programs read; what is drawn says "plate"."""
OFF_PAGE = "plombersnag"
"""Outside the crop box: in the file, never on the page."""
BOOKMARK, NOTE, ATTACHED, ATTACHED_NAME = "ryngaldo", "spuvichel", "nimboract", "axolprime"
FIRST_NAME, SHEET, LAST_NAME, LAST = "ostrelwin", "yttrelbane", "ibexfolder", "tarnubold"
"""The first file's name, a sheet's name, the name of a file only the full scope sees, and its clause."""
RED = (255, 0, 0)

BEYOND_TEXT = (OFF_PAGE, BOOKMARK, NOTE, ATTACHED, ATTACHED_NAME)
"""In a file but in no sentence: no response of the API carries them, except the canonical PDF."""

OWNERS: dict[str, frozenset[str]] = {
    ZANDER: frozenset(),
    MOKTH: frozenset({"development"}),
    BRELF: frozenset({"quality"}),
    CUVIN: frozenset({"production_engineering"}),
    GHOLT: frozenset({"logistics"}),
    FYRN: frozenset({"costing"}),
    SHARED: frozenset({"logistics", "legal"}),
    OWNED_HEADING: frozenset({"production_engineering"}),
    BARE_HEADING: frozenset(),
    HUXB: frozenset({"legal"}),
    DWELM: frozenset({"purchasing"}),
    COST: frozenset({"costing"}),
    FIFTH: frozenset({"logistics"}),
    ACTUAL_TEXT: frozenset({"development"}),
    SHEET: frozenset({"purchasing"}),
    LAST: frozenset(),
    LAST_NAME: frozenset(),
    FIRST_NAME: frozenset(
        {"development", "quality", "production_engineering", "costing", "logistics", "legal"}
    ),
    **dict.fromkeys(BEYOND_TEXT, frozenset()),
}
"""Which limited accounts may read each sentinel. The administrator and sales read every one of them."""


def foreign(name: str) -> tuple[str, ...]:
    """The sentinels this limited account must never receive."""
    return tuple(word for word, owners in OWNERS.items() if name not in owners)


def own(name: str) -> tuple[str, ...]:
    return tuple(word for word, owners in OWNERS.items() if name in owners)


ROUTING: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "d01:0": ("sales", (), False),
    "d01:1.1": ("development", (), False),
    "d01:1.2": ("quality", (), False),
    "d01:1.3": ("production_engineering", (), False),
    "d01:1.4": ("logistics", (), False),
    "d01:1.5": ("costing", (), True),
    "d01:1.6": ("logistics", ("legal",), False),
    "d01:2": ("production_engineering", (), False),
    "d01:2.1": ("logistics", (), False),
    "d01:3.1": ("logistics", (), False),
    "d02:1.1": ("legal", (), False),
    "d02:1.2": ("purchasing", (), False),
    "d02:1.3": ("quality", ("costing",), True),
    "d02:1.4": ("quality", (), False),
    "d02:1.5": ("quality", (), False),
    "d02:1.6": ("quality", (), False),
    "d02:1.7": ("quality", (), False),
    "d02:1.8": ("logistics", (), False),
    "d02:1.9": ("development", (), False),
    f"d03:{SHEET}": ("purchasing", (), False),
    "d04:1.1": ("sales", (), False),
}
"""Lead, reference departments and cost flag per clause, as the rules must judge them."""

MAIN = (
    f"General remarks {ZANDER} on the cover.",
    "1 Part one",
    f"1.1 The drawing shows the sheet thickness and tolerance {MOKTH} of the bracket.",
    f"1.2 An audit of the ISO 9001 certification {BRELF} happens every year.",
    f"1.3 The press and the stamping equipment {CUVIN} are described.",
    f"1.4 Packaging in small load carrier or container {GHOLT} is agreed.",
    f"1.5 Payment terms and currency {FYRN} are listed.",
    f"1.6 Warranty, liability, packaging and container {SHARED} go together.",
    f"2 Shopfloor {OWNED_HEADING}",
    "The press and the stamping line belong to the owner.",
    "2.1 Packaging and container rules apply here.",
    f"3 Overview {BARE_HEADING}",
    "3.1 Freight and shipping follow the calendar.",
)
EXTRA = (
    "1 Part two",
    f"1.1 Liability and governing law {HUXB} are set out in Annex 77.",
    f"1.2 Raw material coil comes from a sub-supplier {DWELM} nearby.",
    f"1.3 Inspection certificate and audit {COST} raise the target price by 7.31 EUR.",
    "1.4 Salt spray 96 h until red rust, checked in the audit.",
    "1.5 Salt spray 240 h until red rust, checked in the audit.",
    "1.6 Salt spray 480 h until red rust, checked in the audit.",
    "1.7 Salt spray 720 h until red rust, checked in the audit.",
    f"1.8 Salt spray {FIFTH} for packaging, container, freight and shipping.",
)
LASTLY = (
    "1 Part four",
    f"1.1 The scope of work, deadline and nomination {LAST} are open.",
)
STEP = 30


def _lines(page: pymupdf.Page, lines: tuple[str, ...]) -> float:
    """One text block per line, as a word processor would draw a short list; returns the next baseline."""
    for number, text in enumerate(lines):
        page.insert_text((60, 72 + STEP * number), text, fontsize=9)
    return 72 + STEP * len(lines)


def _main_pdf(path: Path) -> Path:
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        _lines(page, MAIN)
        doc.set_toc([[1, f"Bookmark {BOOKMARK}", 1]])
        page.add_text_annot((540, 60), f"Note {NOTE}")
        doc.embfile_add(
            f"{ATTACHED_NAME}.txt", f"Attachment {ATTACHED}".encode(), filename=f"{ATTACHED_NAME}.txt"
        )
        doc.save(str(path))
    return path


def _extra_pdf(path: Path) -> Path:
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        baseline = _lines(page, EXTRA)
        page.insert_text((60, baseline), "1.9 The drawing of the bracket shows a", fontsize=9)
        # Drawn: "plate". Read by a program that honours /ActualText: the sentinel.
        xref = page.get_contents()[-1]
        stream = (
            f"\nq /Span <</ActualText ({ACTUAL_TEXT}.)>> BDC BT /helv 9 Tf 1 0 0 1 240 {842 - baseline:g} Tm "
            "(plate.) Tj ET EMC Q\n"
        )
        doc.update_stream(xref, doc.xref_stream(xref) + stream.encode("ascii"))
        page.insert_text((60, 832), f"Margin {OFF_PAGE} text", fontsize=9)
        pixels = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 90, 40))
        pixels.set_rect(pixels.irect, RED)
        page.insert_image(pymupdf.Rect(400, 600, 490, 640), pixmap=pixels)
        page.set_cropbox(pymupdf.Rect(0, 0, 595, 800))
        doc.save(str(path))
    return path


def _sheet(path: Path) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(["Position", "Raw material", "Sub-supplier"])
    sheet.append(["A", "coil", "near"])
    sheet.append(["B", "coil", "far"])
    book.save(str(path))
    return path


def _last_pdf(path: Path) -> Path:
    with pymupdf.open() as doc:
        _lines(doc.new_page(width=595, height=842), LASTLY)
        doc.save(str(path))
    return path


def make_isolation_package(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return [
        _main_pdf(folder / f"01_{FIRST_NAME}.pdf"),
        _extra_pdf(folder / "02_extra.pdf"),
        _sheet(folder / "03_list.xlsx"),
        _last_pdf(folder / f"04_{LAST_NAME}.pdf"),
    ]


@dataclass(frozen=True)
class FileOf:
    """The files of the package and who sees at least one of their clauses."""

    id: str
    visible_to: frozenset[str]


FILES = (
    FileOf(
        "d01",
        frozenset({"development", "quality", "production_engineering", "costing", "logistics", "legal"}),
    ),
    FileOf("d02", frozenset({"development", "quality", "purchasing", "costing", "logistics", "legal"})),
    FileOf("d03", frozenset({"purchasing"})),
    FileOf("d04", frozenset()),
)
