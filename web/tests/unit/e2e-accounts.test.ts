/** The e2e suite reads its accounts from a file named by the environment. Values are invented. */
import { describe, expect, it } from "vitest";
import { parseAccounts } from "../e2e-real/accounts";

const expected = { admin: "Pw-admin-1", quality: "Pw-quality-1" };

describe("parseAccounts", () => {
  it("reads JSON as a map or as a list", () => {
    expect(parseAccounts(JSON.stringify(expected))).toEqual(expected);
    const list = Object.entries(expected).map(([username, password]) => ({ username, password }));
    expect(parseAccounts(JSON.stringify(list))).toEqual(expected);
    expect(parseAccounts(JSON.stringify({ accounts: list }))).toEqual(expected);
  });

  it("reads a Markdown table by its headings, whatever the column order", () => {
    const md = [
      "# 계정",
      "",
      "| 부서 | 아이디 | 비밀번호 |",
      "|---|---|---|",
      "| 관리자 | `admin` | `Pw-admin-1` |",
      "| 품질 | quality | Pw-quality-1 |",
    ].join("\n");
    expect(parseAccounts(md)).toEqual(expected);
  });

  it("reads the memo of `autorfx users seed`, where a later section wins", () => {
    const head = "| 아이디 | 비밀번호 | 이름 | 역할 | 부서 | 금액 열람 |\n|---|---|---|---|---|---|\n";
    const memo = [
      `## AutoRFx 계정 (2026-11-25)\n\n- 화면 주소: http://localhost:3000\n\n${head}| admin | \`Pw-admin-1\` | 관리자 | admin | - | O |\n| quality | \`old-one\` | 품질 | member | 품질 | X |\n`,
      `## AutoRFx 초기화 (2026-11-26)\n\n${head}| quality | \`Pw-quality-1\` | 품질 | member | 품질 | X |\n`,
    ].join("\n");
    expect(parseAccounts(memo)).toEqual(expected);
  });

  it("reads a password with a pipe or a colon in a list", () => {
    expect(parseAccounts("- admin: a:b|c12345\n- quality: Pw-quality-1\n")).toEqual({
      admin: "a:b|c12345",
      quality: "Pw-quality-1",
    });
  });

  it("finds nothing in other text", () => {
    expect(parseAccounts("계정은 서버 관리자에게 물어보세요.")).toEqual({});
  });
});
