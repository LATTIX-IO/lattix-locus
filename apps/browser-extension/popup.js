/* Lattix Locus popup: share a tab, reconnect, stop (LOCUS-350). Human gestures only. */
"use strict";

const api = globalThis.browser ?? globalThis.chrome;

function send(message) {
  return new Promise((resolve) => {
    try {
      const maybe = api.runtime.sendMessage(message, (reply) => resolve(reply || {}));
      if (maybe && typeof maybe.then === "function") maybe.then((r) => resolve(r || {}), () => resolve({}));
    } catch (_) {
      resolve({});
    }
  });
}

async function currentTab() {
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  return tab || null;
}

async function refresh() {
  const tab = await currentTab();
  const status = await send({ type: "status", tabId: tab ? tab.id : null });
  const line = document.getElementById("status");
  if (status.halted) line.textContent = "Stopped. Reset computer use in Locus to continue.";
  else if (status.connected) line.textContent = "Connected to Locus.";
  else if (status.hostPort) line.textContent = "Waiting for Locus...";
  else line.textContent = "Not connected. Pair this browser in Locus.";
  document.getElementById("tab").textContent = status.shared
    ? "This tab is shared with Locus."
    : "This tab is not shared.";
  const share = document.getElementById("share");
  share.textContent = status.shared ? "Stop sharing this tab" : "Share this tab with Locus";
  share.dataset.on = status.shared ? "1" : "0";
  share.disabled = !tab || !/^https?:/.test(tab.url || "");
}

document.getElementById("share").addEventListener("click", async (event) => {
  const tab = await currentTab();
  if (!tab) return;
  await send({ type: "share", tabId: tab.id, on: event.currentTarget.dataset.on !== "1" });
  refresh();
});
document.getElementById("connect").addEventListener("click", async () => {
  await send({ type: "connect" });
  setTimeout(refresh, 500);
});
document.getElementById("stop").addEventListener("click", async () => {
  await send({ type: "panic" });
  refresh();
});
refresh();
