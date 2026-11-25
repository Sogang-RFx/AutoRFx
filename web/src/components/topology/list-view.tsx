"use client";
/**
 * The flow map as tables: clauses per department, clauses per file, and the count of clauses the account
 * may not see. This is what shows when WebGL is missing or the scene fails, and it is always one click away
 * ("목록으로 보기"). Choosing a department here is the keyboard path into the same selection as the map.
 */
import { cx } from "@/components/ui/primitives";
import type { TopoModel } from "@/lib/topology/model";
import { departmentRows, fileRows } from "@/lib/topology/tables";
import type { Selection } from "@/lib/topology/selection";

const HEAD = "px-3 py-2 text-left text-label font-semibold text-ink-muted";
const NUM = "px-3 py-2 text-right tabular";

interface ListViewProps {
  model: TopoModel;
  selection: Selection;
  onPickDepartment: (id: string) => void;
}

export function ListView({ model, selection, onPickDepartment }: ListViewProps) {
  const departments = departmentRows(model);
  const files = fileRows(model);
  const showIssues = model.issues !== null;
  return (
    <div className="flex flex-col gap-6 p-4" data-testid="topology-list">
      <section aria-labelledby="topology-dept-title" className="overflow-x-auto">
        <h3 id="topology-dept-title" className="mb-2 text-dense font-semibold text-ink">
          부서별 조항 수
        </h3>
        <table className="w-full min-w-96 border-collapse text-dense">
          <caption className="sr-only">부서마다 주관하는 조항과 참조하는 조항의 수</caption>
          <thead>
            <tr className="border-b border-line">
              <th scope="col" className={HEAD}>부서</th>
              <th scope="col" className={cx(HEAD, "text-right")}>주관</th>
              <th scope="col" className={cx(HEAD, "text-right")}>참조</th>
              <th scope="col" className={cx(HEAD, "text-right")}>독소조항</th>
            </tr>
          </thead>
          <tbody>
            {departments.map((d) => {
              const isChosen = selection?.kind === "department" && selection.id === d.id;
              return (
                <tr key={d.id} className={cx("border-b border-line", isChosen && "bg-accent-tint")}>
                  <th scope="row" className="px-3 py-1 text-left font-medium">
                    <button
                      type="button"
                      aria-pressed={isChosen}
                      onClick={() => onPickDepartment(d.id)}
                      className="min-h-8 rounded-chip px-1 text-left text-ink hover:text-accent"
                    >
                      {d.label}
                    </button>
                  </th>
                  <td className={NUM}>{d.lead}</td>
                  <td className={NUM}>{d.ref}</td>
                  <td className={NUM}>{d.redFlags}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section aria-labelledby="topology-file-title" className="overflow-x-auto">
        <h3 id="topology-file-title" className="mb-2 text-dense font-semibold text-ink">
          파일별 조항 수
        </h3>
        <table className="w-full min-w-96 border-collapse text-dense">
          <caption className="sr-only">파일마다 열람할 수 있는 조항과 열람 권한이 없는 조항의 수</caption>
          <thead>
            <tr className="border-b border-line">
              <th scope="col" className={HEAD}>파일</th>
              <th scope="col" className={cx(HEAD, "text-right")}>조항</th>
              <th scope="col" className={cx(HEAD, "text-right")}>열람 권한 없음</th>
              <th scope="col" className={cx(HEAD, "text-right")}>독소조항</th>
              {showIssues && <th scope="col" className={cx(HEAD, "text-right")}>정합성 이슈</th>}
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.id} className="border-b border-line">
                <th scope="row" className="px-3 py-2 text-left font-medium text-ink">
                  <span className="tabular text-ink-muted">{f.tag}</span> {f.name}
                </th>
                <td className={NUM}>{f.visible}</td>
                <td className={NUM}>{f.hidden}</td>
                <td className={NUM}>{f.redFlags}</td>
                {showIssues && <td className={NUM}>{f.issues}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <p className="text-dense text-ink-muted tabular" data-testid="hidden-count">
        {model.hiddenClauses > 0
          ? `열람 권한이 없는 조항 ${model.hiddenClauses}개: 부서와 내용은 이 계정에 표시되지 않습니다.`
          : "이 계정은 모든 조항을 열람할 수 있습니다."}
        {model.issues && model.issues.hidden > 0 && ` 열람 권한이 없는 이슈 ${model.issues.hidden}건이 따로 있습니다.`}
      </p>
    </div>
  );
}
