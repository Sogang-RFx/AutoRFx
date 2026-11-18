"""Canonical PDF per file (SPEC §3).

Every page number in a citation is a page of the canonical PDF, so each file
gets exactly one, made by a recorded tool:

* PDF: the original bytes.
* DOCX: paragraphs and tables read with python-docx, rendered with PyMuPDF Story.
* DOC: LibreOffice when installed; on macOS without it, Quick Look to HTML and a
  headless Chromium print. Without either tool the conversion fails with an
  explicit error: the page layout is never guessed another way (SPEC §11).
* XLSX: the grid renderer in :mod:`autorfx.ingest.sheets`.

Output is deterministic for a given tool and version: metadata dates and the
document id are removed before saving (INV-7).
"""

from __future__ import annotations

import hashlib
import html
import io
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import pymupdf
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from autorfx.ingest.sheets import render_xlsx
from autorfx.models import FileKind, FileLayout
from autorfx.workspace import Workspace

log = logging.getLogger(__name__)

A4 = (595.0, 842.0)
DOCX_CSS = (
    "body{font-family:sans-serif;font-size:10pt;line-height:1.35}"
    "h1{font-size:15pt}h2{font-size:12.5pt}h3{font-size:11pt}h4,h5,h6{font-size:10pt}"
    "p{margin:0 0 6pt 0}table{border-collapse:collapse}td{border:0.5pt solid #888;padding:2pt 4pt}"
)
TIMEOUT_S = 180


class ConversionError(RuntimeError):
    """A canonical PDF could not be made. The message is shown to the user."""


@dataclass(frozen=True)
class Converted:
    tool: str
    page_count: int
    sha256: str
    warnings: tuple[str, ...] = ()
    layout: FileLayout | None = None


def save_deterministic(doc: pymupdf.Document, target: Path) -> None:
    """Write without creation dates or a fresh document id, so equal input gives equal bytes."""
    doc.set_metadata({})
    doc.del_xml_metadata()
    doc.save(str(target), garbage=4, deflate=True, no_new_id=True)


def _finish(
    target: Path, tool: str, warnings: tuple[str, ...] = (), layout: FileLayout | None = None
) -> Converted:
    data = target.read_bytes()
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages = doc.page_count
    if pages < 1:
        raise ConversionError("기준 PDF에 쪽이 없습니다.")
    return Converted(tool, pages, hashlib.sha256(data).hexdigest(), warnings, layout)


# ── PDF ─────────────────────────────────────────────────────────────────────
def _pdf_identity(source: Path, target: Path) -> Converted:
    try:
        with pymupdf.open(str(source)) as doc:
            if doc.needs_pass:
                raise ConversionError("암호가 걸린 PDF는 읽을 수 없습니다.")
            if doc.page_count < 1:
                raise ConversionError("쪽이 없는 PDF입니다.")
    except ConversionError:
        raise
    except Exception as exc:  # PyMuPDF raises several unrelated types for a damaged file
        raise ConversionError("PDF를 열 수 없습니다. 파일이 손상됐을 수 있습니다.") from exc
    shutil.copyfile(source, target)
    return _finish(target, "identity")


# ── DOCX ────────────────────────────────────────────────────────────────────
def _heading_level(style_name: str) -> int | None:
    name = style_name.casefold()
    if name == "title":
        return 1
    if name.startswith("heading"):
        digits = "".join(ch for ch in name if ch.isdigit())
        return min(int(digits), 6) if digits else 1
    return None


def _docx_html(document: Document) -> tuple[str, int]:
    """HTML of the body in document order, and the number of auto-numbered paragraphs."""
    parts: list[str] = []
    numbered = 0
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.replace("\t", " ")
            if not text.strip():
                continue
            if child.pPr is not None and child.pPr.numPr is not None:
                numbered += 1
            level = _heading_level(paragraph.style.name if paragraph.style is not None else "")
            tag_name = f"h{level}" if level else "p"
            parts.append(f"<{tag_name}>{html.escape(text)}</{tag_name}>")
        elif tag == "tbl":
            rows = []
            for row in Table(child, document).rows:
                cells = "".join(f"<td>{html.escape(cell.text)}</td>" for cell in row.cells)
                rows.append(f"<tr>{cells}</tr>")
            parts.append(f"<table>{''.join(rows)}</table>")
    return "".join(parts), numbered


def _story_to_pdf(body_html: str, css: str, size: tuple[float, float], margins: tuple[float, ...]) -> bytes:
    buffer = io.BytesIO()
    story = pymupdf.Story(html=body_html, user_css=css)
    writer = pymupdf.DocumentWriter(buffer)
    mediabox = pymupdf.Rect(0, 0, *size)
    left, top, right, bottom = margins
    where = mediabox + (left, top, -right, -bottom)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return buffer.getvalue()


def _docx_to_pdf(source: Path, target: Path) -> Converted:
    try:
        document = Document(str(source))
    except Exception as exc:  # python-docx raises zipfile, KeyError or lxml errors for bad input
        raise ConversionError("DOCX를 열 수 없습니다. 파일이 손상됐을 수 있습니다.") from exc
    body, numbered = _docx_html(document)
    if not body:
        raise ConversionError("DOCX에 본문이 없습니다.")
    section = document.sections[0]
    size = (
        section.page_width.pt if section.page_width else A4[0],
        section.page_height.pt if section.page_height else A4[1],
    )
    margins = tuple(
        m.pt if m is not None else 72.0
        for m in (section.left_margin, section.top_margin, section.right_margin, section.bottom_margin)
    )
    with pymupdf.open(stream=_story_to_pdf(body, DOCX_CSS, size, margins), filetype="pdf") as doc:
        save_deterministic(doc, target)
    warnings = (
        (f"자동 번호 문단 {numbered}개: 번호가 기준 PDF에 나타나지 않을 수 있습니다.",) if numbered else ()
    )
    tool = f"python-docx {version('python-docx')} + pymupdf-story {pymupdf.__version__}"
    return _finish(target, tool, warnings)


# ── DOC ─────────────────────────────────────────────────────────────────────
CSP = (
    b'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src file: data:; '
    b"style-src 'unsafe-inline' file:; font-src file: data:\">"
)
HEAD = re.compile(rb"<head[^>]*>", re.IGNORECASE)


def _lock_down(preview: Path) -> None:
    """Put a policy that blocks scripts and remote loads into a generated preview page.

    Inserted after ``<head>`` (or prepended when there is none) so the page's own doctype and
    layout mode stay as Quick Look wrote them.
    """
    page = preview.read_bytes()
    match = HEAD.search(page)
    at = match.end() if match else 0
    preview.write_bytes(page[:at] + CSP + page[at:])


def find_soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    bundled = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return found or (str(bundled) if bundled.is_file() else None)


def find_chromium() -> str | None:
    configured = os.environ.get("AUTORFX_CHROMIUM")
    if configured:
        return configured if Path(configured).is_file() else None
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    shells = sorted(cache.glob("chromium_headless_shell-*/chrome-headless-shell-mac-*/chrome-headless-shell"))
    if shells:
        return str(shells[-1])
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(chrome) if chrome.is_file() else None


def _run(command: list[str], what: str) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConversionError(f"{what} 실행에 실패했습니다.") from exc
    if done.returncode != 0:
        raise ConversionError(f"{what} 변환이 실패했습니다 (종료 코드 {done.returncode}).")
    return done.stdout.strip()


def _strip_trailing_blank_pages(doc: pymupdf.Document) -> None:
    last = doc.page_count - 1
    while last > 0:
        page = doc[last]
        if page.get_text("words") or page.get_images() or page.get_drawings():
            break
        last -= 1
    if last < doc.page_count - 1:
        doc.delete_pages(from_page=last + 1, to_page=doc.page_count - 1)


def _doc_to_pdf(source: Path, target: Path, scratch: Path) -> Converted:
    scratch.mkdir(parents=True, exist_ok=True)
    work = scratch / "input.doc"
    shutil.copyfile(source, work)
    soffice = find_soffice()
    chromium = find_chromium()
    if soffice:
        _run(
            [
                soffice,
                "--headless",
                "--norestore",
                f"-env:UserInstallation={(scratch / 'profile').as_uri()}",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(scratch),
                str(work),
            ],
            "LibreOffice",
        )
        raw = scratch / "input.pdf"
        tool = f"libreoffice ({_run([soffice, '--version'], 'LibreOffice')}) + pymupdf {pymupdf.__version__}"
    elif sys.platform == "darwin" and shutil.which("qlmanage") and chromium:
        _run(["qlmanage", "-p", "-o", str(scratch), str(work)], "Quick Look")
        preview = scratch / "input.doc.qlpreview" / "Preview.html"
        if not preview.is_file():
            raise ConversionError("Quick Look이 미리보기를 만들지 못했습니다.")
        raw = scratch / "raw.pdf"
        # The preview is built from an uploaded document: it is printed with scripts blocked (a CSP
        # in the scratch copy; Chromium cannot print headless with scripts switched off), with no
        # name resolution or proxy to reach, and with a throwaway profile.
        _lock_down(preview)
        _run(
            [
                chromium,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--host-resolver-rules=MAP * ~NOTFOUND",
                "--proxy-server=127.0.0.1:9",
                "--disable-background-networking",
                f"--user-data-dir={scratch / 'chromium-profile'}",
                f"--print-to-pdf={raw}",
                preview.as_uri(),
            ],
            "Chromium",
        )
        tool = f"quicklook + {_run([chromium, '--version'], 'Chromium')} + pymupdf {pymupdf.__version__}"
    else:
        raise ConversionError(
            ".doc 파일을 기준 PDF로 바꿀 도구가 없습니다. "
            "LibreOffice를 설치하거나 .docx 또는 PDF로 저장해 올려 주세요."
        )
    if not raw.is_file():
        raise ConversionError("변환 결과 파일이 만들어지지 않았습니다.")
    with pymupdf.open(str(raw)) as doc:
        _strip_trailing_blank_pages(doc)
        save_deterministic(doc, target)
    return _finish(target, tool)


# ── entry point ─────────────────────────────────────────────────────────────
def make_canonical(source: Path, kind: FileKind, workspace: Workspace, file_id: str) -> Converted:
    """Write ``workspace.canonical_pdf(file_id)`` (and the layout file for sheets)."""
    target = workspace.canonical_pdf(file_id)
    if kind == "pdf":
        return _pdf_identity(source, target)
    if kind == "docx":
        return _docx_to_pdf(source, target)
    if kind == "xlsx":
        layout, tool = render_xlsx(source, target, file_id)
        workspace.save_layout(layout)
        return _finish(target, tool, layout=layout)
    scratch = workspace.root / "tmp" / file_id
    try:
        return _doc_to_pdf(source, target, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
