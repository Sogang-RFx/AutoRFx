/**
 * Deterministic pre-cost calculation for the mock API (SPEC §8, INV-5).
 *
 *   fixed_cost_per_unit = fixed_costs / min_quantity
 *   unit_cost           = fixed_cost_per_unit + variable_cost_per_unit
 *
 * Decimal arithmetic only — never JavaScript floats. Until T305 replaces this
 * with the Python implementation, SPEC §8 is the reference for both.
 */
import Decimal from "decimal.js";
import type { PrecostInputs } from "../contract";

const D = Decimal.clone({ precision: 40, rounding: Decimal.ROUND_HALF_UP });

/** Decimal places kept in results. The single place the rounding rule lives. */
export const RESULT_DECIMALS = 4;

export class PrecostInputError extends Error {}

/** The one rounding rule for every pre-cost output (SPEC §8: "반올림 규칙은 한 곳에"). */
export function roundResult(value: Decimal): string {
  return value.toDecimalPlaces(RESULT_DECIMALS, Decimal.ROUND_HALF_UP).toFixed(RESULT_DECIMALS);
}

export function computePrecost(inputs: PrecostInputs): { fixed_cost_per_unit: string; unit_cost: string } {
  if (!Number.isInteger(inputs.min_quantity) || inputs.min_quantity <= 0) {
    throw new PrecostInputError("최소 수량은 1 이상의 정수여야 합니다.");
  }
  const fixed = new D(inputs.fixed_costs);
  const variable = new D(inputs.variable_cost_per_unit);
  if (fixed.isNegative() || variable.isNegative()) throw new PrecostInputError("비용은 음수일 수 없습니다.");
  if (!/^[A-Z]{3}$/.test(inputs.currency)) throw new PrecostInputError("통화는 세 글자 코드여야 합니다.");

  // Round only at the end: intermediate values keep full precision.
  const perUnit = fixed.div(inputs.min_quantity);
  return { fixed_cost_per_unit: roundResult(perUnit), unit_cost: roundResult(perUnit.plus(variable)) };
}
