/** What the backend computed: verdict, quotes, cost lines, score and shocks. */
import type { ReactNode } from "react";
import type { DecisionStatus, PrecostResponse } from "@/lib/rfq/contract";
import { fractionToPercent } from "@/lib/rfq/precost";
import { IconCheck, IconPause, IconWarn } from "../ui/icons";
import { cx } from "../ui/primitives";

export const DECISION: Record<DecisionStatus, { label: string; className: string; icon: ReactNode }> = {
  go: { label: "Go", className: "border-ok bg-ok-tint text-ok", icon: <IconCheck size={16} /> },
  conditional: { label: "조건부 Go", className: "border-warn bg-warn-tint text-warn", icon: <IconWarn size={16} /> },
  no_go: { label: "No-Go", className: "border-danger bg-danger-tint text-danger", icon: <IconWarn size={16} /> },
  hold: { label: "보류", className: "border-line-control bg-surface text-ink", icon: <IconPause size={16} /> },
  baseline_undefined: { label: "기준 없음", className: "border-line-control bg-surface text-ink-muted", icon: null },
  not_applicable: { label: "자동 판정 대상 아님", className: "border-line-control bg-surface text-ink-muted", icon: null },
};

/** "1234567.8900" → "1,234,567.89": thousands grouped, trailing zeros after the point dropped. */
export function grouped(value: string): string {
  const [whole, fraction = ""] = value.split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = whole.replace("-", "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const kept = fraction.replace(/0+$/, "");
  return `${sign}${digits}${kept ? `.${kept}` : ""}`;
}

export type Cost = NonNullable<PrecostResponse["data"]["cost"]>;

export function QuoteTiles({ c, cur }: { c: Cost; cur: string }) {
  const resale = c.cost_basis === "resale";
  const tiles = [
    { key: "min", title: "최소 견적 (수명주기 평균 마진 유지)", unit: c.min_quote, total: c.totals.min_quote, markup: c.markup?.min_quote, tone: "bg-canvas", titleTone: "text-ink-muted" },
    { key: "safe", title: "안전 견적 (복합 충격 반영)", unit: c.safe_quote, total: c.totals.safe_quote, markup: c.markup?.safe_quote, tone: "bg-accent-tint", titleTone: "text-accent" },
  ];
  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid grid-cols-2 gap-3">
        {tiles.map((t) => (
          <div key={t.key} className={cx("flex flex-col gap-1 rounded-control p-3", t.tone)} data-testid={`${t.key}-tile`}>
            <span className={cx("text-label", t.titleTone)}>{t.title}</span>
            <span className="text-heading font-semibold text-ink tabular" data-testid={`${t.key}-quote`}>
              {grouped(t.unit)} <span className="text-label">{cur} / {resale ? "단위" : "개"}</span>
            </span>
            {t.markup !== undefined && (
              <span className="text-label text-ink tabular" data-testid={`${t.key}-markup`}>
                매입가 대비 마크업 {fractionToPercent(t.markup)} %
              </span>
            )}
            <span className="border-t border-line pt-1 text-dense text-ink tabular" data-testid={`${t.key}-total`}>
              총 견적 <span className="font-semibold">{grouped(t.total)}</span> {cur}
            </span>
          </div>
        ))}
      </div>
      <p className="text-label text-ink-muted tabular">
        총 견적 = 연도별 물량 × 그 해 단가(연간 인하 반영)의 합, 전체 {c.total_quantity.toLocaleString("ko-KR")}{" "}
        {resale ? "단위" : "개"}.
        {resale && c.markup
          ? ` 단위는 물량을 넣은 기준과 같습니다(예: 매입 100 ${cur}어치 = 1단위). 단위당 매입가 ${grouped(c.markup.purchased)} ${cur}.`
          : ""}
      </p>
    </div>
  );
}

export function DecisionBox({ d }: { d: PrecostResponse["data"] }) {
  const decision = DECISION[d.decision.status];
  return (
      <section
        aria-labelledby="decision-title"
        className={cx("flex flex-col gap-2 rounded-control border p-3", decision.className)}
        data-testid="decision"
        data-status={d.decision.status}
      >
        <h3 id="decision-title" className="sr-only">판정</h3>
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex items-center gap-1.5 text-title font-semibold" data-testid="decision-status">
            {decision.icon}
            {decision.label}
          </span>
          {d.decision.score !== null && (
            <span className="ml-auto text-title font-semibold tabular" data-testid="decision-score">
              {d.decision.score}
              <span className="text-label font-medium"> / 100점</span>
            </span>
          )}
        </div>
        <ul className="flex list-disc flex-col gap-0.5 pl-5 text-dense text-ink">
          {d.decision.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        <p className="text-label text-ink-muted" data-testid="decision-rule">
          판정 규칙 <code className="font-semibold text-ink">{d.decision.rule}</code> · data/rules/cost_model.yaml을 그대로 적용한
          결과이며, 입찰 여부는 원가 담당이 정합니다.
        </p>
      </section>
  );
}

export function ScoreTable({ score }: { score: NonNullable<PrecostResponse["data"]["score"]> }) {
  return (
    <table className="w-full text-left text-dense tabular" data-testid="score-items">
      <caption className="pb-1 text-left text-label font-semibold text-ink-muted">
        가중 점수 {score.total}점 (Go ≥ 70 · 조건부 50~70)
      </caption>
      <tbody>
        {score.items.map((s) => (
          <tr key={s.id} className="border-t border-line">
            <td className="py-1.5 pr-2 text-ink">{s.label}</td>
            <td className="py-1.5 pr-2 text-right text-label text-ink-muted">×{s.weight}%</td>
            <td className="py-1.5 pr-2 text-right font-semibold text-ink">{s.score}</td>
            <td className="py-1.5 text-label text-ink-muted">{s.note}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function KnockoutStatus({ k }: { k: PrecostResponse["data"]["knockout"] }) {
  if (k.checks.length === 0) return null;
  return (
    <p className="text-label text-ink-muted" data-testid="knockout-status" data-status={k.status}>
      1차 넉아웃:{" "}
      <strong className="text-ink">{k.status === "passed" ? "모두 충족" : k.status === "failed" ? "불충족 있음" : "판단 불가 있음"}</strong> (
      {k.checks.length}개 조건)
    </p>
  );
}

export const AWARD_SOURCE = { input: "직접 고름", rfq: "RFQ에서 읽음", assumed: "RFQ에 없어 가정" } as const;

export function LinesResult({ d }: { d: PrecostResponse["data"] }) {
  const cur = d.currency;
  const lines = d.lines ?? [];
  const summary = d.summary;
  if (!summary) return null;
  const split = d.award?.mode === "split";
  return (
    <div className="flex flex-col gap-4 p-4" data-testid="precost-result">
      <DecisionBox d={d} />
      {d.award && (
        <p className="text-label text-ink-muted" data-testid="result-award">
          낙찰 방식: <strong className="text-ink">{d.award.label}</strong> ({AWARD_SOURCE[d.award.source]}) · 입찰 품목 {summary.lines_bid}/
          {lines.length}개
        </p>
      )}
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1 rounded-control bg-canvas p-3" data-testid="lines-min-total">
          <span className="text-label text-ink-muted">최소 견적 총액 (입찰 품목 합계)</span>
          <span className="text-heading font-semibold text-ink tabular">
            {grouped(summary.totals.min_quote)} <span className="text-label">{cur}</span>
          </span>
        </div>
        <div className="flex flex-col gap-1 rounded-control bg-accent-tint p-3" data-testid="lines-safe-total">
          <span className="text-label text-accent">안전 견적 총액</span>
          <span className="text-heading font-semibold text-ink tabular">
            {grouped(summary.totals.safe_quote)} <span className="text-label">{cur}</span>
          </span>
        </div>
      </div>
      <p className="-mt-2 text-label text-ink-muted tabular">
        전체 {summary.total_quantity.toLocaleString("ko-KR")}개
        {summary.target_total !== null ? ` · 목표가 총액 ${grouped(summary.target_total)} ${cur}` : " · 목표가가 없는 품목이 있어 목표가 총액 없음"}
        {Number(summary.shared_costs) > 0 ? ` · 공통비 ${grouped(summary.shared_costs)} ${cur} → 개당 ${summary.shared_per_unit} 배분` : ""}
      </p>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-dense tabular" data-testid="line-results">
          <caption className="pb-1 text-left text-label font-semibold text-ink-muted">품목별 결과 (단가는 개당)</caption>
          <thead className="text-label text-ink-muted">
            <tr>
              <th scope="col" className="py-1 pr-2">품목</th>
              <th scope="col" className="px-1 py-1 text-right">목표가</th>
              <th scope="col" className="px-1 py-1 text-right">최소</th>
              <th scope="col" className="px-1 py-1 text-right">안전</th>
              <th scope="col" className="px-1 py-1 text-right">총 견적 (최소)</th>
              <th scope="col" className="py-1 pl-2">판정</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => {
              const status = DECISION[l.decision.status];
              return (
                <tr key={`${l.item}-${i}`} className={cx("border-t border-line", !l.included && "text-ink-muted")} data-testid="line-result"
                  data-status={l.decision.status} data-included={l.included}>
                  <td className="py-1.5 pr-2">
                    <span className={cx("font-medium", l.included ? "text-ink" : "line-through")}>{l.item}</span>
                    {split && summary.suggested.includes(i) && <span className="ml-1.5 text-label text-ok">추천</span>}
                    {!l.included && <span className="ml-1.5 text-label">입찰 제외</span>}
                  </td>
                  <td className="px-1 py-1.5 text-right">{l.target_price ?? "–"}</td>
                  <td className="px-1 py-1.5 text-right">{l.min_quote}</td>
                  <td className="px-1 py-1.5 text-right">{l.safe_quote}</td>
                  <td className="px-1 py-1.5 text-right">{grouped(l.totals.min_quote)}</td>
                  <td className="py-1.5 pl-2">
                    <span className={cx("inline-flex rounded-control border px-1.5 text-label font-semibold", status.className)}>
                      {status.label}
                      {l.decision.score !== null ? ` ${l.decision.score}` : ""}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <details className="text-dense">
        <summary className="cursor-pointer text-label font-semibold text-ink-muted">품목별 판정 근거와 원가 구성</summary>
        <ul className="mt-2 flex flex-col gap-3">
          {lines.map((l, i) => (
            <li key={`${l.item}-${i}`} className="flex flex-col gap-1">
              <span className="font-semibold text-ink">{l.item}</span>
              <ul className="list-disc pl-5 text-label text-ink">
                {l.decision.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              <p className="text-label text-ink-muted tabular">
                {l.lines.map((c) => `${c.label} ${c.value}`).join(" · ")} → 개당 원가 {l.piece_cost} {cur}
              </p>
            </li>
          ))}
        </ul>
      </details>
      {d.score && <ScoreTable score={d.score} />}
      <KnockoutStatus k={d.knockout} />
    </div>
  );
}

export function Result({ run }: { run: PrecostResponse }) {
  const d = run.data;
  if (d.lines) return <LinesResult d={d} />;
  if (!d.cost) return null;
  const cost = d.cost;
  const cur = d.currency;
  const resale = cost.cost_basis === "resale";
  return (
    <div className="flex flex-col gap-4 p-4" data-testid="precost-result">
      <DecisionBox d={d} />

      <QuoteTiles c={cost} cur={cur} />
      <p className="-mt-2 text-label text-ink-muted tabular">
        {resale ? "단위당 원가" : "개당 원가"} {cost.piece_cost}
        {cost.tooling_per_piece && Number(cost.tooling_per_piece) > 0 ? ` + 금형 ${cost.tooling_per_piece}` : ""} · 수명주기 계수 {cost.lifetime_factor}
        {cost.material_indexed ? " · 재료비 연동(인하 제외)" : ""}
        {resale ? " · 유통 산식: 불량·판관비 가산 없음, 운영비는 원가 항목으로 직접 반영" : ""}
      </p>

      {cost.lines.length > 0 && (
        <table className="w-full text-left text-dense tabular" data-testid="cost-lines">
          <caption className="pb-1 text-left text-label font-semibold text-ink-muted">
            원가 구성 ({resale ? "단위당" : "개당"}, 가장 불리한 경우)
          </caption>
          <tbody>
            {cost.lines.map((l) => (
              <tr key={l.category} className="border-t border-line" data-category={l.category}>
                <td className="py-1.5 pr-2 text-ink">{l.label}</td>
                <td className="py-1.5 text-right font-semibold text-ink">
                  {l.value} {cur}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {d.score && <ScoreTable score={d.score} />}
      <KnockoutStatus k={d.knockout} />

      <details className="text-dense" open={cost.scenarios.length > 1}>
        <summary className="cursor-pointer text-label font-semibold text-ink-muted">충격과 충돌 경우</summary>
        <table className="mt-1 w-full text-left tabular" data-testid="shocks">
          <tbody>
            {cost.shocks.map((s) => (
              <tr key={s.id} className="border-t border-line">
                <td className={cx("py-1.5 pr-2", s.applied ? "text-ink" : "text-ink-muted line-through")}>{s.label}</td>
                <td className="py-1.5 pr-2 text-right font-semibold text-ink">
                  {s.min_quote_delta !== null && s.applied ? `${s.min_quote_delta} ${cur}` : ""}
                </td>
                <td className="py-1.5 text-label text-ink-muted">{s.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {cost.scenarios.length > 1 && (
          <table className="mt-3 w-full text-left tabular" data-testid="scenarios">
            <caption className="pb-1 text-left text-label text-ink-muted">충돌 값별 최소 견적 (가장 불리한 값을 씀)</caption>
            <tbody>
              {cost.scenarios.map((s) => (
                <tr key={s.label} className="border-t border-line">
                  <td className="py-1.5 pr-2 text-ink">{s.label}</td>
                  <td className="py-1.5 text-right font-semibold text-ink">
                    {s.min_quote} {cur}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>

      <p className="text-label text-ink-muted tabular" data-testid="npv">
        참고 NPV ({cost.npv.price_basis === "target_price" ? "목표가" : "최소 견적"} 기준, 할인율{" "}
        {fractionToPercent(cost.npv.discount_rate)}%): <strong className="text-ink">{cost.npv.value} {cur}</strong>
        {cost.npv.payback_year !== null ? ` · 회수 ${cost.npv.payback_year}년` : " · 기간 내 회수 안 됨"} · 판정에는 쓰지
        않습니다.
      </p>
    </div>
  );
}
