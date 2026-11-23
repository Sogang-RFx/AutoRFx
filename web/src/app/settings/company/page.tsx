"use client";
/**
 * Company settings (initial setup for the pre-cost). The supplier's own figures,
 * which no RFQ contains: a unit-price table, the five knockout core items
 * (certifications, processes, supply qualifications, payment limit, liability
 * limit) and a few rates. Saved as a whole on the server. Cost figures, so only
 * cost-visible teams can open it.
 */
import Link from "next/link";
import { useState } from "react";
import { useTeamGroup } from "@/components/shell/app-shell";
import { IconLock } from "@/components/ui/icons";
import { Button, EmptyState, ErrorState, ForbiddenState, Panel, Skeleton, cx } from "@/components/ui/primitives";
import { ITEM_CATEGORIES, type CompanyRequest, type CompanyResponse, type ItemCategory } from "@/lib/rfq/contract";
import {
  CATEGORY_DEPARTMENT,
  CATEGORY_LABEL,
  currencyOptions,
  defaultValidUntil,
  fractionToPercent,
  isDecimal,
  isInt,
  percentToFraction,
} from "@/lib/rfq/precost";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

type Row = {
  key: string;
  id: string | null;
  category: ItemCategory;
  name: string;
  unit: string;
  price: string;
  currency: string;
  valid_until: string;
  department: string;
};
type Form = {
  home_currency: string;
  discount: string; // percent
  margin: string;
  scrap: string;
  sga: string;
  max_payment_days: string;
  max_liability: string;
  certifications: string;
  processes: string;
  qualifications: string;
};

let nextKey = 0;
const newKey = () => `price-row-${++nextKey}`;

function toForm(d: CompanyResponse["data"]): Form {
  return {
    home_currency: d.settings.home_currency,
    discount: fractionToPercent(d.settings.discount_rate),
    margin: fractionToPercent(d.settings.min_margin),
    scrap: fractionToPercent(d.settings.scrap_rate),
    sga: fractionToPercent(d.settings.sga_rate),
    max_payment_days: d.settings.max_payment_days?.toString() ?? "",
    max_liability: d.settings.max_liability ?? "",
    certifications: d.profile.certifications.join(", "),
    processes: d.profile.processes.join(", "),
    qualifications: d.profile.qualifications.join(", "),
  };
}

const toRows = (d: CompanyResponse["data"]): Row[] => d.prices.map((p) => ({ ...p, key: newKey() }));
const list = (v: string) =>
  v
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);

function problems(form: Form, rows: Row[]): string[] {
  const out: string[] = [];
  if (!/^[A-Z]{3}$/.test(form.home_currency)) out.push("기준 통화는 영문 대문자 3자(예: KRW)로 넣으세요.");
  for (const [k, label] of [
    ["discount", "할인율"],
    ["margin", "최소 마진"],
    ["scrap", "스크랩률"],
    ["sga", "판관비율"],
  ] as const) {
    if (!isDecimal(form[k])) out.push(`${label}을(를) 숫자(%)로 넣으세요.`);
  }
  if (form.max_payment_days && !isInt(form.max_payment_days)) out.push("결제 기간 한도는 일수(정수)로 넣으세요.");
  if (form.max_liability && !isDecimal(form.max_liability)) out.push("책임 한도는 숫자로 넣으세요.");
  rows.forEach((r, i) => {
    if (!r.name.trim() || !r.unit.trim() || !isDecimal(r.price) || !/^[A-Z]{3}$/.test(r.currency) || !r.valid_until)
      out.push(`단가표 ${i + 1}행: 이름·단위·단가·통화·유효기간을 모두 넣으세요.`);
  });
  return out;
}

export default function CompanySettingsPage() {
  const session = useSession();
  const { restricted, ready } = useTeamGroup();
  const loaded = useResource(ready && !restricted ? `company:${session.team}` : null, (s) =>
    rfqApi.company({ team: session.team }, s),
  );
  const [state, setState] = useState<{ form: Form; rows: Row[]; data: CompanyResponse["data"] } | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  if (state === null && loaded.status === "ok") {
    setState({ form: toForm(loaded.data.data), rows: toRows(loaded.data.data), data: loaded.data.data });
  }

  const header = (
    <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
      <Link href="/" className="font-serif text-heading font-semibold text-ink">
        AutoRFx
      </Link>
      <h1 className="text-heading font-semibold text-ink">사전원가 기본 설정</h1>
      <button type="button" onClick={() => history.back()} className="ml-auto text-label text-ink-muted hover:text-ink">
        ← 돌아가기
      </button>
    </header>
  );

  if (ready && restricted) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-5xl flex-col gap-6 px-4 py-8 md:px-8">
        {header}
        <Panel title="사전원가 기본 설정" labelId="company-title">
          <EmptyState title="이 팀에는 원가 정보가 제공되지 않습니다">
            <p className="flex items-center gap-1.5">
              <IconLock size={14} /> 금액 열람이 허용된 팀을 고르면 볼 수 있습니다.
            </p>
          </EmptyState>
        </Panel>
      </main>
    );
  }

  const setForm = (patch: Partial<Form>) => {
    setState((s) => (s ? { ...s, form: { ...s.form, ...patch } } : s));
    setNotice(null);
  };
  const setRows = (rows: Row[]) => {
    setState((s) => (s ? { ...s, rows } : s));
    setNotice(null);
  };

  async function save() {
    if (!state) return;
    const { form, rows } = state;
    const body: CompanyRequest = {
      settings: {
        home_currency: form.home_currency,
        discount_rate: percentToFraction(form.discount),
        min_margin: percentToFraction(form.margin),
        scrap_rate: percentToFraction(form.scrap),
        sga_rate: percentToFraction(form.sga),
        max_payment_days: form.max_payment_days ? Number(form.max_payment_days) : null,
        max_liability: form.max_liability.trim() || null,
      },
      profile: {
        certifications: list(form.certifications),
        processes: list(form.processes),
        qualifications: list(form.qualifications),
      },
      prices: rows.map((r) => ({
        id: r.id,
        category: r.category,
        name: r.name.trim(),
        unit: r.unit,
        price: r.price.trim(),
        currency: r.currency,
        valid_until: r.valid_until,
        department: r.department,
      })),
    };
    setIsSaving(true);
    setNotice(null);
    try {
      const res = await rfqApi.saveCompany(body, { team: session.team });
      setState({ form: toForm(res.data), rows: toRows(res.data), data: res.data });
      setNotice({ kind: "ok", text: `저장했습니다. 단가표 ${res.data.prices.length}행.` });
    } catch (e) {
      setNotice({ kind: "error", text: describeError(e) });
    } finally {
      setIsSaving(false);
    }
  }

  const issues = state ? problems(state.form, state.rows) : [];
  const text = (key: keyof Form, label: string, hint?: string, suffix?: string) =>
    state && (
      <label className="flex flex-col gap-1">
        <span className="text-label font-semibold text-ink-muted">{label}</span>
        <span className="flex items-center gap-1.5">
          <input
            value={state.form[key]}
            onChange={(e) => setForm({ [key]: e.target.value })}
            className="min-h-9 w-full rounded-control border border-line-control bg-surface px-2 text-dense text-ink tabular"
            data-testid={`company-${key}`}
          />
          {suffix && <span className="text-label text-ink-muted">{suffix}</span>}
        </span>
        {hint && <span className="text-label text-ink-muted">{hint}</span>}
      </label>
    );

  return (
    <main className="mx-auto flex min-h-dvh max-w-6xl flex-col gap-6 px-4 py-8 md:px-8">
      {header}
      <p className="max-w-3xl text-body text-ink-muted">
        RFQ에는 우리 회사의 원가가 없습니다. 처음 한 번 단가표와 기본 비율, 1차 넉아웃 핵심 항목 5가지를 넣어 두면 사전원가
        화면이 이 값을 씁니다. 넣지 않은 값은 추측하지 않고, 계산할 때 물어봅니다.
      </p>
      {loaded.status === "error" &&
        (loaded.forbidden ? <ForbiddenState message={loaded.error} /> : <ErrorState message={loaded.error} />)}
      {(loaded.status === "loading" || !ready) && <Skeleton lines={6} />}

      {state && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="기본 비율" labelId="rates-title">
              <div className="grid gap-3 p-4 sm:grid-cols-2">
                <label className="flex flex-col gap-1">
                  <span className="text-label font-semibold text-ink-muted">기준 통화</span>
                  <select
                    value={state.form.home_currency}
                    onChange={(e) => setForm({ home_currency: e.target.value })}
                    className="min-h-9 w-full rounded-control border border-line-control bg-surface px-2 text-dense text-ink"
                    data-testid="company-home_currency"
                  >
                    {currencyOptions(state.form.home_currency).map((c) => (
                      <option key={c.code} value={c.code}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                  <span className="text-label text-ink-muted">견적 통화와 다르면 환율 충격을 반영합니다.</span>
                </label>
                {text("discount", "할인율 (NPV)", "WACC 또는 차입금리 + 2~3%p", "%")}
                {text("margin", "최소 마진", undefined, "%")}
                {text("scrap", "스크랩률", "직접비에 곱합니다.", "%")}
                {text("sga", "판관비율", "직접비에 곱합니다.", "%")}
              </div>
            </Panel>
            <Panel title="1차 넉아웃 핵심 항목" labelId="profile-title">
              <div className="grid gap-3 p-4 sm:grid-cols-2">
                {text("certifications", "보유 인증", "쉼표로 구분 · 예: IATF 16949, ISO 14001")}
                {text("processes", "주력 공정", "예: 프레스, 용접, 전착도장")}
                {text("qualifications", "공급 자격", "예: 현대차 1차 협력사")}
                {text("max_payment_days", "결제 기간 한도", "이보다 긴 결제 조건은 질문으로 확인합니다.", "일")}
                {text("max_liability", "책임 한도 기준", "이보다 큰 책임 한도는 질문으로 확인합니다.")}
              </div>
            </Panel>
          </div>

          <Panel
            title={`단가표 ${state.rows.length}행`}
            labelId="prices-title"
            actions={
              <Button
                data-testid="price-add"
                onClick={() =>
                  setRows([
                    ...state.rows,
                    {
                      key: newKey(),
                      id: null,
                      category: "material",
                      name: "",
                      unit: "kg",
                      price: "",
                      currency: state.form.home_currency,
                      valid_until: defaultValidUntil(state.data.today),
                      department: CATEGORY_DEPARTMENT.material,
                    },
                  ])
                }
              >
                행 추가
              </Button>
            }
          >
            {state.rows.length === 0 ? (
              <p className="p-6 text-dense text-ink-muted">
                아직 단가가 없습니다. 재료·가공·구매품·물류 단가를 넣어 두면 사전원가 화면에서 고를 수 있습니다. 사전원가
                화면에서 새로 넣은 단가도 여기에 저장됩니다.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[820px] text-left text-dense">
                  <thead className="border-b border-line text-label text-ink-muted">
                    <tr>
                      <th scope="col" className="px-3 py-2">분류</th>
                      <th scope="col" className="px-2 py-2">이름</th>
                      <th scope="col" className="px-2 py-2">단위</th>
                      <th scope="col" className="px-2 py-2">단가</th>
                      <th scope="col" className="px-2 py-2">통화</th>
                      <th scope="col" className="px-2 py-2">유효기간</th>
                      <th scope="col" className="px-2 py-2"><span className="sr-only">삭제</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.rows.map((r, i) => {
                      const patch = (p: Partial<Row>) =>
                        setRows(state.rows.map((x) => (x.key === r.key ? { ...x, ...p } : x)));
                      const expired = r.valid_until !== "" && r.valid_until < state.data.today;
                      const cell = "min-h-8 w-full rounded-control border border-line-control bg-surface px-2 text-dense text-ink";
                      return (
                        <tr key={r.key} className="border-b border-line last:border-0" data-testid="price-row">
                          <td className="px-3 py-1.5">
                            <select
                              aria-label={`${i + 1}행 분류`}
                              value={r.category}
                              onChange={(e) => {
                                const category = e.target.value as ItemCategory;
                                patch({ category, department: CATEGORY_DEPARTMENT[category] });
                              }}
                              className={cell}
                            >
                              {ITEM_CATEGORIES.map((c) => (
                                <option key={c} value={c}>
                                  {CATEGORY_LABEL[c]}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="px-2 py-1.5">
                            <input aria-label={`${i + 1}행 이름`} value={r.name} maxLength={80}
                              placeholder="예: 고장력강판 HC420LA" onChange={(e) => patch({ name: e.target.value })} className={cell} />
                          </td>
                          <td className="w-20 px-2 py-1.5">
                            <input aria-label={`${i + 1}행 단위`} value={r.unit} maxLength={16}
                              onChange={(e) => patch({ unit: e.target.value })} className={cell} />
                          </td>
                          <td className="w-28 px-2 py-1.5">
                            <input aria-label={`${i + 1}행 단가`} value={r.price} inputMode="decimal"
                              onChange={(e) => patch({ price: e.target.value })} className={cx(cell, "tabular")} />
                          </td>
                          <td className="w-24 px-2 py-1.5">
                            <select aria-label={`${i + 1}행 통화`} value={r.currency}
                              onChange={(e) => patch({ currency: e.target.value })} className={cell}>
                              {currencyOptions(r.currency).map((c) => (
                                <option key={c.code} value={c.code}>
                                  {c.code}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="w-40 px-2 py-1.5">
                            <input type="date" aria-label={`${i + 1}행 유효기간`} value={r.valid_until}
                              onChange={(e) => patch({ valid_until: e.target.value })}
                              className={cx(cell, expired && "border-warn text-warn")} />
                          </td>
                          <td className="px-2 py-1.5">
                            <Button variant="ghost" onClick={() => setRows(state.rows.filter((x) => x.key !== r.key))}>
                              삭제
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          {state.data.answers.length > 0 && (
            <Panel title={`저장된 넉아웃 답 ${state.data.answers.length}개`} labelId="answers-title">
              <ul className="divide-y divide-line">
                {state.data.answers.map((a) => (
                  <li key={a.key} className="flex flex-wrap items-baseline gap-2 px-4 py-2 text-dense">
                    <span className="text-ink">{a.label}</span>
                    <span className={cx("ml-auto font-semibold", a.answer === "yes" ? "text-ok" : "text-danger")}>
                      {a.answer === "yes" ? "예" : "아니오"}
                    </span>
                    <span className="text-label text-ink-muted tabular">{a.saved_on}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" data-testid="company-save" disabled={isSaving || issues.length > 0} onClick={save}>
              {isSaving ? "저장 중…" : "저장"}
            </Button>
            {issues.length > 0 && <span className="text-label text-warn">{issues[0]}</span>}
            {notice && (
              <p
                role={notice.kind === "error" ? "alert" : "status"}
                data-testid={`company-${notice.kind}`}
                className={cx("text-dense", notice.kind === "error" ? "text-danger" : "text-ok")}
              >
                {notice.text}
              </p>
            )}
          </div>
        </>
      )}
    </main>
  );
}
