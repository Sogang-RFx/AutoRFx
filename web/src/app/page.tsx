"use client";
/**
 * Start screen (TASKS S010, SPEC §14, US7). An RFQ is a package of files the
 * user uploads. Nothing already on the server is listed or offered here.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { IconArrowRight, IconClose, IconGear, IconUpload, IconWarn } from "@/components/ui/icons";
import { Button, EngineBadge, ErrorState, cx } from "@/components/ui/primitives";
import type { MetaResponse } from "@/lib/rfq/contract";
import { describeError, rfqApi, type UploadSource } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

const PRINCIPLES = [
  { title: "원문은 그대로", body: "원본을 고치지 않고 보관합니다. 모든 판단은 파일과 원문 위치로 돌아갑니다." },
  { title: "인용은 문장 단위", body: "결과마다 어느 파일 몇 페이지 몇 번째 문장인지 붙이고, 확인되지 않은 인용은 따로 표시합니다." },
  { title: "금액은 팀별로", body: "가격 조항은 열람 권한이 없는 팀의 화면, 원문, 내보내기에서 모두 빠집니다." },
];

type Upload = MetaResponse["data"]["upload"];

/** Problems with the picked files, checked before anything is sent. */
function checkFiles(files: File[], rules: Upload | null): string[] {
  if (!rules) return [];
  const problems: string[] = [];
  if (files.length > rules.max_files) problems.push(`파일은 ${rules.max_files}개까지 올릴 수 있습니다.`);
  for (const f of files) {
    const ext = f.name.includes(".") ? `.${f.name.split(".").pop()?.toLowerCase()}` : "";
    if (!rules.accept.includes(ext)) problems.push(`'${f.name}': 받지 않는 형식입니다 (${rules.accept.join(", ")}).`);
    if (f.size > rules.max_file_mb * 1024 * 1024) problems.push(`'${f.name}': 파일당 ${rules.max_file_mb}MB를 넘습니다.`);
  }
  return problems;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function StartPage() {
  const router = useRouter();
  const session = useSession();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const meta = useResource(session.ready ? "meta" : null, (s) => rfqApi.meta(s));
  const rules = meta.status === "ok" ? meta.data.data.upload : null;
  const accept = rules?.accept ?? [".pdf", ".doc", ".docx", ".xlsx"];
  const problems = checkFiles(files, rules);

  function addFiles(list: FileList | null) {
    if (!list) return;
    const incoming = Array.from(list);
    // Same name twice would collide on the server; the newer pick replaces the older.
    setFiles((prev) => [...prev.filter((p) => !incoming.some((f) => f.name === p.name)), ...incoming]);
  }

  async function start(source: UploadSource) {
    setIsBusy(true);
    setError(null);
    try {
      const res = await rfqApi.upload(source);
      const q = new URLSearchParams(window.location.search);
      router.push(`/rfq/${encodeURIComponent(res.data.id)}/processing?${q.toString()}`);
    } catch (e) {
      setError(describeError(e));
      setIsBusy(false);
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    addFiles(e.dataTransfer.files);
  }

  return (
    <main className="mx-auto grid min-h-dvh max-w-6xl content-center gap-12 px-4 py-12 md:px-8 lg:grid-cols-[1.1fr_1fr] lg:gap-16">
      <section aria-labelledby="hero-title" className="flex flex-col justify-center gap-6">
        <p className="flex flex-wrap items-center gap-2 text-label font-semibold text-ink-muted">
          AutoRFx · 자동차 부품 RFQ 분석
          {meta.status === "ok" && <EngineBadge engines={meta.data.data.engines} />}
          <Link
            href="/settings/departments"
            data-testid="org-settings-link"
            className="rounded-control border border-line-control px-2 py-0.5 font-medium text-ink transition-soft hover:bg-surface"
          >
            부서 설정
          </Link>
          <Link
            href="/settings/company"
            data-testid="company-settings-link"
            aria-label="사전원가 기본 설정"
            title="사전원가 기본 설정 (단가표 · 회사 프로필 · 비율)"
            className="inline-flex items-center gap-1 rounded-control border border-line-control px-2 py-0.5 font-medium text-ink transition-soft hover:bg-surface"
          >
            <IconGear size={14} />
            기본 설정
          </Link>
        </p>
        <h1
          id="hero-title"
          className="font-serif text-ink"
          style={{ fontSize: "calc(clamp(32px, 6vw, 46px) + var(--bump))", lineHeight: 1.12 }}
        >
          여러 파일로 온 RFQ를,
          <br />
          <span className="italic">부서별 판단과 원문 근거</span>로.
        </h1>
        <p className="max-w-xl text-body text-ink-muted">
          요청서, 사양서, 물량표를 한 패키지로 읽어 조항을 주관·참조 부서로 나누고, 요건 강도와 독소조항을 표시하고, 파일
          사이에 어긋나는 값을 찾습니다. 모든 결과는 원문 문장에 연결되고, 금액 조항은 열람 권한이 있는 팀에만 보입니다.
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
              RFQ 패키지 올리기
            </h2>
            <div
              role="button"
              tabIndex={0}
              aria-describedby="upload-rules"
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
              <span className="text-body font-medium text-ink">파일을 끌어 놓거나 눌러서 고르기 (여러 개 가능)</span>
              <span id="upload-rules" className="text-label text-ink-muted">
                {accept.join(" · ")}
                {rules && ` · 최대 ${rules.max_files}개, 파일당 ${rules.max_file_mb}MB`}
              </span>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept={accept.join(",")}
                className="hidden"
                data-testid="file-input"
                onChange={(e) => {
                  addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>
            {files.length > 0 && (
              <ul className="flex flex-col divide-y divide-line rounded-control border border-line" data-testid="picked-files">
                {files.map((f) => (
                  <li key={f.name} className="flex items-center gap-2 px-3 py-2 text-dense">
                    <span className="min-w-0 flex-1 truncate text-ink" data-testid="file-name" title={f.name}>
                      {f.name}
                    </span>
                    <span className="text-label text-ink-muted tabular">{formatSize(f.size)}</span>
                    <button
                      type="button"
                      onClick={() => setFiles((prev) => prev.filter((p) => p !== f))}
                      aria-label={`${f.name} 빼기`}
                      className="rounded-control p-1 text-ink-muted hover:bg-canvas hover:text-ink"
                    >
                      <IconClose size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {problems.length > 0 && (
              <ul role="alert" className="flex flex-col gap-1 rounded-control border border-warn/40 bg-warn-tint px-3 py-2 text-label text-warn">
                {problems.map((p) => (
                  <li key={p} className="flex items-start gap-1.5">
                    <IconWarn size={12} className="mt-0.5 shrink-0" /> {p}
                  </li>
                ))}
              </ul>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                variant="primary"
                disabled={isBusy || files.length === 0 || problems.length > 0}
                onClick={() => start({ kind: "files", files })}
                data-testid="start-upload"
              >
                분석 시작{files.length > 1 ? ` (파일 ${files.length}개)` : ""} <IconArrowRight size={14} />
              </Button>
            </div>
            {meta.status === "error" && <ErrorState message={`서버 정보를 불러오지 못했습니다. ${meta.error}`} />}
            {error && <ErrorState message={error} />}
          </div>
        </div>
      </section>
    </main>
  );
}
