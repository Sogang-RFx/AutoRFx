/** Pre-cost helpers: exact percent conversion and the company request body. Values invented for the test. */
import { describe, expect, it } from "vitest";
import { CompanyRequest } from "@/lib/rfq/contract";
import { companyRequest, defaultValidUntil, fractionToPercent, percentToFraction } from "@/lib/rfq/precost";

describe("percent", () => {
  it("converts exactly, without float noise", () => {
    expect(percentToFraction("3")).toBe("0.03");
    expect(percentToFraction("0.1")).toBe("0.001");
    expect(percentToFraction("8.25")).toBe("0.0825");
    expect(fractionToPercent("0.07")).toBe("7");
    expect(fractionToPercent("0.015")).toBe("1.5");
  });
  it("refuses what is not a decimal", () => {
    expect(percentToFraction("3%")).toBe("");
    expect(percentToFraction("-1")).toBe("");
  });
});

describe("company request", () => {
  it("keeps the rows and appends new ones without an id", () => {
    const company = {
      settings: {
        home_currency: "KRW", discount_rate: "0.08", min_margin: "0.05", scrap_rate: "0.015", sga_rate: "0.08",
        max_payment_days: null, max_liability: null,
      },
      profile: { certifications: [], processes: [], qualifications: [] },
      prices: [{ id: "price-000000000001", category: "material" as const, name: "강판", unit: "kg", price: "1.05",
        currency: "EUR", valid_until: "2027-12-31", department: "purchasing" }],
      answers: [],
      today: "2026-11-22",
      categories: {},
    };
    const body = companyRequest(company, [{ category: "process", name: "프레스", unit: "h", price: "180",
      currency: "EUR", valid_until: defaultValidUntil(company.today), department: "production_engineering" }]);
    expect(CompanyRequest.parse(body).prices.map((p) => p.id)).toEqual(["price-000000000001", null]);
    expect(body.prices[1].valid_until).toBe("2027-12-31");
  });
});
