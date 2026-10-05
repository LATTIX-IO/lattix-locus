#!/usr/bin/env node
/**
 * Attributes first-load JS bytes to npm packages using source maps.
 *
 *   npx vite build --sourcemap --outDir <tmp>   (run inside a variant directory)
 *   node bench/attribute.mjs <variant> <tmp-dist-dir>
 *
 * Writes results/attribution-<variant>.json: minified bytes per package for the entry chunk and
 * the chunks it imports statically. Bytes are generated-code bytes, before gzip.
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { SourceMapConsumer } from "source-map-js";

const ROOT = resolve(import.meta.dirname, "..");
const [variant, distDir] = process.argv.slice(2);
const html = readFileSync(join(distDir, "index.html"), "utf8");
const entry = html.match(/<script[^>]+src="\/assets\/([^"]+)"/)[1];
const first = new Set();
const stack = [entry];
while (stack.length) {
  const f = stack.pop();
  if (first.has(f)) continue;
  first.add(f);
  const src = readFileSync(join(distDir, "assets", f), "utf8");
  for (const m of src.matchAll(/(?:^|[;\n}])\s*import\s*(?:[\w${},*\s]+from\s*)?["']\.\/([^"']+\.js)["']/g)) stack.push(m[1]);
}

const pkgOf = (source) => {
  const s = source.replace(/\\/g, "/");
  const i = s.lastIndexOf("node_modules/");
  if (i < 0) return s.includes("/shared/") ? "(bake-off shared code)" : "(variant code)";
  const rest = s.slice(i + "node_modules/".length).split("/");
  return rest[0].startsWith("@") ? `${rest[0]}/${rest[1]}` : rest[0];
};

const bytes = {};
let total = 0;
for (const f of first) {
  const code = readFileSync(join(distDir, "assets", f), "utf8");
  if (!existsSync(join(distDir, "assets", `${f}.map`))) {
    bytes["(bundler runtime, no map)"] = (bytes["(bundler runtime, no map)"] ?? 0) + code.length;
    total += code.length;
    continue;
  }
  const map = new SourceMapConsumer(JSON.parse(readFileSync(join(distDir, "assets", `${f}.map`), "utf8")));
  const lines = code.split("\n");
  let prev = null;
  map.eachMapping((m) => {
    if (prev && prev.generatedLine === m.generatedLine) {
      const len = m.generatedColumn - prev.generatedColumn;
      const k = prev.source ? pkgOf(prev.source) : "(unmapped)";
      bytes[k] = (bytes[k] ?? 0) + len;
      total += len;
    } else if (prev) {
      const len = (lines[prev.generatedLine - 1]?.length ?? 0) - prev.generatedColumn;
      const k = prev.source ? pkgOf(prev.source) : "(unmapped)";
      bytes[k] = (bytes[k] ?? 0) + len;
      total += len;
    }
    prev = m;
  }, null, SourceMapConsumer.GENERATED_ORDER);
}
const top = Object.entries(bytes)
  .sort((a, b) => b[1] - a[1])
  .map(([pkg, b]) => ({ pkg, kB: Math.round(b / 102.4) / 10, pct: Math.round((1000 * b) / total) / 10 }));
const out = { variant, firstLoadChunks: [...first], attributedBytes: total, packages: top };
writeFileSync(join(ROOT, "results", `attribution-${variant}.json`), JSON.stringify(out, null, 2));
console.log(JSON.stringify({ variant, attributedKB: Math.round(total / 1024), top: top.slice(0, 15).map((t) => `${t.pkg} ${t.kB} kB (${t.pct}%)`) }, null, 2));
