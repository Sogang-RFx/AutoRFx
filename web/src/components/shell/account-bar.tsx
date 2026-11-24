"use client";
/** Links this account may use, and the account menu. The screen hides, the server refuses. */
import Link from "next/link";
import { isCostVisible } from "@/lib/rfq/scope";
import { useViewer } from "@/lib/rfq/session";
import { IconGear, IconKey } from "../ui/icons";
import { AccountMenu } from "./account-menu";

const linkClass =
  "inline-flex min-h-8 items-center gap-1 rounded-control border border-line-control px-2 text-label font-medium text-ink transition-soft hover:bg-canvas";

export function AccountBar() {
  const viewer = useViewer();
  return (
    <>
      {isCostVisible(viewer) && (
        <Link
          href="/settings/company"
          data-testid="company-settings-link"
          aria-label="사전원가 기본 설정"
          title="사전원가 기본 설정 (단가표 · 회사 프로필 · 비율)"
          className={linkClass}
        >
          <IconGear size={14} />
          기본 설정
        </Link>
      )}
      {viewer.can_manage && (
        <Link href="/admin" data-testid="admin-link" className={linkClass}>
          <IconKey size={14} />
          계정·권한
        </Link>
      )}
      <AccountMenu />
    </>
  );
}
