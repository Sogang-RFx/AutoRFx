"use client";
/** Password change: the forced screen after a reset and the dialog of the account menu share it. */
import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { MAX_PASSWORD, MIN_PASSWORD, checkNewPassword, passwordError } from "@/lib/rfq/auth-messages";
import { useSession } from "@/lib/rfq/session";
import { Button, FormMessage, TextField } from "../ui/primitives";

type Props = { username: string; onDone?: () => void; secondary?: ReactNode };

export function PasswordForm({ username, onDone, secondary }: Props) {
  const { changePassword } = useSession();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const currentRef = useRef<HTMLInputElement>(null);
  const nextRef = useRef<HTMLInputElement>(null);

  useEffect(() => currentRef.current?.focus(), []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (isBusy) return;
    const problem = checkNewPassword(current, next, again);
    setError(problem);
    if (problem) {
      nextRef.current?.focus();
      return;
    }
    setIsBusy(true);
    try {
      await changePassword(current, next);
      onDone?.();
    } catch (err) {
      setError(passwordError(err));
      setIsBusy(false);
      currentRef.current?.focus();
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4" data-testid="password-form">
      {/* Tells a password manager whose password this is. */}
      <input type="text" name="username" autoComplete="username" value={username} readOnly hidden />
      <TextField
        ref={currentRef}
        label="현재 비밀번호"
        name="current-password"
        type="password"
        autoComplete="current-password"
        required
        maxLength={MAX_PASSWORD}
        value={current}
        onChange={(e) => setCurrent(e.target.value)}
      />
      <TextField
        ref={nextRef}
        label="새 비밀번호"
        name="new-password"
        type="password"
        autoComplete="new-password"
        required
        value={next}
        onChange={(e) => setNext(e.target.value)}
        hint={`${MIN_PASSWORD}자 이상 ${MAX_PASSWORD}자까지. 바꾸면 이 계정의 다른 로그인은 모두 끝납니다.`}
      />
      <TextField
        label="새 비밀번호 확인"
        name="new-password-again"
        type="password"
        autoComplete="new-password"
        required
        value={again}
        onChange={(e) => setAgain(e.target.value)}
      />
      <FormMessage tone="error">{error}</FormMessage>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" disabled={isBusy || !current || !next || !again}>
          {isBusy ? "바꾸는 중…" : "비밀번호 바꾸기"}
        </Button>
        {secondary}
      </div>
    </form>
  );
}
