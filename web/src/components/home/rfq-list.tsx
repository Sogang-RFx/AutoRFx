"use client";
/** Packages on the server (GET /rfqs), named as far as this account may see them. */
import Link from "next/link";
import { useEffect, useState } from "react";
import { formatTime } from "@/lib/rfq/admin";
import type { RfqListItem } from "@/lib/rfq/contract";
import { rfqApi } from "@/lib/rfq/provider";
import { rfqStatusView } from "@/lib/rfq/rfq-status";
import { useViewer } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";
import { IconArrowRight, IconCheck, IconPause, IconSpinner, IconWarn } from "../ui/icons";
import { Button, EmptyState, ErrorState, Panel, Skeleton, cx } from "../ui/primitives";

/** How often to ask again while an analysis runs. */
const POLL_MS = 4000;

const STATUS_ICON = {
  done: <IconCheck size={14} className="text-ok" />,
  running: <IconSpinner size={14} className="spin text-accent" />,
  failed: <IconWarn size={14} className="text-danger" />,
  stale: <IconPause size={14} className="text-ink-muted" />,
} as const;

function Row({ item }: { item: RfqListItem }) {
  const viewer = useViewer();
  const view = rfqStatusView(item, viewer.can_upload);
  const body = (
    <>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-body font-medium text-ink" title={item.name}>
          {item.name}
        </span>
        <span className="text-label text-ink-muted tabular">
          파일 {item.file_count}개{item.created_at !== null && ` · ${formatTime(item.created_at)}`}
        </span>
        {view.note && <span className="text-label text-ink-muted">{view.note}</span>}
      </span>
      <span
        className={cx(
          "inline-flex shrink-0 items-center gap-1 text-label font-medium",
          item.status === "failed" ? "text-danger" : "text-ink-muted",
        )}
      >
        {STATUS_ICON[item.status]}
        {view.label}
      </span>
      {view.href && <IconArrowRight size={14} className="shrink-0 text-ink-muted" />}
    </>
  );
  const rowClass = "flex items-center gap-3 px-4 py-3";
  return (
    <li className="border-b border-line last:border-b-0" data-testid="rfq-row" data-status={item.status}>
      {view.href ? (
        <Link href={view.href} className={cx(rowClass, "transition-soft hover:bg-canvas")}>
          {body}
        </Link>
      ) : (
        <div className={rowClass}>{body}</div>
      )}
    </li>
  );
}

export function RfqList() {
  const viewer = useViewer();
  const [version, setVersion] = useState(0);
  const list = useResource("rfqs", (s) => rfqApi.rfqs(s), version);
  const items = list.status === "ok" ? list.data.data.rfqs : [];
  const isRunning = items.some((i) => i.status === "running");

  useEffect(() => {
    if (!isRunning) return;
    const timer = setInterval(() => setVersion((v) => v + 1), POLL_MS);
    return () => clearInterval(timer);
  }, [isRunning]);

  return (
    <Panel
      title={list.status === "ok" ? `RFQ ${items.length}건` : "RFQ"}
      labelId="rfq-list-title"
      actions={
        <Button variant="ghost" onClick={() => setVersion((v) => v + 1)}>
          새로고침
        </Button>
      }
    >
      {list.status === "loading" && <Skeleton lines={4} />}
      {list.status === "error" && <ErrorState message={list.error} onRetry={() => setVersion((v) => v + 1)} />}
      {list.status === "ok" && items.length === 0 && (
        <EmptyState title="아직 올라온 RFQ가 없습니다">
          <p>
            {viewer.can_upload
              ? "RFQ 파일을 올리면 여기에 나옵니다."
              : "업로드할 수 있는 계정이 RFQ를 올리면 여기에 나옵니다."}
          </p>
        </EmptyState>
      )}
      {items.length > 0 && (
        <ul data-testid="rfq-list">
          {items.map((i) => (
            <Row key={i.id} item={i} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
