"""Command line: run the API, list the sample packages, or analyse a package without the server."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from autorfx.analysis import make_provider
from autorfx.config import get_settings
from autorfx.ingest import list_samples
from autorfx.pipeline import PipelineError, load_rfq, run_pipeline
from autorfx.rules import RulesError, load_ruleset


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("autorfx.api:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
    return 0


def _samples(_: argparse.Namespace) -> int:
    for sample in list_samples(get_settings()):
        sys.stdout.write(f"{sample.id}\t파일 {len(sample.files)}개\t{sample.label}\n")
    return 0


def _analyze(args: argparse.Namespace) -> int:
    settings = get_settings()
    ruleset = load_ruleset(settings.rules_dir)
    if args.sample:
        sample = next((s for s in list_samples(settings) if s.id == args.sample), None)
        if sample is None:
            sys.stderr.write(f"샘플 '{args.sample}'이(가) 없습니다. `autorfx samples`로 목록을 확인하세요.\n")
            return 2
        sources, name, is_sample = list(sample.files), sample.label, True
    elif args.files:
        sources, name, is_sample = [Path(f) for f in args.files], Path(args.files[0]).stem, False
    else:
        sys.stderr.write("파일 경로를 주거나 --sample을 지정하세요.\n")
        return 2
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

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.run(args)
    except (PipelineError, RulesError) as exc:
        sys.stderr.write(f"오류: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
