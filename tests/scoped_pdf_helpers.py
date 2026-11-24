"""Reading a served PDF for leaks: raw bytes, every stream, the text layer and the pixels."""

from __future__ import annotations

import html
import io
import zipfile
from collections.abc import Iterable
from importlib import import_module
from pathlib import Path

import pymupdf

GREY_LEVEL = round(0.82 * 255)
DPI = 144


def scoped_pdf(workspace, file, parsed, visible, scope) -> Path:  # type: ignore[no-untyped-def]
    """``autorfx.scoped_pdf.scoped_pdf``, imported late: a missing module fails one test, not the run."""
    return import_module("autorfx.scoped_pdf").scoped_pdf(workspace, file, parsed, visible, scope)


def blobs_of(content: bytes) -> list[bytes]:
    """The raw file, every object and every decompressed stream of it."""
    out = [content]
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        out.append(str(doc.metadata).encode())
        out.append("".join(page.get_text() for page in doc).encode())
        for xref in range(1, doc.xref_length()):
            out.append(doc.xref_object(xref).encode())
            if doc.xref_is_stream(xref):
                out.append(doc.xref_stream(xref) or b"")
                out.append(doc.xref_stream_raw(xref) or b"")
    return out


def words_in(content: bytes, words: Iterable[str]) -> set[str]:
    """Which of ``words`` stand anywhere in the PDF, in the encodings a PDF string may have."""
    found = set()
    blobs = [blob.lower() for blob in blobs_of(content)]
    for word in words:
        forms = (word.encode(), word.encode().hex().encode(), word.encode("utf-16-be"))
        forms = (*forms, word.encode("utf-16-be").hex().encode())
        if any(form.lower() in blob for form in forms for blob in blobs):
            found.add(word)
    return found


def unzipped(content: bytes) -> str:
    """Every part of an .xlsx file, as text (Korean is stored as numeric XML entities)."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        parts = [archive.read(name).decode("utf-8", "replace") for name in archive.namelist()]
        return html.unescape("\n".join([*archive.namelist(), *parts]))


def box_of(page: pymupdf.Page, rect: dict, *, trim: float = 0.0) -> pymupdf.Rect:
    """A sentence rectangle of the API as a page rectangle; ``trim`` points shorter on every side."""
    width, height = page.rect.width, page.rect.height
    box = pymupdf.Rect(
        rect["x"] * width,
        rect["y"] * height,
        (rect["x"] + rect["w"]) * width,
        (rect["y"] + rect["h"]) * height,
    )
    return box + (trim, trim, -trim, -trim)


def pixels(page: pymupdf.Page, clip: pymupdf.Rect | None = None) -> list[tuple[int, int, int]]:
    samples = page.get_pixmap(clip=clip, dpi=DPI, alpha=False, colorspace=pymupdf.csRGB).samples
    return list(zip(samples[0::3], samples[1::3], samples[2::3], strict=True))


def is_grey(pixel: tuple[int, int, int], tolerance: int = 6) -> bool:
    return all(abs(channel - GREY_LEVEL) <= tolerance for channel in pixel)


def is_red(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red > 150 and green < 100 and blue < 100


def has_ink(page: pymupdf.Page, rect: dict) -> bool:
    """Black text on a white ground inside the box."""
    found = pixels(page, box_of(page, rect))
    return min(min(p) for p in found) < 110 and max(min(p) for p in found) > 235


def assert_flat(content: bytes) -> None:
    """docs/auth-rbac.md §5.6: nothing but one picture layer per page."""
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        assert doc.get_toc() == [] and doc.embfile_count() == 0
        assert not any(value for key, value in (doc.metadata or {}).items() if key not in ("format",))
        for page in doc:
            assert page.get_text().strip() == ""
            assert page.get_fonts(full=True) == []
            assert not list(page.annots()) and not list(page.widgets() or ()) and not page.get_links()
            assert page.cropbox == page.mediabox, "nothing is kept outside the visible page"
