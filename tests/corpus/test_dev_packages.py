"""The real development packages, end to end (SC-001, SC-004, SC-011, SC-012).

These tests read ``data/raw/dev`` and are skipped where it is absent (the files
are not in the repository). They assert counts, ids and invariants only: no
sentence of a development RFQ is written into a test.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pymupdf
import pytest
from openpyxl import load_workbook

from autorfx import access
from autorfx.analysis import RuleOnlyProvider
from autorfx.config import Settings
from autorfx.export import rtm_workbook
from autorfx.ingest import list_samples
from autorfx.pipeline import RfqData, load_rfq, run_pipeline
from autorfx.retrieval import answer_question
from autorfx.rules import Ruleset

pytestmark = pytest.mark.corpus

QUESTIONS = ("목표가는 얼마인가요?", "단가", "price", "결제 조건", "payment terms", "납기", "보증 기간은?")
MIN_SECRET = 12


def process(dev_corpus: Path, folder: Path, ruleset: Ruleset) -> dict[str, RfqData]:
    settings = Settings(_env_file=None, data_dir=folder / "data")
    samples = list_samples(settings.model_copy(update={"data_dir": dev_corpus.parents[1]}))
    out = {}
    for sample in samples:
        workspace = run_pipeline(
            sample.files,
            name=sample.label,
            settings=settings,
            ruleset=ruleset,
            provider=RuleOnlyProvider(),
            is_sample=True,
            now_ms=1,
        )
        out[sample.id] = load_rfq(workspace, ruleset)
    return out


@pytest.fixture(scope="module")
def packages(
    dev_corpus: Path, tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset
) -> dict[str, RfqData]:
    found = process(dev_corpus, tmp_path_factory.mktemp("corpus"), ruleset)
    if not found:
        pytest.skip("data/raw/dev에 RFQ 폴더가 없습니다")
    return found


def test_every_development_package_is_processed_without_an_llm(packages: dict[str, RfqData]):
    """SC-001, SC-013, INV-3: all packages finish; every clause has a verified, rule-based result."""
    for name, data in packages.items():
        assert data.clauses and len(data.analyses) == len(data.clauses), name
        assert all(a.summary.citation_status == "verified" and a.origin == "rule" for a in data.analyses), (
            name
        )
        assert all(file.page_count >= 1 and file.clause_count >= 1 for file in data.manifest.files), name
        assert data.engines["llm_connected"] is False, name
        for file in data.manifest.files:
            assert data.workspace.masked_pdf(file.id).is_file(), (name, file.id, "masked source")


def test_positions_are_unique_and_inside_the_page(packages: dict[str, RfqData]):
    """INV-2: each sentence has one position of its own, and its rectangles lie on the page."""
    for name, data in packages.items():
        for file_id, parsed in data.parsed.items():
            keys = [s.locus.key() for s in parsed.sentences]
            assert len(keys) == len(set(keys)), (name, file_id)
            for sentence in parsed.sentences:
                assert sentence.rects, (name, file_id, sentence.locus)
                assert all(r.x + r.w <= 1.0001 and r.y + r.h <= 1.0001 for r in sentence.rects)


def test_nothing_hidden_reaches_a_restricted_team(packages: dict[str, RfqData], ruleset: Ruleset):
    """INV-4, INV-8, SC-004: no hidden sentence appears in any response, export, answer or masked PDF."""
    for name, data in packages.items():
        hidden_clauses = [c for c in data.clauses if c.is_cost_sensitive]
        if not hidden_clauses:
            continue
        # A hidden sentence that also stands inside visible text (e.g. the issuer's name) proves nothing.
        visible_text = "\n".join(s.text for c in data.clauses if not c.is_cost_sensitive for s in c.sentences)
        secrets = {
            s.text
            for c in hidden_clauses
            for s in c.sentences
            if len(s.text) >= MIN_SECRET and s.text not in visible_text
        }
        assert secrets, name
        group = "cost_restricted"
        rows = access.rtm_rows(data.clauses, data.analyses, group=group, ruleset=ruleset)
        workbook = rtm_workbook(
            rows, manifest=data.manifest, ruleset=ruleset, engines=data.engines, group=group
        )
        sheet = load_workbook(io.BytesIO(workbook)).active
        payloads = [
            access.clause_items(data.clauses, data.analyses, group=group, department=None, ruleset=ruleset),
            rows,
            access.issue_items(data.consistency.issues, group=group, clauses=data.clauses),
            [list(row) for row in sheet.iter_rows(values_only=True)],
            [
                answer_question(
                    q, index=data.index, clauses=data.clauses, group=group, provider=RuleOnlyProvider()
                )
                for q in QUESTIONS
            ],
        ]
        for file in data.manifest.files:
            own = [c for c in data.clauses if c.source_doc == file.id]
            parsed = data.parsed[file.id]
            for page in range(1, parsed.page_count + 1):
                payloads.append(access.sentences_for(parsed, own, group=group, page_number=page))
        blob = json.dumps(payloads, ensure_ascii=False, default=str)
        leaked = [secret for secret in secrets if json.dumps(secret, ensure_ascii=False)[1:-1] in blob]
        assert not leaked, (name, len(leaked))

        for file in data.manifest.files:
            keys = access.hidden_keys([c for c in data.clauses if c.source_doc == file.id])
            if not keys:
                continue
            with pymupdf.open(str(access.source_pdf(data.workspace, file, group=group))) as doc:
                pages = [" ".join(page.get_text().split()) for page in doc]
            for sentence in data.parsed[file.id].sentences:
                if sentence.locus.key() in keys and sentence.text in secrets:
                    flat = " ".join(sentence.text.split())
                    assert flat not in pages[sentence.locus.page_number - 1], (name, file.id)


def test_every_issue_has_verified_evidence(packages: dict[str, RfqData]):
    """SC-011, INV-3: whatever issues a package has, each one cites verified evidence."""
    for name, data in packages.items():
        for issue in data.consistency.issues:
            assert issue.citation_status == "verified" and issue.evidence, (name, issue.id)
        issue_ids = {issue.id for issue in data.consistency.issues}
        for candidate in data.cost_inputs["candidates"]:
            if candidate["issue_id"]:
                assert candidate["issue_id"] in issue_ids, (name, candidate["field"])


def test_processing_twice_gives_identical_artifacts(
    packages: dict[str, RfqData], dev_corpus: Path, tmp_path: Path, ruleset: Ruleset
):
    """SC-012, INV-7: a second run elsewhere writes the same bytes, including the masked sources."""
    again = process(dev_corpus, tmp_path, ruleset)
    for name, first in packages.items():
        second = again[name]
        assert first.manifest.id == second.manifest.id, name
        for artifact in ("clauses.json", "analysis.json", "consistency.json", "cost_inputs.json"):
            a, b = first.workspace.root / artifact, second.workspace.root / artifact
            assert a.read_bytes() == b.read_bytes(), (name, artifact)
        for file in first.manifest.files:
            a, b = first.workspace.masked_pdf(file.id), second.workspace.masked_pdf(file.id)
            assert a.read_bytes() == b.read_bytes(), (name, file.id)
