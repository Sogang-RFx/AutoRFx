"""Cross-document checks (CONSIST-03..05, SPEC §16).

Four kinds of issue, each with the sentences that prove it:

* ``conflict``: one entity stated with incompatible values in different places.
  The same value in another notation is not a conflict.
* ``missing_attachment``: a referenced annex, attachment or exhibit that is no
  file of the package and is declared nowhere in it. The package is a closed
  list, so "not in the list" means missing. External standards (ISO, DIN …)
  are not attachments and are never reported.
* ``broken_reference``: a reference to a section number that does not exist in
  the same file, or a reference whose target is blank.
* ``notation``: one identifier written in two ways (transposed or substituted
  letters in hyphenated codes).

Order is deterministic: by category, then by the position of the first evidence.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from autorfx.consistency.normalize import dates_agree
from autorfx.models import (
    Clause,
    ConsistencyIssue,
    EntityMention,
    IssueCategory,
    IssueEvidence,
    Locus,
    Model,
    PackageManifest,
    ParsedFile,
    ParsedSentence,
)
from autorfx.rules import EntityRule, Ruleset

CATEGORY_ORDER: tuple[IssueCategory, ...] = ("conflict", "missing_attachment", "broken_reference", "notation")
ATTACHMENT = re.compile(
    # The label may end a sentence ("… und Anlage 9."), but "Annex 3.2" names a section, not Annex 3.
    r"\b(Anlage|Anhang|Annex|Appendix|Attachment|Exhibit)s?\s+([A-Z](?:-\d+)?|\d{1,2})(?!\w)(?!\.\d)",
    re.IGNORECASE,
)
SECTION_REF = re.compile(
    r"\b(?:Abschnitt|Ziffer|Kapitel|Punkt|section|clause|paragraph)\s+(\d{1,2}(?:\.\d{1,2})+)\b",
    re.IGNORECASE,
)
BLANK_TARGET = re.compile(
    r"\b(?:contained|described|set forth|listed|specified|defined|included) in\s*,", re.IGNORECASE
)
CODE = re.compile(r"\b[A-Z]{2,}(?:-[A-Z]{2,})+\b")
ORDINAL = re.compile(r"^0*(\d{1,2})[_\-. ]")
MAX_EVIDENCE = 4


class ConsistencyReport(Model):
    issues: tuple[ConsistencyIssue, ...]
    files: int
    entities: int
    """Entities for which at least two mentions were compared."""
    references: int
    """References to attachments and sections that were resolved or found missing."""


def _place(locus: Locus) -> str:
    anchor = f" {locus.anchor}" if locus.anchor else ""
    return f"{locus.source_doc} {locus.page_number}쪽{anchor}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "x"


def _agree(rule: EntityRule, left: str, right: str) -> bool:
    if rule.kind == "date" or rule.compare == "month":
        return dates_agree(left, right)
    if rule.compare == "tolerance" and rule.tolerance:
        try:
            return abs(Decimal(left) - Decimal(right)) <= Decimal(rule.tolerance)
        except InvalidOperation:
            return left == right
    return left == right


def _distinct(rule: EntityRule, mentions: Sequence[EntityMention]) -> list[EntityMention]:
    """One mention per value that agrees with no earlier one, in document order."""
    kept: list[EntityMention] = []
    for mention in mentions:
        if not any(_agree(rule, mention.normalized, other.normalized) for other in kept):
            kept.append(mention)
    return kept


def _conflicts(
    mentions: Sequence[EntityMention], ruleset: Ruleset, departments: tuple[str, ...]
) -> tuple[list[ConsistencyIssue], int]:
    by_entity: dict[str, list[EntityMention]] = defaultdict(list)
    for mention in mentions:
        by_entity[mention.entity].append(mention)
    issues: list[ConsistencyIssue] = []
    compared = 0
    for rule in ruleset.entities:
        found = by_entity.get(rule.id, [])
        if len(found) < 2:
            continue
        compared += 1
        general = [m for m in found if m.scope is None]
        scopes = sorted({m.scope for m in found if m.scope is not None})
        groups = [general + [m for m in found if m.scope == scope] for scope in scopes] or [general]
        distinct: list[EntityMention] = []
        for group in groups:
            values = _distinct(rule, group)
            if len(values) > 1:
                distinct.extend(v for v in values if v not in distinct)
        distinct = _distinct(rule, distinct)
        if len(distinct) < 2:
            continue
        shown = " ↔ ".join(m.raw.strip() for m in distinct[:3])
        places = "; ".join(f"{_place(m.locus)}: {m.raw.strip()}" for m in distinct)
        issues.append(
            ConsistencyIssue(
                id=f"c-{rule.id}",
                category="conflict",
                severity=rule.severity,
                entity=rule.id,
                title=f"{rule.label}: 문서마다 값이 다릅니다 ({shown})",
                detail=f"같은 항목이 서로 다른 값으로 적혀 있습니다. {places}",
                affects_cost=rule.affects_cost,
                cost_fields=rule.cost_fields,
                departments=tuple(dict.fromkeys((rule.department, *departments))),
                evidence=tuple(
                    IssueEvidence(locus=m.locus, text=m.text, value=m.raw.strip(), normalized=m.normalized)
                    for m in distinct[:MAX_EVIDENCE]
                ),
                is_cost_sensitive=any(m.is_cost_sensitive for m in distinct),
            )
        )
    return issues, compared


def _declared_attachments(manifest: PackageManifest, parsed: Sequence[ParsedFile]) -> set[str]:
    """Labels the package provides: file-name ordinals and self-declarations ("Annex 3 – …")."""
    declared: set[str] = set()
    for record in manifest.files:
        if match := ORDINAL.match(record.name):
            declared.add(str(int(match[1])))
        for named in ATTACHMENT.finditer(record.name.replace("_", " ")):
            declared.add(named[2].upper().lstrip("0") or "0")
    for file in parsed:
        for sentence in file.sentences:
            match = ATTACHMENT.match(sentence.text.strip())
            starts_file = sentence.locus.page_number == 1 and sentence.locus.sentence_index <= 4
            if match and (starts_file or len(sentence.text) <= 80):
                declared.add(match[2].upper().lstrip("0") or "0")
    return declared


def _references(
    manifest: PackageManifest, parsed: Sequence[ParsedFile], departments: tuple[str, ...]
) -> tuple[list[ConsistencyIssue], int]:
    declared = _declared_attachments(manifest, parsed)
    missing: dict[str, list[ParsedSentence]] = defaultdict(list)
    names: dict[str, str] = {}
    broken: list[ConsistencyIssue] = []
    resolved = 0
    for file in parsed:
        sections = {s.section_id.split("#")[0] for s in file.sections}
        blanks = 0
        for sentence in file.sentences:
            for match in ATTACHMENT.finditer(sentence.text):
                label = match[2].upper().lstrip("0") or "0"
                if match.start() == 0 and len(sentence.text) <= 80:
                    continue  # the attachment naming itself
                resolved += 1
                if label not in declared:
                    missing[label].append(sentence)
                    names.setdefault(label, f"{match[1]} {match[2]}")
            for match in SECTION_REF.finditer(sentence.text):
                resolved += 1
                target = match[1]
                if target in sections or any(s.startswith(target + ".") for s in sections):
                    continue
                if not any(s.startswith(target.rsplit(".", 1)[0] + ".") for s in sections):
                    continue  # no sibling section was read in that chapter: absence proves nothing
                broken.append(
                    ConsistencyIssue(
                        id=f"b-{file.file_id}-{_slug(target)}",
                        category="broken_reference",
                        severity="medium",
                        title=f"'{match[0]}'이 가리키는 조항이 이 문서에 없습니다",
                        detail=f"{_place(sentence.locus)}에서 {target}을 참조하지만 그 조항이 없습니다.",
                        departments=departments,
                        evidence=(IssueEvidence(locus=sentence.locus, text=sentence.text, value=target),),
                    )
                )
            if BLANK_TARGET.search(sentence.text):
                blanks += 1
                resolved += 1
                broken.append(
                    ConsistencyIssue(
                        id=f"b-{file.file_id}-blank-{blanks}",
                        category="broken_reference",
                        severity="medium",
                        title="참조 대상이 비어 있습니다",
                        detail=f"{_place(sentence.locus)}의 문장에서 참조 대상의 이름이 빠져 있습니다.",
                        departments=departments,
                        evidence=(IssueEvidence(locus=sentence.locus, text=sentence.text),),
                    )
                )
    issues = [
        ConsistencyIssue(
            id=f"m-{_slug(names[label])}",
            category="missing_attachment",
            severity="high",
            title=f"'{names[label]}'이(가) 받은 파일 묶음에 없습니다",
            detail=(
                f"본문 {len(sentences)}곳에서 '{names[label]}'을(를) 참조하지만 그 첨부가 묶음에 없습니다. "
                "발주처에 요청해야 합니다."
            ),
            departments=departments,
            evidence=tuple(
                IssueEvidence(locus=s.locus, text=s.text, value=names[label])
                for s in sentences[:MAX_EVIDENCE]
            ),
        )
        for label, sentences in missing.items()
    ]
    unique_broken = list({issue.id: issue for issue in broken}.values())
    return issues + unique_broken, resolved


def _near(left: str, right: str) -> bool:
    """One substitution or one swap of neighbouring letters; digits are not involved (codes have none)."""
    if len(left) != len(right) or left == right:
        return False
    differ = [i for i, (a, b) in enumerate(zip(left, right, strict=True)) if a != b]
    if len(differ) == 1:
        return True
    return (
        len(differ) == 2
        and differ[1] == differ[0] + 1
        and left[differ[0]] == right[differ[1]]
        and left[differ[1]] == right[differ[0]]
    )


def _notation(parsed: Sequence[ParsedFile], departments: tuple[str, ...]) -> list[ConsistencyIssue]:
    first: dict[str, ParsedSentence] = {}
    counts: Counter[str] = Counter()
    for file in parsed:
        for sentence in file.sentences:
            for code in CODE.findall(sentence.text):
                counts[code] += 1
                first.setdefault(code, sentence)
    issues: list[ConsistencyIssue] = []
    used: set[str] = set()
    for code in sorted(counts, key=lambda c: (-counts[c], c)):
        if code in used:
            continue
        variants = [other for other in counts if other not in used and _near(code, other)]
        if not variants:
            continue
        cluster = [code, *sorted(variants)]
        used.update(cluster)
        issues.append(
            ConsistencyIssue(
                id=f"n-{_slug(code)}",
                category="notation",
                severity="low",
                title=f"같은 식별자가 다르게 적혀 있습니다 ({' ↔ '.join(cluster)})",
                detail="글자 하나가 다르거나 자리가 바뀐 표기입니다. 오타인지 다른 대상인지 확인해야 합니다.",
                departments=departments,
                evidence=tuple(
                    IssueEvidence(locus=first[c].locus, text=first[c].text, value=c)
                    for c in cluster[:MAX_EVIDENCE]
                ),
            )
        )
    return issues


def check_package(
    manifest: PackageManifest,
    parsed: Sequence[ParsedFile],
    clauses: Sequence[Clause],
    mentions: Sequence[EntityMention],
    ruleset: Ruleset,
) -> ConsistencyReport:
    """All consistency issues of a package and what was compared to find them."""
    owner = tuple(d.id for d in ruleset.departments if d.receives_all)[:1]
    conflicts, compared = _conflicts(mentions, ruleset, ())
    references, resolved = _references(manifest, parsed, owner)
    issues = conflicts + references + _notation(parsed, owner)

    known = {s.locus.key(): c.is_cost_sensitive for c in clauses for s in c.sentences}
    order = {s.locus.key(): i for i, s in enumerate(s for f in parsed for s in f.sentences)}
    finished = [
        issue.model_copy(
            update={
                "citation_status": "verified"
                if all(e.locus.key() in known for e in issue.evidence)
                else "unverified",
                # A position in no clause (a heading above its sub-clauses) cannot be shown to be
                # harmless, so it counts as cost-sensitive (fail closed, INV-8).
                "is_cost_sensitive": issue.is_cost_sensitive
                or any(known.get(e.locus.key(), True) for e in issue.evidence),
            }
        )
        for issue in issues
    ]
    finished.sort(
        key=lambda i: (CATEGORY_ORDER.index(i.category), order.get(i.evidence[0].locus.key(), 0), i.id)
    )
    return ConsistencyReport(
        issues=tuple(finished), files=len(manifest.files), entities=compared, references=resolved
    )
