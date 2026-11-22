"use client";
/**
 * Department settings (SPEC US10, TASKS S103). The company adds its own departments
 * and ticks, for each, the work areas (the rule departments) it receives. Saving
 * replaces the whole list on the server; the team picker and the clause filter then
 * use these departments. Classification is not changed and nothing is reanalysed.
 * Cost visibility is off by default and warns when switched on (RBAC-03).
 */
import Link from "next/link";
import { useState } from "react";
import { IconWarn } from "@/components/ui/icons";
import { Button, ErrorState, Panel, Skeleton, cx } from "@/components/ui/primitives";
import type { OrgDepartment } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

type Row = { key: string; id: string | null; label: string; areas: string[]; cost_visible: boolean };

const MAX_DEPARTMENTS = 30;
const MAX_LABEL = 40;

let nextKey = 0;
const newKey = () => `row-${++nextKey}`;
const toRow = (d: OrgDepartment): Row => ({
  key: newKey(),
  id: d.id,
  label: d.label,
  areas: [...d.areas],
  cost_visible: d.cost_visible,
});

async function loadSettings(signal: AbortSignal) {
  const [meta, org] = await Promise.all([rfqApi.meta(signal), rfqApi.orgDepartments(signal)]);
  return { areas: meta.data.work_areas ?? [], legacy: meta.data.departments, departments: org.data.departments };
}

export default function DepartmentSettingsPage() {
  const session = useSession();
  const loaded = useResource(session.ready ? "org-settings" : null, loadSettings);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  // Start editing from what the server holds, once it arrives.
  if (rows === null && loaded.status === "ok") setRows(loaded.data.departments.map(toRow));
  const areas = loaded.status === "ok" ? loaded.data.areas : [];

  function update(key: string, patch: Partial<Row>) {
    setRows((current) => (current ?? []).map((r) => (r.key === key ? { ...r, ...patch } : r)));
    setNotice(null);
  }

  function choices(row: Row) {
    const legacy = loaded.status === "ok" ? loaded.data.legacy : [];
    const saved = legacy.filter((area) => row.areas.includes(area.id));
    return [...areas, ...saved.map(({ id, label }) => ({ id, label: label + " (기존 설정)" }))];
  }

  function toggleArea(row: Row, area: string) {
    const next = row.areas.includes(area) ? row.areas.filter((a) => a !== area) : [...row.areas, area];
    update(row.key, { areas: next });
  }

  async function save(list: Row[]) {
    setIsSaving(true);
    setNotice(null);
    try {
      const res = await rfqApi.saveOrgDepartments({
        departments: list.map(({ id, label, areas: chosen, cost_visible }) => ({ id, label, areas: chosen, cost_visible })),
      });
      setRows(res.data.departments.map(toRow));
      const count = res.data.departments.length;
      setNotice({
        kind: "ok",
        text:
          count === 0
            ? "기본 부서로 되돌렸습니다. 팀 선택은 룰셋의 기본 팀을 씁니다."
            : `저장했습니다. 팀 선택과 조항 필터에 부서 ${count}개가 반영됩니다.`,
      });
    } catch (e) {
      setNotice({ kind: "error", text: describeError(e) });
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-5xl flex-col gap-6 px-4 py-8 md:px-8">
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <Link href="/" className="font-serif text-heading font-semibold text-ink">
          AutoRFx
        </Link>
        <h1 className="text-heading font-semibold text-ink">부서 설정</h1>
        <button type="button" onClick={() => history.back()} className="ml-auto text-label text-ink-muted hover:text-ink">
          ← 돌아가기
        </button>
      </header>
      <p className="max-w-3xl text-body text-ink-muted">
        회사 부서를 추가하고, 부서마다 받을 <strong className="font-semibold text-ink">업무 영역</strong>을 고르세요. 저장하면 팀
        선택과 조항 필터가 이 부서로 바뀝니다. 조항이 어느 업무 영역으로 분류됐는지는 바뀌지 않고, 다시 분석하지도 않습니다.
      </p>

      {loaded.status === "error" && <ErrorState message={loaded.error} />}
      {loaded.status === "loading" && <Skeleton lines={5} />}

      {rows !== null && (
        <Panel
          title={`회사 부서 ${rows.length}개`}
          labelId="org-title"
          actions={
            <Button
              data-testid="org-add"
              disabled={rows.length >= MAX_DEPARTMENTS}
              onClick={() => setRows([...rows, { key: newKey(), id: null, label: "", areas: [], cost_visible: false }])}
            >
              부서 추가
            </Button>
          }
        >
          {rows.length === 0 ? (
            <p className="p-6 text-dense text-ink-muted" data-testid="org-empty">
              아직 회사 부서가 없습니다. 지금은 룰셋의 기본 8개 팀(영업·개발·품질·생산기술·구매·원가·물류·법무)을 씁니다.
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {rows.map((row, i) => (
                <li key={row.key} className="flex flex-col gap-3 px-4 py-4" data-testid="org-row">
                  <div className="flex flex-wrap items-center gap-3">
                    <label className="flex min-w-0 flex-1 basis-60 items-center gap-2 text-label text-ink-muted">
                      <span className="shrink-0">부서 이름</span>
                      <input
                        data-testid={`org-label-${i}`}
                        value={row.label}
                        maxLength={MAX_LABEL}
                        placeholder="예: 견적팀"
                        onChange={(e) => update(row.key, { label: e.target.value })}
                        className="min-h-9 min-w-0 flex-1 rounded-control border border-line-control bg-surface px-2 text-dense text-ink"
                      />
                    </label>
                    <label className="flex items-center gap-2 text-dense text-ink">
                      <input
                        type="checkbox"
                        data-testid={`org-cost-${i}`}
                        checked={row.cost_visible}
                        onChange={(e) => update(row.key, { cost_visible: e.target.checked })}
                      />
                      금액 열람 허용
                    </label>
                    <Button variant="ghost" onClick={() => setRows(rows.filter((r) => r.key !== row.key))}>
                      삭제
                    </Button>
                  </div>
                  {row.cost_visible && (
                    <p className="flex items-start gap-1.5 rounded-control bg-warn-tint px-3 py-2 text-label text-warn" role="note">
                      <IconWarn size={14} className="mt-0.5 shrink-0" />
                      이 부서는 목표가·단가 같은 금액 조항과 사전원가 화면을 볼 수 있습니다. 금액을 다루는 부서에만 켜세요.
                    </p>
                  )}
                  <fieldset>
                    <legend className="mb-1.5 text-label text-ink-muted">받을 업무 영역 · {row.areas.length}개 선택</legend>
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                      {choices(row).map((a) => {
                        const isOn = row.areas.includes(a.id);
                        return (
                          <label
                            key={a.id}
                            className={cx(
                              "inline-flex cursor-pointer items-center gap-1.5 min-h-10 rounded-control border px-3 py-2 text-dense font-medium transition-soft",
                              "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent",
                              isOn ? "border-ink bg-ink text-white" : "border-line text-ink-muted hover:border-line-control hover:text-ink",
                            )}
                          >
                            <input
                              type="checkbox"
                              className="shrink-0 accent-accent"
                              data-testid={`org-area-${i}-${a.id}`}
                              checked={isOn}
                              onChange={() => toggleArea(row, a.id)}
                            />
                            {a.label}

                          </label>
                        );
                      })}
                    </div>
                  </fieldset>
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
            <Button variant="primary" data-testid="org-save" disabled={isSaving} onClick={() => save(rows)}>
              {isSaving ? "저장 중…" : "저장"}
            </Button>
            <Button variant="ghost" data-testid="org-reset" disabled={isSaving} onClick={() => save([])}>
              기본 부서로 되돌리기
            </Button>
            {notice && (
              <p
                role={notice.kind === "error" ? "alert" : "status"}
                data-testid={`org-${notice.kind}`}
                className={cx("text-dense", notice.kind === "error" ? "text-danger" : "text-ok")}
              >
                {notice.text}
              </p>
            )}
          </div>
        </Panel>
      )}
    </main>
  );
}
