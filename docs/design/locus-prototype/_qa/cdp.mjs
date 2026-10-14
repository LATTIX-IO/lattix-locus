// Real-browser QA: headless Chrome over the DevTools protocol.
// usage: node _qa/cdp.mjs <mode> [args]   modes: reflow | shot <page> <w> <h> <out> [dark] [script]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const ROOT = resolve(import.meta.dirname, '..');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 9300 + Math.floor(Math.random() * 600);
const prof = mkdtempSync(join(tmpdir(), 'locus-qa-'));
const proc = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${prof}`, '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', '--allow-file-access-from-files', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function target() { for (let i = 0; i < 60; i++) { try { const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); const p = l.find(x => x.type === 'page'); if (p) return p.webSocketDebuggerUrl; } catch {} await sleep(250); } throw new Error('chrome did not start'); }
const ws = new WebSocket(await target()); await new Promise(r => ws.onopen = r);
let id = 0; const wait = new Map();
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && wait.has(d.id)) { wait.get(d.id)(d); wait.delete(d.id); } };
const send = (method, params = {}) => new Promise(r => { const i = ++id; wait.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const evalJs = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400)); return r.result.result.value; };
async function load(page, w, h, opts = {}) {
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: opts.dpr || 1, mobile: w < 600 });
  await send('Emulation.setTouchEmulationEnabled', { enabled: w < 600 });
  await send('Page.navigate', { url: pathToFileURL(join(ROOT, page.split('?')[0])).href + (page.includes('?') ? '?' + page.split('?')[1].split('#')[0] : '') + (page.includes('#') ? '#' + page.split('#')[1] : '') });
  for (let i = 0; i < 40; i++) { await sleep(150); try { if (await evalJs('document.readyState') === 'complete') break; } catch {} }
  if (opts.dark !== undefined) await evalJs(`localStorage.setItem('locus.theme', '${opts.dark ? 'dark' : 'light'}'); ${opts.dark ? "document.documentElement.dataset.theme='dark'" : "delete document.documentElement.dataset.theme"}`);
  if (opts.script) await evalJs(opts.script);
  await sleep(opts.settle || 700);
}
const MEASURE = `(() => { const vw = document.documentElement.clientWidth, off = [];
  const OK = '.tbl-wrap,.tabs,pre,.code,.steps,.stage-bar,.cv-items,.palette,.drawer,.dialog,.toasts,.wf,.topo,.seg,.board,.scope-bar';
  const boxes = ['html', '.main', '.top', '.nav-list', '.proto-bar', '.page'].map(q => { const e = document.querySelector(q); return e ? q + ':' + (e.scrollWidth - e.clientWidth) : null; }).filter(Boolean);
  const inner = [...document.querySelectorAll('.main, .top, .nav, .proto-bar')].reduce((n, e) => Math.max(n, e.scrollWidth - e.clientWidth), 0);
  document.querySelectorAll('.app *').forEach(el => { if (el.closest(OK)) return; const r = el.getBoundingClientRect(); if (!r.width || r.right <= vw + 1 || getComputedStyle(el).visibility === 'hidden') return;
    off.push((el.id ? '#' + el.id : el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '')) + ' →' + Math.round(r.right)); });
  const sw = document.documentElement.scrollWidth;
  return { vw, sw, overflow: Math.max(sw - vw, inner, off.length ? 1 : 0), boxes, offenders: [...new Set(off)].slice(0, 6) }; })()`;
const mode = process.argv[2];
try {
  if (mode === 'reflow') {
    const pages = (process.argv[3] || 'home.html,work.html,work-projects.html,work-schedules.html,work-conversations.html,task.html?task=TSK-3318,plan-review.html,activity.html,activity-run.html?run=RUN-48213,library.html,library-agent.html?agent=AGT-0042,library-team.html?team=TEAM-07,memory.html,control-tower.html,settings.html,auth.html,index.html,screen-registry.html,prototype-tests.html').split(',');
    const widths = (process.argv[4] || '320,390,640,768,1280').split(',').map(Number);
    const rows = [];
    for (const p of pages) for (const w of widths) { await load(p, w, 900); const m = await evalJs(MEASURE); rows.push({ page: p, w, ...m }); console.log(`${m.overflow > 0 ? 'OVER' : 'ok  '} ${String(w).padStart(4)} ${p}${m.overflow > 0 ? '  +' + m.overflow + 'px  ' + m.offenders.join(', ') : ''}`); }
    writeFileSync(join(ROOT, '_qa', 'reflow.json'), JSON.stringify(rows, null, 1));
  } else if (mode === 'shot') {
    const [page, w, h, out, theme, script] = process.argv.slice(3);
    await load(page, +w, +h, { dark: theme === 'dark' ? true : theme === 'light' ? false : undefined, script, settle: 1200 });
    const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    writeFileSync(out, Buffer.from(r.result.data, 'base64')); console.log('wrote', out, JSON.stringify(await evalJs(MEASURE)));
  } else if (mode === 'errors') {
    const errs = []; ws.addEventListener('message', m => { const d = JSON.parse(m.data); if (d.method === 'Runtime.exceptionThrown') errs.push(cur + ': ' + d.params.exceptionDetails.exception?.description?.split(String.fromCharCode(10))[0]); if (d.method === 'Runtime.consoleAPICalled' && d.params.type === 'error') errs.push(cur + ': console ' + JSON.stringify(d.params.args.map(a => a.value || a.description)).slice(0, 200)); if (d.method === 'Log.entryAdded' && d.params.entry.level === 'error') errs.push(cur + ': ' + d.params.entry.text.slice(0, 200)); });
    await send('Runtime.enable'); await send('Log.enable'); let cur = '';
    for (const p of process.argv[3].split(',')) for (const theme of ['light', 'dark']) { cur = p + ' (' + theme + ')'; await load(p, 1280, 900, { dark: theme === 'dark', settle: 900 }); }
    console.log(errs.length ? errs.join(String.fromCharCode(10)) : 'no errors');
  } else if (mode === 'a11y') {
    // accessibility tree: every interactive node must have a name; each page needs main + navigation landmarks and one h1
    const ROLES = ['button','link','textbox','combobox','checkbox','radio','tab','menuitem','switch','searchbox','spinbutton','listbox'];
    await send('Accessibility.enable'); const out = [];
    for (const spec of process.argv[3].split(',')) { const [p, script] = spec.split('|'); await load(p, 1280, 900, { script: script || undefined, settle: 900 });
      const { result } = await send('Accessibility.getFullAXTree'); const nodes = result.nodes.filter(n => !n.ignored);
      const role = n => n.role && n.role.value, name = n => (n.name && n.name.value || '').trim();
      const unnamed = nodes.filter(n => ROLES.includes(role(n)) && !name(n)).map(n => role(n));
      const modal = nodes.find(n => role(n) === 'dialog' && n.properties && n.properties.some(pp => pp.name === 'modal' && pp.value.value)); const lm = modal ? (name(modal) ? [] : ['dialog name']) : (p.startsWith('auth') ? ['main'] : ['main','navigation']).filter(r => !nodes.some(n => role(n) === r)); const h1 = nodes.filter(n => role(n) === 'heading' && n.properties && n.properties.some(pp => pp.name === 'level' && pp.value.value === 1)).length;
      const inter = nodes.filter(n => ROLES.includes(role(n))).length;
      out.push(`${unnamed.length || lm.length || (h1 < 1 && !modal) ? 'FAIL' : 'ok  '} ${p}${script ? ' [' + script.slice(0, 30) + ']' : ''} · ${inter} interactive · unnamed ${unnamed.length}${unnamed.length ? ' (' + [...new Set(unnamed)].join(',') + ')' : ''} · missing landmarks ${lm.join(',') || 'none'} · h1 ${h1}${modal ? ' · modal dialog: ' + name(modal) : ''}`); }
    console.log(out.join(String.fromCharCode(10)));
  } else if (mode === 'eval') {
    const [page, w, script] = process.argv.slice(3); await load(page, +w || 1280, 900); console.log(JSON.stringify(await evalJs(script), null, 1));
  }
} finally { ws.close(); proc.kill(); }
