"use client";
import { useState } from "react";
import type { FormEvent } from "react";
import { createProblem, toCreateRequest, type NewAccountDraft } from "@/lib/rfq/admin";
import type { Department } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { Button, FormMessage, Panel, TextField } from "../ui/primitives";
import { RoleFields } from "./role-fields";

const EMPTY: NewAccountDraft = { username: "", display_name: "", password: "", role: "member", department: "" };

export function CreateAccount({ departments, onCreated }: { departments: Department[]; onCreated: () => void }) {
  const [draft, setDraft] = useState(EMPTY);
  const [isBusy, setIsBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  function change(patch: Partial<NewAccountDraft>) {
    setDraft((d) => ({ ...d, ...patch }));
    setNotice(null);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const problem = createProblem(draft);
    if (problem) return setNotice({ tone: "error", text: problem });
    setIsBusy(true);
    setNotice(null);
    try {
      const res = await rfqApi.createUser(toCreateRequest(draft));
      setDraft(EMPTY);
      setNotice({ tone: "ok", text: `${res.data.user.username} 계정을 만들었습니다. 임시 비밀번호를 직접 전해 주세요.` });
      onCreated();
    } catch (err) {
      setNotice({ tone: "error", text: describeError(err) });
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <Panel title="계정 만들기" labelId="create-title" as="aside">
      <form onSubmit={submit} className="flex flex-col gap-3 p-4" data-testid="create-user">
        <TextField
          label="아이디"
          name="new-username"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          maxLength={32}
          value={draft.username}
          onChange={(e) => change({ username: e.target.value })}
          hint="영문 소문자, 숫자, -, _ 로 3~32자"
        />
        <RoleFields draft={draft} departments={departments} onChange={change} />
        <TextField
          label="임시 비밀번호"
          name="temporary-password"
          autoComplete="off"
          spellCheck={false}
          maxLength={128}
          value={draft.password}
          onChange={(e) => change({ password: e.target.value })}
          hint="8자 이상. 첫 로그인 때 반드시 바꿔야 합니다."
        />
        <FormMessage tone={notice?.tone ?? "ok"}>{notice?.text}</FormMessage>
        <Button type="submit" variant="primary" disabled={isBusy}>
          {isBusy ? "만드는 중…" : "계정 만들기"}
        </Button>
      </form>
    </Panel>
  );
}
