// App start-up, routing and all user actions. Screens are in screens.js; data loading in data.js;
// personal storage in store.js. No inline scripts anywhere (Content-Security-Policy).

import { DataClient } from "./data.js";
import { Store } from "./store.js";
import * as screens from "./screens.js";
import { errorBox } from "./views.js";
import { abstractShard, buildHash, debounce, fmtDate, html, parseHash } from "./util.js";
import { cityKey, cityLabel, rowsToObjects, searchCities } from "./logic.js";
import { isStandalone, pushStatus, shareCode, SHARE_INSTRUCTIONS, turnOff, turnOn } from "./push.js";

const view = document.getElementById("view");
const banners = document.getElementById("banners");
const overlayRoot = document.getElementById("overlay-root");
const backBtn = document.querySelector(".back-btn");

const memoryStorage = { data: {}, getItem(k) { return this.data[k] ?? null; }, setItem(k, v) { this.data[k] = String(v); }, removeItem(k) { delete this.data[k]; } };
let backend;
try { localStorage.setItem("msac.test", "1"); localStorage.removeItem("msac.test"); backend = localStorage; } catch { backend = memoryStorage; }

const data = new DataClient("data/");
const store = new Store(backend);
const state = { depth: 0, goingBack: false, renderToken: 0, currentBack: "#/", lastRoute: "", citySuggestions: [], swReg: null };

// ---------------------------------------------------------------- data helpers (memoized per build)
const memo = new Map();
function once(key, fn) {
  const k = `${data.meta ? data.meta.build_id : ""}:${key}`;
  if (!memo.has(k)) memo.set(k, fn().catch((e) => { memo.delete(k); throw e; }));
  return memo.get(k);
}
const ctxBase = {
  data, store,
  grades: () => once("grades", async () => {
    const g = await data.get("grades.json");
    return { ...g, byId: Object.fromEntries(g.mechanisms.map((m) => [m.id, m])) };
  }),
  trials: () => once("trials", async () => {
    const t = await data.get("trials.json");
    return { ...t, byId: Object.fromEntries(t.trials.map((x) => [x.id, x])) };
  }),
  library: () => once("library", async () => {
    const index = await data.get("library/index.json");
    const shards = await Promise.all(index.shards.map((s) => data.get(`library/${s.name}.json`)));
    return shards.flatMap((s) => rowsToObjects(index.columns, s.rows));
  }),
};

// ---------------------------------------------------------------- routing
const ROUTES = [
  [[], (c) => screens.home(c)],
  [["grades"], (c) => screens.gradesList(c)],
  [["grades", "help"], (c) => screens.gradesHelp(c)],
  [["grades", ":id"], (c, id) => screens.mechanism(c, id)],
  [["grades", ":id", ":cat"], (c, id, cat) => screens.results(c, id, cat)],
  [["trials"], (c) => screens.trialsList(c)],
  [["trial", ":id"], (c, id) => screens.trialDetail(c, id)],
  [["trials", ":id"], (c, id) => screens.trialDetail(c, id)],   // alias: notification links may use #/trials/NCT…
  [["library"], (c) => screens.library(c)],
  [["saved"], (c) => screens.saved(c)],
  [["settings"], (c) => screens.settings(c)],
  [["settings", "install"], (c) => screens.install(c)],
];

function match(parts) {
  for (const [pattern, fn] of ROUTES) {
    if (pattern.length !== parts.length) continue;
    const params = [];
    if (pattern.every((p, i) => (p.startsWith(":") ? (params.push(parts[i]), true) : p === parts[i]))) return [fn, params];
  }
  return [() => screens.notFound(), []];
}

async function render({ keepScroll = false } = {}) {
  const token = ++state.renderToken;
  delete document.documentElement.dataset.ready;
  const { parts, query } = parseHash(location.hash);
  const [fn, params] = match(parts);
  const slow = setTimeout(() => { if (token === state.renderToken) view.innerHTML = '<p class="loading">Loading…</p>'; }, 150);
  let screen;
  try {
    if (!data.meta) await data.loadMeta();
    screen = await fn({ ...ctxBase, query, parts }, ...params);
  } catch (err) {
    screen = { title: "Couldn't load", section: "", back: "#/", body: errorBox(err) };
  }
  clearTimeout(slow);
  if (token !== state.renderToken) return;
  view.innerHTML = String(screen.body);
  document.title = screen.title && screen.title !== "MSA-C Navigator" ? `${screen.title} · MSA-C Navigator` : "MSA-C Navigator";
  document.querySelectorAll("[data-section]").forEach((a) => {
    if (a.dataset.section === screen.section) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  state.currentBack = screen.back || null;
  backBtn.hidden = !screen.back;
  if (screen.back) backBtn.setAttribute("href", screen.back);
  if (!keepScroll) window.scrollTo(0, 0);
  showOfflineBanner();
  if (parts[0] === "settings" && parts.length === 1) await afterSettings();
  // "Screen finished drawing" mark, used by the automated browser tests instead of a fixed wait.
  if (token === state.renderToken && state.started) document.documentElement.dataset.ready = "1";
}

window.addEventListener("hashchange", () => {
  if (state.goingBack) { state.depth = Math.max(0, state.depth - 1); state.goingBack = false; } else state.depth += 1;
  render();
});

function navigate(hash, { replace = false } = {}) {
  if (replace) { history.replaceState(null, "", hash); render(); } else location.hash = hash;
}

// ---------------------------------------------------------------- feedback
let toastTimer;
function toast(message) {
  let el = document.querySelector(".toast");
  if (!el) { el = document.createElement("div"); el.className = "toast"; el.setAttribute("role", "status"); document.body.appendChild(el); }
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

function setBanner(id, content) {
  let el = document.getElementById(id);
  if (!content) { if (el) el.remove(); return; }
  if (!el) { el = document.createElement("div"); el.id = id; banners.appendChild(el); }
  el.className = `banner ${id.replace("banner-", "")}`;
  el.innerHTML = String(content);
}

function showOfflineBanner() {
  const offline = data.offline || navigator.onLine === false;
  const asOf = fmtDate(data.dataAsOf());
  setBanner("banner-offline", offline ? html`<span>Offline: showing data as of ${asOf || "the last visit"}.</span>
    <button type="button" class="secondary" data-action="refresh">Retry</button>` : null);
}
window.addEventListener("online", () => refresh(true));
window.addEventListener("offline", showOfflineBanner);

async function refresh(quiet = false) {
  const btn = document.querySelector(".refresh-btn");
  btn.classList.add("is-loading");
  try {
    const before = data.meta && data.meta.build_id;
    await data.loadMeta(true);
    pruneDataCache();
    if (state.swReg) state.swReg.update().catch(() => {});
    await render({ keepScroll: true });
    if (!quiet) toast(data.offline ? "Offline: showing saved data" : before && before !== data.meta.build_id ? "New data loaded" : "Up to date");
  } catch (err) {
    showOfflineBanner();
    if (!quiet) toast(err.message || "Couldn't refresh");
  } finally {
    btn.classList.remove("is-loading");
  }
}

// ---------------------------------------------------------------- welcome (first run)
function showWelcome() {
  const standalone = isStandalone();
  overlayRoot.innerHTML = String(html`<div class="overlay" role="dialog" aria-modal="true" aria-labelledby="welcome-title">
    <div class="sheet">
      <img class="welcome-icon" src="icons/icon-192.png" alt="" width="64" height="64">
      <h1 id="welcome-title">Welcome to MSA-C Navigator</h1>
      <p>A guide to research and clinical trials on multiple system atrophy, focused on the cerebellar type (MSA-C).</p>
      <ul>
        <li><strong>What it does:</strong> grades each area of MSA biology on how treatments have done, lists trials you could
          join (nearest first), and keeps a searchable library of studies. The data updates automatically.</li>
        <li><strong>Not medical advice.</strong> Grades and rankings are automated and can be wrong. Talk to your neurologist
          before any decision about treatment or trials.</li>
        ${standalone ? "" : html`<li><strong>Add it to your Home Screen:</strong> in Safari tap Share, then "Add to Home Screen",
          and open it from the icon from then on.</li>`}
        <li><strong>Notifications (optional):</strong> in the Home Screen app, Settings can notify you when a new trial open to
          MSA-C starts recruiting, or a relevant trial changes status.</li>
        <li><strong>Back up your saved items:</strong> stars and notes stay only on this phone. Use Settings > Export backup now and then.</li>
      </ul>
      <div class="actions"><button type="button" data-action="welcome-start">Get started</button>
        <button type="button" class="secondary" data-action="welcome-install">How to install</button></div>
    </div></div>`);
  overlayRoot.querySelector("button").focus();
}
function closeWelcome() { store.setWelcomeSeen(); overlayRoot.innerHTML = ""; }

// ---------------------------------------------------------------- settings: city, storage, notifications
async function afterSettings() {
  const pushDone = renderPush();
  const statusEl = document.getElementById("storage-status");
  if (statusEl && navigator.storage && navigator.storage.persisted) {
    try {
      const kept = await navigator.storage.persisted();
      statusEl.textContent = kept ? "This phone has agreed to keep the app's storage even when space runs low."
        : "iOS may clear this app's storage if the phone runs low on space, so keep a backup.";
    } catch { /* not available */ }
  }
  await pushDone.catch(() => {});
}

async function renderPush() {
  const body = document.getElementById("push-body");
  if (!body) return;
  let st;
  try { st = await pushStatus(data.meta); } catch { st = { state: "unsupported", message: "Notifications aren't available here." }; }
  const code = store.state.push.code;
  if (st.state === "ready") {
    body.innerHTML = String(html`<div class="actions"><button type="button" data-action="push-on">Turn on notifications</button></div>`);
  } else if (st.state === "on") {
    body.innerHTML = String(html`<p class="status-line ok">Notifications are on.</p>
      <p class="meta">If you haven't yet, send the code to your helper so the app can reach this iPhone.</p>
      <div class="actions"><button type="button" data-action="push-share">Send notification code to helper</button>
        <button type="button" class="secondary" data-action="push-off">Turn off notifications</button></div>
      ${code ? html`<details><summary>Show the code</summary><p class="meta">${SHARE_INSTRUCTIONS}</p>
        <textarea class="code-box" id="push-code" readonly>${code}</textarea>
        <div class="actions"><button type="button" class="secondary" data-action="push-copy">Copy code</button></div></details>` : ""}`);
  } else {
    body.innerHTML = String(html`<p class="status-line">${st.message}</p>`);
  }
}

const citySuggest = debounce(async (input) => {
  const list = document.getElementById("city-list");
  if (!list) return;
  const q = input.value.trim();
  state.citySuggestions = [];
  if (q.length >= 2) {
    try {
      const name = `cities/${cityKey(q)}.json`;
      const file = data.has(name) ? await data.get(name) : { rows: [] };
      if (input.value.trim() === q) state.citySuggestions = searchCities(file.rows, q, 8);
    } catch { state.citySuggestions = []; }
  }
  list.innerHTML = String(html`${state.citySuggestions.map((c, i) => html`<li role="option" data-action="pick-city" data-i="${i}">${cityLabel(c)}</li>`)}`);
  list.hidden = !state.citySuggestions.length;
  input.setAttribute("aria-expanded", state.citySuggestions.length ? "true" : "false");
}, 150);

async function saveCityFromQuery(query) {
  const status = document.getElementById("city-status");
  let pick = state.citySuggestions[0];
  if (!pick) {
    const name = `cities/${cityKey(query)}.json`;
    const file = data.has(name) ? await data.get(name) : { rows: [] };
    pick = searchCities(file.rows, query, 1)[0];
  }
  if (!pick) {
    if (status) status.textContent = query ? `No place called "${query}" was found. Try the start of the name, or add the state or country after a comma.` : "Type a city or town first.";
    return;
  }
  store.setHomeCity({ ...pick, source: "city" });
  toast(`Home city: ${cityLabel(pick)}`);
  render({ keepScroll: true });
}

function useLocation() {
  if (!("geolocation" in navigator)) { toast("Location isn't available on this device."); return; }
  toast("Finding your location…");
  navigator.geolocation.getCurrentPosition((pos) => {
    store.setHomeCity({ name: "Your location", region: "", country: "", lat: Number(pos.coords.latitude.toFixed(3)),
      lon: Number(pos.coords.longitude.toFixed(3)), source: "location" });
    toast("Using your location (stored only on this phone)");
    render({ keepScroll: true });
  }, (err) => {
    toast(err && err.code === 1 ? "Location wasn't allowed. You can type a city instead." : "Couldn't find your location. Type a city instead.");
  }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 });
}

// ---------------------------------------------------------------- backup
async function exportBackup() {
  const backup = store.exportBackup();
  const text = JSON.stringify(backup, null, 2);
  const name = `msac-navigator-backup-${backup.exported_at.slice(0, 10)}.json`;
  const file = typeof File === "function" ? new File([text], name, { type: "application/json" }) : null;
  try {
    if (file && navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file], title: "MSA-C Navigator backup" });
      toast("Backup ready to save");
      return;
    }
  } catch (e) {
    if (e && e.name === "AbortError") { toast("Backup not shared"); return; }
  }
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  toast("Backup downloaded");
}

async function importBackup(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  try {
    const result = store.importBackup(await file.text());
    toast(`Restored ${result.saved} saved ${result.saved === 1 ? "item" : "items"}${result.home_city ? " and your home location" : ""}`);
    render({ keepScroll: true });
  } catch (e) {
    toast(e.message || "That backup couldn't be read.");
  } finally {
    input.value = "";
  }
}

// ---------------------------------------------------------------- service worker
function pruneDataCache() {
  if (navigator.serviceWorker && navigator.serviceWorker.controller && data.meta) {
    const keep = [new URL("data/meta.json", location.href).href, ...data.currentUrls().map((u) => new URL(u, location.href).href)];
    navigator.serviceWorker.controller.postMessage({ type: "prune", keep });
  }
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (!reloading) { reloading = true; location.reload(); } });
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data && e.data.type === "open" && typeof e.data.url === "string") {
      const hash = e.data.url.includes("#") ? e.data.url.slice(e.data.url.indexOf("#")) : "#/";
      navigate(hash);
    }
  });
  navigator.serviceWorker.register("sw.js").then((reg) => {
    state.swReg = reg;
    const offerUpdate = (worker) => {
      state.waitingWorker = worker;
      setBanner("banner-update", html`<span>An update to the app is ready.</span>
        <button type="button" data-action="apply-update">Update now</button>`);
    };
    if (reg.waiting && navigator.serviceWorker.controller) offerUpdate(reg.waiting);
    reg.addEventListener("updatefound", () => {
      const w = reg.installing;
      if (!w) return;
      w.addEventListener("statechange", () => {
        if (w.state === "installed" && navigator.serviceWorker.controller) offerUpdate(w);
      });
    });
    pruneDataCache();
  }).catch(() => { /* the app still works online without a service worker */ });
}

// ---------------------------------------------------------------- events
const ACTIONS = {
  back() {
    if (state.depth > 0) { state.goingBack = true; history.back(); } else navigate(state.currentBack || "#/");
  },
  refresh() { refresh(); },
  star(el) {
    const { type, id, title, url, status } = el.dataset;
    const on = store.toggleSaved(type, id, { title, url, status });
    document.querySelectorAll(`.star[data-type="${CSS.escape(type)}"][data-id="${CSS.escape(id)}"]`).forEach((b) => {
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.setAttribute("aria-label", `${on ? "Remove from saved" : "Save"}: ${title || id}`);
      b.title = on ? "Saved" : "Save";
    });
    toast(on ? "Saved" : "Removed from saved");
    // Ask iOS (17+) to keep the app's storage once there is something worth keeping.
    if (on && navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
    if (parseHash(location.hash).parts[0] === "saved") render({ keepScroll: true });
  },
  async abstract(el) {
    const box = el.parentElement.querySelector(`[data-abstract-for="${CSS.escape(el.dataset.uid)}"]`);
    if (!box) return;
    if (!box.hidden) { box.hidden = true; el.textContent = "Show abstract"; el.setAttribute("aria-expanded", "false"); return; }
    el.textContent = "Loading…";
    let text = "No abstract is available for this paper.";
    try {
      const shard = await abstractShard(el.dataset.uid);
      const name = `abstracts/${shard}.json`;
      if (data.has(name)) text = (await data.get(name)).abstracts[el.dataset.uid] || text;
    } catch { text = "The abstract couldn't be loaded. Check the connection and try again."; }
    box.textContent = text;
    box.hidden = false;
    el.textContent = "Hide abstract";
    el.setAttribute("aria-expanded", "true");
  },
  more(el) {
    const { parts, query } = parseHash(location.hash);
    query.set("n", el.dataset.n);
    navigate(buildHash(parts, Object.fromEntries(query)), { replace: true });
  },
  seen(el) { store.markSeen(el.dataset.id, el.dataset.status); render({ keepScroll: true }); },
  "use-location": useLocation,
  "clear-city"() { store.setHomeCity(null); toast("Home location cleared"); render({ keepScroll: true }); },
  "pick-city"(el) {
    const c = state.citySuggestions[Number(el.dataset.i)];
    if (c) { store.setHomeCity({ ...c, source: "city" }); toast(`Home city: ${cityLabel(c)}`); render({ keepScroll: true }); }
  },
  export: exportBackup,
  "show-welcome": showWelcome,
  "welcome-start": closeWelcome,
  "welcome-install"() { closeWelcome(); navigate("#/settings/install"); },
  reset(el) {
    if (el.dataset.armed === "1") {
      store.clearAll();
      toast("Everything on this phone was erased");
      navigate("#/");
      return;
    }
    el.dataset.armed = "1";
    el.textContent = "Tap again to erase everything";
    setTimeout(() => { if (el.isConnected) { el.dataset.armed = ""; el.textContent = "Erase app data"; } }, 6000);
  },
  "apply-update"() {
    if (state.waitingWorker) state.waitingWorker.postMessage({ type: "skip-waiting" }); else location.reload();
  },
  "push-on"() {
    // Notification.requestPermission is called straight away inside turnOn, while the tap still counts (iOS requires it).
    turnOn(data.meta).then((code) => {
      store.setPush({ subscribed: true, code, subscribed_at: new Date().toISOString() });
      toast("Notifications are on. Now send the code to your helper.");
      renderPush();
    }).catch((e) => { toast(e.message || "Notifications couldn't be turned on."); renderPush(); });
  },
  async "push-share"() {
    const code = store.state.push.code;
    if (!code) { toast("Turn notifications off and on again to make a new code."); return; }
    const result = await shareCode(code);
    if (result === "unavailable") {
      const box = document.getElementById("push-code");
      if (box) { box.closest("details").open = true; box.select(); }
      toast("Sharing isn't available here. Copy the code instead.");
    }
  },
  async "push-copy"() {
    const box = document.getElementById("push-code");
    try { await navigator.clipboard.writeText(`${SHARE_INSTRUCTIONS}\n\n${store.state.push.code}`); toast("Code copied"); }
    catch { if (box) box.select(); toast("Select the code and copy it."); }
  },
  async "push-off"() {
    try { await turnOff(); } catch { /* already off */ }
    store.setPush({ subscribed: false, code: null, subscribed_at: null });
    toast("Notifications are off. Tell your helper they can remove your code.");
    renderPush();
  },
};

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el || !ACTIONS[el.dataset.action]) return;
  if (el.tagName === "A") e.preventDefault();
  ACTIONS[el.dataset.action](el, e);
});

document.addEventListener("submit", async (e) => {
  const form = e.target.closest("[data-form]");
  if (!form) return;
  e.preventDefault();
  const kind = form.dataset.form;
  const fd = new FormData(form);
  if (kind === "trial-filters" || kind === "library") {
    const params = Object.fromEntries([...fd.entries()].filter(([, v]) => v !== ""));
    navigate(buildHash([kind === "library" ? "library" : "trials"], params));
  } else if (kind === "note") {
    store.setNote(form.dataset.type, form.dataset.id, fd.get("note"));
    const msg = form.querySelector(".note-saved");
    if (msg) { msg.hidden = false; setTimeout(() => { msg.hidden = true; }, 2000); }
  } else if (kind === "city") {
    await saveCityFromQuery(String(fd.get("city") || "").trim());
  }
});

document.addEventListener("input", (e) => {
  if (e.target.matches && e.target.matches('[data-input="city"]')) citySuggest(e.target);
});
document.addEventListener("change", (e) => {
  if (e.target.matches && e.target.matches('[data-input="import"]')) importBackup(e.target);
});

// ---------------------------------------------------------------- start
async function start() {
  if (!location.hash) history.replaceState(null, "", "#/");
  registerServiceWorker();
  try {
    await data.loadMeta();
    const disc = document.getElementById("disclaimer-text");
    if (disc && data.meta.disclaimer) disc.textContent = data.meta.disclaimer;
  } catch { /* render() shows the error with a retry button */ }
  state.started = false;
  await render();
  if (!store.state.welcome_seen) showWelcome();
  state.started = true;
  document.documentElement.dataset.ready = "1";
  if (navigator.storage && navigator.storage.persist && Object.keys(store.state.saved).length) {
    navigator.storage.persist().catch(() => {});
  }
}

start();
