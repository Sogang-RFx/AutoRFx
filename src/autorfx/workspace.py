"""Artifact layout of one RFQ (SPEC §3).

Everything the pipeline produces for an RFQ lives in one folder, and every
module reads and writes it through this class. Nothing is ever written next to
a source file (INV-1)::

    data/work/<rfq_id>/
      manifest.json                    PackageManifest
      original/<file name>             byte-identical copies of the received files, read-only
      canonical/<file_id>.pdf          canonical PDF; every page number refers to it
      canonical/<file_id>.masked.pdf   cost-sensitive text removed, for restricted teams
      layout/<file_id>.json            FileLayout (DOC, DOCX, XLSX; not for PDF originals)
      parsed/<file_id>.json            ParsedFile
      clauses.json                     Clause[]
      analysis.json                    ClauseAnalysis[]
      mentions.json                    EntityMention[]
      consistency.json                 consistency issues and what was checked
      cost_inputs.json                 cost input candidates
      job.json                         JobState
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from autorfx.guard import GuardError, assert_inside
from autorfx.models import (
    Clause,
    ClauseAnalysis,
    EntityMention,
    FileLayout,
    PackageManifest,
    ParsedFile,
)

T = TypeVar("T", bound=BaseModel)

RFQ_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{2,63}$")
FILE_ID = re.compile(r"^d\d{2,3}$")


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.match(value):
        raise GuardError(f"잘못된 {what}: {value!r}")
    return value


class Workspace:
    """Paths and typed JSON access for one RFQ's artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @classmethod
    def for_rfq(cls, work_dir: Path, rfq_id: str) -> Workspace:
        """Workspace of ``rfq_id``. The id is validated so it cannot escape ``work_dir``."""
        _check(RFQ_ID, rfq_id, "RFQ 식별자")
        return cls(assert_inside(Path(work_dir) / rfq_id, work_dir))

    @property
    def rfq_id(self) -> str:
        return self.root.name

    def ensure(self) -> Workspace:
        for folder in (self.original_dir, self.canonical_dir, self.layout_dir, self.parsed_dir):
            folder.mkdir(parents=True, exist_ok=True)
        return self

    def exists(self) -> bool:
        return self.manifest_path.is_file()

    # ── paths ───────────────────────────────────────────────────────────────
    @property
    def original_dir(self) -> Path:
        return self.root / "original"

    @property
    def canonical_dir(self) -> Path:
        return self.root / "canonical"

    @property
    def layout_dir(self) -> Path:
        return self.root / "layout"

    @property
    def parsed_dir(self) -> Path:
        return self.root / "parsed"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def clauses_path(self) -> Path:
        return self.root / "clauses.json"

    @property
    def analysis_path(self) -> Path:
        return self.root / "analysis.json"

    @property
    def mentions_path(self) -> Path:
        return self.root / "mentions.json"

    @property
    def consistency_path(self) -> Path:
        return self.root / "consistency.json"

    @property
    def cost_inputs_path(self) -> Path:
        return self.root / "cost_inputs.json"

    @property
    def run_path(self) -> Path:
        """How the stored results were computed: engines, ruleset digest, finish time."""
        return self.root / "run.json"

    @property
    def job_path(self) -> Path:
        return self.root / "job.json"

    def original_path(self, name: str) -> Path:
        return assert_inside(self.original_dir / Path(name).name, self.original_dir)

    def canonical_pdf(self, file_id: str) -> Path:
        return self.canonical_dir / f"{_check(FILE_ID, file_id, '파일 식별자')}.pdf"

    def masked_pdf(self, file_id: str) -> Path:
        return self.canonical_dir / f"{_check(FILE_ID, file_id, '파일 식별자')}.masked.pdf"

    def layout_path(self, file_id: str) -> Path:
        return self.layout_dir / f"{_check(FILE_ID, file_id, '파일 식별자')}.json"

    def parsed_path(self, file_id: str) -> Path:
        return self.parsed_dir / f"{_check(FILE_ID, file_id, '파일 식별자')}.json"

    # ── JSON ────────────────────────────────────────────────────────────────
    def write_text(self, path: Path, text: str) -> None:
        """Write atomically, so a reader never sees a half-written file."""
        target = assert_inside(path, self.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)

    def write_json(self, path: Path, data: object) -> None:
        self.write_text(path, json.dumps(data, ensure_ascii=False, indent=1))

    def read_json(self, path: Path) -> object:
        return json.loads(assert_inside(path, self.root).read_text(encoding="utf-8"))

    def write_model(self, path: Path, model: BaseModel) -> None:
        self.write_json(path, model.model_dump(mode="json"))

    def write_models(self, path: Path, models: Sequence[BaseModel]) -> None:
        self.write_json(path, [m.model_dump(mode="json") for m in models])

    def read_model(self, path: Path, cls: type[T]) -> T:
        return cls.model_validate(self.read_json(path))

    def read_models(self, path: Path, cls: type[T]) -> tuple[T, ...]:
        data = self.read_json(path)
        if not isinstance(data, list):
            raise ValueError(f"{path.name}: 목록이어야 합니다")
        return tuple(cls.model_validate(item) for item in data)

    # ── typed helpers ───────────────────────────────────────────────────────
    def save_manifest(self, manifest: PackageManifest) -> None:
        self.write_model(self.manifest_path, manifest)

    def manifest(self) -> PackageManifest:
        return self.read_model(self.manifest_path, PackageManifest)

    def save_layout(self, layout: FileLayout) -> None:
        self.write_model(self.layout_path(layout.file_id), layout)

    def layout(self, file_id: str) -> FileLayout | None:
        path = self.layout_path(file_id)
        return self.read_model(path, FileLayout) if path.is_file() else None

    def save_parsed(self, parsed: ParsedFile) -> None:
        self.write_model(self.parsed_path(parsed.file_id), parsed)

    def parsed(self, file_id: str) -> ParsedFile:
        return self.read_model(self.parsed_path(file_id), ParsedFile)

    def save_clauses(self, clauses: Sequence[Clause]) -> None:
        self.write_models(self.clauses_path, clauses)

    def clauses(self) -> tuple[Clause, ...]:
        return self.read_models(self.clauses_path, Clause)

    def save_analysis(self, analysis: Sequence[ClauseAnalysis]) -> None:
        self.write_models(self.analysis_path, analysis)

    def analysis(self) -> tuple[ClauseAnalysis, ...]:
        return self.read_models(self.analysis_path, ClauseAnalysis)

    def save_mentions(self, mentions: Sequence[EntityMention]) -> None:
        self.write_models(self.mentions_path, mentions)

    def mentions(self) -> tuple[EntityMention, ...]:
        return self.read_models(self.mentions_path, EntityMention)
