"""Access control on data (RBAC-01..03, INV-4, INV-8).

Cost-sensitive clauses are removed before a response is built, never on the
screen. A restricted team (and any unknown team) receives, for such a clause,
only its identity: no text, no summary, no title, no page. The same holds for
sentences, RTM rows, consistency issues and the source PDF, whose hidden text
is truly redacted, not covered.

Order matters: mask first, then filter by department. Masked rows are returned
under every department, so the filter cannot reveal which department a hidden
clause belongs to.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

import pymupdf

from autorfx.chunker import matches_cost_pattern, mentions_cost
from autorfx.ingest.convert import save_deterministic
from autorfx.models import (
    Clause,
    ClauseAnalysis,
    ConsistencyIssue,
    FileRecord,
    Group,
    ParsedFile,
    ParsedSentence,
)
from autorfx.work_areas import matching_areas
from autorfx.rules import Ruleset
from autorfx.workspace import Workspace

Key = tuple[str, int, int]
MAX_EXCERPT = 12
TRIM = 0.15
GREY = (0.82, 0.82, 0.82)
TOKEN = re.compile(r"[^\W_]+(?:[.,][^\W_]+)*")
FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES


class AccessDenied(PermissionError):
    """The team may not see this. The message is shown to the user."""


def assert_cost_visible(group: Group) -> None:
    if group != "cost_visible":
        raise AccessDenied("이 팀은 금액 관련 정보를 볼 수 없습니다.")


def hidden_keys(clauses: Sequence[Clause]) -> frozenset[Key]:
    """Positions of every sentence of a cost-sensitive clause."""
    return frozenset(s.locus.key() for c in clauses if c.is_cost_sensitive for s in c.sentences)


def _opaque(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def _masked(clause: Clause) -> dict:
    """The identity of a hidden clause. A clause number ("4.2") says nothing, so it stays.

    Only a numbered clause keeps its number: the id of a sheet is its name and the id of other
    kinds may come from text too ("Target price EUR 7.77" as a sheet name), so those get an
    opaque id that is stable across responses (INV-8).
    """
    if clause.kind == "clause":
        return {
            "kind": "masked",
            "clause_id": clause.clause_id,
            "source_doc": clause.source_doc,
            "section_id": clause.section_id,
        }
    section = f"hidden-{_opaque(clause.clause_id)}"
    return {
        "kind": "masked",
        "clause_id": f"{clause.source_doc}:{section}",
        "source_doc": clause.source_doc,
        "section_id": section,
    }


def masked_issue_id(issue_id: str) -> str:
    """Issue ids name the entity or quote the document ("c-target_price", "n-ek-kr"); hidden ones don't."""
    return f"hidden-{_opaque(issue_id)}"


def _is_hidden(clause: Clause, analysis: ClauseAnalysis, group: Group, hidden: frozenset[Key]) -> bool:
    if group == "cost_visible":
        return False
    # Fail closed: a visible clause whose finding cites hidden text is hidden too.
    return clause.is_cost_sensitive or any(c.key() in hidden for c in analysis.summary.finding.citations)


def _excerpt(clause: Clause, analysis: ClauseAnalysis) -> list[dict]:
    cited = {c.key() for c in analysis.summary.finding.citations}
    chosen = [s for s in clause.sentences if s.locus.key() in cited]
    for sentence in clause.sentences:
        if len(chosen) >= MAX_EXCERPT:
            break
        if sentence not in chosen:
            chosen.append(sentence)
    order = {s.locus.key(): i for i, s in enumerate(clause.sentences)}
    chosen.sort(key=lambda s: order[s.locus.key()])
    return [{"locus": s.locus.model_dump(mode="json"), "text": s.text} for s in chosen]


def _matches(analysis: ClauseAnalysis, areas: Sequence[str] | None, ruleset: Ruleset) -> bool:
    """Whether a clause belongs to any of the rule departments; ``None`` is no filter."""
    if areas is None:
        return True
    routed = (analysis.primary_department, *analysis.reference_departments)
    for area in areas:
        rule = ruleset.department(area)
        if rule is not None and (rule.receives_all or area in routed):
            return True
    return False


def clause_items(
    clauses: Sequence[Clause],
    analyses: Sequence[ClauseAnalysis],
    *,
    group: Group,
    department: str | None,
    ruleset: Ruleset,
    areas: Sequence[str] | None = None,
) -> list[dict]:
    """Contract ``ClauseItem`` dicts in document order: masked first, then filtered by department.

    ``areas`` filters by several rule departments at once (a company department, SPEC US10);
    without it the filter is ``department`` alone.
    """
    wanted = tuple(areas) if areas is not None else ((department,) if department else None)
    hidden = hidden_keys(clauses)
    by_id = {a.clause_id: a for a in analyses}
    items: list[dict] = []
    for clause in clauses:
        analysis = by_id.get(clause.clause_id)
        if analysis is None:
            continue
        if _is_hidden(clause, analysis, group, hidden):
            items.append(_masked(clause))
            continue
        work_areas = matching_areas(clause.text)
        if not _matches(analysis, wanted, ruleset) and not any(a in work_areas for a in wanted or ()):
            continue
        flag = analysis.red_flag
        items.append(
            {
                "kind": "clause",
                "clause_id": clause.clause_id,
                "source_doc": clause.source_doc,
                "section_id": clause.section_id,
                "section_path": list(clause.section_path),
                "title": _title(clause, group, ruleset),
                "page_number": clause.page_number,
                "lang": clause.lang,
                "work_areas": work_areas,
                "primary_department": analysis.primary_department,
                "reference_departments": list(analysis.reference_departments),
                "priority": analysis.priority,
                "red_flag": flag.model_dump(mode="json") if flag else None,
                "excerpt": _excerpt(clause, analysis),
                "summary": analysis.summary.model_dump(mode="json"),
                "translation_ko": analysis.translation_ko,
                "origin": analysis.origin,
            }
        )
    return items


def rtm_rows(
    clauses: Sequence[Clause], analyses: Sequence[ClauseAnalysis], *, group: Group, ruleset: Ruleset
) -> list[dict]:
    """Contract ``RtmRow`` dicts, one per clause, masked for restricted teams."""
    hidden = hidden_keys(clauses)
    by_id = {a.clause_id: a for a in analyses}
    rows: list[dict] = []
    for clause in clauses:
        analysis = by_id.get(clause.clause_id)
        if analysis is None:
            continue
        if _is_hidden(clause, analysis, group, hidden):
            rows.append(_masked(clause))
            continue
        citations = analysis.summary.finding.citations
        position = citations[0] if citations else clause.sentences[0].locus
        flag = analysis.red_flag
        rows.append(
            {
                "kind": "clause",
                "clause_id": clause.clause_id,
                "source_doc": clause.source_doc,
                "section_id": clause.section_id,
                "title": _title(clause, group, ruleset),
                "primary_department": analysis.primary_department,
                "reference_departments": list(analysis.reference_departments),
                "priority": analysis.priority,
                "red_flag": {"type": flag.type, "label": flag.label} if flag else None,
                "page_number": position.page_number,
                "sentence_index": position.sentence_index,
                "anchor": position.anchor,
                "citation_status": analysis.summary.citation_status,
            }
        )
    return rows


def sentences_for(
    parsed: ParsedFile, clauses: Sequence[Clause], *, group: Group, page_number: int
) -> list[dict]:
    """Contract ``Sentence`` dicts of one page.

    A restricted team gets only sentences of clauses it may see: an allowlist, not a blocklist.
    A heading that belongs to no clause ("4.2 Target price EUR 1.20" directly above 4.2.1) is
    therefore left out too, although no rule marked it.
    """
    if not 1 <= page_number <= parsed.page_count:
        raise ValueError(f"{page_number}쪽은 없습니다. 이 파일은 {parsed.page_count}쪽입니다.")
    allowed = (
        None
        if group == "cost_visible"
        else frozenset(s.locus.key() for c in clauses if not c.is_cost_sensitive for s in c.sentences)
    )
    return [
        s.public().model_dump(mode="json")
        for s in parsed.sentences
        if s.locus.page_number == page_number and (allowed is None or s.locus.key() in allowed)
    ]


def visible_keys(clauses: Sequence[Clause]) -> frozenset[Key]:
    """Positions of every sentence of a clause a restricted team may see: the allowlist."""
    return frozenset(s.locus.key() for c in clauses if not c.is_cost_sensitive for s in c.sentences)


def _issue_secret(issue: ConsistencyIssue, visible: frozenset[Key]) -> bool:
    """Allowlist: an issue is shown only when every piece of its evidence lies in a visible clause.

    Evidence in no clause at all (a heading above its sub-clauses) is not known to be harmless.
    """
    return issue.is_cost_sensitive or any(e.locus.key() not in visible for e in issue.evidence)


def _title(clause: Clause, group: Group, ruleset: Ruleset) -> str:
    """The title a group may see. A visible clause can inherit a heading that states a price
    ("4.2 Target price EUR 1.20" above 4.2.1): such a title is not sent to a restricted team."""
    if group == "cost_visible" or not mentions_cost(clause.title, ruleset):
        return clause.title
    return ""


def withheld_ids(
    clauses: Sequence[Clause],
    analyses: Sequence[ClauseAnalysis],
    issues: Sequence[ConsistencyIssue],
    *,
    group: Group,
) -> frozenset[str]:
    """Clause and issue ids this group sees only as masked rows; for reports built elsewhere."""
    if group == "cost_visible":
        return frozenset()
    hidden = hidden_keys(clauses)
    by_id = {a.clause_id: a for a in analyses}
    masked = {
        c.clause_id
        for c in clauses
        if c.clause_id in by_id and _is_hidden(c, by_id[c.clause_id], group, hidden)
    }
    visible = visible_keys(clauses)
    return frozenset(masked | {i.id for i in issues if _issue_secret(i, visible)})


def issue_items(issues: Sequence[ConsistencyIssue], *, group: Group, clauses: Sequence[Clause]) -> list[dict]:
    """Contract ``IssueItem`` dicts; an issue with cost-sensitive evidence shows only that it exists."""
    visible = visible_keys(clauses)
    items: list[dict] = []
    for issue in issues:
        if group != "cost_visible" and _issue_secret(issue, visible):
            items.append({"kind": "masked", "id": masked_issue_id(issue.id)})
            continue
        items.append(
            {
                "kind": "issue",
                "id": issue.id,
                "category": issue.category,
                "severity": issue.severity,
                "entity": issue.entity,
                "title": issue.title,
                "detail": issue.detail,
                "affects_cost": issue.affects_cost,
                "departments": list(issue.departments),
                "evidence": [e.model_dump(mode="json") for e in issue.evidence],
                "citation_status": issue.citation_status,
            }
        )
    return items


# ── source PDF ──────────────────────────────────────────────────────────────
def _tokens(text: str) -> Counter[str]:
    """Tokens that identify a sentence, with how often they occur: anything with a digit, and longer words."""
    return Counter(t.casefold() for t in TOKEN.findall(text) if any(ch.isdigit() for ch in t) or len(t) >= 7)


def _assert_removed(canonical: Path, target: Path, hidden: Sequence[ParsedSentence]) -> None:
    """Re-open the masked file and fail unless every occurrence of a hidden sentence's tokens is gone.

    A token may also stand elsewhere on the page (a header, a sheet name, a visible sentence), so
    occurrences are counted: what remains may not exceed what the page held outside the hidden text.
    """
    secret: dict[int, Counter[str]] = defaultdict(Counter)
    for sentence in hidden:
        secret[sentence.locus.page_number] += _tokens(sentence.text)
    with pymupdf.open(str(canonical)) as before, pymupdf.open(str(target)) as after:
        for page_number, tokens in secret.items():
            had = _tokens(before[page_number - 1].get_text("text", flags=FLAGS))
            left = _tokens(after[page_number - 1].get_text("text", flags=FLAGS))
            if any(left[token] > max(0, had[token] - count) for token, count in tokens.items()):
                target.unlink(missing_ok=True)
                raise AccessDenied(f"가림 처리에 실패했습니다 ({page_number}쪽). 원문을 제공하지 않습니다.")


def _has_cost_text(text: str, ruleset: Ruleset) -> bool:
    # The body-text rule of the chunker: a visible clause passed exactly this test on its own text.
    return matches_cost_pattern(" ".join(text.split()), ruleset)


def _clean_beyond_sentences(
    doc: pymupdf.Document, ruleset: Ruleset, visible: Mapping[int, Sequence[pymupdf.Rect]]
) -> bool:
    """Remove cost text that is in the file but in no parsed clause. True when the file is safe to serve.

    Sentence redaction only reaches what the parser kept. Index lines, page headers, sheet titles,
    bookmarks and notes are outside it, so they are handled here: bookmarks and notes are dropped,
    and in a text block that matches a cost pattern every line is redacted except the lines of
    sentences the team may see (``visible``, per page number): those clauses passed the same rules
    on their own text, and a block often holds a visible clause next to a hidden one. The result is
    checked again over the whole page; a file with an attachment or a form field, or with cost text
    still on a page, is not served (INV-8).
    """
    doc.set_toc([])
    for number, page in enumerate(doc, start=1):
        for note in list(page.annots()):
            page.delete_annot(note)
        shown = visible.get(number, ())
        for block in page.get_text("dict")["blocks"]:
            lines = block.get("lines", [])
            text = " ".join(span["text"] for line in lines for span in line["spans"])
            if not _has_cost_text(text, ruleset):
                continue
            for line in lines:
                box = pymupdf.Rect(line["bbox"])
                centre = (box.tl + box.br) / 2
                if not any(rect.contains(centre) for rect in shown):
                    page.add_redact_annot(box, fill=GREY)
        page.apply_redactions()
    if doc.embfile_count() > 0:
        return False
    return not any(page.first_widget is not None or _has_cost_text(page.get_text(), ruleset) for page in doc)


def build_masked_pdf(
    workspace: Workspace,
    file: FileRecord,
    clauses: Sequence[Clause],
    parsed: ParsedFile,
    ruleset: Ruleset | None = None,
) -> Path:
    """Write the canonical PDF with every cost-sensitive sentence redacted (text removed, grey box drawn).

    With ``ruleset`` (the pipeline always passes it) the rest of the file is cleaned and checked too;
    see :func:`_clean_beyond_sentences`. Images are not inspected: text inside a picture stays.
    """
    canonical, target = workspace.canonical_pdf(file.id), workspace.masked_pdf(file.id)
    keys = hidden_keys([c for c in clauses if c.source_doc == file.id])
    hidden = [s for s in parsed.sentences if s.locus.key() in keys]
    if not hidden and ruleset is None:
        shutil.copyfile(canonical, target)
        return target
    by_page: dict[int, list[ParsedSentence]] = defaultdict(list)
    for sentence in hidden:
        by_page[sentence.locus.page_number].append(sentence)
    with pymupdf.open(str(canonical)) as doc:
        for page_number in sorted(by_page):
            page = doc[page_number - 1]
            width, height = page.rect.width, page.rect.height
            for sentence in by_page[page_number]:
                for rect in sentence.rects:
                    # Trimmed vertically so that the lines above and below are not touched.
                    box = pymupdf.Rect(
                        rect.x * width,
                        (rect.y + rect.h * TRIM) * height,
                        (rect.x + rect.w) * width,
                        (rect.y + rect.h * (1 - TRIM)) * height,
                    )
                    page.add_redact_annot(box, fill=GREY)
            page.apply_redactions()
        visible: dict[int, list[pymupdf.Rect]] = defaultdict(list)
        allowed = {
            s.locus.key()
            for c in clauses
            if c.source_doc == file.id and not c.is_cost_sensitive
            for s in c.sentences
        }
        for sentence in parsed.sentences:
            if sentence.locus.key() in allowed:
                page = doc[sentence.locus.page_number - 1]
                width, height = page.rect.width, page.rect.height
                visible[sentence.locus.page_number] += [
                    pymupdf.Rect(r.x * width, r.y * height, (r.x + r.w) * width, (r.y + r.h) * height)
                    + (-1, -1, 1, 1)
                    for r in sentence.rects
                ]
        if ruleset is not None and not _clean_beyond_sentences(doc, ruleset, visible):
            target.unlink(missing_ok=True)
            raise AccessDenied("가림 처리 뒤에도 금액 관련 내용이 남아 원문을 제공하지 않습니다.")
        save_deterministic(doc, target)
    _assert_removed(canonical, target, hidden)
    return target


def source_pdf(workspace: Workspace, file: FileRecord, *, group: Group) -> Path:
    """The PDF a team may see. A restricted team never gets the canonical file, masked or nothing."""
    if group == "cost_visible":
        return workspace.canonical_pdf(file.id)
    masked = workspace.masked_pdf(file.id)
    if not masked.is_file():
        raise AccessDenied("가림 처리된 원문이 아직 준비되지 않았습니다.")
    return masked
