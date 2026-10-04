/*
 * Lattix Locus browser extension: background (LOCUS-350, decision D-25).
 *
 * One Manifest V3 codebase for Chrome, Edge and Firefox. The extension is the
 * hands, not the brain:
 *
 *  - It talks only to the Locus native-messaging host (io.lattix.locus_browser),
 *    which the browser starts for this pinned extension ID only. The host
 *    relays to the local Locus backend over loopback with the pairing key from
 *    the OS secret store. The extension never holds that key.
 *  - It executes only commands the backend sends, and the backend sends a
 *    command only after the Locus gateway authorized it. It never acts on its
 *    own initiative, and page content cannot send it commands.
 *  - Floors it enforces itself (defence in depth): no typing into password,
 *    card, CVV, SSN or one-time-code fields; secret field values are never
 *    read; http(s) pages only; an element must still match the facts the
 *    gateway authorized; after a panic every older command is refused.
 *  - Sharing a tab is a human gesture in the popup. Commands cannot share tabs.
 */
"use strict";

const api = globalThis.browser ?? globalThis.chrome;
const HOST_NAME = "io.lattix.locus_browser";
const NAV_TIMEOUT_MS = 15000;
const RECONNECT_ALARM = "locus-reconnect";

const state = {
  port: null,
  connected: false,
  lastError: "",
  panicEpoch: 0,
  halted: false,
  awaitingPanicAck: false,
  cancelled: new Set(),
};
const memory = {};

function fail(code, message) {
  const error = new Error(message || code);
  error.code = code;
  return error;
}

// --- tab sharing (human decisions, kept in session storage) -----------------
async function sessionGet(key, fallback) {
  try {
    const area = api.storage && api.storage.session;
    if (area) {
      const out = await area.get(key);
      if (out && out[key] !== undefined) return out[key];
    }
  } catch (_) {
    /* fall back to memory */
  }
  return memory[key] !== undefined ? memory[key] : fallback;
}

async function sessionSet(key, value) {
  memory[key] = value;
  try {
    const area = api.storage && api.storage.session;
    if (area) await area.set({ [key]: value });
  } catch (_) {
    /* memory copy is enough for this browser session */
  }
}

async function sharedTabs() {
  return new Set(await sessionGet("sharedTabs", []));
}

async function setShared(tabId, on) {
  if (!Number.isInteger(tabId)) throw fail("bad_tab", "tab id must be an integer");
  const shared = await sharedTabs();
  if (on) shared.add(tabId);
  else shared.delete(tabId);
  await sessionSet("sharedTabs", Array.from(shared));
  return on;
}

api.tabs.onRemoved.addListener((tabId) => {
  setShared(tabId, false).catch(() => {});
});

// --- helpers ------------------------------------------------------------------
function isHttpUrl(url) {
  try {
    const parsed = new URL(String(url || ""));
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch (_) {
    return false;
  }
}

function browserName() {
  const ua = String((globalThis.navigator && navigator.userAgent) || "");
  if (ua.includes("Firefox/")) return "firefox";
  if (ua.includes("Edg/")) return "edge";
  return "chrome";
}

async function tabById(tabId) {
  if (tabId === null || tabId === undefined) {
    const [tab] = await api.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab) throw fail("no_tab", "no active tab");
    return tab;
  }
  if (!Number.isInteger(tabId)) throw fail("bad_tab", "tab id must be an integer");
  try {
    return await api.tabs.get(tabId);
  } catch (_) {
    throw fail("no_tab", "tab not found");
  }
}

function tabView(tab, shared) {
  return {
    tab_id: tab.id,
    url: tab.url || "",
    title: tab.title || "",
    active: !!tab.active,
    window_id: tab.windowId,
    shared: shared.has(tab.id),
  };
}

async function runContent(tabId, op, args) {
  const tab = await tabById(tabId);
  if (!isHttpUrl(tab.url)) throw fail("unsupported_page", "Locus only works on http(s) pages");
  await api.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  const results = await api.scripting.executeScript({
    target: { tabId: tab.id },
    func: (name, input) => globalThis.__locusContent.run(name, input),
    args: [op, args || {}],
  });
  const out = results && results[0] ? results[0].result : undefined;
  if (!out || typeof out !== "object") throw fail("content_error", "no result from the page");
  if (out.error) throw fail(String(out.error.code || "content_error"), out.error.message);
  return out;
}

function stopped(commandId) {
  return state.halted || state.cancelled.has(commandId);
}

async function waitForLoad(tabId, commandId) {
  const started = Date.now();
  let sawLoading = false;
  for (;;) {
    if (stopped(commandId)) throw fail("cancelled", "stopped");
    let tab;
    try {
      tab = await api.tabs.get(tabId);
    } catch (_) {
      throw fail("no_tab", "tab closed while loading");
    }
    if (tab.status === "loading") sawLoading = true;
    const elapsed = Date.now() - started;
    if (tab.status === "complete" && (sawLoading || elapsed > 1000)) return tab;
    if (elapsed > NAV_TIMEOUT_MS) return tab;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

// --- commands (every one was authorized by the Locus gateway first) ----------
const OPS = {
  async list_tabs() {
    const shared = await sharedTabs();
    const tabs = await api.tabs.query({});
    return { tabs: tabs.map((tab) => tabView(tab, shared)) };
  },

  async tab_info(args) {
    const shared = await sharedTabs();
    return tabView(await tabById(args.tab_id), shared);
  },

  async inspect(args) {
    return runContent(args.tab_id, "inspect", { ref: String(args.ref || "") });
  },

  async observe(args) {
    const maxChars = Math.min(Math.max(Number(args.max_chars) || 16000, 1000), 50000);
    return runContent(args.tab_id, "observe", { max_chars: maxChars });
  },

  async navigate(args, msg) {
    const url = String(args.url || "");
    if (!isHttpUrl(url)) throw fail("navigation_not_http", "only http(s) URLs");
    let tab;
    if (args.tab_id === null || args.tab_id === undefined) {
      // A tab the agent opens is the agent's to use: mark it shared.
      tab = await api.tabs.create({ url, active: false });
      await setShared(tab.id, true);
    } else {
      tab = await tabById(args.tab_id);
      tab = await api.tabs.update(tab.id, { url });
    }
    const done = await waitForLoad(tab.id, msg.id);
    return { tab_id: done.id, url: done.url || url };
  },

  async act(args) {
    const control = String(args.control || "");
    let out;
    if (control === "scroll") {
      out = await runContent(args.tab_id, "scroll", { value: String(args.value || "down") });
    } else {
      out = await runContent(args.tab_id, "act", {
        ref: String(args.ref || ""),
        control,
        value: String(args.value || ""),
        expect_digest: String(args.expect_digest || ""),
      });
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
    const tab = await tabById(args.tab_id).catch(() => null);
    return { ...out, url: tab ? tab.url || "" : "" };
  },

  async screenshot(args) {
    const tab = await tabById(args.tab_id);
    if (!tab.active) throw fail("tab_not_active", "only the visible tab can be captured");
    const mask = await runContent(tab.id, "mask", { on: true });
    try {
      const dataUrl = await api.tabs.captureVisibleTab(tab.windowId, { format: "png" });
      return { data_url: dataUrl, masked: Number(mask.masked) || 0 };
    } finally {
      await runContent(tab.id, "mask", { on: false }).catch(() => {});
    }
  },
};

async function handleCommand(msg) {
  const id = String(msg.id || "");
  try {
    if (state.awaitingPanicAck) throw fail("panic", "computer use was stopped from the browser");
    if (typeof msg.epoch !== "number" || msg.epoch <= state.panicEpoch) {
      throw fail("panic", "computer use was stopped (panic)");
    }
    state.halted = false;
    const op = String(msg.op || "");
    const fn = Object.prototype.hasOwnProperty.call(OPS, op) ? OPS[op] : null;
    if (!fn) throw fail("unknown_op", "unknown command");
    const args = msg.args && typeof msg.args === "object" ? msg.args : {};
    const result = await fn(args, msg);
    if (stopped(id)) throw fail("cancelled", "stopped");
    return { type: "result", id, ok: true, result };
  } catch (error) {
    return {
      type: "result",
      id,
      ok: false,
      error: {
        code: String((error && error.code) || "error").slice(0, 64),
        message: String((error && error.message) || error).slice(0, 300),
      },
    };
  } finally {
    state.cancelled.delete(id);
  }
}

// Messages from the native host (the relay). Returns the reply, if any.
async function onHostMessage(msg) {
  if (!msg || typeof msg !== "object") return null;
  if (msg.type === "panic") {
    state.panicEpoch = Math.max(state.panicEpoch, Number(msg.epoch) || 0);
    state.halted = true;
    state.awaitingPanicAck = false;
    return null;
  }
  if (msg.type === "cancel") {
    state.cancelled.add(String(msg.id || ""));
    return null;
  }
  if (msg.type === "status") {
    state.connected = msg.connected === true;
    state.lastError = String(msg.error || "");
    return null;
  }
  if (msg.type === "command") {
    const reply = await handleCommand(msg);
    if (state.port) state.port.postMessage(reply);
    return reply;
  }
  return null;
}

// --- native host connection -------------------------------------------------
function connect() {
  if (state.port) return;
  let port;
  try {
    port = api.runtime.connectNative(HOST_NAME);
  } catch (error) {
    state.lastError = String((error && error.message) || error);
    return;
  }
  state.port = port;
  port.onMessage.addListener((msg) => {
    onHostMessage(msg);
  });
  port.onDisconnect.addListener(() => {
    const error = api.runtime.lastError;
    state.lastError = error ? String(error.message || error) : "disconnected";
    state.port = null;
    state.connected = false;
  });
  port.postMessage({ type: "hello", browser: browserName(), version: api.runtime.getManifest().version });
}

if (api.alarms) {
  api.alarms.create(RECONNECT_ALARM, { periodInMinutes: 1 });
  api.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === RECONNECT_ALARM) connect();
  });
}
api.runtime.onStartup.addListener(connect);
api.runtime.onInstalled.addListener(connect);

// --- popup (extension pages only; content scripts and pages are refused) ----
function fromExtensionPage(sender) {
  return !!sender && sender.id === api.runtime.id && !sender.tab;
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!fromExtensionPage(sender) || !msg || typeof msg !== "object") return false;
  (async () => {
    if (msg.type === "status") {
      const shared = await sharedTabs();
      return {
        connected: state.connected,
        hostPort: !!state.port,
        lastError: state.lastError,
        shared: Number.isInteger(msg.tabId) ? shared.has(msg.tabId) : false,
        sharedCount: shared.size,
        halted: state.halted || state.awaitingPanicAck,
      };
    }
    if (msg.type === "share") return { shared: await setShared(msg.tabId, msg.on === true) };
    if (msg.type === "connect") {
      connect();
      return { ok: true };
    }
    if (msg.type === "panic") {
      // Stop locally at once, and ask Locus to latch panic for every tool.
      state.halted = true;
      state.awaitingPanicAck = true;
      if (state.port) state.port.postMessage({ type: "event", event: "panic" });
      return { ok: true };
    }
    return { error: "unknown" };
  })().then(sendResponse, (error) => sendResponse({ error: String(error) }));
  return true;
});

// Test hook: Playwright drives the same entry points the native port and the
// popup use (the integration test has no native host). Only extension
// contexts and debuggers can reach a service worker's globals.
globalThis.locus = { onHostMessage, setShared, sharedTabs };
