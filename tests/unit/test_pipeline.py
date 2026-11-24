"""The pipeline end to end on a hand-made package, and scoring against an answer key."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from autorfx import scoring
from autorfx.analysis import RuleOnlyProvider, ScriptedProvider
from autorfx.analysis.usage import Price, Usage, usd_text
from autorfx.config import Settings
from autorfx.jobs import JobStore, load_job
from autorfx.pipeline import PipelineError, load_rfq, run_pipeline
from autorfx.rules import Ruleset
from autorfx.workspace import Workspace
from contract_schema import assert_valid
from factories import PACKAGE_SECRET, make_package

ARTIFACTS = ("clauses.json", "analysis.json", "mentions.json", "consistency.json", "cost_inputs.json")


def settings_in(folder: Path) -> Settings:
    return Settings(_env_file=None, data_dir=folder / "data")


def process(folder: Path, ruleset: Ruleset, sources: list[Path] | None = None) -> Workspace:
    return run_pipeline(
        sources or make_package(folder / "in"),
        name="Synthetic package",
        settings=settings_in(folder),
        ruleset=ruleset,
        provider=RuleOnlyProvider(),
        is_sample=False,
        now_ms=1_700_000_000_000,
    )


@pytest.fixture(scope="module")
def processed(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset):
    folder = tmp_path_factory.mktemp("pipeline")
    workspace = process(folder, ruleset)
    return workspace, load_rfq(workspace, ruleset)


def test_pipeline_stores_every_result(processed):
    """SPEC §4: clauses, analysis, consistency, cost inputs and masked sources are written."""
    workspace, data = processed
    assert [c.clause_id for c in data.clauses] == [
        "d01:0",
        "d01:1.1",
        "d01:2.1",
        "d01:2.2",
        "d01:2.3",
        "d02:1.1",
        "d02:1.2",
    ]
    assert [c.clause_id for c in data.clauses if c.is_cost_sensitive] == ["d01:2.2"]
    assert len(data.analyses) == len(data.clauses)
    assert all(a.summary.citation_status == "verified" for a in data.analyses), "INV-3"
    assert [i.id for i in data.consistency.issues] == ["c-incoterm"]
    assert_valid("cost-inputs", {"data": data.cost_inputs, "provenance": {"cost_inputs": "real"}})
    assert [f.clause_count for f in data.manifest.files] == [5, 2] and data.manifest.files[0].lang == "en"
    for name in ARTIFACTS:
        assert (workspace.root / name).is_file(), name
    assert workspace.masked_pdf("d01").is_file() and workspace.masked_pdf("d02").is_file()


def test_results_say_how_they_were_computed(processed, ruleset: Ruleset):
    """INV-10, INV-12: the engine and the ruleset are stored with the results, not looked up later."""
    workspace, data = processed
    assert data.engines == {
        "analysis": "rule-only",
        "translation": "none",
        "llm_connected": False,
        "retrieval": "bm25-glossary",
    }
    assert workspace.read_json(workspace.run_path)["ruleset_digest"] == ruleset.digest
    assert all(a.origin == "rule" and a.translation_ko is None for a in data.analyses)


def test_original_files_are_stored_read_only_and_the_text_is_kept(processed):
    """INV-1: the stored original cannot be written; hiding happens at the response, not in storage."""
    workspace, data = processed
    for file in data.manifest.files:
        assert not workspace.original_path(file.name).stat().st_mode & 0o222, "no write permission"
    assert PACKAGE_SECRET in " ".join(s.text for s in data.parsed["d01"].sentences)


def test_same_package_gives_the_same_results(tmp_path: Path, ruleset: Ruleset):
    """INV-7: two runs in different places produce identical ids, artifacts and masked files."""
    sources = make_package(tmp_path / "in")
    first = process(tmp_path / "a", ruleset, sources)
    second = process(tmp_path / "b", ruleset, sources)
    assert first.rfq_id == second.rfq_id
    for name in (*ARTIFACTS, "manifest.json", "run.json"):
        assert (first.root / name).read_bytes() == (second.root / name).read_bytes(), name
    for file_id in ("d01", "d02"):
        assert first.masked_pdf(file_id).read_bytes() == second.masked_pdf(file_id).read_bytes(), file_id
        assert first.canonical_pdf(file_id).read_bytes() == second.canonical_pdf(file_id).read_bytes()


def test_job_reports_every_stage(tmp_path: Path, ruleset: Ruleset):
    """JOB-01: each stage ends done, or skipped with the reason; the job is then done."""
    settings = settings_in(tmp_path)
    workspace = Workspace.for_rfq(settings.work_dir, "rfq-000000000000").ensure()
    job = JobStore(workspace, 1)
    job.start()
    run_pipeline(
        make_package(tmp_path / "in"),
        name="pkg",
        settings=settings,
        ruleset=ruleset,
        provider=RuleOnlyProvider(),
        is_sample=False,
        now_ms=1,
        reporter=job,
    )
    state = job.state
    assert state.done
    assert {s.id: s.status for s in state.stages} == {
        "store": "done",
        "canonical": "done",
        "parse": "done",
        "translate": "skipped",
        "route": "done",
        "assess": "done",
        "verify": "done",
        "consistency": "done",
    }
    by_id = {s.id: s.detail for s in state.stages}
    assert by_id["parse"] == "조항 7개, 문장 10개" and by_id["consistency"].startswith("이슈 1건 (값 충돌 1")
    assert "연결되지 않았습니다" in by_id["translate"]
    assert_valid("job", {"data": state.model_dump(mode="json"), "provenance": {"document": "real"}})


def test_unreadable_file_stops_the_job_at_its_stage(tmp_path: Path, ruleset: Ruleset):
    """JOB-02: a broken PDF fails the conversion stage; later stages never run on partial input."""
    broken = tmp_path / "in" / "broken.pdf"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"%PDF-1.7 this is not a document")
    settings = settings_in(tmp_path)
    workspace = Workspace.for_rfq(settings.work_dir, "rfq-000000000001").ensure()
    job = JobStore(workspace, 1)
    job.start()
    with pytest.raises(PipelineError, match="broken.pdf"):
        run_pipeline(
            [broken],
            name="broken",
            settings=settings,
            ruleset=ruleset,
            provider=RuleOnlyProvider(),
            is_sample=False,
            now_ms=1,
            reporter=job,
        )
    statuses = {s.id: s.status for s in job.state.stages}
    assert statuses["canonical"] == "error" and statuses["parse"] == "pending" and not job.state.done
    assert load_job(workspace) == job.state, "the failure is on disk"


# ── scoring ─────────────────────────────────────────────────────────────────
def write_key(folder: Path, sha256: str, **overrides: object) -> Path:
    key = {
        "dataset": "synthetic key v1",
        "package_sha256": sha256,
        "clauses": [
            {"file": "01_rfq.pdf", "section_id": "2.2", "primary_department": "costing", "priority": "must"},
            {"file": "01_rfq.pdf", "section_id": "2.3", "primary_department": "legal", "red_flag": None},
            {"file": "d02", "section_id": "1.2", "priority": "must", "red_flag": "unlimited_liability"},
            {"file": "02_logistics.pdf", "section_id": "9.9", "priority": "must"},
        ],
        "issues": ["c-incoterm", "m-annex-4"],
        **overrides,
    }
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "synthetic.json"
    path.write_text(json.dumps(key), encoding="utf-8")
    return path


def test_without_a_key_nothing_is_scored(processed, tmp_path: Path):
    """EVAL-02, INV-10: no key, no numbers."""
    _, data = processed
    result = scoring.evaluate(data.manifest, data.analyses, data.consistency.issues, None, now_ms=5)
    assert result["status"] == "not_scored" and "metrics" not in result
    assert_valid("evaluation", {"data": result, "provenance": {"evaluation": "real"}})
    assert scoring.find_key(data.manifest, tmp_path / "none") is None


def test_scoring_compares_labels_and_lists_the_misses(processed, tmp_path: Path):
    """EVAL-01: accuracy per label, P/R for red flags and issues, and each miss with what was expected."""
    _, data = processed
    write_key(tmp_path / "keys", data.manifest.sha256)
    key = scoring.find_key(data.manifest, tmp_path / "keys")
    result = scoring.evaluate(data.manifest, data.analyses, data.consistency.issues, key, now_ms=5)
    assert_valid("evaluation", {"data": result, "provenance": {"evaluation": "real"}})
    assert (result["status"], result["dataset"], result["scored_at"]) == ("scored", "synthetic key v1", 5)
    metrics = {m["key"]: (m["value"], m["support"]) for m in result["metrics"]}
    assert metrics["routing_accuracy"] == (0.5, 2), "costing is right, legal is not"
    assert metrics["priority_accuracy"] == (1.0, 2)
    assert metrics["red_flag_recall"] == (0.0, 1) and metrics["red_flag_precision"] == (1.0, 0)
    assert metrics["consistency_recall"] == (0.5, 2) and metrics["consistency_precision"] == (1.0, 1)
    assert metrics["citation_verified"] == (1.0, 7)
    errors = {(e["dimension"], e["ref"]): (e["expected"], e["actual"]) for e in result["errors"]}
    assert errors[("routing", "01_rfq.pdf §2.3")] == ("legal", "logistics")
    assert errors[("red_flag", "d02 §1.2")] == ("unlimited_liability", "없음")
    assert errors[("clause", "02_logistics.pdf §9.9")] == ("조항 있음", "조항을 찾지 못함")
    assert errors[("consistency", "m-annex-4")] == ("이슈 있음", "찾지 못함")


def test_a_restricted_team_sees_no_misses_about_hidden_clauses(processed, tmp_path: Path):
    """INV-8 (review M1): the error list names the judged department of each clause, so the rows of
    hidden clauses are left out for a restricted team; the metrics still count them."""
    from autorfx import access

    _, data = processed
    clauses = [
        {"file": "01_rfq.pdf", "section_id": "2.2", "primary_department": "legal"},
        {"file": "01_rfq.pdf", "section_id": "2.3", "primary_department": "legal"},
    ]
    write_key(tmp_path / "keys", data.manifest.sha256, clauses=clauses)
    key = scoring.find_key(data.manifest, tmp_path / "keys")
    withheld = access.withheld_ids(
        data.clauses, data.analyses, data.consistency.issues, group="cost_restricted"
    )
    assert "d01:2.2" in withheld
    restricted = scoring.evaluate(
        data.manifest, data.analyses, data.consistency.issues, key, now_ms=5, withheld=withheld
    )
    visible = scoring.evaluate(data.manifest, data.analyses, data.consistency.issues, key, now_ms=5)
    refs = {e["ref"] for e in restricted["errors"]}
    assert "01_rfq.pdf §2.2" not in refs and "01_rfq.pdf §2.3" in refs
    assert "01_rfq.pdf §2.2" in {e["ref"] for e in visible["errors"]}
    assert "costing" not in json.dumps(restricted, ensure_ascii=False)
    assert restricted["metrics"] == visible["metrics"]


def test_key_of_another_package_is_not_used(processed, tmp_path: Path):
    """EVAL-01: a key is matched by the package hash, never by position or file name."""
    _, data = processed
    write_key(tmp_path / "keys", "f" * 64)
    assert scoring.find_key(data.manifest, tmp_path / "keys") is None


def test_malformed_key_is_an_error_not_a_silent_skip(processed, tmp_path: Path):
    _, data = processed
    write_key(tmp_path / "keys", data.manifest.sha256, clauses=[{"file": "x"}])
    with pytest.raises(scoring.AnswerKeyError, match="형식"):
        scoring.find_key(data.manifest, tmp_path / "keys")


def test_held_out_keys_are_never_read(processed, tmp_path: Path):
    """INV-11: a folder of the evaluation set is not opened, whatever it contains."""
    _, data = processed
    write_key(tmp_path / "eval", data.manifest.sha256)
    assert scoring.find_key(data.manifest, tmp_path / "eval") is None


class _OneVectorEncoder:
    """Test double for an embedding model: every text maps to the same unit vector."""

    name = "test-encoder"

    def encode(self, texts):
        import numpy as np

        return np.ones((len(texts), 2), dtype=np.float32) / np.sqrt(2)


def test_served_engines_name_the_search_actually_used(processed, ruleset: Ruleset):
    """ENGINE-01, rule 6: results stored under BM25 but loaded with an embedding model are searched
    hybrid, so the engines served with them say so; without a model they still say BM25."""
    workspace, _ = processed
    stored = json.loads(workspace.run_path.read_text())["engines"]["retrieval"]
    assert stored == "bm25-glossary"
    hybrid = load_rfq(workspace, ruleset, encoder=_OneVectorEncoder())
    assert hybrid.index.dense is not None
    assert hybrid.engines["retrieval"] == "bm25-glossary+dense:test-encoder"
    assert load_rfq(workspace, ruleset).engines["retrieval"] == "bm25-glossary"


# ── estimated translation cost (INV-12) ─────────────────────────────────────
PRICES = {"gpt-x": Price(input=Decimal("0.10"), cached_input=Decimal("0.01"), output=Decimal("0.50"))}
ZERO_USAGE = {
    "calls": 0,
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "output_tokens": 0,
    "reasoning_tokens": 0,
}


def translated_run(folder: Path, ruleset: Ruleset, prices: dict[str, Price] | None) -> Workspace:
    provider = ScriptedProvider(
        translation="번역문",
        provider_id="openai:gpt-x",
        usage=Usage(calls=1, input_tokens=1000, cached_input_tokens=200, output_tokens=1000),
    )
    return run_pipeline(
        make_package(folder / "in"),
        name="Synthetic package",
        settings=settings_in(folder),
        ruleset=ruleset,
        provider=provider,
        is_sample=False,
        now_ms=1_700_000_000_000,
        prices=prices,
    )


def test_a_rule_only_run_records_no_usage_and_no_cost(processed):
    """TRANS-03, INV-12: nothing translated, nothing billed; every clause says no provider."""
    workspace, data = processed
    run = json.loads(workspace.run_path.read_text())
    assert run["translation"] == {
        "model": "none",
        "usage": ZERO_USAGE,
        "cost_usd": None,
        "counts": {"no_provider": len(data.clauses)},
    }


def test_a_translating_run_records_usage_and_the_estimated_cost(tmp_path: Path, ruleset: Ruleset):
    """INV-12: usage is summed over the run and priced with the model's own prices, as a plain string."""
    workspace = translated_run(tmp_path, ruleset, PRICES)
    record = json.loads(workspace.run_path.read_text())["translation"]
    calls = record["usage"]["calls"]
    assert calls > 0 and record["model"] == "openai:gpt-x"
    assert record["usage"]["output_tokens"] == 1000 * calls
    one_call = Decimal("800") * Decimal("0.10") + Decimal("200") * Decimal("0.01") + 1000 * Decimal("0.50")
    assert record["cost_usd"] == usd_text(one_call * calls / 1_000_000)
    assert sum(record["counts"].values()) == len(load_rfq(workspace, ruleset).clauses)


def test_without_prices_the_cost_is_null_but_usage_is_kept(tmp_path: Path, ruleset: Ruleset):
    """INV-12: an unknown price is null, never a guess."""
    record = json.loads(translated_run(tmp_path, ruleset, None).run_path.read_text())["translation"]
    assert record["cost_usd"] is None and record["usage"]["calls"] > 0


def test_an_old_run_without_the_cost_record_still_loads(tmp_path: Path, ruleset: Ruleset):
    """INV-10: results stored before the cost was recorded load, and the record is not served as an engine."""
    workspace = translated_run(tmp_path, ruleset, PRICES)
    run = json.loads(workspace.run_path.read_text())
    del run["translation"]
    workspace.run_path.write_text(json.dumps(run), encoding="utf-8")
    data = load_rfq(workspace, ruleset)
    assert set(data.engines) == {"analysis", "translation", "llm_connected", "retrieval"}
    assert data.engines["translation"] == "openai:gpt-x"
