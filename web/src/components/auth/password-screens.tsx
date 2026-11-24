"use client";
/** The forced change after a reset (nothing else opens before it) and the dialog for a voluntary one. */
import { useEffect, useRef, useState } from "react";
import type { Viewer } from "@/lib/rfq/contract";
import { describeError } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { Button, FormMessage } from "../ui/primitives";
import { AuthFrame } from "./auth-frame";
import { PasswordForm } from "./password-form";

export function ForcedPasswordScreen({ viewer }: { viewer: Viewer }) {
  const { logout } = useSession();
  const [error, setError] = useState<string | null>(null);
  return (
    <AuthFrame>
      <div className="flex flex-col gap-1">
        <h1 className="text-title font-semibold text-ink">비밀번호를 먼저 바꿔 주세요</h1>
        <p className="text-body text-ink-muted">
          <strong className="font-semibold text-ink">{viewer.display_name}</strong> 계정은 관리자가 정한 임시 비밀번호를
          쓰고 있습니다. 새 비밀번호를 정하면 바로 이어서 쓸 수 있습니다.
        </p>
      </div>
      <PasswordForm
        username={viewer.username}
        secondary={
          <Button variant="ghost" onClick={() => logout().catch((e: unknown) => setError(describeError(e)))}>
            로그아웃
          </Button>
        }
      />
      <FormMessage tone="error">{error && `로그아웃하지 못했습니다. ${error}`}</FormMessage>
    </AuthFrame>
  );
}

/** A native modal dialog: the browser keeps the focus inside and closes it with Escape. */
export function PasswordDialog({ viewer, onClose }: { viewer: Viewer; onClose: (changed: boolean) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby="password-dialog-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose(false);
      }}
      className="m-auto w-[calc(100vw-2rem)] max-w-112 rounded-panel border border-line bg-surface p-6 text-ink shadow-float backdrop:bg-ink/40"
    >
      <div className="flex flex-col gap-4">
        <h2 id="password-dialog-title" className="text-heading font-semibold text-ink">
          비밀번호 변경
        </h2>
        <PasswordForm
          username={viewer.username}
          onDone={() => onClose(true)}
          secondary={
            <Button variant="ghost" onClick={() => onClose(false)}>
              취소
            </Button>
          }
        />
      </div>
    </dialog>
  );
}
