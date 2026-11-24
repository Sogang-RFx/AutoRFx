"""No account receives anything of a clause outside its scope (docs/auth-rbac.md §1, §2, §4; INV-4, INV-8).

The package (tests/isolation_package.py) gives every department a word of its own. Each data route is called
by each account and everything it returns is searched for the words of the others. The PDFs are in
test_isolation_pdf.py. Questions never contain a sentinel here, except where the test is about that: the
words of the question itself may be named as absent.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator

import pytest

from auth_helpers import (
    ACCOUNTS,
    COST_VISIBLE,
    FULL,
    LIMITED,
    Accounts,
    error_of,
    running_app,
    settings_in,
)
from autorfx.analysis import RuleOnlyProvider
from autorfx.pipeline import load_rfq, run_pipeline
from autorfx.rules import Ruleset
from contract_schema import assert_valid
from factories import precost_inputs
from isolation_package import (
    BARE_HEADING,
    BEYOND_TEXT,
    COST,
    FIFTH,
    FILES,
    FIRST_NAME,
    LAST_NAME,
    OWNED_HEADING,
    OWNERS,
    ROUTING,
    SHEET,
    foreign,
    make_isolation_package,
    own,
)
from scoped_pdf_helpers import unzipped

NO_COST = tuple(name for name in LIMITED if name not in COST_VISIBLE)
QUESTIONS = (
    "인증 조건은?",
    "포장과 용기 조건은?",
    "보증과 책임은?",
    "금형과 도장은?",
    "소재와 두께는?",
    "결제 조건은?",
    "목표가는 얼마인가요?",
    "납기는?",
    "염수분무 시험 시간은?",
    "audit certification",
    "packaging container",
    "salt spray",
    "press and stamping line",
    "what is the weather",
)
ISO = "certification:ISO9001"
Api = tuple[Accounts, str]


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Api]:
    root = tmp_path_factory.mktemp("isolation")
    settings = settings_in(root)
    with running_app(settings, ruleset) as accounts:
        rfq_id = accounts.upload(make_isolation_package(root / "in"))
        write_key(accounts, rfq_id)
        yield accounts, rfq_id


def write_key(accounts: Accounts, rfq_id: str) -> None:
    """An answer key that is wrong everywhere, so that every clause and issue is an error row."""
    manifest = json.loads((accounts.settings.work_dir / rfq_id / "manifest.json").read_text("utf-8"))
    names = {f["id"]: f["name"] for f in manifest["files"]}
    clauses = [
        {"file": names[doc], "section_id": section, "primary_department": "sales", "priority": "optional"}
        for doc, section in (clause_id.split(":", 1) for clause_id in ROUTING)
        if ROUTING[f"{doc}:{section}"][0] != "sales"
    ]
    clauses += [
        {"file": names["d04"], "section_id": "1.1", "primary_department": "legal"},
        {"file": names["d04"], "section_id": "9.9", "priority": "must"},
        {"file": names["d01"], "section_id": "9.9", "priority": "must"},
    ]
    key = {
        "dataset": "isolation key",
        "package_sha256": manifest["sha256"],
        "clauses": clauses,
        "issues": ["m-unseen-annex", "c-cpk"],
    }
    folder = accounts.settings.answer_keys_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "isolation.json").write_text(json.dumps(key), encoding="utf-8")


def data_of(accounts: Accounts, name: str, path: str, **params: str) -> dict:
    response = accounts[name].get(path, params=params)
    assert response.status_code == 200, (name, path, response.text)
    return response.json()["data"]


def ask(accounts: Accounts, name: str, rfq_id: str, question: str) -> dict:
    response = accounts[name].post(f"/rfqs/{rfq_id}/chat", json={"question": question})
    assert response.status_code == 200, response.text
    assert_valid("chat", response.json())
    return response.json()["data"]


def text_paths(rfq_id: str) -> list[str]:
    """Every GET route that returns text about the RFQ, with all its parameters."""
    base = f"/rfqs/{rfq_id}"
    filters = ("sales", "quality", "costing", "logistics", "unknown")
    return [
        "/meta",
        "/auth/me",
        "/rfqs",
        base,
        f"{base}/job",
        f"{base}/clauses",
        *(f"{base}/clauses?department={d}" for d in filters),
        f"{base}/rtm",
        f"{base}/consistency",
        f"{base}/evaluation",
        *(f"{base}/files/{f.id}/pages/1/sentences" for f in FILES),
        f"{base}/files/d09/source",
        f"{base}/files/d01/pages/9/sentences",
    ]


def everything(accounts: Accounts, rfq_id: str, name: str) -> dict[str, str]:
    """All text an account can get, by where it came from: bodies, headers, every part of the workbook."""
    client = accounts[name]
    out: dict[str, str] = {}
    for path in text_paths(rfq_id):
        response = client.get(path)
        assert response.status_code in (200, 404), (path, response.text)
        out[path] = response.text + "\n" + json.dumps(dict(response.headers))
    for question in QUESTIONS:
        response = client.post(f"/rfqs/{rfq_id}/chat", json={"question": question})
        assert response.status_code == 200, response.text
        out[f"chat {question}"] = response.text + json.dumps(dict(response.headers))
    workbook = client.get(f"/rfqs/{rfq_id}/rtm.xlsx")
    assert workbook.status_code == 200
    out["rtm.xlsx"] = unzipped(workbook.content) + json.dumps(dict(workbook.headers))
    return out


# ── preconditions ───────────────────────────────────────────────────────────
def test_the_package_is_routed_as_these_tests_assume(tmp_path, ruleset: Ruleset):
    """Precondition (green before the change): the rules judge every clause as ``ROUTING`` says and the
    sentinels stand where ``OWNERS`` says; otherwise everything below would prove nothing."""
    workspace = run_pipeline(
        make_isolation_package(tmp_path / "in"),
        name="isolation",
        settings=settings_in(tmp_path),
        ruleset=ruleset,
        provider=RuleOnlyProvider(),
        is_sample=False,
        now_ms=1,
    )
    data = load_rfq(workspace, ruleset)
    by_id = {a.clause_id: a for a in data.analyses}
    found = {
        c.clause_id: (
            by_id[c.clause_id].primary_department,
            by_id[c.clause_id].reference_departments,
            c.is_cost_sensitive,
        )
        for c in data.clauses
    }
    assert found == ROUTING
    # Who reads a word, derived from the routing and the rule files, equals the written table.
    for word, owners in OWNERS.items():
        holders = [c for c in data.clauses if word in c.text or (c.kind == "table" and word == c.title)]
        if word in (*BEYOND_TEXT, BARE_HEADING, FIRST_NAME, LAST_NAME):
            assert not holders, word
            continue
        (clause,) = holders
        primary, reference, cost = found[clause.clause_id]
        readers = {d for d in (primary, *reference) if d in LIMITED}
        if cost:
            readers &= set(COST_VISIBLE)
        assert readers == owners, word
    assert all(sum(w in OWNERS and n in OWNERS[w] for w in OWNERS) for n in LIMITED), "each owns a word"
    issues = {i.id: i for i in data.consistency.issues}
    assert set(issues) == {"c-salt_spray_hours", "m-annex-77"}
    conflict = issues["c-salt_spray_hours"]
    assert len(conflict.evidence) == 4 and FIFTH in conflict.detail
    owners = {found[f"d02:{e.locus.section_id}"][0] for e in conflict.evidence}
    assert owners == {"quality"}, "quality sees all four kept sentences, but not the fifth clause"
    title = next(c for c in data.clauses if c.clause_id == "d01:2.1").title
    assert OWNED_HEADING in title and found["d01:2.1"][0] == "logistics"
    assert BARE_HEADING in next(c for c in data.clauses if c.clause_id == "d01:3.1").title


def test_the_full_scope_receives_every_word(api: Api):
    """The search below can find a leak: the administrator and sales do receive every sentinel."""
    accounts, rfq_id = api
    for name in FULL:
        text = "\n".join(everything(accounts, rfq_id, name).values())
        for word in OWNERS:
            if word not in BEYOND_TEXT:
                assert word in text, (name, word)
        assert FIFTH in json.dumps(data_of(accounts, name, f"/rfqs/{rfq_id}/consistency"))


# ── text ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", LIMITED)
def test_nothing_of_another_department_is_in_any_response(api: Api, name: str):
    """INV-8, auth-rbac §4: bodies, headers, workbook parts, chat, evaluation, names, jobs, errors."""
    accounts, rfq_id = api
    for where, text in everything(accounts, rfq_id, name).items():
        leaked = [word for word in foreign(name) if word in text]
        assert not leaked, (name, where, leaked)


@pytest.mark.parametrize("name", LIMITED)
def test_a_department_receives_all_of_its_own(api: Api, name: str):
    """RBAC-01: the filter hides the others, not the account's own clauses and file names."""
    accounts, rfq_id = api
    texts = everything(accounts, rfq_id, name)
    for word in own(name):
        assert any(word in text for text in texts.values()), (name, word)
    for word in own(name):
        if word not in (FIRST_NAME, SHEET):
            assert word in texts[f"/rfqs/{rfq_id}/clauses"], (name, word)
            assert any(word in t for where, t in texts.items() if where.endswith("/sentences")), (name, word)


@pytest.mark.parametrize("name", ACCOUNTS)
def test_each_account_sees_exactly_the_clauses_of_its_scope(api: Api, name: str):
    """auth-rbac §2: the visible rows are the routed ones, every other clause is a lock, in order."""
    accounts, rfq_id = api
    items = data_of(accounts, name, f"/rfqs/{rfq_id}/clauses")["clauses"]
    rows = data_of(accounts, name, f"/rfqs/{rfq_id}/rtm")["rows"]
    assert len(items) == len(rows) == len(ROUTING)
    for (clause_id, (primary, reference, cost)), item, row in zip(ROUTING.items(), items, rows, strict=True):
        sees = name in FULL or (name in (primary, *reference) and (not cost or name in COST_VISIBLE))
        assert item["kind"] == row["kind"] == ("clause" if sees else "masked"), (name, clause_id)
        assert item["clause_id"] == row["clause_id"]
        if sees:
            assert item["clause_id"] == clause_id
            assert (item["primary_department"], tuple(item["reference_departments"])) == (primary, reference)
        else:
            assert set(item) == {"kind", "clause_id", "source_doc", "section_id"}


@pytest.mark.parametrize("name", LIMITED)
def test_sentences_are_those_of_the_visible_clauses(api: Api, name: str):
    """CITE-03: no heading of nobody, no sentence of another department, on any page."""
    accounts, rfq_id = api
    items = data_of(accounts, name, f"/rfqs/{rfq_id}/clauses")["clauses"]
    shown = {(i["source_doc"], i["section_id"]) for i in items if i["kind"] == "clause"}
    found = set()
    for file in FILES:
        page = data_of(accounts, name, f"/rfqs/{rfq_id}/files/{file.id}/pages/1/sentences")
        found |= {(s["locus"]["source_doc"], s["locus"]["section_id"]) for s in page["sentences"]}
    assert found == shown


@pytest.mark.parametrize("name", LIMITED)
def test_locked_rows_do_not_depend_on_the_filter(api: Api, ruleset: Ruleset, name: str):
    """INV-8: which rows are locked, their order and their ids are the same under every department filter."""
    accounts, rfq_id = api
    path = f"/rfqs/{rfq_id}/clauses"

    def locked(**params: str) -> list[dict]:
        return [i for i in data_of(accounts, name, path, **params)["clauses"] if i["kind"] == "masked"]

    plain = locked()
    assert plain
    for department in (*ruleset.department_ids, "unknown", ""):
        assert locked(department=department) == plain, department


@pytest.mark.parametrize("name", NO_COST)
def test_a_lock_does_not_say_why(api: Api, name: str):
    """auth-rbac §2: a cost clause and a clause of another department look the same."""
    accounts, rfq_id = api
    items = data_of(accounts, name, f"/rfqs/{rfq_id}/clauses")["clauses"]
    cost = next(i for i in items if i["clause_id"] == "d02:1.3")
    other = next(i for i in items if i["kind"] == "masked" and i["clause_id"] == "d01:1.5")
    assert cost == {"kind": "masked", "clause_id": "d02:1.3", "source_doc": "d02", "section_id": "1.3"}
    assert set(cost) == set(other) and COST not in json.dumps(items)


@pytest.mark.parametrize("name", [n for n in LIMITED if n != "purchasing"])
def test_opaque_ids_are_not_a_hash_anyone_can_compute(api: Api, name: str):
    """auth-rbac §4: the id of a front matter and of a sheet (its name) and of an issue is a keyed hash."""
    accounts, rfq_id = api
    items = data_of(accounts, name, f"/rfqs/{rfq_id}/clauses")["clauses"]
    issues = data_of(accounts, name, f"/rfqs/{rfq_id}/consistency")["issues"]
    opaque = [i["section_id"] for i in items if i["section_id"].startswith("hidden-")]
    assert len(opaque) == 2, "the front matter and the sheet"
    opaque += [i["id"] for i in issues if i["kind"] == "masked"]
    assert len(opaque) >= 3 and len(set(opaque)) == len(opaque)
    for plain in (f"d03:{SHEET}", SHEET, "d01:0", "0", "c-salt_spray_hours", "m-annex-77"):
        for algorithm in ("sha256", "sha1", "md5"):
            digest = hashlib.new(algorithm, plain.encode()).hexdigest()
            assert not any(digest[:10] in value for value in opaque), plain


def test_opaque_ids_are_stable(api: Api):
    """The same lock has the same id in every response, so the screen can keep its place."""
    accounts, rfq_id = api
    for path in ("clauses", "rtm", "consistency"):
        first = data_of(accounts, "quality", f"/rfqs/{rfq_id}/{path}")
        assert first == data_of(accounts, "quality", f"/rfqs/{rfq_id}/{path}")


# ── consistency ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", LIMITED)
def test_an_issue_is_shown_only_to_who_sees_all_its_evidence(api: Api, name: str):
    """auth-rbac §4: the missing annex is legal's; the conflict names five values and is nobody's: quality
    sees the four kept sentences, but the title and detail could name the fifth."""
    accounts, rfq_id = api
    response = accounts[name].get(f"/rfqs/{rfq_id}/consistency")
    assert_valid("consistency", response.json())
    issues = response.json()["data"]["issues"]
    shown = [i["id"] for i in issues if i["kind"] == "issue"]
    assert shown == (["m-annex-77"] if name == "legal" else [])
    assert len(issues) == 2
    assert [i["kind"] for i in issues] == sorted(i["kind"] for i in issues), "locks come last"
    for value in ("96 h", "240 h", "720 h", FIFTH, "salt_spray", "염수분무"):
        assert value not in json.dumps(issues, ensure_ascii=False), (name, value)


# ── chat ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", LIMITED)
def test_a_foreign_word_is_answered_like_a_word_nobody_wrote(api: Api, name: str):
    """INV-4: asking for a hidden word gives the same answer as asking for nonsense, so the index, the
    missing list and the suggestions do not tell whether the RFQ contains it."""
    accounts, rfq_id = api
    nonsense = "qqzzvvwwkk"
    for template in ("{}", "{} packaging", "certification and {}"):
        expected = ask(accounts, name, rfq_id, template.format(nonsense))
        for word in foreign(name):
            if " " in word or word in (FIRST_NAME, LAST_NAME):
                continue
            got = ask(accounts, name, rfq_id, template.format(word))
            assert json.loads(json.dumps(got).replace(word, nonsense)) == expected, (word, template)


def test_chat_answers_each_department_from_its_own_clauses(api: Api):
    """RAG-04: the same question, different evidence, always inside the scope."""
    accounts, rfq_id = api
    quality = ask(accounts, "quality", rfq_id, "audit certification")
    assert quality["status"] == "answered"
    logistics = ask(accounts, "logistics", rfq_id, "packaging container")
    assert logistics["status"] == "answered"
    for name, question in (("logistics", "audit certification"), ("quality", "safety packaging container")):
        assert ask(accounts, name, rfq_id, question)["answer"] is None, (name, question)
    for data, name in ((quality, "quality"), (logistics, "logistics")):
        items = data_of(accounts, name, f"/rfqs/{rfq_id}/clauses")["clauses"]
        shown = {(i["source_doc"], i["section_id"]) for i in items if i["kind"] == "clause"}
        cited = {(c["source_doc"], c["section_id"]) for c in data["answer"]["finding"]["citations"]}
        assert cited and cited <= shown


def test_suggestions_come_from_the_scope(api: Api):
    """INV-4: a topic is offered only when the account's own text covers it."""
    accounts, rfq_id = api
    offered = {n: set(ask(accounts, n, rfq_id, "weather")["suggestions"]) for n in (*LIMITED, "sales")}
    assert "포장" in offered["logistics"] and "인증" in offered["quality"]
    for name in ("quality", "costing", "purchasing", "development", "production_engineering"):
        assert "포장" not in offered[name], name
    for name in ("logistics", "legal", "purchasing", "development"):
        assert "인증" not in offered[name], name
    assert all("목표가" not in offered[n] and "결제" not in offered[n] for n in NO_COST)


class EveryTextEncoder:
    """Test double for an embedding model: every sentence is as close to every question as can be."""

    name = "test-everything"

    def encode(self, texts):
        import numpy as np

        return np.ones((len(texts), 2), dtype=np.float32) / np.sqrt(2)


class SpyChatModel:
    """Test double for a chat LLM: records the evidence it is given and repeats its first sentence."""

    id = "spy"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def plan(self, question, history, languages):
        from autorfx.chat.models import Plan

        return Plan(intent="lookup", question=question, queries=())

    def answer(self, question, evidence, *, overview, feedback):
        from autorfx.chat.models import Draft, Statement

        self.seen += [e.text for e in evidence]
        return Draft(
            answered=True,
            complete=True,
            statements=(Statement(text=evidence[0].text, citations=(evidence[0].number,)),),
        )


@pytest.fixture(scope="module")
def dense_api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Api]:
    """An app whose search is hybrid with an encoder that lets every sentence through the gate."""
    import autorfx.api.state as state_module

    root = tmp_path_factory.mktemp("dense")
    patch = pytest.MonkeyPatch()
    patch.setattr(state_module, "load_encoder", lambda *a, **k: EveryTextEncoder())
    try:
        with running_app(settings_in(root), ruleset) as accounts:
            yield accounts, accounts.upload(make_isolation_package(root / "in"))
    finally:
        patch.undo()


@pytest.mark.parametrize("name", LIMITED)
def test_embedding_search_stays_inside_the_scope(dense_api: Api, name: str):
    """INV-4: with embeddings every question finds something; it is never another department's."""
    accounts, rfq_id = dense_api
    assert "dense" in data_of(accounts, name, "/meta")["engines"]["retrieval"]
    answered = 0
    for question in QUESTIONS:
        data = ask(accounts, name, rfq_id, question)
        answered += data["status"] == "answered"
        leaked = [w for w in foreign(name) if w in json.dumps(data, ensure_ascii=False)]
        assert not leaked, (name, question, leaked)
    assert answered >= 5, "the search did answer, so there was something to leak"


@pytest.mark.parametrize("name", [*LIMITED, "sales"])
def test_the_language_model_is_given_evidence_of_the_scope_only(dense_api: Api, name: str):
    """INV-4: what is sent to the chat model is inside the scope, not only what comes back."""
    accounts, rfq_id = dense_api
    spy, before = SpyChatModel(), accounts.state.chat_model
    accounts.state.chat_model = spy
    try:
        for question in QUESTIONS:
            ask(accounts, name, rfq_id, question)
    finally:
        accounts.state.chat_model = before
    assert spy.seen, "the model was asked"
    sent = "\n".join(spy.seen)
    assert not [w for w in foreign(name) if name in LIMITED and w in sent]


# ── evaluation, names, lists ────────────────────────────────────────────────
@pytest.mark.parametrize("name", ACCOUNTS)
def test_evaluation_lists_the_misses_of_visible_clauses_only(api: Api, name: str):
    """EVAL-01, INV-8: the metrics count everything; a row (it names a department) is shown for a visible
    clause only, also when the key names a clause or an issue that was not found."""
    accounts, rfq_id = api
    response = accounts[name].get(f"/rfqs/{rfq_id}/evaluation")
    assert_valid("evaluation", response.json())
    data = response.json()["data"]
    full = data_of(accounts, "admin", f"/rfqs/{rfq_id}/evaluation")
    assert data["status"] == "scored" and data["metrics"] == full["metrics"]
    if name in FULL:
        assert data["errors"] == full["errors"] and len(data["errors"]) > 20
        return
    items = data_of(accounts, name, f"/rfqs/{rfq_id}/clauses")["clauses"]
    names = {f["id"]: f["name"] for f in data_of(accounts, "admin", f"/rfqs/{rfq_id}")["files"]}
    shown = {f"{names[i['source_doc']]} §{i['section_id']}" for i in items if i["kind"] == "clause"}
    expected = [
        e
        for e in full["errors"]
        if e["ref"] in shown
        or (name == "legal" and (e["dimension"], e["ref"]) == ("consistency", "m-annex-77"))
    ]
    assert expected and data["errors"] == expected, name


@pytest.mark.parametrize("name", LIMITED)
def test_a_file_without_a_visible_clause_has_no_name(api: Api, name: str):
    """auth-rbac §4: name ``파일 {id}``, no warning, no count; the package name follows its first file."""
    accounts, rfq_id = api
    response = accounts[name].get(f"/rfqs/{rfq_id}")
    assert_valid("document", response.json())
    document = response.json()["data"]
    full = {f["id"]: f for f in data_of(accounts, "admin", f"/rfqs/{rfq_id}")["files"]}
    assert full["d01"]["warnings"], "the attachment makes a warning: there is one to hide"
    for file, record in zip(FILES, document["files"], strict=True):
        assert record["id"] == file.id
        if name in file.visible_to:
            assert record["name"] == full[file.id]["name"]
        else:
            assert (record["name"], record["warnings"], record["clause_count"]) == (f"파일 {file.id}", [], 0)
    listed = next(r for r in data_of(accounts, name, "/rfqs")["rfqs"] if r["id"] == rfq_id)
    anonymous = f"RFQ {rfq_id[-6:]}"
    assert (document["name"] == anonymous) is (name not in FILES[0].visible_to)
    assert listed["name"] == document["name"] and listed["status"] == "done" and listed["file_count"] == 4


@pytest.mark.parametrize("name", LIMITED)
def test_progress_names_stages_and_nothing_else(api: Api, name: str):
    """auth-rbac §4: stage names and states; the detail (counts, file names) is blank."""
    accounts, rfq_id = api
    response = accounts[name].get(f"/rfqs/{rfq_id}/job")
    assert_valid("job", response.json())
    mine = response.json()["data"]["stages"]
    full = data_of(accounts, "sales", f"/rfqs/{rfq_id}/job")["stages"]
    assert any(stage["detail"] for stage in full)
    assert [{**stage, "detail": ""} for stage in full] == mine


def test_the_workbook_says_for_which_scope_it_was_made(api: Api, ruleset: Ruleset):
    """auth-rbac §4: a file that leaves the system says whose view it is."""
    accounts, rfq_id = api
    for name in LIMITED:
        parts = unzipped(accounts[name].get(f"/rfqs/{rfq_id}/rtm.xlsx").content)
        assert ruleset.department(name).label in parts, name
    top = {n: unzipped(accounts[n].get(f"/rfqs/{rfq_id}/rtm.xlsx").content) for n in ("sales", "legal")}
    assert top["sales"] != top["legal"]


BROKEN = ("manifest.json", "clauses.json", "analysis.json", "run.json")


@pytest.mark.parametrize("artifact", BROKEN)
def test_an_rfq_whose_results_cannot_be_read_has_no_name_for_a_limited_account(api: Api, artifact: str):
    """auth-rbac §4, fail closed: without the results nobody can tell whether the first file is visible."""
    accounts, rfq_id = api
    work = accounts.settings.work_dir
    copy_id = f"rfq-broken{BROKEN.index(artifact):06d}"
    shutil.copytree(work / rfq_id, work / copy_id)
    try:
        (work / copy_id / artifact).write_text("{ not json", encoding="utf-8")
        for name in LIMITED:
            response = accounts[name].get("/rfqs")
            assert response.status_code == 200, response.text
            assert_valid("rfq-list", response.json())
            listed = {r["id"]: r for r in response.json()["data"]["rfqs"]}
            assert listed[copy_id]["name"] == f"RFQ {copy_id[-6:]}", name
            assert FIRST_NAME not in json.dumps(listed[copy_id])
            if artifact == "clauses.json":
                failing = accounts[name].get(f"/rfqs/{copy_id}/clauses")
                assert error_of(failing) == (500, "corrupt_results")
        assert accounts["sales"].get("/rfqs").status_code == 200
    finally:
        for path in (work / copy_id / "original").iterdir():
            path.chmod(0o600)
        shutil.rmtree(work / copy_id)


def test_a_stale_rfq_is_listed_without_its_name_for_a_limited_account(api: Api):
    """auth-rbac §4: results of other rules are not served (409), and not used to show a name either."""
    accounts, rfq_id = api
    work = accounts.settings.work_dir
    copy_id = "rfq-stale0000001"
    shutil.copytree(work / rfq_id, work / copy_id)
    try:
        run = json.loads((work / copy_id / "run.json").read_text("utf-8"))
        (work / copy_id / "run.json").write_text(json.dumps({**run, "ruleset_digest": "0" * 64}))
        for name in (*LIMITED, "sales"):
            listed = {r["id"]: r for r in data_of(accounts, name, "/rfqs")["rfqs"]}
            assert listed[copy_id]["status"] == "stale"
            assert (listed[copy_id]["name"] == f"RFQ {copy_id[-6:]}") is (name in LIMITED), name
            response = accounts[name].get(f"/rfqs/{copy_id}/clauses")
            assert error_of(response) == (409, "stale_results")
            assert FIRST_NAME not in response.text
    finally:
        for path in (work / copy_id / "original").iterdir():
            path.chmod(0o600)
        shutil.rmtree(work / copy_id)


# ── pre-cost: the one exception ─────────────────────────────────────────────
def test_pre_cost_is_open_to_cost_departments_only(api: Api):
    """auth-rbac §2: sales, purchasing, costing and admin; everyone else is refused, with no content."""
    accounts, rfq_id = api
    body = {"rfq_id": rfq_id, "inputs": precost_inputs(), "resolved": {}}
    for name in ACCOUNTS:
        client = accounts[name]
        responses = [
            client.get(f"/rfqs/{rfq_id}/precost-setup"),
            client.get("/company"),
            client.get("/price-sheet/words"),
            client.post("/precost", json=body),
        ]
        if name in COST_VISIBLE:
            assert [r.status_code for r in responses] == [200] * 4, name
            continue
        columns = client.post(
            f"/rfqs/{rfq_id}/price-sheet/columns",
            json={"table": "d03:t1", "header_row": 0, "columns": {"no": 0}},
        )
        word = client.post(
            "/price-sheet/words", json={"role": "description", "text": "x", "action": "approve"}
        )
        for response in (*responses, columns, word):
            assert error_of(response) == (403, "forbidden"), (name, response.request.url.path)
            assert not [w for w in OWNERS if w in response.text]


def test_pre_cost_uses_the_whole_rfq_for_every_cost_department(api: Api):
    """auth-rbac §2, the exception: the same inputs, questions and evidence for each, so the decision does
    not depend on who asks; it does not extend to any other route."""
    accounts, rfq_id = api
    setups = {n: data_of(accounts, n, f"/rfqs/{rfq_id}/precost-setup") for n in COST_VISIBLE}
    assert setups["purchasing"] == setups["costing"] == setups["sales"] == setups["admin"]
    fields = {c["field"]: c for c in setups["purchasing"]["cost_inputs"]["candidates"]}
    cited = fields["target_price"]["evidence"]["finding"]["citations"][0]
    assert fields["target_price"]["value"] == "7.31"
    assert (cited["source_doc"], cited["section_id"]) == ("d02", "1.3"), "a clause purchasing cannot open"
    assert ISO in json.dumps(setups["purchasing"]), "asked because of a clause of quality"
    assert COST not in "\n".join(everything(accounts, rfq_id, "purchasing").values())


def remembered(accounts: Accounts) -> set[str]:
    return {a["key"] for a in data_of(accounts, "admin", "/company")["answers"]}


def test_only_an_administrators_answers_are_remembered(api: Api):
    """auth-rbac §2: for anyone else ``remember`` is accepted and not stored; the calculation works."""
    accounts, rfq_id = api
    checks = data_of(accounts, "admin", f"/rfqs/{rfq_id}/precost-setup")
    assert ISO in json.dumps(checks), "there is a fact question to answer"
    body = {
        "rfq_id": rfq_id,
        "inputs": precost_inputs(answers={ISO: "yes"}),
        "resolved": {},
        "remember": True,
    }
    assert remembered(accounts) == set()
    for name in COST_VISIBLE:
        if name == "admin":
            continue
        response = accounts[name].post("/precost", json=body)
        assert response.status_code == 200, response.text
        answers = {c["key"]: c["answer"] for c in response.json()["data"]["knockout"]["checks"]}
        assert answers[ISO] == "yes", "the answer is used"
        assert remembered(accounts) == set(), name
    assert accounts["admin"].post("/precost", json={**body, "remember": False}).status_code == 200
    assert remembered(accounts) == set()
    assert accounts["admin"].post("/precost", json=body).status_code == 200
    assert remembered(accounts) == {ISO}


def test_only_an_administrator_saves_the_company_settings(api: Api):
    """auth-rbac §2: cost departments read them."""
    accounts, _ = api
    company = data_of(accounts, "admin", "/company")
    body = {key: company[key] for key in ("settings", "profile", "prices")}
    assert_valid("company-request", body)
    for name in ACCOUNTS:
        response = accounts[name].put("/company", json=body)
        if name == "admin":
            assert response.status_code == 200, response.text
        else:
            assert error_of(response) == (403, "forbidden"), name
    for name in COST_VISIBLE:
        assert data_of(accounts, name, "/company")["settings"] == company["settings"]
