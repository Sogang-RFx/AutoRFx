"use client";
/**
 * The whole app stands behind this gate (docs/auth-rbac.md §6). Nothing is drawn until the backend
 * says who is signed in. The login and password screens replace the content in place, so the
 * address stays what the user asked for and opens after the login.
 *
 * Screen state belongs to one account with one scope: another account, or another department of the
 * same account, remounts everything below (chat, filters, unsaved forms, loaded data).
 */
import { Fragment } from "react";
import type { ReactNode } from "react";
import { scopeKey } from "@/lib/rfq/auth-store";
import { useSession } from "@/lib/rfq/session";
import { LoginScreen } from "./login-screen";
import { ForcedPasswordScreen } from "./password-screens";
import { UnreachableScreen } from "./unreachable-screen";

export function AuthGate({ children }: { children?: ReactNode }) {
  const { auth, retry } = useSession();
  switch (auth.status) {
    case "loading":
      return null;
    case "unreachable":
      return <UnreachableScreen error={auth.error} onRetry={retry} />;
    case "anonymous":
      return <LoginScreen reason={auth.reason} />;
    case "ready":
      if (auth.viewer.must_change_password) return <ForcedPasswordScreen key={auth.viewer.user_id} viewer={auth.viewer} />;
      return <Fragment key={scopeKey(auth.viewer)}>{children}</Fragment>;
  }
}
