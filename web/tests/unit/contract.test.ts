/**
 * Contract test (TASKS S006, CONTRACT-01, INV-9): the committed JSON Schema in
 * /contract matches the zod contract. Backend responses are checked against
 * the same schema at run time by the provider (`provider.ts`) and in pytest.
 */
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { renderContract } from "@/lib/rfq/contract-json";

const CONTRACT_DIR = path.resolve(__dirname, "../../../contract");

describe("JSON Schema files in /contract", () => {
  it("match the zod contract exactly (run `pnpm contract:emit` after changing contract.ts)", () => {
    const rendered = renderContract();
    const onDisk = readdirSync(CONTRACT_DIR).filter((f) => f.endsWith(".schema.json"));
    expect(onDisk.map((f) => f.replace(".schema.json", "")).sort()).toEqual(Object.keys(rendered).sort());
    for (const [name, text] of Object.entries(rendered)) {
      expect(readFileSync(path.join(CONTRACT_DIR, `${name}.schema.json`), "utf8"), name).toBe(text);
    }
  });
});
