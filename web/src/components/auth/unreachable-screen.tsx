"use client";
/** The backend does not answer: not the same as signed out, so no login form and no other data (INV-10). */
import { useState } from "react";
import { Button } from "../ui/primitives";
import { IconWarn } from "../ui/icons";
import { AuthFrame } from "./auth-frame";

export function UnreachableScreen({ error, onRetry }: { error: string; onRetry: () => Promise<void> }) {
  const [isBusy, setIsBusy] = useState(false);

  async function retry() {
    setIsBusy(true);
    await onRetry();
    setIsBusy(false);
  }

  return (
    <AuthFrame>
      <div role="alert" className="flex flex-col gap-2" data-testid="unreachable">
        <h1 className="flex items-center gap-2 text-title font-semibold text-ink">
          <IconWarn size={20} className="shrink-0 text-danger" />
          백엔드에 연결하지 못했습니다
        </h1>
        <p className="text-body text-ink-muted">
          로그인 여부를 확인할 수 없어 아무것도 보여주지 않습니다. 서버가 켜져 있는지 확인하고 다시 시도하세요.
        </p>
        <p className="border-l-2 border-danger pl-3 text-dense text-ink">{error}</p>
      </div>
      <Button variant="primary" onClick={retry} disabled={isBusy} className="min-h-10 self-start">
        {isBusy ? "확인하는 중…" : "다시 시도"}
      </Button>
    </AuthFrame>
  );
}
