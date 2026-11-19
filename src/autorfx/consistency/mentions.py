"""Entity mentions (CONSIST-01): values of the entities in ``entities.yaml``, found in text and tables.

In running text a value is taken when it follows one of the entity's labels in
the same sentence. In a table the label is a column header (the value is the
cell below, and the row's first cell names the part) or the first cell of a row
(the value is the next cell). Codes of a closed vocabulary (Incoterms) are
matched as whole words, case-sensitively, so ordinary words are not mistaken
for them.

Every mention keeps the sentence it came from; a table mention's position is
its row with the cell's anchor.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from autorfx.consistency.normalize import NUMBER, multiplier_of, parse_dates, parse_number, plain
from autorfx.models import Clause, EntityMention, Locus, ParsedFile, Table
from autorfx.rules import EntityRule, Ruleset

WINDOW = 70
CURRENCY = r"EUR|USD|KRW|GBP|JPY|CNY|€|\$"
GRADE = re.compile(r"\b[A-Z]{1,4}\d{2,4}[A-Z]{0,3}\b")
PART = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{4,}$")


def _label_pattern(rule: EntityRule) -> re.Pattern[str] | None:
    labels = sorted(rule.labels.all(), key=len, reverse=True)
    if not labels:
        return None
    return re.compile("|".join(rf"(?<!\w){re.escape(label)}(?!\w)" for label in labels), re.IGNORECASE)


def _code_pattern(rule: EntityRule) -> re.Pattern[str] | None:
    return re.compile(r"\b(" + "|".join(map(re.escape, rule.codes)) + r")\b") if rule.codes else None


def _unit_factor(rule: EntityRule, unit: str | None) -> Decimal:
    if unit:
        for alias, factor in rule.units.items():
            if alias.casefold() == unit.casefold():
                return Decimal(factor)
    return Decimal(1)


def _quantity(rule: EntityRule, text: str, lang: str) -> tuple[str, str, str | None] | None:
    """(raw, normalized, unit) of the first value of the rule's kind in ``text``."""
    if rule.kind == "date":
        found = parse_dates(text, lang)
        return (text.strip()[:40], "|".join(found), None) if found else None
    if rule.kind == "percent":
        match = re.search(rf"({NUMBER})\s*%", text)
        value = parse_number(match[1], lang) if match else None
        return (match[0], plain(value), "%") if match and value is not None else None
    if rule.kind == "money":
        match = re.search(rf"({NUMBER})\s*(?:{CURRENCY})|(?:{CURRENCY})\s*({NUMBER})", text)
        value = parse_number(match[1] or match[2], lang) if match else None
        return (match[0], plain(value), None) if match and value is not None else None
    if rule.kind == "text":
        match = GRADE.search(text)
        return (match[0], match[0].casefold(), None) if match else None
    if rule.units:
        units = "|".join(re.escape(u) for u in sorted(rule.units, key=len, reverse=True))
        match = re.search(rf"({NUMBER})\s*({units})(?!\w)", text, re.IGNORECASE)
        value = parse_number(match[1], lang) if match else None
        if match and value is not None:
            return (match[0], plain(value * _unit_factor(rule, match[2])), rule.unit)
        return None
    match = re.search(rf"({NUMBER})", text)
    value = parse_number(match[1], lang) if match else None
    return (match[0], plain(value), rule.unit) if match and value is not None else None


def _from_sentence(rule: EntityRule, text: str, lang: str) -> tuple[str, str, str | None] | None:
    label = _label_pattern(rule)
    for pattern in rule.patterns.all():
        match = re.search(pattern, text, re.IGNORECASE)
        if match and "value" in match.groupdict():
            value = parse_number(match["value"], lang)
            if value is not None:
                unit = match.groupdict().get("unit")
                return (match[0], plain(value * _unit_factor(rule, unit)), rule.unit)
    if rule.kind == "code":
        codes = _code_pattern(rule)
        if codes is None or (label is not None and not label.search(text)):
            return None
        match = codes.search(text)
        return (match[0], match[0], None) if match else None
    if label is None:
        return None
    found = label.search(text)
    return _quantity(rule, text[found.end() : found.end() + WINDOW], lang) if found else None


def _cell(rule: EntityRule, cell: str, header: str) -> tuple[str, str, str | None] | None:
    """A table cell as a value. Spreadsheet numbers are written with a dot as decimal separator."""
    if rule.kind == "code":
        codes = _code_pattern(rule)
        match = codes.search(cell) if codes else None
        return (cell, match[0], None) if match else None
    if rule.kind in ("number", "money"):
        value = parse_number(cell, "en")
        if value is None:
            return _quantity(rule, cell, "en")
        return (cell, plain(value * multiplier_of(header)), rule.unit)
    return _quantity(rule, cell, "en")


def _from_table(rule: EntityRule, table: Table) -> list[tuple[Locus, str, str, str, str | None, str | None]]:
    label = _label_pattern(rule)
    if label is None:
        return []
    out = []
    for column, header in enumerate(table.header):
        if not label.search(header):
            continue
        for row, locus, anchors in zip(table.rows, table.row_loci, table.cell_anchors, strict=True):
            cell = row[column] if column < len(row) else ""
            found = _cell(rule, cell, header) if cell else None
            if found:
                part = row[0] if rule.scope == "part" and column and PART.match(row[0]) else None
                where = locus.model_copy(update={"anchor": anchors[column] or locus.anchor})
                out.append((where, " | ".join(c for c in row if c), *found, part))
    for row, locus, anchors in zip(table.rows, table.row_loci, table.cell_anchors, strict=True):
        if len(row) >= 2 and row[0] and row[1] and label.fullmatch(row[0].strip()):
            found = _cell(rule, row[1], row[0])
            if found:
                where = locus.model_copy(update={"anchor": anchors[1] or locus.anchor})
                out.append((where, " | ".join(c for c in row if c), *found, None))
    return out


def extract_mentions(
    parsed: Sequence[ParsedFile], clauses: Sequence[Clause], ruleset: Ruleset
) -> tuple[EntityMention, ...]:
    """Every value of every entity, in document order."""
    cost = {s.locus.key(): c.is_cost_sensitive for c in clauses for s in c.sentences}
    mentions: list[EntityMention] = []
    seen: set[tuple[str, tuple[str, int, int], str | None, str]] = set()

    def add(
        rule: EntityRule,
        locus: Locus,
        text: str,
        raw: str,
        normalized: str,
        unit: str | None,
        scope: str | None,
    ) -> None:
        key = (rule.id, locus.key(), locus.anchor, normalized)
        if key in seen:
            return
        seen.add(key)
        mentions.append(
            EntityMention(
                entity=rule.id,
                scope=scope,
                raw=raw,
                normalized=normalized,
                unit=unit,
                locus=locus,
                text=text,
                is_cost_sensitive=cost.get(locus.key(), True),  # in no clause: treated as cost (INV-8)
            )
        )

    tables = {(t.source_doc, t.section_id): [] for file in parsed for t in file.tables}
    for file in parsed:
        for table in file.tables:
            tables[(table.source_doc, table.section_id)].append(table)
    for clause in clauses:
        if clause.kind == "table":
            for table in tables.get((clause.source_doc, clause.section_id), []):
                for rule in ruleset.entities:
                    for locus, text, raw, normalized, unit, scope in _from_table(rule, table):
                        add(rule, locus, text, raw, normalized, unit, scope)
            continue
        for sentence in clause.sentences:
            for rule in ruleset.entities:
                found = _from_sentence(rule, sentence.text, clause.lang)
                if found:
                    add(rule, sentence.locus, sentence.text, *found, None)
    return tuple(mentions)
