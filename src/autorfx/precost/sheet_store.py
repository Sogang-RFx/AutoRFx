"""Price-sheet layouts a user assigned, and the header words they suggest for the dictionary.

When the rules of ``price_sheet.yaml`` cannot read a table, a user assigns its columns on
screen. Two things are kept in the work folder (never in ``data/rules``):

- the layout: the header row's cells -> the columns' roles. A table with the same header
  is read the same way from then on, in any RFQ. It only applies to that exact header,
  so it cannot change how another layout is read.
- candidates: header words the rules did not know for the role the user gave them. They
  change nothing until someone approves them; an approved word is then used for every
  table, like a word of ``price_sheet.yaml``. The domain team moves approved words into
  ``price_sheet.yaml`` when it reviews the rules.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from pydantic import Field, ValidationError

from autorfx.models import Model
from autorfx.rules import COLUMN_ROLES, ColumnRole

log = logging.getLogger(__name__)

STORE_DIR = "_price_sheets"
MAX_LAYOUTS = 500
MAX_WORDS = 300
MAX_WORD = 80


class PriceSheetError(ValueError):
    """An assignment or a word that cannot be saved; the message is shown to the user."""


class Candidate(Model):
    role: ColumnRole
    text: str = Field(min_length=1, max_length=MAX_WORD)
    count: int = Field(ge=1)
    last_seen: str


class _Stored(Model):
    layouts: dict[str, dict[ColumnRole, int]] = Field(default_factory=dict)
    candidates: tuple[Candidate, ...] = ()
    approved: dict[ColumnRole, tuple[str, ...]] = Field(default_factory=dict)


def clean_word(text: str) -> str:
    return " ".join(text.split()).strip().lower()[:MAX_WORD]


def usable_header_word(text: str) -> bool:
    """True when a header cell is safe as a dictionary word (has a letter, not only digits/punct)."""
    cleaned = clean_word(text)
    return bool(cleaned) and bool(re.search(r"[^\W\d_]", cleaned, re.UNICODE))


def check_columns(columns: Mapping[str, int], width: int) -> dict[str, int]:
    """Role -> column of an assignment. Raises :class:`PriceSheetError` naming the problem."""
    found: dict[str, int] = {}
    used: set[int] = set()
    for role, index in columns.items():
        if role not in COLUMN_ROLES:
            raise PriceSheetError(f"알 수 없는 열 역할입니다: {role}")
        if not isinstance(index, int) or not 0 <= index < width:
            raise PriceSheetError(f"'{role}' 열 번호가 표 밖입니다.")
        if index in used:
            raise PriceSheetError("한 열에 역할을 두 개 줄 수 없습니다.")
        used.add(index)
        found[role] = index
    if "quantity" not in found:
        raise PriceSheetError("수량 열을 지정하세요.")
    if "description" not in found and "part" not in found:
        raise PriceSheetError("품명(설명) 열이나 품번 열을 지정하세요.")
    return found


class PriceSheetStore:
    """Layouts, candidates and approved words, saved as one JSON file and replaced as a whole."""

    def __init__(self, work_dir: Path) -> None:
        self.path = work_dir / STORE_DIR / "price_sheets.json"
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> _Stored:
        if not self.path.is_file():
            return _Stored()
        try:
            return _Stored.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            log.warning("가격표 열 지정 기록을 읽지 못했습니다: %s", exc)
            return _Stored()

    def _write(self, data: _Stored) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(data.model_dump(mode="json"), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(tmp, self.path)
        self._data = data

    @property
    def layouts(self) -> dict[str, dict[str, int]]:
        return {k: dict(v) for k, v in self._data.layouts.items()}

    @property
    def approved(self) -> dict[str, tuple[str, ...]]:
        return dict(self._data.approved)

    @property
    def candidates(self) -> tuple[Candidate, ...]:
        return self._data.candidates

    def assign(
        self, signature: str, columns: Mapping[str, int], words: Mapping[str, str], today: date
    ) -> None:
        """Remember a layout; ``words`` (role -> header text) the rules did not know become candidates."""
        with self._lock:
            layouts = {**self._data.layouts, signature: dict(columns)}
            if len(layouts) > MAX_LAYOUTS:
                layouts.pop(next(iter(layouts)))
            approved = {role: set(ws) for role, ws in self._data.approved.items()}
            kept = {(c.role, c.text): c for c in self._data.candidates}
            for role, raw in words.items():
                text = clean_word(raw)
                if (
                    not text
                    or text.startswith("re:")
                    or not usable_header_word(text)
                    or text in approved.get(role, set())
                ):
                    continue
                old = kept.get((role, text))
                kept[(role, text)] = Candidate(
                    role=role, text=text, count=(old.count + 1) if old else 1, last_seen=today.isoformat()
                )
            candidates = sorted(kept.values(), key=lambda c: c.last_seen, reverse=True)[:MAX_WORDS]
            self._write(_Stored(layouts=layouts, candidates=tuple(candidates), approved=self._data.approved))

    def decide(self, role: str, text: str, action: str) -> None:
        """``approve`` or ``reject`` a candidate, or ``remove`` an approved word."""
        if role not in COLUMN_ROLES:
            raise PriceSheetError(f"알 수 없는 열 역할입니다: {role}")
        text = clean_word(text)
        with self._lock:
            candidates = tuple(c for c in self._data.candidates if (c.role, c.text) != (role, text))
            approved = {r: list(ws) for r, ws in self._data.approved.items()}
            if action == "approve":
                if len(candidates) == len(self._data.candidates):
                    raise PriceSheetError("승인할 후보가 없습니다. 화면을 새로 고쳐 주세요.")
                if not usable_header_word(text):
                    raise PriceSheetError("글자가 없는 머리글은 승인할 수 없습니다.")
                if text not in approved.setdefault(role, []):
                    approved[role].append(text)
            elif action == "reject":
                if len(candidates) == len(self._data.candidates):
                    raise PriceSheetError("거절할 후보가 없습니다. 화면을 새로 고쳐 주세요.")
            elif action == "remove":
                if text not in approved.get(role, []):
                    raise PriceSheetError("승인된 단어가 아닙니다.")
                approved[role].remove(text)
                candidates = self._data.candidates
            else:
                raise PriceSheetError(f"알 수 없는 동작입니다: {action}")
            self._write(
                _Stored(
                    layouts=self._data.layouts,
                    candidates=candidates,
                    approved={r: tuple(ws) for r, ws in approved.items() if ws},
                )
            )


__all__ = [
    "Candidate",
    "PriceSheetError",
    "PriceSheetStore",
    "check_columns",
    "clean_word",
    "usable_header_word",
]
