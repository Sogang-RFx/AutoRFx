/** Column-assigner helpers: keep assigned empty columns, join wrapped headers, refuse incomplete roles. */
import { describe, expect, it } from "vitest";
import {
  gridWidth,
  headerRows,
  joinHeader,
  problem,
  rolesOf,
  shownColumns,
} from "@/lib/rfq/sheet-columns";

describe("shownColumns", () => {
  it("hides empty columns that nothing uses", () => {
    const rows = [
      ["Item", "", "Qty", "Price"],
      ["Pad", "", "4", ""],
    ];
    const shown = shownColumns(rows, 0, 1, 4);
    expect(shown.map((c) => c.data)).toEqual([0, 2, 3]);
    expect(shown[0].extra).toEqual([1]);
  });

  it("keeps a price column the layout already assigned even when every cell is empty", () => {
    // Two-line header: "Unit Price" sits on the row above; the header_row cell is blank.
    const rows = [
      ["Item", "Qty", "Unit Price"],
      ["Item", "Qty", ""],
      ["Pad", "4", ""],
    ];
    const without = shownColumns(rows, 1, 2, 3);
    expect(without.map((c) => c.data)).toEqual([0, 1]);
    const kept = shownColumns(rows, 1, 2, 3, new Set([2]));
    expect(kept.map((c) => c.data)).toEqual([0, 1, 2]);
  });
});

describe("rolesOf / problem / joinHeader / headerRows / gridWidth", () => {
  it("maps roles onto the grid width", () => {
    expect(rolesOf({ description: 0, quantity: 2, price: 3 }, 4)).toEqual([
      "description",
      "",
      "quantity",
      "price",
    ]);
  });

  it("requires quantity and a name column", () => {
    expect(problem(["", "quantity", ""])).toMatch(/품명/);
    expect(problem(["description", "", ""])).toMatch(/수량/);
    expect(problem(["description", "quantity", ""])).toBeNull();
  });

  it("joins wrapped header lines", () => {
    expect(joinHeader(["Total Bid", "Total Bid Price"])).toBe("Total Bid Price");
    expect(joinHeader(["Unit", "Price"])).toBe("Unit Price");
  });

  it("detects a sub-heading row under a merged title", () => {
    const rows = [
      ["No", "Description", "Total Bid Price", ""],
      ["", "", "Unit Price", "Total Amount"],
      ["1", "Pad", "10", "40"],
    ];
    expect(headerRows(rows, 0)).toEqual(["sub"]);
  });

  it("uses the longest row for grid width", () => {
    expect(gridWidth([["a"], ["b", "c", "d"]])).toBe(3);
  });
});
