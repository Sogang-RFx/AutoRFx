"use client";
/**
 * Processing timeline (TASKS S010, SPEC §14, UI-01). Polls the job until it is
 * done or a stage fails. Each stage shows where its data comes from.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { IconArrowRight, IconCheck, IconSpinner, IconWarn } from "@/components/ui/icons";
import { ErrorState, MockChip, Panel, Skeleton, cx } from "@/components/ui/primitives";
import type { JobResponse, JobStage } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";

const POLL_MS = 350;
const MAX_RETRIES = 3;

function sinceFor(id: string): number {
  const key = `autorfx.since.${id}`;
  const fromUrl = Number(new URLSearchParams(window.location.search).get("since"));
  let since = Number.isFinite(fromUrl) && fromUrl > 0 ? fromUrl : NaN;
  try {
    if (Number.isNaN(since)) since = Number(window.sessionStorage.getItem(key)) || Date.now();
    window.sessionStorage.setItem(key, String(since));
  } catch {
    if (Number.isNaN(since)) since = Date.now();
  }
  return since;
}

function StageIcon({ status }: { status: JobStage["status"] }) {
  const common = "flex size-7 shrink-0 items-center justify-center rounded-full border";
  if (status === "done")
    return (
      <span className={cx(common, "border-ok bg-ok-tint text-ok")}>
        <IconCheck size={14} />
      </span>
    );
  if (status === "running")
    return (
      <span className={cx(common, "border-accent bg-accent-tint text-accent")}>
        <IconSpinner size={14} className="spin" />
      </span>
    );
  if (status === "error")
    return (
      <span className={cx(common, "border-danger bg-danger-tint text-danger")}>
        <IconWarn size={14} />
      </span>
    );
  return <span className={cx(common, "border-line bg-surface text-ink-muted")} />;
}

const STATUS_LABEL: Record<JobStage["status"], string> = {
  pending: "대기",
  running: "진행 중",
  done: "완료",
  error: "멈춤",
};

export default function ProcessingPage() {
  const { id } = useParams<{ id: string }>();
  const rfqId = decodeURIComponent(id);
  const session = useSession();
  const [job, setJob] = useState<JobResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session.ready) return;
    const since = sinceFor(rfqId);
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const tick = async () => {
      try {
        const res = await rfqApi.job(rfqId, since, session, controller.signal);
        failures = 0;
        setError(null);
        setJob(res);
        const halted = res.data.stages.some((s) => s.status === "error");
        if (!res.data.done && !halted) timer = setTimeout(tick, POLL_MS);
      } catch (e) {
        if (controller.signal.aborted) return;
        failures += 1;
        // A transient failure retries with backoff; after MAX_RETRIES it is shown.
        if (failures <= MAX_RETRIES) timer = setTimeout(tick, POLL_MS * 2 ** failures);
        else setError(describeError(e));
      }
    };
    void tick();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
    // Re-run only when the demo inputs change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfqId, session.ready, session.scenario, session.speed]);

  const stages = job?.data.stages ?? [];
  const doneCount = stages.filter((s) => s.status === "done").length;
  const halted = stages.find((s) => s.status === "error");
  const progress = stages.length ? Math.round((doneCount / stages.length) * 100) : 0;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8 md:px-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-title font-semibold text-ink">처리 진행</h1>
        <p className="text-body text-ink-muted">
          원본을 보관하고 기준 PDF를 만든 뒤, 조항을 나누고 분석해서 모든 결과의 인용을 원문과 대조합니다.
        </p>
      </div>

      <Panel
        title="파이프라인"
        provenance={job?.provenance}
        labelId="pipeline-title"
        actions={
          <span className="text-label text-ink-muted tabular" aria-live="polite">
            {stages.length ? `${doneCount}/${stages.length} 단계 · ${progress}%` : ""}
          </span>
        }
      >
        <div aria-hidden="true" className="h-0.5 overflow-hidden bg-canvas">
          <div
            className="h-full origin-left bg-accent transition-soft"
            style={{ transform: `scaleX(${progress / 100})`, transitionDuration: "var(--dur-panel)" }}
          />
        </div>
        {error && <ErrorState message={error} />}
        {!job && !error && <Skeleton lines={7} />}
        {job && (
          <ol className="flex flex-col px-4 py-4" data-testid="stages">
            {stages.map((stage, i) => (
              <li key={stage.id} className="relative flex gap-4 pb-5 last:pb-0" data-status={stage.status}>
                {i < stages.length - 1 && (
                  <span
                    aria-hidden="true"
                    className={cx(
                      "absolute left-3.5 top-7 bottom-0 w-px",
                      stage.status === "done" ? "bg-ok" : "bg-line",
                    )}
                  />
                )}
                <StageIcon status={stage.status} />
                <div className="flex min-w-0 flex-1 flex-col gap-0.5 pt-0.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={cx("text-body font-semibold", stage.status === "pending" ? "text-ink-muted" : "text-ink")}>
                      {stage.label}
                    </span>
                    <span
                      className={cx("text-label", stage.status === "error" ? "font-semibold text-danger" : "text-ink-muted")}
                    >
                      {STATUS_LABEL[stage.status]}
                    </span>
                    {job.provenance[stage.part] === "mock" && <MockChip title={`이 단계(${stage.part})는 목업입니다.`} />}
                  </div>
                  <p className={cx("text-dense", stage.status === "error" ? "text-danger" : "text-ink-muted")}>
                    {stage.detail}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </Panel>

      {job?.data.done && (
        <div className="flex flex-wrap items-center gap-3 rounded-panel border border-ok/40 bg-ok-tint px-4 py-3">
          <IconCheck size={18} className="text-ok" />
          <p className="flex-1 text-body text-ink">처리가 끝났습니다. 부서별 조항과 원문 근거를 확인하세요.</p>
          <Link
            href={`/rfq/${encodeURIComponent(rfqId)}/clauses`}
            data-testid="go-clauses"
            className="inline-flex min-h-9 items-center gap-2 rounded-control bg-accent px-3 text-dense font-medium text-white transition-soft hover:bg-ink"
          >
            조항 작업 화면 <IconArrowRight size={14} />
          </Link>
        </div>
      )}
      {halted && <ErrorState message={`'${halted.label}' 단계에서 처리가 멈췄습니다. ${halted.detail}`} />}
    </div>
  );
}
