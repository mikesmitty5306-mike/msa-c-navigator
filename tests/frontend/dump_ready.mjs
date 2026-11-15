// Opens a page in headless Edge/Chromium, waits until the app marks the screen as drawn
// (<html data-ready="1">), then prints the page's HTML. Replaces --dump-dom with a fixed
// --virtual-time-budget, which gave up too early when the machine was busy.
// Usage: node dump_ready.mjs <browser.exe> <url> [timeoutMs]
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

if (typeof WebSocket === "undefined") {            // built into Node 22+; exit code 3 = "skip this test"
  process.stderr.write(`SKIP: Node.js 22 or newer is needed for the screen tests (found ${process.version})\n`);
  process.exit(3);
}
const [browser, url, timeoutArg] = process.argv.slice(2);
const TIMEOUT = Number(timeoutArg || 60000);
const profile = mkdtempSync(join(tmpdir(), "msac-edge-"));
const flags = ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  `--user-data-dir=${profile}`, "--remote-debugging-port=0"];
// GitHub's Ubuntu runners block Chrome's sandbox (user namespaces), so it is turned off there only.
if (process.platform === "linux" && process.env.CI) flags.push("--no-sandbox", "--disable-dev-shm-usage");
const proc = spawn(browser, [...flags, "about:blank"], { stdio: "ignore" });

const DEBUG = !!process.env.MSAC_DUMP_DEBUG;
const t0 = Date.now();
function log(msg) {       // MSAC_DUMP_DEBUG=1: network and console events, for diagnosing a stuck screen
  const p = msg.params || {};
  const what = p.request ? `${p.request.method} ${p.request.url}` : p.response ? `${p.response.status} ${p.response.url}`
    : p.args ? p.args.map((a) => a.value ?? a.description).join(" ") : p.exceptionDetails ? JSON.stringify(p.exceptionDetails).slice(0, 300)
    : p.frame ? p.frame.url : p.errorText || p.requestId || "";
  process.stderr.write(`[${Date.now() - t0}ms] ${msg.method} ${what}\n`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const deadline = Date.now() + TIMEOUT;

async function cleanup(code) {
  // Close the browser and wait for it, so its temporary profile can be deleted (nothing is left on disk).
  const exited = proc.exitCode !== null ? Promise.resolve() : new Promise((r) => proc.once("exit", r));
  try {
    // On Windows, Edge's helper processes (e.g. the crash reporter) outlive the main one and lock the profile.
    if (process.platform === "win32" && proc.pid) spawnSync("taskkill", ["/T", "/F", "/PID", String(proc.pid)], { stdio: "ignore" });
    else proc.kill();
  } catch { /* already gone */ }
  await Promise.race([exited, sleep(5000)]);
  for (let i = 0; i < 40; i++) {
    try { rmSync(profile, { recursive: true, force: true }); break; } catch { await sleep(250); }
  }
  process.exit(code);
}

async function main() {
  const portFile = join(profile, "DevToolsActivePort");
  let port = "";
  while (!port) {
    if (Date.now() > deadline) throw new Error("browser did not start");
    try {                                   // the browser may still be writing (or locking) the file
      const text = existsSync(portFile) ? readFileSync(portFile, "utf8") : "";
      if (text.includes("\n")) port = text.split("\n")[0].trim();
    } catch { /* busy: try again */ }
    if (!port) await sleep(100);
  }
  let target;
  while (!target) {
    const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    target = list.find((t) => t.type === "page");
    if (!target) await sleep(100);
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("devtools connection failed")); });
  let nextId = 0;
  const pending = new Map();
  const inflight = new Map();
  const errors = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    else if (msg.method) {
      const p = msg.params || {};
      if (msg.method === "Network.requestWillBeSent") inflight.set(p.requestId, p.request.url);
      if (msg.method === "Network.loadingFailed") errors.push(`failed: ${inflight.get(p.requestId)} ${p.errorText}`);
      if (msg.method === "Network.loadingFinished" || msg.method === "Network.loadingFailed") inflight.delete(p.requestId);
      if (msg.method === "Runtime.exceptionThrown") errors.push(JSON.stringify(p.exceptionDetails).slice(0, 300));
      if (DEBUG) log(msg);
    }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const id = ++nextId; pending.set(id, res); ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, returnByValue: true })).result?.result?.value;

  await send("Page.enable");
  await send("Network.enable"); await send("Runtime.enable");   // to report unfinished requests and errors
  await send("Page.navigate", { url });
  let ready = false;
  while (Date.now() < deadline) {
    if (await evaluate("document.readyState === 'complete' && document.documentElement.dataset.ready === '1'")) { ready = true; break; }
    await sleep(100);
  }
  process.stdout.write((await evaluate("document.documentElement.outerHTML")) || "");
  if (!ready) {
    const why = await evaluate("JSON.stringify({href: location.href, state: document.readyState, sw: !!(navigator.serviceWorker && navigator.serviceWorker.controller), view: (document.getElementById('view') || document.body).innerText.slice(0, 200)})");
    process.stderr.write(`NOT READY: the app never marked the screen as drawn ${why} unfinished=${JSON.stringify([...inflight.values()])} errors=${JSON.stringify(errors)}\n`);
  }
  ws.close();
  return ready ? 0 : 2;
}

main().then(cleanup, (err) => { process.stderr.write(String(err && err.stack || err) + "\n"); cleanup(1); });
