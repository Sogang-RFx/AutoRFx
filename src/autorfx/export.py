"""Server-side exports (RTM-02, ROUTE-04).

Workbooks are built from rows that were already filtered for the team, so an
export can never contain more than the screen (INV-8). The first row says how
the content was computed, the same way the screen does (ENGINE-03).
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font

from autorfx.models import Group, PackageManifest
from autorfx.rules import Ruleset

PRIORITY_LABEL = {"must": "Must", "should": "Should", "optional": "Optional", "info": "Info"}
MASKED_TEXT = "이 팀에는 표시되지 않음"
HEADERS = (
    "파일",
    "조항",
    "제목",
    "주관 부서",
    "참조 부서",
    "요건 강도",
    "독소조항",
    "쪽",
    "문장",
    "셀",
    "인용 검증",
)
FIXED_TIME = datetime(2000, 1, 1)


def engine_note(engines: Mapping[str, object], group: Group) -> str:
    """One line that says how the rows were computed and for which group."""
    how = "LLM 연결" if engines.get("llm_connected") else "규칙 기반 · LLM 미연결"
    who = "금액 열람 가능" if group == "cost_visible" else "금액 조항 가림"
    return f"AutoRFx RTM · {how} (분석: {engines.get('analysis', '?')}) · {who}"


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> object:
    """Text from documents and file names is written as text, never as a formula (CSV/XLSX injection)."""
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return f"'{value}"
    return value


def _append(sheet, values: Sequence[object]) -> None:  # type: ignore[no-untyped-def]
    sheet.append([_cell(value) for value in values])


def rtm_workbook(
    rows: Sequence[dict],
    *,
    manifest: PackageManifest,
    ruleset: Ruleset,
    engines: Mapping[str, object],
    group: Group,
) -> bytes:
    """The RTM as an .xlsx file. ``rows`` are contract ``RtmRow`` dicts, already filtered."""
    names = {f.id: f.name for f in manifest.files}
    labels = {d.id: d.label for d in ruleset.departments}
    book = Workbook()
    book.properties.created = FIXED_TIME
    book.properties.modified = FIXED_TIME
    sheet = book.active
    sheet.title = "RTM"
    _append(sheet, [engine_note(engines, group)])
    _append(sheet, [f"{manifest.name} · 파일 {len(manifest.files)}개 · 원본 해시 {manifest.sha256[:12]}"])
    _append(sheet, list(HEADERS))
    for cell in sheet[3]:
        cell.font = Font(bold=True)
    for row in rows:
        name = names.get(row["source_doc"], row["source_doc"])
        if row["kind"] == "masked":
            _append(sheet, [name, row["section_id"], MASKED_TEXT])
            continue
        flag = row["red_flag"]
        _append(
            sheet,
            [
                name,
                row["section_id"],
                row["title"],
                labels.get(row["primary_department"], row["primary_department"]),
                ", ".join(labels.get(d, d) for d in row["reference_departments"]),
                PRIORITY_LABEL[row["priority"]],
                flag["label"] if flag else "",
                row["page_number"],
                row["sentence_index"],
                row["anchor"] or "",
                "검증됨" if row["citation_status"] == "verified" else "미검증",
            ],
        )
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
