"use client";
/**
 * Start screen (TASKS S010, SPEC §14, US7). In the demo shell the chosen file
 * is never transmitted: only its name is sent, and the result is the sample.
 */
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { IconArrowRight, IconLock, IconUpload } from "@/components/ui/icons";
import { Button, ErrorState, MockChip, cx } from "@/components/ui/primitives";
import { modeOf } from "@/lib/rfq/config";
import { describeError, rfqApi } from "@/lib/rfq/provider";

const PRINCIPLES = [
  { title: "원문은 그대로", body: "원본을 고치지 않고 보관합니다. 모든 판단은 원문 위치로 돌아갑니다." },
  { title: "인용은 문장 단위", body: "요약마다 몇 페이지 몇 번째 문장인지 붙이고, 확인되지 않은 인용은 따로 표시합니다." },
  { title: "금액은 팀별로", body: "가격 조항은 열람 권한이 없는 팀의 화면, 원문, 내보내기에서 모두 빠집니다." },
];

/** Upload copy depends on the document part's mode; a config error counts as "not mock". */
function isDocumentMock(): boolean {
  try {
    return modeOf("document") === "mock";
  } catch {
    return false;
  }
}

export default function StartPage() {
  const isMock = isDocumentMock();
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start(name: string | null) {
    setIsBusy(true);
    setError(null);
    try {
      const res = await rfqApi.upload(name);
      const q = new URLSearchParams(window.location.search);
      q.set("since", String(res.data.started_at));
      router.push(`/rfq/${encodeURIComponent(res.data.id)}/processing?${q.toString()}`);
    } catch (e) {
      setError(describeError(e));
      setIsBusy(false);
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) setFileName(file.name);
  }

  return (
    <main className="mx-auto grid min-h-dvh max-w-6xl content-center gap-12 px-4 py-12 md:px-8 lg:grid-cols-[1.1fr_1fr] lg:gap-16">
      <section aria-labelledby="hero-title" className="flex flex-col justify-center gap-6">
        <p className="flex items-center gap-2 text-label font-semibold text-ink-muted">
          AutoRFx · 자동차 부품 RFQ 분석 {isMock && <MockChip title="이 데모 셸은 목업 데이터로 동작합니다." />}
        </p>
        <h1
          id="hero-title"
          className="font-serif text-ink"
          style={{ fontSize: "calc(clamp(32px, 6vw, 46px) + var(--bump))", lineHeight: 1.12 }}
        >
          수십 쪽짜리 RFQ를,
          <br />
          <span className="italic">부서별 판단과 원문 근거</span>로.
        </h1>
        <p className="max-w-xl text-body text-ink-muted">
          조항을 담당 부서로 나누고, 요건 강도와 독소조항을 표시하고, 모든 결과를 원문 문장에 연결합니다. 금액 조항은 열람
          권한이 있는 팀에만 보입니다.
        </p>
        <ol className="grid gap-4 border-t border-line pt-6 md:grid-cols-3">
          {PRINCIPLES.map((p, i) => (
            <li key={p.title} className="flex flex-col gap-1">
              <span className="font-serif text-heading text-ink-muted tabular">0{i + 1}</span>
              <span className="text-dense font-semibold text-ink">{p.title}</span>
              <span className="text-dense text-ink-muted">{p.body}</span>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="upload-title" className="flex flex-col justify-center gap-4">
        <div className="relative">
          {/* paper stack: the source document sits behind the interpretation card */}
          <div aria-hidden="true" className="absolute inset-x-6 -top-3 h-full rounded-panel border border-line bg-surface-source" />
          <div aria-hidden="true" className="absolute inset-x-3 -top-1.5 h-full rounded-panel border border-line bg-surface-source" />
          <div className="relative flex flex-col gap-5 rounded-panel border border-line bg-surface p-6 shadow-float">
            <h2 id="upload-title" className="text-heading font-semibold text-ink">
              RFQ 올리기
            </h2>
            <div
              role="button"
              tabIndex={0}
              aria-describedby={isMock ? "upload-note" : undefined}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  inputRef.current?.click();
                }
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={onDrop}
              className={cx(
                "flex cursor-pointer flex-col items-center gap-2 rounded-control border border-dashed px-4 py-8 text-center transition-soft",
                isDragging ? "border-accent bg-accent-tint" : "border-line-control bg-surface-source hover:bg-canvas",
              )}
            >
              <IconUpload size={22} className="text-ink-muted" />
              <span className="text-body font-medium text-ink" data-testid="file-name">
                {fileName ? fileName : "파일을 끌어 놓거나 눌러서 고르기"}
              </span>
              <span className="text-label text-ink-muted">.doc · .docx · .pdf</span>
              <input
                ref={inputRef}
                type="file"
                accept=".doc,.docx,.pdf"
                className="hidden"
                data-testid="file-input"
                onChange={(e) => setFileName(e.target.files?.[0]?.name ?? null)}
              />
            </div>
            {isMock && (
              <p
                id="upload-note"
                className="flex items-start gap-2 rounded-control bg-mock-tint px-3 py-2 text-label text-mock"
              >
                <IconLock size={14} className="mt-0.5 shrink-0" />
                목업 모드에서는 파일을 전송하거나 저장하지 않습니다. 파일 이름만 표시하고, 결과는 합성 샘플 RFQ의 것입니다.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" disabled={isBusy || !fileName} onClick={() => start(fileName)} data-testid="start-upload">
                분석 시작 <IconArrowRight size={14} />
              </Button>
              <Button variant="secondary" disabled={isBusy} onClick={() => start(null)} data-testid="start-sample">
                샘플 RFQ로 보기
              </Button>
            </div>
            {error && <ErrorState message={error} />}
          </div>
        </div>
      </section>
    </main>
  );
}
