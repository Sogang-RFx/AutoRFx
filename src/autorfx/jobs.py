"""Processing stages and their progress (UI-01).

The pipeline reports to a :class:`JobReporter`; the API serves what was
recorded. Progress is a stored fact, not a simulation: a stage is ``done``
only after its output has been written, and a job that died never keeps
showing ``running`` (see :func:`close_interrupted`).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from autorfx.models import JobStage, JobState, StageStatus
from autorfx.workspace import Workspace


@dataclass(frozen=True)
class StageDef:
    id: str
    label: str
    part: str
    """The contract part this stage produces (PARTS in contract.ts)."""


STAGES: tuple[StageDef, ...] = (
    StageDef("store", "원본 보관", "document"),
    StageDef("canonical", "기준 PDF 변환", "source"),
    StageDef("parse", "문장·조항 파싱", "parsing"),
    StageDef("translate", "번역", "translation"),
    StageDef("route", "부서 분류", "analysis"),
    StageDef("assess", "요건 강도·독소조항", "analysis"),
    StageDef("verify", "인용 검증", "citation"),
    StageDef("consistency", "문서 간 정합성", "consistency"),
)
STAGE_IDS = tuple(s.id for s in STAGES)

INTERRUPTED = "서버가 다시 시작되어 처리가 중단됐습니다. 다시 올려 주세요."


class JobReporter(Protocol):
    """What the pipeline calls. Implementations must be safe to call from several threads."""

    def update(self, stage_id: str, status: StageStatus, detail: str = "") -> None: ...

    def fail(self, stage_id: str, message: str) -> None: ...

    def finish(self) -> None: ...


class NullReporter:
    """Reporter for the command line and tests that do not look at progress."""

    def update(self, stage_id: str, status: StageStatus, detail: str = "") -> None:
        return None

    def fail(self, stage_id: str, message: str) -> None:
        return None

    def finish(self) -> None:
        return None


def initial_state(started_at: int) -> JobState:
    return JobState(
        started_at=started_at,
        done=False,
        stages=tuple(
            JobStage(id=s.id, label=s.label, detail="", part=s.part, status="pending") for s in STAGES
        ),
    )


def _with_stage(state: JobState, stage_id: str, status: StageStatus, detail: str) -> JobState:
    if stage_id not in STAGE_IDS:
        raise KeyError(f"알 수 없는 단계: {stage_id}")
    stages = tuple(
        s.model_copy(update={"status": status, "detail": detail}) if s.id == stage_id else s
        for s in state.stages
    )
    return state.model_copy(update={"stages": stages})


class JobStore:
    """File-backed job state of one RFQ: ``job.json`` in its workspace."""

    def __init__(self, workspace: Workspace, started_at: int) -> None:
        self._workspace = workspace
        self._lock = threading.Lock()
        self._state = initial_state(started_at)

    def start(self) -> JobState:
        with self._lock:
            self._save()
            return self._state

    def update(self, stage_id: str, status: StageStatus, detail: str = "") -> None:
        with self._lock:
            self._state = _with_stage(self._state, stage_id, status, detail)
            self._save()

    def fail(self, stage_id: str, message: str) -> None:
        """Stop at this stage. Later stages stay ``pending``; the job is not ``done``."""
        self.update(stage_id, "error", message)

    def finish(self) -> None:
        with self._lock:
            self._state = self._state.model_copy(update={"done": True})
            self._save()

    @property
    def state(self) -> JobState:
        with self._lock:
            return self._state

    def _save(self) -> None:
        self._workspace.write_model(self._workspace.job_path, self._state)


def load_job(workspace: Workspace) -> JobState | None:
    path = workspace.job_path
    return workspace.read_model(path, JobState) if path.is_file() else None


def close_interrupted(workspace: Workspace) -> bool:
    """Turn a job left ``running`` by a dead process into an error. True when something changed."""
    state = load_job(workspace)
    if state is None or state.done or any(s.status == "error" for s in state.stages):
        return False
    stage = next((s for s in state.stages if s.status == "running"), None) or next(
        (s for s in state.stages if s.status == "pending"), None
    )
    if stage is None:
        return False
    workspace.write_model(workspace.job_path, _with_stage(state, stage.id, "error", INTERRUPTED))
    return True
