"use client";
/** Upload of one RFQ package. Shown only to an account that may upload; the server checks again. */
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import type { DragEvent } from "react";
import type { MetaResponse } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { useMeta } from "@/lib/rfq/use-meta";
import { IconArrowRight, IconClose, IconUpload, IconWarn } from "../ui/icons";
import { Button, ErrorState, cx } from "../ui/primitives";

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

export function UploadCard() {
  const router = useRouter();
  const meta = useMeta();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rules = meta.data?.upload ?? null;
  const accept = rules?.accept ?? [".pdf", ".doc", ".docx", ".xlsx"];
  const problems = checkFiles(files, rules);

  function addFiles(list: FileList | null) {
    if (!list) return;
    const incoming = Array.from(list);
    // Same name twice would collide on the server; the newer pick replaces the older.
    setFiles((prev) => [...prev.filter((p) => !incoming.some((f) => f.name === p.name)), ...incoming]);
  }

  async function start() {
    setIsBusy(true);
    setError(null);
    try {
      const res = await rfqApi.upload({ kind: "files", files });
      router.push(`/rfq/${encodeURIComponent(res.data.id)}/processing`);
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
    <div className="relative mt-3">
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
            onClick={start}
            data-testid="start-upload"
          >
            분석 시작{files.length > 1 ? ` (파일 ${files.length}개)` : ""} <IconArrowRight size={14} />
          </Button>
        </div>
        {meta.error && <ErrorState message={`서버 정보를 불러오지 못했습니다. ${meta.error}`} />}
        {error && <ErrorState message={error} />}
      </div>
    </div>
  );
}
