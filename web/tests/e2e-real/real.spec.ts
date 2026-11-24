/**
 * Real-backend flow (SPEC US7, §13.2, docs/auth-rbac.md) with files in RFQ_E2E_FILES (paths separated
 * by ":") and accounts in RFQ_E2E_ACCOUNTS. Nothing about any RFQ or account is written in this file:
 * every expectation comes at run time from the backend's answers. A check whose precondition the
 * package or the accounts do not have is skipped with a reason.
 *
 * Flow: the gate stands in front of everything → wrong password → upload → each role sees its own scope
 * and no text outside it → a citation opens its file and sentence → consistency → chat → pre-cost as the
 * backend computed it, 403 otherwise → logout reaches the other tab → accounts screen.
 */
import { expect, test } from "@playwright/test";
import type {
  ChatResponse,
  ConsistencyResponse,
  DocumentResponse,
  PrecostResponse,
  PrecostSetupResponse,
} from "../../src/lib/rfq/contract";
import {
  ACCOUNTS,
  API,
  FILES,
  apiJson,
  castAccounts,
  checkFiles,
  clausesOf,
  fillLogin,
  leakMarkers,
  newApi,
  openAs,
  signOut,
  type Actor,
  type Cast,
} from "./helpers";

/** Generic questions; which of them the package answers is decided at run time. */
const QUESTIONS = ["인도조건", "납기", "포장", "보증 기간", "결제 조건"];
const COST_QUESTION = "목표가는 얼마인가?";

let cast: Cast;
let rfqId = "";
const at = (p: string) => `/rfq/${encodeURIComponent(rfqId)}/${p}`;

test.describe.serial("real backend with user-supplied files and accounts", () => {
  test.skip(FILES.length === 0, "RFQ_E2E_FILES가 없어 건너뜁니다. 테스트할 파일 경로를 ':'로 이어 주세요.");
  test.skip(
    Object.keys(ACCOUNTS).length === 0,
    "RFQ_E2E_ACCOUNTS에서 계정을 읽지 못해 건너뜁니다. ACCOUNTS.local.md나 JSON 파일의 경로를 주세요.",
  );

  test.beforeAll(async () => {
    checkFiles();
    cast = await castAccounts();
  });

  test("the gate comes first: no request but the session check, and no data", async ({ page }) => {
    const paths: string[] = [];
    page.on("request", (r) => {
      if (r.url().startsWith(API)) paths.push(new URL(r.url()).pathname);
    });
    await page.goto("/");
    await expect(page.getByTestId("login-form")).toBeVisible();
    await expect(page.getByTestId("rfq-list")).toHaveCount(0);
    await expect(page.getByLabel("아이디")).toBeFocused();
    expect([...new Set(paths)]).toEqual(["/auth/me"]);

    const api = await newApi();
    for (const p of ["/meta", "/rfqs", "/admin/users"]) expect((await api.get(`${API}${p}`)).status(), p).toBe(401);
    expect((await api.get(`${API}/rfqs?team=sales`)).status()).toBe(401);
  });

  test("a wrong password and an unknown id read the same", async ({ page }) => {
    await page.goto("/");
    const told: string[] = [];
    for (const username of [cast.restricted.username, "nobody-here"]) {
      await fillLogin(page, username, "not-the-password");
      await expect(page.getByTestId("form-error")).toContainText("맞지 않습니다");
      told.push(await page.getByTestId("form-error").innerText());
      await expect(page.getByLabel("비밀번호")).toBeFocused();
    }
    expect(told[0]).toBe(told[1]);
    await fillLogin(page, cast.restricted.username, cast.restricted.password);
    await expect(page.getByTestId("account-menu")).toContainText(cast.restricted.viewer.display_name);
  });

  test("upload and analyse the package as an account that may upload", async ({ page }) => {
    await openAs(page, cast.restricted, "/");
    await expect(page.getByTestId("no-upload")).toBeVisible();
    await expect(page.getByTestId("file-input")).toHaveCount(0);

    await openAs(page, cast.full ?? cast.admin, "/");
    await page.getByTestId("file-input").setInputFiles(FILES);
    await expect(page.getByTestId("file-name")).toHaveCount(FILES.length);
    await page.getByTestId("start-upload").click();
    await page.waitForURL(/\/rfq\/[^/]+\/processing/);
    rfqId = decodeURIComponent(new URL(page.url()).pathname.split("/")[2]);

    await expect(page.getByTestId("go-clauses")).toBeVisible({ timeout: 200_000 });
    await expect(page.locator("[data-testid=stages] li").first()).toBeVisible();
    // Every stage finished; a skipped one (e.g. translation without a provider) shows its reason, not a failure.
    await expect(page.locator("[data-testid=stages] li[data-status=error]")).toHaveCount(0);
    const skipped = page.locator("[data-testid=stages] li[data-status=skipped]");
    if ((await skipped.count()) > 0) await expect(skipped.first()).toContainText("건너뜀");
    await expect(page.getByTestId("package-files").locator("li")).toHaveCount(FILES.length);
  });

  test("each role sees its scope and no text outside it", async ({ page }) => {
    await openAs(page, cast.admin, at("clauses"));
    await expect(page.getByTestId("clause-row").first()).toBeVisible();
    await expect(page.getByTestId("masked-row")).toHaveCount(0);
    await expect(page.getByTestId("engine-badge")).toBeVisible();
    await expect(page.getByTestId("masked-pdf-note")).toHaveCount(0);

    const others = [cast.full, cast.costMember, cast.restricted].filter((a): a is Actor => a !== undefined);
    for (const actor of others) {
      const items = await clausesOf(actor, rfqId);
      const masked = items.filter((c) => c.kind === "masked").length;
      const markers = await leakMarkers(cast, actor, rfqId);
      await openAs(page, actor, at("clauses"));
      await expect(page.getByTestId("scope-line")).toContainText(`조항 ${items.length - masked}`);
      await expect(page.getByTestId("masked-row")).toHaveCount(masked);
      await expect(page.getByTestId("masked-pdf-note")).toHaveCount(
        actor.viewer.full_scope && actor.viewer.group === "cost_visible" ? 0 : 1,
      );
      const text = await page.locator("main").innerText();
      for (const m of markers) expect(text, `${actor.username}: screen`).not.toContain(m);
      for (const p of ["clauses", "rtm", "consistency"]) {
        const body = await (await actor.api.get(`${API}/rfqs/${rfqId}/${p}?team=sales`)).text();
        for (const m of markers) expect(body, `${actor.username}: ${p}`).not.toContain(m);
      }
    }
  });

  test("the department control only changes the view, and lock rows stay", async ({ page }) => {
    await openAs(page, cast.restricted, at("clauses"));
    const masked = (await clausesOf(cast.restricted, rfqId)).filter((c) => c.kind === "masked").length;
    const filter = page.getByRole("group", { name: "보기: 부서" });
    test.skip((await filter.count()) === 0, "이 계정이 볼 수 있는 조항이 없습니다.");
    await filter.getByRole("button").nth(1).click();
    await expect(page.getByTestId("role-filter")).toBeVisible();
    await expect(page.getByTestId("masked-row")).toHaveCount(masked);
  });

  test("the Excel file downloads with the session", async ({ page }) => {
    await openAs(page, cast.restricted, at("rtm"));
    const [file] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-rtm-xlsx").click()]);
    expect(file.suggestedFilename()).toMatch(/^AutoRFx_RTM_.*\.xlsx$/);
  });

  test("a citation opens the right file, page and sentence", async ({ page }) => {
    const doc = await apiJson<DocumentResponse>(cast.restricted, `/rfqs/${rfqId}`);
    const cited = (await clausesOf(cast.restricted, rfqId)).filter(
      (c) => c.kind === "clause" && c.summary.finding.citations.length > 0,
    );
    test.skip(cited.length === 0, "인용이 붙은 조항이 없습니다.");
    // Prefer a clause outside the first file so the viewer must switch files.
    const first = doc.data.files[0]?.id;
    const target = cited.find((c) => c.source_doc !== first) ?? cited[0];

    await openAs(page, cast.restricted, at("clauses"));
    await page.locator(`[data-clause-id="${target.clause_id}"]`).click();
    if (doc.data.files.length > 1) await expect(page.getByTestId("file-select")).toHaveValue(target.source_doc);
    await expect(page.getByTestId("source-mark").first()).toBeVisible();
    await expect(page.getByTestId("clause-origin").first()).toBeVisible();
  });

  test("consistency: counts match the backend; a cross-file issue opens its second file", async ({ page }) => {
    const actor = cast.restricted;
    const mine = await apiJson<ConsistencyResponse>(actor, `/rfqs/${rfqId}/consistency`);
    await openAs(page, actor, at("consistency"));
    await expect(page.getByTestId("checked-summary")).toContainText(`파일 ${mine.data.checked.files}개`);
    await expect(page.getByTestId("issue-masked")).toHaveCount(mine.data.issues.filter((i) => i.kind === "masked").length);

    const all = await apiJson<ConsistencyResponse>(cast.admin, `/rfqs/${rfqId}/consistency`);
    const cross = all.data.issues.find(
      (i) => i.kind === "issue" && new Set(i.evidence.map((e) => e.locus.source_doc)).size > 1,
    );
    test.skip(cross?.kind !== "issue", "파일 사이에 걸친 정합성 이슈가 없습니다.");
    if (cross?.kind !== "issue") return;
    await openAs(page, cast.admin, at("consistency"));
    await page.locator(`[data-issue-id="${cross.id}"]`).click();
    const second = cross.evidence.find((e) => e.locus.source_doc !== cross.evidence[0].locus.source_doc)!;
    await page.getByTestId("evidence-card").nth(cross.evidence.indexOf(second)).getByRole("button", { name: /원문 열기/ }).click();
    await expect(page.getByTestId("file-select")).toHaveValue(second.locus.source_doc);
    await expect(page.getByTestId("source-mark").first()).toBeVisible();
  });

  test("chat: the screen shows what the backend answered, and another account starts empty", async ({ page }) => {
    const ask = async (actor: Actor, question: string) =>
      (await (await actor.api.post(`${API}/rfqs/${rfqId}/chat`, { data: { question } })).json()) as ChatResponse;

    let question: string | undefined;
    for (const q of QUESTIONS) {
      if ((await ask(cast.admin, q)).data.status === "answered") {
        question = q;
        break;
      }
    }
    test.skip(!question, "준비한 일반 질문 중 이 패키지가 답하는 것이 없습니다.");

    await openAs(page, cast.admin, at("chat"));
    await page.getByTestId("chat-input").fill(question!);
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("chat-answered")).toBeVisible();
    await expect(page.getByTestId("source-mark").first()).toBeVisible();

    const expected = (await ask(cast.restricted, COST_QUESTION)).data.status;
    await openAs(page, cast.restricted, at("chat"));
    await expect(page.getByTestId("chat-answered")).toHaveCount(0);
    await page.getByTestId("chat-input").fill(COST_QUESTION);
    await page.keyboard.press("Enter");
    await expect(page.getByTestId(`chat-${expected}`)).toBeVisible();
  });

  test("pre-cost: the screen shows the backend's result; 403 for an account without cost", async ({ page }) => {
    await openAs(page, cast.admin, at("precost"));
    await expect(page.getByTestId("rfq-type")).toBeVisible();
    await expect(page.getByTestId("precost-scope-note")).toContainText("RFQ 전체");
    // Inputs chosen by the test, not taken from any RFQ.
    // A value the RFQ states sits in the summary until "수정" opens it; a missing one is an input already.
    const open = async (id: string, input: string) => {
      if (!(await page.getByTestId(input).isVisible())) await page.getByTestId(`edit-${id}`).click();
      return page.getByTestId(input);
    };
    await (await open("currency", "input-currency")).selectOption("EUR");
    await (await open("target_price", "input-target_price")).fill("10");
    const items = page.getByTestId("cost-item");
    const first = items.first();
    await first.getByRole("combobox").first().selectOption("");
    await first.getByLabel("1행 이름").fill("테스트 재료");
    await first.getByLabel("1행 수량").fill("1");
    await first.getByLabel("1행 단가", { exact: true }).fill("2");
    for (const q of await page.getByTestId("knockout-questions").getByRole("radiogroup").all()) {
      await q.getByRole("radio", { name: "예" }).click();
    }

    const decide = async () => {
      const [res] = await Promise.all([
        page.waitForResponse((r) => r.url().endsWith("/precost") && r.request().method() === "POST"),
        page.getByTestId("calc").click(),
      ]);
      expect(res.status()).toBe(200);
      return ((await res.json()) as PrecostResponse).data;
    };
    const setup = await apiJson<PrecostSetupResponse>(cast.admin, `/rfqs/${rfqId}/precost-setup`);
    const note = setup.data.assumption_note;
    expect(note).not.toBe("");
    const assertShown = async (d: PrecostResponse["data"]) => {
      // The tile drops trailing zeros (1.2100 → 1.21); S1 quotes are below 1 000, so no grouping commas.
      await expect(page.getByTestId("min-quote")).toContainText(String(Number(d.cost?.min_quote)));
      await expect(page.getByTestId("decision")).toHaveAttribute("data-status", d.decision.status);
      await expect(page.getByTestId("decision-rule")).toContainText(d.decision.rule);
      await expect(page.getByTestId("assumption-note")).toHaveText(note);
    };
    await assertShown(await decide());

    // Resolve every conflict with its first stated value, then decide again.
    const pickers = page.getByTestId("conflict-pickers").getByRole("radiogroup");
    const count = await pickers.count();
    if (count > 0) {
      for (let i = 0; i < count; i++) await pickers.nth(i).getByRole("radio").nth(1).check();
      await assertShown(await decide());
    }

    for (const p of [`/rfqs/${rfqId}/precost-setup`, "/company"]) {
      expect((await cast.restricted.api.get(`${API}${p}`)).status(), p).toBe(403);
      expect((await cast.restricted.api.get(`${API}${p}?team=sales`)).status(), p).toBe(403);
    }
    await openAs(page, cast.restricted, at("precost"));
    await expect(page.getByTestId("forbidden")).toBeVisible();
    await expect(page.getByRole("link", { name: "사전원가" })).toHaveCount(0);
  });

  test("company settings: cost-visible accounts read, only an administrator saves", async ({ page }) => {
    test.skip(!cast.costMember, "금액을 보는 부서 계정이 없습니다.");
    await openAs(page, cast.costMember!, "/settings/company");
    await expect(page.getByTestId("company-read-only")).toBeVisible();
    await expect(page.getByTestId("company-save")).toHaveCount(0);
    await openAs(page, cast.admin, "/settings/company");
    await expect(page.getByTestId("company-save")).toBeVisible();
  });

  test("logout in tab A clears tab B", async ({ page, context }) => {
    await openAs(page, cast.admin, at("clauses"));
    const other = await context.newPage();
    await other.goto(at("rtm"));
    await expect(other.getByTestId("rtm-table")).toBeVisible();
    await signOut(page);
    await expect(other.getByTestId("login-form")).toBeVisible();
    await expect(other.getByTestId("rtm-table")).toHaveCount(0);
    await other.close();
  });

  test("accounts: an administrator creates, changes and deletes one; others are refused", async ({ page, browser }) => {
    const username = `e2e-${Date.now().toString(36)}`;
    const temporary = `temp-${Date.now()}`;
    await openAs(page, cast.restricted, "/admin");
    await expect(page.getByTestId("forbidden")).toBeVisible();
    await expect(page.getByTestId("admin-link")).toHaveCount(0);

    await openAs(page, cast.admin, "/admin");
    await expect(page.getByTestId("users-table")).toContainText(cast.admin.username);
    const form = page.getByTestId("create-user");
    await form.getByLabel("아이디").fill(username);
    await form.getByLabel("이름").fill("e2e 임시");
    await form.getByLabel("부서").selectOption(cast.restricted.viewer.department!);
    await form.getByLabel("임시 비밀번호").fill(temporary);
    await form.getByRole("button", { name: "계정 만들기" }).click();
    const row = page.locator(`[data-testid=user-row][data-username="${username}"]`);
    await expect(row).toContainText("임시 비밀번호");

    try {
      // The new account has to change its password before anything else.
      const context = await browser.newContext();
      const fresh = await context.newPage();
      await fresh.goto("/");
      await fillLogin(fresh, username, temporary);
      await expect(fresh.getByTestId("password-form")).toBeVisible();
      await expect(fresh.getByTestId("rfq-list")).toHaveCount(0);
      await fresh.getByLabel("현재 비밀번호").fill(temporary);
      await fresh.getByLabel("새 비밀번호", { exact: true }).fill(`${temporary}-new`);
      await fresh.getByLabel("새 비밀번호 확인").fill(`${temporary}-new`);
      await fresh.getByRole("button", { name: "비밀번호 바꾸기" }).click();
      await expect(fresh.getByTestId("account-menu")).toBeVisible();

      await page.reload();
      await row.getByRole("button", { name: /계정 관리/ }).click();
      await page.getByTestId("account-panel").getByRole("button", { name: "사용 중지" }).click();
      await expect(row).toContainText("사용 중지");
      await fresh.reload();
      await expect(fresh.getByTestId("login-form")).toBeVisible();
      await context.close();
    } finally {
      const panel = page.getByTestId("account-panel");
      if (!(await panel.isVisible())) await row.getByRole("button", { name: /계정 관리/ }).click();
      await panel.getByRole("button", { name: "삭제" }).click();
      await page.getByTestId("delete-confirm").getByRole("button", { name: "삭제" }).click();
      await expect(row).toHaveCount(0);
    }
    await expect(page.getByTestId("audit-table")).toContainText(username);
  });
});
