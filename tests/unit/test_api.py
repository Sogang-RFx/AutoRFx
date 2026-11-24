"""The HTTP API on a hand-made package: contract shapes, access control, errors."""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from autorfx.analysis import RuleOnlyProvider
from autorfx.api import create_app
from autorfx.config import Settings
from autorfx.rules import Ruleset
from contract_schema import assert_valid
from factories import PACKAGE_SECRET, make_package, precost_inputs

VISIBLE, RESTRICTED = "costing", "quality"
SECRETS = (PACKAGE_SECRET, "1.2345", "target price", "rejected")
PDF = "application/pdf"
Api = tuple[TestClient, str, list[Path]]


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Api]:
    root = tmp_path_factory.mktemp("api")
    settings = Settings(_env_file=None, data_dir=root / "data", embedding_model=None)
    make_package(settings.samples_dir / "synthetic-sample")
    sources = make_package(root / "upload")
    app = create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())
    with TestClient(app) as client:
        files = [("files", (p.name, p.read_bytes(), PDF)) for p in sources]
        response = client.post("/rfqs", files=files)
        assert response.status_code == 200, response.text
        app.state.autorfx.wait()
        yield client, response.json()["data"]["id"], sources


def get(client: TestClient, path: str, schema: str | None = None, **params: str) -> dict:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    if schema:
        assert_valid(schema, body)
    return body


def error(response) -> tuple[int, str]:
    body = response.json()
    assert_valid("error", body)
    return response.status_code, body["error"]["code"]


# ── intake ──────────────────────────────────────────────────────────────────
def test_meta_describes_teams_engines_samples_and_limits(api: Api):
    """META-01, ENGINE-03: the client learns who may see cost, and how analysis is computed."""
    client, _, _ = api
    data = get(client, "/meta", "meta")["data"]
    groups = {team["id"]: team["group"] for team in data["teams"]}
    assert groups["costing"] == groups["sales"] == groups["purchasing"] == "cost_visible"
    assert groups["quality"] == groups["legal"] == "cost_restricted" and len(groups) == 8
    assert data["engines"] == {
        "analysis": "rule-only",
        "translation": "none",
        "retrieval": "bm25-glossary",
        "llm_connected": False,
    }
    # The development folder holds a package, but the API never lists it (SPEC §13.2).
    assert data["samples"] == []
    assert data["upload"]["accept"] == [".pdf", ".doc", ".docx", ".xlsx"]


def test_upload_runs_the_job_and_the_document_is_served(api: Api):
    """PKG-01, JOB-01: files in, a finished job and a document with per-file facts out."""
    client, rfq_id, _ = api
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
    client, rfq_id, sources = api
    files = [("files", (p.name, p.read_bytes(), PDF)) for p in reversed(sources)]
    body = client.post("/rfqs", files=files).json()
    assert_valid("upload", body)
    assert body["data"]["id"] == rfq_id and "저장된 결과" in body["data"]["notice"]


def test_packages_on_the_server_cannot_be_opened_through_the_api(api: Api):
    """SPEC §13.2: only uploaded files are analysed; a development-folder package is not reachable by id."""
    client, _, _ = api
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
    client, _, _ = api
    response = client.post("/rfqs", files=[("files", (name, content, "application/octet-stream"))])
    assert error(response) == (400, "invalid_request") and expected in response.json()["error"]["message"]


def test_upload_name_cannot_leave_the_upload_folder(api: Api):
    """Security: a client path is reduced to its file name before anything is written."""
    client, _, _ = api
    response = client.post("/rfqs", files=[("files", ("../../evil.txt", b"x", "text/plain"))])
    assert error(response) == (400, "invalid_request")
    state = client.app.state.autorfx
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
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "data", max_files=1, max_file_mb=1, embedding_model=None
    )
    chunks = [PART_HEAD, *([b"x" * MB] * 40), b"\r\n--B--\r\n"]
    headers = [(b"content-length", str(sum(map(len, chunks))).encode())] if declare_length else []
    with TestClient(create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())) as client:
        status, code, pulled = _post_raw(client.app, chunks, headers)
        upload_root = client.app.state.autorfx.upload_root
    assert (status, code) == (413, "too_large")
    assert pulled <= (0 if declare_length else 4), f"read {pulled} of {len(chunks)} chunks before refusing"
    assert not upload_root.exists() or not any(upload_root.iterdir()), "nothing is left on disk"


def test_results_of_older_rules_are_not_served(tmp_path: Path, ruleset: Ruleset):
    """INV-8 (review open question): cost sensitivity follows the rules, so results computed under
    other rule files are refused until the package is analysed again; analysing again fixes it."""
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", embedding_model=None)
    sources = make_package(tmp_path / "upload")
    files = [("files", (p.name, p.read_bytes(), PDF)) for p in sources]
    with TestClient(create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())) as client:
        rfq_id = client.post("/rfqs", files=files).json()["data"]["id"]
        client.app.state.autorfx.wait()
        assert client.get(f"/rfqs/{rfq_id}/clauses").status_code == 200
    changed = ruleset.model_copy(update={"digest": "0" * 64})
    with TestClient(create_app(settings, ruleset=changed, provider=RuleOnlyProvider())) as client:
        assert error(client.get(f"/rfqs/{rfq_id}/clauses")) == (409, "stale_results")
        again = client.post("/rfqs", files=files).json()["data"]
        assert again["id"] == rfq_id and again["notice"] == "", "a real new analysis, not the stored one"
        client.app.state.autorfx.wait()
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
    client, _, _ = api
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_a_failed_job_is_reported_and_has_no_results(api: Api):
    """JOB-02: the job shows where it stopped; result endpoints say so instead of serving partial data."""
    client, _, _ = api
    body = client.post("/rfqs", files=[("files", ("broken.pdf", b"%PDF-1.7 not a document", PDF))]).json()
    client.app.state.autorfx.wait()
    rfq_id = body["data"]["id"]
    stages = {s["id"]: s for s in get(client, f"/rfqs/{rfq_id}/job", "job")["data"]["stages"]}
    assert stages["canonical"]["status"] == "error" and "broken.pdf" in stages["canonical"]["detail"]
    assert error(client.get(f"/rfqs/{rfq_id}/clauses")) == (409, "job_failed")


# ── analysis, per team ──────────────────────────────────────────────────────
@pytest.mark.parametrize("team", [VISIBLE, RESTRICTED, None, "nobody"])
def test_every_response_conforms_to_the_contract(api: Api, team: str | None):
    """CONTRACT-01, INV-9: the shapes the web validates are the shapes the backend returns."""
    client, rfq_id, _ = api
    params = {"team": team} if team else {}
    group = "cost_visible" if team == VISIBLE else "cost_restricted"
    assert get(client, f"/rfqs/{rfq_id}/clauses", "clauses", **params)["data"]["group"] == group
    assert get(client, f"/rfqs/{rfq_id}/rtm", "rtm", **params)["data"]["group"] == group
    assert get(client, f"/rfqs/{rfq_id}/consistency", "consistency", **params)["data"]["group"] == group
    get(client, f"/rfqs/{rfq_id}/files/d01/pages/1/sentences", "sentences", **params)
    get(client, f"/rfqs/{rfq_id}/evaluation", "evaluation")
    chat = client.post(f"/rfqs/{rfq_id}/chat", json={"team": team, "question": "인도조건은?"})
    assert chat.status_code == 200
    assert_valid("chat", chat.json())
    assert chat.json()["data"]["status"] == "answered"


def test_cost_visible_team_sees_the_price_clause(api: Api):
    client, rfq_id, _ = api
    clauses = get(client, f"/rfqs/{rfq_id}/clauses", team=VISIBLE)["data"]["clauses"]
    price = next(c for c in clauses if c["clause_id"] == "d01:2.2")
    assert price["kind"] == "clause" and PACKAGE_SECRET in price["excerpt"][0]["text"]
    assert price["primary_department"] == "costing" and price["origin"] == "rule"
    source = client.get(f"/rfqs/{rfq_id}/files/d01/source", params={"team": VISIBLE})
    assert source.headers["content-type"] == PDF and source.headers["cache-control"] == "no-store"
    with pymupdf.open(stream=source.content, filetype="pdf") as doc:
        assert "1.2345" in doc[0].get_text()


def test_nothing_about_the_price_reaches_a_restricted_team(api: Api):
    """INV-8, SC-004: every endpoint is called as a restricted team and searched for the hidden text."""
    client, rfq_id, _ = api
    page = ""
    for team in (RESTRICTED, None, "nobody"):
        params = {"team": team} if team else {}
        texts = [
            json.dumps(get(client, f"/rfqs/{rfq_id}{path}", **params), ensure_ascii=False)
            for path in ("/clauses", "/rtm", "/consistency", "/files/d01/pages/1/sentences", "", "/job")
        ]
        for department in ("costing", "sales", "quality"):
            filtered = get(client, f"/rfqs/{rfq_id}/clauses", department=department, **params)
            texts.append(json.dumps(filtered, ensure_ascii=False))
        for question in ("목표가는 얼마인가요?", "target price", "price", "rejected quotes", "1.2345"):
            reply = client.post(f"/rfqs/{rfq_id}/chat", json={"team": team, "question": question})
            texts.append(reply.text)
        source = client.get(f"/rfqs/{rfq_id}/files/d01/source", params=params)
        with pymupdf.open(stream=source.content, filetype="pdf") as doc:
            page = doc[0].get_text()
        workbook = client.get(f"/rfqs/{rfq_id}/rtm.xlsx", params=params)
        sheet = load_workbook(io.BytesIO(workbook.content)).active
        texts.append(json.dumps([list(row) for row in sheet.iter_rows(values_only=True)], default=str))
        for text in (*texts, page):
            for secret in SECRETS:
                assert secret not in text, (team, secret)
    masked = get(client, f"/rfqs/{rfq_id}/clauses", team=RESTRICTED)["data"]["clauses"][3]
    assert masked == {"kind": "masked", "clause_id": "d01:2.2", "source_doc": "d01", "section_id": "2.2"}
    assert "2.1 Delivery is FCA" in page and "2.3 The warranty period" in page, "neighbours intact"


def test_cost_screens_are_closed_to_restricted_teams(api: Api):
    """RBAC-01, COST-04: company figures, the setup and the calculation are refused, not emptied."""
    client, rfq_id, _ = api
    body = {"rfq_id": rfq_id, "inputs": precost_inputs(), "resolved": {}}
    for team in (RESTRICTED, None, "nobody"):
        params = {"team": team} if team else {}
        assert error(client.get(f"/rfqs/{rfq_id}/precost-setup", params=params)) == (403, "forbidden")
        assert error(client.get("/company", params=params)) == (403, "forbidden")
        assert error(client.post("/precost", json={**body, "team": team})) == (403, "forbidden")


# ── pre-cost ────────────────────────────────────────────────────────────────
def test_company_settings_round_trip_and_bad_rows_are_refused(api: Api):
    """Initial setup: the price table and profile are saved whole; a bad row names itself."""
    client, _, _ = api
    company = get(client, "/company", "company", team=VISIBLE)["data"]
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
    saved = client.put("/company", params={"team": VISIBLE}, json=body)
    assert saved.status_code == 200, saved.text
    assert_valid("company", saved.json())
    stored = saved.json()["data"]
    assert stored["prices"][0]["id"].startswith("price-") and stored["profile"]["certifications"] == [
        "IATF 16949"
    ]
    bad = {**body, "prices": [{**row, "department": "nobody"}]}
    assert error(client.put("/company", params={"team": VISIBLE}, json=bad)) == (400, "invalid_request")
    twice = {**body, "prices": [row, row]}
    assert error(client.put("/company", params={"team": VISIBLE}, json=twice)) == (400, "invalid_request")


def test_setup_and_calculation_for_a_cost_visible_team(api: Api):
    """COST-01, COST-05: cited inputs from the RFQ, a computed minimum quote, a decision with its rule."""
    client, rfq_id, _ = api
    setup = get(client, f"/rfqs/{rfq_id}/precost-setup", "precost-setup", team=VISIBLE)["data"]
    by_field = {c["field"]: c for c in setup["cost_inputs"]["candidates"]}
    assert by_field["target_price"]["value"] == "1.2345"
    assert by_field["target_price"]["evidence"]["citation_status"] == "verified"
    assert [a["value"] for a in by_field["incoterm"]["alternatives"]] == ["FCA", "DDP"]
    assert setup["classification"]["type"] == "other", "one drawing is not enough to call it manufacturing"

    plain = {"team": VISIBLE, "rfq_id": None, "inputs": precost_inputs(target_price="2"), "resolved": {}}
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
    client, _, _ = api
    body = {"team": VISIBLE, "rfq_id": None, "inputs": precost_inputs(**change), "resolved": {}}
    assert error(client.post("/precost", json=body)) == (422, "invalid_request")


# ── errors and headers ──────────────────────────────────────────────────────
def test_unknown_things_are_not_found(api: Api):
    client, rfq_id, _ = api
    assert error(client.get("/rfqs/rfq-doesnotexist/clauses")) == (404, "not_found")
    assert error(client.get("/rfqs/..%2F..%2Fetc/job")) == (404, "not_found")
    assert error(client.get(f"/rfqs/{rfq_id}/files/d99/source")) == (404, "not_found")
    assert error(client.get(f"/rfqs/{rfq_id}/files/d01/pages/99/sentences")) == (404, "not_found")
    assert error(client.get("/nowhere")) == (404, "not_found")
    assert error(client.get(f"/rfqs/{rfq_id}/files/d01/pages/x/sentences")) == (422, "invalid_request")


def test_chat_refuses_an_empty_question(api: Api):
    client, rfq_id, _ = api
    empty = client.post(f"/rfqs/{rfq_id}/chat", json={"team": VISIBLE, "question": "  "})
    assert error(empty) == (422, "invalid_request")
    assert error(client.post(f"/rfqs/{rfq_id}/chat", json={"team": VISIBLE})) == (422, "invalid_request")


def test_security_headers_and_cors(api: Api):
    client, _, _ = api
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = client.get("/health", headers={"Origin": "https://example.org"})
    assert "access-control-allow-origin" not in other.headers


def test_the_evidence_gate_follows_the_loaded_encoder(
    tmp_path: Path, ruleset: Ruleset, monkeypatch: pytest.MonkeyPatch
):
    """The gate comes from the encoder in use, not from the setting, so the two cannot disagree."""
    from autorfx.api import state as state_module
    from topic_encoder import TopicEncoder

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", embedding_model=None)
    files = [("files", (p.name, p.read_bytes(), PDF)) for p in make_package(tmp_path / "upload")]
    seen: list[float | None] = []
    real = state_module.load_rfq

    def spy(*args, **kwargs):
        seen.append(kwargs.get("min_score"))
        return real(*args, **kwargs)

    monkeypatch.setattr(state_module, "load_rfq", spy)
    with TestClient(create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())) as client:
        rfq_id = client.post("/rfqs", files=files).json()["data"]["id"]
        app_state = client.app.state.autorfx
        app_state.wait()
        encoder = TopicEncoder()
        encoder.name = "openai:text-embedding-3-large"
        app_state.encoder = encoder
        app_state._data.clear()
        assert client.get(f"/rfqs/{rfq_id}/clauses").status_code == 200
    assert seen[-1] == 0.30
