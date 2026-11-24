"""Company departments (SPEC US10, §7).

A company names its own departments and picks, for each, which rule departments
("업무 영역", ``data/rules/departments.yaml``) it receives. Classification never
changes: clauses keep their rule departments, and a company department sees the
union of its areas. Its access group is stored with it and is ``cost_restricted``
unless cost visibility was switched on, so a new department fails closed.

The list lives in ``work/_org/departments.json`` and is replaced as a whole. The
leading underscore keeps the folder from looking like an RFQ workspace.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field, ValidationError

from autorfx.models import Group, Model
from autorfx.rules import Ruleset
from autorfx.work_areas import canonical_area

log = logging.getLogger(__name__)

ORG_DIR = "_org"
MAX_DEPARTMENTS = 30
MAX_LABEL = 40
ID_PATTERN = r"^org-[0-9a-f]{12}$"


class OrgError(ValueError):
    """A list that cannot be saved; the message is shown to the user."""


class OrgDepartment(Model):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(min_length=1, max_length=MAX_LABEL)
    areas: tuple[str, ...] = Field(min_length=1)
    cost_visible: bool = False

    @property
    def group(self) -> Group:
        return "cost_visible" if self.cost_visible else "cost_restricted"


class _Stored(Model):
    departments: tuple[OrgDepartment, ...] = ()


def _new_id(taken: set[str]) -> str:
    while (candidate := f"org-{secrets.token_hex(6)}") in taken:
        pass
    return candidate


def validate(
    raw: Sequence[dict], *, ruleset: Ruleset, current: Sequence[OrgDepartment]
) -> tuple[OrgDepartment, ...]:
    """Check a whole list and give new departments an id. Raises :class:`OrgError`."""
    if len(raw) > MAX_DEPARTMENTS:
        raise OrgError(f"부서는 {MAX_DEPARTMENTS}개까지 둘 수 있습니다.")
    known_areas = set(ruleset.department_ids) | {a.id for a in ruleset.work_areas.areas}
    known_ids = {d.id for d in current}
    taken = set(known_ids)
    seen_labels: set[str] = set()
    seen_ids: set[str] = set()
    result: list[OrgDepartment] = []
    for position, item in enumerate(raw, start=1):
        label = str(item.get("label") or "").strip()
        if not label:
            raise OrgError(f"{position}번째 부서의 이름이 비어 있습니다.")
        if len(label) > MAX_LABEL:
            raise OrgError(f"'{label[:MAX_LABEL]}…': 부서 이름은 {MAX_LABEL}자까지입니다.")
        if label.casefold() in seen_labels:
            raise OrgError(f"'{label}': 같은 이름의 부서가 두 번 있습니다.")
        seen_labels.add(label.casefold())

        # Settings saved with the earlier fine-grained work areas load as the grouped ones.
        areas = tuple(
            dict.fromkeys(canonical_area(ruleset.work_areas, str(a)) for a in item.get("areas") or ())
        )
        if not areas:
            raise OrgError(f"'{label}': 받을 업무 영역을 하나 이상 고르세요.")
        if unknown := [a for a in areas if a not in known_areas]:
            raise OrgError(f"'{label}': 없는 업무 영역입니다 ({', '.join(unknown)}).")

        given = item.get("id")
        if given is None:
            dept_id = _new_id(taken)
        elif given in known_ids and given not in seen_ids:
            dept_id = given
        else:
            raise OrgError(f"'{label}': 알 수 없는 부서 id입니다. 화면을 새로 고친 뒤 다시 저장하세요.")
        taken.add(dept_id)
        seen_ids.add(dept_id)

        cost_visible = item.get("cost_visible", False)
        if not isinstance(cost_visible, bool):
            raise OrgError(f"'{label}': 금액 열람 값이 올바르지 않습니다.")
        result.append(OrgDepartment(id=dept_id, label=label, areas=areas, cost_visible=cost_visible))
    return tuple(result)


class OrgStore:
    """The saved company departments, read once and replaced as a whole."""

    def __init__(self, work_dir: Path, ruleset: Ruleset) -> None:
        self.path = work_dir / ORG_DIR / "departments.json"
        self._ruleset = ruleset
        self._lock = threading.Lock()
        self._departments = self._load()

    def _load(self) -> tuple[OrgDepartment, ...]:
        if not self.path.is_file():
            return ()
        try:
            stored = _Stored.model_validate_json(self.path.read_text(encoding="utf-8"))
            raw = [d.model_dump(mode="json") for d in stored.departments]
            return validate(raw, ruleset=self._ruleset, current=stored.departments)
        except (OSError, ValidationError, OrgError) as exc:
            # Fails closed: without the list every company team is unknown, so restricted (RBAC-03).
            log.warning("회사 부서 설정을 읽지 못해 기본 부서를 씁니다: %s", exc)
            return ()

    @property
    def departments(self) -> tuple[OrgDepartment, ...]:
        return self._departments

    def find(self, team: str | None) -> OrgDepartment | None:
        if not team:
            return None
        return next((d for d in self._departments if d.id == team), None)

    def replace(self, raw: Sequence[dict]) -> tuple[OrgDepartment, ...]:
        with self._lock:
            departments = validate(raw, ruleset=self._ruleset, current=self._departments)
            payload = {"departments": [d.model_dump(mode="json") for d in departments]}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self._departments = departments
            return departments
