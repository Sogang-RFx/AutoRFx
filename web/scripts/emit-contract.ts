/**
 * Writes /contract/*.schema.json from the zod contract.
 * Usage: pnpm contract:emit        (write)
 *        pnpm contract:emit --check (exit 1 when files differ)
 */
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import path from "node:path";
import { renderContract } from "../src/lib/rfq/contract-json";

const CONTRACT_DIR = path.resolve(__dirname, "../../contract");
const check = process.argv.includes("--check");

mkdirSync(CONTRACT_DIR, { recursive: true });
const drift: string[] = [];
for (const [name, text] of Object.entries(renderContract())) {
  const file = path.join(CONTRACT_DIR, `${name}.schema.json`);
  const current = existsSync(file) ? readFileSync(file, "utf8") : null;
  if (current === text) continue;
  if (check) drift.push(name);
  else writeFileSync(file, text);
}
if (drift.length > 0) {
  process.stderr.write(`contract drift: ${drift.join(", ")} — run pnpm contract:emit\n`);
  process.exit(1);
}
