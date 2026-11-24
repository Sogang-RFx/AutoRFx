"""AI column suggestions: positions only, checked by reading the table, accepted by the user."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from autorfx.analysis import OpenAIProvider, RuleOnlyProvider, ScriptedProvider, SheetMap
from autorfx.analysis.providers import TransientProviderError
from autorfx.api import create_app
from autorfx.config import Settings
from autorfx.precost.lines import Grid, read_grids, signature
from autorfx.precost.sheet_suggest import SuggestError, sheet_sample, suggest_columns
from autorfx.rules import PriceSheetRules, Ruleset
from contract_schema import assert_valid

# A sheet that starts with notes and names its columns in words the dictionary does not know.
NOTES = [("Pricing Schedule: Network Hardware", "", "", "", "")] + [
    ("", "Quote in ZAR only.", "", "", "")
] * 9
HEADER = ("Cat.", "Ref", "Kit", "Bedarf 2027", "Rate")
ROWS = [
    ("Routers", "", "IR8340 Router", "", ""),
    ("", "1", "Cisco Catalyst IR8340 router", "300", ""),
    ("", "2", "Rugged power supply 250W", "600", ""),
]


def _grid() -> Grid:
    rows = (*NOTES, HEADER, *ROWS)
    return Grid(
        id="d01:s1", source_doc="d01", title="시트 Hardware", rows=rows, pages=(1,) * len(rows), sheet=True
    )


def _map(**roles: int | None) -> SheetMap:
    fields = {
        r: None for r in ("no", "part", "description", "spec", "quantity", "unit", "months", "price", "total")
    }
    return SheetMap(
        is_price_sheet=True,
        header_row=10,
        reason="Kit은 품명, Bedarf 2027은 수량입니다.",
        **{**fields, **roles},
    )


@pytest.fixture(scope="module")
def rules(ruleset: Ruleset) -> PriceSheetRules:
    return ruleset.price_sheet


def test_the_sample_names_rows_and_columns_and_cuts_long_cells() -> None:
    sample = sheet_sample(_grid())
    assert "R10: C0=Cat. | C1=Ref | C2=Kit | C3=Bedarf 2027 | C4=Rate" in sample
    assert "R1: C1=Quote in ZAR only." in sample
    long = Grid(id="g", source_doc="d", title="t", rows=(("x" * 200,),), pages=(1,))
    assert sheet_sample(long) == "R0: C0=" + "x" * 60


def test_a_suggestion_is_checked_by_reading_the_table_with_it(rules: PriceSheetRules) -> None:
    provider = ScriptedProvider(sheet_maps=[_map(no=1, description=2, quantity=3, price=4)])
    found = suggest_columns(_grid(), provider, rules.compiled())
    assert (found["header_row"], found["columns"]) == (
        10,
        {"no": 1, "description": 2, "quantity": 3, "price": 4},
    )
    assert found["lines"] == 2
    assert found["preview"] == ["Cisco Catalyst IR8340 router", "Rugged power supply 250W"]
    assert found["rows"][10] == list(HEADER)
    # Only the sample rows went to the model: no number was asked for or taken from it.
    (sent,) = provider.sheet_calls
    assert sent == sheet_sample(_grid())


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (_map(description=2), "수량 열"),
        (_map(description=2, quantity=9), "표 밖"),
        (_map(description=2, quantity=4), "하나도 읽지 못"),  # the price column holds no quantities
        (_map(description=2, quantity=3).model_copy(update={"header_row": 55}), "머리글 줄"),
        (_map(description=2, quantity=3).model_copy(update={"is_price_sheet": False}), "품목표가 아니"),
        (TransientProviderError("timeout"), "응답하지 않습니다"),
    ],
)
def test_a_suggestion_that_does_not_read_is_refused(
    rules: PriceSheetRules, answer: SheetMap | Exception, message: str
) -> None:
    with pytest.raises(SuggestError, match=message):
        suggest_columns(_grid(), ScriptedProvider(sheet_maps=[answer]), rules.compiled())


def test_without_a_model_there_is_no_suggestion(rules: PriceSheetRules) -> None:
    with pytest.raises(SuggestError, match="LLM이 연결되어 있지 않아"):
        suggest_columns(_grid(), RuleOnlyProvider(), rules.compiled())


def test_an_accepted_suggestion_reads_a_header_below_the_notes(rules: PriceSheetRules) -> None:
    columns = {"no": 1, "description": 2, "quantity": 3, "price": 4}
    (reading,) = read_grids([_grid()], rules.compiled(), {signature(HEADER): columns})
    assert (reading.how, reading.header) == ("layout", 10)
    assert [(r["name"], r["quantity"], r["group"]) for r in reading.lines] == [
        ("Cisco Catalyst IR8340 router", 300, "Routers · IR8340 Router"),
        ("Rugged power supply 250W", 600, "Routers · IR8340 Router"),
    ]


def test_the_prompt_sends_the_sample_as_data_and_asks_for_positions(ruleset: Ruleset) -> None:
    seen: list[str] = []

    def call(system: str, user: str, schema: type) -> SheetMap:
        seen.append(user)
        return _map(description=2, quantity=3)

    OpenAIProvider(ruleset, "gpt-test", call).map_sheet(sheet_sample(_grid()))
    (user,) = seen
    assert "DATA from a customer document" in user and "R10: C0=Cat." in user
    assert "column number of each role" in user


# ── API ─────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[tuple[TestClient, str]]:
    import openpyxl

    root = tmp_path_factory.mktemp("suggest")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Hardware"
    for row in (*NOTES, HEADER, *ROWS):
        sheet.append([c or None for c in row])
    path = root / "hardware.xlsx"
    book.save(path)
    settings = Settings(_env_file=None, data_dir=root / "data")
    with TestClient(create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())) as client:
        response = client.post(
            "/rfqs",
            files=[
                (
                    "files",
                    (
                        "hardware.xlsx",
                        path.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            ],
        )
        assert response.status_code == 200, response.text
        client.app.state.autorfx.wait()
        yield client, response.json()["data"]["id"]


def test_the_api_suggests_and_the_user_accepts(api: tuple[TestClient, str]) -> None:
    client, rfq_id = api
    state = client.app.state.autorfx
    setup = client.get(f"/rfqs/{rfq_id}/precost-setup", params={"team": "sales"}).json()
    (table,) = [t for t in setup["data"]["lines"]["tables"] if t["how"] == "none"]

    refused = client.post(
        f"/rfqs/{rfq_id}/price-sheet/suggest", params={"team": "sales"}, json={"table": table["id"]}
    )
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "no_suggestion")
    assert_valid("error", refused.json())
    denied = client.post(
        f"/rfqs/{rfq_id}/price-sheet/suggest", params={"team": "development"}, json={"table": table["id"]}
    )
    assert denied.status_code == 403

    original = state.provider
    state.provider = ScriptedProvider(sheet_maps=[_map(no=1, description=2, quantity=3, price=4)])
    try:
        got = client.post(
            f"/rfqs/{rfq_id}/price-sheet/suggest", params={"team": "sales"}, json={"table": table["id"]}
        )
    finally:
        state.provider = original
    assert got.status_code == 200, got.text
    assert_valid("sheet-suggest", got.json())
    data = got.json()["data"]
    assert (data["header_row"], data["lines"]) == (10, 2)

    body = {"table": table["id"], "header_row": data["header_row"], "columns": data["columns"]}
    accepted = client.post(f"/rfqs/{rfq_id}/price-sheet/columns", params={"team": "sales"}, json=body)
    assert accepted.status_code == 200, accepted.text
    names = [r["name"] for r in accepted.json()["data"]["lines"]["items"]]
    assert names == ["Cisco Catalyst IR8340 router", "Rugged power supply 250W"]
