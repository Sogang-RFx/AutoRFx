// Copies the PDF.js worker into public/ so the source viewer can load it from
// the same origin without bundler-specific worker handling.
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const pkgDir = path.dirname(require.resolve("pdfjs-dist/package.json"));
const src = path.join(pkgDir, "build", "pdf.worker.min.mjs");
const dest = path.join(here, "..", "public", "pdf.worker.min.mjs");

if (!existsSync(src)) {
  process.stderr.write(`pdf.worker.min.mjs not found at ${src}\n`);
  process.exit(1);
}
mkdirSync(path.dirname(dest), { recursive: true });
copyFileSync(src, dest);
