"""Cost masking for restricted teams, contract-shaped responses, the masked PDF, and exports."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pymupdf
import pytest
from openpyxl import load_workbook

from autorfx import access
from autorfx.analysis import RuleOnlyProvider, ScriptedProvider, TransientProviderError, analyze
from autorfx.chunker import build_clauses
from autorfx.export import MASKED_TEXT, rtm_workbook
from autorfx.models import ConsistencyIssue, IssueEvidence
from autorfx.parser.pdf import parse_pdf
from autorfx.rules import Ruleset
from autorfx.workspace import Workspace
from contract_schema import assert_valid
from factories import make_clause, make_file, make_locus, make_manifest, pdf_from_html

SECRET = "1.2345 EUR"
OPEN = make_clause("2.1", ["2.1 Delivery is FCA supplier plant according to Incoterms 2020."], title="Terms")
HIDDEN = make_clause(
    "2.2",
    [f"2.2 The target price is {SECRET} per piece.", "Quotes above it will be rejected."],
    title="Zielvorgabe",
    first_sentence=2,
    is_cost_sensitive=True,
)
QUALITY = make_clause(
    "3.1", ["3.1 The supplier shall hold a valid IATF 16949 certification."], first_sentence=4
)
CLAUSES = (OPEN, HIDDEN, QUALITY)
ENGINES = {"analysis": "rule-only", "translation": "none", "llm_connected": False}
PROVENANCE = {"parsing": "real", "analysis": "real", "citation": "real", "translation": "real"}
SECRETS = (SECRET, "1.2345", "target price", "Zielvorgabe", "rejected")


@pytest.fixture(scope="module")
def analyses(ruleset: Ruleset):
    return analyze(CLAUSES, ruleset, RuleOnlyProvider())


def issues() -> tuple[ConsistencyIssue, ...]:
    return (
        ConsistencyIssue(
            id="c-target_price",
            category="conflict",
            severity="high",
            entity="target_price",
            title=f"목표가: 문서마다 값이 다릅니다 ({SECRET} ↔ 1.30 EUR)",
            detail="같은 항목이 서로 다른 값으로 적혀 있습니다.",
            affects_cost=True,
            cost_fields=("target_price",),
            departments=("costing",),
            evidence=(
                IssueEvidence(locus=HIDDEN.sentences[0].locus, text=HIDDEN.sentences[0].text, value=SECRET),
            ),
        ),
        ConsistencyIssue(
            id="m-anlage-9",
            category="missing_attachment",
            severity="high",
            title="'Anlage 9'이(가) 받은 파일 묶음에 없습니다",
            detail="본문 1곳에서 참조합니다.",
            departments=("sales",),
            evidence=(
                IssueEvidence(locus=OPEN.sentences[0].locus, text=OPEN.sentences[0].text, value="Anlage 9"),
            ),
        ),
    )


# ── clauses ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("group", ["cost_visible", "cost_restricted"])
def test_clause_response_conforms_to_the_contract(ruleset: Ruleset, analyses, group: str):
    """CONTRACT-01, INV-9: what the backend returns validates against the schema the web uses."""
    items = access.clause_items(CLAUSES, analyses, group=group, department=None, ruleset=ruleset)  # type: ignore[arg-type]
    assert_valid("clauses", {"data": {"group": group, "clauses": items}, "provenance": PROVENANCE})
    rows = access.rtm_rows(CLAUSES, analyses, group=group, ruleset=ruleset)  # type: ignore[arg-type]
    assert_valid("rtm", {"data": {"group": group, "rows": rows}, "provenance": PROVENANCE})
    found = access.issue_items(issues(), group=group, clauses=CLAUSES)  # type: ignore[arg-type]
    checked = {"files": 1, "entities": 1, "references": 1}
    assert_valid(
        "consistency",
        {
            "data": {"group": group, "issues": found, "checked": checked},
            "provenance": {"consistency": "real"},
        },
    )


def test_cost_visible_team_sees_everything(ruleset: Ruleset, analyses):
    """RBAC-01: a team that may see cost clauses gets them in full, lead department first."""
    items = access.clause_items(CLAUSES, analyses, group="cost_visible", department=None, ruleset=ruleset)
    assert [i["kind"] for i in items] == ["clause", "clause", "clause"]
    price = items[1]
    assert price["primary_department"] == "costing" and SECRET in price["excerpt"][0]["text"]
    assert price["summary"]["finding"]["departments"][0] == "costing"
    assert price["origin"] == "rule" and price["translation_ko"] is None


def test_translation_of_a_cost_clause_never_reaches_a_restricted_team(ruleset: Ruleset):
    """INV-8: a translation (and its status) of a hidden clause is not in any restricted response."""
    marker = "비밀번역문장"
    translated = analyze(CLAUSES, ruleset, ScriptedProvider(translations={"d01:2.2": [marker]}))
    assert marker in (translated[1].translation_ko or "")
    payload = json.dumps(
        {
            "clauses": access.clause_items(
                CLAUSES, translated, group="cost_restricted", department=None, ruleset=ruleset
            ),
            "rtm": access.rtm_rows(CLAUSES, translated, group="cost_restricted", ruleset=ruleset),
        },
        ensure_ascii=False,
    )
    assert marker not in payload
    assert "translation_note" not in payload and "translation_sentences" not in payload


def test_visible_clauses_say_why_a_translation_is_missing_and_masked_ones_do_not(ruleset: Ruleset):
    """TRANS-03, INV-8: the status is served for visible clauses only, and nothing else of the record."""
    failing = {c.clause_id: [TransientProviderError("TimeoutError")] for c in CLAUSES}
    failed = analyze(CLAUSES, ruleset, ScriptedProvider(translations=failing))
    items = access.clause_items(CLAUSES, failed, group="cost_restricted", department=None, ruleset=ruleset)
    assert [i.get("translation_status") for i in items] == ["failed", None, "failed"]
    assert "translation_status" not in items[1]
    everything = access.clause_items(CLAUSES, failed, group="cost_visible", department=None, ruleset=ruleset)
    assert [i["translation_status"] for i in everything] == ["failed"] * 3


def test_masked_clause_carries_identity_only(ruleset: Ruleset, analyses):
    """INV-8: a hidden clause is exactly its id, file and number: no text, title, page or department."""
    items = access.clause_items(CLAUSES, analyses, group="cost_restricted", department=None, ruleset=ruleset)
    assert items[1] == {"kind": "masked", "clause_id": "d01:2.2", "source_doc": "d01", "section_id": "2.2"}
    assert items[0]["kind"] == items[2]["kind"] == "clause"


def test_nothing_of_a_hidden_clause_reaches_a_restricted_team(ruleset: Ruleset, analyses):
    """INV-8, SC-004: every response for a restricted team is searched for the hidden clause's content."""
    payload = json.dumps(
        {
            "clauses": [
                access.clause_items(CLAUSES, analyses, group="cost_restricted", department=d, ruleset=ruleset)
                for d in (None, *ruleset.department_ids)
            ],
            "rtm": access.rtm_rows(CLAUSES, analyses, group="cost_restricted", ruleset=ruleset),
            "issues": access.issue_items(issues(), group="cost_restricted", clauses=CLAUSES),
        },
        ensure_ascii=False,
    )
    for secret in (*SECRETS, "c-target_price", "target_price"):
        assert secret not in payload
    assert access.masked_issue_id("c-target_price") in payload, "the issue is shown to exist, by an opaque id"


def test_department_filter_cannot_reveal_a_hidden_clauses_department(ruleset: Ruleset, analyses):
    """INV-8: masked rows are the same under every department; visible ones follow lead and reference."""

    def ids(department: str | None, kind: str) -> list[str]:
        items = access.clause_items(
            CLAUSES, analyses, group="cost_restricted", department=department, ruleset=ruleset
        )
        return [i["clause_id"] for i in items if i["kind"] == kind]

    for department in (None, *ruleset.department_ids, "unknown"):
        assert ids(department, "masked") == ["d01:2.2"]
    assert ids("quality", "clause") == ["d01:3.1"]
    assert ids("logistics", "clause") == ["d01:2.1"]
    assert ids("sales", "clause") == ["d01:2.1", "d01:3.1"], "the department that receives everything"
    assert ids("unknown", "clause") == []


def test_finding_that_cites_hidden_text_is_hidden_too(ruleset: Ruleset, analyses):
    """INV-8, fail closed: a visible clause whose finding points into a hidden clause is masked."""
    leaky = analyses[0].model_copy(
        update={
            "summary": analyses[0].summary.model_copy(
                update={
                    "finding": analyses[0].summary.finding.model_copy(
                        update={"citations": (HIDDEN.sentences[0].locus,)}
                    )
                }
            )
        }
    )
    items = access.clause_items(
        CLAUSES, [leaky, *analyses[1:]], group="cost_restricted", department=None, ruleset=ruleset
    )
    assert items[0]["kind"] == "masked"


def test_cost_endpoints_are_refused_for_restricted_teams():
    """RBAC-01, COST-04: the pre-cost screens are not available to a restricted team."""
    access.assert_cost_visible("cost_visible")
    with pytest.raises(access.AccessDenied):
        access.assert_cost_visible("cost_restricted")


def test_issue_with_hidden_evidence_is_masked(ruleset: Ruleset):
    """INV-8: a consistency issue about a cost clause shows only that it exists."""
    restricted = access.issue_items(issues(), group="cost_restricted", clauses=CLAUSES)
    assert restricted[0] == {"kind": "masked", "id": access.masked_issue_id("c-target_price")}
    assert restricted[0]["id"].startswith("hidden-") and "price" not in restricted[0]["id"]
    assert restricted[1]["kind"] == "issue" and restricted[1]["evidence"][0]["value"] == "Anlage 9"
    visible = access.issue_items(issues(), group="cost_visible", clauses=CLAUSES)
    assert visible[0]["title"].startswith("목표가") and "is_cost_sensitive" not in visible[0]


# ── sentences and the source PDF ────────────────────────────────────────────
HTML = (
    "<h2>2 Terms</h2>"
    "<p>2.1 Delivery is FCA supplier plant according to Incoterms 2020.</p>"
    f"<p>2.2 The target price is {SECRET} per piece and quotes above it will be rejected.</p>"
    "<p>2.3 The supplier shall hold a valid certification.</p>"
)


@pytest.fixture
def document(tmp_path: Path, ruleset: Ruleset):
    workspace = Workspace.for_rfq(tmp_path / "work", "rfq-access000001").ensure()
    pdf_from_html(workspace.canonical_pdf("d01"), HTML)
    parsed = parse_pdf("d01", workspace.canonical_pdf("d01"))
    clauses = build_clauses(parsed, make_file(), ruleset)
    return workspace, parsed, clauses


def test_sentence_endpoint_leaves_hidden_sentences_out(document):
    """INV-8: the viewer of a restricted team gets no coordinates and no text of a hidden sentence."""
    _, parsed, clauses = document
    assert [c.section_id for c in clauses if c.is_cost_sensitive] == ["2.2"]
    everything = access.sentences_for(parsed, clauses, group="cost_visible", page_number=1)
    visible = access.sentences_for(parsed, clauses, group="cost_restricted", page_number=1)
    # The hidden sentence and the chapter heading "2 Terms", which belongs to no clause (allowlist).
    assert [s["text"] for s in everything if s not in visible] == ["2 Terms", parsed.sentences[2].text]
    assert [s["locus"]["section_id"] for s in visible] == ["2.1", "2.3"]
    assert SECRET not in json.dumps(visible) and SECRET in json.dumps(everything)
    assert_valid(
        "sentences",
        {
            "data": {"source_doc": "d01", "page_number": 1, "sentences": visible},
            "provenance": {"source": "real"},
        },
    )
    with pytest.raises(ValueError, match="2쪽은 없습니다"):
        access.sentences_for(parsed, clauses, group="cost_visible", page_number=2)


def _text(path: Path) -> str:
    with pymupdf.open(str(path)) as doc:
        return " ".join(" ".join(page.get_text("text").split()) for page in doc)


def test_masked_pdf_has_the_hidden_text_removed_not_covered(document):
    """INV-8, RBAC-02: the text is gone from the file itself; neighbouring clauses are intact."""
    workspace, parsed, clauses = document
    masked = access.build_masked_pdf(workspace, make_file(), clauses, parsed)
    text = _text(masked)
    assert "1.2345" not in text and "target price" not in text
    assert "2.1 Delivery is FCA supplier plant" in text and "2.3 The supplier shall hold" in text
    with pymupdf.open(str(masked)) as doc:
        assert doc.page_count == 1
    again = access.build_masked_pdf(workspace, make_file(), clauses, parsed).read_bytes()
    assert again == masked.read_bytes(), "INV-7: masking is deterministic"


def test_file_without_hidden_text_is_served_unchanged(document, ruleset: Ruleset):
    workspace, parsed, clauses = document
    open_only = [c.model_copy(update={"is_cost_sensitive": False}) for c in clauses]
    masked = access.build_masked_pdf(workspace, make_file(), open_only, parsed)
    assert masked.read_bytes() == workspace.canonical_pdf("d01").read_bytes()


def test_restricted_team_never_gets_the_canonical_file(document):
    """INV-8, fail closed: without a masked file the source is refused, not served unmasked."""
    workspace, parsed, clauses = document
    assert access.source_pdf(workspace, make_file(), group="cost_visible") == workspace.canonical_pdf("d01")
    with pytest.raises(access.AccessDenied):
        access.source_pdf(workspace, make_file(), group="cost_restricted")
    access.build_masked_pdf(workspace, make_file(), clauses, parsed)
    assert access.source_pdf(workspace, make_file(), group="cost_restricted") == workspace.masked_pdf("d01")


# ── export ──────────────────────────────────────────────────────────────────
def test_rtm_workbook_contains_what_the_team_may_see(ruleset: Ruleset, analyses):
    """RTM-02, INV-8, ENGINE-03: the export equals the filtered rows and states how it was computed."""
    manifest = make_manifest()

    def cells(group: str) -> list[list[object]]:
        rows = access.rtm_rows(CLAUSES, analyses, group=group, ruleset=ruleset)  # type: ignore[arg-type]
        data = rtm_workbook(rows, manifest=manifest, ruleset=ruleset, engines=ENGINES, group=group)  # type: ignore[arg-type]
        sheet = load_workbook(io.BytesIO(data)).active
        return [[c for c in row] for row in sheet.iter_rows(values_only=True)]

    visible = cells("cost_visible")
    assert "규칙 기반 · LLM 미연결" in visible[0][0] and "금액 열람 가능" in visible[0][0]
    assert visible[2][:5] == ["파일", "조항", "제목", "주관 부서", "참조 부서"]
    assert [row[1] for row in visible[3:]] == ["2.1", "2.2", "3.1"]
    assert visible[4][3] == "원가" and visible[4][5] == "Must"
    restricted = cells("cost_restricted")
    assert "금액 조항 가림" in restricted[0][0]
    assert list(restricted[4][:3]) == ["rfq.pdf", "2.2", MASKED_TEXT] and restricted[4][3] is None
    assert all(secret not in json.dumps(restricted, ensure_ascii=False, default=str) for secret in SECRETS)


# ── leaks the reviews found (2026-11-18) ────────────────────────────────────
def _pdf_with_lines(path: Path, lines: list[str], *, footer: str | None = None) -> Path:
    """A PDF whose lines are separate text blocks, as a word processor would draw a short list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    for number, text in enumerate(lines):
        page.insert_text((72, 72 + 28 * number), text, fontsize=10)
    if footer:
        page.insert_text((72, 830), footer, fontsize=8)
    doc.save(str(path))
    return path


def _masked_text(workspace: Workspace, path: Path, ruleset: Ruleset) -> str:
    """Parse, chunk and mask ``path`` as file d01; the text a restricted team would receive."""
    import shutil as _shutil

    _shutil.copyfile(path, workspace.canonical_pdf("d01"))
    parsed = parse_pdf("d01", workspace.canonical_pdf("d01"))
    clauses = build_clauses(parsed, make_file(), ruleset)
    masked = access.build_masked_pdf(workspace, make_file(), clauses, parsed, ruleset)
    with pymupdf.open(str(masked)) as doc:
        return " ".join(" ".join(page.get_text().split()) for page in doc)


def test_price_lines_the_parser_drops_as_an_index_are_still_removed(tmp_path: Path, ruleset: Ruleset):
    """INV-8 (review, CRITICAL): a run of short numbered lines is read as a contents list and dropped by
    the parser, so no clause hides it; the page-level check must remove the price anyway."""
    workspace = Workspace.for_rfq(tmp_path / "work", "rfq-leak00000001").ensure()
    lines = [
        "1 General",
        "1.1 Scope",
        "The supplier shall deliver stamped brackets.",
        "2 Commercial terms",
        "2.1 Target price EUR 1.20 per piece",
        "2.2 Annual price reduction 3 % from the second year",
        "2.3 Payment terms 60 days net",
        "2.4 Currency EUR",
        "2.5 Incoterm FCA Bremen",
        "3 Quality",
    ]
    text = _masked_text(workspace, _pdf_with_lines(tmp_path / "in.pdf", lines), ruleset)
    assert "Target price" not in text and "1.20" not in text and "Payment terms" not in text
    assert "The supplier shall deliver stamped brackets." in text, "unrelated text stays"


def test_price_text_in_the_page_margin_is_removed(tmp_path: Path, ruleset: Ruleset):
    """INV-8 (review H1): header and footer text is never a sentence, so only the page check sees it."""
    workspace = Workspace.for_rfq(tmp_path / "work", "rfq-leak00000002").ensure()
    path = _pdf_with_lines(
        tmp_path / "in.pdf",
        ["1 Scope", "1.1 The supplier shall deliver brackets to the plant."],
        footer="Target price EUR 4.4321 per piece (quotation basis)",
    )
    text = _masked_text(workspace, path, ruleset)
    assert "4.4321" not in text and "The supplier shall deliver brackets" in text


def test_bookmarks_and_notes_are_removed(tmp_path: Path, ruleset: Ruleset):
    """INV-8 (review H1): the outline and annotations are shown by viewers and carry text."""
    workspace = Workspace.for_rfq(tmp_path / "work", "rfq-leak00000003").ensure()
    path = pdf_from_html(tmp_path / "in.pdf", HTML)
    with pymupdf.open(str(path)) as doc:
        doc.set_toc([[1, f"Target price {SECRET}", 1]])
        doc[0].add_text_annot((500, 800), f"Note: target price {SECRET}")
        doc.save(str(tmp_path / "noted.pdf"))
    import shutil as _shutil

    _shutil.copyfile(tmp_path / "noted.pdf", workspace.canonical_pdf("d01"))
    parsed = parse_pdf("d01", workspace.canonical_pdf("d01"))
    clauses = build_clauses(parsed, make_file(), ruleset)
    masked = access.build_masked_pdf(workspace, make_file(), clauses, parsed, ruleset)
    with pymupdf.open(str(masked)) as doc:
        assert doc.get_toc() == [] and not list(doc[0].annots())
        assert "1.2345" not in doc[0].get_text()
    assert b"1.2345" not in masked.read_bytes()


def test_a_file_with_an_attachment_is_not_served_to_a_restricted_team(tmp_path: Path, ruleset: Ruleset):
    """INV-8 (review H1), fail closed: an embedded file cannot be checked, so the source is refused."""
    workspace = Workspace.for_rfq(tmp_path / "work", "rfq-leak00000004").ensure()
    path = pdf_from_html(tmp_path / "in.pdf", HTML)
    with pymupdf.open(str(path)) as doc:
        doc.embfile_add("prices.txt", b"target price 1.2345 EUR")
        doc.save(str(workspace.canonical_pdf("d01")))
    parsed = parse_pdf("d01", workspace.canonical_pdf("d01"))
    clauses = build_clauses(parsed, make_file(), ruleset)
    with pytest.raises(access.AccessDenied):
        access.build_masked_pdf(workspace, make_file(), clauses, parsed, ruleset)
    assert not workspace.masked_pdf("d01").exists()
    with pytest.raises(access.AccessDenied):
        access.source_pdf(workspace, make_file(), group="cost_restricted")


def test_a_heading_that_belongs_to_no_clause_is_not_sent(ruleset: Ruleset):
    """INV-8 (review LOW-11): sentences of a restricted team come from an allowlist of visible clauses."""
    from autorfx.models import ParsedFile, SectionInfo
    from factories import make_sentence

    heading = make_sentence("4.2 Target price EUR 1.20", 1, role="heading", section_id="4.2")
    body = make_sentence("4.2.1 Delivery is FCA supplier plant.", 2, section_id="4.2.1")
    parsed = ParsedFile(
        file_id="d01",
        lang="en",
        page_count=1,
        sentences=(heading, body),
        sections=(SectionInfo(section_id="4.2", heading_only=True), SectionInfo(section_id="4.2.1")),
    )
    clauses = [make_clause("4.2.1", [body.text], first_sentence=2)]
    restricted = access.sentences_for(parsed, clauses, group="cost_restricted", page_number=1)
    assert [s["text"] for s in restricted] == [body.text]
    visible = access.sentences_for(parsed, clauses, group="cost_visible", page_number=1)
    assert len(visible) == 2


def test_an_inherited_title_that_states_a_price_is_not_sent(ruleset: Ruleset):
    """INV-8 (commit review HIGH): a visible clause may inherit "Target price EUR 1.20" from the
    heading above it; a restricted team gets the clause without that title."""
    child = make_clause("4.2.1", ["4.2.1 Delivery is FCA supplier plant."], title="Target price EUR 1.20")
    result = analyze([child], ruleset, RuleOnlyProvider())
    restricted = access.clause_items(
        [child], result, group="cost_restricted", department=None, ruleset=ruleset
    )
    rtm = access.rtm_rows([child], result, group="cost_restricted", ruleset=ruleset)
    assert restricted[0]["kind"] == "clause" and restricted[0]["title"] == "" and rtm[0]["title"] == ""
    visible = access.clause_items([child], result, group="cost_visible", department=None, ruleset=ruleset)
    assert visible[0]["title"] == "Target price EUR 1.20"


def test_an_issue_with_evidence_in_no_clause_is_masked(ruleset: Ruleset):
    """INV-8 (commit review MEDIUM), allowlist: evidence that belongs to no clause is not known to be
    harmless, so the issue is masked for a restricted team even when nothing marked it."""
    from autorfx.models import ConsistencyIssue, IssueEvidence

    stray = make_locus(30, section_id="4.2")
    issue = ConsistencyIssue(
        id="c-thickness",
        category="conflict",
        severity="high",
        entity="thickness",
        title="판재 두께: 문서마다 값이 다릅니다",
        detail="",
        evidence=(
            IssueEvidence(locus=OPEN.sentences[0].locus, text=OPEN.sentences[0].text, value="2.5"),
            IssueEvidence(locus=stray, text="4.2 Target price EUR 1.20, sheet 2.0 mm", value="2.0"),
        ),
    )
    restricted = access.issue_items([issue], group="cost_restricted", clauses=CLAUSES)
    assert restricted == [{"kind": "masked", "id": access.masked_issue_id("c-thickness")}]
    assert access.issue_items([issue], group="cost_visible", clauses=CLAUSES)[0]["kind"] == "issue"


def test_a_hidden_sheet_does_not_show_its_name(ruleset: Ruleset, analyses):
    """INV-8 (review H2): a sheet's id is its name, and a name can be the price itself."""
    sheet = make_clause(
        "Target price EUR 7.77",
        ["Item | Target price", "Bracket | 7.77"],
        source_doc="d03",
        kind="table",
        is_cost_sensitive=True,
    )
    sheet_analysis = analyze([sheet], ruleset, RuleOnlyProvider())
    rows = access.clause_items(
        [sheet], sheet_analysis, group="cost_restricted", department=None, ruleset=ruleset
    )
    rtm = access.rtm_rows([sheet], sheet_analysis, group="cost_restricted", ruleset=ruleset)
    assert rows[0]["kind"] == "masked" and rows[0] == rtm[0]
    assert "7.77" not in json.dumps(rows + rtm) and "Target" not in json.dumps(rows + rtm)
    assert (
        rows[0]["section_id"].startswith("hidden-") and rows[0]["clause_id"] == f"d03:{rows[0]['section_id']}"
    )
