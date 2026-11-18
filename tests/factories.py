"""Builders for hand-made test data.

All text here is invented. Tests never quote the development RFQs; tests that
need them are marked ``corpus`` and read ``data/raw/dev`` at run time.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from autorfx.models import (
    Clause,
    ClauseKind,
    FileRecord,
    Lang,
    Locus,
    PackageManifest,
    ParsedSentence,
    Rect,
    SentenceRole,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def make_locus(
    sentence_index: int = 1,
    *,
    source_doc: str = "d01",
    page_number: int = 1,
    section_id: str = "1",
    paragraph_index: int = 1,
    anchor: str | None = None,
) -> Locus:
    return Locus(
        source_doc=source_doc,
        page_number=page_number,
        section_id=section_id,
        paragraph_index=paragraph_index,
        sentence_index=sentence_index,
        anchor=anchor,
    )


def make_sentence(
    text: str,
    sentence_index: int = 1,
    *,
    role: SentenceRole = "body",
    **locus: object,
) -> ParsedSentence:
    """A sentence with one plausible rect: line ``sentence_index`` of the page."""
    y = min(0.05 + 0.03 * (sentence_index - 1), 0.95)
    return ParsedSentence(
        locus=make_locus(sentence_index, **locus),  # type: ignore[arg-type]
        text=text,
        rects=(Rect(x=0.1, y=y, w=0.8, h=0.02),),
        role=role,
    )


def make_clause(
    section_id: str,
    texts: Sequence[str],
    *,
    title: str = "",
    source_doc: str = "d01",
    page_number: int = 1,
    first_sentence: int = 1,
    lang: Lang = "en",
    kind: ClauseKind = "clause",
    is_cost_sensitive: bool = False,
    is_boilerplate: bool = False,
) -> Clause:
    """A clause whose sentences are numbered consecutively on one page."""
    sentences = tuple(
        make_sentence(
            text,
            first_sentence + i,
            source_doc=source_doc,
            page_number=page_number,
            section_id=section_id,
            paragraph_index=first_sentence + i,
        )
        for i, text in enumerate(texts)
    )
    parts = section_id.split(".")
    return Clause(
        clause_id=f"{source_doc}:{section_id}",
        source_doc=source_doc,
        section_id=section_id,
        section_path=tuple(".".join(parts[: i + 1]) for i in range(len(parts))),
        title=title,
        kind=kind,
        lang=lang,
        sentences=sentences,
        is_cost_sensitive=is_cost_sensitive,
        is_boilerplate=is_boilerplate,
    )


def make_file(file_id: str = "d01", name: str = "rfq.pdf", *, page_count: int = 1) -> FileRecord:
    return FileRecord(
        id=file_id,
        name=name,
        kind="pdf",
        lang="en",
        page_count=page_count,
        sha256=SHA_A,
        canonical_tool="identity",
        canonical_sha256=SHA_A,
    )


def make_manifest(
    files: Sequence[FileRecord] | None = None, *, rfq_id: str = "rfq-test000001"
) -> PackageManifest:
    records = tuple(files) if files else (make_file(),)
    return PackageManifest(
        id=rfq_id,
        name="Synthetic test package",
        files=records,
        page_count=sum(f.page_count for f in records),
        sha256=SHA_B,
        is_sample=False,
        created_at=1_700_000_000_000,
    )


PACKAGE_SECRET = "1.2345 EUR"
PACKAGE_MAIN = (
    "<p>Request for Quotation RFQ-2026-014 for seat brackets.</p>"
    "<h2>1 Scope</h2>"
    "<p>1.1 The supplier shall deliver brackets according to drawing AB-100.</p>"
    "<h2>2 Commercial</h2>"
    "<p>2.1 Delivery is FCA supplier plant according to Incoterms 2020.</p>"
    f"<p>2.2 The target price is {PACKAGE_SECRET} per piece and quotes above it will be rejected.</p>"
    "<p>2.3 The warranty period is 36 months from delivery.</p>"
)
PACKAGE_LOGISTICS = (
    "<h2>1 Logistics</h2>"
    "<p>1.1 Delivery terms: DDP buyer plant (Incoterms 2020).</p>"
    "<p>1.2 The supplier shall hold a safety stock of 10 working days.</p>"
)


def make_package(folder: Path) -> list[Path]:
    """A two-file package: a price clause to hide, and delivery terms that contradict each other."""
    return [
        pdf_from_html(folder / "01_rfq.pdf", PACKAGE_MAIN),
        pdf_from_html(folder / "02_logistics.pdf", PACKAGE_LOGISTICS),
    ]


def pdf_from_html(path: Path, html: str) -> Path:
    """A text PDF whose paragraphs are separate blocks, as a word processor would produce."""
    import pymupdf

    path.parent.mkdir(parents=True, exist_ok=True)
    story = pymupdf.Story(
        html=html, user_css="body{font-family:sans-serif;font-size:10pt}p{margin:0 0 8pt 0}"
    )
    writer = pymupdf.DocumentWriter(str(path))
    box = pymupdf.Rect(0, 0, 595, 842)
    more = True
    while more:
        device = writer.begin_page(box)
        more, _ = story.place(box + (72, 72, -72, -72))
        story.draw(device)
        writer.end_page()
    writer.close()
    return path
