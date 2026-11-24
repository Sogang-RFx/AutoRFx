"""The HTTP API on a hand-made package: contract shapes, access control, errors.

Every request is made by a logged-in account (docs/auth-rbac.md): ``sales`` has the full scope,
``costing`` and ``logistics`` see their own department, with and without cost clauses.
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from auth_helpers import (
    COOKIE,
    COST_VISIBLE,
    FULL,
    LIMITED,
    ORIGIN,
    PDF,
    Accounts,
    running_app,
    settings_in,
)
from auth_helpers import error_of as error
from autorfx.analysis import RuleOnlyProvider, ScriptedProvider
from autorfx.analysis.usage import Usage, usd_text
from autorfx.api import create_app
from autorfx.config import Settings
from autorfx.rules import RulesError, Ruleset
from contract_schema import assert_valid
from factories import PACKAGE_SECRET, make_package, precost_inputs

FULL_VIEW, VISIBLE, RESTRICTED = "sales", "costing", "logistics"
"""Everything; its own department with cost clauses; its own department without them."""
NO_COST = ("development", "quality", "production_engineering", "logistics", "legal")
TOPICS = """\
topics:
  - {intent: conflicts, label: 문서 간 불일치, patterns: [문서끼리]}
  - {intent: red_flags, label: 불리한 조항, patterns: [독소]}
  - {intent: overview, label: RFQ 요약, patterns: [RFQ 요약]}
"""
SECRETS = (PACKAGE_SECRET, "1.2345", "target price", "rejected")
Api = tuple[Accounts, str, list[Path]]


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Api]:
    root = tmp_path_factory.mktemp("api")
    settings = settings_in(root)
    make_package(settings.samples_dir / "synthetic-sample")
    (settings.data_dir / "chat").mkdir(parents=True)
    (settings.data_dir / "chat" / "topics.yaml").write_text(TOPICS, encoding="utf-8")
    sources = make_package(root / "upload")
    with running_app(settings, ruleset) as accounts:
        yield accounts, accounts.upload(sources), sources


def get(client: TestClient, path: str, schema: str | None = None, **params: str) -> dict:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    if schema:
        assert_valid(schema, body)
    return body


# ── intake ──────────────────────────────────────────────────────────────────
def test_meta_describes_departments_engines_samples_and_limits(api: Api, ruleset: Ruleset):
    """META-01, ENGINE-03: the client learns the departments and how analysis is computed.

    Who may see cost is not listed per team any more: ``/auth/me`` says it for the account itself.
    """
    data = get(api[0][RESTRICTED], "/meta", "meta")["data"]
    assert [d["id"] for d in data["departments"]] == list(ruleset.department_ids)
    assert len(data["departments"]) == 8
    assert [d["id"] for d in data["departments"] if d["receives_all"]] == ["sales"]
    assert "teams" not in data and "work_areas" not in data
    assert data["engines"] == {
        "analysis": "rule-only",
        "translation": "none",
        "retrieval": "bm25-glossary",
        "llm_connected": False,
    }
    # The development folder holds a package, but the API never lists it (SPEC §13.2).
    assert data["samples"] == []
    assert data["upload"]["accept"] == [".pdf", ".doc", ".docx", ".xlsx"]


def test_the_account_says_who_may_see_cost(api: Api):
    """RBAC-01: the group the web shows is the one of the account, from the rule files."""
    groups = {
        name: get(api[0][name], "/auth/me", "viewer")["data"]["viewer"]["group"] for name in (*FULL, *LIMITED)
    }
    assert {name for name, group in groups.items() if group == "cost_visible"} == set(COST_VISIBLE)
    assert len(groups) == 9


def test_upload_runs_the_job_and_the_document_is_served(api: Api):
    """PKG-01, JOB-01: files in, a finished job and a document with per-file facts out."""
    accounts, rfq_id, _ = api
    client = accounts[FULL_VIEW]
    job = get(client, f"/rfqs/{rfq_id}/job", "job")["data"]
    assert job["done"] and {s["status"] for s in job["stages"]} == {"done", "skipped"}
    document = get(client, f"/rfqs/{rfq_id}", "document")["data"]
    assert document["id"] == rfq_id and not document["is_sample"]
    assert [(f["id"], f["name"], f["clause_count"]) for f in document["files"]] == [
        ("d01", "01_rfq.pdf", 5),
        ("d02", "02_logistics.pdf", 2),
    ]


def test_the_same_package_is_not_analysed_twice(api: Api):
    """PKG-05: the id comes from the content; a second upload opens the stored results."""
    accounts, rfq_id, sources = api
    client = accounts[FULL_VIEW]
    files = [("files", (p.name, p.read_bytes(), PDF)) for p in reversed(sources)]
    body = client.post("/rfqs", files=files).json()
    assert_valid("upload", body)
    assert body["data"]["id"] == rfq_id and "저장된 결과" in body["data"]["notice"]


def test_packages_on_the_server_cannot_be_opened_through_the_api(api: Api):
    """SPEC §13.2: only uploaded files are analysed; a development-folder package is not reachable by id."""
    accounts, _, _ = api
    client = accounts[FULL_VIEW]
    for sample_id in ("synthetic-sample", "nope"):
        response = client.post("/rfqs", json={"file_names": [], "sample_id": sample_id})
        assert error(response) == (400, "invalid_request")
        assert "synthetic-sample" not in response.text


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("notes.txt", b"hello", "지원하지 않는 형식"),
        ("fake.pdf", b"PK\x03\x04 not a pdf", "확장자"),
        ("empty.pdf", b"", "빈 파일"),
        ("README.md", b"# readme", "RFQ가 아니"),
    ],
)
def test_files_that_are_not_rfqs_are_refused(api: Api, name: str, content: bytes, expected: str):
    """PKG-03: wrong type, wrong content, empty files and guide files are refused with the reason."""
    accounts, _, _ = api
    client = accounts[FULL_VIEW]
    response = client.post("/rfqs", files=[("files", (name, content, "application/octet-stream"))])
    assert error(response) == (400, "invalid_request") and expected in response.json()["error"]["message"]


def test_upload_name_cannot_leave_the_upload_folder(api: Api):
    """Security: a client path is reduced to its file name before anything is written."""
    accounts, _, _ = api
    client = accounts[FULL_VIEW]
    response = client.post("/rfqs", files=[("files", ("../../evil.txt", b"x", "text/plain"))])
    assert error(response) == (400, "invalid_request")
    state = accounts.state
    assert not list(state.settings.data_dir.parent.rglob("evil.txt"))
    assert not state.upload_root.exists() or not any(state.upload_root.iterdir()), "uploads are cleaned up"


MB = 1024 * 1024
PART_HEAD = (
    b'--B\r\nContent-Disposition: form-data; name="files"; filename="big.pdf"\r\n'
    b"Content-Type: application/pdf\r\n\r\n"
)


def _post_raw(app, chunks: list[bytes], headers: list[tuple[bytes, bytes]]) -> tuple[int, str, int]:
    """POST /rfqs straight to the ASGI app; returns status, error code and how many body chunks it read."""
    pulled = 0
    sent: list[dict] = []

    async def receive() -> dict:
        nonlocal pulled
        if pulled == len(chunks):
            return {"type": "http.disconnect"}
        pulled += 1
        return {"type": "http.request", "body": chunks[pulled - 1], "more_body": pulled < len(chunks)}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/rfqs",
        "raw_path": b"/rfqs",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8000"),
            (b"content-type", b"multipart/form-data; boundary=B"),
            *headers,
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body)["error"]["code"], pulled


@pytest.mark.parametrize("declare_length", [True, False], ids=["content-length", "chunked"])
def test_an_oversized_upload_is_refused_while_it_arrives(
    tmp_path: Path, ruleset: Ruleset, declare_length: bool
):
    """PKG-03, security: the size limit stops reading the body; the server never buffers an oversized
    upload (40 MB sent against a 1 file x 1 MB limit) before refusing it."""
    settings = settings_in(tmp_path, max_files=1, max_file_mb=1)
    chunks = [PART_HEAD, *([b"x" * MB] * 40), b"\r\n--B--\r\n"]
    headers = [(b"content-length", str(sum(map(len, chunks))).encode())] if declare_length else []
    with running_app(settings, ruleset, names=("sales",)) as accounts:
        session = accounts["sales"].cookies[COOKIE]
        who = [(b"cookie", f"{COOKIE}={session}".encode()), (b"origin", ORIGIN.encode())]
        status, code, pulled = _post_raw(accounts.app, chunks, [*headers, *who])
        upload_root = accounts.state.upload_root
    assert (status, code) == (413, "too_large")
    assert pulled <= (0 if declare_length else 4), f"read {pulled} of {len(chunks)} chunks before refusing"
    assert not upload_root.exists() or not any(upload_root.iterdir()), "nothing is left on disk"


@pytest.mark.parametrize(
    ("username", "expected"), [(None, (401, "unauthenticated")), ("logistics", (403, "forbidden"))]
)
def test_an_upload_that_is_not_allowed_is_refused_before_its_body_is_read(
    tmp_path: Path, ruleset: Ruleset, username: str | None, expected: tuple[int, str]
):
    """Security (auth-rbac §3): without a session, or the right to upload, no upload byte is taken."""
    settings = settings_in(tmp_path, max_files=1, max_file_mb=1)
    chunks = [PART_HEAD, *([b"x" * MB] * 4), b"\r\n--B--\r\n"]
    headers = [(b"origin", ORIGIN.encode())]
    with running_app(settings, ruleset, names=("logistics",)) as accounts:
        if username:
            headers.append((b"cookie", f"{COOKIE}={accounts[username].cookies[COOKIE]}".encode()))
        status, code, pulled = _post_raw(accounts.app, chunks, headers)
        upload_root = accounts.state.upload_root
    assert (status, code) == expected
    assert pulled == 0, "nothing of the body was read"
    assert not upload_root.exists() or not any(upload_root.iterdir())


def test_results_of_older_rules_are_not_served(tmp_path: Path, ruleset: Ruleset):
    """INV-8 (review open question): cost sensitivity follows the rules, so results computed under
    other rule files are refused until the package is analysed again; analysing again fixes it."""
    settings = settings_in(tmp_path)
    sources = make_package(tmp_path / "upload")
    with running_app(settings, ruleset, names=("sales",)) as accounts:
        rfq_id = accounts.upload(sources)
        assert accounts["sales"].get(f"/rfqs/{rfq_id}/clauses").status_code == 200
    changed = ruleset.model_copy(update={"digest": "0" * 64})
    with running_app(settings, changed, create=False) as accounts:
        client = accounts["sales"]
        assert error(client.get(f"/rfqs/{rfq_id}/clauses")) == (409, "stale_results")
        files = [("files", (p.name, p.read_bytes(), PDF)) for p in sources]
        again = client.post("/rfqs", files=files).json()["data"]
        assert again["id"] == rfq_id and again["notice"] == "", "a real new analysis, not the stored one"
        accounts.state.wait()
        assert client.get(f"/rfqs/{rfq_id}/clauses").status_code == 200


def test_a_spreadsheet_formula_in_a_name_is_written_as_text(tmp_path: Path, ruleset: Ruleset):
    """Security (review M2): a file name or title starting with "=" must not become a formula."""
    from autorfx.export import rtm_workbook
    from factories import make_manifest

    manifest = make_manifest().model_copy(update={"name": '=HYPERLINK("http://x","open")'})
    rows = [
        {
            "kind": "clause",
            "clause_id": "d01:1",
            "source_doc": "d01",
            "section_id": "1",
            "title": "=1+1",
            "primary_department": "sales",
            "reference_departments": [],
            "priority": "must",
            "red_flag": None,
            "page_number": 1,
            "sentence_index": 1,
            "anchor": "@SUM(A1)",
            "citation_status": "verified",
        }
    ]
    engines = {"analysis": "rule-only", "llm_connected": False}
    content = rtm_workbook(rows, manifest=manifest, ruleset=ruleset, engines=engines, group="cost_visible")
    sheet = load_workbook(io.BytesIO(content)).active
    cells = [cell for row in sheet.iter_rows() for cell in row if cell.value is not None]
    assert all(cell.data_type != "f" for cell in cells)
    assert any(str(cell.value).startswith("'=1+1") for cell in cells)


def test_a_request_for_another_host_is_refused(api: Api):
    """Security (review M3): DNS rebinding; the server answers only for its own host names."""
    client = api[0].anonymous()
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def keyed_settings(root: Path) -> Settings:
    return Settings(_env_file=None, data_dir=root / "data", OPENAI_API_KEY="sk-test-not-real")  # type: ignore[call-arg]


def test_a_key_without_a_price_file_stops_the_server_at_start(tmp_path: Path, ruleset: Ruleset):
    """INV-12: the price file is read when the app starts, so a missing file is not found mid-job."""
    app = create_app(keyed_settings(tmp_path), ruleset=ruleset, provider=RuleOnlyProvider())
    with pytest.raises(RulesError, match="prices.yaml"), TestClient(app):
        pass


def test_a_job_records_its_estimated_cost(tmp_path: Path, ruleset: Ruleset):
    """INV-12: the server passes the prices to the pipeline; run.json holds the usage and the cost."""
    settings = keyed_settings(tmp_path)
    prices = settings.translation_prices_path
    prices.parent.mkdir(parents=True)
    prices.write_text(
        'source: x\nchecked: "2026-11-24"\nmodels:\n  gpt-x:\n    input: "1"\n    cached_input: "1"\n'
        '    output: "1"\n',
        encoding="utf-8",
    )
    provider = ScriptedProvider(
        translation="번역문", provider_id="openai:gpt-x", usage=Usage(calls=1, output_tokens=1000)
    )
    with running_app(settings, ruleset, provider=provider) as accounts:
        rfq_id = accounts.upload(make_package(tmp_path / "upload"))
    run = json.loads((settings.work_dir / rfq_id / "run.json").read_text(encoding="utf-8"))
    calls = run["translation"]["usage"]["calls"]
    assert calls > 0 and run["translation"]["cost_usd"] == usd_text(Decimal(calls) / 1000)


def test_a_failed_job_is_reported_and_has_no_results(api: Api):
    """JOB-02: the job shows where it stopped; result endpoints say so instead of serving partial data."""
    accounts, _, _ = api
    client = accounts[FULL_VIEW]
    body = client.post("/rfqs", files=[("files", ("broken.pdf", b"%PDF-1.7 not a document", PDF))]).json()
    accounts.state.wait()
    rfq_id = body["data"]["id"]
    stages = {s["id"]: s for s in get(client, f"/rfqs/{rfq_id}/job", "job")["data"]["stages"]}
    assert stages["canonical"]["status"] == "error" and "broken.pdf" in stages["canonical"]["detail"]
    failed = client.get(f"/rfqs/{rfq_id}/clauses")
    assert error(failed) == (409, "job_failed")


def test_a_limited_account_gets_the_stage_of_a_failed_job_but_no_detail(api: Api):
    """auth-rbac §4: the stage detail and the 409 message may name a file, so a limited account gets
    the stage names and states only."""
    accounts, _, _ = api
    sources = [("files", ("secretname.pdf", b"%PDF-1.7 still not a document", PDF))]
    rfq_id = accounts[FULL_VIEW].post("/rfqs", files=sources).json()["data"]["id"]
    accounts.state.wait()
    for name in LIMITED:
        job = accounts[name].get(f"/rfqs/{rfq_id}/job")
        assert_valid("job", job.json())
        stages = {s["id"]: s for s in job.json()["data"]["stages"]}
        assert stages["canonical"]["status"] == "error" and stages["canonical"]["label"]
        assert all(stage["detail"] == "" for stage in stages.values()), name
        failed = accounts[name].get(f"/rfqs/{rfq_id}/clauses")
        assert error(failed) == (409, "job_failed")
        assert stages["canonical"]["label"] in failed.json()["error"]["message"]
        assert "secretname" not in job.text + failed.text, name


# ── analysis, per account ───────────────────────────────────────────────────
@pytest.mark.parametrize("name", [*FULL, *LIMITED])
def test_every_response_conforms_to_the_contract(api: Api, name: str):
    """CONTRACT-01, INV-9: the shapes the web validates are the shapes the backend returns."""
    accounts, rfq_id, _ = api
    client = accounts[name]
    group = "cost_visible" if name in COST_VISIBLE else "cost_restricted"
    assert get(client, f"/rfqs/{rfq_id}/clauses", "clauses")["data"]["group"] == group
    assert get(client, f"/rfqs/{rfq_id}/rtm", "rtm")["data"]["group"] == group
    assert get(client, f"/rfqs/{rfq_id}/consistency", "consistency")["data"]["group"] == group
    get(client, f"/rfqs/{rfq_id}/files/d01/pages/1/sentences", "sentences")
    get(client, f"/rfqs/{rfq_id}/evaluation", "evaluation")
    get(client, f"/rfqs/{rfq_id}", "document")
    get(client, f"/rfqs/{rfq_id}/job", "job")
    get(client, "/rfqs", "rfq-list")
    chat = client.post(f"/rfqs/{rfq_id}/chat", json={"question": "인도조건은?"})
    assert chat.status_code == 200
    assert_valid("chat", chat.json())
    # The delivery clauses are routed to logistics: an account that cannot see them has no evidence.
    answered = name in (*FULL, "logistics")
    assert chat.json()["data"]["status"] == ("answered" if answered else "no_evidence")


def test_cost_visible_account_sees_the_price_clause(api: Api):
    """RBAC-01: the cost department gets its price clause in full."""
    accounts, rfq_id, _ = api
    for name in (FULL_VIEW, VISIBLE):
        clauses = get(accounts[name], f"/rfqs/{rfq_id}/clauses")["data"]["clauses"]
        price = next(c for c in clauses if c["clause_id"] == "d01:2.2")
        assert price["kind"] == "clause" and PACKAGE_SECRET in price["excerpt"][0]["text"]
        assert price["primary_department"] == "costing" and price["origin"] == "rule"
    source = accounts[FULL_VIEW].get(f"/rfqs/{rfq_id}/files/d01/source")
    assert source.headers["content-type"] == PDF and source.headers["cache-control"] == "no-store"
    with pymupdf.open(stream=source.content, filetype="pdf") as doc:
        assert "1.2345" in doc[0].get_text()


def test_a_department_sees_its_clauses_and_a_lock_for_every_other(api: Api):
    """auth-rbac §2: own clauses (lead or reference) in full, the rest only as locked rows."""
    accounts, rfq_id, _ = api
    expected = {
        "sales": ["d01:0", "d01:1.1", "d01:2.1", "d01:2.2", "d01:2.3", "d02:1.1", "d02:1.2"],
        "development": ["d01:1.1"],
        "logistics": ["d01:2.1", "d01:2.3", "d02:1.1", "d02:1.2"],
        "legal": ["d01:2.3"],
        "costing": ["d01:2.2"],
        "quality": [],
        "purchasing": [],
        "production_engineering": [],
    }
    for name, own in expected.items():
        items = get(accounts[name], f"/rfqs/{rfq_id}/clauses")["data"]["clauses"]
        assert len(items) == 7, "every clause is a row, visible or locked"
        assert [c["clause_id"] for c in items if c["kind"] == "clause"] == own, name
    everything = get(accounts["sales"], f"/rfqs/{rfq_id}/clauses")
    assert get(accounts["admin"], f"/rfqs/{rfq_id}/clauses") == everything


@pytest.mark.parametrize("name", NO_COST)
def test_nothing_about_the_price_reaches_a_restricted_account(api: Api, name: str):
    """INV-8, SC-004: every endpoint is called as a restricted account and searched for the hidden text."""
    accounts, rfq_id, _ = api
    client = accounts[name]
    texts = [
        json.dumps(get(client, f"/rfqs/{rfq_id}{path}"), ensure_ascii=False)
        for path in ("/clauses", "/rtm", "/consistency", "/files/d01/pages/1/sentences", "", "/job")
    ]
    for department in ("costing", "sales", "quality"):
        filtered = get(client, f"/rfqs/{rfq_id}/clauses", department=department)
        texts.append(json.dumps(filtered, ensure_ascii=False))
    for question in (
        "목표가는 얼마인가요?",
        "price",
        "독소조항이 있나요?",
        "문서끼리 다른 값은?",
        "RFQ 요약",
    ):
        reply = client.post(f"/rfqs/{rfq_id}/chat", json={"question": question})
        assert reply.status_code == 200
        texts.append(reply.text)
    for question in ("target price", "rejected quotes", "1.2345"):
        # The words of the question itself may be named as absent; nothing else may come back.
        data = client.post(f"/rfqs/{rfq_id}/chat", json={"question": question}).json()["data"]
        assert data["status"] in ("restricted", "no_evidence") and data["answer"] is None
    source = client.get(f"/rfqs/{rfq_id}/files/d01/source")
    assert source.status_code == 200, source.text
    with pymupdf.open(stream=source.content, filetype="pdf") as doc:
        texts.append("".join(page.get_text() for page in doc))
    workbook = client.get(f"/rfqs/{rfq_id}/rtm.xlsx")
    sheet = load_workbook(io.BytesIO(workbook.content)).active
    texts.append(json.dumps([list(row) for row in sheet.iter_rows(values_only=True)], default=str))
    for text in texts:
        for secret in SECRETS:
            assert secret not in text, (name, secret)
    masked = get(client, f"/rfqs/{rfq_id}/clauses")["data"]["clauses"][3]
    assert masked == {"kind": "masked", "clause_id": "d01:2.2", "source_doc": "d01", "section_id": "2.2"}


def test_the_neighbours_of_a_hidden_sentence_stay_on_the_page(api: Api):
    """RBAC-02: the scoped page shows the visible sentences around the hidden one (as an image)."""
    accounts, rfq_id, _ = api
    client = accounts[RESTRICTED]
    found = get(client, f"/rfqs/{rfq_id}/files/d01/pages/1/sentences")["data"]["sentences"]
    assert [s["text"][:20] for s in found] == ["2.1 Delivery is FCA ", "2.3 The warranty per"]
    source = client.get(f"/rfqs/{rfq_id}/files/d01/source")
    assert source.headers["content-type"] == PDF and source.headers["cache-control"] == "no-store"
    with pymupdf.open(stream=source.content, filetype="pdf") as doc:
        page = doc[0]
        for rect in (r for sentence in found for r in sentence["rects"]):
            x, y = rect["x"] * page.rect.width, rect["y"] * page.rect.height
            clip = pymupdf.Rect(x, y, x + rect["w"] * page.rect.width, y + rect["h"] * page.rect.height)
            samples = page.get_pixmap(clip=clip, dpi=144).samples
            assert min(samples) < 90 and max(samples) > 240, "black text on a white ground"


def test_cost_screens_are_closed_to_restricted_accounts(api: Api):
    """RBAC-01, COST-04: company figures, the setup and the calculation are refused, not emptied."""
    accounts, rfq_id, _ = api
    body = {"rfq_id": rfq_id, "inputs": precost_inputs(), "resolved": {}}
    for name in NO_COST:
        client = accounts[name]
        assert error(client.get(f"/rfqs/{rfq_id}/precost-setup")) == (403, "forbidden")
        assert error(client.get("/company")) == (403, "forbidden")
        assert error(client.post("/precost", json=body)) == (403, "forbidden")
    for name in COST_VISIBLE:
        assert accounts[name].get(f"/rfqs/{rfq_id}/precost-setup").status_code == 200, name


# ── pre-cost ────────────────────────────────────────────────────────────────
def test_company_settings_round_trip_and_bad_rows_are_refused(api: Api):
    """Initial setup: the price table and profile are saved whole; a bad row names itself."""
    accounts, _, _ = api
    client = accounts["admin"]
    company = get(accounts[VISIBLE], "/company", "company")["data"]
    assert company["settings"]["discount_rate"] == "0.08"
    row = {
        "id": None,
        "category": "material",
        "name": "HC420LA",
        "unit": "kg",
        "price": "1.05",
        "currency": "EUR",
        "valid_until": "2099-12-31",
        "department": "purchasing",
    }
    body = {
        "settings": company["settings"],
        "profile": {**company["profile"], "certifications": ["IATF 16949"]},
        "prices": [row],
    }
    assert_valid("company-request", body)
    saved = client.put("/company", json=body)
    assert saved.status_code == 200, saved.text
    assert_valid("company", saved.json())
    stored = saved.json()["data"]
    assert stored["prices"][0]["id"].startswith("price-") and stored["profile"]["certifications"] == [
        "IATF 16949"
    ]
    bad = {**body, "prices": [{**row, "department": "nobody"}]}
    assert error(client.put("/company", json=bad)) == (400, "invalid_request")
    twice = {**body, "prices": [row, row]}
    assert error(client.put("/company", json=twice)) == (400, "invalid_request")


def test_setup_and_calculation_for_a_cost_visible_account(api: Api):
    """COST-01, COST-05: cited inputs from the RFQ, a computed minimum quote, a decision with its rule."""
    accounts, rfq_id, _ = api
    client = accounts[VISIBLE]
    setup = get(client, f"/rfqs/{rfq_id}/precost-setup", "precost-setup")["data"]
    by_field = {c["field"]: c for c in setup["cost_inputs"]["candidates"]}
    assert by_field["target_price"]["value"] == "1.2345"
    assert by_field["target_price"]["evidence"]["citation_status"] == "verified"
    assert [a["value"] for a in by_field["incoterm"]["alternatives"]] == ["FCA", "DDP"]
    assert setup["classification"]["type"] == "other", "one drawing is not enough to call it manufacturing"

    plain = {"rfq_id": None, "inputs": precost_inputs(target_price="2"), "resolved": {}}
    response = client.post("/precost", json=plain)
    assert response.status_code == 200, response.text
    assert_valid("precost", response.json())
    data = response.json()["data"]
    # 1 kg × 1 EUR × 1.015 × 1.08 = 1.0962; the minimum quote adds the 5 % minimum margin.
    assert (data["cost"]["piece_cost"], data["cost"]["min_quote"]) == ("1.0962", "1.1510")
    assert data["decision"]["rule"] == "score" and data["decision"]["score"] is not None

    result = client.post("/precost", json={**plain, "rfq_id": rfq_id}).json()["data"]
    assert (result["decision"]["status"], result["decision"]["rule"]) == ("not_applicable", "rfq_type")


@pytest.mark.parametrize(
    "change",
    [
        {"volumes": []},
        {"currency": "eur"},
        {"target_price": "-1"},
        {"annual_reduction_rate": "1"},
        {
            "items": [
                {
                    "label": "a",
                    "category": "steel",
                    "quantity": "1",
                    "unit": "kg",
                    "unit_price": "1",
                    "price_id": None,
                }
            ]
        },
        {"extra": 1},
        {"target_price": "1E+999999999"},
    ],
)
def test_invalid_cost_input_is_refused(api: Api, change: dict):
    """COST-01: no number is computed from an invalid input."""
    client = api[0][VISIBLE]
    body = {"rfq_id": None, "inputs": precost_inputs(**change), "resolved": {}}
    assert error(client.post("/precost", json=body)) == (422, "invalid_request")


# ── errors and headers ──────────────────────────────────────────────────────
def test_unknown_things_are_not_found(api: Api):
    accounts, rfq_id, _ = api
    client = accounts[RESTRICTED]
    assert error(client.get("/rfqs/rfq-doesnotexist/clauses")) == (404, "not_found")
    assert error(client.get("/rfqs/..%2F..%2Fetc/job")) == (404, "not_found")
    assert error(client.get(f"/rfqs/{rfq_id}/files/d99/source")) == (404, "not_found")
    assert error(client.get(f"/rfqs/{rfq_id}/files/d01/pages/99/sentences")) == (404, "not_found")
    assert error(client.get("/nowhere")) == (404, "not_found")
    assert error(client.get(f"/rfqs/{rfq_id}/files/d01/pages/x/sentences")) == (422, "invalid_request")


def test_chat_refuses_an_empty_question(api: Api):
    accounts, rfq_id, _ = api
    client = accounts[VISIBLE]
    empty = client.post(f"/rfqs/{rfq_id}/chat", json={"question": "  "})
    assert error(empty) == (422, "invalid_request")
    assert error(client.post(f"/rfqs/{rfq_id}/chat", json={})) == (422, "invalid_request")


# ── chat ────────────────────────────────────────────────────────────────────
TURN = {"question": "보증 기간은?", "answer": "36개월입니다."}


@pytest.fixture(scope="module")
def chat_api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Api]:
    """The same package, with a chat model that each test replaces."""
    root = tmp_path_factory.mktemp("chat-api")
    settings = settings_in(root)
    sources = make_package(root / "upload")
    with running_app(settings, ruleset) as accounts:
        yield accounts, accounts.upload(sources), sources


def script(accounts: Accounts, *drafts: object):
    """Queue drafts for the next question and return the model to inspect its calls."""
    from autorfx.chat.model import ScriptedChatModel

    model = ScriptedChatModel(plans=[], drafts=list(drafts))
    accounts.state.chat_model = model
    return model


def test_history_is_accepted_and_reaches_the_planner(chat_api: Api):
    """RAG-03, CONTRACT-01: a follow-up arrives with the conversation; a missing history is an empty one."""
    from autorfx.chat.models import Draft, Turn

    accounts, rfq_id, _ = chat_api
    client = accounts[FULL_VIEW]
    nothing = Draft(answered=False, complete=True, statements=())
    body = {"question": "그럼 그건 언제부터야?", "history": [TURN]}
    assert_valid("chat-request", body)
    model = script(accounts, nothing)
    response = client.post(f"/rfqs/{rfq_id}/chat", json=body)
    assert response.status_code == 200, response.text
    assert_valid("chat", response.json())
    ((question, history, _),) = model.plan_calls
    assert question == body["question"] and tuple(history) == (Turn(**TURN),)

    model = script(accounts, nothing)
    plain = client.post(f"/rfqs/{rfq_id}/chat", json={"question": "보증 기간은?"})
    assert plain.status_code == 200 and tuple(model.plan_calls[0][1]) == ()


def test_the_longest_allowed_history_is_accepted(chat_api: Api):
    """CONTRACT-01: six turns, 500 characters asked, 4000 answered."""
    from autorfx.chat.models import Draft

    accounts, rfq_id, _ = chat_api
    script(accounts, Draft(answered=False, complete=True, statements=()))
    body = {
        "question": "보증 기간은?",
        "history": [{"question": "q" * 500, "answer": "a" * 4000}] * 6,
    }
    assert_valid("chat-request", body)
    assert accounts[FULL_VIEW].post(f"/rfqs/{rfq_id}/chat", json=body).status_code == 200


@pytest.mark.parametrize(
    "history",
    [
        "보증",
        [["q", "a"]],
        [{"answer": "a"}],
        [{"question": "", "answer": "a"}],
        [{"question": "q" * 501, "answer": "a"}],
        [{"question": "q", "answer": "a" * 4001}],
        [{"question": "q", "answer": 1}],
        [{**TURN, "role": "system"}],
        [TURN] * 7,
    ],
    ids=["text", "list", "no-question", "empty", "long-q", "long-a", "number", "extra", "seven"],
)
def test_a_malformed_history_is_refused(api: Api, history: object):
    """§5, INV-9: checked at the boundary; more than six turns is malformed too."""
    accounts, rfq_id, _ = api
    body = {"question": "보증 기간은?", "history": history}
    assert error(accounts[FULL_VIEW].post(f"/rfqs/{rfq_id}/chat", json=body)) == (422, "invalid_request")


def test_a_generated_answer_is_served_with_its_citations(chat_api: Api):
    """CITE-01, INV-12: the API passes the model's answer through the same checks."""
    from autorfx.chat.models import Draft, Statement

    accounts, rfq_id, _ = chat_api

    class Reader:
        """Cites the warranty sentence by the number it was given, whatever that is."""

        id = "reader"

        def plan(self, question, history, languages):
            from autorfx.chat.models import Plan

            return Plan(intent="lookup", question=question, queries=())

        def answer(self, question, evidence, *, overview, feedback):
            number = next(e.number for e in evidence if "36 months" in e.text)
            statement = Statement(text="보증 기간은 36개월입니다.", citations=(number,))
            return Draft(answered=True, complete=True, statements=(statement,))

    accounts.state.chat_model = Reader()
    reply = accounts[RESTRICTED].post(f"/rfqs/{rfq_id}/chat", json={"question": "보증 기간은?"})
    assert_valid("chat", reply.json())
    data = reply.json()["data"]
    assert (data["status"], data["mode"]) == ("answered", "generative")
    assert data["statements"][0]["citations"][0]["source_doc"] == "d01"


def test_stored_results_answer_without_a_model(api: Api):
    """INV-12: the delivery terms contradict each other; the consistency check found it, no LLM did."""
    accounts, rfq_id, _ = api
    client = accounts[FULL_VIEW]
    issues = get(client, f"/rfqs/{rfq_id}/consistency")["data"]["issues"]
    assert issues, "the delivery terms contradict each other"
    reply = client.post(f"/rfqs/{rfq_id}/chat", json={"question": "문서끼리 다른 값은?"})
    assert_valid("chat", reply.json())
    data = reply.json()["data"]
    assert (data["status"], data["mode"]) == ("answered", "stored")
    assert {s["text"] for s in data["statements"]} == {i["title"] for i in issues}


def test_the_chat_model_follows_settings_and_injection(tmp_path: Path, ruleset: Ruleset):
    """§5, ENGINE-03: an injected provider means no chat model unless one is injected too."""
    from autorfx.chat.model import ScriptedChatModel

    prices = tmp_path / "data" / "translation" / "prices.yaml"
    prices.parent.mkdir(parents=True, exist_ok=True)
    prices.write_text(
        'checked: "2026-11-24"\nmodels:\n  gpt-4o:\n'
        '    input: "2.50"\n    cached_input: "1.25"\n    output: "10.00"\n',
        encoding="utf-8",
    )
    keyed = Settings(_env_file=None, data_dir=tmp_path / "data", OPENAI_API_KEY="sk-test-not-real")  # type: ignore[call-arg]
    plain = Settings(_env_file=None, data_dir=tmp_path / "data", OPENAI_API_KEY="")  # type: ignore[call-arg]
    scripted = ScriptedChatModel(plans=[], drafts=[])
    for settings, options, expected in (
        (keyed, {"provider": RuleOnlyProvider()}, None),
        (plain, {}, None),
        (keyed, {}, "openai:gpt-4o"),
        (plain, {"provider": RuleOnlyProvider(), "chat_model": scripted}, scripted.id),
    ):
        with TestClient(create_app(settings, ruleset=ruleset, **options)) as client:
            model = client.app.state.autorfx.chat_model
        assert (model.id if model is not None else None) == expected
    assert plain.chat_topics_path == tmp_path / "data" / "chat" / "topics.yaml"


def test_security_headers_and_cors(api: Api):
    client = api[0].anonymous(origin=None)
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = client.get("/health", headers={"Origin": "https://example.org"})
    assert "access-control-allow-origin" not in other.headers


def test_a_keyless_server_creates_no_translation_cache(tmp_path: Path, ruleset: Ruleset):
    """TRANS-03: without a translating provider no translation.sqlite is created; with one it is."""
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")  # type: ignore[call-arg]
    keyless = create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())
    with TestClient(keyless):
        assert keyless.state.autorfx.translation_cache is None
    assert not (settings.cache_dir / "translation.sqlite").exists()
    translating = create_app(settings, ruleset=ruleset, provider=ScriptedProvider(translation="번역문"))
    with TestClient(translating):
        state = translating.state.autorfx
        assert state.translation_cache is not None
    # Shutdown closes both caches: a server started many times must not pile up connections.
    for cache in (state.cache, state.translation_cache):
        with pytest.raises(sqlite3.ProgrammingError):
            cache.get("k")
    assert (settings.cache_dir / "translation.sqlite").exists()


def test_the_evidence_gate_follows_the_loaded_encoder(
    tmp_path: Path, ruleset: Ruleset, monkeypatch: pytest.MonkeyPatch
):
    """The gate comes from the encoder in use, not from the setting, so the two cannot disagree."""
    from autorfx.api import state as state_module
    from topic_encoder import TopicEncoder

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", embedding_model=None)
    seen: list[float | None] = []
    real = state_module.load_rfq

    def spy(*args, **kwargs):
        seen.append(kwargs.get("min_score"))
        return real(*args, **kwargs)

    monkeypatch.setattr(state_module, "load_rfq", spy)
    with running_app(settings, ruleset) as accounts:
        rfq_id = accounts.upload(make_package(tmp_path / "upload"))
        app_state = accounts.state
        encoder = TopicEncoder()
        encoder.name = "openai:text-embedding-3-large"
        app_state.encoder = encoder
        app_state._data.clear()
        assert accounts["admin"].get(f"/rfqs/{rfq_id}/clauses").status_code == 200
    assert seen[-1] == 0.30


def test_a_failed_embedding_is_not_cached_so_the_next_request_tries_again(
    tmp_path: Path, ruleset: Ruleset, monkeypatch: pytest.MonkeyPatch
):
    """A 429 or a dropped network on the first open must not keep the RFQ on keyword search."""
    from autorfx import pipeline
    from topic_encoder import TopicEncoder

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", embedding_model=None)
    with running_app(settings, ruleset) as accounts:
        rfq_id = accounts.upload(make_package(tmp_path / "upload"))
        app_state = accounts.state
        app_state.encoder = TopicEncoder()
        app_state._data.clear()
        real = pipeline.DenseIndex
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("offline")
            return real(*args, **kwargs)

        monkeypatch.setattr(pipeline, "DenseIndex", flaky)
        client = accounts["admin"]
        first = client.get("/meta")  # warm-up, keeps the client alive
        assert first.status_code == 200
        assert client.get(f"/rfqs/{rfq_id}/clauses").status_code == 200
        assert rfq_id not in app_state._data, "the keyword-only result is not kept"
        assert client.get(f"/rfqs/{rfq_id}/clauses").status_code == 200
        assert rfq_id in app_state._data, "once the embedding works, the result is kept"


def test_the_browser_may_send_every_method_the_web_uses(api: Api):
    """auth-rbac §3: the web app saves with PUT, PATCH and DELETE and sends its cookie from another origin,
    so the preflight allows them without a session (ported from the removed department settings tests)."""
    client = api[0].anonymous(origin=None)
    for path, method in (
        ("/company", "PUT"),
        ("/admin/users/u1", "PATCH"),
        ("/admin/users/u1", "DELETE"),
        ("/auth/login", "POST"),
    ):
        response = client.options(
            path,
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200, (path, method, response.text)
        assert method in response.headers["access-control-allow-methods"]
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"


def test_the_department_settings_are_gone(api: Api):
    """auth-rbac §3: company departments are removed; their addresses no longer exist."""
    admin = api[0]["admin"]
    assert error(admin.get("/org/departments")) == (404, "not_found")
    assert error(admin.put("/org/departments", json={"departments": []})) == (404, "not_found")
