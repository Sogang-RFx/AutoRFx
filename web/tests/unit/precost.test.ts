/** Pre-cost arithmetic (SPEC §8, INV-5): decimal only, validated inputs. */
import { describe, expect, it } from "vitest";
import { PrecostInputError, computePrecost } from "@/lib/rfq/mock/precost";

const base = { item: "FDLM", currency: "USD" };

describe("computePrecost", () => {
  it("amortizes fixed cost over the minimum quantity", () => {
    const r = computePrecost({ ...base, min_quantity: 400000, fixed_costs: "227000", variable_cost_per_unit: "9.35" });
    expect(r).toEqual({ fixed_cost_per_unit: "0.5675", unit_cost: "9.9175" });
  });

  it("does not suffer from binary floating point error", () => {
    // 0.1 + 0.2 in IEEE-754 is 0.30000000000000004
    const r = computePrecost({ ...base, min_quantity: 10, fixed_costs: "1", variable_cost_per_unit: "0.2" });
    expect(r.unit_cost).toBe("0.3000");
  });

  it("rounds only the final value, half up, to the documented precision", () => {
    const r = computePrecost({ ...base, min_quantity: 3, fixed_costs: "1", variable_cost_per_unit: "0" });
    expect(r.fixed_cost_per_unit).toBe("0.3333");
    const up = computePrecost({ ...base, min_quantity: 8, fixed_costs: "1", variable_cost_per_unit: "0.00005" });
    expect(up.unit_cost).toBe("0.1251"); // 0.125 + 0.00005 = 0.12505 → 0.1251
  });

  it("rejects invalid inputs instead of guessing", () => {
    expect(() => computePrecost({ ...base, min_quantity: 0, fixed_costs: "1", variable_cost_per_unit: "1" })).toThrow(
      PrecostInputError,
    );
    expect(() => computePrecost({ ...base, min_quantity: 1, fixed_costs: "-1", variable_cost_per_unit: "1" })).toThrow(
      PrecostInputError,
    );
    expect(() =>
      computePrecost({ ...base, currency: "usd", min_quantity: 1, fixed_costs: "1", variable_cost_per_unit: "1" }),
    ).toThrow(PrecostInputError);
  });
});
