"""Work areas read from data/rules/work_areas.yaml and matched as whole words."""

from pathlib import Path

import pytest

from autorfx.rules import RulesError, WorkAreaRules, load_ruleset
from autorfx.work_areas import canonical_area, matching_areas

RULES = load_ruleset(Path(__file__).resolve().parents[2] / "data" / "rules").work_areas


@pytest.mark.parametrize(
    ("text", "area"),
    [
        ("Die Unterlagen sind in Anlage 3 beigefügt.", "production"),  # Anlage N = attachment N
        ("In case of breach of these terms the buyer may terminate.", "contract"),  # reach
        ("Die Steuerung des Aktuators erfolgt über CAN.", "payment"),  # steuer
        ("See the disclaimer on the cover page.", "quality-assurance"),  # claim
        ("Zueignung an den Auftraggeber.", "quality-assurance"),  # eignung
        ("Bezeichnung des Bauteils: Batteriehalter.", "engineering"),  # zeichnung
        ("Prices shall reflect the market as it reaches its peak.", "contract"),  # reach as a verb
    ],
)
def test_a_keyword_inside_another_word_is_not_a_match(text: str, area: str) -> None:
    assert area not in matching_areas(RULES, text)


@pytest.mark.parametrize(
    ("text", "area"),
    [
        ("Warranty claims are settled within 30 days.", "quality-assurance"),
        ("Es gelten unsere Zahlungsbedingungen.", "payment"),
        ("Alle Preise verstehen sich zuzüglich Umsatzsteuer.", "payment"),
        ("Parts shall comply with REACH and RoHS.", "contract"),
        ("Die Produkthaftung bleibt unberührt.", "contract"),
        ("Rechnungen sind an die Buchhaltung zu senden.", "payment"),
        ("결제는 월말 마감 후 60일입니다.", "payment"),
        ("The supplier keeps a safety stock of two weeks.", "delivery"),
    ],
)
def test_a_keyword_matches_as_a_word_with_its_endings_and_compounds(text: str, area: str) -> None:
    assert area in matching_areas(RULES, text)


def test_the_more_specific_area_comes_first() -> None:
    areas = matching_areas(RULES, "The tooling cost and the payment terms are listed below.")
    assert areas[:2] == ["pricing", "payment"]  # "tooling cost" is longer than "payment"


def test_an_earlier_topic_id_maps_to_its_area() -> None:
    assert [canonical_area(RULES, a) for a in ("quote", "packaging", "pricing", "other")] == [
        "pricing",
        "delivery",
        "pricing",
        "other",
    ]


def test_a_bad_expression_names_the_area() -> None:
    with pytest.raises(ValueError, match="정규식 오류"):
        WorkAreaRules(areas=[{"id": "x", "label": "X", "topics": {"t": ["re:("]}}])


def test_the_rule_file_is_required(tmp_path: Path) -> None:
    rules = Path(__file__).resolve().parents[2] / "data" / "rules"
    for f in rules.glob("*.yaml"):
        if f.name != "work_areas.yaml":
            (tmp_path / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(RulesError, match="work_areas"):
        load_ruleset(tmp_path)


def test_the_catalog_has_ten_areas_with_their_own_ids() -> None:
    """Ported from the removed department settings tests: the areas themselves stay."""
    assert len({a.id for a in RULES.areas}) == len(RULES.areas) == 10


def test_fine_grained_topics_of_one_area_map_to_that_area() -> None:
    assert canonical_area(RULES, "breakdown") == canonical_area(RULES, "quote") == "pricing"


def test_a_manufacturing_sentence_is_a_production_clause() -> None:
    """US10: "Anlage N" names an attachment (above), a real manufacturing sentence is production."""
    assert "production" in matching_areas(RULES, "Der Lieferant beschreibt den Fertigungsprozess.")
