#!/usr/bin/env node
/**
 * Counts integration code per variant: non-blank, non-comment lines between
 * `---- <KIND> start` and `---- <KIND> end` markers in src/, plus whole glue files.
 *
 *   node bench/count-glue.mjs
 */
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const isCode = (l) => {
  const t = l.trim();
  return t && !t.startsWith("//") && !t.startsWith("/*") && !t.startsWith("*") && !t.startsWith("{/*") && !t.includes("bench-only");
};
const out = {};
for (const v of ["variant-assistant-ui", "variant-copilotkit"]) {
  const counts = {};
  const src = join(ROOT, v, "src");
  for (const f of readdirSync(src)) {
    const lines = readFileSync(join(src, f), "utf8").split("\n");
    if (f === "agent.ts") {
      counts["AGENT (mock AG-UI source, identical)"] = lines.filter(isCode).length;
      continue;
    }
    if (f.endsWith(".css") && lines.some((l) => l.includes("THEME-GLUE"))) {
      counts["THEME-GLUE"] = (counts["THEME-GLUE"] ?? 0) + lines.filter((l) => isCode(l) && !l.trim().startsWith("*")).length;
      continue;
    }
    let kind = null;
    for (const l of lines) {
      const m = l.match(/----\s+([A-Z-]+)\s+(start|end)/);
      if (m) {
        kind = m[2] === "start" ? m[1] : null;
        continue;
      }
      if (kind && isCode(l)) counts[kind] = (counts[kind] ?? 0) + 1;
    }
  }
  out[v] = counts;
}
writeFileSync(join(ROOT, "results", "glue-lines.json"), JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
