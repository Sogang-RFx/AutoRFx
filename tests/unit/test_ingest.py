"""Package intake: validation, immutable originals, canonical PDFs, determinism."""

from __future__ import annotations

import stat
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from openpyxl import Workbook
from openpyxl.styles import PatternFill

from autorfx.config import Settings
from autorfx.ingest import IngestError, ingest_package, list_samples, package_digest, sha256_file
from autorfx.jobs import JobStore, load_job
from autorfx.workspace import Workspace

GERMAN = "Der Lieferant muss die Prüflehre nach Zeichnung fertigen (Maß ≥ 2,5 mm, Toleranz ± 0,15)."


def make_pdf(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 100 + 20 * i), line, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def make_docx(path: Path) -> Path:
    doc = Document()
    doc.add_heading("1. Allgemeines", level=2)
    doc.add_paragraph("1.1\t" + GERMAN)
    doc.add_paragraph("1.2\tDie Lieferung erfolgt zweimal pro Woche.")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Merkmal"
    table.rows[0].cells[1].text = "Wert"
    doc.save(str(path))
    return path


def make_xlsx(path: Path, rows: int = 3) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = "Part Data"
    sheet.append(["Part number", "Thickness [mm]", "Unit Price"])
    for i in range(rows):
        sheet.append([f"X-{i + 1}", 2.0, 0])
    form = book.create_sheet("Form")
    form["A1"] = "Material price [EUR/kg]"
    form["B1"].fill = PatternFill("solid", fgColor="FFF4CC")
    book.save(str(path))
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, AUTORFX_DATA_DIR=str(tmp_path / "data"))  # type: ignore[call-arg]


@pytest.fixture
def sources(tmp_path: Path) -> list[Path]:
    folder = tmp_path / "upload"
    folder.mkdir()
    return [
        make_xlsx(folder / "03_data.xlsx"),
        make_pdf(folder / "02_spec.pdf", ["2 Scope", "2.1 The supplier shall deliver the part."]),
        make_docx(folder / "01_anfrage.docx"),
    ]


def ingest(sources: list[Path], settings: Settings, **kwargs: object):
    return ingest_package(
        sources, name="Test package", settings=settings, is_sample=False, now_ms=1, **kwargs
    )  # type: ignore[arg-type]


def test_files_get_ids_in_natural_name_order(sources: list[Path], settings: Settings):
    """PKG-01: file ids do not depend on the order the files were given in."""
    _, manifest = ingest(sources, settings)
    assert [(f.id, f.name, f.kind) for f in manifest.files] == [
        ("d01", "01_anfrage.docx", "docx"),
        ("d02", "02_spec.pdf", "pdf"),
        ("d03", "03_data.xlsx", "xlsx"),
    ]
    _, again = ingest(list(reversed(sources)), settings)
    assert again == manifest
    assert manifest.id == f"rfq-{manifest.sha256[:12]}"
    assert manifest.sha256 == package_digest((f.name, f.sha256) for f in manifest.files)


def test_originals_are_stored_identical_and_read_only(sources: list[Path], settings: Settings):
    """INV-1, DOC-01: the stored copy equals the source, cannot be written, and the source is untouched."""
    before = {p.name: sha256_file(p) for p in sources}
    workspace, manifest = ingest(sources, settings)
    for record in manifest.files:
        stored = workspace.original_path(record.name)
        assert sha256_file(stored) == record.sha256 == before[record.name]
        assert not stored.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert {p.name: sha256_file(p) for p in sources} == before


def test_nothing_is_written_outside_the_workspace(sources: list[Path], settings: Settings, tmp_path: Path):
    """INV-1: no artifact appears next to the source files."""
    listing = sorted(p.name for p in (tmp_path / "upload").iterdir())
    workspace, _ = ingest(sources, settings)
    assert sorted(p.name for p in (tmp_path / "upload").iterdir()) == listing
    assert workspace.root.is_relative_to(settings.work_dir)
    assert not (workspace.root / "tmp").exists()


def test_pdf_canonical_is_the_original_bytes(sources: list[Path], settings: Settings):
    """DOC-02: a PDF is its own canonical PDF."""
    workspace, manifest = ingest(sources, settings)
    record = manifest.files[1]
    assert record.canonical_tool == "identity"
    assert record.canonical_sha256 == record.sha256
    assert workspace.canonical_pdf("d02").read_bytes() == workspace.original_path(record.name).read_bytes()


def test_docx_text_survives_rendering(sources: list[Path], settings: Settings):
    """DOC-02, INV-1: German characters and clause numbers are in the canonical PDF's text layer."""
    workspace, manifest = ingest(sources, settings)
    flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
    with pymupdf.open(str(workspace.canonical_pdf("d01"))) as doc:
        text = " ".join(" ".join(page.get_text("text", flags=flags).split()) for page in doc)
    assert GERMAN in text
    assert "1.2 Die Lieferung" in text and "Merkmal" in text
    assert manifest.files[0].canonical_tool.startswith("python-docx")


def test_sheet_layout_records_rows_cells_and_input_fields(sources: list[Path], settings: Settings):
    """CITE-03, COST-05: every row and cell has an anchor and a place; fill-in cells are marked."""
    workspace, manifest = ingest(sources, settings)
    layout = workspace.layout("d03")
    assert layout is not None and manifest.files[2].canonical_tool.startswith("autorfx-sheet-renderer")
    rows = {(r.sheet, r.row_index): r for r in layout.rows}
    header = rows[("Part Data", 1)]
    assert [c.anchor for c in header.cells] == ["Part Data!A1", "Part Data!B1", "Part Data!C1"]
    first = rows[("Part Data", 2)]
    assert [(c.text, c.is_input) for c in first.cells] == [("X-1", False), ("2", False), ("0", True)]
    blank = next(c for c in rows[("Form", 1)].cells if c.anchor == "Form!B1")
    assert (blank.text, blank.is_input) == ("", True)
    for row in layout.rows:
        assert 1 <= row.page_number <= manifest.files[2].page_count
        assert row.rect.w > 0 and row.rect.h > 0
        assert all(c.rect.w > 0 and c.rect.h > 0 for c in row.cells)


def test_long_sheet_spans_pages_and_keeps_every_row(tmp_path: Path, settings: Settings):
    """DOC-02: a large price list is rendered completely, not truncated."""
    source = make_xlsx(tmp_path / "big.xlsx", rows=160)
    workspace, manifest = ingest([source], settings)
    layout = workspace.layout("d01")
    assert layout is not None and manifest.files[0].page_count > 1
    data_rows = [r for r in layout.rows if r.sheet == "Part Data"]
    assert len(data_rows) == 161
    assert {r.page_number for r in data_rows} == set(range(1, max(r.page_number for r in data_rows) + 1))


def test_same_package_gives_the_same_bytes(sources: list[Path], tmp_path: Path):
    """INV-7: two independent runs produce identical canonical PDFs, layouts and manifests."""
    results = []
    for name in ("a", "b"):
        settings = Settings(_env_file=None, AUTORFX_DATA_DIR=str(tmp_path / name))  # type: ignore[call-arg]
        workspace, manifest = ingest(sources, settings)
        layout = workspace.layout_path("d03").read_bytes()
        results.append(
            (manifest, [workspace.canonical_pdf(f.id).read_bytes() for f in manifest.files], layout)
        )
    assert results[0] == results[1]


def test_ingesting_again_reuses_the_workspace(sources: list[Path], settings: Settings):
    """PKG-05: a second upload of the same package lands in the same place with the same content."""
    first_ws, first = ingest(sources, settings)
    second_ws, second = ingest(sources, settings)
    assert (first_ws.root, first) == (second_ws.root, second)
    assert len(list(settings.work_dir.iterdir())) == 1


def test_progress_is_reported(sources: list[Path], settings: Settings):
    """UI-01: the store and canonical stages end as done with counts."""
    workspace = Workspace.for_rfq(settings.work_dir, "rfq-progress0001").ensure()
    store = JobStore(workspace, started_at=1)
    store.start()
    ingest(sources, settings, reporter=store)
    state = load_job(workspace)
    assert state is not None
    done = {s.id: (s.status, s.detail) for s in state.stages[:2]}
    assert done["store"][0] == done["canonical"][0] == "done"
    assert "3" in done["store"][1] and "3" in done["canonical"][1]


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (lambda d: [], "파일이 없습니다"),
        (lambda d: [d / "missing.pdf"], "찾을 수 없습니다"),
        (lambda d: [_write(d / "notes.txt", b"hello")], "지원하지 않는 형식"),
        (lambda d: [_write(d / "fake.pdf", b"PK\x03\x04 not a pdf")], "확장자"),
        (lambda d: [_write(d / "empty.pdf", b"")], "빈 파일"),
        (lambda d: [_write(d / "_SUMMARY.md", b"guide")], "안내 파일"),
        (lambda d: [make_pdf(d / "eval" / "x.pdf", ["x"])], "평가용"),
        (lambda d: [make_pdf(d / "a" / "same.pdf", ["x"]), make_pdf(d / "b" / "same.pdf", ["y"])], "두 번"),
    ],
)
def test_bad_input_is_refused_with_a_reason(tmp_path: Path, settings: Settings, build, expected: str):
    """PKG-03, GUARD-01: each rejection names the file and says why; nothing is stored."""
    with pytest.raises(IngestError, match=expected):
        ingest(build(tmp_path), settings)
    assert not settings.work_dir.exists() or not any(settings.work_dir.iterdir())


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_limits_come_from_settings(sources: list[Path], tmp_path: Path):
    """PKG-03: file count and size limits are enforced."""
    few = Settings(_env_file=None, AUTORFX_DATA_DIR=str(tmp_path / "d"), AUTORFX_MAX_FILES=2)  # type: ignore[call-arg]
    with pytest.raises(IngestError, match="너무 많습니다"):
        ingest(sources, few)


def test_damaged_file_fails_at_conversion_and_is_reported(tmp_path: Path, settings: Settings):
    """SPEC §11: a file that passes the magic check but cannot be read stops the job with a message."""
    broken = _write(tmp_path / "broken.docx", b"PK\x03\x04" + b"\x00" * 64)
    workspace = Workspace.for_rfq(settings.work_dir, "rfq-broken000001").ensure()
    store = JobStore(workspace, started_at=1)
    store.start()
    with pytest.raises(IngestError, match="broken.docx"):
        ingest([broken], settings, reporter=store)
    stage = next(s for s in load_job(workspace).stages if s.id == "canonical")  # type: ignore[union-attr]
    assert stage.status == "error" and "broken.docx" in stage.detail


def test_samples_are_listed_from_the_development_folder(tmp_path: Path, settings: Settings):
    """PKG-04, GUARD-01: one sample per folder; guide files and the held-out set are not offered."""
    root = settings.samples_dir
    make_pdf(_write(root / "[SYN] S1 Demo" / "sub" / "02.pdf", b"x"), ["x"])
    make_pdf(_write(root / "[SYN] S1 Demo" / "01.pdf", b"x"), ["x"])
    _write(root / "[SYN] S1 Demo" / "README.md", b"guide")
    _write(root / "_SUMMARY.md", b"guide")
    _write(root / "empty folder" / "notes.txt", b"x")
    make_pdf(_write(root / "eval" / "hidden.pdf", b"x"), ["x"])
    samples = list_samples(settings)
    assert [(s.id, s.label, [p.name for p in s.files]) for s in samples] == [
        ("syn-s1-demo", "[SYN] S1 Demo", ["01.pdf", "02.pdf"])
    ]
    assert list_samples(Settings(_env_file=None, AUTORFX_DATA_DIR=str(tmp_path / "none"))) == ()  # type: ignore[call-arg]
