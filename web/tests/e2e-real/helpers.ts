/** Sign-in and API access of the e2e suite. Every account comes from RFQ_E2E_ACCOUNTS. */
import { existsSync } from "node:fs";
import path from "node:path";
import { expect, request as playwrightRequest, type APIRequestContext, type Page } from "@playwright/test";
import type { ClauseItem, ClausesResponse, Viewer, ViewerResponse } from "../../src/lib/rfq/contract";
import { loadAccounts } from "./accounts";

export const API = process.env.RFQ_E2E_API ?? "http://localhost:8011";
/** The backend refuses a change whose Origin is not the web app's. */
const ORIGIN = "http://localhost:3200";

export const FILES = (process.env.RFQ_E2E_FILES ?? "")
  .split(":")
  .map((f) => f.trim())
  .filter(Boolean)
  .map((f) => path.resolve(f));

export const ACCOUNTS = loadAccounts(process.env.RFQ_E2E_ACCOUNTS);

export type Actor = { username: string; password: string; viewer: Viewer; api: APIRequestContext };
export type Cast = {
  admin: Actor;
  /** Sees every clause without being an administrator. */
  full?: Actor;
  /** Cost visible, but only its own department's clauses. */
  costMember?: Actor;
  /** Neither the full scope nor cost. */
  restricted: Actor;
};

export function checkFiles(): void {
  for (const f of FILES) {
    if (!existsSync(f)) throw new Error(`RFQ_E2E_FILES의 파일이 없습니다: ${f}`);
    if (f.split(path.sep).some((part) => part.toLowerCase().startsWith("eval"))) {
      throw new Error("평가용 세트(eval*)는 테스트에 쓰지 않습니다.");
    }
  }
}

export const newApi = () => playwrightRequest.newContext({ extraHTTPHeaders: { Origin: ORIGIN } });

async function signInByApi(username: string, password: string): Promise<Actor> {
  const api = await newApi();
  const res = await api.post(`${API}/auth/login`, { data: { username, password } });
  expect(res.status(), `login of ${username}`).toBe(200);
  const { viewer } = ((await res.json()) as ViewerResponse).data;
  return { username, password, viewer, api };
}

/** Who plays which part is read from what the backend says about each account. */
export async function castAccounts(): Promise<Cast> {
  const actors: Actor[] = [];
  for (const [username, password] of Object.entries(ACCOUNTS)) actors.push(await signInByApi(username, password));
  const usable = actors.filter((a) => !a.viewer.must_change_password);
  const isMember = (a: Actor) => !a.viewer.can_manage;
  const admin = usable.find((a) => a.viewer.can_manage);
  const restricted = usable.find((a) => isMember(a) && !a.viewer.full_scope && a.viewer.group === "cost_restricted");
  if (!admin || !restricted) {
    throw new Error("RFQ_E2E_ACCOUNTS에 관리자 계정 하나와 금액을 볼 수 없는 부서 계정 하나가 있어야 합니다.");
  }
  return {
    admin,
    restricted,
    full: usable.find((a) => isMember(a) && a.viewer.full_scope),
    costMember: usable.find((a) => isMember(a) && !a.viewer.full_scope && a.viewer.group === "cost_visible"),
  };
}

export async function apiJson<T>(actor: Actor, p: string): Promise<T> {
  const res = await actor.api.get(`${API}${p}`);
  expect(res.status(), `${actor.username} ${p}`).toBe(200);
  return (await res.json()) as T;
}

export const clausesOf = async (actor: Actor, rfqId: string): Promise<ClauseItem[]> =>
  (await apiJson<ClausesResponse>(actor, `/rfqs/${encodeURIComponent(rfqId)}/clauses`)).data.clauses;

const excerptTexts = (items: ClauseItem[]) =>
  items.flatMap((c) => (c.kind === "clause" ? c.excerpt.map((e) => e.text) : []));

/**
 * Long distinctive sentences of the clauses an account must not see, taken from the administrator's
 * own response. Sentences that also stand in a clause the account may see are not secrets.
 */
export async function leakMarkers(cast: Cast, actor: Actor, rfqId: string): Promise<string[]> {
  const seen = await clausesOf(actor, rfqId);
  const hidden = new Set(seen.filter((c) => c.kind === "masked").map((c) => c.clause_id));
  const allowed = excerptTexts(seen);
  const all = await clausesOf(cast.admin, rfqId);
  return excerptTexts(all.filter((c) => hidden.has(c.clause_id))).filter(
    (t) => t.length >= 24 && !allowed.some((a) => a.includes(t)),
  );
}

export async function fillLogin(page: Page, username: string, password: string): Promise<void> {
  await page.getByLabel("아이디").fill(username);
  await page.getByLabel("비밀번호").fill(password);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
}

/** Opens a path as an account: the login screen stands in front of it and the address is kept. */
export async function openAs(page: Page, actor: Actor, p: string): Promise<void> {
  await page.context().clearCookies();
  await page.goto(p);
  await expect(page.getByTestId("login-form")).toBeVisible();
  await fillLogin(page, actor.username, actor.password);
  await expect(page.getByTestId("account-menu")).toBeVisible();
  expect(new URL(page.url()).pathname).toBe(new URL(p, page.url()).pathname);
}

export async function signOut(page: Page): Promise<void> {
  await page.getByTestId("account-menu").click();
  await page.getByTestId("logout").click();
  await expect(page.getByTestId("login-form")).toBeVisible();
}
