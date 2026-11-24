"""The pipeline: one RFQ package in, every stored result out (SPEC §1).

    store → canonical → parse → translate → route → assess → verify → consistency

Each stage reports to the job. A failure stops the job at its stage with a
message for the user; nothing downstream runs on partial results. What was
computed, and how (engines, ruleset), is written next to the results, so a
response can always say where its content came from (INV-10, INV-12).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from autorfx import access
from autorfx.analysis import AnalysisProvider, CacheStore, analyze, engine_ids
from autorfx.chunker import apply_cost_entities, build_clauses
from autorfx.config import Settings
from autorfx.consistency import ConsistencyReport, check_package, extract_mentions
from autorfx.ingest import IngestError, ingest_package
from autorfx.jobs import JobReporter, NullReporter
from autorfx.models import Clause, ClauseAnalysis, EntityMention, PackageManifest, ParsedFile
from autorfx.parser import ParseError, parse_file
from autorfx.precost import extract_cost_inputs
from autorfx.retrieval import SearchIndex
from autorfx.retrieval.dense import DenseIndex, Encoder
from autorfx.rules import Ruleset
from autorfx.workspace import Workspace

log = logging.getLogger(__name__)

RETRIEVAL_ENGINE = "bm25-glossary"


def retrieval_engine(encoder: Encoder | None) -> str:
    """How questions are retrieved: BM25 alone, or BM25 fused with an embedding model."""
    return RETRIEVAL_ENGINE if encoder is None else f"{RETRIEVAL_ENGINE}+dense:{encoder.name}"


PIPELINE_VERSION = "2026-11-25.2"
"""Raise when a change alters stored results (parsing, chunking, masking): older results are then
re-computed, like after a rules change. .2: page-level masking, index runs, body-less leaf clauses.
.3: page-level masking spares the lines of visible sentences.
.4: positions in no clause count as cost-sensitive in mentions and issues.
2026-11-25.2: spreadsheet header cells keep their sheet columns (empty columns no longer shift them)."""
MASK_FAILED = "가림 처리에 실패했습니다. 금액 열람 권한이 없는 팀에는 이 파일의 원문을 제공하지 않습니다."
CATEGORY_LABEL = {
    "conflict": "값 충돌",
    "missing_attachment": "첨부 누락",
    "broken_reference": "참조 오류",
    "notation": "표기 불일치",
}


class PipelineError(RuntimeError):
    """The job stopped. The message is the one shown to the user."""


@dataclass(frozen=True)
class RfqData:
    """Everything stored for one analysed RFQ, plus its search index."""

    workspace: Workspace
    manifest: PackageManifest
    parsed: Mapping[str, ParsedFile]
    clauses: tuple[Clause, ...]
    analyses: tuple[ClauseAnalysis, ...]
    mentions: tuple[EntityMention, ...]
    consistency: ConsistencyReport
    cost_inputs: dict
    engines: dict
    index: SearchIndex


def _parse(
    workspace: Workspace, manifest: PackageManifest, ruleset: Ruleset, report: JobReporter
) -> tuple[PackageManifest, list[ParsedFile], tuple[Clause, ...], tuple[EntityMention, ...]]:
    total = len(manifest.files)
    report.update("parse", "running", f"0/{total} 파일")
    parsed: list[ParsedFile] = []
    per_file: list[tuple[Clause, ...]] = []
    for number, file in enumerate(manifest.files, start=1):
        try:
            result = parse_file(workspace, file)
        except ParseError as exc:
            message = f"'{file.name}': {exc}"
            report.fail("parse", message)
            raise PipelineError(message) from exc
        workspace.save_parsed(result)
        parsed.append(result)
        per_file.append(build_clauses(result, file, ruleset))
        report.update("parse", "running", f"{number}/{total} 파일")
    clauses = tuple(clause for group in per_file for clause in group)
    # A clause that states a cost entity is cost-sensitive too; mentions carry that flag.
    clauses = apply_cost_entities(clauses, extract_mentions(parsed, clauses, ruleset), ruleset)
    mentions = extract_mentions(parsed, clauses, ruleset)
    files = tuple(
        file.model_copy(
            update={
                "lang": result.lang,
                "clause_count": len(group),
                "warnings": (*file.warnings, *result.warnings),
            }
        )
        for file, result, group in zip(manifest.files, parsed, per_file, strict=True)
    )
    manifest = manifest.model_copy(update={"files": files})
    workspace.save_manifest(manifest)
    workspace.save_clauses(clauses)
    workspace.save_mentions(mentions)
    sentences = sum(len(result.sentences) for result in parsed)
    report.update("parse", "done", f"조항 {len(clauses)}개, 문장 {sentences}개")
    return manifest, parsed, clauses, mentions


def _mask(
    workspace: Workspace,
    manifest: PackageManifest,
    clauses: Sequence[Clause],
    parsed: Sequence[ParsedFile],
    ruleset: Ruleset,
) -> PackageManifest:
    """Write the masked PDFs. A file that cannot be masked is withheld from restricted teams."""
    files = []
    for file, result in zip(manifest.files, parsed, strict=True):
        try:
            access.build_masked_pdf(workspace, file, clauses, result, ruleset)
        except access.AccessDenied:
            log.warning("masking failed for %s/%s; the source is withheld", manifest.id, file.id)
            file = file.model_copy(update={"warnings": (*file.warnings, MASK_FAILED)})
        files.append(file)
    return manifest.model_copy(update={"files": tuple(files)})


def run_pipeline(
    sources: Sequence[Path],
    *,
    name: str,
    settings: Settings,
    ruleset: Ruleset,
    provider: AnalysisProvider,
    is_sample: bool,
    now_ms: int,
    reporter: JobReporter | None = None,
    cache: CacheStore | None = None,
    retrieval: str = RETRIEVAL_ENGINE,
) -> Workspace:
    """Process a package end to end and store the results in its workspace."""
    report = reporter or NullReporter()
    try:
        workspace, manifest = ingest_package(
            sources, name=name, settings=settings, is_sample=is_sample, now_ms=now_ms, reporter=report
        )
    except IngestError as exc:
        raise PipelineError(str(exc)) from exc
    manifest, parsed, clauses, mentions = _parse(workspace, manifest, ruleset, report)

    analyses = analyze(
        clauses,
        ruleset,
        provider,
        cache=cache,
        reporter=report,
        max_concurrency=settings.llm_concurrency,
        policy=settings.confirm_policy,
    )
    workspace.save_analysis(analyses)

    report.update("consistency", "running", f"파일 {len(manifest.files)}개 대조")
    consistency = check_package(manifest, parsed, clauses, mentions, ruleset)
    workspace.write_model(workspace.consistency_path, consistency)
    cost_inputs = extract_cost_inputs(manifest, parsed, clauses, mentions, consistency.issues, ruleset)
    workspace.write_json(workspace.cost_inputs_path, cost_inputs)
    manifest = _mask(workspace, manifest, clauses, parsed, ruleset)
    workspace.save_manifest(manifest)
    workspace.write_json(
        workspace.run_path,
        {
            "engines": {**engine_ids(provider), "retrieval": retrieval},
            "ruleset_digest": ruleset.digest,
            "pipeline_version": PIPELINE_VERSION,
            "finished_at": now_ms,
        },
    )
    counts = [
        f"{label} {sum(issue.category == category for issue in consistency.issues)}"
        for category, label in CATEGORY_LABEL.items()
    ]
    report.update("consistency", "done", f"이슈 {len(consistency.issues)}건 ({', '.join(counts)})")
    report.finish()
    log.info("processed %s: %d clauses, %d issues", manifest.id, len(clauses), len(consistency.issues))
    return workspace


def load_rfq(
    workspace: Workspace,
    ruleset: Ruleset,
    *,
    encoder: Encoder | None = None,
    min_score: float | None = None,
) -> RfqData:
    """The stored results of a finished job. With an encoder, the search index is hybrid."""
    manifest = workspace.manifest()
    clauses = workspace.clauses()
    run = workspace.read_json(workspace.run_path)
    if not isinstance(run, dict) or not isinstance(run.get("engines"), dict):
        raise PipelineError("처리 기록이 손상됐습니다. RFQ를 다시 올려 주세요.")
    cost_inputs = workspace.read_json(workspace.cost_inputs_path)
    if not isinstance(cost_inputs, dict):
        raise PipelineError("원가 입력 후보가 손상됐습니다. RFQ를 다시 올려 주세요.")
    return RfqData(
        workspace=workspace,
        manifest=manifest,
        parsed={file.id: workspace.parsed(file.id) for file in manifest.files},
        clauses=clauses,
        analyses=workspace.analysis(),
        mentions=workspace.mentions(),
        consistency=workspace.read_model(workspace.consistency_path, ConsistencyReport),
        cost_inputs=cost_inputs,
        # Retrieval runs when a question is asked, with the encoder loaded now, not the one recorded at
        # analysis time; the served engines say which (rule 6: no result shown as another engine's).
        engines={**run["engines"], "retrieval": retrieval_engine(encoder)},
        index=SearchIndex(clauses, ruleset, dense=_dense(clauses, encoder, min_score)),
    )


def _dense(clauses: Sequence[Clause], encoder: Encoder | None, min_score: float | None) -> DenseIndex | None:
    if encoder is None:
        return None
    if min_score is None:
        return DenseIndex(clauses, encoder)
    return DenseIndex(clauses, encoder, min_score=min_score)
