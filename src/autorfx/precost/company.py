"""The supplier's own figures (SPEC §8, initial setup).

An RFQ states what the customer wants; what it costs the supplier is never in
the RFQ. The company enters that once, in the program: a unit-price table
(material, process, purchased parts, outsourcing, logistics), the five profile
items the first knockout checks (certifications, processes, supply
qualifications, liability limit, payment limit) and a few rates (discount rate,
margin, scrap, SG&A). Answers to knockout questions about facts ("we hold
IATF 16949") are kept here as well, so the same question is not asked twice.

Stored in ``work/_org/company.json``, replaced as a whole. Nothing here is invented: a
value the company has not entered stays empty, and the calculation asks for it instead of guessing.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import Field, ValidationError, field_validator

from autorfx.models import Answer, ItemCategory, Model
from autorfx.rules import Ruleset

log = logging.getLogger(__name__)

COMPANY_FILE = Path("_org") / "company.json"
MAX_PRICES = 300
MAX_ANSWERS = 500
MAX_PROFILE_ENTRIES = 40
PRICE_ID = r"^price-[0-9a-f]{12}$"


class CompanyError(ValueError):
    """Settings that cannot be saved; the message is shown to the user."""


PLURAL = re.compile(r"(?<=[A-Za-z]{3})(?<![sS])[sS]\b")


def canonical(text: str) -> str:
    """How profile entries and RFQ requirements are compared.

    No spaces, hyphens or dots, upper case, and an English plural "s" dropped, so
    "authorized Ford Dealers" and "authorized Ford Dealer" are the same requirement.
    """
    return re.sub(r"[\s\-_.]+", "", PLURAL.sub("", text)).upper()


class PriceRow(Model):
    id: str = Field(pattern=PRICE_ID)
    category: ItemCategory
    name: str = Field(min_length=1, max_length=80)
    unit: str = Field(min_length=1, max_length=16)
    price: Decimal = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    valid_until: date
    department: str


class CompanySettings(Model):
    home_currency: str = Field(pattern=r"^[A-Z]{3}$")
    discount_rate: Decimal = Field(ge=0, le=1)
    min_margin: Decimal = Field(ge=0, lt=1)
    scrap_rate: Decimal = Field(ge=0, lt=1)
    sga_rate: Decimal = Field(ge=0, le=1)
    max_payment_days: int | None = Field(default=None, ge=0, le=365)
    max_liability: Decimal | None = Field(default=None, ge=0)
    """Largest liability cap the company accepts without asking legal, in any currency."""


class CompanyProfile(Model):
    """The five knockout core items: three lists here, the two limits in the settings."""

    certifications: tuple[str, ...] = ()
    processes: tuple[str, ...] = ()
    qualifications: tuple[str, ...] = ()

    @field_validator("certifications", "processes", "qualifications")
    @classmethod
    def _clean(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(dict.fromkeys(v.strip() for v in value if v.strip()))
        if len(cleaned) > MAX_PROFILE_ENTRIES:
            raise ValueError(f"항목은 {MAX_PROFILE_ENTRIES}개까지 넣을 수 있습니다")
        if any(len(v) > 80 for v in cleaned):
            raise ValueError("항목 이름은 80자까지입니다")
        return cleaned

    def has(self, field: str, value: str) -> bool:
        wanted = canonical(value)
        return any(canonical(entry) == wanted for entry in getattr(self, field))


class StoredAnswer(Model):
    """A fact the company confirmed while answering a knockout question."""

    key: str = Field(min_length=1, max_length=200)
    label: str = Field(max_length=300)
    answer: Answer
    saved_on: date


class Company(Model):
    settings: CompanySettings
    profile: CompanyProfile = CompanyProfile()
    prices: tuple[PriceRow, ...] = ()
    answers: tuple[StoredAnswer, ...] = ()

    def price(self, price_id: str) -> PriceRow | None:
        return next((p for p in self.prices if p.id == price_id), None)

    def answer(self, key: str) -> StoredAnswer | None:
        return next((a for a in self.answers if a.key == key), None)


def defaults(ruleset: Ruleset) -> Company:
    d = ruleset.cost_model.company_defaults
    return Company(
        settings=CompanySettings(
            home_currency=d.home_currency,
            discount_rate=Decimal(d.discount_rate),
            min_margin=Decimal(d.min_margin),
            scrap_rate=Decimal(d.scrap_rate),
            sga_rate=Decimal(d.sga_rate),
        )
    )


def _new_id(taken: set[str]) -> str:
    while (candidate := f"price-{secrets.token_hex(6)}") in taken:
        pass
    return candidate


def validate(raw: Mapping, *, ruleset: Ruleset, current: Company) -> Company:
    """Check a whole company record and give new price rows an id. Raises :class:`CompanyError`."""
    prices = list(raw.get("prices") or ())
    if len(prices) > MAX_PRICES:
        raise CompanyError(f"단가표는 {MAX_PRICES}행까지 넣을 수 있습니다.")
    known = {p.id for p in current.prices}
    taken = set(known)
    seen: set[str] = set()
    names: set[tuple[str, str]] = set()
    departments = set(ruleset.department_ids)
    rows = []
    for position, row in enumerate(prices, start=1):
        if not isinstance(row, Mapping):
            raise CompanyError(f"단가표 {position}행의 형식이 올바르지 않습니다.")
        row = dict(row)
        given = row.get("id")
        if given is None:
            row["id"] = _new_id(taken)
        elif given not in known or given in seen:
            raise CompanyError(
                f"단가표 {position}행: 알 수 없는 id입니다. 화면을 새로 고친 뒤 다시 저장하세요."
            )
        taken.add(row["id"])
        seen.add(row["id"])
        if row.get("department") not in departments:
            raise CompanyError(f"단가표 {position}행: 담당 부서를 고르세요.")
        try:
            price = PriceRow.model_validate(row)
        except ValidationError as exc:
            raise CompanyError(f"단가표 {position}행의 값이 올바르지 않습니다: {_first(exc)}") from exc
        name_key = (price.category, price.name.strip().casefold())
        if name_key in names:
            raise CompanyError(f"단가표 {position}행: '{price.name}'이(가) 같은 분류에 두 번 있습니다.")
        names.add(name_key)
        rows.append(price)
    try:
        settings = CompanySettings.model_validate(raw.get("settings") or {})
        profile = CompanyProfile.model_validate(raw.get("profile") or {})
    except ValidationError as exc:
        raise CompanyError(f"회사 설정 값이 올바르지 않습니다: {_first(exc)}") from exc
    return Company(settings=settings, profile=profile, prices=tuple(rows), answers=current.answers)


def _first(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(p) for p in error.get("loc", ()))
    return f"{where} {error.get('msg', '')}".strip()


class CompanyStore:
    """The saved company record, read once and replaced as a whole."""

    def __init__(self, work_dir: Path, ruleset: Ruleset) -> None:
        self.path = work_dir / COMPANY_FILE
        self._ruleset = ruleset
        self._lock = threading.Lock()
        self._company = self._load()

    def _load(self) -> Company:
        if not self.path.is_file():
            return defaults(self._ruleset)
        try:
            return Company.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            log.warning("회사 설정을 읽지 못해 기본값을 씁니다: %s", exc)
            return defaults(self._ruleset)

    @property
    def company(self) -> Company:
        return self._company

    def _write(self, company: Company) -> Company:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(company.model_dump_json(indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self._company = company
        return company

    def replace(self, raw: Mapping) -> Company:
        with self._lock:
            return self._write(validate(raw, ruleset=self._ruleset, current=self._company))

    def remember(self, answers: Sequence[StoredAnswer]) -> Company:
        """Keep fact answers; a newer answer to the same question replaces the older one."""
        with self._lock:
            kept = {a.key: a for a in self._company.answers}
            for answer in answers:
                kept[answer.key] = answer
            if len(kept) > MAX_ANSWERS:
                raise CompanyError(f"저장된 답은 {MAX_ANSWERS}개까지입니다.")
            return self._write(self._company.model_copy(update={"answers": tuple(kept.values())}))
