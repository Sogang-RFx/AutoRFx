"""Shared models, rule files, path guards, workspace and job state."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from autorfx import guard
from autorfx.config import Settings
from autorfx.jobs import INTERRUPTED, STAGE_IDS, JobStore, close_interrupted, load_job
from autorfx.models import Clause, Locus
from autorfx.rules import COST_FIELDS, RulesError, Ruleset, load_ruleset
from autorfx.workspace import Workspace
from conftest import CONTRACT_DIR, RULES_DIR
from factories import make_clause, make_locus, make_manifest, make_sentence


# ── models ──────────────────────────────────────────────────────────────────
def test_models_are_immutable_and_closed():
    """INV-9: unknown fields are an error, and a value cannot change after it is made."""
    locus = make_locus(3)
    with pytest.raises(ValidationError):
        locus.page_number = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Locus.model_validate({**locus.model_dump(), "extra": 1})


@pytest.mark.parametrize("field", ["page_number", "paragraph_index", "sentence_index"])
def test_locus_counts_from_one(field: str):
    """INV-2: a position that cannot exist is rejected, not stored."""
    with pytest.raises(ValidationError):
        Locus.model_validate({**make_locus().model_dump(), field: 0})


def test_clause_carries_its_position():
    """CHUNK-02, INV-2: every clause knows its file, page, section and first sentence."""
    clause = make_clause("II.G", ["First.", "Second."], page_number=7, first_sentence=4)
    assert (clause.source_doc, clause.page_number, clause.section_id, clause.sentence_index) == (
        "d01",
        7,
        "II.G",
        4,
    )
    assert clause.section_path == ("II", "II.G")
    with pytest.raises(ValidationError):
        Clause.model_validate({**clause.model_dump(), "sentences": []})


def test_public_sentence_drops_internal_fields():
    """INV-8: the response shape of a sentence has no internal judgement in it."""
    assert set(make_sentence("Text.").public().model_dump()) == {"locus", "text", "rects"}


# ── rule files ──────────────────────────────────────────────────────────────
def test_ruleset_loads_and_names_eight_departments(ruleset: Ruleset):
    """ROUTE-03: departments come from the rule file."""
    assert ruleset.department_ids == (
        "sales",
        "development",
        "quality",
        "production_engineering",
        "purchasing",
        "costing",
        "logistics",
        "legal",
    )
    assert [d.id for d in ruleset.departments if d.receives_all] == ["sales"]
    assert len(ruleset.digest) == 64


def test_unknown_team_is_restricted(ruleset: Ruleset):
    """RBAC-03: no team, or a team that is not in the rule file, never sees cost clauses."""
    assert ruleset.group_of("costing") == "cost_visible"
    assert ruleset.group_of("development") == "cost_restricted"
    for team in (None, "", "admin", "COSTING"):
        assert ruleset.group_of(team) == "cost_restricted"


def test_cost_fields_match_the_contract():
    """CONTRACT-01: the cost input names are the same on both sides."""
    schema = json.loads((CONTRACT_DIR / "cost-inputs.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["data"]["properties"]["candidates"]["items"]["properties"]["field"]["enum"]
    assert tuple(enum) == COST_FIELDS


def _copy_rules(tmp_path: Path) -> Path:
    target = tmp_path / "rules"
    shutil.copytree(RULES_DIR, target)
    return target


def test_editing_a_rule_file_changes_the_digest(tmp_path: Path, ruleset: Ruleset):
    """ROUTE-03: a rule change is picked up without a code change and invalidates cached analysis."""
    rules = _copy_rules(tmp_path)
    path = rules / "priority.yaml"
    text = path.read_text(encoding="utf-8")
    assert "        - shall\n" in text
    path.write_text(
        text.replace("        - shall\n", "        - shall\n        - has to\n", 1), encoding="utf-8"
    )
    edited = load_ruleset(rules)
    assert "has to" in edited.priority[0].expressions.en
    assert edited.digest != ruleset.digest


@pytest.mark.parametrize(
    ("file", "old", "new", "expected"),
    [
        ("red_flags.yaml", "'liquidated damages'", "'liquidated (damages'", "정규식"),
        ("entities.yaml", "department: logistics", "department: nobody", "없는 부서"),
        ("cost_model.yaml", "scales: [material, credit]", "scales: [steel, credit]", "없는 원가 분류"),
        ("entities.yaml", "cost_fields: [thickness]", "cost_fields: [gross_weight]", "알 수 없는 원가 입력"),
        ("priority.yaml", "- level: info", "- level: must", "priority.yaml"),
    ],
)
def test_broken_rule_file_is_refused_with_a_reason(
    tmp_path: Path, file: str, old: str, new: str, expected: str
):
    """SPEC §11: a malformed rule file stops the load and says what is wrong."""
    rules = _copy_rules(tmp_path)
    path = rules / file
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(RulesError, match=expected):
        load_ruleset(rules)


def test_missing_rule_file_is_refused(tmp_path: Path):
    rules = _copy_rules(tmp_path)
    (rules / "glossary.yaml").unlink()
    with pytest.raises(RulesError, match="glossary.yaml"):
        load_ruleset(rules)


# ── guards ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "path",
    ["data/raw/eval/S2/01.pdf", "data/raw/eval.zip", "/vault/261117/EVAL.ZIP", "data/raw/dev/eval_notes.md"],
)
def test_held_out_set_is_refused(path: str):
    """The evaluation RFQs are never read during development."""
    with pytest.raises(guard.GuardError):
        guard.assert_not_held_out(Path(path))


@pytest.mark.parametrize(
    "path",
    ["data/raw/dev/S1/01_Anfrage.docx", "data/raw/dev/CBP/Attachment E EVALUATION CRITERIA (2).pdf"],
)
def test_development_files_are_allowed(path: str):
    assert guard.assert_not_held_out(Path(path)) == Path(path)


def test_guide_files_are_not_rfqs():
    assert not guard.is_rfq_file(Path("dev/_SUMMARY.md"))
    assert not guard.is_rfq_file(Path("dev/README.md"))
    assert not guard.is_rfq_file(Path("dev/.DS_Store"))
    assert guard.is_rfq_file(Path("dev/RFQ#901936.doc"))


def test_writes_are_confined_to_the_work_folders(tmp_path: Path):
    """INV-1: nothing is written beside a source file."""
    work, cache, source = tmp_path / "work", tmp_path / "cache", tmp_path / "raw" / "dev"
    assert guard.assert_writable(work / "rfq-1" / "x.json", work, cache)
    with pytest.raises(guard.GuardError):
        guard.assert_writable(source / "RFQ.doc", work, cache)
    with pytest.raises(guard.GuardError):
        guard.assert_inside(work / ".." / "raw" / "x", work)


# ── workspace ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rfq_id", ["../etc", "a/b", "RFQ-1", "x", ""])
def test_workspace_refuses_ids_that_could_leave_the_work_folder(tmp_path: Path, rfq_id: str):
    with pytest.raises(guard.GuardError):
        Workspace.for_rfq(tmp_path, rfq_id)


def test_workspace_refuses_bad_file_ids_and_names(workspace: Workspace):
    with pytest.raises(guard.GuardError):
        workspace.canonical_pdf("../d01")
    assert workspace.original_path("../../evil.doc").parent == workspace.original_dir.resolve()


def test_workspace_round_trips_models(workspace: Workspace):
    """INV-7: what is stored is read back identical."""
    manifest = make_manifest()
    clauses = (make_clause("1", ["One."]), make_clause("2", ["Two.", "Three."], first_sentence=2))
    workspace.save_manifest(manifest)
    workspace.save_clauses(clauses)
    assert workspace.exists()
    assert workspace.manifest() == manifest
    assert workspace.clauses() == clauses
    assert not list(workspace.root.rglob("*.tmp"))


# ── job state ───────────────────────────────────────────────────────────────
def test_job_progress_is_stored_and_ordered(workspace: Workspace):
    """UI-01: stages are reported in pipeline order and persisted."""
    store = JobStore(workspace, started_at=1)
    assert [s.id for s in store.start().stages] == list(STAGE_IDS)
    store.update("store", "done", "파일 3개")
    store.update("canonical", "running")
    loaded = load_job(workspace)
    assert loaded is not None and not loaded.done
    assert [s.status for s in loaded.stages[:3]] == ["done", "running", "pending"]
    with pytest.raises(KeyError):
        store.update("nope", "done")


def test_failed_stage_stops_the_job(workspace: Workspace):
    """SPEC §11: a failure is shown at its stage; later stages are not marked done."""
    store = JobStore(workspace, started_at=1)
    store.start()
    store.fail("parse", "텍스트 레이어가 없습니다")
    state = load_job(workspace)
    assert state is not None and not state.done
    parse = next(s for s in state.stages if s.id == "parse")
    assert (parse.status, parse.detail) == ("error", "텍스트 레이어가 없습니다")
    assert all(s.status == "pending" for s in state.stages if s.id != "parse")


def test_job_left_running_by_a_dead_process_becomes_an_error(workspace: Workspace):
    store = JobStore(workspace, started_at=1)
    store.start()
    store.update("store", "done")
    store.update("canonical", "running")
    assert close_interrupted(workspace) is True
    state = load_job(workspace)
    assert state is not None
    assert next(s for s in state.stages if s.id == "canonical").detail == INTERRUPTED
    assert close_interrupted(workspace) is False


def test_finished_job_is_left_alone(workspace: Workspace):
    store = JobStore(workspace, started_at=1)
    store.start()
    store.finish()
    assert close_interrupted(workspace) is False
    assert load_job(workspace).done is True  # type: ignore[union-attr]


# ── settings ────────────────────────────────────────────────────────────────
def test_keys_are_optional_and_placeholders_do_not_count():
    """OPS-01, TRANS-03: no key means rule-based analysis and no translation, not a crash."""
    plain = Settings(_env_file=None)  # type: ignore[call-arg]
    assert plain.llm_connected is False and plain.deepl_api_key is None
    placeholder = Settings(_env_file=None, OPENAI_API_KEY="your_openai_api_key_here", DEEPL_API_KEY=" ")  # type: ignore[call-arg]
    assert placeholder.llm_connected is False and placeholder.deepl_api_key is None


def test_translation_model_and_effort_settings():
    """TRANS-03: the translation model and effort are optional; blank means unset, a bad effort is refused."""
    plain = Settings(_env_file=None)  # type: ignore[call-arg]
    assert plain.openai_translation_model is None and plain.openai_translation_effort is None
    blank = Settings(_env_file=None, OPENAI_TRANSLATION_MODEL="  ", OPENAI_TRANSLATION_EFFORT=" ")  # type: ignore[call-arg]
    assert blank.openai_translation_model is None and blank.openai_translation_effort is None
    chosen = Settings(_env_file=None, OPENAI_TRANSLATION_MODEL="gpt-x", OPENAI_TRANSLATION_EFFORT="none")  # type: ignore[call-arg]
    assert (chosen.openai_translation_model, chosen.openai_translation_effort) == ("gpt-x", "none")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, OPENAI_TRANSLATION_EFFORT="max")  # type: ignore[call-arg]


def test_translation_prices_live_next_to_the_rules(tmp_path: Path):
    """INV-12: the price file is data/translation/prices.yaml under the data folder."""
    settings = Settings(_env_file=None, AUTORFX_DATA_DIR=str(tmp_path))  # type: ignore[call-arg]
    assert settings.translation_prices_path == tmp_path / "translation" / "prices.yaml"


def test_secret_is_not_printed():
    settings = Settings(_env_file=None, OPENAI_API_KEY="sk-test-not-a-real-key")  # type: ignore[call-arg]
    assert settings.llm_connected is True
    assert "sk-test" not in repr(settings) and "sk-test" not in str(settings.model_dump())


def test_derived_folders_follow_the_data_dir(tmp_path: Path):
    settings = Settings(_env_file=None, AUTORFX_DATA_DIR=str(tmp_path))  # type: ignore[call-arg]
    assert settings.rules_dir == tmp_path / "rules"
    assert settings.work_dir == tmp_path / "work"
    assert settings.samples_dir == tmp_path / "raw" / "dev"
    assert not guard.is_held_out(settings.samples_dir.relative_to(tmp_path))


FAKE_KEY = "sk-FAKEsecretkey123"


def assert_no_secret(exc: ValidationError) -> None:
    shown = " ".join([str(exc), repr(exc), repr(exc.errors())])
    assert FAKE_KEY not in shown and FAKE_KEY[:5] not in shown


@pytest.mark.parametrize("effort", ["none", "max"], ids=["without-model", "invalid-value"])
def test_settings_errors_never_show_the_api_key(effort: str, monkeypatch: pytest.MonkeyPatch):
    """OPS-01: a validation error about the translation effort does not carry the API key."""
    with pytest.raises(ValidationError) as by_alias:
        Settings(_env_file=None, OPENAI_API_KEY=FAKE_KEY, OPENAI_TRANSLATION_EFFORT=effort)  # type: ignore[call-arg]
    assert_no_secret(by_alias.value)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENAI_TRANSLATION_EFFORT", effort)
    with pytest.raises(ValidationError) as by_env:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert_no_secret(by_env.value)


def test_effort_without_a_model_message_is_kept_and_a_bad_model_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
):
    """OPS-01: the message names both variables; an invalid model value still gives a normal error."""
    monkeypatch.setenv("OPENAI_TRANSLATION_EFFORT", "low")
    with pytest.raises(ValidationError, match="OPENAI_TRANSLATION_EFFORT needs OPENAI_TRANSLATION_MODEL"):
        Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, OPENAI_TRANSLATION_MODEL=["x"], OPENAI_TRANSLATION_EFFORT="low")  # type: ignore[call-arg]
