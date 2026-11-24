"use client";
/**
 * Source viewer (TASKS S012, SPEC §14, CITE-03). Shows one file of the RFQ
 * package at a time: its canonical PDF as the server chose it for this team
 * (restricted teams get the cost-redacted PDF), with highlight boxes from the
 * sentence coordinates of the same PDF. A citation opens its own file, page and
 * sentence. A region (a price-sheet table) opens its page with the region outlined.
 * PDF.js is loaded on demand so it stays out of the first-load bundle.
 */
import { useEffect, useRef, useState } from "react";
import type { Locus, SentencesResponse } from "@/lib/rfq/contract";
import { fileTag, usePackageFiles } from "@/lib/rfq/package-context";
import { describeError, rfqApi, type RequestCtx } from "@/lib/rfq/provider";
import { useResource } from "@/lib/rfq/use-resource";
import { IconArrowRight, IconLock } from "../ui/icons";
import { Button, ErrorState, ForbiddenState, Panel, Skeleton, cx, useLocusLabel } from "../ui/primitives";

/** In PDF.js 6 the loading task owns `destroy()`; the document proxy does not. */
type PdfDoc = { numPages: number; getPage: (n: number) => Promise<PdfPage>; destroy: () => Promise<void> };
type PdfPage = {
  getViewport: (o: { scale: number }) => { width: number; height: number };
  render: (o: { canvas: HTMLCanvasElement; viewport: { width: number; height: number } }) => {
    promise: Promise<void>;
    cancel: () => void;
  };
};

class SourceForbidden extends Error {}

async function loadPdf(url: () => string, signal: AbortSignal): Promise<PdfDoc> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
  // url() may throw a ConfigError; inside this async function it becomes a rejection.
  const res = await fetch(url(), { cache: "no-store", signal });
  if (res.status === 403) {
    const body = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
    throw new SourceForbidden(body?.error?.message ?? "이 팀에는 이 파일의 원문이 제공되지 않습니다.");
  }
  if (!res.ok) throw new Error(`원문 PDF를 받지 못했습니다 (${res.status}).`);
  const data = new Uint8Array(await res.arrayBuffer());
  const task = pdfjs.getDocument({ data });
  const proxy = await task.promise;
  return {
    numPages: proxy.numPages,
    getPage: (n) => proxy.getPage(n) as unknown as Promise<PdfPage>,
    destroy: () => task.destroy(),
  };
}

type Loaded = { key: string; doc: PdfDoc | null; error: string | null; forbidden: boolean };

/** A part of a page to outline, as fractions of the page (a table found by the price-sheet reader). */
export type SourceRegion = {
  source_doc: string;
  /** The page opened first. */
  page: number;
  /** Boxes on any page of the file; each page shows its own. */
  boxes: { page: number; x: number; y: number; w: number; h: number }[];
  label: string;
};

type Props = {
  rfqId: string;
  ctx: RequestCtx;
  restricted: boolean;
  target: Locus | null;
  region?: SourceRegion | null;
  className?: string;
};

export function SourceViewer({ rfqId, ctx, restricted, target, region = null, className }: Props) {
  const files = usePackageFiles();
  const locusLabel = useLocusLabel();
  const [chosen, setChosen] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [width, setWidth] = useState(0);
  const holderRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const markRef = useRef<HTMLDivElement>(null);

  // Follow the citation target to its file and page (state adjusted during render, not in an effect).
  const [followed, setFollowed] = useState<Locus | null>(null);
  if (target !== followed) {
    setFollowed(target);
    if (target) {
      setChosen(target.source_doc);
      setPage(target.page_number);
    }
  }
  const [followedRegion, setFollowedRegion] = useState<SourceRegion | null>(null);
  if (region !== followedRegion) {
    setFollowedRegion(region);
    if (region) {
      setChosen(region.source_doc);
      setPage(region.page);
    }
  }
  const fileId = chosen ?? files[0]?.id ?? null;
  const current = files.find((f) => f.id === fileId) ?? null;

  // One PDF per file and team: the server decides which file a team receives.
  const sourceKey = fileId ? `${rfqId}:${fileId}:${ctx.team ?? "none"}` : null;
  useEffect(() => {
    if (!sourceKey || !fileId) return;
    const controller = new AbortController();
    let doc: PdfDoc | null = null;
    loadPdf(() => rfqApi.sourceUrl(rfqId, fileId, ctx), controller.signal).then(
      (d) => {
        doc = d;
        if (controller.signal.aborted) void d.destroy();
        else setLoaded({ key: sourceKey, doc: d, error: null, forbidden: false });
      },
      (e: unknown) => {
        if (controller.signal.aborted) return;
        const forbidden = e instanceof SourceForbidden;
        setLoaded({ key: sourceKey, doc: null, error: describeError(e), forbidden });
      },
    );
    return () => {
      controller.abort();
      if (doc) void doc.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey]);
  const state = loaded?.key === sourceKey ? loaded : null;
  const doc = state?.doc ?? null;

  // Track available width.
  useEffect(() => {
    const el = holderRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Render the page.
  useEffect(() => {
    if (!doc || !canvasRef.current || width < 50) return;
    let cancelled = false;
    let task: { cancel: () => void } | null = null;
    void doc.getPage(Math.min(page, doc.numPages)).then((p) => {
      if (cancelled || !canvasRef.current) return;
      const base = p.getViewport({ scale: 1 });
      const cssScale = Math.min(width, 900) / base.width;
      const ratio = window.devicePixelRatio || 1;
      const viewport = p.getViewport({ scale: cssScale * ratio });
      const canvas = canvasRef.current;
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      setSize({ w: base.width * cssScale, h: base.height * cssScale });
      const t = p.render({ canvas, viewport });
      task = t;
      t.promise.catch(() => undefined);
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, page, width]);

  const sentences = useResource<SentencesResponse>(
    fileId ? `sent:${rfqId}:${fileId}:${ctx.team ?? "none"}:${page}` : null,
    (s) => rfqApi.sentences(rfqId, fileId ?? "", page, ctx, s),
  );
  const onTargetPage = Boolean(target && target.source_doc === fileId && target.page_number === page);
  const marked =
    target && onTargetPage && sentences.status === "ok"
      ? (sentences.data.data.sentences.find((s) => s.locus.sentence_index === target.sentence_index) ?? null)
      : null;
  const targetMissing = onTargetPage && sentences.status === "ok" && !marked;
  const outlined = region && region.source_doc === fileId ? region.boxes.filter((b) => b.page === page) : [];

  const markKey = marked ? `s:${marked.locus.sentence_index}` : outlined.length && size ? `r:${page}:${outlined[0].y}` : null;
  useEffect(() => {
    // Scroll only the viewer, never the page around it.
    const holder = holderRef.current;
    const mark = markRef.current;
    if (!markKey || !holder || !mark) return;
    const delta = mark.getBoundingClientRect().top - holder.getBoundingClientRect().top - holder.clientHeight / 3;
    holder.scrollBy({ top: delta, behavior: "smooth" });
  }, [markKey]);

  const pageCount = doc?.numPages ?? current?.page_count ?? 0;

  function switchFile(id: string) {
    setChosen(id);
    setPage(1);
  }

  return (
    <Panel
      title="원문"
      labelId="source-title"
      provenance={sentences.status === "ok" ? sentences.data.provenance : undefined}
      className={className}
      bodyClassName="flex flex-col"
      actions={
        <div className="flex flex-wrap items-center gap-1">
          {files.length > 1 && (
            <label className="flex items-center gap-1 text-label text-ink-muted">
              <span className="sr-only">파일</span>
              <select
                data-testid="file-select"
                value={fileId ?? ""}
                onChange={(e) => switchFile(e.target.value)}
                className="min-h-8 max-w-48 truncate rounded-control border border-line-control bg-surface px-1.5 text-label text-ink"
              >
                {files.map((f) => (
                  <option key={f.id} value={f.id}>
                    {fileTag(files, f.id)} · {f.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <Button variant="ghost" aria-label="이전 페이지" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            <IconArrowRight size={14} className="rotate-180" />
          </Button>
          <span className="text-label text-ink-muted tabular" aria-live="polite" data-testid="page-indicator">
            {page} / {pageCount || "–"}
          </span>
          <Button
            variant="ghost"
            aria-label="다음 페이지"
            disabled={!pageCount || page >= pageCount}
            onClick={() => setPage((p) => p + 1)}
          >
            <IconArrowRight size={14} />
          </Button>
        </div>
      }
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-surface-source px-4 py-2 text-label">
        {current && (
          <span className="min-w-0 truncate text-ink-muted" data-testid="source-file" title={current.name}>
            {files.length > 1 && <strong className="font-semibold text-ink">{fileTag(files, current.id)} </strong>}
            {current.name}
          </span>
        )}
        {target ? (
          <span className="font-semibold text-ink tabular" data-testid="source-locator">
            {locusLabel(target)}
          </span>
        ) : region ? (
          <span className="font-semibold text-ink" data-testid="source-locator">
            {region.label}
          </span>
        ) : (
          <span className="text-ink-muted">인용을 누르면 해당 파일의 문장으로 이동합니다.</span>
        )}
        {restricted && (
          <span className="ml-auto inline-flex items-center gap-1 text-ink-muted" data-testid="masked-pdf-note">
            <IconLock size={12} /> 금액 조항이 가려진 원문
          </span>
        )}
      </div>
      {current && current.warnings.length > 0 && (
        <ul className="border-b border-line px-4 py-1.5 text-label text-ink-muted">
          {current.warnings.map((w) => (
            <li key={w}>참고: {w}</li>
          ))}
        </ul>
      )}
      {targetMissing && (
        <p role="status" className="border-b border-warn/40 bg-warn-tint px-4 py-2 text-label text-warn">
          이 위치의 문장을 원문에서 찾지 못했습니다. 인용이 검증되지 않았습니다.
        </p>
      )}
      <div
        ref={holderRef}
        tabIndex={0}
        role="region"
        aria-label={`원문 ${current ? `${current.name} ` : ""}${page}페이지 (방향키로 스크롤)`}
        className="min-h-0 flex-1 overflow-auto bg-canvas p-3 md:p-4"
      >
        {state?.forbidden && state.error && <ForbiddenState message={state.error} />}
        {state?.error && !state.forbidden && <ErrorState message={state.error} />}
        {!doc && !state?.error && <Skeleton lines={10} className="mx-auto max-w-xl rounded-panel bg-surface-source" />}
        <div
          className={cx("relative mx-auto bg-white shadow-float", !doc && "hidden")}
          style={size ? { width: size.w, height: size.h } : undefined}
        >
          <canvas
            ref={canvasRef}
            className="block"
            role="img"
            aria-label={`원문 ${page}페이지`}
            style={size ? { width: size.w, height: size.h } : undefined}
          />
          {marked?.rects.map((r, i) => (
            <div
              key={i}
              ref={i === 0 ? markRef : undefined}
              data-testid="source-mark"
              aria-hidden="true"
              className="pointer-events-none absolute rounded-chip bg-mark"
              style={{
                left: `${r.x * 100}%`,
                top: `${r.y * 100}%`,
                width: `${r.w * 100}%`,
                height: `${r.h * 100}%`,
                mixBlendMode: "multiply",
                boxShadow: "0 0 0 1px rgb(122 74 0 / 0.35)",
              }}
            />
          ))}
          {!marked &&
            outlined.map((r, i) => (
              <div
                key={`region-${i}`}
                ref={i === 0 ? markRef : undefined}
                data-testid="source-region"
                aria-hidden="true"
                className="pointer-events-none absolute rounded-chip border-2 border-accent bg-accent/10"
                style={{ left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.w * 100}%`, height: `${r.h * 100}%` }}
              />
            ))}
        </div>
        {marked && <p className="sr-only">강조된 문장: {marked.text}</p>}
      </div>
    </Panel>
  );
}
