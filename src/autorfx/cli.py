"""Command line: run the API, list the sample packages, analyse a package, or compare search modes."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from autorfx.analysis import make_provider
from autorfx.config import get_settings
from autorfx.ingest import list_samples
from autorfx.pipeline import PipelineError, load_rfq, retrieval_engine, run_pipeline
from autorfx.retrieval import compare, score
from autorfx.retrieval.dense import load_encoder
from autorfx.rules import RulesError, load_ruleset


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("autorfx.api:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
    return 0


def _samples(_: argparse.Namespace) -> int:
    for sample in list_samples(get_settings()):
        sys.stdout.write(f"{sample.id}\t파일 {len(sample.files)}개\t{sample.label}\n")
    return 0


DEFAULT_QUESTIONS = Path("data/questions/retrieval_ko.yaml")
DEFAULT_KEY_NAME = "retrieval_ko.yaml"


def _sources(args: argparse.Namespace, settings) -> tuple[list[Path], str, bool] | None:
    if args.sample:
        sample = next((s for s in list_samples(settings) if s.id == args.sample), None)
        if sample is None:
            sys.stderr.write(f"샘플 '{args.sample}'이(가) 없습니다. `autorfx samples`로 목록을 확인하세요.\n")
            return None
        return list(sample.files), sample.label, True
    if args.files:
        return [Path(f) for f in args.files], Path(args.files[0]).stem, False
    sys.stderr.write("파일 경로를 주거나 --sample을 지정하세요.\n")
    return None


def _analyze(args: argparse.Namespace) -> int:
    settings = get_settings()
    ruleset = load_ruleset(settings.rules_dir)
    found = _sources(args, settings)
    if found is None:
        return 2
    sources, name, is_sample = found
    workspace = run_pipeline(
        sources,
        name=name,
        settings=settings,
        ruleset=ruleset,
        provider=make_provider(settings, ruleset),
        is_sample=is_sample,
        now_ms=int(time.time() * 1000),
    )
    data = load_rfq(workspace, ruleset)
    flagged = sum(analysis.red_flag is not None for analysis in data.analyses)
    llm = "연결" if data.engines["llm_connected"] else "미연결"
    issues = len(data.consistency.issues)
    lines = [
        f"RFQ {data.manifest.id}: 파일 {len(data.manifest.files)}개, {data.manifest.page_count}쪽",
        f"분석 엔진: {data.engines['analysis']} (LLM {llm})",
        f"조항 {len(data.clauses)}개, 독소조항 후보 {flagged}개, 정합성 이슈 {issues}건",
        f"결과 위치: {workspace.root}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _compare_search(args: argparse.Namespace) -> int:
    """BM25 vs embedding vs hybrid on one RFQ, for a list of questions (report under data/work)."""
    settings = get_settings()
    ruleset = load_ruleset(settings.rules_dir)
    model = args.model or settings.embedding_model
    if not model:
        sys.stderr.write("임베딩 모델을 정해 주세요: --model BAAI/bge-m3 또는 .env의 EMBEDDING_MODEL\n")
        return 2
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    encoder = load_encoder(model, openai_api_key=key, cache_dir=settings.cache_dir)
    if encoder is None:
        sys.stderr.write(f"임베딩 모델 '{model}'을(를) 불러오지 못했습니다. 위 로그를 확인하세요.\n")
        return 1
    found = _sources(args, settings)
    if found is None:
        return 2
    sources, name, is_sample = found
    workspace = run_pipeline(
        sources,
        name=name,
        settings=settings,
        ruleset=ruleset,
        provider=make_provider(settings, ruleset),
        is_sample=is_sample,
        now_ms=int(time.time() * 1000),
        retrieval=retrieval_engine(encoder),
    )
    gate = args.min_score if args.min_score is not None else settings.embedding_min_score
    data = load_rfq(workspace, ruleset, encoder=encoder, min_score=gate)
    questions = compare.load_questions(Path(args.questions))
    rows = compare.run(data.index, questions, top=args.top)
    out = settings.work_dir / "_compare"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{data.manifest.id}-{time.strftime('%Y%m%d-%H%M')}"
    report = compare.to_markdown(rows, questions, title=name, model=encoder.name, gate=gate)
    (out / f"{stem}.md").write_text(report, encoding="utf-8")
    compare.to_csv(rows, out / f"{stem}.csv")
    sys.stdout.write(
        f"비교 결과: {out / (stem + '.md')}\n채점용 CSV: {out / (stem + '.csv')}\n"
        "RFQ 원문이 들어 있으니 깃허브에 올리지 마세요.\n"
    )
    return 0


def _score_search(args: argparse.Namespace) -> int:
    """Every development sample in the answer key, scored per mode and evidence gate (issue #14)."""
    settings = get_settings()
    ruleset = load_ruleset(settings.rules_dir)
    key_path = Path(args.key) if args.key else settings.answer_keys_dir / DEFAULT_KEY_NAME
    if not key_path.is_file():
        sys.stderr.write(f"정답 표가 없습니다: {key_path}\n")
        return 2
    answer_key = score.load_key(key_path)
    model = args.model or settings.embedding_model
    if not model:
        sys.stderr.write("임베딩 모델을 정해 주세요: --model BAAI/bge-m3 또는 .env의 EMBEDDING_MODEL\n")
        return 2
    api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    encoder = load_encoder(model, openai_api_key=api_key, cache_dir=settings.cache_dir)
    if encoder is None:
        sys.stderr.write(f"임베딩 모델 '{model}'을(를) 불러오지 못했습니다. 위 로그를 확인하세요.\n")
        return 1
    questions = compare.load_questions(Path(args.questions))
    runs = []
    for sample in list_samples(settings):
        sample_key = answer_key.for_sample(sample.label)
        if sample_key is None:
            continue
        workspace = run_pipeline(
            list(sample.files),
            name=sample.label,
            settings=settings,
            ruleset=ruleset,
            provider=make_provider(settings, ruleset),
            is_sample=True,
            now_ms=int(time.time() * 1000),
            retrieval=retrieval_engine(encoder),
        )
        data = load_rfq(workspace, ruleset, encoder=encoder)
        runs.append((sample_key.match, data.index, sample_key))
        sys.stdout.write(f"채점 대상: {sample.label}\n")
    if not runs:
        sys.stderr.write("정답 표의 samples와 맞는 개발용 RFQ가 data/raw/dev에 없습니다.\n")
        return 2
    gates = tuple(float(g) for g in args.gates.split(",")) if args.gates else score.DEFAULT_GATES
    rows = score.gate_rows(runs, questions, gates=gates, top=args.top)
    report = score.to_markdown(rows, model=encoder.name, source=answer_key.source, top=args.top)
    out = settings.work_dir / "_compare"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"score-{time.strftime('%Y%m%d-%H%M')}.md"
    path.write_text(report, encoding="utf-8")
    sys.stdout.write(report + f"\n\n저장: {path}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autorfx", description="AutoRFx: RFQ 분석 파이프라인")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="API 서버를 실행한다")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(run=_serve)
    commands.add_parser("samples", help="서버에 있는 샘플 RFQ를 나열한다").set_defaults(run=_samples)
    analyze = commands.add_parser("analyze", help="RFQ 패키지를 분석한다")
    analyze.add_argument("files", nargs="*", help="RFQ 파일 경로 (PDF, DOC, DOCX, XLSX)")
    analyze.add_argument("--sample", help="샘플 RFQ의 id")
    analyze.set_defaults(run=_analyze)
    comp = commands.add_parser("compare-search", help="키워드(BM25)·임베딩·하이브리드 검색 결과를 비교한다")
    comp.add_argument("files", nargs="*", help="RFQ 파일 경로 (PDF, DOC, DOCX, XLSX)")
    comp.add_argument("--sample", help="샘플 RFQ의 id")
    comp.add_argument("--questions", default=str(DEFAULT_QUESTIONS), help="질문 YAML")
    comp.add_argument("--model", help="임베딩 모델 (기본: EMBEDDING_MODEL)")
    comp.add_argument("--min-score", type=float, help="임베딩 근거 기준 (기본: EMBEDDING_MIN_SCORE)")
    comp.add_argument("--top", type=int, default=3, help="질문별로 보여줄 결과 수")
    comp.set_defaults(run=_compare_search)
    sc = commands.add_parser("score-search", help="정답 표로 검색 방식과 근거 기준을 자동 채점한다 (#14)")
    sc.add_argument("--key", help="정답 표 YAML (기본: data/answer_keys/retrieval_ko.yaml)")
    sc.add_argument("--questions", default=str(DEFAULT_QUESTIONS), help="질문 YAML")
    sc.add_argument("--model", help="임베딩 모델 (기본: EMBEDDING_MODEL)")
    sc.add_argument("--gates", help="비교할 근거 기준, 쉼표로 구분 (기본: 0.45,0.50,0.55,0.60,0.65)")
    sc.add_argument("--top", type=int, default=3, help="질문별로 보는 결과 수")
    sc.set_defaults(run=_score_search)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.run(args)
    except (PipelineError, RulesError) as exc:
        sys.stderr.write(f"오류: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
