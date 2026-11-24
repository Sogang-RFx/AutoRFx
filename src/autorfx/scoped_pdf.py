"""The source PDF of one scope, as pictures (docs/auth-rbac.md §5; INV-8, RBAC-02).

Redacting glyphs is not enough for a page most of which is hidden: ``/ActualText``, text outside the crop
box, bookmarks and attachments stay in the file. So everything outside the visible sentences is removed
from a copy in memory, what is left is drawn, and only the boxes of visible sentences are copied, as
images, onto grey pages of a new document. The result is opened again and checked; a file that does not
pass, or any error on the way, is refused, and nothing of it stays on disk.

The canonical file is read only. Pictures inside a visible box stay in it (§8).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from weakref import WeakValueDictionary

import pymupdf

from autorfx.access import GREY, TRIM, AccessDenied, Key, page_with_remains, visible_keys
from autorfx.auth.policy import Scope
from autorfx.ingest.convert import save_deterministic
from autorfx.models import Clause, FileRecord, ParsedFile, ParsedSentence
from autorfx.workspace import Workspace

log = logging.getLogger(__name__)

SCOPE_VERSION = "2026-11-25.1"
"""Raise when a change draws a page differently: files of an older version are then not served."""
DPI = 144
ZOOM = DPI / 72
GREY_PIXEL = tuple(round(channel * 255) for channel in GREY)
TOLERANCE = 2
WORDS = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_PRESERVE_LIGATURES
SLACK = 1.0
"""Points a word may stand outside the box of its sentence: coordinates are stored as fractions."""
BAND, EDGE = 0.45, 0.5
"""A removed word is marked by a thin band through its middle: a redaction removes every character it
touches, so a full box would take letters of the lines above and below and of the next word."""
REFUSED = "이 계정에 보여 줄 수 있는 원문을 만들지 못해 원문을 제공하지 않습니다."


class _Mismatch(Exception):
    """A check failed. The message goes to the log, never to the client."""


# ── one drawing per key ─────────────────────────────────────────────────────
_registry = threading.Lock()
_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_drawing = threading.Lock()
"""PyMuPDF is not safe to use from several threads at once; request threads take turns."""


@contextmanager
def _one_at_a_time(key: str) -> Iterator[None]:
    with _registry:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        yield


def _cache_key(file: FileRecord, parsed: ParsedFile, allowed: frozenset[Key]) -> str:
    """Everything the picture depends on. Who asks is not part of it."""
    boxes = [(s.locus.key(), [(r.x, r.y, r.w, r.h) for r in s.rects]) for s in parsed.sentences]
    content = [SCOPE_VERSION, file.canonical_sha256, sorted(allowed), boxes]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode("utf-8")).hexdigest()


# ── geometry ────────────────────────────────────────────────────────────────
def _boxes(page: pymupdf.Page, sentence: ParsedSentence) -> list[pymupdf.Rect]:
    width, height = page.rect.width, page.rect.height
    return [
        pymupdf.Rect(r.x * width, r.y * height, (r.x + r.w) * width, (r.y + r.h) * height)
        for r in sentence.rects
    ]


def _trimmed(box: pymupdf.Rect) -> pymupdf.Rect:
    """Shorter at the top and bottom, so that the lines above and below are not touched."""
    return box + (0, box.height * TRIM, 0, -box.height * TRIM)


def _pixels(box: pymupdf.Rect) -> pymupdf.IRect:
    """The whole pixels inside a box (rounded inwards: nothing outside the box is drawn)."""
    small = 1e-3  # a box that ends on a pixel edge keeps that pixel in spite of float noise
    return pymupdf.IRect(
        -int(-(box.x0 * ZOOM - small) // 1),
        -int(-(box.y0 * ZOOM - small) // 1),
        int((box.x1 * ZOOM + small) // 1),
        int((box.y1 * ZOOM + small) // 1),
    )


def _holds(boxes: Sequence[pymupdf.Rect], word: tuple) -> bool:
    x, y = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
    return any(box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1 for box in boxes)


def _by_page(sentences: Sequence[ParsedSentence]) -> Mapping[int, list[ParsedSentence]]:
    pages: dict[int, list[ParsedSentence]] = defaultdict(list)
    for sentence in sentences:
        pages[sentence.locus.page_number].append(sentence)
    return pages


# ── steps of §5 ─────────────────────────────────────────────────────────────
def _check_positions(doc: pymupdf.Document, file: FileRecord, parsed: ParsedFile) -> None:
    """§5.2: the positions belong to this document: same pages, and a word at every sentence."""
    if not doc.page_count == parsed.page_count == file.page_count:
        raise _Mismatch("number of pages")
    pages = _by_page(parsed.sentences)
    if any(not 1 <= number <= doc.page_count for number in pages):
        raise _Mismatch("a sentence on a page that does not exist")
    for number, sentences in pages.items():
        page = doc[number - 1]
        if page.rotation:
            raise _Mismatch(f"page {number} is rotated")
        words = page.get_text("words", flags=WORDS)
        for sentence in sentences:
            around = [box + (-SLACK, -SLACK, SLACK, SLACK) for box in _boxes(page, sentence)]
            if not any(_holds(around, word) for word in words):
                raise _Mismatch(f"no word at sentence {sentence.locus.sentence_index} of page {number}")


def _strip(page: pymupdf.Page) -> None:
    for annotation in list(page.annots()):
        page.delete_annot(annotation)
    for widget in list(page.widgets()):
        page.delete_widget(widget)
    for link in page.get_links():
        page.delete_link(link)


def _bands(words: Sequence[tuple]) -> list[pymupdf.Rect]:
    """Thin bands through the middle of these words, which follow each other in reading order.

    Neighbours on a line share a band: one redaction per word takes seconds on a dense page.
    """
    runs: list[list[tuple]] = []
    for word in words:
        last = runs[-1][-1] if runs else None
        if last is not None and (
            (word[5], word[6], word[7]) == (last[5], last[6], last[7] + 1)
            and abs(word[1] - last[1]) + abs(word[3] - last[3]) < SLACK
            and 0 <= word[0] - last[2] < word[3] - word[1]
        ):
            runs[-1].append(word)
        else:
            runs.append([word])
    bands = []
    for run in runs:
        box = pymupdf.Rect(run[0][0], run[0][1], run[-1][2], run[0][3])
        edge = min(EDGE, *((word[2] - word[0]) / 4 for word in (run[0], run[-1])))
        bands.append(box + (edge, box.height * BAND, -edge, -box.height * BAND))
    return bands


def _redact(
    page: pymupdf.Page, allowed: Sequence[pymupdf.Rect], hidden: Sequence[pymupdf.Rect]
) -> Counter[str]:
    """§5.3: remove every word that is not a visible sentence's. Returns the words that may remain."""
    words = page.get_text("words", flags=WORDS)
    stays = [_holds(allowed, word) and not _holds(hidden, word) for word in words]
    for band in _bands([word for word, may_stay in zip(words, stays, strict=True) if not may_stay]):
        page.add_redact_annot(band, fill=GREY, cross_out=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE, graphics=2)
    return Counter(word[4] for word, may_stay in zip(words, stays, strict=True) if may_stay)


def _cut_down(
    doc: pymupdf.Document, canonical: pymupdf.Document, parsed: ParsedFile, allowed: frozenset[Key]
) -> pymupdf.Document:
    """§5.3-4: a copy that holds the words of visible sentences only, saved, opened again and checked."""
    hidden = [s for s in parsed.sentences if s.locus.key() not in allowed]
    kept: list[Counter[str]] = []
    pages = _by_page(parsed.sentences)
    for number, page in enumerate(doc, start=1):
        _strip(page)
        shown_boxes: list[pymupdf.Rect] = []
        hidden_boxes: list[pymupdf.Rect] = []
        for sentence in pages.get(number, ()):
            if sentence.locus.key() in allowed:
                shown_boxes += [box + (-SLACK, 0, SLACK, 0) for box in _boxes(page, sentence)]
            else:
                hidden_boxes += [_trimmed(box) for box in _boxes(page, sentence)]
        kept.append(_redact(page, shown_boxes, hidden_boxes))
    reduced = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
    try:
        if (number := page_with_remains(canonical, reduced, hidden)) is not None:
            raise _Mismatch(f"hidden words are left on page {number}")
        for number, (page, may_stay) in enumerate(zip(reduced, kept, strict=True), start=1):
            left = Counter(word[4] for word in page.get_text("words", flags=WORDS))
            if left - may_stay:
                raise _Mismatch(f"words that may not stay are left on page {number}")
    except BaseException:
        reduced.close()
        raise
    return reduced


def _draw(
    reduced: pymupdf.Document, parsed: ParsedFile, allowed: frozenset[Key]
) -> tuple[pymupdf.Document, list[list[pymupdf.IRect]]]:
    """§5.5: grey pages of the same size with a picture of each visible box, and where those are."""
    flat = pymupdf.open()
    drawn: list[list[pymupdf.IRect]] = []
    pages = _by_page(parsed.sentences)
    for number, page in enumerate(reduced, start=1):
        target = flat.new_page(width=page.rect.width, height=page.rect.height)
        # Larger than the page: the last pixels of a page of fractional size would else be mixed with white.
        target.draw_rect(target.rect + (-2, -2, 2, 2), color=None, fill=GREY)
        areas: list[pymupdf.IRect] = []
        grey: list[pymupdf.IRect] = []
        for sentence in pages.get(number, ()):
            for box in _boxes(page, sentence):
                if sentence.locus.key() in allowed:
                    areas.append(_pixels(box & page.rect))
                else:
                    grey.append(_pixels(_trimmed(box)))
        areas = [area for area in areas if not area.is_empty]
        drawn.append(areas)
        if not areas:
            continue
        # Drawn once per page; nothing but the boxes below is copied from it.
        picture = page.get_pixmap(dpi=DPI, annots=False, alpha=False, colorspace=pymupdf.csRGB)
        for area in grey:
            picture.set_rect(area, GREY_PIXEL)
        for area in areas:
            part = pymupdf.Pixmap(pymupdf.csRGB, area, False)
            part.copy(picture, area)
            target.insert_image(pymupdf.Rect(area) / ZOOM, pixmap=part)
    return flat, drawn


def _check_flat(path: Path, reduced: pymupdf.Document, drawn: Sequence[Sequence[pymupdf.IRect]]) -> None:
    """§5.6: the finished file holds the pictures of the visible boxes on grey, and nothing else."""
    with pymupdf.open(str(path), filetype="pdf") as doc:
        if doc.page_count != reduced.page_count:
            raise _Mismatch("pages of the finished file")
        if doc.get_toc() or doc.embfile_count() or any(v for k, v in doc.metadata.items() if k != "format"):
            raise _Mismatch("bookmarks, attachments or metadata in the finished file")
        for number, (page, areas) in enumerate(zip(doc, drawn, strict=True), start=1):
            if page.rect != reduced[number - 1].rect or page.cropbox != page.mediabox:
                raise _Mismatch(f"size of page {number}")
            if page.get_text().strip() or page.get_fonts(full=True):
                raise _Mismatch(f"text on page {number}")
            if list(page.annots()) or list(page.widgets()) or page.get_links():
                raise _Mismatch(f"annotations on page {number}")
            if len(page.get_image_info()) != len(areas):
                raise _Mismatch(f"pictures on page {number}")
            picture = page.get_pixmap(dpi=DPI, alpha=False, colorspace=pymupdf.csRGB)
            for area in areas:
                picture.set_rect(area, GREY_PIXEL)
            if not _is_grey(picture):
                raise _Mismatch(f"something is drawn outside the visible boxes of page {number}")


def _is_grey(picture: pymupdf.Pixmap) -> bool:
    """Whether every pixel is the grey of a hidden area."""
    channels = (frozenset(picture.samples[offset::3]) for offset in range(3))
    return all(
        abs(value - wanted) <= TOLERANCE
        for values, wanted in zip(channels, GREY_PIXEL, strict=True)
        for value in values
    )


def _make(
    canonical: bytes, target: Path, file: FileRecord, parsed: ParsedFile, allowed: frozenset[Key]
) -> None:
    with (
        pymupdf.open(stream=canonical, filetype="pdf") as original,
        pymupdf.open(stream=canonical, filetype="pdf") as doc,
    ):
        _check_positions(original, file, parsed)
        with _cut_down(doc, original, parsed, allowed) as reduced:
            flat, drawn = _draw(reduced, parsed, allowed)
            with flat:
                save_deterministic(flat, target)
            _check_flat(target, reduced, drawn)


def scoped_pdf(
    workspace: Workspace, file: FileRecord, parsed: ParsedFile, visible: Sequence[Clause], scope: Scope
) -> Path:
    """The PDF of ``file`` that ``scope`` may see; ``visible`` are its visible clauses of the package.

    The full scope gets the canonical file. Any other gets the flattened copy, made on the first request
    and kept under a name that says what is in it. Raises :class:`AccessDenied` when it cannot be made.
    """
    if scope.is_full:
        return workspace.canonical_pdf(file.id)
    allowed = visible_keys([c for c in visible if c.source_doc == file.id])
    known = {s.locus.key() for s in parsed.sentences}
    own = all(s.locus.source_doc == file.id for s in parsed.sentences)
    if parsed.file_id != file.id or not own or not allowed <= known:
        log.warning("scoped pdf of %s/%s refused: positions of another file", workspace.rfq_id, file.id)
        raise AccessDenied(REFUSED)
    key = _cache_key(file, parsed, allowed)
    target = workspace.scoped_dir / f"{key}.pdf"
    with _one_at_a_time(str(target)):
        if target.is_file():
            return target
        temporary: Path | None = None
        try:
            canonical = workspace.canonical_pdf(file.id).read_bytes()
            if hashlib.sha256(canonical).hexdigest() != file.canonical_sha256:
                raise _Mismatch("the canonical file is not the one that was parsed")
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(dir=target.parent, prefix=f"{key[:16]}-", suffix=".tmp")
            os.close(handle)
            temporary = Path(name)
            with _drawing:
                _make(canonical, temporary, file, parsed, allowed)
            os.replace(temporary, target)
        except _Mismatch as exc:
            log.warning("scoped pdf of %s/%s refused: %s", workspace.rfq_id, file.id, exc)
            raise AccessDenied(REFUSED) from exc
        except Exception as exc:  # PyMuPDF raises several unrelated types; none is a reason to send a file
            log.exception("scoped pdf of %s/%s failed", workspace.rfq_id, file.id)
            raise AccessDenied(REFUSED) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return target
