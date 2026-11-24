"use client";
/**
 * Tables found in an RFQ, for line-by-line pricing. A table the price-sheet dictionary
 * could not read is shown as printed, and the user says what each column holds. The server
 * then reads it, remembers that header for the next RFQ with the same layout, and queues
 * the header words as dictionary candidates (approved under 설정 › 가격표 사전).
 * The tables read are grouped in item lists, and the user picks the lists to price.
 */
import Link from "next/link";
import { useState } from "react";
import type { SourceRegion } from "@/components/source-viewer/source-viewer";
import { Button, cx } from "@/components/ui/primitives";
import { useTeamGroup } from "@/components/shell/app-shell";
import { ColumnRole, MAX_HEADER_ROW, type RfqLines, type SheetList, type SheetSuggestResponse, type SheetTable } from "@/lib/rfq/contract";
import { fileTag, usePackageFiles } from "@/lib/rfq/package-context";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import {
  gridWidth,
  headerRows,
  joinHeader,
  problem,
  rolesOf,
  shownColumns,
  type Roles,
  type Shown,
} from "@/lib/rfq/sheet-columns";

export const ROLE_LABEL: Record<ColumnRole, string> = {
  no: "번호",
  part: "품번",
  description: "품명·설명",
  spec: "규격·사양",
  quantity: "수량",
  unit: "단위",
  months: "개월 수",
  price: "단가",
  total: "금액",
};

const HOW_LABEL: Record<SheetTable["how"], string> = {
  rules: "사전으로 읽음",
  layout: "전에 지정한 양식",
  continued: "앞 표에 이어짐",
  none: "읽지 못함",
};

const select = "min-h-8 w-full rounded-control border border-line-control bg-surface px-1 text-label text-ink";

function ColumnAssigner({
  table,
  rfqId,
  team,
  onRead,
  onCancel,
}: {
  table: SheetTable;
  rfqId: string;
  team: string | null;
  onRead: (lines: RfqLines) => void;
  onCancel: () => void;
}) {
  const { engines } = useTeamGroup();
  // The AI suggestion brings more rows when the header sits low (a sheet that starts with notes).
  const [allRows, setAllRows] = useState(table.rows);
  const width = gridWidth(allRows);
  const [header, setHeader] = useState(table.header_row);
  const [roles, setRoles] = useState<Roles>(() => rolesOf(table.columns, width));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [suggestion, setSuggestion] = useState<SheetSuggestResponse["data"] | null>(null);
  const below = headerRows(allRows, header);
  const end = header + 1 + below.length;
  const isHeader = (r: number) => r === header || (r > header && below[r - header - 1] === "sub");
  // Keep columns the AI or a saved layout already assigned, even when their cells are empty.
  const kept = new Set(roles.flatMap((role, i) => (role ? [i] : [])));
  const shown = shownColumns(allRows, header, end, width, kept);
  // The header's wrapped lines are joined into it and not repeated; sub-heading rows stay.
  const wrapped = allRows.slice(header, end).filter((_, k) => k === 0 || below[k - 1] === "wrap");
  const rows = allRows
    .map((row, r) => (r === header ? row.map((_, i) => joinHeader(wrapped.map((x) => x[i] ?? ""))) : row))
    .map((row, r) => ({ row, r }))
    .filter(({ r }) => r <= header || r >= end || below[r - header - 1] === "sub");
  const roleOf = (col: Shown): ColumnRole | "" => col.cols.map((i) => roles[i]).find(Boolean) ?? "";
  const picked = shown.map(roleOf);
  const missing = problem(picked);

  // One role per column and one column per role: picking a role elsewhere clears it there.
  // The role goes to the column that holds the values, which is what the server reads.
  const pick = (col: Shown, role: ColumnRole | "") =>
    setRoles((prev) =>
      prev.map((r, i) => (i === col.data ? role : col.cols.includes(i) || (r === role && role !== "") ? "" : r)),
    );

  // Positions only: the server checked the suggestion by reading the table; the user still accepts it.
  const ask = async () => {
    setAsking(true);
    setError(null);
    try {
      const res = await rfqApi.suggestSheetColumns(rfqId, { table: table.id }, { team });
      const found = res.data;
      setAllRows(found.rows);
      setHeader(found.header_row);
      setRoles(rolesOf(found.columns, gridWidth(found.rows)));
      setSuggestion(found);
    } catch (e) {
      setSuggestion(null);
      setError(describeError(e));
    } finally {
      setAsking(false);
    }
  };

  const submit = async () => {
    const columns: Partial<Record<ColumnRole, number>> = {};
    shown.forEach((col, k) => {
      const r = picked[k];
      if (r) columns[r] = col.data;
    });
    setBusy(true);
    setError(null);
    try {
      const res = await rfqApi.assignSheetColumns(rfqId, { table: table.id, header_row: header, columns }, { team });
      onRead(res.data.lines);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-control border border-accent p-3" data-testid="column-assigner">
      <p className="text-label text-ink-muted">
        각 열 위에서 무엇이 적힌 열인지 고르세요. <span className="font-semibold text-ink">수량</span>과{" "}
        <span className="font-semibold text-ink">품명·설명</span>(또는 품번)은 꼭 필요합니다. 단가·금액 열은 보통 비어 있지만
        수량으로 잘못 읽지 않도록 지정해 두면 좋습니다.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-label text-ink-muted">
          머리글 줄
          <select className={cx(select, "w-auto")} value={header} data-testid="assign-header"
            onChange={(e) => setHeader(Number(e.target.value))}>
            {allRows.slice(0, MAX_HEADER_ROW + 1).map((row, i) => (
              <option key={i} value={i}>
                {i + 1}번째 줄: {row.filter(Boolean).join(" · ").slice(0, 40) || "(빈 줄)"}
              </option>
            ))}
          </select>
        </label>
        {engines?.llm_connected && (
          <Button variant="ghost" onClick={ask} disabled={asking || busy} data-testid="assign-suggest"
            title="이 표의 앞부분(최대 40줄)만 AI에 보내 머리글 줄과 열 위치를 묻습니다. 품목 이름과 숫자는 코드가 원본 칸에서 읽습니다.">
            {asking ? "AI가 표를 보는 중…" : "AI로 열 찾기"}
          </Button>
        )}
      </div>
      {suggestion && (
        <div className="rounded-control bg-accent-tint p-2 text-label text-ink" data-testid="assign-suggestion">
          <p>
            <span className="font-semibold">AI 제안</span> ({suggestion.engine}): {suggestion.reason}
          </p>
          <p className="text-ink-muted">
            이 열 지정으로 미리 읽어 보니 품목 {suggestion.lines}개가 나옵니다
            {suggestion.preview.length > 0 && ` (${suggestion.preview.join(", ")} …)`}. 아래 표에서 열을 확인하고, 맞으면
            &lsquo;이 열 지정으로 품목 읽기&rsquo;를 누르세요. 다르면 고친 뒤 누르면 됩니다.
          </p>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[32rem] text-left text-dense">
          <thead>
            <tr>
              {shown.map((col, k) => (
                <th key={col.data} className="min-w-[7.5rem] p-1 align-bottom font-normal">
                  <select className={select} aria-label={`${k + 1}열 역할`} value={picked[k]} data-testid={`assign-role-${k}`}
                    onChange={(e) => pick(col, e.target.value as ColumnRole | "")}>
                    <option value="">— 쓰지 않음</option>
                    {ColumnRole.options.map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABEL[r]}
                      </option>
                    ))}
                  </select>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ row, r }) => (
              <tr key={r} className={cx("border-t border-line", isHeader(r) && "bg-accent-tint font-semibold", r < header && "text-ink-muted")}>
                {shown.map((col) => {
                  const cell = [...col.cols, ...col.extra].sort((a, b) => a - b).map((i) => row[i] ?? "").filter(Boolean).join(" ");
                  return (
                    <td key={col.data} className="max-w-[14rem] truncate p-1 text-ink" title={cell}>
                      {cell}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-label text-ink-muted">
        이 머리글은 기억해 두었다가 같은 양식의 RFQ가 오면 그대로 읽습니다. 머리글 단어는 가격표 사전 추가 후보로 올라가고,{" "}
        <Link href="/settings/price-sheet" className="text-accent hover:underline">
          가격표 사전
        </Link>
        에서 승인해야 다른 양식에도 쓰입니다.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={submit} disabled={missing !== null || busy} data-testid="assign-submit">
          이 열 지정으로 품목 읽기
        </Button>
        <Button variant="ghost" onClick={onCancel} data-testid="assign-cancel">
          취소
        </Button>
        {missing && <span className="text-label text-warn">{missing}</span>}
        {error && <span className="text-label text-danger" role="alert">{error}</span>}
      </div>
    </div>
  );
}

export function SheetTables({
  tables,
  rfqId,
  team,
  onRead,
}: {
  tables: SheetTable[];
  rfqId: string;
  team: string | null;
  /** New lines after a table was assigned; the caller refills the line items. */
  onRead: (lines: RfqLines) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const unread = tables.filter((t) => t.how === "none");
  const read = tables.filter((t) => t.how !== "none");
  const row = (t: SheetTable) => (
    <li key={t.id} className="flex flex-col gap-2" data-testid={`sheet-table-${t.id}`} data-how={t.how}>
      <div className="flex flex-wrap items-center gap-2 text-dense">
        <span className="font-semibold text-ink">{t.title}</span>
        <span className="text-label text-ink-muted">
          {HOW_LABEL[t.how]}
          {t.lines > 0 && ` · 품목 ${t.lines}개`}
        </span>
        <span className="min-w-0 flex-1 truncate text-label text-ink-muted" title={t.rows[t.header_row]?.join(" · ")}>
          {t.rows[t.header_row]?.filter(Boolean).join(" · ")}
        </span>
        {t.how === "continued" ? (
          <span className="text-label text-ink-muted">이어진 표 — 첫 표에서 열을 지정하세요</span>
        ) : (
          <Button variant="ghost" data-testid={`assign-open-${t.id}`} onClick={() => setOpen(open === t.id ? null : t.id)}>
            {t.how === "none" ? "열 지정" : "열 다시 지정"}
          </Button>
        )}
      </div>
      {open === t.id && t.how !== "continued" && (
        <ColumnAssigner table={t} rfqId={rfqId} team={team} onCancel={() => setOpen(null)}
          onRead={(lines) => {
            setOpen(null);
            onRead(lines);
          }} />
      )}
    </li>
  );
  if (tables.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 rounded-control border border-line p-3" data-testid="sheet-tables">
      {unread.length > 0 && (
        <>
          <p className="text-label text-ink-muted">
            <span className="font-semibold text-ink">읽지 못한 표 {unread.length}개</span>가 있습니다. 가격표(품목표)라면 열을
            지정해 주세요. 가격표가 아니면 그대로 두면 됩니다.
          </p>
          <ul className="flex flex-col gap-2">{unread.map(row)}</ul>
        </>
      )}
      {read.length > 0 && (
        <details>
          <summary className="cursor-pointer text-label font-semibold text-ink-muted">
            품목을 읽은 표 {read.length}개 (잘못 읽었으면 열을 다시 지정)
          </summary>
          <ul className="mt-2 flex flex-col gap-2">{read.map(row)}</ul>
        </details>
      )}
    </div>
  );
}

function pagesLabel(pages: number[]): string {
  return pages.length > 1 ? `${pages[0]}–${pages[pages.length - 1]}쪽` : `${pages[0]}쪽`;
}

/** The part of the source a list is printed on, for the viewer beside the picker. */
export function listRegion(list: SheetList): SourceRegion {
  const page = list.boxes[0]?.page ?? list.pages[0];
  return {
    source_doc: list.source_doc,
    page,
    boxes: list.boxes,
    label: `${list.caption || "품목 목록"} · ${pagesLabel(list.pages)}`,
  };
}

/**
 * Which item lists of the RFQ to price. An RFQ may print the same items twice, as a specification
 * and again as the bid form: the lists with price columns are suggested, the user decides, and
 * two picked lists that repeat each other are flagged (nothing is merged automatically).
 */
export function SheetLists({
  lists,
  chosen,
  onChange,
  shown,
  onShow,
}: {
  lists: SheetList[];
  chosen: Set<string>;
  onChange: (next: Set<string>) => void;
  shown: string | null;
  onShow: (list: SheetList) => void;
}) {
  const files = usePackageFiles();
  const byId = new Map(lists.map((l) => [l.id, l]));
  const name = (id: string) => byId.get(id)?.caption || "다른 목록";
  const toggle = (id: string, on: boolean) => {
    const next = new Set(chosen);
    if (on) next.add(id);
    else next.delete(id);
    onChange(next);
  };
  return (
    <div className="flex flex-col gap-2" data-testid="sheet-lists">
      <p className="text-label text-ink-muted">
        <span className="font-semibold text-ink">RFQ에서 품목 목록 {lists.length}개를 찾았습니다.</span> 가격을 매길 목록을
        고르세요. 단가·금액 칸이 있는 목록(견적 기입표)을 먼저 골라 두었습니다. 목록을 누르면 오른쪽 원문에서 그 표를
        보여줍니다.
      </p>
      <ul className="flex flex-col gap-2">
        {lists.map((l) => {
          const clash = chosen.has(l.id) ? l.overlaps.filter((o) => chosen.has(o.list)) : [];
          return (
            <li key={l.id} data-testid={`sheet-list-${l.id}`} data-chosen={chosen.has(l.id)}
              className={cx("flex flex-col gap-1 rounded-control border p-2", shown === l.id ? "border-accent" : "border-line")}>
              <div className="flex items-start gap-2">
                <input type="checkbox" className="mt-1" checked={chosen.has(l.id)} disabled={l.lines === 0}
                  aria-label={`${l.caption || "품목 목록"} 가격 매기기`} data-testid="list-check"
                  onChange={(e) => toggle(l.id, e.target.checked)} />
                <button type="button" className="flex min-w-0 flex-1 flex-col items-start gap-0.5 text-left" data-testid="list-show"
                  onClick={() => onShow(l)}>
                  <span className="text-dense font-semibold text-ink">{l.caption || "품목 목록"}</span>
                  <span className="text-label text-ink-muted">
                    {files.length > 1 && `${fileTag(files, l.source_doc)} · `}
                    {pagesLabel(l.pages)} · 품목 {l.lines}개 · {l.has_price ? "단가·금액 칸 있음" : "단가 칸 없음"}
                    {l.how === "layout" && " · 전에 지정한 양식"}
                  </span>
                  <span className="w-full truncate text-label text-ink-muted" title={l.header.filter(Boolean).join(" · ")}>
                    {l.header.filter(Boolean).join(" · ")}
                  </span>
                </button>
              </div>
              {l.replaced_by && (
                <p className="text-label text-ink-muted" data-testid="list-replaced">
                  수정본 &lsquo;{name(l.replaced_by)}&rsquo;이 이 목록의 품목을 모두 대체했습니다.
                </p>
              )}
              {clash.map((o) => (
                <p key={o.list} role="status" className="text-label text-warn" data-testid="list-overlap">
                  &lsquo;{name(o.list)}&rsquo;와 품목 {o.items}개가 같아 보입니다. 같은 품목의 사양표와 견적표라면 하나만 고르세요.
                </p>
              ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
