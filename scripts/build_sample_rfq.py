"""Render the synthetic RFQ manuscript into PDFs and demo fixtures (TASKS S002/S003).

Usage (from web/):  pnpm fixtures

The clause structure comes from the manuscript; sentence rectangles are recorded
while this script lays the text out, so they match the produced PDF exactly.
Nothing here parses a PDF back into structure (that is the parser's job, T102+).
pypdf is used only to cross-check that the rendered text is where we think it
is, and that the masked PDF does not contain cost-clause text.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "web/src/fixtures/sample"
OUT_DIR = ROOT / "web/src/fixtures/generated"

PAGE_W, PAGE_H = A4
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 72.0, 72.0, 84.0, 72.0
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R

BODY = ("Times-Roman", 10.5, 15.5)
COVER_TITLE = ("Times-Bold", 17.0, 23.0)
TABLE_FONT = ("Helvetica", 8.5)
TABLE_BOLD = ("Helvetica-Bold", 8.5)
TABLE_ROW_H = 17.0
INK = HexColor("#1C1B19")
MUTED = HexColor("#6E6A62")
RULE = HexColor("#CFCAC0")
MASK_FILL = HexColor("#E4E0D6")


# ── Layout model ──────────────────────────────────────────────────────────────
@dataclass
class SentenceBox:
    key: str  # "<clause>#<n>" or "0#<n>"
    section_id: str
    text: str
    page: int = 0
    paragraph_index: int = 0
    sentence_index: int = 0
    rects: list[tuple[float, float, float, float]] = field(default_factory=list)  # pt, top-left


@dataclass
class Op:
    """A drawing operation on one page. `section_id` lets the masked pass skip it."""

    kind: str  # text | rule
    section_id: str | None
    args: tuple


@dataclass
class Page:
    number: int
    ops: list[Op] = field(default_factory=list)
    paragraphs: int = 0
    sentences: int = 0


class Layout:
    def __init__(self) -> None:
        self.pages: list[Page] = []
        self.sentences: list[SentenceBox] = []
        self.clause_pages: dict[str, int] = {}
        self._new_page()

    def _new_page(self) -> None:
        self.pages.append(Page(number=len(self.pages) + 1))
        self.y = MARGIN_T

    @property
    def page(self) -> Page:
        return self.pages[-1]

    def _ensure(self, height: float) -> None:
        if self.y + height > PAGE_H - MARGIN_B:
            self._new_page()

    def heading(self, text: str, size: float, before: float, after: float, keep: float) -> None:
        if self.y > MARGIN_T:
            self.y += before
        self._ensure(size + after + keep)
        baseline = self.y + size
        self.page.ops.append(Op("text", None, ("Helvetica-Bold", size, MARGIN_L, baseline, text, INK)))
        self.y = baseline + after

    def paragraph(self, section_id: str, keys_texts: list[tuple[str, str]], font=BODY, after: float = 7.0) -> None:
        name, size, leading = font
        space = stringWidth(" ", name, size)
        words: list[tuple[str, int]] = []
        boxes: list[SentenceBox] = []
        for i, (key, text) in enumerate(keys_texts):
            boxes.append(SentenceBox(key=key, section_id=section_id, text=text))
            words.extend((w, i) for w in text.split(" ") if w)

        lines: list[list[tuple[str, int, float, float]]] = []  # word, sentence, x, width
        line: list[tuple[str, int, float, float]] = []
        x = 0.0
        for w, si in words:
            width = stringWidth(w, name, size)
            if line and x + width > CONTENT_W:
                lines.append(line)
                line, x = [], 0.0
            line.append((w, si, x, width))
            x += width + space
        if line:
            lines.append(line)

        self._ensure(len(lines) * leading)
        page = self.page
        page.paragraphs += 1
        for box in boxes:
            page.sentences += 1
            box.page, box.paragraph_index, box.sentence_index = page.number, page.paragraphs, page.sentences

        for li, ln in enumerate(lines):
            top = self.y + li * leading
            baseline = top + (leading + size * 0.7) / 2
            for w, _si, wx, _ww in ln:
                page.ops.append(Op("text", section_id, (name, size, MARGIN_L + wx, baseline, w, INK)))
            for si in sorted({s for _, s, _, _ in ln}):
                seg = [(wx, ww) for _, s, wx, ww in ln if s == si]
                x0 = seg[0][0]
                x1 = seg[-1][0] + seg[-1][1]
                boxes[si].rects.append((MARGIN_L + x0 - 1.5, top + 1.0, x1 - x0 + 3.0, leading - 2.0))
        self.y += len(lines) * leading + after
        self.sentences.extend(boxes)

    def table(self, section_id: str, keys_rows: list[tuple[str, list[str]]], after: float = 8.0) -> None:
        cols = len(keys_rows[0][1])
        first_w = 110.0
        rest_w = (CONTENT_W - first_w) / (cols - 1)
        widths = [first_w] + [rest_w] * (cols - 1)
        height = TABLE_ROW_H * len(keys_rows)
        self._ensure(height)
        page = self.page
        page.paragraphs += 1
        top0 = self.y
        for ri, (key, cells) in enumerate(keys_rows):
            top = top0 + ri * TABLE_ROW_H
            page.sentences += 1
            self.sentences.append(
                SentenceBox(
                    key=key,
                    section_id=section_id,
                    text=" | ".join(cells),
                    page=page.number,
                    paragraph_index=page.paragraphs,
                    sentence_index=page.sentences,
                    rects=[(MARGIN_L, top, CONTENT_W, TABLE_ROW_H)],
                )
            )
            font = TABLE_BOLD if ri == 0 else TABLE_FONT
            cx = MARGIN_L
            for ci, cell in enumerate(cells):
                tx = cx + 5 if ci == 0 else cx + widths[ci] - 5 - stringWidth(cell, *font)
                page.ops.append(Op("text", section_id, (font[0], font[1], tx, top + 11.5, cell, INK)))
                cx += widths[ci]
            page.ops.append(Op("rule", None, (MARGIN_L, top, MARGIN_L + CONTENT_W, top)))
        page.ops.append(Op("rule", None, (MARGIN_L, top0 + height, MARGIN_L + CONTENT_W, top0 + height)))
        self.y += height + after


# ── Build ─────────────────────────────────────────────────────────────────────
def layout_manuscript(ms: dict) -> Layout:
    lay = Layout()
    cover = ms["cover"]
    lay.paragraph("0", [("0#1", cover[0])], font=COVER_TITLE, after=6)
    lay.paragraph("0", [(f"0#{i + 2}", t) for i, t in enumerate(cover[1:])], after=18)
    for section in ms["sections"]:
        lay.heading(f"{section['id']}.  {section['title']}", 12.5, before=10, after=8, keep=40)
        for clause in section["clauses"]:
            cid = clause["id"]
            lay.heading(f"{cid}  {clause['title']}", 10.5, before=4, after=6, keep=30)
            n = 0
            for block in clause["blocks"]:
                if "p" in block:
                    items = []
                    for text in block["p"]:
                        n += 1
                        items.append((f"{cid}#{n}", text))
                    lay.paragraph(cid, items)
                else:
                    rows = []
                    for cells in block["table"]:
                        n += 1
                        rows.append((f"{cid}#{n}", [str(c) for c in cells]))
                    lay.table(cid, rows)
            first = next(s for s in lay.sentences if s.key == f"{cid}#1")
            lay.clause_pages[cid] = first.page
    return lay


def render(lay: Layout, header: str, masked: set[str] | None) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=1, pageCompression=0)
    c.setTitle("NV-RFQ-2026-0412 (synthetic sample)" + (" - cost clauses hidden" if masked else ""))
    c.setAuthor("AutoRFx demo generator")
    c.setCreator("scripts/build_sample_rfq.py")
    total = len(lay.pages)
    hidden_boxes = [s for s in lay.sentences if masked and s.section_id in masked]
    for page in lay.pages:
        c.setFont("Helvetica", 7.5)
        c.setFillColor(MUTED)
        head = header + ("  |  COST CLAUSES HIDDEN" if masked else "")
        c.drawString(MARGIN_L, PAGE_H - 46, head)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(MARGIN_L, PAGE_H - 54, PAGE_W - MARGIN_R, PAGE_H - 54)
        c.drawCentredString(PAGE_W / 2, 40, f"Page {page.number} of {total}")
        for op in page.ops:
            hidden = masked is not None and op.section_id in masked
            if op.kind == "text" and not hidden:
                name, size, x, baseline, text, color = op.args
                c.setFont(name, size)
                c.setFillColor(color)
                c.drawString(x, PAGE_H - baseline, text)
            elif op.kind == "rule":
                x0, y0, x1, y1 = op.args
                c.setStrokeColor(RULE)
                c.line(x0, PAGE_H - y0, x1, PAGE_H - y1)
        c.setFillColor(MASK_FILL)
        for box in hidden_boxes:
            if box.page == page.number:
                for x, y, w, h in box.rects:
                    c.rect(x, PAGE_H - y - h, w, h, stroke=0, fill=1)
        c.showPage()
    c.save()
    return buf.getvalue()


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def cross_check(original: bytes, masked_pdf: bytes, lay: Layout, cost: set[str]) -> None:
    orig_pages = [norm(p.extract_text() or "") for p in PdfReader(io.BytesIO(original)).pages]
    masked_all = "".join(norm(p.extract_text() or "") for p in PdfReader(io.BytesIO(masked_pdf)).pages)
    for s in lay.sentences:
        probe = norm(s.text.replace(" | ", ""))
        if probe not in orig_pages[s.page - 1]:
            sys.exit(f"cross-check failed: {s.key} not found on page {s.page}")
        if s.section_id in cost and probe in masked_all:
            sys.exit(f"masking failed: {s.key} text is present in the masked PDF")
    for leak in ("14.80", "185,000", "42,000", "400,000"):
        if leak in masked_all:
            sys.exit(f"masking failed: '{leak}' present in masked PDF")


def to_locus(doc_id: str, s: SentenceBox) -> dict:
    return {
        "source_doc": doc_id,
        "page_number": s.page,
        "section_id": s.section_id,
        "paragraph_index": s.paragraph_index,
        "sentence_index": s.sentence_index,
    }


def to_rect(r: tuple[float, float, float, float]) -> dict:
    x, y, w, h = r
    return {"x": round(x / PAGE_W, 5), "y": round(y / PAGE_H, 5), "w": round(w / PAGE_W, 5), "h": round(h / PAGE_H, 5)}


def write_json(name: str, data: object) -> None:
    (OUT_DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ms = yaml.safe_load((SAMPLE_DIR / "manuscript.yaml").read_text(encoding="utf-8"))
    doc_id = ms["doc"]["id"]
    cost = {c["id"] for s in ms["sections"] for c in s["clauses"] if c.get("cost")}

    lay = layout_manuscript(ms)
    original = render(lay, ms["doc"]["header"], masked=None)
    masked_pdf = render(lay, ms["doc"]["header"], masked=cost)
    cross_check(original, masked_pdf, lay, cost)

    (SAMPLE_DIR / "sample-rfq.pdf").write_bytes(original)
    (SAMPLE_DIR / "sample-rfq.masked.pdf").write_bytes(masked_pdf)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    by_key = {s.key: s for s in lay.sentences}

    def verified(kind: str, text: str, departments: list[str], keys: list[str], priority=None, reason=None) -> dict:
        return {
            "finding": {
                "kind": kind,
                "departments": departments,
                "text": text,
                "priority": priority,
                "red_flag_reason": reason,
                "citations": [to_locus(doc_id, by_key[k]) for k in keys],
            },
            "citation_status": "verified",
            "failed_citations": [],
        }

    write_json(
        "document.json",
        {
            "id": doc_id,
            "name": ms["doc"]["display_name"],
            "file_name": ms["doc"]["file_name"],
            "page_count": len(lay.pages),
            "sha256": hashlib.sha256(original).hexdigest(),
            "sha256_masked": hashlib.sha256(masked_pdf).hexdigest(),
        },
    )

    write_json(
        "sentences.json",
        {
            "pages": [
                {
                    "page_number": p.number,
                    "sentences": [
                        {"locus": to_locus(doc_id, s), "text": s.text, "rects": [to_rect(r) for r in s.rects]}
                        for s in lay.sentences
                        if s.page == p.number
                    ],
                }
                for p in lay.pages
            ]
        },
    )

    out_clauses = []
    for section in ms["sections"]:
        for clause in section["clauses"]:
            cid, a = clause["id"], clause["analysis"]
            keys = [k for k in by_key if k.startswith(f"{cid}#")]
            red = a.get("red_flag")
            summary = verified(
                "summary",
                a["summary"],
                a["departments"],
                [f"{cid}#{n}" for n in a["cite"]],
                priority=a["priority"],
                reason=red["reason"] if red else None,
            )
            if "failed_cite" in a:
                fc = a["failed_cite"]
                summary["citation_status"] = "unverified"
                summary["failed_citations"] = [
                    {
                        "source_doc": doc_id,
                        "page_number": lay.clause_pages[cid] + fc["page_offset"],
                        "section_id": cid,
                        "paragraph_index": 1,
                        "sentence_index": fc["sentence_index"],
                    }
                ]
            out_clauses.append(
                {
                    "kind": "clause",
                    "section_id": cid,
                    "section_path": [section["id"], cid],
                    "title": clause["title"],
                    "page_number": lay.clause_pages[cid],
                    "departments": a["departments"],
                    "priority": a["priority"],
                    "red_flag": {"type": red["type"], "reason": red["reason"]} if red else None,
                    "excerpt": [{"locus": to_locus(doc_id, by_key[k]), "text": by_key[k].text} for k in keys],
                    "summary": summary,
                    "translation_ko": a.get("translation"),
                }
            )
    write_json(
        "clauses.json",
        {"cost_sensitive": sorted(cost), "scenario_unverified": ms["scenario_unverified"], "clauses": out_clauses},
    )

    write_json(
        "chat.json",
        {
            "items": [
                {
                    "question": q["question"],
                    "keywords": q["keywords"],
                    "cost_sensitive": any(k.split("#")[0] in cost for k in q["cite"]),
                    "answer": verified("answer", q["answer"], q["departments"], q["cite"]),
                }
                for q in ms["chat"]
            ]
        },
    )

    ci = ms["cost_inputs"]
    write_json(
        "cost-inputs.json",
        {
            "item": ci["item"],
            "candidates": [
                {
                    "field": c["field"],
                    "label": c["label"],
                    "value": c["value"],
                    "note": c["note"],
                    "evidence": verified("cost_input", c["note"], ["costing"], c["cite"]) if c["cite"] else None,
                }
                for c in ci["candidates"]
            ],
        },
    )

    write_json("meta.json", ms["meta"])
    print(f"pages={len(lay.pages)} sentences={len(lay.sentences)} clauses={len(out_clauses)} cost={sorted(cost)}")


if __name__ == "__main__":
    main()
