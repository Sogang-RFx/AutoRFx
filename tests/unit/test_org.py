"""Company departments (SPEC US10, §7): a company names its own departments and picks, per
department, which rule departments ("업무 영역") it receives. Classification is untouched;
the overlay only changes who sees what, and a company department is cost-restricted unless
cost visibility was switched on for it."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from autorfx.analysis import RuleOnlyProvider
from autorfx.api import create_app
from autorfx.config import Settings
from autorfx.rules import Ruleset
from autorfx.work_areas import matching_areas
from contract_schema import assert_valid
from factories import PACKAGE_SECRET, make_package, precost_inputs

PDF = "application/pdf"
PRECOST = {"inputs": precost_inputs(), "resolved": {}}
Org = tuple[TestClient, str, Settings]


@pytest.fixture(scope="module")
def org(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Org]:
    root = tmp_path_factory.mktemp("org")
    settings = Settings(_env_file=None, data_dir=root / "data")
    sources = make_package(root / "upload")
    with TestClient(create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())) as client:
        files = [("files", (p.name, p.read_bytes(), PDF)) for p in sources]
        response = client.post("/rfqs", files=files)
        assert response.status_code == 200, response.text
        client.app.state.autorfx.wait()
        yield client, response.json()["data"]["id"], settings


@pytest.fixture
def client(org: Org) -> Iterator[TestClient]:
    """Each test starts and ends with the default departments."""
    client = org[0]
    assert client.put("/org/departments", json={"departments": []}).status_code == 200
    yield client
    client.put("/org/departments", json={"departments": []})


def save(client: TestClient, *departments: dict) -> list[dict]:
    response = client.put("/org/departments", json={"departments": list(departments)})
    assert response.status_code == 200, response.text
    assert_valid("org-departments", response.json())
    return response.json()["data"]["departments"]


def dept(label: str, areas: list[str], cost_visible: bool = False) -> dict:
    return {"id": None, "label": label, "areas": areas, "cost_visible": cost_visible}


def clause_ids(client: TestClient, rfq_id: str, **params: str) -> list[str]:
    body = client.get(f"/rfqs/{rfq_id}/clauses", params=params).json()
    return [c["clause_id"] for c in body["data"]["clauses"]]


def test_departments_are_saved_read_back_and_kept_across_a_restart(
    client: TestClient, org: Org, ruleset: Ruleset
):
    """US10-4: what was saved is what the server serves, also after it starts again."""
    _, _, settings = org
    saved = save(
        client,
        dept("견적팀", ["sales", "costing"], cost_visible=True),
        dept("설비팀", ["production_engineering"]),
    )
    assert [d["label"] for d in saved] == ["견적팀", "설비팀"]
    assert all(d["id"].startswith("org-") for d in saved), (
        "ids are made by the server and never clash with rules"
    )
    assert saved[1]["cost_visible"] is False

    got = client.get("/org/departments").json()
    assert_valid("org-departments", got)
    assert got["data"]["departments"] == saved

    # An id that is sent back keeps its department; renaming does not make a new one.
    renamed = save(client, {**saved[0], "label": "영업견적팀"})
    assert renamed[0]["id"] == saved[0]["id"]

    with TestClient(create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())) as again:
        assert again.get("/org/departments").json()["data"]["departments"] == renamed


@pytest.mark.parametrize(
    "departments",
    [
        [dept("품질기획", ["nonexistent"])],
        [dept("품질기획", [])],
        [dept("", ["quality"])],
        [dept("   ", ["quality"])],
        [dept("x" * 41, ["quality"])],
        [dept("품질팀", ["quality"]), dept(" 품질팀 ", ["logistics"])],
        [dept(f"팀{i}", ["quality"]) for i in range(31)],
        [{**dept("품질기획", ["quality"]), "id": "costing"}],
    ],
    ids=[
        "unknown-area",
        "no-area",
        "empty-label",
        "blank-label",
        "long-label",
        "duplicate",
        "too-many",
        "foreign-id",
    ],
)
def test_invalid_settings_are_refused_and_nothing_changes(client: TestClient, departments: list[dict]):
    """US10-4: a refused save says why and leaves the previous settings in place."""
    before = save(client, dept("물류팀", ["logistics"]))
    response = client.put("/org/departments", json={"departments": departments})
    assert response.status_code == 400
    body = response.json()
    assert_valid("error", body)
    assert body["error"]["code"] == "invalid_request" and body["error"]["message"]
    assert client.get("/org/departments").json()["data"]["departments"] == before


def test_meta_lists_company_departments_once_saved_and_the_defaults_otherwise(
    client: TestClient, ruleset: Ruleset
):
    """US10-3: the team picker follows the company; with none saved it is the rule departments."""
    defaults = client.get("/meta").json()["data"]
    assert [t["id"] for t in defaults["teams"]] == list(ruleset.department_ids)

    saved = save(
        client,
        dept("견적팀", ["sales", "costing"], cost_visible=True),
        dept("설비팀", ["production_engineering"]),
    )
    meta = client.get("/meta").json()
    assert_valid("meta", meta)
    teams = meta["data"]["teams"]
    assert [(t["id"], t["label"], t["group"], t["areas"]) for t in teams] == [
        (saved[0]["id"], "견적팀", "cost_visible", ["sales", "costing"]),
        (saved[1]["id"], "설비팀", "cost_restricted", ["production_engineering"]),
    ]
    assert meta["data"]["departments"] == defaults["departments"], "the work areas stay the rule departments"


def test_a_company_department_sees_the_union_of_its_areas(client: TestClient, org: Org):
    """US10-1: the filter is the union of the chosen areas; an area that receives all clauses gives all."""
    _, rfq_id, _ = org
    both, sales = save(client, dept("품질물류팀", ["quality", "logistics"]), dept("영업팀", ["sales"]))
    union = set(clause_ids(client, rfq_id, department="quality")) | set(
        clause_ids(client, rfq_id, department="logistics")
    )
    mine = clause_ids(client, rfq_id, department=both["id"])
    assert union, "the package has quality or logistics clauses"
    assert set(mine) == union and len(mine) == len(set(mine))
    assert clause_ids(client, rfq_id, department=sales["id"]) == clause_ids(client, rfq_id)


def test_a_company_department_is_cost_restricted_unless_switched_on(client: TestClient, org: Org):
    """US10-2, INV-8, RBAC-03: cost visibility is off by default and nothing about cost leaks."""
    _, rfq_id, _ = org
    closed, opened = save(
        client, dept("원가분석팀", ["costing"]), dept("원가팀", ["costing"], cost_visible=True)
    )

    team = closed["id"]
    body = client.get(f"/rfqs/{rfq_id}/clauses", params={"team": team}).json()
    assert body["data"]["group"] == "cost_restricted"
    assert any(c["kind"] == "masked" for c in body["data"]["clauses"])
    for path in ("clauses", "rtm", "consistency"):
        text = client.get(f"/rfqs/{rfq_id}/{path}", params={"team": team}).text
        assert PACKAGE_SECRET not in text, f"{path} leaks cost text"
    chat = client.post(f"/rfqs/{rfq_id}/chat", json={"team": team, "question": "목표가는 얼마인가?"})
    assert PACKAGE_SECRET not in chat.text
    assert client.get(f"/rfqs/{rfq_id}/precost-setup", params={"team": team}).status_code == 403
    assert client.get("/company", params={"team": team}).status_code == 403
    assert client.post("/precost", json={**PRECOST, "rfq_id": rfq_id, "team": team}).status_code == 403

    seen = client.get(f"/rfqs/{rfq_id}/clauses", params={"team": opened["id"]})
    assert seen.json()["data"]["group"] == "cost_visible"
    assert PACKAGE_SECRET in seen.text
    assert client.get(f"/rfqs/{rfq_id}/precost-setup", params={"team": opened["id"]}).status_code == 200


def test_the_browser_may_send_the_save_request(client: TestClient, org: Org):
    """US10: the web app saves with PUT from another origin, so the CORS preflight must allow it."""
    _, _, settings = org
    origin = settings.cors_origin_list[0]
    response = client.options(
        "/org/departments",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]


def test_a_removed_company_department_is_restricted_again(client: TestClient, org: Org):
    """RBAC-03: an id that is no longer saved is an unknown team."""
    _, rfq_id, _ = org
    (gone,) = save(client, dept("원가팀", ["costing"], cost_visible=True))
    save(client)
    body = client.get(f"/rfqs/{rfq_id}/clauses", params={"team": gone["id"]}).json()
    assert body["data"]["group"] == "cost_restricted"


def test_work_areas_route_matching_clauses(client: TestClient, org: Org):
    catalog = client.get("/meta").json()["data"]["work_areas"]
    assert len({a["id"] for a in catalog}) == len(catalog) == 10
    (team,) = save(client, dept("견적 담당", ["pricing"], cost_visible=True))
    url = f"/rfqs/{org[1]}/clauses"

    def rows(**params):
        return client.get(url, params={"team": team["id"], **params}).json()["data"]["clauses"]

    all_rows = rows()
    selected = rows(department=team["id"])
    assert 0 < len(selected) < len(all_rows)
    assert all("pricing" in row["work_areas"] for row in selected)
    assert PACKAGE_SECRET in str(selected)


def test_an_earlier_fine_grained_area_is_saved_as_its_group(client: TestClient, org: Org):
    """Settings made with the first 34 areas keep working: each id becomes the group that holds it."""
    (team,) = save(client, dept("견적 담당", ["quote", "breakdown", "packaging"]))
    assert team["areas"] == ["pricing", "delivery"]


def test_a_german_attachment_reference_is_not_a_production_clause():
    """US10: "Anlage N" names an attachment; the production area must not take the clause."""
    assert "production" not in matching_areas("Die Zeichnungen sind in Anlage 3 beigefügt.")
    assert "production" in matching_areas("Der Lieferant beschreibt den Fertigungsprozess.")
