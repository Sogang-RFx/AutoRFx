"""Read item rows from the ruled Form 3.02 price sheet, including page continuations."""
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import re

import pymupdf


def _rows(page):
    # The form draws each cell edge as a narrow vertical rectangle. A one-row
    # continuation can be missed by table detectors that require multiple rows.
    spans = defaultdict(set)
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.width <= 1 and rect.height >= 8:
            spans[round(rect.y0, 1), round(rect.y1, 1)].add(round(rect.x0, 1))
    words = page.get_text("words")
    for (top, bottom), boundaries in sorted(spans.items()):
        edges = sorted(boundaries)
        cells = [
            " ".join(w[4] for w in words if left <= (w[0]+w[2])/2 < right and top <= (w[1]+w[3])/2 < bottom)
            for left, right in zip(edges, edges[1:])
        ]
        if cells:
            yield cells


@lru_cache(maxsize=16)
def _generic_items(doc):
    """Recover numbered requirement rows from PDFs whose table grid was lost."""
    items = []
    for page_number, page in enumerate(doc, 1):
        lines = [line.strip() for line in page.get_text().splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if not re.fullmatch(r"\d{1,3}", line):
                continue
            number = int(line)
            if number < 1 or number > 999:
                continue
            end = next((j for j in range(index + 1, len(lines)) if re.fullmatch(r"\d{1,3}", lines[j])), len(lines))
            block = lines[index + 1:end]
            quantity_index = next((j for j, value in enumerate(block) if re.fullmatch(r"\d{1,6}(?:[,.]\d+)?", value)), None)
            if quantity_index is None or not block[:quantity_index]:
                continue
            quantity = int(float(block[quantity_index].replace(",", ".")))
            location = block[quantity_index + 1] if quantity_index + 1 < len(block) else ""
            if not location or len(location) > 80:
                location = None
            name = block[0]
            specification = " ".join(block[1:quantity_index])
            if name.lower() in {"item", "item #", "section", "page"}:
                continue
            items.append({"category": "RFQ 품목", "item": str(number), "part": "",
                          "updated_part": "", "description": f"{name} {specification}".strip(),
                          "quantity": quantity, "page": page_number, "location": location})
    # A row can appear in a page header or in a repeated annex. Keep the first occurrence.
    unique = {}
    for item in items:
        unique.setdefault((item["item"], item["description"], item["quantity"]), item)
    return list(unique.values())


def _annex_items(doc):
    """Read common UNDP-style Annex 2 item lists without relying on PDF grid lines."""
    items = []
    for page_number, page in enumerate(doc, 1):
        text = page.get_text()
        if not any(marker.lower() in text.lower() for marker in ("TABLE 1", "Total Price of Goods", "Salyan", "Masalli")):
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cursor = 0
        starts = [i for i in range(len(lines) - 2)
                  if re.fullmatch(r"\d{1,3}", lines[i])
                  and not re.fullmatch(r"\d{1,6}", lines[i + 1])
                  and not re.fullmatch(r"\d{1,6}", lines[i + 2])]
        locations = [i for i, line in enumerate(lines) if line.lower() in {"salyan", "masalli"}]
        for location_index in locations:
            candidates = [i for i in starts if cursor <= i < location_index]
            if not candidates:
                continue
            start = candidates[-1]
            quantity_candidates = [i for i in range(start + 1, location_index) if re.fullmatch(r"\d{1,6}", lines[i])]
            if not quantity_candidates:
                continue
            quantity_index = quantity_candidates[-1]
            block = lines[start + 1:quantity_index]
            if len(block) < 2:
                continue
            item_name = block[0]
            specification = " ".join(block[1:])
            items.append({"category": "RFQ 품목", "item": lines[start], "part": "", "updated_part": "",
                          "description": f"{item_name} {specification}".strip(),
                          "quantity": int(lines[quantity_index]), "page": page_number,
                          "location": lines[location_index]})
            cursor = location_index + 1
    unique = {}
    for item in items:
        unique.setdefault((item["item"], item["description"], item["location"]), item)
    return list(unique.values())


def extract_price_sheet(path: Path, digest: str):
    # The manifest hash invalidates cached extraction if the source changes.
    with pymupdf.open(path) as doc:
        pages = []
        for number, page in enumerate(doc):
            title = " ".join(page.get_text().split())[:100].upper()
            if title.startswith("FORM 3.02 PRICE SHEET"):
                pages.append((number, "REVISED" in title))
        revised = any(flag for _, flag in pages)
        category = ""
        items = []
        tax = None
        for number, flag in pages:
            if flag != revised:
                continue
            page = doc[number]
            match = re.search(r"([\d.]+)%\s+Sales Tax", page.get_text(), re.I)
            if match:
                tax = match[1]
            for cells in _rows(page):
                heading = re.fullmatch(r"Category\s+\d+\s*[–—-]\s*(.+)", cells[0], re.I)
                if heading and "subtotal" not in cells[0].lower() and len(cells) == 1:
                    category = cells[0].rstrip(":")
                if len(cells) not in (6, 7) or not cells[0].isdigit() or not category:
                    continue
                item, part = cells[:2]
                desc, _, qty, _ = cells[-4:]
                if not part or not qty.replace(",", "").isdigit():
                    continue
                items.append(dict(category=category, item=item, part=part.replace(" ", ""),
                                  updated_part=cells[2] if len(cells) == 7 else "",
                                  description=desc, quantity=int(qty.replace(",", "")), page=number+1))
        if items:
            return dict(source=path.name, revised=revised, tax=tax, items=items)
        generic = _annex_items(doc) or _generic_items(doc)
        return dict(source=path.name, revised=False, tax=tax, items=generic)
