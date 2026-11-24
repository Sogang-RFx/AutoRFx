"use client";
/**
 * The signed-in account (docs/auth-rbac.md §6): name and department on the button; scope, cost
 * visibility, password change and logout inside. Escape and a click outside close it, and the focus
 * goes back to the button.
 */
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { describeError } from "@/lib/rfq/provider";
import { isCostVisible, scopeName } from "@/lib/rfq/scope";
import { useSession, useViewer } from "@/lib/rfq/session";
import { PasswordDialog } from "../auth/password-screens";
import { IconUser } from "../ui/icons";
import { Button, FormMessage } from "../ui/primitives";

export function AccountMenu() {
  const router = useRouter();
  const { logout } = useSession();
  const viewer = useViewer();
  const [isOpen, setIsOpen] = useState(false);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const role = viewer.role === "admin" ? "관리자" : null;
  const department = [role, viewer.department_label].filter(Boolean).join(" · ");

  useEffect(() => {
    if (!isOpen) return;
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setIsOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setIsOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [isOpen]);

  async function signOut() {
    setIsBusy(true);
    setNotice(null);
    try {
      await logout();
      // The next person at this screen starts at the list, not at this account's page.
      router.replace("/");
    } catch (e) {
      setNotice({ tone: "error", text: `로그아웃하지 못했습니다. ${describeError(e)}` });
      setIsBusy(false);
    }
  }

  function openDialog() {
    setIsOpen(false);
    setIsDialogOpen(true);
  }

  function closeDialog(changed: boolean) {
    setIsDialogOpen(false);
    if (changed) {
      setNotice({ tone: "ok", text: "비밀번호를 바꿨습니다." });
      setIsOpen(true);
    }
    buttonRef.current?.focus();
  }

  const facts = [
    { label: "아이디", value: viewer.username },
    { label: "부서", value: department || "–" },
    { label: "조항 범위", value: scopeName(viewer) },
    { label: "금액 조항", value: isCostVisible(viewer) ? "열람함" : "열람하지 않음" },
  ];

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        data-testid="account-menu"
        aria-expanded={isOpen}
        aria-controls="account-panel"
        onClick={() => {
          setIsOpen((v) => !v);
          setNotice(null);
        }}
        className="inline-flex min-h-8 max-w-56 items-center gap-1.5 rounded-control border border-line-control px-2 text-label font-medium text-ink transition-soft hover:bg-canvas"
      >
        <IconUser size={14} className="shrink-0" />
        <span className="truncate">{viewer.display_name}</span>
        {department && department !== viewer.display_name && <span className="shrink-0 text-ink-muted">{department}</span>}
      </button>
      {isOpen && (
        <section
          id="account-panel"
          aria-label="내 계정"
          className="absolute right-0 top-full z-40 mt-2 flex w-72 max-w-[calc(100vw-2rem)] flex-col gap-3 rounded-panel border border-line bg-surface p-4 shadow-float"
        >
          <p className="text-body font-semibold text-ink">{viewer.display_name}</p>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-dense" data-testid="account-facts">
            {facts.map((f) => (
              <div key={f.label} className="contents">
                <dt className="text-ink-muted">{f.label}</dt>
                <dd className="text-ink">{f.value}</dd>
              </div>
            ))}
          </dl>
          <div className="flex flex-wrap gap-2 border-t border-line pt-3">
            <Button onClick={openDialog}>비밀번호 변경</Button>
            <Button onClick={signOut} disabled={isBusy} data-testid="logout">
              {isBusy ? "로그아웃 중…" : "로그아웃"}
            </Button>
          </div>
          <FormMessage tone={notice?.tone ?? "ok"}>{notice?.text}</FormMessage>
        </section>
      )}
      {isDialogOpen && <PasswordDialog viewer={viewer} onClose={closeDialog} />}
    </div>
  );
}
