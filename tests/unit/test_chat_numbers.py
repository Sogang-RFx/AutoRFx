"""The number check of chat answers (INV-3, CITE-01, docs/design/chat.md §3.2).

What is refused matters more than what is accepted: a wrong date that passes is shown as verified.
"""

from __future__ import annotations

import pytest

from autorfx.chat.numbers import unsupported_numbers

TWO_DATES = ("Abgabe bis 04.12.2026.", "Start am 15.03.2027.")
TWO_WORDED = ("Samples on 15 March 2028 and the audit on 3 May 2029.",)


@pytest.mark.parametrize(
    ("text", "sources"),
    [
        ("납기를 지켜야 합니다.", ("Deliver on time.",)),
        ("보증은 36개월입니다.", ("Die Frist beträgt 36 Monate.",)),
        ("불량률은 0.5% 이하입니다.", ("höchstens 0,5 %",)),
        ("연 200,000개입니다.", ("200.000 Stück pro Jahr",)),
        ("Cpk 1,67 이상이고 IATF 16949가 필요합니다.", ("Cpk 1.67 or better", "IATF 16949")),
        ("기한은 04.12.2026입니다.", ("bis 04.12.2026",)),
        ("기한은 12/04/2026입니다.", ("due 12/04/2026",)),
        ("도면 04번입니다.", ("Zeichnung 04",)),
    ],
)
def test_the_same_number_in_another_notation_is_supported(text: str, sources: tuple[str, ...]):
    """CITE-01, rule 1: separators may differ, digits may not."""
    assert unsupported_numbers(text, sources) == ()


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("2026년 12월 4일까지입니다.", "bis 04.12.2026"),
        ("2026년 12월입니다.", "bis 04.12.2026"),
        ("2026년입니다.", "bis 04.12.2026"),
        ("12월 4일까지입니다.", "bis 04.12.2026"),
        ("2026-12-04까지입니다.", "bis 04.12.2026"),
        ("2026.12.04까지입니다.", "bis 04.12.2026"),
        ("2026년 12월 4일까지입니다.", "due 2026-12-04"),
        ("2028년 3월입니다.", "SOP im März 2028"),
        ("2028년입니다.", "SOP im März 2028"),
        ("2028년 3월 4일입니다.", "am 4. März 2028"),
        ("2028년 3월입니다.", "am 4. März 2028"),
        ("2028년 3월 4일입니다.", "on March 4, 2028"),
        ("2028년 3월 4일입니다.", "on 4 March 2028"),
        ("2029년 12월입니다.", "Dezember 2029"),
        ("2029년 5월 15일입니다.", "15. Mai 2029"),
        ("2028년 3월입니다.", "SOP 03/2028"),
        ("2026년 12월 15일입니다.", "due 12/15/2026"),
        ("2026년 12월 15일입니다.", "due 15/12/2026"),
    ],
)
def test_a_date_in_korean_is_supported_by_the_same_date(text: str, source: str):
    """CITE-01, rule 2: one date of the source, with the day (and month) optionally left out."""
    assert unsupported_numbers(text, (source,)) == ()


def test_each_date_of_the_answer_may_come_from_its_own_source():
    """CITE-01: two whole dates are not a mixture."""
    assert unsupported_numbers("2026년 12월 4일까지, 시작은 2027년 3월 15일입니다.", TWO_DATES) == ()
    assert unsupported_numbers("2028년 3월 15일과 2029년 5월 3일입니다.", TWO_WORDED) == ()


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("한도는 500만 유로입니다.", "up to 5 Mio. EUR"),
        ("한도는 500만 유로입니다.", "up to 5 million EUR"),
        ("한도는 5백만 유로입니다.", "up to 5 million EUR"),
        ("한도는 5,000,000 유로입니다.", "bis 5 Mio. EUR"),
        ("한도는 250만 유로입니다.", "bis 2,5 Mio. EUR"),
        ("한도는 20억입니다.", "bis 2 Mrd. EUR"),
        ("한도는 30억입니다.", "up to 3 billion"),
        ("수량은 1만 개입니다.", "10 thousand pieces"),
        ("수량은 1만 개입니다.", "10k pieces"),
        ("한도는 5 Mio. EUR입니다.", "bis 5 Mio. EUR"),
    ],
)
def test_a_multiplier_of_the_same_value_is_supported(text: str, source: str):
    """CITE-01, rule 3: 5 Mio. is 500만."""
    assert unsupported_numbers(text, (source,)) == ()


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("2년입니다.", "for two years"),
        ("시료는 3개입니다.", "drei Muster"),
        ("12주입니다.", "innerhalb von zwölf Wochen"),
        ("10일입니다.", "Within Ten days"),
    ],
)
def test_a_number_word_is_supported(text: str, source: str):
    """CITE-01, rule 4: two to twelve, zwei to zwölf."""
    assert unsupported_numbers(text, (source,)) == ()


def test_list_markers_are_not_numbers():
    """§3.2: a marker at the start of a line is layout."""
    assert unsupported_numbers("1. 보증은 36개월\n2) 교체는 무상\n  3. 그 밖", ("36 Monate",)) == ()


# ── refused ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("보증은 24개월입니다.", ("24",)),
        ("보증은 36개월, 연장은 48개월입니다.", ("48",)),
        ("보증은 12~24개월입니다.", ("12", "24")),
    ],
)
def test_the_offending_number_is_returned_as_written(text: str, expected: tuple[str, ...]):
    """CITE-01: the retry names what is wrong, so the check has to say it."""
    assert unsupported_numbers(text, ("Die Frist beträgt 36 Monate.",)) == expected


@pytest.mark.parametrize(
    ("text", "sources"),
    [
        # parts of two dates, each part in a cited sentence
        ("2027년 12월 4일입니다.", TWO_DATES),
        ("2026년 3월 4일입니다.", TWO_DATES),
        ("2026년 3월입니다.", TWO_DATES),
        ("12월 15일입니다.", TWO_DATES),
        ("2026-03-15입니다.", TWO_DATES),
        ("2029년 3월 15일입니다.", TWO_WORDED),
        ("2028년 5월 3일입니다.", TWO_WORDED),
        ("2028년 3월 3일입니다.", TWO_WORDED),
        # the day of the date taken for its month
        ("2028년 4월입니다.", ("am 4. März 2028",)),
        # a day the source does not give
        ("2028년 3월 4일입니다.", ("SOP im März 2028",)),
        ("2028년 3월 4일입니다.", ("SOP 03/2028",)),
        # another year or month than the cited sentences state
        ("2027년입니다.", ("SOP im März 2028",)),
        ("2028년 4월입니다.", ("SOP im März 2028",)),
        ("2026년 12월 5일입니다.", ("bis 04.12.2026",)),
        # dd.mm.yyyy read as mm.dd.yyyy
        ("2026년 4월 12일입니다.", ("bis 04.12.2026",)),
        # March is not May
        ("2029년 3월입니다.", ("15. Mai 2029",)),
        # bare year vs incompatible unit (INV-3)
        ("2028년입니다.", ("2028 EUR",)),
        ("2028년입니다.", ("2028 Tage",)),
        ("2028 EUR입니다.", ("am 4. März 2028",)),
        ("2028개월입니다.", ("am 4. März 2028",)),
    ],
)
def test_a_date_no_cited_sentence_states_is_refused(text: str, sources: tuple[str, ...]):
    """CITE-01, INV-3: year, month and day must belong to one date of the source."""
    assert unsupported_numbers(text, sources)


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("2026년 12월 4일입니다.", "due 12/04/2026"),
        ("2026년 4월 12일입니다.", "due 12/04/2026"),
        ("2026년 12월 10일입니다.", "due 12/10/2026"),
        ("2026년 10월 12일입니다.", "due 12/10/2026"),
        ("2026-12-10입니다.", "due 12/10/2026"),
    ],
)
def test_an_ambiguous_date_is_never_resolved(text: str, source: str):
    """INV-3: 12/10/2026 may be October or December; it may only be copied."""
    assert unsupported_numbers(text, (source,))


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("한도는 50만 유로입니다.", "up to 5 Mio. EUR"),
        ("한도는 5만 유로입니다.", "up to 5 Mio. EUR"),
        ("한도는 5억 유로입니다.", "up to 5 Mio. EUR"),
        ("한도는 5천만 유로입니다.", "up to 5 million EUR"),
        ("한도는 5,000 유로입니다.", "bis 5 Mio. EUR"),
        ("한도는 2억입니다.", "bis 2 Mrd. EUR"),
        ("한도는 25만 유로입니다.", "bis 2,5 Mio. EUR"),
        ("수량은 10만 개입니다.", "10k pieces"),
        ("수량은 5백만 개입니다.", "5 pieces"),
        ("수량은 5만 개입니다.", "5 pieces"),
        ("수량은 200개입니다.", "200.000 Stück"),
    ],
)
def test_a_wrong_magnitude_is_refused(text: str, source: str):
    """CITE-01: the 5 of "5 Mio." does not support 5만."""
    assert unsupported_numbers(text, (source,))


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("보증은 3년입니다.", "36 Monate"),
        ("길이는 1000mm입니다.", "Länge 1 m"),
        ("3주입니다.", "21 Tage"),
        ("비율은 0.05입니다.", "5 %"),
    ],
)
def test_a_converted_unit_is_refused(text: str, source: str):
    """CITE-01: nothing is calculated, not even correctly."""
    assert unsupported_numbers(text, (source,))


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("1년입니다.", "ein Jahr"),
        ("1개입니다.", "eine Probe"),
        ("1회입니다.", "one sample is enough"),
        ("3년입니다.", "for two years"),
        ("13개입니다.", "thirteen parts"),
        ("10회입니다.", "as often as needed"),
        ("8kg입니다.", "the weight of the part"),
        ("2개입니다.", "the network"),
        ("3일입니다.", "dreißig Tage"),
    ],
)
def test_articles_and_parts_of_words_are_not_numbers(text: str, source: str):
    """CITE-01, rule 4: "one" and "ein" are articles; "often" does not contain a ten."""
    assert unsupported_numbers(text, (source,))


@pytest.mark.parametrize(
    ("text", "sources"),
    [
        ("125개입니다.", ("12 Wochen und 5 Tage",)),
        ("3612입니다.", ("36 Monate", "12 Wochen")),
        ("12개입니다.", ("125 Stück",)),
        ("16번입니다.", ("IATF 16949",)),
        ("4번입니다.", ("Zeichnung 04",)),
        ("04번입니다.", ("Zeichnung 4",)),
        ("36개월입니다.", ("Keine Frist.",)),
    ],
)
def test_digits_are_not_glued_or_cut(text: str, sources: tuple[str, ...]):
    """CITE-01, rule 1: a number is one number of one source; a leading zero is part of it."""
    assert unsupported_numbers(text, sources)


@pytest.mark.parametrize(
    "text",
    ["보증 24. 조건입니다.", "1.5배입니다.", "24개월입니다.", "1. 보증은 24개월입니다."],
)
def test_only_a_marker_at_the_start_of_a_line_is_skipped(text: str):
    """§3.2: everything else is checked, including a decimal that starts a line."""
    assert unsupported_numbers(text, ("36 Monate",))


def test_nothing_is_supported_without_sources():
    assert unsupported_numbers("보증은 36개월입니다.", ())


# ── calendar weeks ──────────────────────────────────────────────────────────
WEEK = "Start of deliveries: KW 6/2027."


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("2027년 6주차에 시작합니다.", WEEK),
        ("2027년 제6주차에 시작합니다.", WEEK),
        ("2027년 KW 6에 시작합니다.", WEEK),
        ("KW 6/2027에 시작합니다.", WEEK),
        ("KW 6에 시작합니다.", WEEK),
        ("6주차에 시작합니다.", WEEK),
        ("6주차(2027년)에 시작합니다.", WEEK),
        ("2027년 6주차입니다.", "KW06/2027"),
        ("2027년 6주차입니다.", "KW 06.2027"),
        ("2027년 6주차입니다.", "CW 6, 2027"),
        ("2027년 6주차입니다.", "calendar week 6 of 2027"),
        ("2027년 6주차입니다.", "Kalenderwoche 6 / 2027"),
        ("2027년 6주차입니다.", "6. KW 2027"),
        ("2027년 6주차입니다.", "2027-W06"),
        ("2027년 6주차입니다.", "2027년 KW 6"),
        ("2027년 6주차부터 8주차까지입니다.", "KW 6-8/2027"),
        ("2027년 KW 6~8입니다.", "KW 6 bis 8/2027"),
        ("2026년 50주차부터 2027년 6주차까지입니다.", "KW 50/2026 - 6/2027"),
        ("WK 6입니다.", "wk. 6"),
        ("12주입니다.", "Lead time (weeks) | 12 | pcs"),
        ("12주입니다.", "Lieferzeit in Wochen: 12"),
        ("8주이고 2028년 3월입니다.", "8 weeks | 03/2028"),
        ("0.3입니다.", "cw 0,3"),
        ("150 kW입니다.", "150 kW"),
        ("주당 5회입니다.", "per week 5 deliveries"),
    ],
)
def test_a_calendar_week_is_supported_by_the_same_week(text: str, source: str):
    """CITE-01, rule 5: a week is a week in every notation; a duration in weeks stays a number."""
    assert unsupported_numbers(text, (source,)) == ()


@pytest.mark.parametrize(
    "source",
    [
        "KW 6/2027",
        "KW06/2027",
        "CW 6/2027",
        "WK 6/2027",
        "wk. 06/2027",
        "week 6/2027",
        "Weeks 6/2027",
        "Woche 6/2027",
        "Kalenderwoche 6/2027",
        "Lieferwoche 6/2027",
        "KW Nr. 6/2027",
        "KW: 6/2027",
        "Start (KW) | 6/2027 | 100",
        "KW 5-6/2027",
        "KW 5 und 6/2027",
        "KW 50/2026 - 6/2027",
        "KW 4, 5, 6/2027",
        "W06/2027",
        "2027-W06",
        "Q6/2027",
    ],
)
@pytest.mark.parametrize(
    "text", ["2027년 6월입니다.", "6/2027입니다.", "06/2027입니다.", "2027-06-01입니다."]
)
def test_a_week_is_never_a_month(text: str, source: str):
    """INV-3: "KW 6/2027" let "2027년 6월" pass as verified."""
    assert unsupported_numbers(text, (source,))


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("2027년 7주차입니다.", WEEK),
        ("2028년 6주차입니다.", WEEK),
        ("KW 7입니다.", WEEK),
        ("KW 6/2028입니다.", WEEK),
        ("2028년입니다.", WEEK),
        ("6개입니다.", WEEK),
        ("2027년 6주차입니다.", "KW 6"),
        ("2027년 6주차입니다.", "06/2027"),
        ("2027년 6주차입니다.", "6 Wochen, 2027"),
        ("6주차입니다.", "6 Wochen"),
        ("2027년 7주차입니다.", "KW 6-8/2027"),
        ("2027년 50주차입니다.", "KW 50/2026 - 6/2027"),
        ("2027년 6월 2주차입니다.", "KW 2/2027"),
        ("6월 2주차입니다.", "KW 2/2027"),
    ],
)
def test_another_week_is_refused(text: str, source: str):
    """INV-3: neither another week, nor another year, nor a year the source does not give."""
    assert unsupported_numbers(text, (source,))


@pytest.mark.parametrize(
    "source",
    ["Nr. 6/2027", "No. 6/2027", "Dok.-Nr.: 6/2027", "# 6/2027", "AB-6/2027", "Regulation (EU) 6/2027"],
)
def test_a_document_number_is_not_a_month(source: str):
    """INV-3: "Nr. 6/2027" may be the sixth document of 2027: it is copied or not used."""
    assert unsupported_numbers("2027년 6월입니다.", (source,))
    assert unsupported_numbers("번호는 6/2027입니다.", (source,)) == ()


# ── dates without a space ───────────────────────────────────────────────────
DAYS = ["September 23,2020", "September 23rd, 2020", "Sept. 23,2020", "23 September,2020"]


@pytest.mark.parametrize("source", DAYS)
def test_a_date_is_read_without_a_space_after_the_comma(source: str):
    """CITE-01, rule 2: the text layer of a PDF often loses the space."""
    assert unsupported_numbers("2020년 9월 23일입니다.", (source,)) == ()
    for wrong in ("2020년 9월 24일입니다.", "2020년 8월 23일입니다.", "2021년 9월 23일입니다."):
        assert unsupported_numbers(wrong, (source,))


def test_a_grouped_number_after_a_month_is_not_a_year():
    """INV-3: "Mar 1,500" is an amount."""
    assert unsupported_numbers("1500년 3월 1일입니다.", ("Mar 1,500",))
    assert unsupported_numbers("1,500개입니다.", ("Mar 1,500",)) == ()


# ── thousands given by the column ───────────────────────────────────────────
K_ROW = "Vehicles [k units] | 60 | 120"


@pytest.mark.parametrize(
    "text", ["60 k units입니다.", "60 k입니다.", "60k입니다.", "60 k, 이후 120 k입니다.", "60입니다."]
)
def test_a_number_under_a_thousands_unit_may_be_written_with_k(text: str):
    """CITE-01, rule 3: the same cited sentence states the unit "[k units]"."""
    assert unsupported_numbers(text, (K_ROW,)) == ()
    assert unsupported_numbers(text, ("Parts [k pcs] | 60 | 120",)) == ()


@pytest.mark.parametrize(
    ("text", "sources"),
    [
        ("60 k입니다.", ("Vehicles | 60 | 120",)),
        ("60 k입니다.", ("Weight [kg] | 60 | 120",)),
        ("60 k입니다.", ("Vehicles | 60", "Parts [k pcs] | 70")),
        ("61 k입니다.", (K_ROW,)),
        ("6만 대입니다.", (K_ROW,)),
        ("60,000대입니다.", (K_ROW,)),
        ("60 thousand입니다.", (K_ROW,)),
        ("60 Mio.입니다.", (K_ROW,)),
    ],
)
def test_k_without_its_unit_in_the_same_sentence_is_refused(text: str, sources: tuple[str, ...]):
    """INV-3: nothing is multiplied, and one row does not lend its unit to another."""
    assert unsupported_numbers(text, sources)


# ── identifiers ─────────────────────────────────────────────────────────────
IDENTIFIERS = ["E40", "HC420LA", "5E4.804.117.A", "NW-RFQ-2026-04417", "ISO9001", "NW_2026_A1"]


@pytest.mark.parametrize("token", IDENTIFIERS)
def test_an_identifier_may_stand_in_any_evidence_sentence(token: str):
    """CITE-01, rule 6: a part number is not a figure; that it exists in the evidence is enough."""
    claim = f"{token}은 무상으로 교체합니다."
    assert unsupported_numbers(claim, ("Free of charge.",), context=(f"Part {token}, see drawing.",)) == ()
    assert unsupported_numbers(claim, (f"Part {token}.",)) == ()
    assert unsupported_numbers(claim, ("Free of charge.",), context=("Other part.",)) == (token,)
    assert unsupported_numbers(claim, ("Free of charge.",)) == (token,)


@pytest.mark.parametrize(
    ("text", "seen", "expected"),
    [
        ("E40입니다.", "E400, E41 and XE40.", ("E40",)),
        ("E40입니다.", "e40", ("E40",)),
        ("HC420LA입니다.", "HC420LAD and HC420LA-X", ("HC420LA",)),
        ("5E4.804.117.B입니다.", "5E4.804.117.A", ("5E4.804.117.B",)),
        ("40개입니다.", "E40", ("40",)),
        ("420입니다.", "HC420LA", ("420",)),
    ],
)
def test_only_the_identical_token_counts(text: str, seen: str, expected: tuple[str, ...]):
    """INV-3: not a longer one, not another case; and its digits support no number."""
    assert unsupported_numbers(text, (seen,), context=(seen,)) == expected


@pytest.mark.parametrize(
    "figure",
    [
        "60k", "12mm", "5EUR", "100%", "36-Monats-Frist", "12mm-24mm", "12m3", "5kg/m2", "24h", "10x20mm",
        "EUR5", "EUR1.25", "USD100", "MY2028", "FY2028", "SOP2028", "Cpk1.67", "Rz6.3", "Max10", "Pos.5",
        "mid-2028", "Mar-2028", "15-Mar-2028", "Q1/2027",
    ],
)  # fmt: skip
def test_a_figure_with_a_unit_is_not_an_identifier(figure: str):
    """CITE-01: a value has to stand in a cited sentence, whatever is glued to it."""
    assert unsupported_numbers(f"{figure}입니다.", ("Nothing.",), context=(f"It is {figure} here.",))
    assert unsupported_numbers(f"{figure}입니다.", (f"It is {figure} here.",)) == ()


# ── units ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("보증 기간은 36개월입니다.", "Gewährleistung 36 Tage"),
        ("60일 이내에 지급합니다.", "Zahlungsziel 60 Monate"),
        ("한도는 1.000 EUR입니다.", "Limit 1.000 USD"),
        ("12주입니다.", "12 months"),
        ("24시간입니다.", "24 days"),
        ("5년입니다.", "5 Monate"),
        ("500유로입니다.", "500 $"),
    ],
)
def test_the_same_number_with_another_unit_is_refused(text: str, source: str):
    """INV-3: 36 days do not support 36 months."""
    assert unsupported_numbers(text, (source,))


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("보증 기간은 36개월입니다.", "36 Monate"),
        ("36개월입니다.", "Warranty (months) | 36"),
        ("10영업일입니다.", "10 working days"),
        ("10일입니다.", "10 Arbeitstage"),
        ("48시간입니다.", "480 h / 48 h"),
        ("1.500 EUR입니다.", "EUR 1.500 pauschal"),
        ("90일입니다.", "90 Tage netto, 90 pieces"),
        ("5영업일입니다.", "five (5) business days"),
        ("1,500유로입니다.", "1.500 €"),
    ],
)
def test_the_same_unit_in_another_language_is_supported(text: str, source: str):
    """CITE-01: a unit is compared only when both sides state one of the same kind."""
    assert unsupported_numbers(text, (source,)) == ()
