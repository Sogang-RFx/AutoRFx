"use client";
/** Login (docs/auth-rbac.md §3, §6). One message for a wrong id and a wrong password. */
import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import type { AnonymousReason } from "@/lib/rfq/auth-store";
import { loginError } from "@/lib/rfq/auth-messages";
import { useSession } from "@/lib/rfq/session";
import { Button, FormMessage, TextField } from "../ui/primitives";
import { AuthFrame } from "./auth-frame";

const REASON: Record<Exclude<AnonymousReason, null>, string> = {
  expired: "로그인이 끝났습니다. 다시 로그인하면 보던 화면으로 돌아갑니다.",
  signed_out: "로그아웃했습니다.",
};

export function LoginScreen({ reason }: { reason: AnonymousReason }) {
  const { login } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [capsLock, setCapsLock] = useState(false);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => usernameRef.current?.focus(), []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (isBusy) return;
    setIsBusy(true);
    setError(null);
    try {
      // Success replaces this screen; there is nothing left to update.
      await login(username.trim(), password);
    } catch (err) {
      setError(loginError(err));
      setIsBusy(false);
      setPassword("");
      passwordRef.current?.focus();
    }
  }

  const onKey = (e: KeyboardEvent) => setCapsLock(e.getModifierState("CapsLock"));

  return (
    <AuthFrame>
      <div className="flex flex-col gap-1">
        <h1 className="text-title font-semibold text-ink">로그인</h1>
        <p className="text-body text-ink-muted">부서 계정으로 들어오세요. 볼 수 있는 범위는 계정이 정합니다.</p>
      </div>
      {!error && <FormMessage tone="info">{reason && REASON[reason]}</FormMessage>}
      <form onSubmit={submit} className="flex flex-col gap-4" data-testid="login-form">
        <TextField
          ref={usernameRef}
          label="아이디"
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          required
          maxLength={64}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="min-h-10"
        />
        <TextField
          ref={passwordRef}
          label="비밀번호"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={onKey}
          onKeyUp={onKey}
          onBlur={() => setCapsLock(false)}
          hint={capsLock ? "Caps Lock이 켜져 있습니다." : undefined}
          className="min-h-10"
        />
        <FormMessage tone="error">{error}</FormMessage>
        <Button type="submit" variant="primary" className="min-h-10" disabled={isBusy || !username.trim() || !password}>
          {isBusy ? "확인하는 중…" : "로그인"}
        </Button>
      </form>
      <p className="border-t border-line pt-4 text-label text-ink-muted">
        계정과 비밀번호 초기화는 관리자가 맡습니다.
      </p>
    </AuthFrame>
  );
}
