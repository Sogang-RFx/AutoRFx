/** Name, role and department of an account; the scope that follows is written under the choice. */
import { useId } from "react";
import { ROLE_LABEL, departmentCost, departmentScope, type AccountDraft } from "@/lib/rfq/admin";
import type { Department, Role } from "@/lib/rfq/contract";
import { TextField, controlClass } from "../ui/primitives";

type Props = { draft: AccountDraft; departments: Department[]; onChange: (patch: Partial<AccountDraft>) => void };

export function RoleFields({ draft, departments, onChange }: Props) {
  const id = useId();
  const department = departments.find((d) => d.id === draft.department) ?? null;
  const isAdmin = draft.role === "admin";
  const scope = isAdmin
    ? "조항 전체를 보고 계정과 회사 설정을 관리합니다."
    : department
      ? `조항 범위: ${departmentScope(department)} · 금액 조항: ${departmentCost(department)} · ${department.role}`
      : "부서를 고르면 조항 범위가 정해집니다.";
  return (
    <>
      <TextField
        label="이름"
        name="display-name"
        autoComplete="off"
        maxLength={60}
        value={draft.display_name}
        onChange={(e) => onChange({ display_name: e.target.value })}
      />
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-role`} className="text-label font-semibold text-ink-muted">
            역할
          </label>
          <select
            id={`${id}-role`}
            value={draft.role}
            onChange={(e) => onChange({ role: e.target.value as Role })}
            className={controlClass}
          >
            {(["member", "admin"] as const).map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={`${id}-department`} className="text-label font-semibold text-ink-muted">
            부서
          </label>
          <select
            id={`${id}-department`}
            value={isAdmin ? "" : draft.department}
            disabled={isAdmin}
            aria-describedby={`${id}-scope`}
            onChange={(e) => onChange({ department: e.target.value })}
            className={controlClass}
          >
            <option value="">{isAdmin ? "없음" : "고르세요"}</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p id={`${id}-scope`} className="text-label text-ink-muted">
        {scope}
      </p>
    </>
  );
}
