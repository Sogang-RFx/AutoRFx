"""PDF to sentences with positions (PARSE-01, PARSE-04).

Words are read with their boxes, grouped into the blocks PyMuPDF reports
(a block is a paragraph), and each paragraph's text is assembled word by word
while recording the character range of every word. A sentence's rectangles are
then the boxes of its words, one rectangle per rendered line, so a sentence
that wraps is highlighted exactly (CITE-03).

Ligatures are expanded on extraction, otherwise "specification" set with an
fi-ligature would not equal the same word in the source. Page furniture
(headers, footers, watermarks, page numbers) is taken out of the body: in a
document of three pages or more it must repeat in the margin of several pages;
in a shorter one anything wholly inside the top or bottom margin band counts.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from autorfx.models import Locus, ParsedFile, ParsedSentence, Rect, SectionInfo
from autorfx.parser.sentences import detect_language, split_sentences
from autorfx.parser.structure import Outliner, detect_mode, find_index_runs, is_toc_line

FLAGS = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_PRESERVE_LIGATURES
BAND = 0.05
JOINERS = frozenset({"und", "oder", "and", "or", "bzw.", "sowie"})
DIGITS = re.compile(r"\d+")
MIN_WORDS_ON_PAGE = 5


class ParseError(ValueError):
    """The file cannot be parsed. The message is shown to the user."""


@dataclass(frozen=True)
class _Word:
    text: str
    box: tuple[float, float, float, float]
    line: int


@dataclass(frozen=True)
class Paragraph:
    page: int
    size: tuple[float, float]
    text: str
    words: tuple[_Word, ...]
    spans: tuple[tuple[int, int], ...]
    """Character range of each word inside ``text``."""

    def rects(self, start: int, end: int) -> tuple[Rect, ...]:
        """One normalized rectangle per line touched by the characters ``start``..``end``."""
        lines: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
        for word, (a, b) in zip(self.words, self.spans, strict=True):
            if a < end and b > start:
                lines[word.line].append(word.box)
        width, height = self.size
        out = []
        for boxes in lines.values():
            x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
            x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
            clamp = lambda v: min(max(v, 0.0), 1.0)  # noqa: E731
            x, y = clamp(x0 / width), clamp(y0 / height)
            out.append(
                Rect(
                    x=x, y=y, w=min(clamp((x1 - x0) / width), 1 - x), h=min(clamp((y1 - y0) / height), 1 - y)
                )
            )
        return tuple(out)


def _assemble(words: list[_Word]) -> tuple[str, tuple[_Word, ...], tuple[tuple[int, int], ...]]:
    parts: list[str] = []
    kept: list[_Word] = []
    spans: list[tuple[int, int]] = []
    position = 0
    for word in words:
        text = word.text.replace("​", " ").strip()
        if not text:
            continue
        if kept:
            previous = parts[-1]
            broken_word = (
                word.line != kept[-1].line
                and previous.endswith("-")
                and len(previous) > 1
                and text.casefold() not in JOINERS
            )
            if not broken_word:
                parts.append(" ")
                position += 1
        spans.append((position, position + len(text)))
        parts.append(text)
        kept.append(word)
        position += len(text)
    return "".join(parts), tuple(kept), tuple(spans)


def _blocks(page: pymupdf.Page) -> list[tuple[tuple[float, float, float, float], list[_Word]]]:
    grouped: dict[int, list[tuple[int, int, _Word]]] = defaultdict(list)
    for x0, y0, x1, y1, text, block, line, number in page.get_text("words", flags=FLAGS):
        grouped[block].append((line, number, _Word(text, (x0, y0, x1, y1), line)))
    blocks = []
    for items in grouped.values():
        words = [w for _, _, w in sorted(items, key=lambda item: (item[0], item[1]))]
        box = (
            min(w.box[0] for w in words),
            min(w.box[1] for w in words),
            max(w.box[2] for w in words),
            max(w.box[3] for w in words),
        )
        blocks.append((box, words))
    return sorted(blocks, key=lambda item: (round(item[0][1] / 3), item[0][0]))


def _furniture_key(text: str) -> str:
    return DIGITS.sub("#", " ".join(text.split()).casefold())


def read_paragraphs(path: Path) -> tuple[list[Paragraph], int, list[str]]:
    """Paragraphs in reading order, the page count, and warnings."""
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:  # PyMuPDF raises several unrelated types for a damaged file
        raise ParseError("기준 PDF를 열 수 없습니다.") from exc
    with doc:
        pages: list[tuple[int, tuple[float, float], list[tuple[bool, Paragraph]]]] = []
        margin_counts: Counter[str] = Counter()
        warnings: list[str] = []
        for index, page in enumerate(doc, start=1):
            size = (page.rect.width, page.rect.height)
            items: list[tuple[bool, Paragraph]] = []
            word_count = 0
            for box, words in _blocks(page):
                text, kept, spans = _assemble(words)
                if not text:
                    continue
                word_count += len(kept)
                in_band = box[3] <= size[1] * BAND or box[1] >= size[1] * (1 - BAND)
                if in_band:
                    margin_counts[_furniture_key(text)] += 1
                items.append((in_band, Paragraph(index, size, text, kept, spans)))
            if word_count < MIN_WORDS_ON_PAGE:
                what = "이미지만 있습니다" if page.get_images() else "글자가 거의 없습니다"
                warnings.append(f"{index}쪽: {what}. 이 쪽의 내용은 분석되지 않았습니다.")
            pages.append((index, size, items))
        page_count = doc.page_count

    need = max(2, round(page_count * 0.3)) if page_count >= 3 else 1
    paragraphs: list[Paragraph] = []
    removed = 0
    for _, _, items in pages:
        for in_band, paragraph in items:
            if in_band and margin_counts[_furniture_key(paragraph.text)] >= need:
                removed += 1
            else:
                paragraphs.append(paragraph)
    if removed:
        warnings.append(f"머리글·바닥글 {removed}개를 본문에서 뺐습니다.")
    return paragraphs, page_count, warnings


def parse_pdf(file_id: str, path: Path) -> ParsedFile:
    """Sentences, sections and warnings of one canonical PDF. Deterministic (INV-7)."""
    paragraphs, page_count, warnings = read_paragraphs(path)
    if not paragraphs:
        raise ParseError("텍스트 레이어가 없는 문서입니다. 스캔본은 읽을 수 없습니다 (OCR은 범위 밖).")
    lang = detect_language(" ".join(p.text for p in paragraphs)[:30000])
    outliner = Outliner(detect_mode([p.text for p in paragraphs]))

    sections: list[SectionInfo] = []
    bodies: Counter[str] = Counter()
    title_like: dict[str, bool] = {}
    sentences: list[ParsedSentence] = []
    paragraph_no: Counter[int] = Counter()
    sentence_no: Counter[int] = Counter()
    current: str | None = None
    front_page: int | None = None
    toc_lines = 0
    index_runs = find_index_runs([p.text for p in paragraphs])

    for position, paragraph in enumerate(paragraphs):
        if position in index_runs or is_toc_line(paragraph.text):
            toc_lines += 1
            continue
        heading = outliner.feed(paragraph.text)
        label_len = 0
        role = "body"
        if heading is not None:
            current = heading.section_id
            sections.append(SectionInfo(section_id=current, title=heading.title, path=heading.path))
            title_like[current] = heading.title_like
            label_len = heading.label_len
            # The paragraph is a heading when it holds nothing but the label and the section's own title.
            own_title = bool(heading.title) and paragraph.text[label_len:].strip() == heading.title
            role = "heading" if own_title else "body"
            front_page = None
        elif current is None or (front_page is not None and front_page != paragraph.page):
            # Text before the first numbered section: kept citable, one front-matter section per page.
            front_page = paragraph.page
            count = sum(s.kind == "front_matter" for s in sections) + 1
            current = "0" if count == 1 else f"0#{count}"
            sections.append(SectionInfo(section_id=current, path=(current,), kind="front_matter"))
        spans = split_sentences(paragraph.text[label_len:], lang)
        spans = [(a + label_len, b + label_len) for a, b in spans] or [(0, len(paragraph.text))]
        spans[0] = (0, spans[0][1])
        paragraph_no[paragraph.page] += 1
        for start, end in spans:
            rects = paragraph.rects(start, end)
            if not rects:
                continue
            sentence_no[paragraph.page] += 1
            sentences.append(
                ParsedSentence(
                    locus=Locus(
                        source_doc=file_id,
                        page_number=paragraph.page,
                        section_id=current,
                        paragraph_index=paragraph_no[paragraph.page],
                        sentence_index=sentence_no[paragraph.page],
                    ),
                    text=paragraph.text[start:end],
                    rects=rects,
                    role=role,
                )
            )
            if role == "body":
                bodies[current] += 1

    if toc_lines:
        warnings.append(f"목차 {toc_lines}줄은 조항에서 뺐습니다.")
    # A title with no text of its own is only a heading when it heads child sections. A leaf whose
    # one line is its title ("2.1 Target price EUR 1.20 per piece") states something: it is a clause.
    parents = {part for s in sections for part in s.path[:-1]}
    final = tuple(
        s.model_copy(
            update={
                "heading_only": title_like.get(s.section_id, False)
                and bodies[s.section_id] == 0
                and s.section_id in parents
            }
        )
        for s in sections
    )
    return ParsedFile(
        file_id=file_id,
        lang=lang,
        page_count=page_count,
        sentences=tuple(sentences),
        sections=final,
        warnings=tuple(warnings),
    )
