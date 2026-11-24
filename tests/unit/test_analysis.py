"""Rule candidates, citation verification, providers and the clause graph."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from autorfx.analysis import (
    ClauseInput,
    Judgement,
    MemoryCache,
    OpenAIProvider,
    RuleOnlyProvider,
    ScriptedProvider,
    SqliteCache,
    TransientProviderError,
    analyze,
    cache_key,
    engine_ids,
    find_candidates,
    make_provider,
    verify,
)
from autorfx.analysis.citation import number_signatures
from autorfx.analysis.llm import SYSTEM, Translation, strip_sentence_marks
from autorfx.config import Settings
from autorfx.jobs import JobStore, load_job
from autorfx.models import Finding
from autorfx.rules import Ruleset, load_ruleset
from autorfx.workspace import Workspace
from factories import make_clause, make_locus

DELIVERY = make_clause(
    "4.1", ["4.1 Delivery is FCA supplier plant according to Incoterms 2020.", "Packaging is returnable."]
)
PRICE = make_clause(
    "4.3", ["4.3 The supplier shall grant an annual price reduction of 3 % from the second delivery year."]
)
GERMAN = make_clause(
    "5.2",
    ["5.2 Die Erstmuster sind spätestens 8 Wochen vor SOP vorzulegen.", "Der Prüfbericht ist beizufügen."],
    lang="de",
)
PLAIN = make_clause("9.9", ["9.9 Nothing here matches any rule at all."])


# ── citation enforcer ───────────────────────────────────────────────────────
def _finding(text: str, *numbers: int, section: str = "4.1") -> Finding:
    return Finding(
        kind="summary", text=text, citations=tuple(make_locus(n, section_id=section) for n in numbers)
    )


def test_finding_with_existing_citations_is_verified():
    """CITE-01: a finding that cites sentences of the clause is a normal result."""
    assert verify(_finding("", 1, 2), [DELIVERY]).citation_status == "verified"


def test_finding_without_citations_is_unverified():
    """CITE-02, INV-3: no citation, no normal result."""
    assert verify(_finding("text"), [DELIVERY]).citation_status == "unverified"


def test_citation_of_a_missing_position_is_unverified():
    """CITE-02: a position that does not exist is reported, not dropped."""
    result = verify(_finding("", 1, 7), [DELIVERY])
    assert result.citation_status == "unverified"
    assert [locus.sentence_index for locus in result.failed_citations] == [7]


def test_citation_outside_the_allowed_evidence_is_unverified():
    """CITE-02, INV-4: evidence the request was not allowed to use cannot support a finding."""
    allowed = {DELIVERY.sentences[0].locus.key()}
    assert verify(_finding("", 1), [DELIVERY], allowed=allowed).citation_status == "verified"
    assert verify(_finding("", 2), [DELIVERY], allowed=allowed).citation_status == "unverified"


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("연 3 % 인하를 요구한다.", "verified"),
        ("연 3,0 % 인하", "unverified"),
        ("연 5 % 인하를 요구한다.", "unverified"),
        ("단가 인하를 요구한다.", "verified"),
    ],
)
def test_generated_text_may_not_state_numbers_the_source_lacks(text: str, status: str):
    """CITE-02: with the number guard, every figure in the text must be in a cited sentence."""
    finding = _finding(text, 1, section="4.3")
    assert verify(finding, [PRICE], check_numbers=True).citation_status == status
    assert verify(finding, [PRICE]).citation_status == "verified"


# ── candidates ──────────────────────────────────────────────────────────────
def test_department_comes_from_keywords(ruleset: Ruleset):
    """ROUTE-01, ROUTE-03: the lead department is the best keyword match of the rule file."""
    delivery = find_candidates(DELIVERY, ruleset)
    assert delivery.primary == "logistics"
    price = find_candidates(PRICE, ruleset)
    assert price.primary == "costing"
    german = find_candidates(GERMAN, ruleset)
    assert german.primary == "quality"


def test_clause_without_any_keyword_goes_to_the_department_that_receives_everything(ruleset: Ruleset):
    """ROUTE-06: no keyword, no guess: the receives-all department leads and the clause needs confirmation."""
    plain = find_candidates(PLAIN, ruleset)
    assert (plain.primary, plain.reference, plain.needs_confirm) == ("sales", (), True)
    assert (plain.priority, plain.priority_marker) == ("info", None)
    assert plain.evidence == (1,)


def test_boilerplate_goes_to_its_department_without_confirmation(ruleset: Ruleset):
    """ROUTE-03: standard clauses are classified by rule and never sent to an LLM."""
    clause = make_clause("II.3", ["II.3 52.212-99 EXAMPLE The contractor shall comply."], is_boilerplate=True)
    found = find_candidates(clause, ruleset)
    assert (found.primary, found.needs_confirm) == ("legal", False)


@pytest.mark.parametrize(
    ("sentence", "priority"),
    [
        ("The supplier shall deliver weekly.", "must"),
        ("The supplier may not subcontract the coating.", "must"),
        ("Parts should be marked with the date code.", "should"),
        ("The supplier may propose an alternative material.", "optional"),
        ("This overview is for information only.", "info"),
        ("Die Teile sind nach Zeichnung zu fertigen.", "must"),
        ("Die Erstmuster sind bis 04.12.2026 vorzulegen.", "must"),
        ("Die Kapazität von 150.000 Sätzen hat der Lieferant vorzuhalten.", "must"),
        ("Die Stückzahlen sind Anlage 3 zu entnehmen.", "info"),
        ("Der Lieferant kann ein alternatives Material vorschlagen.", "optional"),
        ("Die Beschichtung darf nicht fremdvergeben werden.", "must"),
        ("Coating thickness ≥ 20 µm.", "must"),
    ],
)
def test_requirement_strength_from_expressions(ruleset: Ruleset, sentence: str, priority: str):
    """RTM-01: strength follows the rule file; prohibitions and German infinitive forms are obligations."""
    assert find_candidates(make_clause("1.1", [sentence]), ruleset).priority == priority


def test_strongest_sentence_decides_the_clause(ruleset: Ruleset):
    clause = make_clause("1.1", ["Parts should be marked.", "The supplier shall keep records."])
    found = find_candidates(clause, ruleset)
    assert (found.priority, found.priority_marker) == ("must", "shall")
    assert 2 in found.evidence


def test_red_flag_needs_the_pattern_and_no_cancelling_expression(ruleset: Ruleset):
    """RISK-01: a capped penalty is not flagged; an uncapped one is, with its sentence and reason."""
    open_ended = make_clause("8.1", ["8.1 Liquidated damages of 0.5 % per day apply to late delivery."])
    capped = make_clause("8.2", ["8.2 Liquidated damages apply but shall not exceed 5 % of the order value."])
    hit = find_candidates(open_ended, ruleset).red_flag
    assert hit is not None and (hit.type, hit.sentence) == ("unlimited_liquidated_damages", 1)
    assert "Liquidated damages" in hit.reason
    assert find_candidates(capped, ruleset).red_flag is None
    price = find_candidates(PRICE, ruleset)
    assert price.red_flag is not None and price.red_flag.type == "forced_cost_reduction"
    assert price.needs_confirm, "a red flag is always worth a second look"


# ── rule-only end to end ────────────────────────────────────────────────────
CLAUSES = (DELIVERY, PRICE, GERMAN, PLAIN)


def test_rule_only_analysis_is_complete_verified_and_reproducible(ruleset: Ruleset):
    """ENGINE-02, INV-3, INV-7: without an LLM every clause gets a verified, identical result, in order."""
    first = analyze(CLAUSES, ruleset, RuleOnlyProvider(), max_concurrency=4)
    second = analyze(CLAUSES, ruleset, RuleOnlyProvider(), max_concurrency=1)
    assert [a.model_dump_json() for a in first] == [a.model_dump_json() for a in second]
    assert [a.clause_id for a in first] == [c.clause_id for c in CLAUSES]
    for analysis in first:
        assert analysis.origin == "rule"
        assert analysis.summary.citation_status == "verified"
        assert analysis.summary.finding.citations
        assert analysis.summary.finding.text == "", "nothing wrote a summary, so there is none (INV-12)"
        assert analysis.translation_ko is None
    assert first[1].red_flag is not None and first[1].red_flag.label == "CR 강제"
    assert first[1].summary.finding.departments[0] == first[1].primary_department == "costing"


def test_progress_reports_every_stage(ruleset: Ruleset, workspace: Workspace):
    """UI-01, TRANS-03: translation is skipped with a reason when no provider translates."""
    store = JobStore(workspace, started_at=1)
    store.start()
    analyze(CLAUSES, ruleset, RuleOnlyProvider(), reporter=store)
    stages = {s.id: s for s in load_job(workspace).stages}  # type: ignore[union-attr]
    assert stages["translate"].status == "skipped" and "연결되지" in stages["translate"].detail
    assert [stages[s].status for s in ("route", "assess", "verify")] == ["done"] * 3
    assert "4/4" in stages["verify"].detail


# ── LLM path with a scripted provider ───────────────────────────────────────
GOOD = Judgement(
    primary_department="logistics",
    reference_departments=("costing", "nobody"),
    priority="should",
    summary="인도조건은 FCA이다.",
    citations=(1,),
)
BAD = GOOD.model_copy(update={"citations": (9,)})


def run_one(provider: ScriptedProvider, ruleset: Ruleset, clause=DELIVERY, **kwargs: object):
    return analyze([clause], ruleset, provider, policy="always", **kwargs)[0]  # type: ignore[arg-type]


def test_llm_result_is_validated_against_the_rule_files(ruleset: Ruleset):
    """ROUTE-03, CITE-01: unknown departments are dropped and sentence numbers become positions."""
    provider = ScriptedProvider({"d01:4.1": [GOOD]}, translation="번역문")
    result = run_one(provider, ruleset)
    assert (result.origin, result.primary_department, result.reference_departments) == (
        "llm",
        "logistics",
        ("costing",),
    )
    assert result.summary.citation_status == "verified"
    assert result.summary.finding.text == "인도조건은 FCA이다."
    assert result.summary.finding.citations == (DELIVERY.sentences[0].locus,)
    assert result.translation_ko == "번역문"
    assert provider.confirm_calls == [("d01:4.1", None)]


def test_failed_citation_is_retried_once_with_the_reason(ruleset: Ruleset):
    """CITE-02: a bad citation gets one retry that says what was wrong."""
    provider = ScriptedProvider({"d01:4.1": [BAD, GOOD]})
    result = run_one(provider, ruleset)
    assert (result.origin, result.summary.citation_status) == ("llm", "verified")
    assert [feedback is None for _, feedback in provider.confirm_calls] == [True, False]
    assert "between 1 and 2" in (provider.confirm_calls[1][1] or "")


def test_two_failures_fall_back_to_the_rule_result(ruleset: Ruleset):
    """INV-3: after the retry the rule result is used and labelled; the bad answer never shows as normal."""
    provider = ScriptedProvider({"d01:4.1": [BAD]})
    result = run_one(provider, ruleset)
    assert len(provider.confirm_calls) == 2
    assert (result.origin, result.summary.citation_status, result.summary.finding.text) == (
        "rule_fallback",
        "verified",
        "",
    )


def test_summary_with_a_figure_the_clause_lacks_is_rejected(ruleset: Ruleset):
    """CITE-02: a generated summary cannot introduce a number."""
    invented = GOOD.model_copy(update={"summary": "인도조건은 FCA이며 운임은 500 EUR이다."})
    result = run_one(ScriptedProvider({"d01:4.1": [invented]}), ruleset)
    assert result.origin == "rule_fallback"


def test_valid_red_flag_evidence_does_not_hide_a_nonexistent_citation(ruleset: Ruleset):
    """INV-3 (review MEDIUM): sentence 99 does not exist; that the red-flag evidence (1) does must not
    make the result look verified."""
    masked_bad = GOOD.model_copy(
        update={
            "citations": (1, 99),
            "red_flag_type": "forced_cost_reduction",
            "red_flag_reason": "단가 인하를 강제한다.",
            "red_flag_evidence": (1,),
        }
    )
    result = run_one(ScriptedProvider({"d01:4.1": [masked_bad]}), ruleset)
    assert result.origin == "rule_fallback" and result.summary.citation_status == "verified"


def test_a_red_flag_reason_with_a_figure_the_clause_lacks_is_rejected(ruleset: Ruleset):
    """CITE-02 (review MEDIUM): the reason is shown to the user, so its numbers are checked too."""
    invented = GOOD.model_copy(
        update={
            "red_flag_type": "unlimited_liquidated_damages",
            "red_flag_reason": "최대 500만 유로까지 책임진다.",
            "red_flag_evidence": (1,),
        }
    )
    result = run_one(ScriptedProvider({"d01:4.1": [invented]}), ruleset)
    assert result.origin == "rule_fallback"
    assert result.red_flag is None or "500" not in result.red_flag.reason


def test_a_translation_with_a_figure_the_clause_lacks_is_withheld(ruleset: Ruleset):
    """CITE-02 (review MEDIUM): a translation shown beside the original may not add numbers."""
    wrong = run_one(
        ScriptedProvider({"d01:4.1": [GOOD]}, translation="인도조건은 FCA, 운임 999 EUR."), ruleset
    )
    assert wrong.translation_ko is None
    right = run_one(
        ScriptedProvider({"d01:4.1": [GOOD]}, translation="인코텀즈 2020에 따른 FCA 인도."), ruleset
    )
    assert right.translation_ko == "인코텀즈 2020에 따른 FCA 인도."


def test_explicit_obligation_is_never_weakened(ruleset: Ruleset):
    """RTM-01: when the rule found "shall", an LLM saying "optional" does not lower the strength."""
    weaker = Judgement(
        primary_department="costing", priority="optional", summary="단가 인하.", citations=(1,)
    )
    result = run_one(ScriptedProvider({"d01:4.3": [weaker]}), ruleset, clause=PRICE)
    assert (result.origin, result.priority) == ("llm", "must")


def test_transient_provider_failure_does_not_fail_the_run(ruleset: Ruleset):
    """SPEC §11: one clause the provider cannot answer gets the rule result; the others are unaffected."""
    provider = ScriptedProvider({"d01:4.1": [TransientProviderError("timeout")], "d01:4.3": [GOOD]})
    results = analyze([DELIVERY, PRICE], ruleset, provider, policy="always")
    assert [r.origin for r in results] == ["rule_fallback", "llm"]
    assert all(r.summary.citation_status == "verified" for r in results)


def test_policy_decides_which_clauses_reach_the_llm(ruleset: Ruleset):
    """ENGINE-01: clear clauses are not sent out; "never" sends nothing; boilerplate is never sent."""
    clear = make_clause("2.1", ["2.1 The supplier shall hold a valid IATF 16949 certification."])
    assert not find_candidates(clear, ruleset).needs_confirm
    boiler = make_clause("II.1", ["II.1 52.212-99 TITLE The contractor shall comply."], is_boilerplate=True)
    for policy, expected in (
        ("ambiguous_only", ["d01:9.9"]),
        ("never", []),
        ("always", ["d01:2.1", "d01:9.9"]),
    ):
        provider = ScriptedProvider()
        analyze([clear, PLAIN, boiler], ruleset, provider, policy=policy)  # type: ignore[arg-type]
        assert sorted(c for c, _ in provider.confirm_calls) == expected


# ── cache ───────────────────────────────────────────────────────────────────
def test_verified_llm_results_are_cached_by_content(ruleset: Ruleset, tmp_path: Path):
    """INV-7: an unchanged clause is not sent again; an edited clause or an edited rule file is."""
    cache = SqliteCache(tmp_path / "cache" / "analysis.sqlite3")
    provider = ScriptedProvider({"d01:4.1": [GOOD]})
    first = run_one(provider, ruleset, cache=cache)
    again = run_one(provider, ruleset, cache=cache)
    assert len(provider.confirm_calls) == 1 and again == first

    moved = DELIVERY.model_copy(
        update={
            "sentences": tuple(
                s.model_copy(update={"locus": s.locus.model_copy(update={"page_number": 7})})
                for s in DELIVERY.sentences
            )
        }
    )
    relocated = run_one(provider, ruleset, clause=moved, cache=cache)
    assert len(provider.confirm_calls) == 1, "the key ignores positions"
    assert relocated.summary.finding.citations[0].page_number == 7, (
        "positions are re-attached from the clause"
    )

    edited = make_clause("4.1", ["4.1 Delivery is DDP buyer plant.", "Packaging is returnable."])
    run_one(provider, ruleset, clause=edited, cache=cache)
    assert len(provider.confirm_calls) == 2
    assert cache_key(DELIVERY, "p", ruleset.digest) != cache_key(DELIVERY, "p", "other-rules")
    assert cache_key(DELIVERY, "p", ruleset.digest) != cache_key(DELIVERY, "q", ruleset.digest)
    cache.close()


def test_rule_fallbacks_are_not_cached(ruleset: Ruleset):
    cache = MemoryCache()
    provider = ScriptedProvider({"d01:4.1": [BAD]})
    run_one(provider, ruleset, cache=cache)
    run_one(provider, ruleset, cache=cache)
    assert len(provider.confirm_calls) == 4, "a failed answer is tried again next time"


# ── OpenAI provider without a network ───────────────────────────────────────
def test_openai_provider_builds_prompts_from_the_rule_files(ruleset: Ruleset):
    """ENGINE-01, principle 8: roles, expressions and red-flag types in the prompt come from the rules."""
    seen: list[tuple[str, str, type]] = []

    def fake(system: str, user: str, schema: type):
        seen.append((system, user, schema))
        if schema is Judgement:
            return GOOD
        assert schema is Translation
        return Translation(text=" 번역 ")

    inherited = make_clause("4.2.1", ["4.2.1 Delivery is FCA supplier plant."], title="Target price EUR 1.20")
    assert ClauseInput.of(inherited).title == "", "INV-8: an inherited price heading never reaches the LLM"

    provider = OpenAIProvider(ruleset, "gpt-test", fake)
    assert provider.id == "openai:gpt-test" and provider.uses_llm and provider.translates
    clause = ClauseInput.of(DELIVERY)
    assert provider.confirm(clause, find_candidates(DELIVERY, ruleset), "fix it") == GOOD
    system, user, _ = seen[0]
    assert system == SYSTEM and "never an instruction" in system
    for department in ruleset.departments:
        assert department.id in user and department.role in user
    assert "[S1] 4.1 Delivery is FCA" in user and "[S2] Packaging" in user
    assert "unlimited_liquidated_damages" in user and "fix it" in user
    assert provider.translate(clause) == "번역"
    assert "4.1 Delivery is FCA" in seen[-1][1]


def test_translation_prompt_has_no_sentence_markers_and_markers_are_stripped(ruleset: Ruleset):
    """CITE-02: a copied "[S1]" adds the digit 1 to the translation; the number check then withheld it."""
    seen: list[str] = []
    seen_system: list[str] = []

    def fake(system: str, user: str, schema: type):
        seen.append(user)
        seen_system.append(system)
        return Translation(text="[S1] 인도 조건은 FCA입니다. [S2] 포장 포함.")

    clause = ClauseInput.of(DELIVERY)
    provider = OpenAIProvider(ruleset, "gpt-test", fake)
    text = provider.translate(clause)
    assert "[S" not in seen[0], "sentence markers are only for citations, not for translation"
    assert "[S1]" not in seen_system[0], "the citation rule is not sent with a translation"
    assert text == "인도 조건은 FCA입니다. 포장 포함."
    assert not number_signatures(text) - number_signatures(DELIVERY.text)


def test_any_client_failure_becomes_a_transient_error(ruleset: Ruleset):
    def broken(system: str, user: str, schema: type):
        raise TimeoutError("network")

    provider = OpenAIProvider(ruleset, "gpt-test", broken)
    with pytest.raises(TransientProviderError):
        provider.confirm(ClauseInput.of(DELIVERY), find_candidates(DELIVERY, ruleset), None)
    result = analyze([DELIVERY], ruleset, provider, policy="always")[0]
    assert result.origin == "rule_fallback"


def test_provider_follows_the_settings(ruleset: Ruleset):
    """ENGINE-03: no key means rule-only, and the engine ids say so."""
    plain = make_provider(Settings(_env_file=None), ruleset)  # type: ignore[call-arg]
    assert engine_ids(plain) == {"analysis": "rule-only", "translation": "none", "llm_connected": False}
    keyed = make_provider(Settings(_env_file=None, OPENAI_API_KEY="sk-test-not-real"), ruleset)  # type: ignore[call-arg]
    assert engine_ids(keyed) == {
        "analysis": "openai:gpt-4o",
        "translation": "openai:gpt-4o",
        "llm_connected": True,
    }


def test_clause_text_never_reaches_the_log(ruleset: Ruleset, caplog: pytest.LogCaptureFixture):
    """Security: logs carry ids and error types, not document text."""
    caplog.set_level(logging.DEBUG)
    provider = ScriptedProvider({"d01:4.1": [TransientProviderError("timeout")]})
    run_one(provider, ruleset)
    assert "d01:4.1" in caplog.text
    assert "FCA" not in caplog.text and "Packaging" not in caplog.text


def test_rule_edit_changes_the_result_without_code_changes(tmp_path: Path, ruleset: Ruleset):
    """ROUTE-03, principle 8: a keyword added to the rule file re-routes a clause."""
    import shutil

    from conftest import RULES_DIR

    rules = tmp_path / "rules"
    shutil.copytree(RULES_DIR, rules)
    path = rules / "departments.yaml"
    text = path.read_text(encoding="utf-8")
    marker = "      en: [terms and conditions,"
    assert marker in text
    path.write_text(
        text.replace(marker, "      en: [nothing here matches, terms and conditions,", 1), encoding="utf-8"
    )
    edited = load_ruleset(rules)
    assert find_candidates(PLAIN, ruleset).primary == "sales"
    assert find_candidates(PLAIN, edited).primary == "legal"


def _translate_to(ruleset: Ruleset, answer: str, clause=DELIVERY) -> str | None:
    provider = OpenAIProvider(ruleset, "gpt-test", lambda system, user, schema: Translation(text=answer))
    return provider.translate(ClauseInput.of(clause))


def test_marks_only_in_the_answer_are_removed_and_line_breaks_are_kept(ruleset: Ruleset):
    assert _translate_to(ruleset, "[S1]") is None
    assert _translate_to(ruleset, "인도는 FCA입니다.\n[S2] 포장 포함.") == "인도는 FCA입니다.\n포장 포함."


def test_a_mark_the_source_itself_contains_is_kept():
    """A connector named "[S1]" in the clause is a part name, not a citation mark."""
    source = "Use connector [S1] at the rear."
    assert (
        strip_sentence_marks("후면의 커넥터 [S1]을 사용합니다.", source) == "후면의 커넥터 [S1]을 사용합니다."
    )
    assert strip_sentence_marks("[S1] 후면의 커넥터 [S1]을 사용.", source).count("[S1]") == 2


def test_a_number_the_clause_lacks_is_still_withheld_after_marks_are_removed(ruleset: Ruleset):
    """CITE-02: removing the marks must not weaken the number check; an invented 999 is still caught."""

    def call(system: str, user: str, schema: type):
        return GOOD if schema is Judgement else Translation(text="[S1] 인도 조건은 FCA이며 999일 이내입니다.")

    result = run_one(OpenAIProvider(ruleset, "gpt-test", call), ruleset)
    assert result.translation_ko is None


def test_citation_marks_in_any_translation_are_removed_not_counted_as_numbers(ruleset: Ruleset):
    """CITE-02: the numbering "[S1]" is ours; it neither stays in the text nor hides the translation."""
    marked = run_one(
        ScriptedProvider({"d01:4.1": [GOOD]}, translation="[S1] 인코텀즈 2020에 따른 FCA 인도. [S2]"), ruleset
    )
    assert marked.translation_ko == "인코텀즈 2020에 따른 FCA 인도."
    invented = run_one(
        ScriptedProvider({"d01:4.1": [GOOD]}, translation="[S1] FCA 인도, 운임 999 EUR."), ruleset
    )
    assert invented.translation_ko is None, "a really invented number is still withheld"
