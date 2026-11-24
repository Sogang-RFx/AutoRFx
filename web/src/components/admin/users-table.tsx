"use client";
import { ROLE_LABEL, departmentScope, formatTime } from "@/lib/rfq/admin";
import type { AdminUser, Department } from "@/lib/rfq/contract";
import { useViewer } from "@/lib/rfq/session";
import { Button, cx } from "../ui/primitives";

type Props = {
  users: AdminUser[];
  departments: Department[];
  selected: string | null;
  onSelect: (id: string | null) => void;
};

function scopeOf(user: AdminUser, departments: Department[]): string {
  if (user.role === "admin") return "전체";
  const department = departments.find((d) => d.id === user.department);
  return department ? departmentScope(department) : "확인 안 됨";
}

/** One table of all accounts. The state is a word, not a colour. */
export function UsersTable({ users, departments, selected, onSelect }: Props) {
  const viewer = useViewer();
  const label = (id: string | null) => departments.find((d) => d.id === id)?.label ?? id ?? "–";
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-dense" data-testid="users-table">
        <thead className="border-b border-line bg-canvas text-label text-ink-muted">
          <tr>
            <th scope="col" className="px-4 py-2 font-semibold">계정</th>
            <th scope="col" className="px-3 py-2 font-semibold">역할 · 부서</th>
            <th scope="col" className="px-3 py-2 font-semibold">조항 범위</th>
            <th scope="col" className="px-3 py-2 font-semibold">상태</th>
            <th scope="col" className="px-3 py-2 font-semibold">마지막 로그인</th>
            <th scope="col" className="px-3 py-2"><span className="sr-only">관리</span></th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr
              key={u.id}
              data-testid="user-row"
              data-username={u.username}
              className={cx("border-b border-line align-top last:border-b-0", selected === u.id && "bg-accent-tint")}
            >
              <th scope="row" className="px-4 py-2.5 font-regular">
                <span className="block font-semibold text-ink">
                  {u.display_name}
                  {u.id === viewer.user_id && <span className="ml-1 text-label font-medium text-ink-muted">나</span>}
                </span>
                <span className="text-label text-ink-muted">{u.username}</span>
              </th>
              <td className="px-3 py-2.5 text-ink">
                {u.role === "admin" ? ROLE_LABEL.admin : label(u.department)}
              </td>
              <td className="px-3 py-2.5 text-ink-muted">{scopeOf(u, departments)}</td>
              <td className="px-3 py-2.5">
                <span className={u.disabled ? "font-semibold text-danger" : "text-ink"}>
                  {u.disabled ? "사용 중지" : "사용 중"}
                </span>
                {u.must_change_password && <span className="block text-label text-ink-muted">임시 비밀번호</span>}
              </td>
              <td className="px-3 py-2.5 text-ink-muted tabular">
                {u.last_login === null ? "없음" : formatTime(u.last_login)}
                {u.sessions > 0 && <span className="block text-label">로그인 {u.sessions}곳</span>}
              </td>
              <td className="px-3 py-2 text-right">
                <Button
                  variant={selected === u.id ? "secondary" : "ghost"}
                  aria-pressed={selected === u.id}
                  aria-label={`${u.display_name} 계정 관리`}
                  onClick={() => onSelect(selected === u.id ? null : u.id)}
                >
                  관리
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
