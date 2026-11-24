"""Access control on data (RBAC-01..03, INV-4, INV-8; docs/auth-rbac.md §2, §4).

:func:`visible_clauses` is the only place that decides which clauses an account sees: the ones routed to
its departments, without cost clauses when it may not see cost. Every response is built from that answer,
before it exists; nothing is filtered afterwards or on the screen. A clause that is not visible is a locked
row with its identity only: no text, no summary, no title, no page and no reason. The same holds for
sentences, RTM rows, consistency issues, file names and the source PDF (:mod:`autorfx.scoped_pdf`).

The department filter of the clause list narrows the visible rows. Locked rows stay under every filter,
so it cannot reveal which department a locked clause belongs to.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Collection, Iterator, Mapping, Sequence
from pathlib import Path

import pymupdf

from autorfx.auth.policy import Scope
from autorfx.chunker import matches_cost_pattern, mentions_cost
from autorfx.consistency.checks import MAX_EVIDENCE
from autorfx.ingest.convert import save_deterministic
from autorfx.models import (
    Clause,
    ClauseAnalysis,
    ConsistencyIssue,
    FileRecord,
    Group,
    PackageManifest,
    ParsedFile,
    ParsedSentence,
)
from autorfx.rules import Ruleset
from autorfx.work_areas import matching_areas
from autorfx.workspace import Workspace

Key = tuple[str, int, int]
MAX_EXCERPT = 12
TRIM = 0.15
GREY = (0.82, 0.82, 0.82)
TOKEN = re.compile(r"[^\W_]+(?:[.,][^\W_]+)*")
FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES


class AccessDenied(PermissionError):
    """The account may not see this. The message is shown to the user."""


def assert_cost_visible(group: Group) -> None:
    if group != "cost_visible":
        raise AccessDenied("이 계정은 금액 관련 정보를 볼 수 없습니다.")


def hidden_keys(clauses: Sequence[Clause]) -> frozenset[Key]:
    """Positions of every sentence of a cost-sensitive clause."""
    return frozenset(s.locus.key() for c in clauses if c.is_cost_sensitive for s in c.sentences)


# ── the decision ────────────────────────────────────────────────────────────
def _is_routed_to(analysis: ClauseAnalysis, areas: Collection[str], ruleset: Ruleset) -> bool:
    """Whether a clause belongs to any of these departments: lead, reference, or one that receives all."""
    routed = (analysis.primary_department, *analysis.reference_departments)
    rules = (ruleset.department(area) for area in areas)
    return any(rule is not None and (rule.receives_all or rule.id in routed) for rule in rules)


def _decide(
    clauses: Sequence[Clause], analyses: Sequence[ClauseAnalysis], scope: Scope, ruleset: Ruleset
) -> list[bool]:
    """Per clause, whether the scope sees it.

    Fail closed: a clause nobody judged is not visible, and neither is one whose finding cites a position
    outside the visible clauses. Hiding a clause can do that to another one, so this runs until nothing
    changes. The full scope sees every judged clause: there is nothing outside it to cite.
    """
    by_id = {a.clause_id: a for a in analyses}
    judged = [by_id.get(c.clause_id) for c in clauses]
    if scope.is_full:
        return [analysis is not None for analysis in judged]
    shown = [
        analysis is not None
        and (scope.cost_visible or not clause.is_cost_sensitive)
        and (scope.areas is None or _is_routed_to(analysis, scope.areas, ruleset))
        for clause, analysis in zip(clauses, judged, strict=True)
    ]
    while True:
        keys = visible_keys([c for c, is_shown in zip(clauses, shown, strict=True) if is_shown])
        kept = [
            is_shown
            and analysis is not None
            and all(locus.key() in keys for locus in analysis.summary.finding.citations)
            for is_shown, analysis in zip(shown, judged, strict=True)
        ]
        if kept == shown:
            return kept
        shown = kept


def visible_clauses(
    clauses: Sequence[Clause], analyses: Sequence[ClauseAnalysis], scope: Scope, ruleset: Ruleset
) -> tuple[Clause, ...]:
    """The clauses this scope may see, in document order: the one decision behind every response."""
    shown = _decide(clauses, analyses, scope, ruleset)
    return tuple(clause for clause, is_shown in zip(clauses, shown, strict=True) if is_shown)


def visible_keys(visible: Sequence[Clause]) -> frozenset[Key]:
    """Positions of every sentence of these clauses: the allowlist."""
    return frozenset(s.locus.key() for c in visible for s in c.sentences)


def _rows(
    clauses: Sequence[Clause], analyses: Sequence[ClauseAnalysis], scope: Scope, ruleset: Ruleset
) -> Iterator[tuple[Clause, ClauseAnalysis, bool]]:
    """Every judged clause in document order, with its analysis and whether the scope sees it."""
    by_id = {a.clause_id: a for a in analyses}
    for clause, is_shown in zip(clauses, _decide(clauses, analyses, scope, ruleset), strict=True):
        analysis = by_id.get(clause.clause_id)
        if analysis is not None:
            yield clause, analysis, is_shown


# ── locked rows ─────────────────────────────────────────────────────────────
def _opaque(value: str, secret: bytes) -> str:
    """Stable across responses, and not recomputable from a guessed name without the store's key."""
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _masked(clause: Clause, secret: bytes) -> dict:
    """The identity of a hidden clause. A clause number ("4.2") says nothing, so it stays.

    Only a numbered clause keeps its number: the id of a sheet is its name and the id of other
    kinds may come from text too ("Target price EUR 7.77" as a sheet name), so those get an
    opaque id (INV-8).
    """
    section = clause.section_id if clause.kind == "clause" else f"hidden-{_opaque(clause.clause_id, secret)}"
    return {
        "kind": "masked",
        "clause_id": clause.clause_id if clause.kind == "clause" else f"{clause.source_doc}:{section}",
        "source_doc": clause.source_doc,
        "section_id": section,
    }


def _masked_issue(issue: ConsistencyIssue, secret: bytes) -> dict:
    """Issue ids name the entity or quote the document ("c-target_price", "n-ek-kr"); hidden ones don't."""
    return {"kind": "masked", "id": f"hidden-{_opaque(issue.id, secret)}"}


# ── clauses and RTM ─────────────────────────────────────────────────────────
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


def _states(clause: Clause, title: str) -> bool:
    """Whether the clause's own text contains these words."""
    words = r"\s+".join(re.escape(word) for word in title.split())
    return bool(words) and re.search(rf"(?<!\w){words}(?!\w)", clause.text, re.IGNORECASE) is not None


def _title(clause: Clause, scope: Scope, ruleset: Ruleset) -> str:
    """The title a scope may see.

    A clause can inherit the heading above it, which may belong to another department or state a price
    ("4.2 Target price EUR 1.20" above 4.2.1). Outside the full scope only words of the clause itself are
    a title, and without cost visibility never words that name cost.
    """
    title = clause.title
    if scope.is_full:
        return title
    if not scope.cost_visible and mentions_cost(title, ruleset):
        return ""
    return title if clause.own_title or _states(clause, title) else ""


def clause_items(
    clauses: Sequence[Clause],
    analyses: Sequence[ClauseAnalysis],
    *,
    scope: Scope,
    ruleset: Ruleset,
    secret: bytes,
    department: str | None = None,
) -> list[dict]:
    """Contract ``ClauseItem`` dicts in document order.

    ``department`` is a view filter on the visible rows; locked rows do not depend on it. The work-area
    keywords only take part in that filter, never in what is visible.
    """
    items: list[dict] = []
    for clause, analysis, is_shown in _rows(clauses, analyses, scope, ruleset):
        if not is_shown:
            items.append(_masked(clause, secret))
            continue
        work_areas = matching_areas(ruleset.work_areas, clause.text)
        if department and not (department in work_areas or _is_routed_to(analysis, (department,), ruleset)):
            continue
        flag = analysis.red_flag
        items.append(
            {
                "kind": "clause",
                "clause_id": clause.clause_id,
                "source_doc": clause.source_doc,
                "section_id": clause.section_id,
                "section_path": list(clause.section_path),
                "title": _title(clause, scope, ruleset),
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
                "translation_status": analysis.translation_status,
                "origin": analysis.origin,
            }
        )
    return items


def rtm_rows(
    clauses: Sequence[Clause],
    analyses: Sequence[ClauseAnalysis],
    *,
    scope: Scope,
    ruleset: Ruleset,
    secret: bytes,
) -> list[dict]:
    """Contract ``RtmRow`` dicts, one per clause; a clause outside the scope is a locked row."""
    rows: list[dict] = []
    for clause, analysis, is_shown in _rows(clauses, analyses, scope, ruleset):
        if not is_shown:
            rows.append(_masked(clause, secret))
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
                "title": _title(clause, scope, ruleset),
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


# ── sentences ───────────────────────────────────────────────────────────────
def sentences_for(
    parsed: ParsedFile, visible: Sequence[Clause], *, scope: Scope, page_number: int
) -> list[dict]:
    """Contract ``Sentence`` dicts of one page.

    Outside the full scope only sentences of the visible clauses: an allowlist, not a blocklist. A heading
    that belongs to no clause ("4.2 Target price EUR 1.20" directly above 4.2.1) is therefore left out
    too, although no rule marked it.
    """
    if not 1 <= page_number <= parsed.page_count:
        raise ValueError(f"{page_number}쪽은 없습니다. 이 파일은 {parsed.page_count}쪽입니다.")
    allowed = visible_keys(visible)
    return [
        s.public().model_dump(mode="json")
        for s in parsed.sentences
        if s.locus.page_number == page_number and (scope.is_full or s.locus.key() in allowed)
    ]


# ── consistency and evaluation ──────────────────────────────────────────────
def _is_issue_shown(issue: ConsistencyIssue, scope: Scope, keys: frozenset[Key]) -> bool:
    """Allowlist: an issue is shown only when every piece of its evidence lies in a visible clause.

    Evidence in no clause at all (a heading above its sub-clauses) is not known to be harmless. The title
    and detail name every value but only ``MAX_EVIDENCE`` sentences are kept, so at that count a value
    of a hidden clause may be among the unnamed rest.
    """
    if scope.is_full:
        return True
    if issue.is_cost_sensitive and not scope.cost_visible:
        return False
    return 0 < len(issue.evidence) < MAX_EVIDENCE and all(e.locus.key() in keys for e in issue.evidence)


def visible_issues(
    issues: Sequence[ConsistencyIssue], *, scope: Scope, visible: Sequence[Clause]
) -> tuple[ConsistencyIssue, ...]:
    """Issues this scope may see, in their stored order."""
    keys = visible_keys(visible)
    return tuple(issue for issue in issues if _is_issue_shown(issue, scope, keys))


def issue_items(
    issues: Sequence[ConsistencyIssue], *, scope: Scope, visible: Sequence[Clause], secret: bytes
) -> list[dict]:
    """Contract ``IssueItem`` dicts. An issue outside the scope shows only that it exists; those come last,
    so that their place does not say what they are about."""
    keys = visible_keys(visible)
    shown = [i for i in issues if _is_issue_shown(i, scope, keys)]
    locked = [i for i in issues if not _is_issue_shown(i, scope, keys)]
    return [
        *(
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
            for issue in shown
        ),
        *sorted((_masked_issue(issue, secret) for issue in locked), key=lambda row: row["id"]),
    ]


def withheld_ids(
    clauses: Sequence[Clause],
    analyses: Sequence[ClauseAnalysis],
    issues: Sequence[ConsistencyIssue],
    *,
    scope: Scope,
    ruleset: Ruleset,
) -> frozenset[str]:
    """Clause and issue ids this scope does not see; for reports built elsewhere."""
    if scope.is_full:
        return frozenset()
    visible = visible_clauses(clauses, analyses, scope, ruleset)
    keys = visible_keys(visible)
    known = {c.clause_id for c in clauses} | {a.clause_id for a in analyses} | {i.id for i in issues}
    shown = {c.clause_id for c in visible} | {i.id for i in issues if _is_issue_shown(i, scope, keys)}
    return frozenset(known - shown)


# ── names ───────────────────────────────────────────────────────────────────
def anonymous_name(rfq_id: str) -> str:
    """The name of a package for an account that may not see its file names."""
    return f"RFQ {rfq_id[-6:]}"


def _package_name(manifest: PackageManifest, shown: Collection[str]) -> str:
    """The package is named after one file ("<stem> 외 2개"), which need not be the first of the sorted
    list. Its name is shown only when that file and the first one are visible (fail closed)."""
    named = [
        f.id
        for f in manifest.files
        if manifest.name == Path(f.name).stem or manifest.name.startswith(f"{Path(f.name).stem} 외 ")
    ]
    if named and all(file_id in shown for file_id in (manifest.files[0].id, *named)):
        return manifest.name
    return anonymous_name(manifest.id)


def package_view(manifest: PackageManifest, visible: Sequence[Clause], *, scope: Scope) -> PackageManifest:
    """The package as this scope may see it: a file without a visible clause has no name, no warning and
    no clause count."""
    if scope.is_full:
        return manifest
    shown = {c.source_doc for c in visible}
    files = tuple(
        f
        if f.id in shown
        else f.model_copy(update={"name": f"파일 {f.id}", "warnings": (), "clause_count": 0})
        for f in manifest.files
    )
    return manifest.model_copy(update={"name": _package_name(manifest, shown), "files": files})


def scope_label(scope: Scope, ruleset: Ruleset) -> str:
    """Whose view a file that leaves the system is (the RTM workbook)."""
    if scope.areas is None:
        return "전체 조항"
    labels = [d.label for d in ruleset.departments if d.id in scope.areas]
    return f"{', '.join(labels) or '없음'} 담당 조항"


# ── source PDF ──────────────────────────────────────────────────────────────
def _tokens(text: str) -> Counter[str]:
    """Tokens that identify a sentence, with how often they occur: anything with a digit, and longer words."""
    return Counter(t.casefold() for t in TOKEN.findall(text) if any(ch.isdigit() for ch in t) or len(t) >= 7)


def page_with_remains(
    before: pymupdf.Document, after: pymupdf.Document, hidden: Sequence[ParsedSentence]
) -> int | None:
    """The first page on which an occurrence of a hidden sentence's tokens is left, or None.

    A token may also stand elsewhere on the page (a header, a sheet name, a visible sentence), so
    occurrences are counted: what remains may not exceed what the page held outside the hidden text.
    """
    secret: dict[int, Counter[str]] = defaultdict(Counter)
    for sentence in hidden:
        secret[sentence.locus.page_number] += _tokens(sentence.text)
    for page_number, tokens in secret.items():
        had = _tokens(before[page_number - 1].get_text("text", flags=FLAGS))
        left = _tokens(after[page_number - 1].get_text("text", flags=FLAGS))
        if any(left[token] > max(0, had[token] - count) for token, count in tokens.items()):
            return page_number
    return None


def _assert_removed(canonical: Path, target: Path, hidden: Sequence[ParsedSentence]) -> None:
    """Re-open the masked file and fail unless every occurrence of a hidden sentence's tokens is gone."""
    with pymupdf.open(str(canonical)) as before, pymupdf.open(str(target)) as after:
        page_number = page_with_remains(before, after, hidden)
    if page_number is not None:
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
