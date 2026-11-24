/**
 * Before the backend says who is signed in nothing of the app is drawn, and nothing but
 * presentation mode is kept in browser storage. Values are invented.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AuthGate } from "@/components/auth/auth-gate";
import { readPresent, writePresent } from "@/lib/rfq/presentation";
import { rfqStatusView } from "@/lib/rfq/rfq-status";
import { SessionProvider } from "@/lib/rfq/session";

describe("the gate", () => {
  it("draws nothing while it is not known who is signed in", () => {
    const html = renderToStaticMarkup(
      createElement(SessionProvider, null, createElement(AuthGate, null, createElement("p", null, "금액 조항"))),
    );
    expect(html).toBe("");
  });
});

describe("presentation mode", () => {
  function storage(initial: Record<string, string> = {}) {
    const data = new Map(Object.entries(initial));
    return { data, getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) };
  }

  it("is kept, and a team stored by an older version is deleted", () => {
    const s = storage({ "autorfx.session": JSON.stringify({ team: "sales", present: true }) });
    expect(readPresent(s, "")).toBe(true);
    expect(s.data.get("autorfx.session")).toBe('{"present":true}');
  });

  it("is read from the address, and a team in the address means nothing", () => {
    const s = storage();
    expect(readPresent(s, "?team=sales&present=1")).toBe(true);
    expect([...s.data.values()].join()).not.toContain("sales");
    writePresent(s, false);
    expect(readPresent(s, "?team=sales")).toBe(false);
  });

  it("survives a broken value", () => {
    expect(readPresent(storage({ "autorfx.session": "{" }), "")).toBe(false);
  });
});

describe("RFQ list states", () => {
  const item = { id: "r/1", name: "RFQ 000001", created_at: null, file_count: 2 };

  it("opens results when done and the progress otherwise", () => {
    expect(rfqStatusView({ ...item, status: "done" }, false).href).toBe("/rfq/r%2F1/clauses");
    expect(rfqStatusView({ ...item, status: "running" }, false).href).toBe("/rfq/r%2F1/processing");
    expect(rfqStatusView({ ...item, status: "failed" }, false).href).toBe("/rfq/r%2F1/processing");
  });

  it("explains failed and stale, and says what this account can do about it", () => {
    expect(rfqStatusView({ ...item, status: "stale" }, true).note).toContain("다시 올리면");
    expect(rfqStatusView({ ...item, status: "stale" }, false).note).toContain("업로드할 수 있는 계정");
    expect(rfqStatusView({ ...item, status: "failed" }, true).note).toContain("다시 올리");
    expect(rfqStatusView({ ...item, status: "stale" }, true).href).toBeNull();
    expect(rfqStatusView({ ...item, status: "done" }, true).note).toBeNull();
  });
});
