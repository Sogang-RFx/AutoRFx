"""The flattened source PDF of one scope (docs/auth-rbac.md §5; INV-8, RBAC-02)."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

import pymupdf
import pytest

from auth_helpers import cost_restricted_scope, scope_for, settings_in, visible_of
from autorfx import access
from autorfx.analysis import RuleOnlyProvider
from autorfx.models import Clause
from autorfx.pipeline import RfqData, load_rfq, run_pipeline
from autorfx.rules import Ruleset
from factories import make_package, pdf_from_html
from isolation_package import OWNERS, RED, make_isolation_package
from scoped_pdf_helpers import assert_flat, box_of, has_ink, is_grey, is_red, pixels, scoped_pdf, words_in


@dataclass(frozen=True)
class Package:
    data: RfqData
    ruleset: Ruleset

    def visible(self, name: str) -> tuple[Clause, ...]:
        return visible_of(self.data.clauses, self.data.analyses, self.scope(name), self.ruleset)

    def scope(self, name: str):  # type: ignore[no-untyped-def]
        return cost_restricted_scope(self.ruleset) if name == "no_cost" else scope_for(name, self.ruleset)

    def file(self, file_id: str):  # type: ignore[no-untyped-def]
        return next(f for f in self.data.manifest.files if f.id == file_id)

    def make(self, name: str, file_id: str = "d01", visible: tuple[Clause, ...] | None = None) -> Path:
        chosen = self.visible(name) if visible is None else visible
        data = self.data
        return scoped_pdf(data.workspace, self.file(file_id), data.parsed[file_id], chosen, self.scope(name))

    @property
    def folder(self) -> Path:
        return self.data.workspace.root / "scoped"

    def left_over(self) -> list[Path]:
        return [p for p in self.folder.rglob("*") if p.is_file()] if self.folder.exists() else []

    def canonical(self, file_id: str = "d01") -> Path:
        return self.data.workspace.canonical_pdf(file_id)


def package_of(folder: Path, sources: list[Path], ruleset: Ruleset) -> Package:
    workspace = run_pipeline(
        sources,
        name="scoped",
        settings=settings_in(folder),
        ruleset=ruleset,
        provider=RuleOnlyProvider(),
        is_sample=False,
        now_ms=1,
    )
    return Package(load_rfq(workspace, ruleset), ruleset)


@pytest.fixture
def simple(tmp_path: Path, ruleset: Ruleset) -> Package:
    return package_of(tmp_path, make_package(tmp_path / "in"), ruleset)


@pytest.fixture
def tricky(tmp_path: Path, ruleset: Ruleset) -> Package:
    return package_of(tmp_path, make_isolation_package(tmp_path / "in"), ruleset)


def single(folder: Path, build, ruleset: Ruleset) -> Package:  # type: ignore[no-untyped-def]
    """A one-file package whose PDF ``build(doc)`` completes."""
    path = pdf_from_html(
        folder / "in" / "01_a.pdf",
        "<h2>1 Part</h2>"
        "<p>1.1 Packaging in small load carrier and container is agreed.</p>"
        "<p>1.2 Liability and governing law are set out by counsel.</p>",
    )
    with pymupdf.open(str(path)) as doc:
        build(doc)
        doc.save(str(folder / "in" / "01_b.pdf"))
    path.unlink()
    return package_of(folder, [folder / "in" / "01_b.pdf"], ruleset)


# ── what is served ──────────────────────────────────────────────────────────
def test_the_full_scope_gets_the_canonical_file_itself(simple: Package):
    """CITE-03: nothing is made for an account that sees everything."""
    for name in ("admin", "sales"):
        assert simple.make(name) == simple.canonical()
    assert simple.left_over() == []


def test_seeing_every_clause_is_not_the_full_scope(simple: Package):
    """INV-8: the canonical file also holds what no clause holds, so it goes to the full scope only."""
    everything = tuple(simple.data.clauses)
    path = simple.make("logistics", visible=everything)
    assert path != simple.canonical() and path.read_bytes() != simple.canonical().read_bytes()
    assert_flat(path.read_bytes())


@pytest.mark.parametrize("name", ["logistics", "legal", "quality", "costing", "no_cost"])
def test_a_scoped_pdf_is_flat_and_shows_the_visible_sentences_only(simple: Package, name: str):
    """auth-rbac §5.3-6."""
    path = simple.make(name)
    assert path.parent == simple.folder and path.suffix == ".pdf"
    assert_flat(path.read_bytes())
    allowed = {s.locus.key() for c in simple.visible(name) for s in c.sentences}
    with pymupdf.open(str(path)) as doc, pymupdf.open(str(simple.canonical())) as original:
        assert [p.rect for p in doc] == [p.rect for p in original]
        page = doc[0]
        for sentence in simple.data.parsed["d01"].sentences:
            for rect in (r.model_dump() for r in sentence.rects):
                if sentence.locus.key() in allowed:
                    assert has_ink(page, rect), sentence.text
                else:
                    found = pixels(page, box_of(page, rect, trim=1.5))
                    assert all(is_grey(p) for p in found), sentence.text


def test_a_heading_of_no_clause_is_grey(simple: Package):
    """auth-rbac §5.1: a heading above sub-clauses is in no clause, so it is hidden from every department,
    also from one that sees all its sub-clauses."""
    heading = next(s for s in simple.data.parsed["d02"].sentences if s.role == "heading")
    assert heading.text == "1 Logistics"
    path = simple.make("logistics", "d02")
    with pymupdf.open(str(path)) as doc:
        for rect in heading.rects:
            assert all(is_grey(p) for p in pixels(doc[0], box_of(doc[0], rect.model_dump(), trim=1.5)))


def test_a_file_without_a_visible_clause_is_an_empty_grey_page(tricky: Package):
    """auth-rbac §5: it is served as grey pages: the number of pages is in the document info anyway."""
    path = tricky.make("quality", "d04")
    assert_flat(path.read_bytes())
    with pymupdf.open(str(path)) as doc:
        assert all(is_grey(p) for page in doc for p in pixels(page))


def test_the_canonical_file_is_never_changed(tricky: Package):
    """INV-1."""
    before = {f.id: tricky.canonical(f.id).read_bytes() for f in tricky.data.manifest.files}
    masked = tricky.data.workspace.masked_pdf("d02").read_bytes()
    for name in ("logistics", "legal", "purchasing"):
        for file in tricky.data.manifest.files:
            tricky.make(name, file.id)
    assert {f.id: tricky.canonical(f.id).read_bytes() for f in tricky.data.manifest.files} == before
    assert tricky.data.workspace.masked_pdf("d02").read_bytes() == masked


def test_it_starts_from_the_canonical_file_not_from_the_masked_one(simple: Package):
    """auth-rbac §5: the masked copy may be missing (masking failed) or stale; neither matters."""
    masked = simple.data.workspace.masked_pdf("d01")
    masked.write_bytes(b"not a pdf")
    assert_flat(simple.make("logistics").read_bytes())
    masked.unlink()
    assert_flat(simple.make("legal").read_bytes())


# ── content a text redaction would miss ─────────────────────────────────────
@pytest.mark.parametrize("name", ["quality", "logistics", "legal", "development", "no_cost"])
def test_bookmarks_notes_attachments_margins_and_pictures_do_not_survive(tricky: Package, name: str):
    """auth-rbac §5: flattened: none of it is in the bytes or in a stream, for any scope."""
    for file in tricky.data.manifest.files:
        content = tricky.make(name, file.id).read_bytes()
        assert words_in(content, OWNERS) == set(), file.id
        with pymupdf.open(stream=content, filetype="pdf") as doc:
            assert not any(is_red(p) for page in doc for p in pixels(page))


def test_a_text_read_by_programs_only_is_never_served(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §5: /ActualText over a whole hidden sentence survives a redaction of its glyphs. The file
    is refused or served flat; the words are in neither case in it."""

    def hide(doc: pymupdf.Document) -> None:
        page = doc[0]
        xref = page.get_contents()[-1]
        page.insert_font(fontname="helv")
        stream = (
            "\nq /Span <</ActualText (1.3 Governing law quenlithor and liability.)>> BDC "
            "BT /helv 10 Tf 1 0 0 1 72 500 Tm (1.3 Governing law and liability.) Tj ET EMC Q\n"
        )
        doc.update_stream(xref, doc.xref_stream(xref) + stream.encode("ascii"))

    package = single(tmp_path, hide, ruleset)
    assert words_in(package.canonical().read_bytes(), ["quenlithor"]), "it is in the file"
    for name in ("logistics", "quality", "legal"):
        try:
            content = package.make(name).read_bytes()
        except access.AccessDenied:
            continue
        assert_flat(content)
        assert not words_in(content, ["quenlithor"])


def test_form_fields_and_links_do_not_survive(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §5.3: widgets and link targets are removed with the annotations."""

    def add(doc: pymupdf.Document) -> None:
        page = doc[0]
        widget = pymupdf.Widget()
        widget.field_name, widget.field_value = "wobblefen", "grumblock"
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(72, 600, 300, 620)
        page.add_widget(widget)
        page.insert_link(
            {
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(72, 72, 300, 100),
                "uri": "https://x.example/snorvex",
            }
        )

    package = single(tmp_path, add, ruleset)
    words = ["wobblefen", "grumblock", "snorvex"]
    assert words_in(package.canonical().read_bytes(), words) == set(words)
    content = package.make("logistics").read_bytes()
    assert_flat(content)
    assert words_in(content, words) == set()


def test_a_picture_under_a_hidden_sentence_is_not_drawn(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §5.3 (images=1): a picture that lies under a hidden sentence is removed as a whole, so
    its other end does not show inside the box of a visible sentence either."""

    def add(doc: pymupdf.Document) -> None:
        hidden = doc[0].search_for("Liability and governing law")[0]
        shown = doc[0].search_for("Packaging in small load carrier")[0]
        picture = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40))
        picture.set_rect(picture.irect, RED)
        doc[0].insert_image(hidden | shown, pixmap=picture, overlay=False, keep_proportion=False)

    package = single(tmp_path, add, ruleset)
    with pymupdf.open(str(package.canonical())) as doc:
        assert any(is_red(p) for p in pixels(doc[0]))
    with pymupdf.open(str(package.make("logistics"))) as doc:
        assert not any(is_red(p) for p in pixels(doc[0]))


# ── fail closed ─────────────────────────────────────────────────────────────
def assert_refused(package: Package, name: str = "logistics", file_id: str = "d01") -> None:
    with pytest.raises(access.AccessDenied):
        package.make(name, file_id)
    assert package.left_over() == [], "no file, no temporary file"


def test_a_redaction_that_does_nothing_is_noticed(simple: Package, monkeypatch: pytest.MonkeyPatch):
    """auth-rbac §5.4: the words that remain are compared with the words that may remain."""
    monkeypatch.setattr(pymupdf.Page, "apply_redactions", lambda *a, **k: True)
    assert_refused(simple)


def test_short_ordinary_words_that_stay_are_noticed_too(
    tmp_path: Path, ruleset: Ruleset, monkeypatch: pytest.MonkeyPatch
):
    """auth-rbac §5.4: every word that remains is counted, not only numbers and long words."""
    path = pdf_from_html(
        tmp_path / "in" / "01_a.pdf",
        "<p>The man is at the hall by us.</p><p>1.1 Packaging and container rules apply here.</p>",
    )
    package = package_of(tmp_path, [path], ruleset)
    assert [c.section_id for c in package.visible("logistics")] == ["1.1"]
    monkeypatch.setattr(pymupdf.Page, "apply_redactions", lambda *a, **k: True)
    assert_refused(package)


def test_a_picture_of_more_than_the_allowed_boxes_is_noticed(
    simple: Package, monkeypatch: pytest.MonkeyPatch
):
    """auth-rbac §5.6: the finished page is rendered again; ink outside the allowed boxes is refused."""
    real = pymupdf.Page.insert_image

    def spill(page, rect, *args, **kwargs):  # type: ignore[no-untyped-def]
        return real(page, pymupdf.Rect(rect) + (0, 0, 0, 60), *args, **{**kwargs, "keep_proportion": False})

    monkeypatch.setattr(pymupdf.Page, "insert_image", spill)
    assert_refused(simple)


def test_text_in_the_finished_file_is_noticed(simple: Package, monkeypatch: pytest.MonkeyPatch):
    """auth-rbac §5.6: the finished file must have no text layer."""
    real = pymupdf.Page.insert_image

    def label(page, rect, *args, **kwargs):  # type: ignore[no-untyped-def]
        page.insert_text((300, 300), "left over", fontsize=4, render_mode=3)
        return real(page, rect, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "insert_image", label)
    assert_refused(simple)


@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError, MemoryError])
def test_any_failure_while_drawing_is_a_refusal(
    simple: Package, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
):
    """auth-rbac §5.7: an exception is never a reason to send another file."""

    def fail(*args: object, **kwargs: object) -> None:
        raise error("drawing failed")

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", fail)
    assert_refused(simple)


def test_a_failure_while_the_file_is_put_in_place_is_a_refusal(
    simple: Package, monkeypatch: pytest.MonkeyPatch
):
    """auth-rbac §5.7: nothing half-written stays."""
    import os

    real = os.replace

    def fail(source, target, **options):  # type: ignore[no-untyped-def]
        if "scoped" in str(target):
            raise OSError("no space left")
        return real(source, target, **options)

    monkeypatch.setattr(os, "replace", fail)
    assert_refused(simple)


def test_positions_that_do_not_fit_the_canonical_file_are_refused(simple: Package):
    """auth-rbac §5.2: a file that was exchanged after parsing must not be drawn by the old positions."""
    other = pdf_from_html(
        simple.data.workspace.root / "other.pdf",
        "<p>Nothing that was parsed.</p><p>1.2345 EUR stands where the packaging clause was.</p>" * 6,
    )
    target = simple.canonical()
    target.chmod(0o600)
    target.write_bytes(other.read_bytes())
    assert_refused(simple)


def test_another_number_of_pages_is_refused(simple: Package):
    """auth-rbac §5.2."""
    target = simple.canonical()
    target.chmod(0o600)
    with pymupdf.open(str(target)) as doc:
        doc.new_page()
        content = doc.tobytes()
    target.write_bytes(content)
    assert_refused(simple)


def test_a_page_count_that_differs_from_the_parsed_file_is_refused(simple: Package):
    """auth-rbac §5.2: also when the canonical file itself is unchanged."""
    data = simple.data
    parsed = data.parsed["d01"].model_copy(update={"page_count": 2})
    with pytest.raises(access.AccessDenied):
        scoped_pdf(
            data.workspace, simple.file("d01"), parsed, simple.visible("logistics"), simple.scope("logistics")
        )
    assert simple.left_over() == []


def test_a_damaged_or_missing_canonical_file_is_refused(simple: Package):
    target = simple.canonical()
    target.chmod(0o600)
    target.write_bytes(b"%PDF-1.7 broken")
    assert_refused(simple)
    target.unlink()
    assert_refused(simple)


def test_a_parsed_file_of_another_document_is_refused(simple: Package):
    """auth-rbac §5.2: the positions belong to the file they are used on."""
    data = simple.data
    with pytest.raises(access.AccessDenied):
        scoped_pdf(
            data.workspace,
            simple.file("d01"),
            data.parsed["d02"],
            simple.visible("logistics"),
            simple.scope("logistics"),
        )
    assert simple.left_over() == []


def test_a_refusal_is_not_remembered(simple: Package, monkeypatch: pytest.MonkeyPatch):
    """A failed attempt leaves nothing that a later request could be served."""
    with monkeypatch.context() as patch:
        patch.setattr(pymupdf.Page, "apply_redactions", lambda *a, **k: True)
        assert_refused(simple)
    assert_flat(simple.make("logistics").read_bytes())
    assert len(simple.left_over()) == 1


# ── cache ───────────────────────────────────────────────────────────────────
def test_the_same_scope_uses_the_file_that_is_there(simple: Package, monkeypatch: pytest.MonkeyPatch):
    """auth-rbac §5: the second request draws nothing."""
    first = simple.make("logistics")
    content, stamp = first.read_bytes(), first.stat().st_mtime_ns

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("drawn again")

    with monkeypatch.context() as patch:
        patch.setattr(pymupdf.Page, "apply_redactions", fail)
        again = simple.make("logistics")
    assert again == first and (again.read_bytes(), again.stat().st_mtime_ns) == (content, stamp)
    assert simple.left_over() == [first]


def test_the_cache_key_is_what_is_visible_not_who_asks(simple: Package):
    """auth-rbac §5: two scopes that see the same sentences share a file; another set is another file."""
    first = simple.make("logistics")
    assert simple.make("legal", visible=simple.visible("logistics")) == first
    fewer = simple.visible("logistics")[:1]
    other = simple.make("logistics", visible=fewer)
    assert other != first and other.read_bytes() != first.read_bytes()
    assert first.is_file(), "the other scope's file is still right"


def test_the_order_of_the_clauses_does_not_matter(simple: Package):
    assert simple.make("logistics") == simple.make("logistics", visible=simple.visible("logistics")[::-1])


def test_other_files_clauses_do_not_change_the_file(simple: Package):
    """The visible set is of the whole package; only this file's part decides this file."""
    own = tuple(c for c in simple.visible("logistics") if c.source_doc == "d01")
    assert simple.make("logistics") == simple.make("logistics", visible=own)


def test_a_new_canonical_file_is_not_served_from_the_old_cache(simple: Package):
    """auth-rbac §5: the hash of the canonical file is part of the key."""
    first = simple.make("logistics")
    target = simple.canonical()
    target.chmod(0o600)
    with pymupdf.open(str(target)) as doc:
        doc[0].draw_rect(pymupdf.Rect(400, 700, 420, 720), color=(0, 0, 1))
        content = doc.tobytes()
    target.write_bytes(content)
    file = simple.file("d01").model_copy(update={"canonical_sha256": hashlib.sha256(content).hexdigest()})
    data = simple.data
    again = scoped_pdf(
        data.workspace, file, data.parsed["d01"], simple.visible("logistics"), simple.scope("logistics")
    )
    assert again != first


def test_moved_positions_are_another_file(simple: Package):
    """auth-rbac §5: the sentence boxes are part of the key: the same text drawn elsewhere is not served."""
    first = simple.make("logistics")
    data = simple.data
    parsed = data.parsed["d01"]
    moved = tuple(
        s.model_copy(
            update={"rects": tuple(r.model_copy(update={"w": max(0.0, r.w - 0.02)}) for r in s.rects)}
        )
        for s in parsed.sentences
    )
    try:
        other = scoped_pdf(
            data.workspace,
            simple.file("d01"),
            parsed.model_copy(update={"sentences": moved}),
            simple.visible("logistics"),
            simple.scope("logistics"),
        )
    except access.AccessDenied:
        return  # the boxes no longer fit the words: refused is as good
    assert other != first


def test_the_version_of_the_drawing_is_part_of_the_key(simple: Package, monkeypatch: pytest.MonkeyPatch):
    """auth-rbac §5: a change of the code makes new files instead of serving old ones."""
    module = import_module("autorfx.scoped_pdf")
    first = simple.make("logistics")
    monkeypatch.setattr(module, "SCOPE_VERSION", "another version")
    assert simple.make("logistics") != first


def test_two_scopes_at_once_get_their_own_files(tricky: Package):
    """auth-rbac §5: a lock per key and a temporary name of its own: no mixed-up and no half-written file."""
    names = ("logistics", "quality", "legal", "development")
    results: dict[tuple[str, int], bytes] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(names) * 2)

    def work(name: str, turn: int) -> None:
        try:
            barrier.wait(30)
            results[name, turn] = tricky.make(name, "d02").read_bytes()
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n, t)) for n in names for t in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    assert not errors and len(results) == len(names) * 2
    for name in names:
        assert results[name, 0] == results[name, 1] == tricky.make(name, "d02").read_bytes()
        allowed = {s.locus.key() for c in tricky.visible(name) for s in c.sentences}
        with pymupdf.open(stream=results[name, 0], filetype="pdf") as doc:
            for sentence in tricky.data.parsed["d02"].sentences:
                for rect in (r.model_dump() for r in sentence.rects):
                    if sentence.locus.key() in allowed:
                        assert has_ink(doc[0], rect), (name, sentence.text)
                    else:
                        found = pixels(doc[0], box_of(doc[0], rect, trim=1.5))
                        assert all(is_grey(p) for p in found), (name, sentence.text)
    assert len({results[n, 0] for n in names}) == len(names)
    assert len(tricky.left_over()) == len(names), "no temporary file is left"


def test_drawing_is_deterministic(tmp_path: Path, ruleset: Ruleset):
    """INV-7: the same package and scope give the same bytes in another place."""
    sources = make_package(tmp_path / "in")
    files = [package_of(tmp_path / n, sources, ruleset).make("logistics") for n in ("a", "b")]
    assert files[0].read_bytes() == files[1].read_bytes() and files[0].name == files[1].name
