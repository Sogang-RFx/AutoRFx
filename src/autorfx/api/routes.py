"""HTTP endpoints. Every response has the shape of ``contract/*.schema.json`` (INV-9).

Who asks comes from the session (:mod:`autorfx.api.security`), never from the request. What they may see
of an RFQ is decided once per request (:mod:`autorfx.api.scoping`) by :mod:`autorfx.access`, and a response
is built from that decision. The pre-cost routes are the one exception (docs/auth-rbac.md §2): departments
that may see cost compute with the whole RFQ, so that the result does not depend on who asks.
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
from autorfx.api.scoping import CurrentRfq, RfqView, job_view, rfq_data, rfq_list
from autorfx.api.security import (
    AdminViewer,
    CurrentGroup,
    CurrentScope,
    CurrentViewer,
    State,
    UploadingViewer,
    current_viewer,
)
from autorfx.api.state import ApiError, AppState, discard, now_ms
from autorfx.auth import Scope, can_manage
from autorfx.chat.models import ChatContext, Turn
from autorfx.chat.service import answer_question
from autorfx.export import rtm_workbook
from autorfx.ingest import ACCEPT, IngestError
from autorfx.models import Answer, FileRecord, Group, Model, PrecostInput
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
from autorfx.precost.lines import LAYOUT_SCAN, find_grid, header_roles, signature
from autorfx.precost.sheet_store import PriceSheetError, check_columns, header_label
from autorfx.precost.sheet_suggest import SuggestError, suggest_columns
from autorfx.scoped_pdf import scoped_pdf

open_router = APIRouter()
router = APIRouter(dependencies=[Depends(current_viewer)])
CHUNK = 1024 * 1024
MULTIPART_OVERHEAD = 1024 * 1024  # part headers and boundaries on top of the file bytes (SPEC PKG-03)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REAL_ANALYSIS = {"parsing": "real", "analysis": "real", "citation": "real", "translation": "real"}


class PrecostBody(Model):
    rfq_id: str | None = None
    inputs: PrecostInput
    resolved: dict[str, str] = Field(default_factory=dict)
    remember: bool = True
    """Keep the yes/no answers to fact questions, so the next RFQ does not ask them again.
    Stored for an administrator only."""


class CompanyBody(Model):
    settings: dict
    profile: dict
    prices: list[dict] = Field(max_length=400)


class ChatTurn(Model):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(max_length=4000)


class ChatBody(Model):
    question: str
    history: tuple[ChatTurn, ...] = Field(default=(), max_length=6)


Department = Annotated[str | None, Query(max_length=64)]
"""A view filter: it narrows what the account sees and never widens it."""


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
@open_router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/meta")
def meta(state: State) -> dict:
    rules, settings = state.ruleset, state.settings
    data = {
        "departments": [
            {
                "id": d.id,
                "label": d.label,
                "role": d.role,
                "receives_all": d.receives_all,
                "cost_visible": d.cost_visible,
            }
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


@router.get("/rfqs")
def rfqs(scope: CurrentScope, state: State) -> dict:
    """The packages on the server."""
    return envelope({"rfqs": rfq_list(state, scope)}, document="real")


@router.post("/rfqs")
async def create_rfq(request: Request, viewer: UploadingViewer, state: State) -> dict:
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
    await run_in_threadpool(state.audit.record, "upload", actor=viewer.username, target=rfq_id)
    return envelope({"id": rfq_id, "started_at": started_at, "notice": notice}, document="real")


@router.get("/rfqs/{rfq_id}")
def document(rfq: CurrentRfq) -> dict:
    manifest = access.package_view(rfq.data.manifest, rfq.visible, scope=rfq.scope)
    return envelope(manifest.model_dump(mode="json", exclude={"created_at"}), document="real")


@router.get("/rfqs/{rfq_id}/job")
def job(rfq_id: str, scope: CurrentScope, state: State) -> dict:
    return envelope(job_view(state.job(rfq_id), scope).model_dump(mode="json"), document="real")


# ── analysis ────────────────────────────────────────────────────────────────
@router.get("/rfqs/{rfq_id}/clauses")
def clauses(rfq: CurrentRfq, state: State, department: Department = None) -> dict:
    items = access.clause_items(
        rfq.data.clauses,
        rfq.data.analyses,
        scope=rfq.scope,
        ruleset=state.ruleset,
        department=department,
        secret=rfq.secret,
    )
    return envelope({"group": rfq.scope.group, "clauses": items}, **REAL_ANALYSIS)


def _rtm(rfq: RfqView, state: AppState) -> list[dict]:
    return access.rtm_rows(
        rfq.data.clauses, rfq.data.analyses, scope=rfq.scope, ruleset=state.ruleset, secret=rfq.secret
    )


@router.get("/rfqs/{rfq_id}/rtm")
def rtm(rfq: CurrentRfq, state: State) -> dict:
    return envelope({"group": rfq.scope.group, "rows": _rtm(rfq, state)}, **REAL_ANALYSIS)


@router.get("/rfqs/{rfq_id}/rtm.xlsx")
def rtm_file(rfq: CurrentRfq, state: State) -> Response:
    rfq_id = rfq.data.manifest.id
    content = rtm_workbook(
        _rtm(rfq, state),
        manifest=access.package_view(rfq.data.manifest, rfq.visible, scope=rfq.scope),
        ruleset=state.ruleset,
        engines=rfq.data.engines,
        group=rfq.scope.group,
        view=access.scope_label(rfq.scope, state.ruleset),
    )
    state.audit.record("download_rtm", actor=rfq.viewer.username, target=rfq_id)
    headers = {
        "Content-Disposition": f'attachment; filename="rtm-{rfq_id}.xlsx"',
        "Cache-Control": "no-store",
    }
    return Response(content, media_type=XLSX, headers=headers)


@router.get("/rfqs/{rfq_id}/consistency")
def consistency(rfq: CurrentRfq) -> dict:
    report = rfq.data.consistency
    payload = {
        "group": rfq.scope.group,
        "issues": access.issue_items(report.issues, scope=rfq.scope, visible=rfq.visible, secret=rfq.secret),
        "checked": {"files": report.files, "entities": report.entities, "references": report.references},
    }
    return envelope(payload, consistency="real")


# ── source ──────────────────────────────────────────────────────────────────
@router.get("/rfqs/{rfq_id}/files/{file_id}/pages/{page_number}/sentences")
def sentences(file_id: str, page_number: int, rfq: CurrentRfq) -> dict:
    file = _file(rfq.data, file_id)
    try:
        found = access.sentences_for(
            rfq.data.parsed[file.id], rfq.visible, scope=rfq.scope, page_number=page_number
        )
    except ValueError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    return envelope({"source_doc": file.id, "page_number": page_number, "sentences": found}, source="real")


@router.get("/rfqs/{rfq_id}/files/{file_id}/source")
def source(file_id: str, rfq: CurrentRfq, state: State) -> FileResponse:
    data, actor = rfq.data, rfq.viewer.username
    file = _file(data, file_id)
    target = f"{data.manifest.id}/{file.id}"
    try:
        path = scoped_pdf(data.workspace, file, data.parsed[file.id], rfq.visible, rfq.scope)
    except access.AccessDenied as exc:
        state.audit.record("download_source", actor=actor, target=target, outcome="denied")
        raise ApiError(403, "forbidden", str(exc)) from exc
    state.audit.record("download_source", actor=actor, target=target)
    # The content depends on the account, so it must not be cached under one URL.
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
def company(state: State, group: CurrentGroup) -> dict:
    """Initial setup: unit prices, profile and rates. Cost figures, so cost-visible teams only."""
    _cost_visible(group)
    return _company_payload(state)


@router.put("/company")
def save_company(body: CompanyBody, viewer: AdminViewer, state: State) -> dict:
    """Replace the settings, profile and price table as a whole; stored answers are kept."""
    try:
        state.company.replace(body.model_dump())
    except CompanyError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    state.audit.record("company_save", actor=viewer.username)
    return _company_payload(state)


def _context(state: AppState, rfq_id: str | None, scope: Scope) -> RfqCostContext:
    if rfq_id is None:
        return RfqCostContext()
    data = rfq_data(state, rfq_id, scope)
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
def precost_setup(rfq_id: str, state: State, scope: CurrentScope) -> dict:
    """Steps ①–⑤ for one RFQ: type, knockout questions, cost inputs with citations, suggested items."""
    _cost_visible(scope.group)
    data = rfq_data(state, rfq_id, scope)
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
        rendered={
            f.id: layout
            for f in data.manifest.files
            if f.kind == "xlsx" and (layout := data.workspace.layout(f.id)) is not None
        },
    )


class ColumnsBody(Model):
    table: str = Field(min_length=1, max_length=120)
    header_row: int = Field(ge=0, lt=LAYOUT_SCAN)
    columns: dict[str, int]


class SuggestBody(Model):
    table: str = Field(min_length=1, max_length=120)


@router.post("/rfqs/{rfq_id}/price-sheet/suggest")
def suggest_sheet_columns(rfq_id: str, body: SuggestBody, state: State, scope: CurrentScope) -> dict:
    """An AI suggestion for the columns of one table: positions only, checked by reading the table.

    Nothing is stored: the user reviews the suggestion in the column assigner and accepts it there
    (``/price-sheet/columns``). Only the first rows of this one table are sent to the model.
    """
    _cost_visible(scope.group)
    data = rfq_data(state, rfq_id, scope)
    rules = state.ruleset.price_sheet
    grid = find_grid(
        data.manifest, data.parsed, _canonical(data), rules, body.table, words=state.price_sheets.approved
    )
    if grid is None:
        raise ApiError(404, "not_found", "표를 찾을 수 없습니다. 화면을 새로 고쳐 주세요.")
    try:
        suggestion = suggest_columns(grid, state.provider, rules.compiled(state.price_sheets.approved))
    except SuggestError as exc:
        raise ApiError(422, "no_suggestion", str(exc)) from exc
    return envelope(suggestion, precost="real")


@router.post("/rfqs/{rfq_id}/price-sheet/columns")
def assign_columns(
    rfq_id: str, body: ColumnsBody, viewer: CurrentViewer, state: State, scope: CurrentScope
) -> dict:
    """Read a table by the columns a user assigned; the same header is read this way from now on."""
    _cost_visible(scope.group)
    data = rfq_data(state, rfq_id, scope)
    rules = state.ruleset.price_sheet
    # Pass approved words so unruled tables found via the dictionary are found again on re-assign.
    grid = find_grid(
        data.manifest,
        data.parsed,
        _canonical(data),
        rules,
        body.table,
        words=state.price_sheets.approved,
    )
    if grid is None:
        raise ApiError(404, "not_found", "표를 찾을 수 없습니다. 화면을 새로 고쳐 주세요.")
    if body.header_row >= len(grid.rows):
        raise ApiError(400, "invalid_request", "머리글 줄이 표 밖입니다.")
    header = grid.rows[body.header_row]
    if sum(1 for c in header if c and str(c).strip()) < 2:
        raise ApiError(
            400, "invalid_request", "머리글 줄에 열이 너무 적습니다. 빈 줄이나 제목 줄은 머리글이 아닙니다."
        )
    # Row lengths can differ (ragged PDF tables); use the grid width, not this header alone.
    width = max((len(row) for row in grid.rows), default=len(header))
    try:
        columns = check_columns(body.columns, width)
    except PriceSheetError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    # Header words the rules would have given another role (or none) are dictionary candidates.
    # Values may sit one cell right of the label; resolve the label column, not the value cell.
    known = header_roles(header, rules.compiled(state.price_sheets.approved))
    claimed = set(columns.values())
    words: dict[str, str] = {}
    for role, index in columns.items():
        label, label_col = header_label(header, index, claimed)
        if label and known.get(role) != label_col:
            words[role] = label
    state.price_sheets.assign(signature(header), columns, words, date.today())
    state.audit.record("price_sheet_columns", actor=viewer.username, target=f"{data.manifest.id}/{grid.id}")
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
def price_sheet_words(state: State, group: CurrentGroup) -> dict:
    """Header words users assigned by hand, waiting for approval, and the approved ones."""
    _cost_visible(group)
    return _words_payload(state)


class WordBody(Model):
    role: str = Field(max_length=20)
    text: str = Field(min_length=1, max_length=80)
    action: Literal["approve", "reject", "remove"]


@router.post("/price-sheet/words")
def decide_word(body: WordBody, viewer: CurrentViewer, state: State, group: CurrentGroup) -> dict:
    _cost_visible(group)
    try:
        state.price_sheets.decide(body.role, body.text, body.action)
    except PriceSheetError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    # The word itself is a header of a customer's table, so only what was done is written.
    state.audit.record("price_sheet_word", actor=viewer.username, detail=f"{body.action} role={body.role}")
    return _words_payload(state)


@router.post("/precost")
def precost(body: PrecostBody, viewer: CurrentViewer, state: State, scope: CurrentScope) -> dict:
    _cost_visible(scope.group)
    context = _context(state, body.rfq_id, scope)
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
    if body.remember and can_manage(viewer):  # others may ask for it; nothing is stored
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
def chat(body: ChatBody, rfq: CurrentRfq, state: State) -> dict:
    data, scope, visible = rfq.data, rfq.scope, rfq.visible
    visible_ids = frozenset(c.clause_id for c in visible)
    context = ChatContext(
        index=data.index.scoped(visible_ids),
        clauses=visible,
        analyses=tuple(a for a in data.analyses if a.clause_id in visible_ids),
        issues=access.visible_issues(data.consistency.issues, scope=scope, visible=visible),
        files=access.package_view(data.manifest, visible, scope=scope).files,
        topics=state.chat_topics,
    )
    history = tuple(Turn(question=t.question, answer=t.answer) for t in body.history)
    try:
        answer = answer_question(
            body.question, context=context, scope=scope, model=state.chat_model, history=history
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc
    return envelope(answer, chat="real")


@router.get("/rfqs/{rfq_id}/evaluation")
def evaluation(rfq: CurrentRfq, state: State) -> dict:
    data, scope = rfq.data, rfq.scope
    try:
        key = scoring.find_key(data.manifest, state.settings.answer_keys_dir)
    except scoring.AnswerKeyError as exc:
        raise ApiError(500, "answer_key", str(exc)) from exc
    issues = data.consistency.issues
    withheld = (
        None
        if scope.is_full
        else access.withheld_ids(data.clauses, data.analyses, issues, scope=scope, ruleset=state.ruleset)
    )
    result = scoring.evaluate(data.manifest, data.analyses, issues, key, now_ms=now_ms(), withheld=withheld)
    return envelope(result, evaluation="real")
