#!/usr/bin/env node
/**
 * One-shot headless bench for both variants (no dev server, no browser download).
 *
 *   node bench/run.mjs [variant ...] [--reps N]
 *
 * Serves each variant's dist/ from a throwaway loopback server, drives it with playwright-core
 * using the Chromium already in the Playwright cache, and writes results/bench-<variant>.json.
 * Measures: stream render time (2,000 deltas at one event per macrotask), long-thread render time
 * (200 messages / 50 tool calls), long tasks, CSP violations, outbound requests (every non-loopback
 * request is logged and aborted), what the library sent to the agent, HITL routing through
 * confirmAction(), keyboard-only reachability, and an axe-core pass.
 */
import { createServer } from "node:http";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { extname, join, resolve } from "node:path";
import { chromium } from "playwright-core";

const require = createRequire(import.meta.url);
const ROOT = resolve(import.meta.dirname, "..");
const STREAM_SENTINEL = "EOS-2000";
const LONG_SENTINEL = "LONG-END-200";
const args = process.argv.slice(2);
const repsIdx = args.indexOf("--reps");
const REPS = repsIdx >= 0 ? Number(args[repsIdx + 1]) : 3;
const variants = args.filter((a, i) => !a.startsWith("--") && i !== repsIdx + 1);
const VARIANTS = variants.length ? variants : ["variant-assistant-ui", "variant-copilotkit"];
// Optional ablation: BENCH_QUERY=md=0 BENCH_TAG=plain appends a query and writes bench-<variant>-<tag>.json.
const EXTRA = process.env.BENCH_QUERY ? `&${process.env.BENCH_QUERY}` : "";
const TAG = process.env.BENCH_TAG ? `-${process.env.BENCH_TAG}` : "";

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png" };

function serve(dir) {
  const server = createServer(async (req, res) => {
    const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
    const file = join(dir, path === "/" ? "index.html" : path);
    try {
      const body = await readFile(file);
      res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(404).end();
    }
  });
  return new Promise((ok) => server.listen(0, "127.0.0.1", () => ok(server)));
}

const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.floor(s.length / 2)] : null;
};

async function cdpMetrics(cdp) {
  const { metrics } = await cdp.send("Performance.getMetrics");
  const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]));
  return { task: m.TaskDuration, script: m.ScriptDuration, layout: m.LayoutDuration, style: m.RecalcStyleDuration };
}
const deltaMs = (a, b) => Object.fromEntries(Object.keys(a).map((k) => [k, Math.round((b[k] - a[k]) * 1000)]));

async function newPage(browser, origin, external, consoleLog, requests = []) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await context.route("**/*", (route) => {
    const url = route.request().url();
    if (url.startsWith(origin) || url.startsWith("data:") || url.startsWith("blob:")) return route.continue();
    external.push({ url, type: route.request().resourceType() });
    return route.abort();
  });
  await context.addInitScript(() => {
    window.__bakeoffConfirm = () => true;
  });
  const page = await context.newPage();
  page.on("request", (r) => {
    const u = r.url();
    if (u.startsWith(origin) && !/\/assets\/|\/$|\?scenario=/.test(u)) requests.push(u.slice(origin.length));
  });
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") consoleLog.push(`${m.type()}: ${m.text().slice(0, 300)}`);
  });
  page.on("pageerror", (e) => consoleLog.push(`pageerror: ${String(e).slice(0, 300)}`));
  const cdp = await context.newCDPSession(page);
  await cdp.send("Performance.enable");
  return { context, page, cdp };
}

async function focusComposer(page) {
  for (let i = 1; i <= 40; i++) {
    await page.keyboard.press("Tab");
    const tag = await page.evaluate(() => {
      const el = document.activeElement;
      return el ? `${el.tagName}:${el.getAttribute("aria-label") ?? ""}:${el.getAttribute("placeholder") ?? ""}` : "";
    });
    if (/^(TEXTAREA|INPUT)/.test(tag) || /contenteditable/i.test(tag)) return i;
  }
  return null;
}

async function tabTo(page, testid, key = "Tab", max = 80) {
  for (let i = 1; i <= max; i++) {
    await page.keyboard.press(key);
    const id = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
    if (id === testid) return i;
  }
  return null;
}

const waitText = (page, text, timeout = 120000) =>
  page
    .waitForFunction((t) => (document.body.textContent.includes(t) ? performance.now() : false), text, { polling: "raf", timeout })
    .then((h) => h.jsonValue());

async function streamRun(browser, origin, rep, out) {
  const external = [];
  const consoleLog = [];
  const requests = [];
  const { context, page, cdp } = await newPage(browser, origin, external, consoleLog, requests);
  const result = { rep };
  try {
    await page.goto(`${origin}/?scenario=run&pace=0${EXTRA}`, { waitUntil: "load" });
    await page.waitForTimeout(300);
    result.tabsToComposer = await focusComposer(page);
    if (result.tabsToComposer === null) {
      // Fall back to a click so the remaining measurements still run.
      await page.locator("textarea, [contenteditable=true], input[type=text]").first().click();
    }
    await page.keyboard.type("Run the weekly report");
    const m0 = await cdpMetrics(cdp);
    await page.keyboard.press("Enter");
    const tSeen = await waitText(page, STREAM_SENTINEL);
    result.streamMainThreadMs = deltaMs(m0, await cdpMetrics(cdp));
    const bench = await page.evaluate(() => window.__bench);
    const start = bench.marks["run-start"];
    result.streamRenderMs = start != null ? Math.round(tSeen - start) : null;
    await page.locator("[data-approval]").first().waitFor({ timeout: 60000 });
    const b2 = await page.evaluate(() => window.__bench);
    const lt = b2.longTasks.filter((t) => t.start >= start);
    result.streamLongTasks = lt.length;
    result.streamLongTaskMs = Math.round(lt.reduce((a, t) => a + t.duration, 0));
    result.streamTBTms = Math.round(lt.reduce((a, t) => a + Math.max(0, t.duration - 50), 0));
    result.plan = await page.locator('[aria-label="Agent plan"]').innerText().catch(() => null);
    result.tier1Rendered = (await page.locator('[aria-label^="Run summary"]').count()) > 0;
    result.tier2Rendered = (await page.locator('[data-tier="2"]').count()) > 0;
    result.tier2Blocked = await page.getByText("Blocked component").count();
    result.tier2ScriptTags = await page.evaluate(() => document.querySelectorAll('script[src*="evil"]').length);
    if (rep === 0 && !TAG) {
      await page.screenshot({ path: join(out, "light.png") });
    }
    // Keyboard-only approval: from wherever focus is, reach the Approve button.
    result.focusAfterInterrupt = await page.evaluate(() => document.activeElement?.tagName ?? null);
    const fwd = await tabTo(page, "approve", "Tab");
    result.tabsToApprove = fwd;
    if (fwd === null) {
      await page.locator('[data-testid="approve"]').first().focus();
    }
    await page.keyboard.press("Enter");
    await page.locator("[data-run-error], [role=alert]").first().waitFor({ timeout: 60000 }).catch(() => null);
    await page.waitForTimeout(300);
    const b3 = await page.evaluate(() => window.__bench);
    result.confirmCalls = b3.confirmCalls.length;
    result.runInputs = b3.runInputs;
    result.resumeSent = b3.runInputs.some((r) => r.hasResume);
    result.resumePayload = b3.runInputs.find((r) => r.hasResume)?.resume ?? null;
    result.errorRendered = (await page.locator("[data-run-error]").count()) > 0 || (await page.getByText(/upstream mail relay timed out/).count()) > 0;
    result.cspViolations = b3.cspViolations;
    result.jsResourcesLoaded = await page.evaluate(() => performance.getEntriesByType("resource").filter((r) => r.name.endsWith(".js")).length);
    result.fontResourcesLoaded = await page.evaluate(() => performance.getEntriesByType("resource").filter((r) => /\.(woff2?|ttf)$/.test(r.name)).length);
    result.codeBlock = await page.evaluate(() => {
      const code = [...document.querySelectorAll("pre code, pre")].find((e) => e.textContent.includes("confirmAction(request)"));
      if (!code) return "missing";
      return code.querySelector("span[style], span[class*=token], span[class*=shiki]") ? "highlighted" : "plain";
    });
    result.pageErrors = b3.errors;
    if (rep === 0 && !TAG) {
      await page.getByRole("button", { name: /dark theme/i }).click();
      await page.waitForTimeout(200);
      await page.screenshot({ path: join(out, "dark.png") });
      // axe-core: evaluated through CDP, so the page CSP does not block it.
      const axeSrc = await readFile(require.resolve("axe-core/axe.min.js"), "utf8");
      await page.evaluate(axeSrc);
      const axe = await page.evaluate(async () => {
        const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } });
        return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes.slice(0, 2).map((n) => n.target.join(" ")) }));
      });
      result.axeDark = axe;
      await page.getByRole("button", { name: /light theme/i }).click();
      await page.waitForTimeout(200);
      result.axeLight = await page.evaluate(async () => {
        const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } });
        return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes.slice(0, 2).map((n) => n.target.join(" ")) }));
      });
    }
  } catch (e) {
    result.failure = String(e).slice(0, 500);
    await page.screenshot({ path: join(out, `failure-stream-${rep}.png`) }).catch(() => {});
  }
  result.externalRequests = external;
  result.sameOriginNonAssetRequests = requests;
  result.console = consoleLog;
  await context.close();
  return result;
}

async function longRun(browser, origin, rep, out) {
  const external = [];
  const consoleLog = [];
  const requests = [];
  const { context, page, cdp } = await newPage(browser, origin, external, consoleLog, requests);
  const result = { rep };
  try {
    await page.goto(`${origin}/?scenario=long&pace=0${EXTRA}`, { waitUntil: "load" });
    await page.waitForTimeout(300);
    await page.locator("textarea, [contenteditable=true], input[type=text]").first().click();
    await page.keyboard.type("Load history");
    const m0 = await cdpMetrics(cdp);
    await page.keyboard.press("Enter");
    const tSeen = await waitText(page, LONG_SENTINEL);
    result.longMainThreadMs = deltaMs(m0, await cdpMetrics(cdp));
    // Scroll the whole thread to the top and back, as a reader would.
    const s0 = await cdpMetrics(cdp);
    await page.evaluate(async () => {
      const el = [...document.querySelectorAll("*")].find((e) => e.scrollHeight > e.clientHeight + 50 && /(auto|scroll)/.test(getComputedStyle(e).overflowY));
      if (!el) return;
      for (let y = el.scrollHeight; y >= 0; y -= 400) {
        el.scrollTop = y;
        await new Promise((r) => requestAnimationFrame(r));
      }
    });
    result.scrollMainThreadMs = deltaMs(s0, await cdpMetrics(cdp));
    result.toolCardsAfterScroll = await page.locator('[data-tool="search_files"]').count();
    await page.waitForTimeout(500);
    const b = await page.evaluate(() => window.__bench);
    const start = b.marks["run-start"];
    result.longRenderMs = start != null ? Math.round(tSeen - start) : null;
    const lt = b.longTasks.filter((t) => t.start >= start);
    result.longTasks = lt.length;
    result.longTaskMs = Math.round(lt.reduce((a, t) => a + t.duration, 0));
    result.longTBTms = Math.round(lt.reduce((a, t) => a + Math.max(0, t.duration - 50), 0));
    result.toolCards = await page.locator('[data-tool="search_files"]').count();
    result.domNodes = await page.evaluate(() => document.getElementsByTagName("*").length);
    const heap = await page.evaluate(() => performance.memory?.usedJSHeapSize ?? null);
    result.heapMB = heap ? Math.round(heap / 1e5) / 10 : null;
    if (rep === 0 && !TAG) await page.screenshot({ path: join(out, "long.png") });
  } catch (e) {
    result.failure = String(e).slice(0, 500);
    await page.screenshot({ path: join(out, `failure-long-${rep}.png`) }).catch(() => {});
  }
  result.externalRequests = external;
  result.sameOriginNonAssetRequests = requests;
  result.console = consoleLog;
  await context.close();
  return result;
}

const browser = await chromium.launch({ headless: true });
try {
  for (const v of VARIANTS) {
    const out = join(ROOT, "results", v);
    await mkdir(out, { recursive: true });
    const server = await serve(join(ROOT, v, "dist"));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const stream = [];
    const long = [];
    for (let r = 0; r < REPS; r++) stream.push(await streamRun(browser, origin, r, out));
    for (let r = 0; r < REPS; r++) long.push(await longRun(browser, origin, r, out));
    server.close();
    const summary = {
      variant: v,
      chromium: browser.version(),
      reps: REPS,
      streamRenderMsMedian: median(stream.map((s) => s.streamRenderMs).filter((x) => x != null)),
      streamTBTmsMedian: median(stream.map((s) => s.streamTBTms).filter((x) => x != null)),
      streamLongTasksMedian: median(stream.map((s) => s.streamLongTasks).filter((x) => x != null)),
      longRenderMsMedian: median(long.map((s) => s.longRenderMs).filter((x) => x != null)),
      longTBTmsMedian: median(long.map((s) => s.longTBTms).filter((x) => x != null)),
      longTasksMedian: median(long.map((s) => s.longTasks).filter((x) => x != null)),
      streamTaskMsMedian: median(stream.map((s) => s.streamMainThreadMs?.task).filter((x) => x != null)),
      streamScriptMsMedian: median(stream.map((s) => s.streamMainThreadMs?.script).filter((x) => x != null)),
      longTaskMsMedianCdp: median(long.map((s) => s.longMainThreadMs?.task).filter((x) => x != null)),
      scrollTaskMsMedian: median(long.map((s) => s.scrollMainThreadMs?.task).filter((x) => x != null)),
      externalRequests: [...stream, ...long].flatMap((s) => s.externalRequests),
      cspViolations: stream.flatMap((s) => s.cspViolations ?? []),
    };
    await writeFile(join(ROOT, "results", `bench-${v}${TAG}.json`), JSON.stringify({ summary, stream, long }, null, 2));
    console.log(JSON.stringify(summary, null, 2));
  }
} finally {
  await browser.close();
}
