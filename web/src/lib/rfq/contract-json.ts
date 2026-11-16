import { z } from "zod";
import { CONTRACT_SCHEMAS } from "./contract";

/** Renders every contract schema as stable, pretty JSON Schema text. */
export function renderContract(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, schema] of Object.entries(CONTRACT_SCHEMAS)) {
    const json = z.toJSONSchema(schema, { target: "draft-2020-12", io: "output" });
    const withId = { $id: `https://autorfx.local/contract/${name}.schema.json`, ...json };
    out[name] = `${JSON.stringify(withId, null, 2)}\n`;
  }
  return out;
}
