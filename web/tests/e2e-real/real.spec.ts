/**
 * Real-backend flow (SPEC US7, §13.2) with files the user supplies in
 * RFQ_E2E_FILES (paths separated by ":"). Nothing about any RFQ is written in
 * this file: every expectation comes at run time from the backend's answers
 * for the uploaded package. A check whose precondition the package does not
 * have (no cost clauses, no cross-file issue, ...) is skipped with a reason.
 *
 * Flow: upload → clauses for a cost-visible and a restricted team (no cost
 * text reaches the restricted team) → a citation opens its file and sentence
 * → consistency → chat → pre-cost decision as the backend computed it, and
 * 403 for a restricted team.
 */
import { existsSync } from "node:fs";
import path from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import type {
  ChatResponse,
  ClauseItem,
  ClausesResponse,
  ConsistencyResponse,
  DocumentResponse,
  MetaResponse,
  PrecostResponse,
  Team,
} from "../../src/lib/rfq/contract";

const API = process.env.RFQ_E2E_API ?? "http://127.0.0.1:8011";
const FILES = (process.env.RFQ_E2E_FILES ?? "")
  .split(":")
  .map((f) => f.trim())
  .filter(Boolean)
  .map((f) => path.resolve(f));
/** Generic questions; which of them the package answers is decided at run time. */
const QUESTIONS = ["인도조건", "납기", "포장", "보증 기간", "결제 조건"];
const COST_QUESTION = "목표가는 얼마인가?";

let rfqId = "";
let visible: Team | undefined;
let restricted: Team | undefined;

async function apiJson<T>(request: APIRequestContext, p: string): Promise<T> {
  const res = await request.get(`${API}${p}`);
  expect(res.status(), p).toBe(200);
  return (await res.json()) as T;
}

async function clausesFor(request: APIRequestContext, team: string): Promise<ClauseItem[]> {
  return (await apiJson<ClausesResponse>(request, `/rfqs/${rfqId}/clauses?team=${team}`)).data.clauses;
}

const excerptTexts = (items: ClauseItem[]) => items.flatMap((c) => (c.kind === "clause" ? c.excerpt.map((e) => e.text) : []));

/**
 * Long distinctive sentences of the clauses a restricted team must not see,
 * taken from the cost-visible team's own response. Sentences that also stand
 * in a clause the restricted team may see are not secrets and are left out.
 */
async function leakMarkers(request: APIRequestContext): Promise<string[]> {
  const seen = await clausesFor(request, restricted!.id);
  const hidden = new Set(seen.filter((c) => c.kind === "masked").map((c) => c.clause_id));
  const allowed = excerptTexts(seen);
  return excerptTexts((await clausesFor(request, visible!.id)).filter((c) => hidden.has(c.clause_id))).filter(
    (t) => t.length >= 24 && !allowed.some((a) => a.includes(t)),
  );
}

const go = (page: Page, p: string, team: string) => page.goto(`/rfq/${encodeURIComponent(rfqId)}/${p}?team=${team}`);

test.describe.serial("real backend with user-supplied files", () => {
  test.skip(FILES.length === 0, "RFQ_E2E_FILES가 없어 건너뜁니다. 테스트할 파일 경로를 ':'로 이어 주세요.");

  test.beforeAll(async ({ request }) => {
    for (const f of FILES) {
      if (!existsSync(f)) throw new Error(`RFQ_E2E_FILES의 파일이 없습니다: ${f}`);
      if (f.split(path.sep).some((part) => part.toLowerCase().startsWith("eval"))) {
        throw new Error("평가용 세트(eval*)는 테스트에 쓰지 않습니다.");
      }
    }
    const meta = await apiJson<MetaResponse>(request, "/meta");
    visible = meta.data.teams.find((t) => t.group === "cost_visible");
    restricted = meta.data.teams.find((t) => t.group === "cost_restricted");
    expect(visible, "a cost-visible team in data/rules").toBeTruthy();
    expect(restricted, "a cost-restricted team in data/rules").toBeTruthy();
  });

  test("upload and analyse the package", async ({ page }) => {
    await page.goto("/");
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

  test("clauses: cost-visible team sees everything; restricted team gets masked rows and no cost text", async ({
    page,
    request,
  }) => {
    await go(page, "clauses", visible!.id);
    await expect(page.getByTestId("clause-row").first()).toBeVisible();
    await expect(page.getByTestId("masked-row")).toHaveCount(0);
    await expect(page.getByTestId("engine-badge")).toBeVisible();

    const masked = (await clausesFor(request, restricted!.id)).filter((c) => c.kind === "masked").length;
    test.skip(masked === 0, "이 패키지에는 금액 조항이 없어 가림 검사를 건너뜁니다.");
    const markers = await leakMarkers(request);

    await go(page, "clauses", restricted!.id);
    await expect(page.getByTestId("masked-row")).toHaveCount(masked);
    await expect(page.getByTestId("masked-pdf-note")).toBeVisible();
    const text = await page.locator("main").innerText();
    for (const m of markers) expect(text, "restricted screen leaks cost text").not.toContain(m);

    for (const p of ["clauses", "rtm", "consistency"]) {
      const body = await (await request.get(`${API}/rfqs/${rfqId}/${p}?team=${restricted!.id}`)).text();
      for (const m of markers) expect(body, `${p} leaks cost text`).not.toContain(m);
    }
  });

  test("company departments: a new department is restricted and filters by its areas (US10)", async ({
    page,
    request,
  }) => {
    const label = "e2e 설비팀";
    await page.goto("/settings/departments");
    await page.getByTestId("org-add").click();
    await page.getByTestId("org-label-0").fill(label);
    await page.getByTestId("org-area-0-production_engineering").check({ force: true });
    await page.getByTestId("org-save").click();
    await expect(page.getByTestId("org-ok")).toBeVisible();

    try {
      const meta = await apiJson<MetaResponse>(request, "/meta");
      const mine = meta.data.teams.find((t) => t.label === label);
      expect(mine?.group, "cost visibility is off by default").toBe("cost_restricted");
      const masked = (await clausesFor(request, mine!.id)).filter((c) => c.kind === "masked").length;

      await go(page, "clauses", mine!.id);
      await expect(page.getByTestId("team-select")).toHaveValue(mine!.id);
      await expect(page.getByTestId("masked-row")).toHaveCount(masked);
      await page.getByRole("group", { name: "부서 필터" }).getByRole("button", { name: label }).click();
      await expect(page.getByTestId("role-filter")).toBeVisible();
      await expect(page.getByRole("link", { name: "사전원가" })).toHaveCount(0);
    } finally {
      await page.goto("/settings/departments");
      await page.getByTestId("org-reset").click();
      await expect(page.getByTestId("org-empty")).toBeVisible();
    }
  });

  test("a citation opens the right file, page and sentence", async ({ page, request }) => {
    const doc = await apiJson<DocumentResponse>(request, `/rfqs/${rfqId}`);
    const cited = (await clausesFor(request, restricted!.id)).filter(
      (c) => c.kind === "clause" && c.summary.finding.citations.length > 0,
    );
    test.skip(cited.length === 0, "인용이 붙은 조항이 없습니다.");
    // Prefer a clause outside the first file so the viewer must switch files.
    const first = doc.data.files[0]?.id;
    const target = cited.find((c) => c.source_doc !== first) ?? cited[0];

    await go(page, "clauses", restricted!.id);
    await page.locator(`[data-clause-id="${target.clause_id}"]`).click();
    await expect(page.getByTestId("file-select")).toHaveValue(target.source_doc);
    await expect(page.getByTestId("source-mark").first()).toBeVisible();
    await expect(page.getByTestId("clause-origin").first()).toBeVisible();
  });

  test("consistency: counts match the backend; a cross-file issue opens its second file", async ({ page, request }) => {
    const res = await apiJson<ConsistencyResponse>(request, `/rfqs/${rfqId}/consistency?team=${restricted!.id}`);
    await go(page, "consistency", restricted!.id);
    await expect(page.getByTestId("checked-summary")).toContainText(`파일 ${res.data.checked.files}개`);
    await expect(page.getByTestId("issue-masked")).toHaveCount(res.data.issues.filter((i) => i.kind === "masked").length);

    const cross = res.data.issues.find(
      (i) => i.kind === "issue" && new Set(i.evidence.map((e) => e.locus.source_doc)).size > 1,
    );
    test.skip(cross?.kind !== "issue", "파일 사이에 걸친 정합성 이슈가 없습니다.");
    if (cross?.kind !== "issue") return;
    await page.locator(`[data-issue-id="${cross.id}"]`).click();
    const second = cross.evidence.find((e) => e.locus.source_doc !== cross.evidence[0].locus.source_doc)!;
    await page.getByTestId("evidence-card").nth(cross.evidence.indexOf(second)).getByRole("button", { name: /원문 열기/ }).click();
    await expect(page.getByTestId("file-select")).toHaveValue(second.locus.source_doc);
    await expect(page.getByTestId("source-mark").first()).toBeVisible();
  });

  test("chat: the screen shows what the backend answered, for both teams", async ({ page, request }) => {
    const ask = async (team: string, question: string) =>
      (await (await request.post(`${API}/rfqs/${rfqId}/chat`, { data: { team, question } })).json()) as ChatResponse;

    let question: string | undefined;
    for (const q of QUESTIONS) {
      if ((await ask(visible!.id, q)).data.status === "answered") {
        question = q;
        break;
      }
    }
    test.skip(!question, "준비한 일반 질문 중 이 패키지가 답하는 것이 없습니다.");

    await go(page, "chat", visible!.id);
    await page.getByTestId("chat-input").fill(question!);
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("chat-answered")).toBeVisible();
    await expect(page.getByTestId("source-mark").first()).toBeVisible();

    const expected = (await ask(restricted!.id, COST_QUESTION)).data.status;
    await go(page, "chat", restricted!.id);
    await page.getByTestId("chat-input").fill(COST_QUESTION);
    await page.keyboard.press("Enter");
    await expect(page.getByTestId(`chat-${expected}`)).toBeVisible();
  });

  test("pre-cost: the screen shows the backend's result; 403 for a restricted team", async ({ page, request }) => {
    await go(page, "precost", visible!.id);
    await expect(page.getByTestId("rfq-type")).toBeVisible();
    // Inputs chosen by the test, not taken from any RFQ.
    await page.getByTestId("input-currency").fill("EUR");
    await page.getByTestId("input-target_price").fill("10");
    const items = page.getByTestId("cost-item");
    const first = items.first();
    await first.getByRole("combobox").first().selectOption("");
    await first.getByLabel("1행 이름").fill("테스트 재료");
    await first.getByLabel("1행 수량").fill("1");
    await first.getByLabel("1행 단가").fill("2");
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
    const assertShown = async (d: PrecostResponse["data"]) => {
      await expect(page.getByTestId("min-quote")).toContainText(d.cost.min_quote);
      await expect(page.getByTestId("decision")).toHaveAttribute("data-status", d.decision.status);
      await expect(page.getByTestId("decision-rule")).toContainText(d.decision.rule);
    };
    await assertShown(await decide());

    // Resolve every conflict with its first stated value, then decide again.
    const pickers = page.getByTestId("conflict-pickers").getByRole("radiogroup");
    const count = await pickers.count();
    if (count > 0) {
      for (let i = 0; i < count; i++) await pickers.nth(i).getByRole("radio").nth(1).check();
      await assertShown(await decide());
    }

    expect((await request.get(`${API}/rfqs/${rfqId}/precost-setup?team=${restricted!.id}`)).status()).toBe(403);
    expect((await request.get(`${API}/company?team=${restricted!.id}`)).status()).toBe(403);
    await go(page, "precost", restricted!.id);
    await expect(page.getByText("이 팀에는 원가 정보가 제공되지 않습니다")).toBeVisible();
    await expect(page.getByRole("link", { name: "사전원가" })).toHaveCount(0);
  });
});
