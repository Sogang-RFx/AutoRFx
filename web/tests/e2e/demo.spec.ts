/**
 * Demo flow (TASKS S020, SC-008, SC-010). Core flow first, then the full flow.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const ID = "sample-nv27-fdlm";
const CLAUSES = `/rfq/${ID}/clauses`;

async function pickTeam(page: Page, team: string) {
  await expect(page.locator(`[data-testid=team-select] option[value="${team}"]`)).toHaveCount(1);
  await page.selectOption("[data-testid=team-select]", team);
}

test.describe("core flow: upload → processing → clauses → citation → team switch", () => {
  test("runs end to end without transmitting the file", async ({ page }) => {
    const uploads: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().includes("/api/mock/rfqs")) uploads.push(req.postData() ?? "");
    });

    await page.goto("/?speed=20");
    await page.setInputFiles("[data-testid=file-input]", {
      name: "client-rfq.doc",
      mimeType: "application/msword",
      buffer: Buffer.from("SECRET-CONTENT-THAT-MUST-NOT-LEAVE"),
    });
    await expect(page.getByTestId("file-name")).toHaveText("client-rfq.doc");
    await page.getByTestId("start-upload").click();

    await page.waitForURL(/processing/);
    expect(uploads).toHaveLength(1);
    expect(JSON.parse(uploads[0])).toEqual({ file_name: "client-rfq.doc" });
    expect(uploads[0]).not.toContain("SECRET-CONTENT");

    await expect(page.getByTestId("go-clauses")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator("[data-testid=stages] li[data-status=done]")).toHaveCount(7);
    await page.getByTestId("go-clauses").click();

    await pickTeam(page, "sales");
    await page.locator("#clause-D\\.3").click();
    await expect(page.getByTestId("clause-detail").first()).toContainText("독소조항 사유");
    await expect(page.getByTestId("source-locator")).toHaveText(/\d+페이지 \d+번째 문장/);
    await expect(page.getByTestId("source-mark").first()).toBeVisible();

    // A cost-restricted team: cost clauses become fixed-height masked rows, the PDF is masked.
    await pickTeam(page, "quality");
    await page.getByRole("button", { name: "전체", exact: true }).click();
    await expect(page.getByTestId("masked-row")).toHaveCount(5);
    await expect(page.getByTestId("masked-pdf-note")).toBeVisible();
    expect(await page.locator("main").innerText()).not.toContain("14.80");
  });

  test("restricted API responses contain no cost-clause content", async ({ request }) => {
    for (const team of ["quality", "rnd", ""]) {
      for (const path of [`clauses?team=${team}`, `rtm?team=${team}`, `pages/2/sentences?team=${team}`]) {
        const body = await (await request.get(`/api/mock/rfqs/${ID}/${path}`)).text();
        for (const marker of ["14.80", "185,000", "42,000", "Annual Price Reduction"]) expect(body).not.toContain(marker);
      }
      expect((await request.get(`/api/mock/rfqs/${ID}/cost-inputs?team=${team}`)).status()).toBe(403);
    }
  });

  test("returns the same result twice", async ({ request }) => {
    const a = await (await request.get(`/api/mock/rfqs/${ID}/clauses?team=sales`)).text();
    const b = await (await request.get(`/api/mock/rfqs/${ID}/clauses?team=sales`)).text();
    expect(a).toBe(b);
  });

  test("keyboard only: move between clauses and open a citation", async ({ page }) => {
    await page.goto(`${CLAUSES}?team=quality`);
    const first = page.getByTestId("clause-row").first();
    await first.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("clause-detail").first()).toBeVisible();
    await page.keyboard.press("ArrowDown");
    await expect(page.locator("[data-testid=clause-row][aria-current=true]")).toHaveCount(1);
    const chip = page.getByRole("button", { name: /원문 열기/ }).first();
    await chip.focus();
    await page.keyboard.press("Enter");
    await expect(chip).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("source-mark").first()).toBeVisible();
  });
});

test.describe("full flow", () => {
  test("every mock part carries a mock marker", async ({ page }) => {
    for (const path of ["processing", "clauses", "rtm", "chat", "evaluation"]) {
      await page.goto(`/rfq/${ID}/${path}?team=sales`);
      await expect(page.getByTestId("mock-summary")).toBeVisible();
      await expect(page.getByTestId("mock-chip").first()).toBeVisible();
    }
  });

  test("unverified scenario marks failing citations", async ({ page }) => {
    await page.goto(`${CLAUSES}?team=quality&scenario=unverified`);
    await page.locator("#clause-C\\.2").click();
    await expect(page.getByTestId("unverified-note").first()).toBeVisible();
  });

  test("error scenario shows an error state, not data", async ({ page }) => {
    await page.goto(`${CLAUSES}?team=sales&scenario=error`);
    await expect(page.getByRole("alert").first()).toContainText("시연용 오류");
  });

  test("RTM export is marked as mock", async ({ page }) => {
    await page.goto(`/rfq/${ID}/rtm?team=sales&scenario=default`);
    await expect(page.getByTestId("rtm-table")).toBeVisible();
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-rtm").click()]);
    expect(download.suggestedFilename()).toMatch(/^MOCK_AutoRFx_RTM_\d{4}-\d{2}-\d{2}\.csv$/);
    const chunks = await (await download.createReadStream()).toArray();
    expect(Buffer.concat(chunks).toString("utf8")).toContain("목업 데이터");
  });

  test("pre-cost computes with decimals; restricted teams cannot open it", async ({ page }) => {
    await page.goto(`/rfq/${ID}/precost?team=costing&scenario=default`);
    await page.getByTestId("input-variable_cost_per_unit").fill("9.35");
    await page.getByTestId("calc").click();
    await expect(page.getByTestId("unit-cost")).toContainText("9.9175");
    await expect(page.getByTestId("precost-result")).toContainText("기준선 미정");

    await pickTeam(page, "quality");
    await expect(page.getByText("이 팀에는 원가 정보가 제공되지 않습니다")).toBeVisible();
    await expect(page.getByRole("link", { name: "사전원가" })).toHaveCount(0);
  });

  test("chatbot answers prepared questions with citations and refuses to invent", async ({ page }) => {
    await page.goto(`/rfq/${ID}/chat?team=sales`);
    await page.getByTestId("chat-input").fill("지체상금에 상한이 있나요?");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("chat-answered")).toContainText("상한이 없습니다");
    await expect(page.getByTestId("source-mark").first()).toBeVisible();
    await page.getByTestId("chat-input").fill("이 회사 주가는 얼마인가요?");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("chat-not_prepared")).toBeVisible();
  });

  test("evaluation shows an honest empty state", async ({ page }) => {
    await page.goto(`/rfq/${ID}/evaluation`);
    await expect(page.getByTestId("eval-empty")).toContainText("채점 전");
  });
});

test.describe("layout and accessibility", () => {
  for (const width of [320, 768, 1024, 1440]) {
    test(`no horizontal overflow and no serious a11y violations at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      for (const path of ["/", `${CLAUSES}?team=quality&scenario=default`, `/rfq/${ID}/rtm?team=sales`]) {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow, `${path} overflows`).toBeLessThanOrEqual(0);
        const axe = await new AxeBuilder({ page }).analyze();
        const serious = axe.violations.filter((v) => v.impact === "critical" || v.impact === "serious");
        expect(serious.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`), path).toEqual([]);
      }
      await page.goto(`${CLAUSES}?team=quality`);
      await page.getByTestId("clause-row").first().click();
      await expect(page.getByTestId("source-mark").first()).toBeVisible();
      await expect(page).toHaveScreenshot(`clauses-${width}.png`, {
        fullPage: width < 1024,
        mask: [page.locator("canvas")],
        animations: "disabled",
      });
    });
  }
});
