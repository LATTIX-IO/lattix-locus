#!/usr/bin/env node
/**
 * Static scan of a variant's production build (dist/).
 *
 *   node bench/scan-bundle.mjs <variant-dir>
 *
 * Reports JS/CSS size (raw and gzip), first-load JS (the entry chunk plus everything it imports
 * statically), lazy chunks, hard-coded hosts (analytics/telemetry flagged), and CSP-relevant code
 * patterns. Writes results/scan-<variant>.json.
 */
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { gzipSync } from "node:zlib";

const ROOT = resolve(import.meta.dirname, "..");
const variant = process.argv[2];
const dist = join(ROOT, variant, "dist");
const assets = join(dist, "assets");
const files = readdirSync(assets);
const read = (f) => readFileSync(join(assets, f), "utf8");
const gz = (s) => gzipSync(Buffer.from(s)).length;

const html = readFileSync(join(dist, "index.html"), "utf8");
const entry = html.match(/<script[^>]+src="\/assets\/([^"]+)"/)[1];
const cssEntry = [...html.matchAll(/<link[^>]+href="\/assets\/([^"]+\.css)"/g)].map((m) => m[1]);

// First load: entry + static imports, transitively.
const firstLoad = new Set();
const stack = [entry];
while (stack.length) {
  const f = stack.pop();
  if (firstLoad.has(f)) continue;
  firstLoad.add(f);
  const src = read(f);
  for (const m of src.matchAll(/(?:^|[;\n}])\s*import\s*(?:[\w${},*\s]+from\s*)?["']\.\/([^"']+\.js)["']/g)) stack.push(m[1]);
}

const js = files.filter((f) => f.endsWith(".js"));
const css = files.filter((f) => f.endsWith(".css"));
const sum = (list, fn) => list.reduce((a, f) => a + fn(f), 0);
const rawOf = (f) => Buffer.byteLength(read(f));
const gzOf = (f) => gz(read(f));

const FLAG = /segment|scarf|posthog|sentry|mixpanel|amplitude|google-analytics|googletagmanager|datadog|intercom|hotjar|telemetry|analytics|copilotkit\.ai|assistant-ui\.com|assistant-api\.com|fonts\.googleapis|fonts\.gstatic|jsdelivr|unpkg|esm\.sh|cdnjs/i;
const hosts = {};
for (const f of [...js, ...css]) {
  for (const m of read(f).matchAll(/https?:\/\/([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/g)) {
    const h = m[1].toLowerCase();
    hosts[h] ??= { count: 0, firstLoad: false, files: new Set() };
    hosts[h].count++;
    hosts[h].files.add(f);
    if (firstLoad.has(f)) hosts[h].firstLoad = true;
  }
}
const hostList = Object.entries(hosts)
  .map(([h, v]) => ({ host: h, count: v.count, firstLoad: v.firstLoad, flagged: FLAG.test(h), chunks: v.files.size }))
  .sort((a, b) => Number(b.flagged) - Number(a.flagged) || b.count - a.count);

const PATTERNS = {
  "Function(": /\bnew Function\(|[^.\w]Function\(\s*[`'"]/g,
  "eval(": /[^.\w]eval\(/g,
  innerHTML: /\.innerHTML\s*=/g,
  dangerouslySetInnerHTML: /dangerouslySetInnerHTML/g,
  "createElement(style)": /createElement\(\s*[`'"]style[`'"]\s*\)/g,
  insertRule: /\.insertRule\(/g,
  adoptedStyleSheets: /adoptedStyleSheets/g,
  "createElement(script)": /createElement\(\s*[`'"]script[`'"]\s*\)/g,
  iframe: /createElement\(\s*[`'"]iframe[`'"]\s*\)|<iframe/g,
  srcdoc: /srcdoc/g,
  sendBeacon: /sendBeacon/g,
  "new WebSocket": /new WebSocket\(/g,
  "fetch(": /[^.\w]fetch\(/g,
  "new Worker": /new Worker\(/g,
  "WebAssembly": /WebAssembly\./g,
};
const patterns = {};
for (const [k, re] of Object.entries(PATTERNS)) {
  let all = 0;
  let first = 0;
  for (const f of js) {
    const n = (read(f).match(re) ?? []).length;
    all += n;
    if (firstLoad.has(f)) first += n;
  }
  patterns[k] = { all, firstLoad: first };
}

const out = {
  variant,
  entry,
  jsChunks: js.length,
  cssFiles: css.length,
  jsRawBytes: sum(js, rawOf),
  jsGzipBytes: sum(js, gzOf),
  firstLoadChunks: firstLoad.size,
  firstLoadJsRawBytes: sum([...firstLoad], rawOf),
  firstLoadJsGzipBytes: sum([...firstLoad], gzOf),
  lazyChunks: js.length - firstLoad.size,
  cssRawBytes: sum(css, rawOf),
  cssGzipBytes: sum(css, gzOf),
  cssEntry,
  fontFiles: files.filter((f) => /\.(woff2?|ttf|otf)$/.test(f)).length,
  flaggedHosts: hostList.filter((h) => h.flagged),
  hosts: hostList,
  patterns,
};
mkdirSync(join(ROOT, "results"), { recursive: true });
writeFileSync(join(ROOT, "results", `scan-${variant}.json`), JSON.stringify(out, null, 2));
const { hosts: _h, ...summary } = out;
console.log(JSON.stringify(summary, null, 2));
