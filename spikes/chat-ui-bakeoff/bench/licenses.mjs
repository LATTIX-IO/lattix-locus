#!/usr/bin/env node
/**
 * Offline license audit of a variant's production dependency tree.
 *
 *   node bench/licenses.mjs <variant-dir>
 *
 * Walks `dependencies`, `optionalDependencies` and non-optional `peerDependencies` from the
 * variant's package.json using Node's resolution (nearest node_modules upwards), reads each
 * installed package.json, and writes results/licenses-<variant>.json. No network.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync, realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const variant = process.argv[2];
if (!variant) throw new Error("usage: licenses.mjs <variant-dir>");
const PERMISSIVE = /^(MIT|ISC|BSD-2-Clause|BSD-3-Clause|0BSD|Apache-2\.0|BlueOak-1\.0\.0|CC0-1\.0|Unlicense|Python-2\.0)$/;

function findPkgDir(name, fromDir) {
  let dir = fromDir;
  for (;;) {
    const cand = join(dir, "node_modules", name, "package.json");
    if (existsSync(cand)) return dirname(realpathSync(cand));
    const up = dirname(dir);
    if (up === dir) return null;
    dir = up;
  }
}

function licenseOf(pkg) {
  if (typeof pkg.license === "string") return pkg.license;
  if (pkg.license?.type) return pkg.license.type;
  if (Array.isArray(pkg.licenses)) return pkg.licenses.map((l) => l.type ?? l).join(" OR ");
  return "UNKNOWN";
}

function isPermissive(expr) {
  const parts = expr.replace(/[()]/g, "").split(/\s+(?:OR|AND)\s+/);
  if (/\sOR\s/.test(expr)) return parts.some((p) => PERMISSIVE.test(p.trim()));
  return parts.every((p) => PERMISSIVE.test(p.trim()));
}

const rootDir = join(ROOT, variant);
const rootPkg = JSON.parse(readFileSync(join(rootDir, "package.json"), "utf8"));
const seen = new Map();
const missing = [];
const queue = [];
const enqueue = (pkg, dir) => {
  const optionalPeers = new Set(Object.entries(pkg.peerDependenciesMeta ?? {}).filter(([, m]) => m?.optional).map(([n]) => n));
  for (const n of Object.keys({ ...(pkg.dependencies ?? {}), ...(pkg.optionalDependencies ?? {}) })) queue.push([n, dir, false]);
  for (const n of Object.keys(pkg.peerDependencies ?? {})) queue.push([n, dir, optionalPeers.has(n)]);
};
enqueue(rootPkg, rootDir);
while (queue.length) {
  const [name, from, optional] = queue.shift();
  const dir = findPkgDir(name, from);
  if (!dir) {
    if (!optional) missing.push(name);
    continue;
  }
  const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
  const key = `${pkg.name}@${pkg.version}`;
  if (seen.has(key)) continue;
  seen.set(key, { name: pkg.name, version: pkg.version, license: licenseOf(pkg), dir });
  enqueue(pkg, dir);
}

const all = [...seen.values()].filter((p) => p.name !== "@bakeoff/shared");
const byLicense = {};
for (const p of all) byLicense[p.license] = (byLicense[p.license] ?? 0) + 1;
const flagged = all.filter((p) => !isPermissive(p.license)).map((p) => `${p.name}@${p.version}: ${p.license}`);
const names = new Set(all.map((p) => p.name));
const dupes = [...names].filter((n) => all.filter((p) => p.name === n).length > 1).map((n) => `${n}: ${all.filter((p) => p.name === n).map((p) => p.version).join(", ")}`);
const out = {
  variant,
  packages: all.length,
  uniqueNames: names.size,
  byLicense: Object.fromEntries(Object.entries(byLicense).sort((a, b) => b[1] - a[1])),
  nonPermissive: flagged,
  duplicateVersions: dupes,
  missingNonOptional: [...new Set(missing)],
  list: all.map((p) => `${p.name}@${p.version} ${p.license}`).sort(),
};
mkdirSync(join(ROOT, "results"), { recursive: true });
writeFileSync(join(ROOT, "results", `licenses-${variant}.json`), JSON.stringify(out, null, 2));
const { list, ...summary } = out;
console.log(JSON.stringify(summary, null, 2));
