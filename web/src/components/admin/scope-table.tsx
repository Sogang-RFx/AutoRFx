/** What each department sees, from the rules the backend sent; nothing about a department is written here. */
import { departmentCost, departmentScope } from "@/lib/rfq/admin";
import type { Department } from "@/lib/rfq/contract";
import { Panel } from "../ui/primitives";

export function ScopeTable({ departments, note }: { departments: Department[]; note: string | null }) {
  return (
    <Panel title="부서별 범위" labelId="scope-title">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-dense" data-testid="scope-table">
          <thead className="border-b border-line bg-canvas text-label text-ink-muted">
            <tr>
              <th scope="col" className="px-4 py-2 font-semibold">부서</th>
              <th scope="col" className="px-3 py-2 font-semibold">하는 일</th>
              <th scope="col" className="px-3 py-2 font-semibold">조항 범위</th>
              <th scope="col" className="px-3 py-2 font-semibold">금액 조항</th>
            </tr>
          </thead>
          <tbody>
            {departments.map((d) => (
              <tr key={d.id} className="border-b border-line last:border-b-0">
                <th scope="row" className="px-4 py-2 font-semibold text-ink">{d.label}</th>
                <td className="px-3 py-2 text-ink-muted">{d.role}</td>
                <td className="px-3 py-2 text-ink">
                  {departmentScope(d)}
                  {!d.receives_all && <span className="text-ink-muted"> · 주관하거나 참조하는 조항</span>}
                </td>
                <td className={d.cost_visible ? "px-3 py-2 text-ink" : "px-3 py-2 text-ink-muted"}>{departmentCost(d)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {note && <p className="border-t border-line px-4 py-3 text-label text-ink-muted">{note}</p>}
    </Panel>
  );
}
