"use client";
/**
 * Pre-cost Go/No-Go (SPEC §8). Steps ①–⑤ come from the backend's setup: the RFQ
 * type, the knockout questions (answered here as yes / no / unknown), the cost
 * inputs the RFQ states with citations, and cost items the price table can
 * already price. The user confirms the inputs and the cost items; the backend
 * then computes the lifetime minimum quote, the shock range, the score and the
 * verdict (⑥–⑨). This screen never computes a number and adds no
 * recommendation of its own.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { LinesEditor, MANUAL, SheetPicker, VolumesEditor, ItemsEditor } from "@/components/precost/editors";
import { Field, Step, input } from "@/components/precost/fields";
import { Cite, KnockoutCount, Question } from "@/components/precost/questions";
import { Result } from "@/components/precost/result";
import { SheetLists, SheetTables, listRegion } from "@/components/precost/sheet-tables";
import { SourceViewer, type SourceRegion } from "@/components/source-viewer/source-viewer";
import { IconCalc } from "@/components/ui/icons";
import { Button, EmptyState, ErrorState, ForbiddenState, Panel, Skeleton, cx } from "@/components/ui/primitives";
import type { CostInputCandidate, PrecostResponse, PriceRow, RfqLines } from "@/lib/rfq/contract";
import { currencyOptions } from "@/lib/rfq/precost";
import {
  AWARD_RFQ,
  STARTS,
  build,
  candidate,
  conflicts,
  initialForm,
  initialItems,
  initialVolumes,
  linesFromRfq,
  newKey,
  type Answer,
  type AwardChoice,
  type Company,
  type FieldSpec,
  type Form,
  type ItemRow,
  type LineRow,
  type Setup,
  type VolumeRow,
} from "@/lib/rfq/precost-form";
import { describeError, isForbidden, rfqApi } from "@/lib/rfq/provider";
import { isCostVisible } from "@/lib/rfq/scope";
import { useViewer } from "@/lib/rfq/session";
import { useMeta } from "@/lib/rfq/use-meta";
import { useResource } from "@/lib/rfq/use-resource";

const FORBIDDEN_NOTE = "이 계정에는 원가 정보가 제공되지 않습니다. 금액 열람 부서의 계정만 볼 수 있습니다.";

async function load(rfqId: string, signal: AbortSignal) {
  const [setup, company] = await Promise.all([rfqApi.precostSetup(rfqId, signal), rfqApi.company(signal)]);
  return { setup: setup.data, company: company.data };
}

export default function PrecostPage() {
  const { id } = useParams<{ id: string }>();
  const rfqId = decodeURIComponent(id);
  const viewer = useViewer();
  const canSee = isCostVisible(viewer);
  const loaded = useResource(canSee ? `precost:${rfqId}` : null, (s) => load(rfqId, s));

  if (!canSee) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <Panel title="사전원가" labelId="precost-title">
          <ForbiddenState message={FORBIDDEN_NOTE} />
        </Panel>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4 p-3 md:p-6">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
        <div className="min-w-0 flex-1">
          <h1 className="text-title font-semibold text-ink">사전원가 Go / No-Go</h1>
          <p className="text-body text-ink-muted">
            RFQ 유형과 1차 넉아웃을 확인하고, RFQ에 없는 값만 채우면 최소 견적·안전 견적·점수·판정을 냅니다. 단가표와 회사
            설정은 위의 기본 설정(톱니바퀴)에서 관리합니다. AI는 수치를 만들지 않습니다.
          </p>
          <p className="text-label text-ink-muted" data-testid="precost-scope-note">
            사전원가는 규칙에 따라 RFQ 전체를 입력으로 씁니다. 부서마다 판정이 달라지지 않게 하기 위해서입니다.
            {!viewer.full_scope && " 원문 PDF는 여기서도 내 열람 범위까지만 보입니다."}
          </p>
        </div>
      </div>
      {loaded.status === "loading" && <Skeleton lines={6} />}
      {loaded.status === "error" &&
        (loaded.forbidden ? <ForbiddenState message={loaded.error} /> : <ErrorState message={loaded.error} />)}
      {loaded.status === "ok" && (
        <Workbench setup={loaded.data.setup} company={loaded.data.company} rfqId={rfqId} />
      )}
    </div>
  );
}

// ── the workbench ───────────────────────────────────────────────────────────
function Workbench({ setup, company, rfqId }: { setup: Setup; company: Company; rfqId: string }) {
  // Answers are remembered for the next RFQ only when an administrator gives them (docs/auth-rbac.md §2).
  const remembers = useViewer().can_manage;
  const [form, setFormState] = useState<Form>(() => initialForm(setup, company));
  const [volumes, setVolumes] = useState<VolumeRow[]>(() => initialVolumes(setup));
  const [items, setItems] = useState<ItemRow[]>(() => initialItems(setup));
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [resolved, setResolved] = useState<Record<string, string>>({});
  const prices: PriceRow[] = company.prices;
  const [editing, setEditing] = useState<Set<string>>(() => new Set());
  const [result, setResult] = useState<PrecostResponse | null>(null);
  const [error, setError] = useState<{ message: string; forbidden: boolean } | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const processValues = setup.requirements.filter((r) => r.requirement === "process").map((r) => r.value);
  // The sheet changes when the user assigns the columns of a table the dictionary could not read.
  const [sheetLines, setSheetLines] = useState(setup.lines);
  // The item lists to price: an RFQ may print the same items as a specification and as the bid form.
  const suggestedLists = (read: RfqLines) => new Set(read.lists.filter((l) => l.suggested).map((l) => l.id));
  const [chosenLists, setChosenLists] = useState<Set<string>>(() => suggestedLists(setup.lines));
  const pickedItems = (read: RfqLines, chosen: Set<string>) => read.items.filter((r) => chosen.has(r.list));
  const sheetAll = pickedItems(sheetLines, chosenLists);
  const sheet = sheetAll.slice(0, sheetLines.limit);
  const firstList = sheetLines.lists.find((l) => l.suggested) ?? sheetLines.lists[0];
  const [region, setRegion] = useState<{ id: string; region: SourceRegion } | null>(() =>
    firstList ? { id: firstList.id, region: listRegion(firstList) } : null,
  );
  const sheetYear = initialVolumes(setup)[0]?.year ?? String(new Date().getFullYear() + 1);
  const [byLine, setByLine] = useState(setup.award.mode === "split" || setup.award.mode === "either" || sheet.length > 0);
  const [award, setAward] = useState<AwardChoice>("rfq");
  const [shared, setShared] = useState("0");
  const [lines, setLines] = useState<LineRow[]>(() =>
    sheet.length > 0
      ? linesFromRfq(sheet, sheetYear, processValues)
      : [{ key: newKey(), item: setup.cost_inputs.item || "품목 1", target: "", volumes: initialVolumes(setup), items: initialItems(setup), processes: processValues, included: true }],
  );
  // "Either": the customer chooses after the bids, so every line is judged on its own as under a split award.
  const rfqAward = setup.award.mode === "either" ? "split" : (setup.award.mode ?? "all_or_none");
  const effectiveAward = award === "rfq" ? rfqAward : award;

  const setForm = (patch: Partial<Form>) => setFormState((f) => ({ ...f, ...patch }));
  const { departmentLabel } = useMeta();
  // Distribution RFQs are priced per unit of purchases (e.g. 100 USD worth), not per piece.
  const per = setup.classification.type === "distribution" ? "단위" : "개";
  const asked = setup.requirements.filter((r) => r.status === "ask");
  const settled = setup.requirements.filter((r) => r.status !== "ask");
  const unanswered = asked.filter((r) => !answers[r.key]);
  const pickers = conflicts(setup.cost_inputs.candidates);
  const built = build(form, volumes, items, answers, setup.cost_inputs.item, byLine ? { lines, award, shared } : null, processValues);
  const problem = "problem" in built ? built.problem : unanswered.length > 0 ? `넉아웃 질문 ${unanswered.length}개에 답하세요.` : null;

  async function submit() {
    if (!("inputs" in built) || problem) return;
    setIsBusy(true);
    setError(null);
    try {
      setResult(await rfqApi.precost({ rfq_id: rfqId, inputs: built.inputs, resolved, remember: remembers }));
    } catch (err) {
      setError({ message: describeError(err), forbidden: isForbidden(err) });
    } finally {
      setIsBusy(false);
    }
  }

  const patchItem = (key: string, patch: Partial<ItemRow>) =>
    setItems((list) => list.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const toggle = (id: string) =>
    setEditing((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // ③/④: each RFQ condition is either stated in the RFQ (shown with its citation, editable on request)
  // or missing (an input in ④). A value read from a sentence that does not state it plainly is filled
  // in, marked as inferred, with that sentence: the user confirms it.
  const evidenceOf = (field: string) => candidate(setup, field)?.evidence ?? null;
  const statedIn = (field: string) => candidate(setup, field)?.value != null;
  const inferredIn = (field: string) => {
    const c = candidate(setup, field);
    return c?.source === "inferred" ? c.note : null;
  };
  const clause = (id: string) => setup.pass_through.find((p) => p.id === id)?.evidence ?? null;
  const switchLabel = (value: Form["material_indexed"], found: boolean, shock: string) =>
    value === "yes" || (value === "auto" && found) ? `있음 · ${shock} 충격 제외` : `없음 · ${shock} 충격 반영`;
  const switchEdit = (key: "material_indexed" | "fx_adjusted") => (
    <select className={cx(input, "max-w-60")} value={form[key]} onChange={(e) => setForm({ [key]: e.target.value as Form[typeof key] })}>
      <option value="auto">RFQ대로</option>
      <option value="yes">있음</option>
      <option value="no">없음</option>
    </select>
  );
  const fields: FieldSpec[] = [
    {
      id: "currency",
      label: "통화",
      stated: statedIn("currency"),
      inferred: inferredIn("currency"),
      evidence: evidenceOf("currency"),
      show: () => form.currency,
      edit: () => (
        <select className={cx(input, "max-w-60")} value={form.currency} data-testid="input-currency"
          onChange={(e) => setForm({ currency: e.target.value })}>
          {currencyOptions(form.currency, company.settings.home_currency).map((c) => (
            <option key={c.code} value={c.code}>
              {c.label}
            </option>
          ))}
        </select>
      ),
    },
    {
      id: "target_price",
      label: `목표가 (${per}당)`,
      hint: "RFQ에 목표가가 없으면 비워 두세요. 판정 대신 최소 입찰가만 냅니다.",
      stated: statedIn("target_price"),
      evidence: evidenceOf("target_price"),
      show: () => (form.target_price ? `${form.target_price} ${form.currency}` : "없음"),
      edit: () => (
        <input className={cx(input, "max-w-60 tabular")} value={form.target_price} inputMode="decimal" data-testid="input-target_price"
          onChange={(e) => setForm({ target_price: e.target.value.trim() })} />
      ),
    },
    {
      id: "payment_days",
      label: "결제 기한 (일)",
      hint: "모르면 비워 두세요.",
      stated: statedIn("payment_days"),
      inferred: inferredIn("payment_days"),
      evidence: evidenceOf("payment_days"),
      show: () => (form.payment_days ? `${form.payment_days}일` : "없음"),
      edit: () => (
        <input className={cx(input, "max-w-60 tabular")} value={form.payment_days} inputMode="numeric" data-testid="input-payment_days"
          onChange={(e) => setForm({ payment_days: e.target.value.trim() })} />
      ),
    },
    {
      id: "reduction",
      label: "연간 단가 인하율",
      hint: "매년 단가를 깎아 달라는 조건이 없으면 0입니다.",
      stated: setup.cost_inputs.annual_reduction_rate !== null,
      evidence: evidenceOf("annual_reduction_rate"),
      show: () => `${form.reduction || 0}% · ${STARTS[form.starts]}`,
      edit: () => (
        <div className="flex flex-wrap items-center gap-2">
          <input className={cx(input, "w-24 tabular")} value={form.reduction} inputMode="decimal" data-testid="input-reduction"
            aria-label="연간 단가 인하율 (%)" onChange={(e) => setForm({ reduction: e.target.value.trim() })} />
          <span className="text-label text-ink-muted">%</span>
          <select className={cx(input, "w-32")} value={form.starts} aria-label="인하 시작" onChange={(e) => setForm({ starts: e.target.value })}>
            {Object.entries(STARTS).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
      ),
    },
    {
      id: "volumes",
      label: "연도별 물량",
      hint:
        per === "단위"
          ? "유통 RFQ는 품목이 많아 '단위'로 넣습니다. 예: 매입 100 USD어치를 1단위로 잡으면 연간 예상 매입액 ÷ 100."
          : "RFQ의 계획 물량(개)을 연도별로 넣으세요.",
      stated: setup.cost_inputs.volumes.length > 0,
      evidence: evidenceOf("volume"),
      show: () => volumes.map((v) => `${v.year} ${Number(v.quantity || 0).toLocaleString("ko-KR")}`).join(" · "),
      edit: () => <VolumesEditor rows={volumes} setRows={setVolumes} per={per} />,
    },
    {
      id: "indexation",
      label: "원자재 연동",
      stated: true,
      evidence: clause("indexation"),
      show: () => switchLabel(form.material_indexed, clause("indexation") !== null, "원자재"),
      edit: () => switchEdit("material_indexed"),
    },
    {
      id: "fx_adjustment",
      label: "환율 조정",
      stated: true,
      evidence: clause("fx_adjustment"),
      show: () => switchLabel(form.fx_adjusted, clause("fx_adjustment") !== null, "환율"),
      edit: () => switchEdit("fx_adjusted"),
    },
  ];
  // Per line item, the target and the volumes are entered on each line.
  const shown = byLine ? fields.filter((f) => f.id !== "target_price" && f.id !== "volumes") : fields;
  const stated = shown.filter((f) => f.stated);
  const missing = shown.filter((f) => !f.stated);
  const hints = (["material", "net_weight", "thickness", "incoterm"] as const)
    .map((field) => candidate(setup, field))
    .filter((c): c is CostInputCandidate => !!c && c.value !== null)
    .map((c) => `${c.label} ${c.value}${c.unit ? ` ${c.unit}` : ""}`);

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
      <Panel title="입력" labelId="inputs-title" provenance={{ precost: "real" }}>
        <Step n="①" title={`RFQ 유형: ${setup.classification.label}`}>
          <p className="text-label text-ink-muted" data-testid="rfq-type" data-type={setup.classification.type}>
            {setup.classification.note}
          </p>
        </Step>

        <Step n="②" title="1차 넉아웃: 자격·계약 조건" aside={<KnockoutCount asked={asked.length} settled={settled.length} />}>
          {setup.requirements.length === 0 && (
            <p className="text-label text-ink-muted">RFQ에서 인증·공정·자격·책임·결제 조건을 찾지 못했습니다.</p>
          )}
          <ul className="flex flex-col gap-2" data-testid="knockout-questions">
            {asked.map((r) => (
              <Question
                key={r.key}
                r={r}
                department={departmentLabel(r.department)}
                remembered={remembers}
                value={answers[r.key]}
                onChange={(a) => setAnswers((p) => ({ ...p, [r.key]: a }))}
              />
            ))}
            {settled.map((r) => (
              <li key={r.key} className="flex flex-wrap items-center gap-2 text-label text-ink-muted">
                <span className={cx("font-semibold", r.status === "pass" ? "text-ok" : "text-danger")}>
                  {r.status === "pass" ? "충족" : "불충족"}
                </span>
                <span className="text-ink">{r.label}: {r.value}</span>
                <span>({r.source === "profile" ? "회사 설정" : "저장된 답"})</span>
                <Cite evidence={r.evidence} />
              </li>
            ))}
          </ul>
          <p className="text-label text-ink-muted">
            &lsquo;모름&rsquo;은 탈락시키지 않지만 Go를 조건부로 낮춥니다. {" "}
            {remembers ? "사실 질문의 예/아니오는 다음 RFQ에도 씁니다." : "여기서 고른 답은 이번 계산에만 쓰이고, 저장은 관리자만 합니다."}
          </p>
        </Step>

        <Step n="③" title="RFQ에서 읽은 값" aside={<span className="text-label text-ink-muted">확인만 하세요 · 다르면 수정</span>}>
          <ul className="flex flex-col divide-y divide-line rounded-control border border-line" data-testid="rfq-values">
            {stated.length === 0 && <li className="px-3 py-2 text-label text-ink-muted">RFQ에서 읽은 원가 조건이 없습니다.</li>}
            {stated.map((f) => (
              <li key={f.id} className="flex flex-col gap-1.5 px-3 py-2" data-testid={`rfq-value-${f.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="w-28 shrink-0 text-label font-semibold text-ink-muted">{f.label}</span>
                  <span className="text-dense font-semibold text-ink tabular">{f.show()}</span>
                  {f.inferred && (
                    <span className="rounded-control border border-warn bg-warn-tint px-1.5 text-label font-semibold text-warn"
                      data-testid={`inferred-${f.id}`}>
                      추정
                    </span>
                  )}
                  <Cite evidence={f.evidence} />
                  <button type="button" className="ml-auto text-label text-accent hover:underline" onClick={() => toggle(f.id)}
                    data-testid={`edit-${f.id}`}>
                    {editing.has(f.id) ? "닫기" : "수정"}
                  </button>
                </div>
                {f.inferred && <p className="text-label text-ink-muted sm:pl-30">{f.inferred}</p>}
                {editing.has(f.id) && <div className="pl-0 sm:pl-30">{f.edit()}</div>}
              </li>
            ))}
          </ul>
        </Step>

        <Step n="④" title="낙찰 방식·입찰 단위">
          <p className="flex flex-wrap items-center gap-2 text-label text-ink-muted" data-testid="award-mode" data-mode={setup.award.mode ?? "none"}>
            <span className="font-semibold text-ink">RFQ: {setup.award.label}</span>
            <Cite evidence={setup.award.evidence} />
          </p>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5" role="radiogroup" aria-label="입찰 단위">
            <label className="flex items-center gap-1.5 text-dense text-ink">
              <input type="radio" name="bid-unit" checked={!byLine} onChange={() => setByLine(false)} data-testid="bid-whole" />
              RFQ 전체를 한 품목으로 계산
            </label>
            <label className="flex items-center gap-1.5 text-dense text-ink">
              <input type="radio" name="bid-unit" checked={byLine} onChange={() => setByLine(true)} data-testid="bid-lines" />
              품목별로 계산
            </label>
          </div>
          {!byLine && sheet.length === 0 && sheetLines.tables.some((t) => t.how === "none") && (
            <p className="text-label text-ink-muted" data-testid="unread-tables-hint">
              RFQ에서 가격표로 읽지 못한 표가 있습니다. 품목별로 계산하려면 &lsquo;품목별로 계산&rsquo;을 고른 뒤 그 표의 열을 지정하세요.
            </p>
          )}
          {byLine && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field
                label="낙찰 방식"
                hint={effectiveAward === "split" ? "품목마다 판정하고, 입찰할 품목만 골라 합계를 냅니다." : "모든 품목을 입찰하고 전체 합계로 판정합니다."}
              >
                <select className={input} value={award} data-testid="input-award" onChange={(e) => setAward(e.target.value as AwardChoice)}>
                  <option value="rfq">
                    RFQ대로: {AWARD_RFQ[setup.award.mode ?? "none"]}
                  </option>
                  <option value="split">품목별 낙찰</option>
                  <option value="all_or_none">일괄 낙찰</option>
                </select>
              </Field>
              <Field label={`공통비 (총액, ${form.currency})`} hint="여러 품목이 함께 쓰는 금형·셋업비. 입찰하는 품목의 물량 비율로 나눕니다.">
                <input className={cx(input, "tabular")} value={shared} inputMode="decimal" data-testid="input-shared"
                  onChange={(e) => setShared(e.target.value.trim())} />
              </Field>
            </div>
          )}
          {byLine && sheetLines.lists.length > 0 && (
            <div className="grid gap-3 lg:grid-cols-2">
              <SheetLists lists={sheetLines.lists} chosen={chosenLists} shown={region?.id ?? null}
                onShow={(l) => setRegion({ id: l.id, region: listRegion(l) })}
                onChange={(next) => {
                  setChosenLists(next);
                  const rows = pickedItems(sheetLines, next).slice(0, sheetLines.limit);
                  setLines((list) => [...linesFromRfq(rows, sheetYear, processValues), ...list.filter((l) => l.group === MANUAL)]);
                }} />
              <SourceViewer rfqId={rfqId} target={null} region={region?.region ?? null}
                className="h-[36rem]" />
            </div>
          )}
          {byLine && sheet.length > 0 && (
            <SheetPicker key={[...chosenLists].join("|")} sheet={sheet} total={sheetAll.length} limit={sheetLines.limit}
              apply={(rows) =>
                // Lines added by hand are kept: the sheet reading missed them.
                setLines((list) => [...linesFromRfq(rows, sheetYear, processValues), ...list.filter((l) => l.group === MANUAL)])
              } />
          )}
          {byLine && (
            <SheetTables tables={sheetLines.tables} rfqId={rfqId}
              onRead={(read) => {
                setSheetLines(read);
                const chosen = suggestedLists(read);
                setChosenLists(chosen);
                const rows = pickedItems(read, chosen).slice(0, read.limit);
                const manuals = (list: LineRow[]) => list.filter((l) => l.group === MANUAL);
                // Replace sheet-derived lines when the re-read finds rows; on zero rows drop the
                // previous auto lines so they do not stay stale (manual lines are kept).
                setLines((list) =>
                  rows.length > 0
                    ? [...linesFromRfq(rows, sheetYear, processValues), ...manuals(list)]
                    : manuals(list),
                );
                // When there was no region yet (or lists changed), point the viewer at the first suggested list.
                const next = read.lists.find((l) => l.suggested) ?? read.lists[0];
                if (next) setRegion({ id: next.id, region: listRegion(next) });
              }} />
          )}
        </Step>

        <Step n="⑤" title="추가로 필요한 값">
          {missing.length > 0 && (
            <div className="flex flex-col gap-3" data-testid="missing-values">
              <p className="text-label text-ink-muted">RFQ에 적혀 있지 않은 값입니다. 직접 넣어 주세요.</p>
              {missing.map((f) => (
                <Field key={f.id} label={f.label} hint={f.hint}>
                  {f.edit()}
                </Field>
              ))}
            </div>
          )}
          {pickers.length > 0 && (
            <fieldset className="flex flex-col gap-3 rounded-control border border-line p-3" data-testid="conflict-pickers">
              <legend className="px-1 text-label font-semibold text-ink-muted">파일마다 다른 값: 고르지 않으면 경우마다 계산합니다</legend>
              <p className="text-label text-ink-muted">
                원가 항목의 수량은 RFQ에 먼저 적힌 값({pickers.map((c) => `${c.label} ${c.alternatives[0]?.value}`).join(", ")}) 기준으로
                넣으세요. 다른 값은 계산이 비례해 바꿉니다.
              </p>
              {pickers.map((c) => {
                const issueId = c.issue_id ?? "";
                return (
                  <div key={issueId} className="flex flex-col gap-1.5" role="radiogroup" aria-label={`${c.label} 값 고르기`}>
                    <span className="text-dense font-medium text-ink">
                      {c.label}
                      {c.unit ? ` (${c.unit})` : ""}
                      <Link href={`/rfq/${encodeURIComponent(rfqId)}/consistency`} className="ml-2 text-label text-accent hover:underline">
                        이슈 보기
                      </Link>
                    </span>
                    <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                      {[null, ...c.alternatives.map((a) => a.value)].map((v) => (
                        <label key={v ?? "none"} className="flex items-center gap-1.5 text-dense text-ink">
                          <input
                            type="radio"
                            name={`resolve-${issueId}`}
                            checked={(resolved[issueId] ?? null) === v}
                            data-testid={`resolve-${issueId}-${v ?? "none"}`}
                            onChange={() =>
                              setResolved((prev) => {
                                const next = { ...prev };
                                if (v === null) delete next[issueId];
                                else next[issueId] = v;
                                return next;
                              })
                            }
                          />
                          {v ?? "아직 정하지 않음"}
                        </label>
                      ))}
                    </div>
                  </div>
                );
              })}
            </fieldset>
          )}

          {byLine ? (
            <LinesEditor
              lines={lines}
              setLines={setLines}
              prices={prices}
              currency={form.currency}
              per={per}
              processValues={processValues}
              split={effectiveAward === "split"}
              suggested={result?.data.summary?.suggested ?? null}
            />
          ) : (
          <div className="flex flex-col gap-1.5">
            <span className="text-label font-semibold text-ink-muted">원가 항목 ({per}당 수량 × 단가)</span>
            {per === "단위" && (
              <p className="text-label text-ink-muted" data-testid="resale-unit-note">
                1단위 = 매입액 100 {form.currency}어치입니다. 첫 줄의 100은 RFQ 값이 아니라 단위의 정의라 그대로 두세요. 인건비·물류비 같은
                운영비는 줄을 추가해 {per}당 금액으로 넣으세요.
              </p>
            )}
            {hints.length > 0 && (
              <p className="text-label text-ink-muted" data-testid="item-hints">
                RFQ 참고: {hints.join(" · ")}
              </p>
            )}
            {prices.length === 0 && (
              <p className="rounded-control bg-warn-tint px-3 py-2 text-label text-warn">
                단가표가 비어 있습니다. 위의 기본 설정(톱니바퀴)에서 관리자가 단가를 넣으면 여기서 고를 수 있습니다.
              </p>
            )}
            <ItemsEditor rows={items} prices={prices} currency={form.currency} per={per} patch={patchItem} setRows={setItems} />
          </div>
          )}

          <div className="grid gap-3 sm:grid-cols-3">
            {!byLine && (
            <Field label="금형비 (총액)" hint="없으면 0">
              <input className={cx(input, "tabular")} value={form.tooling_amount} inputMode="decimal" data-testid="input-tooling"
                onChange={(e) => setForm({ tooling_amount: e.target.value.trim() })} />
            </Field>
            )}
            {!byLine && (
            <Field label="금형비 회수">
              <select className={input} value={form.tooling_payment}
                onChange={(e) => setForm({ tooling_payment: e.target.value as Form["tooling_payment"] })}>
                <option value="amortized">단가에 분할</option>
                <option value="lump_sum">양산 시 일시 지급</option>
                <option value="deferred">후불(N년 뒤)</option>
              </select>
            </Field>
            )}
            {!byLine && form.tooling_payment === "deferred" && (
              <Field label="후불 연수">
                <input className={cx(input, "tabular")} value={form.deferred_years} inputMode="numeric"
                  onChange={(e) => setForm({ deferred_years: e.target.value.trim() })} />
              </Field>
            )}
            <Field label="설비 가동률 (%)" hint="최대 연 물량 기준. 비우면 중립">
              <input className={cx(input, "tabular")} value={form.capacity} inputMode="decimal" data-testid="input-capacity"
                onChange={(e) => setForm({ capacity: e.target.value.trim() })} />
            </Field>
            <Field label="전략적 가치 (0~100)" hint="신규 고객·차종 등. 비우면 중립">
              <input className={cx(input, "tabular")} value={form.strategic} inputMode="numeric" data-testid="input-strategic"
                onChange={(e) => setForm({ strategic: e.target.value.trim() })} />
            </Field>
          </div>
        </Step>

        <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
          <Button variant="primary" onClick={submit} disabled={problem !== null || isBusy} data-testid="calc">
            <IconCalc size={14} /> {result ? "다시 계산" : "계산하고 판정"}
          </Button>
          {problem && <span className="text-label text-warn" data-testid="calc-problem">{problem}</span>}
        </div>
        {error && (error.forbidden ? <ForbiddenState message={error.message} /> : <ErrorState message={error.message} />)}
      </Panel>

      <Panel title="결과" labelId="result-title" provenance={result?.provenance} className="xl:sticky xl:top-4 xl:self-start">
        {!result && <EmptyState title="아직 계산하지 않았습니다" />}
        {result && setup.assumption_note && (
          <p role="note" className="border-b border-warn/40 bg-warn-tint px-4 py-2 text-label text-warn" data-testid="assumption-note">
            {setup.assumption_note}
          </p>
        )}
        {result && <Result run={result} />}
      </Panel>
    </div>
  );
}
