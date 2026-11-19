"""Scoring against an answer key (EVAL-01, EVAL-02).

A key belongs to one package and is matched by the package hash, so results
are never scored against the key of another RFQ. Without a key the answer is
"not scored": no number is shown that was not measured (INV-10). Keys of the
held-out evaluation set are never read (INV-11).

Key format (proposal, to be agreed with the domain team, OQ-1):

    {
      "dataset": "S1 dev key v1",
      "package_sha256": "<manifest sha256>",
      "clauses": [
        {"file": "01_RFQ.docx", "section_id": "4.2",
         "primary_department": "costing", "priority": "must", "red_flag": null}
      ],
      "issues": ["c-thickness", "m-anlage-7"]
    }

A clause entry may leave out a label it does not judge. ``red_flag`` is a type
id from ``red_flags.yaml`` or null.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from pathlib import Path

from pydantic import Field, ValidationError

from autorfx import guard
from autorfx.models import ClauseAnalysis, ConsistencyIssue, Model, PackageManifest, Priority

MAX_ERRORS = 50
UNSET = "__unset__"
NOT_SCORED = "이 RFQ에 맞는 정답지가 없어 채점하지 않았습니다. 측정하지 않은 수치는 표시하지 않습니다."


class KeyClause(Model):
    file: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    primary_department: str | None = None
    priority: Priority | None = None
    red_flag: str | None = UNSET


class AnswerKey(Model):
    dataset: str = Field(min_length=1)
    package_sha256: str = Field(min_length=1)
    clauses: tuple[KeyClause, ...] = ()
    issues: tuple[str, ...] | None = None


class AnswerKeyError(ValueError):
    """An answer key exists for the package but cannot be used. The message is shown to the user."""


def find_key(manifest: PackageManifest, keys_dir: Path) -> AnswerKey | None:
    """The key whose ``package_sha256`` is this package's, or None."""
    if not keys_dir.is_dir() or guard.is_held_out(keys_dir):
        return None
    for path in sorted(keys_dir.glob("*.json")):
        if guard.is_held_out(path) or not guard.is_rfq_file(path):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AnswerKeyError(f"정답지 '{path.name}'을(를) 읽을 수 없습니다.") from exc
        if not isinstance(raw, dict) or raw.get("package_sha256") != manifest.sha256:
            continue
        try:
            return AnswerKey.model_validate(raw)
        except ValidationError as exc:
            raise AnswerKeyError(
                f"정답지 '{path.name}'의 형식이 맞지 않습니다: {exc.error_count()}곳"
            ) from exc
    return None


def _f1(found: set[str], expected: set[str]) -> tuple[float, float, float]:
    hits = len(found & expected)
    precision = hits / len(found) if found else 1.0
    recall = hits / len(expected) if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _metric(key: str, label: str, value: float, support: int, target: float | None = None) -> dict:
    return {"key": key, "label": label, "value": round(value, 4), "target": target, "support": support}


def evaluate(
    manifest: PackageManifest,
    analyses: Sequence[ClauseAnalysis],
    issues: Sequence[ConsistencyIssue],
    key: AnswerKey | None,
    *,
    now_ms: int,
    withheld: Collection[str] = (),
) -> dict:
    """The ``data`` object of ``contract/evaluation.schema.json``.

    ``withheld`` are clause and issue ids the requesting team may not see. They still count in the
    metrics, but their error rows (which name the judged department and red flag) are left out (INV-8).
    """
    if key is None:
        return {
            "status": "not_scored",
            "message": NOT_SCORED,
        }
    file_ids = {file.name: file.id for file in manifest.files} | {file.id: file.id for file in manifest.files}
    by_id = {analysis.clause_id: analysis for analysis in analyses}
    errors: list[dict] = []
    tallies = {"routing": [0, 0], "priority": [0, 0]}
    flagged_expected: set[str] = set()
    flagged_found: set[str] = set()
    judged_flags: set[str] = set()

    hidden = frozenset(withheld)

    def miss(dimension: str, ref: str, expected: object, actual: object, subject: str = "") -> None:
        if subject and subject in hidden:
            return
        errors.append({"dimension": dimension, "ref": ref, "expected": str(expected), "actual": str(actual)})

    for entry in key.clauses:
        ref = f"{entry.file} §{entry.section_id}"
        analysis = by_id.get(f"{file_ids.get(entry.file, entry.file)}:{entry.section_id}")
        if analysis is None:
            miss("clause", ref, "조항 있음", "조항을 찾지 못함")
            continue
        subject = analysis.clause_id
        for dimension, expected, actual in (
            ("routing", entry.primary_department, analysis.primary_department),
            ("priority", entry.priority, analysis.priority),
        ):
            if expected is None:
                continue
            tallies[dimension][1] += 1
            if expected == actual:
                tallies[dimension][0] += 1
            else:
                miss(dimension, ref, expected, actual, subject)
        if entry.red_flag != UNSET:
            judged_flags.add(analysis.clause_id)
            actual_flag = analysis.red_flag.type if analysis.red_flag else None
            if entry.red_flag:
                flagged_expected.add(analysis.clause_id)
            if actual_flag:
                flagged_found.add(analysis.clause_id)
            if (entry.red_flag or None) != actual_flag:
                miss("red_flag", ref, entry.red_flag or "없음", actual_flag or "없음", subject)

    metrics = [
        _metric(name, label, correct / total, total)
        for name, label, (correct, total) in (
            ("routing_accuracy", "주관 부서 정확도", tallies["routing"]),
            ("priority_accuracy", "요건 강도 정확도", tallies["priority"]),
        )
        if total
    ]
    if judged_flags:
        precision, recall, f1 = _f1(flagged_found, flagged_expected)
        metrics += [
            _metric("red_flag_precision", "독소조항 정밀도", precision, len(flagged_found)),
            _metric("red_flag_recall", "독소조항 재현율", recall, len(flagged_expected)),
            _metric("red_flag_f1", "독소조항 F1", f1, len(judged_flags)),
        ]
    if key.issues is not None:
        expected_issues, found_issues = set(key.issues), {issue.id for issue in issues}
        precision, recall, _ = _f1(found_issues, expected_issues)
        metrics += [
            _metric("consistency_precision", "정합성 이슈 정밀도", precision, len(found_issues)),
            _metric("consistency_recall", "정합성 이슈 재현율", recall, len(expected_issues)),
        ]
        for issue_id in sorted(expected_issues - found_issues):
            miss("consistency", issue_id, "이슈 있음", "찾지 못함", issue_id)
        for issue_id in sorted(found_issues - expected_issues):
            miss("consistency", issue_id, "이슈 없음", "이슈로 보고함", issue_id)
    verified = sum(analysis.summary.citation_status == "verified" for analysis in analyses)
    if analyses:
        metrics.append(
            _metric("citation_verified", "인용 검증 통과율", verified / len(analyses), len(analyses))
        )
    return {
        "status": "scored",
        "scored_at": now_ms,
        "dataset": key.dataset,
        "metrics": metrics,
        "errors": errors[:MAX_ERRORS],
    }
