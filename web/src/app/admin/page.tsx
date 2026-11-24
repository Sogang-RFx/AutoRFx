"use client";
/**
 * Accounts and permissions (docs/auth-rbac.md §3): account table, creation, role and department,
 * password reset, forced logout, disable, delete, and the audit log. Administrators only; the server
 * refuses everybody else as well.
 */
import { useState } from "react";
import { AccountPanel } from "@/components/admin/account-panel";
import { AuditLog } from "@/components/admin/audit-log";
import { CreateAccount } from "@/components/admin/create-account";
import { ScopeTable } from "@/components/admin/scope-table";
import { UsersTable } from "@/components/admin/users-table";
import { PageHeader } from "@/components/shell/page-header";
import { ErrorState, ForbiddenState, Panel, Skeleton } from "@/components/ui/primitives";
import { rfqApi } from "@/lib/rfq/provider";
import { useViewer } from "@/lib/rfq/session";
import { useMeta } from "@/lib/rfq/use-meta";
import { useResource } from "@/lib/rfq/use-resource";

export default function AdminPage() {
  const viewer = useViewer();
  return (
    <main className="mx-auto flex min-h-dvh max-w-320 flex-col gap-6 px-4 py-8 md:px-8">
      <PageHeader title="계정·권한" />
      {viewer.can_manage ? (
        <Accounts />
      ) : (
        <Panel title="계정·권한" labelId="admin-title">
          <ForbiddenState message="계정 관리는 관리자 계정만 할 수 있습니다." />
        </Panel>
      )}
    </main>
  );
}

function Accounts() {
  const meta = useMeta();
  const [version, setVersion] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const users = useResource("admin:users", (s) => rfqApi.adminUsers(s), version);
  const list = users.status === "ok" ? users.data.data.users : [];
  const current = list.find((u) => u.id === selected) ?? null;
  const reload = () => setVersion((v) => v + 1);

  return (
    <>
      <p className="max-w-192 text-body text-ink-muted">
        역할과 부서가 볼 수 있는 조항을 정합니다. 바꾼 내용은 그 계정의 다음 요청부터 적용됩니다.
      </p>
      {meta.error && <ErrorState message={`부서 정보를 불러오지 못했습니다. ${meta.error}`} />}
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Panel title={`계정 ${list.length}개`} labelId="users-title">
          {users.status === "loading" && <Skeleton lines={6} />}
          {users.status === "error" &&
            (users.forbidden ? (
              <ForbiddenState message={users.error} />
            ) : (
              <ErrorState message={users.error} onRetry={reload} />
            ))}
          {users.status === "ok" && (
            <UsersTable users={list} departments={meta.departments} selected={selected} onSelect={setSelected} />
          )}
        </Panel>
        {current ? (
          <AccountPanel
            key={current.id}
            user={current}
            departments={meta.departments}
            onChanged={reload}
            onDeleted={() => {
              setSelected(null);
              reload();
            }}
            onClose={() => setSelected(null)}
          />
        ) : (
          <CreateAccount departments={meta.departments} onCreated={reload} />
        )}
      </div>
      <ScopeTable departments={meta.departments} note={meta.data?.assumption_note ?? null} />
      <AuditLog version={version} onReload={reload} />
    </>
  );
}
