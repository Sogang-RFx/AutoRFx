"""What one request may see of one RFQ (docs/auth-rbac.md §4).

A data route takes an :class:`RfqView`: the account, its scope, the stored results and the clauses of them
that are visible, decided once. It also has the two answers that come before any results exist, which may
name a file: the progress of a job and the list of packages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from autorfx import access
from autorfx.api.security import CurrentScope, CurrentViewer, State
from autorfx.api.state import ApiError, AppState
from autorfx.auth import Scope, Viewer
from autorfx.models import Clause, JobState
from autorfx.pipeline import RfqData

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RfqView:
    viewer: Viewer
    scope: Scope
    data: RfqData
    visible: tuple[Clause, ...]
    secret: bytes
    """Key of the opaque ids of locked rows."""


def rfq_data(state: AppState, rfq_id: str, scope: Scope) -> RfqData:
    """The stored results. Why a job failed may name a file, so only the full scope is told."""
    try:
        return state.data(rfq_id)
    except ApiError as exc:
        if scope.is_full or exc.code != "job_failed":
            raise
        stopped = next((s.label for s in state.job(rfq_id).stages if s.status == "error"), None)
        where = f" ({stopped})" if stopped else ""
        raise ApiError(exc.status, exc.code, f"분석이 중단됐습니다{where}.") from None


def rfq_view(rfq_id: str, viewer: CurrentViewer, scope: CurrentScope, state: State) -> RfqView:
    data = rfq_data(state, rfq_id, scope)
    return RfqView(
        viewer=viewer,
        scope=scope,
        data=data,
        visible=access.visible_clauses(data.clauses, data.analyses, scope, state.ruleset),
        secret=state.auth.secret(),
    )


CurrentRfq = Annotated[RfqView, Depends(rfq_view)]


def job_view(job: JobState, scope: Scope) -> JobState:
    """Stage names and states; the details (counts, file names) are for the full scope."""
    if scope.is_full:
        return job
    return job.model_copy(
        update={"stages": tuple(stage.model_copy(update={"detail": ""}) for stage in job.stages)}
    )


def rfq_list(state: AppState, scope: Scope) -> list[dict]:
    """The packages on the server. A name is a file name: it is shown by the rule of the document itself,
    and not at all when there are no current results to apply that rule to."""
    listed = state.rfqs(show_names=scope.is_full)
    if scope.is_full:
        return listed
    return [
        {**row, "name": _visible_name(state, row["id"], scope)} if row["status"] == "done" else row
        for row in listed
    ]


def _visible_name(state: AppState, rfq_id: str, scope: Scope) -> str:
    hidden = access.anonymous_name(rfq_id)
    try:
        # Only what the decision needs is read: no search index is built for a list.
        workspace, job = state.workspace(rfq_id), state.job(rfq_id)
        manifest = workspace.manifest()
        visible = access.visible_clauses(workspace.clauses(), workspace.analysis(), scope, state.ruleset)
        if not job.done or state.job(rfq_id) != job:
            return hidden  # analysed again meanwhile: the files read may be of two runs
    except (ApiError, OSError, ValueError):
        log.exception("the stored results of %s cannot be read; its name is not shown", rfq_id)
        return hidden
    return access.package_view(manifest, visible, scope=scope).name
