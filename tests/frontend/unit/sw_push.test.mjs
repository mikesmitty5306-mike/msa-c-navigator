// Runs web/sw.js in a minimal fake service-worker scope and feeds it the push payloads the backend sends
// (msac/push.py: {"title", "body", "url": "#/trials/<NCT>"} and a summary with "url": "#/trials").
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const SW = readFileSync(join(here, "..", "..", "..", "web", "sw.js"), "utf8");
const SCOPE = "https://example.github.io/msac-navigator/";

function loadWorker({ windows = [] } = {}) {
  const handlers = {};
  const shown = [];
  const opened = [];
  const self = {
    addEventListener: (type, fn) => { handlers[type] = fn; },
    location: { origin: "https://example.github.io" },
    registration: { scope: SCOPE, showNotification: async (title, options) => { shown.push({ title, options }); } },
    clients: { matchAll: async () => windows, openWindow: async (url) => { opened.push(url); }, claim: async () => {} },
    skipWaiting: () => {},
  };
  vm.runInNewContext(SW, { self, URL, Headers, Response, setTimeout, clearTimeout, console, caches: {} });
  return { handlers, shown, opened };
}

async function dispatch(handler, event) {
  const pending = [];
  handler({ ...event, waitUntil: (p) => pending.push(p) });
  await Promise.all(pending);
}

const pushEvent = (payload) => ({ data: { json: () => (typeof payload === "string" ? JSON.parse(payload) : payload), text: () => String(payload) } });

test("backend 'new trial' payload shows a notification that opens the trial", async () => {
  const w = loadWorker();
  await dispatch(w.handlers.push, pushEvent({ title: "New MSA trial", body: "Study of ALT001 in MSA-C", url: "#/trials/NCT07514923" }));
  assert.equal(w.shown.length, 1);
  assert.equal(w.shown[0].title, "New MSA trial");
  assert.equal(w.shown[0].options.body, "Study of ALT001 in MSA-C");
  assert.equal(w.shown[0].options.data.url, "#/trials/NCT07514923");
  await dispatch(w.handlers.notificationclick, { notification: { close() {}, data: w.shown[0].options.data } });
  assert.deepEqual(w.opened, [`${SCOPE}#/trials/NCT07514923`]);
});

test("backend summary payload opens the trial list; an open app is focused instead of a new window", async () => {
  const messages = [];
  const client = { url: SCOPE + "#/", postMessage: (m) => messages.push(m), focus: async () => {} };
  const w = loadWorker({ windows: [client] });
  await dispatch(w.handlers.push, pushEvent({ title: "More MSA trial updates", body: "3 additional trial updates. Open Trials to review them.", url: "#/trials" }));
  await dispatch(w.handlers.notificationclick, { notification: { close() {}, data: w.shown[0].options.data } });
  // objects made inside the vm sandbox have a different prototype, so compare as JSON
  assert.equal(JSON.stringify(messages), JSON.stringify([{ type: "open", url: `${SCOPE}#/trials` }]));
  assert.deepEqual(w.opened, []);
});

test("status-change payload without a tag, odd links and empty pushes are handled safely", async () => {
  const w = loadWorker();
  await dispatch(w.handlers.push, pushEvent({ title: "Trial status changed", body: "X: RECRUITING → COMPLETED", url: "#/trials/NCT1" }));
  assert.equal(w.shown[0].options.tag, undefined);
  await dispatch(w.handlers.push, pushEvent({ title: "Phish", body: "x", url: "https://evil.example/" }));
  assert.equal(w.shown[1].options.data.url, "#/");                 // only in-app "#/" links are followed
  await dispatch(w.handlers.push, { data: null });
  assert.equal(w.shown[2].title, "MSA-C Navigator");                 // iOS requires a visible notification for every push
});
