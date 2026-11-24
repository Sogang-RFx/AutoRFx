"use client";
/** Everything an administrator can do to one account. Deleting asks again inside the page. */
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { draftProblem, passwordProblem, toDraft, toPatch, type AccountDraft } from "@/lib/rfq/admin";
import type { AdminUser, Department } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { useSession, useViewer } from "@/lib/rfq/session";
import { IconClose } from "../ui/icons";
import { Button, FormMessage, Panel, TextField } from "../ui/primitives";
import { RoleFields } from "./role-fields";

type Props = {
  user: AdminUser;
  departments: Department[];
  onChanged: () => void;
  onDeleted: () => void;
  onClose: () => void;
};

export function AccountPanel({ user, departments, onChanged, onDeleted, onClose }: Props) {
  const viewer = useViewer();
  const { retry } = useSession();
  const [draft, setDraft] = useState<AccountDraft>(() => toDraft(user));
  const [password, setPassword] = useState("");
  const [isConfirming, setIsConfirming] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const titleRef = useRef<HTMLParagraphElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const deleteRef = useRef<HTMLButtonElement>(null);
  const isSelf = user.id === viewer.user_id;
  const patch = toPatch(user, draft);

  useEffect(() => titleRef.current?.focus(), []);
  useEffect(() => {
    if (isConfirming) cancelRef.current?.focus();
  }, [isConfirming]);

  async function run(done: string, action: () => Promise<unknown>, after: () => void = onChanged) {
    setIsBusy(true);
    setNotice(null);
    try {
      await action();
      setNotice({ tone: "ok", text: done });
      after();
      // A change to the own account applies to this screen too.
      if (isSelf) void retry();
    } catch (e) {
      setNotice({ tone: "error", text: describeError(e) });
    } finally {
      setIsBusy(false);
    }
  }

  function save(e: FormEvent) {
    e.preventDefault();
    const problem = draftProblem(draft);
    if (problem) return setNotice({ tone: "error", text: problem });
    if (patch) void run("바꾼 내용을 저장했습니다.", () => rfqApi.updateUser(user.id, patch));
  }

  function reset(e: FormEvent) {
    e.preventDefault();
    const problem = passwordProblem(password);
    if (problem) return setNotice({ tone: "error", text: problem });
    void run("임시 비밀번호로 바꿨습니다. 이 계정의 로그인은 모두 끝났습니다.", async () => {
      await rfqApi.resetPassword(user.id, { password });
      setPassword("");
    });
  }

  function cancelDelete() {
    setIsConfirming(false);
    // The delete button comes back with the next render.
    requestAnimationFrame(() => deleteRef.current?.focus());
  }

  return (
    <Panel
      title="계정 관리"
      labelId="account-panel-title"
      as="aside"
      actions={
        <Button variant="ghost" onClick={onClose} aria-label="계정 관리 닫기">
          <IconClose size={14} />
        </Button>
      }
    >
      <div className="flex flex-col divide-y divide-line" data-testid="account-panel" data-username={user.username}>
        <div className="flex flex-col gap-2 p-4">
          <p ref={titleRef} tabIndex={-1} className="text-body font-semibold text-ink">
            {user.display_name} <span className="text-label font-medium text-ink-muted">{user.username}</span>
          </p>
          {isSelf && <p className="text-label text-ink-muted">지금 로그인한 계정입니다. 바꾸면 이 화면에도 바로 적용됩니다.</p>}
          <FormMessage tone={notice?.tone ?? "ok"}>{notice?.text}</FormMessage>
        </div>

        <form onSubmit={save} className="flex flex-col gap-3 p-4">
          <RoleFields draft={draft} departments={departments} onChange={(p) => setDraft((d) => ({ ...d, ...p }))} />
          <Button type="submit" variant="primary" disabled={isBusy || !patch}>
            역할·부서 저장
          </Button>
        </form>

        <form onSubmit={reset} className="flex flex-col gap-3 p-4">
          <TextField
            label="새 임시 비밀번호"
            name="reset-password"
            autoComplete="off"
            spellCheck={false}
            maxLength={128}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            hint="8자 이상. 다음 로그인 때 반드시 바꿔야 합니다."
          />
          <Button type="submit" disabled={isBusy || !password}>
            비밀번호 초기화
          </Button>
        </form>

        <div className="flex flex-wrap gap-2 p-4">
          <Button
            disabled={isBusy || user.sessions === 0}
            onClick={() => run("이 계정을 모든 곳에서 로그아웃시켰습니다.", () => rfqApi.forceLogout(user.id))}
          >
            강제 로그아웃
          </Button>
          <Button
            disabled={isBusy}
            onClick={() =>
              run(user.disabled ? "다시 쓸 수 있게 했습니다." : "사용을 중지했습니다.", () =>
                rfqApi.updateUser(user.id, { disabled: !user.disabled }),
              )
            }
          >
            {user.disabled ? "다시 사용" : "사용 중지"}
          </Button>
          {!isConfirming && (
            <Button ref={deleteRef} variant="danger" disabled={isBusy} onClick={() => setIsConfirming(true)}>
              삭제
            </Button>
          )}
        </div>

        {isConfirming && (
          <div
            role="alertdialog"
            aria-labelledby="delete-title"
            aria-describedby="delete-text"
            data-testid="delete-confirm"
            onKeyDown={(e) => {
              if (e.key === "Escape") cancelDelete();
            }}
            className="flex flex-col gap-3 bg-danger-tint p-4"
          >
            <p id="delete-title" className="text-dense font-semibold text-danger">
              {user.username} 계정을 삭제할까요?
            </p>
            <p id="delete-text" className="text-dense text-ink">
              되돌릴 수 없습니다. 잠시 막아 두려면 사용 중지를 쓰세요. 감사 기록은 남습니다.
            </p>
            <div className="flex gap-2">
              <Button ref={cancelRef} disabled={isBusy} onClick={cancelDelete}>
                취소
              </Button>
              <Button
                variant="danger"
                disabled={isBusy}
                onClick={() => run("삭제했습니다.", () => rfqApi.deleteUser(user.id), onDeleted)}
              >
                삭제
              </Button>
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}
