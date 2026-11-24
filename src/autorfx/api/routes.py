"""HTTP endpoints. Every response has the shape of ``contract/*.schema.json`` (INV-9).

The team comes from the request and decides the access group; an unknown or
missing team is restricted (RBAC-03). Filtering happens in :mod:`autorfx.access`,
before a response exists. There is no authentication: the team is whatever the
client says it is, which is enough for a demo and not for production.
"""

from __future__ import annotations

import decimal
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import Field

from autorfx import access, scoring
from autorfx.api.state import ApiError, AppState, discard, now_ms
from autorfx.export import rtm_workbook
from autorfx.ingest import ACCEPT, IngestError
from autorfx.models import Answer, FileRecord, Group, Model, PrecostInput
from autorfx.org import OrgError
from autorfx.pipeline import RfqData
from autorfx.precost import (
    CompanyError,
    PrecostInputError,
    RfqCostContext,
    StoredAnswer,
    award_mode,
    classify,
    evaluate,
    pass_through,
    prepare,
    requirements,
    rfq_lines,
)
from autorfx.precost.lines import find_grid, header_roles, signature
from autorfx.precost.sheet_store import PriceSheetError, check_columns
from autorfx.retrieval import answer_question
from autorfx.work_areas import catalog

router = APIRouter()
CHUNK = 1024 * 1024
MULTIPART_OVERHEAD = 1024 * 1024  # part headers and boundaries on top of the file bytes (SPEC PKG-03)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REAL_ANALYSIS = {"parsing": "real", "analysis": "real", "citation": "real", "translation": "real"}


class PrecostBody(Model):
    team: str | None = None
    rfq_id: str | None = None
    inputs: PrecostInput
    resolved: dict[str, str] = Field(default_factory=dict)
    remember: bool = True
    """Keep the yes/no answers to fact questions, so the next RFQ does not ask them again."""


class CompanyBody(Model):
    settings: dict
    profile: dict
    prices: list[dict] = Field(max_length=400)


class ChatBody(Model):
    team: str | None = None
    question: str


def state_of(request: Request) -> AppState:
    return request.app.state.autorfx


State = Annotated[AppState, Depends(state_of)]
Team = Annotated[str | None, Query(max_length=64)]


def envelope(data: object, **provenance: str) -> dict:
    return {"data": data, "provenance": provenance}


def _file(data: RfqData, file_id: str) -> FileRecord:
    file = next((f for f in data.manifest.files if f.id == file_id), None)
    if file is None:
        raise ApiError(404, "not_found", "파일을 찾을 수 없습니다.")
    return file


def _cost_visible(group: Group) -> None:
    try:
        access.assert_cost_visible(group)
    except access.AccessDenied as exc:
        raise ApiError(403, "forbidden", str(exc)) from exc


# ── meta and intake ─────────────────────────────────────────────────────────
@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/meta")
def meta(state: State) -> dict:
    rules, settings = state.ruleset, state.settings
    company = state.org.departments
    # The team picker follows the company once it has defined departments (SPEC US10).
    teams = (
        [{"id": d.id, "label": d.label, "group": d.group, "areas": list(d.areas)} for d in company]
        if company
        else [{"id": d.id, "label": d.team_label, "group": rules.group_of(d.id)} for d in rules.departments]
    )
    data = {
        "teams": teams,
        "work_areas": catalog(rules.work_areas),
        "departments": [
            {"id": d.id, "label": d.label, "role": d.role, "receives_all": d.receives_all}
            for d in rules.departments
        ],
        "assumption_note": rules.assumption_note,
        "engines": state.engines,
        # Packages in the development folder are never listed: only uploaded files are analysed (SPEC §13.2).
        "samples": [],
        "upload": {
            "max_files": settings.max_files,
            "max_file_mb": settings.max_file_mb,
            "accept": list(ACCEPT),
        },
    }
    return envelope(data, document="real")


# ── company departments (SPEC US10) ─────────────────────────────────────────
class OrgDepartmentIn(Model):
    id: str | None = None
    label: str = Field(max_length=200)
    areas: list[str] = Field(max_length=50)
    cost_visible: bool = False


class OrgBody(Model):
    departments: list[OrgDepartmentIn] = Field(max_length=200)


def _org_payload(state: AppState) -> dict:
    return envelope(
        {"departments": [d.model_dump(mode="json") for d in state.org.departments]}, document="real"
    )


@router.get("/org/departments")
def org_departments(state: State) -> dict:
    return _org_payload(state)


@router.put("/org/departments")
def save_org_departments(body: OrgBody, state: State) -> dict:
    """Replace the whole list. Nothing is reanalysed: departments only change who sees what."""
    try:
        state.org.replace([d.model_dump() for d in body.departments])
    except OrgError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    return _org_payload(state)


def _bounded(request: Request, limit: int, message: str) -> Request:
    """The same request, whose body is refused with 413 once it grows past ``limit`` bytes.

    ``request.form()`` spools every part before the per-file check can run, so the
    whole body is counted as it arrives; a chunked upload has no length to check first.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ApiError(413, "too_large", message)
    received = 0

    async def receive() -> dict:
        nonlocal received
        event = await request.receive()
        if event["type"] == "http.request":
            received += len(event.get("body", b""))
            if received > limit:
                raise ApiError(413, "too_large", message)
        return event

    return Request(request.scope, receive)


async def _save_uploads(request: Request, state: AppState) -> tuple[list[Path], Path]:
    """Write the uploaded files to a folder of their own; sizes are checked while reading."""
    settings = state.settings
    total = settings.max_files * settings.max_file_mb * 1024 * 1024 + MULTIPART_OVERHEAD
    too_big = (
        f"올린 파일이 너무 큽니다. 한 번에 {settings.max_files}개, "
        f"파일당 {settings.max_file_mb}MB까지 받습니다."
    )
    request = _bounded(request, total, too_big)
    form = await request.form(max_files=settings.max_files, max_fields=settings.max_files + 8)
    uploads = [item for item in form.getlist("files") if hasattr(item, "filename")]
    if not uploads:
        raise ApiError(400, "invalid_request", "파일이 없습니다. RFQ 파일을 하나 이상 올려 주세요.")
    folder = state.upload_root / uuid.uuid4().hex
    folder.mkdir(parents=True)
    limit = settings.max_file_mb * 1024 * 1024
    saved: list[Path] = []
    try:
        for upload in uploads:
            name = Path(upload.filename or "").name  # a client path is never trusted
            if not name or name in {".", ".."}:
                raise ApiError(400, "invalid_request", "파일 이름이 없습니다.")
            target = folder / name
            if target in saved:
                raise ApiError(400, "invalid_request", f"'{name}': 같은 이름의 파일이 두 번 들어왔습니다.")
            size = 0
            with target.open("wb") as handle:
                while chunk := await upload.read(CHUNK):
                    size += len(chunk)
                    if size > limit:
                        message = (
                            f"'{name}': 파일이 너무 큽니다. 파일당 {settings.max_file_mb}MB까지 받습니다."
                        )
                        raise ApiError(413, "too_large", message)
                    await run_in_threadpool(handle.write, chunk)
            saved.append(target)
    except BaseException:
        discard(folder)
        raise
    return saved, folder


@router.post("/rfqs")
async def create_rfq(request: Request, state: State) -> dict:
    """Start an analysis of uploaded files (multipart field ``files``).

    Nothing already on the server can be opened by id (SPEC §13.2).
    """
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise ApiError(400, "invalid_request", "RFQ 파일을 직접 올려 주세요 (multipart 필드 files).")
    sources, cleanup = await _save_uploads(request, state)
    more = f" 외 {len(sources) - 1}개" if len(sources) > 1 else ""
    name, is_sample = f"{sources[0].stem}{more}", False
    try:
        # Validation hashes every file; that must not block the event loop.
        rfq_id, started_at, notice = await run_in_threadpool(
            state.submit, sources, name=name, is_sample=is_sample, cleanup=cleanup
        )
    except IngestError as exc:
        discard(cleanup)
        raise ApiError(400, "invalid_request", str(exc)) from exc
    return envelope({"id": rfq_id, "started_at": started_at, "notice": notice}, document="real")


@router.get("/rfqs/{rfq_id}")
def document(rfq_id: str, state: State) -> dict:
    manifest = state.data(rfq_id).manifest
    return envelope(manifest.model_dump(mode="json", exclude={"created_at"}), document="real")


@router.get("/rfqs/{rfq_id}/job")
def job(rfq_id: str, state: State) -> dict:
    return envelope(state.job(rfq_id).model_dump(mode="json"), document="real")


# ── analysis ────────────────────────────────────────────────────────────────
@router.get("/rfqs/{rfq_id}/clauses")
def clauses(rfq_id: str, state: State, team: Team = None, department: Team = None) -> dict:
    data, group = state.data(rfq_id), state.group_of(team)
    items = access.clause_items(
        data.clauses,
        data.analyses,
        group=group,
        department=department,
        ruleset=state.ruleset,
        areas=state.areas_of(department),
    )
    return envelope({"group": group, "clauses": items}, **REAL_ANALYSIS)


@router.get("/rfqs/{rfq_id}/rtm")
def rtm(rfq_id: str, state: State, team: Team = None) -> dict:
    data, group = state.data(rfq_id), state.group_of(team)
    rows = access.rtm_rows(data.clauses, data.analyses, group=group, ruleset=state.ruleset)
    return envelope({"group": group, "rows": rows}, **REAL_ANALYSIS)


@router.get("/rfqs/{rfq_id}/rtm.xlsx")
def rtm_file(rfq_id: str, state: State, team: Team = None) -> Response:
    data, group = state.data(rfq_id), state.group_of(team)
    rows = access.rtm_rows(data.clauses, data.analyses, group=group, ruleset=state.ruleset)
    content = rtm_workbook(
        rows, manifest=data.manifest, ruleset=state.ruleset, engines=data.engines, group=group
    )
    headers = {
        "Content-Disposition": f'attachment; filename="rtm-{data.manifest.id}.xlsx"',
        "Cache-Control": "no-store",
    }
    return Response(content, media_type=XLSX, headers=headers)


@router.get("/rfqs/{rfq_id}/consistency")
def consistency(rfq_id: str, state: State, team: Team = None) -> dict:
    data, group = state.data(rfq_id), state.group_of(team)
    report = data.consistency
    payload = {
        "group": group,
        "issues": access.issue_items(report.issues, group=group, clauses=data.clauses),
        "checked": {"files": report.files, "entities": report.entities, "references": report.references},
    }
    return envelope(payload, consistency="real")


# ── source ──────────────────────────────────────────────────────────────────
@router.get("/rfqs/{rfq_id}/files/{file_id}/pages/{page_number}/sentences")
def sentences(rfq_id: str, file_id: str, page_number: int, state: State, team: Team = None) -> dict:
    data, group = state.data(rfq_id), state.group_of(team)
    file = _file(data, file_id)
    own = [c for c in data.clauses if c.source_doc == file.id]
    try:
        found = access.sentences_for(data.parsed[file.id], own, group=group, page_number=page_number)
    except ValueError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    return envelope({"source_doc": file.id, "page_number": page_number, "sentences": found}, source="real")


@router.get("/rfqs/{rfq_id}/files/{file_id}/source")
def source(rfq_id: str, file_id: str, state: State, team: Team = None) -> FileResponse:
    data, group = state.data(rfq_id), state.group_of(team)
    file = _file(data, file_id)
    try:
        path = access.source_pdf(data.workspace, file, group=group)
    except access.AccessDenied as exc:
        raise ApiError(403, "forbidden", str(exc)) from exc
    # The content depends on the team, so it must not be cached under one URL.
    headers = {"Cache-Control": "no-store", "Content-Disposition": f'inline; filename="{file.id}.pdf"'}
    return FileResponse(path, media_type="application/pdf", headers=headers)


# ── pre-cost ────────────────────────────────────────────────────────────────
def _company_payload(state: AppState) -> dict:
    company = state.company.company
    return envelope(
        {
            **company.model_dump(mode="json"),
            "today": date.today().isoformat(),
            "categories": state.ruleset.cost_model.item_categories,
        },
        precost="real",
    )


@router.get("/company")
def company(state: State, team: Team = None) -> dict:
    """Initial setup: unit prices, profile and rates. Cost figures, so cost-visible teams only."""
    _cost_visible(state.group_of(team))
    return _company_payload(state)


@router.put("/company")
def save_company(body: CompanyBody, state: State, team: Team = None) -> dict:
    """Replace the settings, profile and price table as a whole; stored answers are kept."""
    _cost_visible(state.group_of(team))
    try:
        state.company.replace(body.model_dump())
    except CompanyError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    return _company_payload(state)


def _context(state: AppState, rfq_id: str | None) -> RfqCostContext:
    if rfq_id is None:
        return RfqCostContext()
    data = state.data(rfq_id)
    company = state.company.company
    found = pass_through(data.clauses, state.ruleset)
    return RfqCostContext(
        issues=tuple(i for i in data.consistency.issues if i.affects_cost),
        classification=classify(data.clauses, state.ruleset),
        requirements=tuple(requirements(data.clauses, data.mentions, company, state.ruleset)),
        pass_through=tuple(k for k, v in found.items() if v),
        red_flag_types=tuple(sorted({a.red_flag.type for a in data.analyses if a.red_flag})),
        award=award_mode(data.clauses, state.ruleset)["mode"],
    )


@router.get("/rfqs/{rfq_id}/precost-setup")
def precost_setup(rfq_id: str, state: State, team: Team = None) -> dict:
    """Steps ①–⑤ for one RFQ: type, knockout questions, cost inputs with citations, suggested items."""
    _cost_visible(state.group_of(team))
    data = state.data(rfq_id)
    result = prepare(
        cost_inputs=data.cost_inputs,
        clauses=data.clauses,
        mentions=data.mentions,
        company=state.company.company,
        ruleset=state.ruleset,
        today=date.today(),
        lines=_lines(state, data),
    )
    return envelope(result, precost="real")


def _canonical(data: RfqData) -> dict[str, Path]:
    return {f.id: data.workspace.canonical_pdf(f.id) for f in data.manifest.files}


def _lines(state: AppState, data: RfqData) -> dict:
    store = state.price_sheets
    return rfq_lines(
        data.manifest,
        data.parsed,
        _canonical(data),
        state.ruleset.price_sheet,
        words=store.approved,
        layouts=store.layouts,
    )


class ColumnsBody(Model):
    table: str = Field(min_length=1, max_length=120)
    header_row: int = Field(ge=0, le=5)
    columns: dict[str, int]


@router.post("/rfqs/{rfq_id}/price-sheet/columns")
def assign_columns(rfq_id: str, body: ColumnsBody, state: State, team: Team = None) -> dict:
    """Read a table by the columns a user assigned; the same header is read this way from now on."""
    _cost_visible(state.group_of(team))
    data = state.data(rfq_id)
    rules = state.ruleset.price_sheet
    grid = find_grid(data.manifest, data.parsed, _canonical(data), rules, body.table)
    if grid is None:
        raise ApiError(404, "not_found", "표를 찾을 수 없습니다. 화면을 새로 고쳐 주세요.")
    if body.header_row >= len(grid.rows):
        raise ApiError(400, "invalid_request", "머리글 줄이 표 밖입니다.")
    header = grid.rows[body.header_row]
    try:
        columns = check_columns(body.columns, len(header))
    except PriceSheetError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    # Header words the rules would have given another role (or none) are dictionary candidates.
    known = header_roles(header, rules.compiled(state.price_sheets.approved))
    words = {role: header[i] for role, i in columns.items() if known.get(role) != i and header[i].strip()}
    state.price_sheets.assign(signature(header), columns, words, date.today())
    return envelope({"lines": _lines(state, data)}, precost="real")


def _words_payload(state: AppState) -> dict:
    store = state.price_sheets
    return envelope(
        {
            "candidates": [c.model_dump(mode="json") for c in store.candidates],
            "approved": [
                {"role": role, "text": text} for role, words in store.approved.items() for text in words
            ],
        },
        precost="real",
    )


@router.get("/price-sheet/words")
def price_sheet_words(state: State, team: Team = None) -> dict:
    """Header words users assigned by hand, waiting for approval, and the approved ones."""
    _cost_visible(state.group_of(team))
    return _words_payload(state)


class WordBody(Model):
    role: str = Field(max_length=20)
    text: str = Field(min_length=1, max_length=80)
    action: Literal["approve", "reject", "remove"]


@router.post("/price-sheet/words")
def decide_word(body: WordBody, state: State, team: Team = None) -> dict:
    _cost_visible(state.group_of(team))
    try:
        state.price_sheets.decide(body.role, body.text, body.action)
    except PriceSheetError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    return _words_payload(state)


@router.post("/precost")
def precost(body: PrecostBody, state: State) -> dict:
    _cost_visible(state.group_of(body.team))
    context = _context(state, body.rfq_id)
    try:
        result = evaluate(
            body.inputs,
            context=context,
            company=state.company.company,
            resolved=body.resolved,
            ruleset=state.ruleset,
            today=date.today(),
        )
    except PrecostInputError as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc
    except decimal.DecimalException as exc:  # "1E+999999999" overflows the arithmetic context
        raise ApiError(
            422, "invalid_request", "계산할 수 없는 크기의 값이 있습니다. 입력을 확인해 주세요."
        ) from exc
    if body.remember:
        facts = [
            StoredAnswer(
                key=c["key"], label=c["question"], answer=_answer(c["answer"]), saved_on=date.today()
            )
            for c in result["knockout"]["checks"]
            if c["kind"] == "fact" and c["source"] == "answer" and c["answer"] in ("yes", "no")
        ]
        if facts:
            try:
                state.company.remember(facts)
            except CompanyError as exc:
                raise ApiError(400, "invalid_request", str(exc)) from exc
    return envelope(result, precost="real")


def _answer(value: str) -> Answer:
    return "yes" if value == "yes" else "no"


# ── chat and evaluation ─────────────────────────────────────────────────────
@router.post("/rfqs/{rfq_id}/chat")
def chat(rfq_id: str, body: ChatBody, state: State) -> dict:
    data, group = state.data(rfq_id), state.group_of(body.team)
    try:
        answer = answer_question(
            body.question, index=data.index, clauses=data.clauses, group=group, provider=state.provider
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc
    return envelope(answer, chat="real")


@router.get("/rfqs/{rfq_id}/evaluation")
def evaluation(rfq_id: str, state: State, team: Team = None) -> dict:
    data, group = state.data(rfq_id), state.group_of(team)
    try:
        key = scoring.find_key(data.manifest, state.settings.answer_keys_dir)
    except scoring.AnswerKeyError as exc:
        raise ApiError(500, "answer_key", str(exc)) from exc
    withheld = access.withheld_ids(data.clauses, data.analyses, data.consistency.issues, group=group)
    result = scoring.evaluate(
        data.manifest, data.analyses, data.consistency.issues, key, now_ms=now_ms(), withheld=withheld
    )
    return envelope(result, evaluation="real")
