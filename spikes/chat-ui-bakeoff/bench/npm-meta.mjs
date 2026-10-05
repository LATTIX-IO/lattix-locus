#!/usr/bin/env node
/**
 * Maintenance / churn signals from npm registry metadata only (no web scraping).
 *
 *   node bench/npm-meta.mjs [--asof YYYY-MM-DD]
 *
 * For each package: stable releases in the last 30/90/365 days, the number of breaking release
 * lines opened in the last 365 days (a new major, or a new minor while on 0.x), maintainers on npm,
 * and the deprecation flag. Writes results/npm-meta.json.
 */
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const asofIdx = process.argv.indexOf("--asof");
const ASOF = asofIdx > 0 ? new Date(process.argv[asofIdx + 1]) : new Date();
const PKGS = [
  "@assistant-ui/react",
  "@assistant-ui/core",
  "@assistant-ui/react-ag-ui",
  "@assistant-ui/react-markdown",
  "@copilotkit/react-core",
  "@copilotkit/react-ui",
  "@copilotkit/core",
  "@ag-ui/client",
  "@ag-ui/core",
];

// Package names are constants above; the shell is only needed to launch npm.cmd on Windows.
const npmView = (pkg) =>
  JSON.parse(
    execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", ["view", pkg, "time", "maintainers", "versions", "deprecated", "--json"], {
      encoding: "utf8",
      shell: process.platform === "win32",
      env: { ...process.env, npm_config_update_notifier: "false" },
    }),
  );

const STABLE = /^\d+\.\d+\.\d+$/;
const line = (v) => {
  const [maj, min] = v.split(".").map(Number);
  return maj === 0 ? `0.${min}` : `${maj}`;
};
const out = { asOf: ASOF.toISOString().slice(0, 10), packages: [] };
for (const pkg of PKGS) {
  const m = npmView(pkg);
  const time = m.time ?? {};
  const stable = Object.entries(time)
    .filter(([v]) => STABLE.test(v))
    .map(([v, t]) => ({ v, t: new Date(t) }))
    .sort((a, b) => a.t - b.t);
  const within = (days) => stable.filter((r) => ASOF - r.t <= days * 864e5 && r.t <= ASOF).length;
  const yearAgo = new Date(ASOF - 365 * 864e5);
  const linesBefore = new Set(stable.filter((r) => r.t < yearAgo).map((r) => line(r.v)));
  const newLines = [...new Set(stable.filter((r) => r.t >= yearAgo && r.t <= ASOF).map((r) => line(r.v)))].filter((l) => !linesBefore.has(l));
  const latest = stable.at(-1);
  out.packages.push({
    pkg,
    latest: latest?.v,
    latestDate: latest?.t.toISOString().slice(0, 10),
    firstPublished: time.created?.slice(0, 10),
    stableReleases: stable.length,
    allVersions: (m.versions ?? []).length,
    releases30d: within(30),
    releases90d: within(90),
    releases365d: within(365),
    breakingLinesOpened365d: newLines.length,
    breakingLines: newLines,
    npmMaintainers: (m.maintainers ?? []).length,
    deprecated: m.deprecated ?? null,
  });
}
writeFileSync(join(ROOT, "results", "npm-meta.json"), JSON.stringify(out, null, 2));
console.table(out.packages.map(({ breakingLines, ...p }) => p));
