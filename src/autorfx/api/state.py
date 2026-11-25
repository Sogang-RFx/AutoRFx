"""Server state: settings, rules, the analysis provider, running jobs and loaded results.

Jobs run one at a time on a worker thread, so a slow conversion never blocks a
request. Results are read from the workspace, which is the only store: a
restart loses nothing but a job that was running (it is closed as failed).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

from autorfx.analysis import AnalysisProvider, SqliteCache, engine_ids, make_provider
from autorfx.analysis.usage import load_prices
from autorfx.auth import AuditLog, AuthStore
from autorfx.auth.passwords import MAX_CONCURRENT
from autorfx.chat.model import ChatModel, make_chat_model
from autorfx.chat.topics import load_topics
from autorfx.config import Settings
from autorfx.guard import GuardError
from autorfx.ingest import identify_package
from autorfx.jobs import JobStore, close_interrupted, load_job
from autorfx.models import JobState, PackageManifest
from autorfx.pipeline import (
    PIPELINE_VERSION,
    PipelineError,
    RfqData,
    load_rfq,
    retrieval_engine,
    run_pipeline,
)
from autorfx.precost import CompanyStore
from autorfx.precost.sheet_store import PriceSheetStore
from autorfx.retrieval.dense import Encoder, load_encoder
from autorfx.rules import Ruleset, load_ruleset
from autorfx.voice import SpeechProvider, make_speech, recognition_hint
from autorfx.workspace import RFQ_ID, Workspace

log = logging.getLogger(__name__)

T = TypeVar("T")

UPLOAD_DIR = "_uploads"
REUSED = "이미 분석한 RFQ입니다. 저장된 결과를 엽니다."
RUNNING = "같은 RFQ를 이미 분석하고 있습니다."
UNEXPECTED = "처리 중 예상하지 못한 오류가 발생했습니다. 다시 시도해 주세요."


class ApiError(Exception):
    """An error with an HTTP status and a message for the user."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def now_ms() -> int:
    return int(time.time() * 1000)


def discard(folder: Path | None) -> None:
    """Remove a folder of uploaded files once the package has been stored or refused."""
    if folder is not None:
        shutil.rmtree(folder, ignore_errors=True)


def _failed(state: JobState) -> bool:
    return any(stage.status == "error" for stage in state.stages)


class AppState:
    def __init__(
        self,
        settings: Settings,
        *,
        ruleset: Ruleset | None = None,
        provider: AnalysisProvider | None = None,
        encoder: Encoder | None = None,
        chat_model: ChatModel | None = None,
        speech: SpeechProvider | None = None,
    ) -> None:
        self.settings = settings
        self.ruleset = ruleset or load_ruleset(settings.rules_dir)
        self.provider = provider or make_provider(settings, self.ruleset)
        # An injected provider replaces the configured engine, so the key must not bring the LLM back.
        self.chat_model = chat_model or (make_chat_model(settings) if provider is None else None)
        # As for the chat model: an injected provider means a test or a keyless run; the key stays out.
        self.speech = speech or (make_speech(settings) if provider is None else None)
        self.speech_hint = recognition_hint(self.ruleset)
        self.chat_topics = load_topics(settings.chat_topics_path)
        # Read now, so a missing price file stops the start and not a job half way.
        self.prices = load_prices(settings.translation_prices_path) if settings.llm_connected else None
        self.cache = SqliteCache(settings.cache_dir / "analysis.sqlite")
        # Only a translating provider reads or writes it: a keyless run leaves no file behind.
        self.translation_cache = (
            SqliteCache(settings.cache_dir / "translation.sqlite") if self.provider.translates else None
        )
        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
        self.encoder = encoder or load_encoder(
            settings.embedding_model, openai_api_key=key, cache_dir=settings.cache_dir
        )
        """Loaded once: a local model such as BGE-M3 takes seconds to load."""
        # A store that cannot be read does not stop the process: it refuses every login and every route.
        self.auth = AuthStore(
            settings.work_dir,
            department_ids=self.ruleset.department_ids,
            session_hours=settings.session_hours,
        )
        self.audit = AuditLog(settings.work_dir)
        self.company = CompanyStore(settings.work_dir, self.ruleset)
        self.price_sheets = PriceSheetStore(settings.work_dir)
        self._jobs: dict[str, JobStore] = {}
        self._data: dict[str, tuple[int, RfqData]] = {}
        """Loaded results per RFQ, with the start time of the job that produced them."""
        self._lock = threading.Lock()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autorfx-job")
        self._passwords = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT, thread_name_prefix="autorfx-password"
        )
        self.stream_executor = ThreadPoolExecutor(
            max_workers=settings.chat_stream_workers, thread_name_prefix="autorfx-chat-stream"
        )
        """Streamed answers only: one that nobody reads any more still runs to its end and must not hold
        the threads other work shares."""
        settings.work_dir.mkdir(parents=True, exist_ok=True)
        discard(self.upload_root)
        for folder in sorted(settings.work_dir.iterdir()):
            if folder.is_dir() and RFQ_ID.match(folder.name):
                close_interrupted(Workspace.for_rfq(settings.work_dir, folder.name))

    @property
    def upload_root(self) -> Path:
        return self.settings.work_dir / UPLOAD_DIR

    @property
    def engines(self) -> dict:
        """How a package submitted now would be analysed."""
        return {**engine_ids(self.provider), "retrieval": retrieval_engine(self.encoder)}

    def close(self) -> None:
        self._worker.shutdown(wait=True, cancel_futures=True)
        self._passwords.shutdown(wait=True, cancel_futures=True)
        self.stream_executor.shutdown(wait=True, cancel_futures=True)
        # After the worker: a running job still reads and writes both.
        self.cache.close()
        if self.translation_cache is not None:
            self.translation_cache.close()

    def wait(self) -> None:
        """Block until every job submitted so far has ended. Jobs run in order on one worker."""
        self._worker.submit(lambda: None).result()

    # ── passwords ───────────────────────────────────────────────────────────
    async def run_password_work(self, work: Callable[..., T], *args: object) -> T:
        """Run ``work``, which hashes a password, on the few threads kept for that (32 MB each)."""
        return await asyncio.get_running_loop().run_in_executor(self._passwords, work, *args)

    # ── lookup ──────────────────────────────────────────────────────────────
    def workspace(self, rfq_id: str) -> Workspace:
        try:
            workspace = Workspace.for_rfq(self.settings.work_dir, rfq_id)
        except GuardError as exc:
            raise ApiError(404, "not_found", "RFQ를 찾을 수 없습니다.") from exc
        if not workspace.job_path.is_file():
            raise ApiError(404, "not_found", "RFQ를 찾을 수 없습니다.")
        return workspace

    def job(self, rfq_id: str) -> JobState:
        workspace = self.workspace(rfq_id)
        with self._lock:
            store = self._jobs.get(rfq_id)
        state = store.state if store else load_job(workspace)
        if state is None:
            raise ApiError(404, "not_found", "RFQ를 찾을 수 없습니다.")
        return state

    def data(self, rfq_id: str) -> RfqData:
        """Results of a finished job. A job that is running or failed has none to show."""
        state = self.job(rfq_id)
        failed = next((stage for stage in state.stages if stage.status == "error"), None)
        if failed is not None:
            raise ApiError(409, "job_failed", f"분석이 중단됐습니다 ({failed.label}): {failed.detail}")
        if not state.done:
            raise ApiError(409, "not_ready", "아직 분석 중입니다. 잠시 뒤에 다시 시도해 주세요.")
        with self._lock:
            cached = self._data.get(rfq_id)
        # A cache entry belongs to one job: a later job of the same package replaces it.
        if cached is not None and cached[0] == state.started_at:
            return cached[1]
        workspace = self.workspace(rfq_id)
        run = workspace.read_json(workspace.run_path) if workspace.run_path.is_file() else None
        if not self._fresh(run):
            # What is cost-sensitive is decided by the rules and the masking code; results computed with
            # older ones are not served (INV-8).
            message = "규칙이나 처리 방식이 바뀌어 저장된 결과가 오래됐습니다. RFQ를 다시 분석해 주세요."
            raise ApiError(409, "stale_results", message)
        try:
            loaded = load_rfq(
                workspace,
                self.ruleset,
                encoder=self.encoder,
                min_score=self.settings.min_score_for(self.encoder.name if self.encoder else None),
            )
        except (PipelineError, OSError, ValueError) as exc:
            log.exception("stored results of %s cannot be read", rfq_id)
            message = "저장된 결과를 읽을 수 없습니다. RFQ를 다시 올려 주세요."
            raise ApiError(500, "corrupt_results", message) from exc
        with self._lock:
            store = self._jobs.get(rfq_id)
            # Not cached when another job started while the results were being read.
            # Nor when the embeddings failed just now (network, rate limit): the next request tries again
            # instead of keeping keyword search for this RFQ until a restart.
            dense_failed = self.encoder is not None and loaded.index.dense is None
            if not dense_failed and (store is None or store.state.started_at == state.started_at):
                self._data[rfq_id] = (state.started_at, loaded)
        return loaded

    def rfqs(self, *, show_names: bool) -> list[dict]:
        """Every package on the server, the newest first."""
        folders = (f for f in self.settings.work_dir.iterdir() if f.is_dir() and RFQ_ID.match(f.name))
        found = [
            self._listed(workspace, show_name=show_names)
            for folder in folders
            if (workspace := Workspace.for_rfq(self.settings.work_dir, folder.name)).job_path.is_file()
        ]
        return sorted(found, key=lambda r: (-(r["created_at"] or 0), r["id"]))

    def _listed(self, workspace: Workspace, *, show_name: bool) -> dict:
        rfq_id = workspace.rfq_id
        job: JobState | None = None
        manifest: PackageManifest | None = None
        try:
            job = self.job(rfq_id)
            manifest = workspace.manifest() if workspace.exists() else None
            status = self._status(workspace, job, manifest)
        except (OSError, ValueError):
            log.exception("the stored files of %s cannot be read", rfq_id)
            status = "failed"
        created_at = manifest.created_at if manifest else job.started_at if job else None
        return {
            "id": rfq_id,
            "name": (manifest.name if manifest and show_name else "") or f"RFQ {rfq_id[-6:]}",
            "status": status,
            "created_at": created_at,
            "file_count": len(manifest.files) if manifest else 0,
        }

    def _status(self, workspace: Workspace, job: JobState, manifest: PackageManifest | None) -> str:
        if _failed(job):
            return "failed"
        if not job.done:
            return "running"
        if manifest is None:
            return "failed"
        run = workspace.read_json(workspace.run_path) if workspace.run_path.is_file() else None
        return "done" if self._fresh(run) else "stale"

    # ── submission ──────────────────────────────────────────────────────────
    def _current(self, workspace: Workspace) -> JobState | None:
        """The finished job, when its results were computed with today's rules and engine."""
        if not workspace.job_path.is_file() or not workspace.run_path.is_file():
            return None
        state = load_job(workspace)
        run = workspace.read_json(workspace.run_path)
        fresh = self._fresh(run) and isinstance(run, dict) and run.get("engines") == self.engines
        return state if state is not None and state.done and fresh else None

    def _fresh(self, run: object) -> bool:
        """Results computed with today's rules and today's pipeline."""
        return (
            isinstance(run, dict)
            and run.get("ruleset_digest") == self.ruleset.digest
            and run.get("pipeline_version") == PIPELINE_VERSION
        )

    def submit(
        self, sources: Sequence[Path], *, name: str, is_sample: bool, cleanup: Path | None = None
    ) -> tuple[str, int, str]:
        """Start analysing a package. Returns its id, the job's start time and a notice for the user."""
        rfq_id = identify_package(sources, self.settings)
        workspace = Workspace.for_rfq(self.settings.work_dir, rfq_id).ensure()
        with self._lock:
            running = self._jobs.get(rfq_id)
            if running is not None and not running.state.done and not _failed(running.state):
                discard(cleanup)
                return rfq_id, running.state.started_at, RUNNING
            finished = self._current(workspace)
            if finished is not None:
                discard(cleanup)
                return rfq_id, finished.started_at, REUSED
            started = now_ms()
            store = JobStore(workspace, started)
            store.start()
            self._jobs[rfq_id] = store
            self._data.pop(rfq_id, None)
        self._worker.submit(self._run, rfq_id, store, list(sources), name, is_sample, started, cleanup)
        return rfq_id, started, ""

    def _run(
        self,
        rfq_id: str,
        store: JobStore,
        sources: list[Path],
        name: str,
        is_sample: bool,
        started: int,
        cleanup: Path | None,
    ) -> None:
        try:
            run_pipeline(
                sources,
                name=name,
                settings=self.settings,
                ruleset=self.ruleset,
                provider=self.provider,
                is_sample=is_sample,
                now_ms=started,
                reporter=store,
                cache=self.cache,
                translation_cache=self.translation_cache,
                prices=self.prices,
                retrieval=retrieval_engine(self.encoder),
            )
        except PipelineError as exc:
            log.warning("job stopped: %s", exc)
        except Exception:  # the job must end in a state the user can see
            log.exception("job crashed")
        finally:
            state = store.state
            if not state.done and not _failed(state):
                open_stages = [s for s in state.stages if s.status in ("running", "pending")]
                store.fail((open_stages or list(state.stages))[0].id, UNEXPECTED)
            with self._lock:
                self._data.pop(rfq_id, None)
            discard(cleanup)
