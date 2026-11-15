// MSA-C Navigator service worker: offline use, data caching and push notifications.
// Bump VERSION whenever any file in SHELL changes, so phones pick up the new app.

const VERSION = "2026-11-15.1";
const SHELL_CACHE = `msac-shell-${VERSION}`;
const DATA_CACHE = "msac-data-v1";
const META_TIMEOUT_MS = 4000;

const SHELL = [
  "./",
  "index.html",
  "manifest.webmanifest",
  "styles/app.css",
  "js/app.js",
  "js/data.js",
  "js/logic.js",
  "js/push.js",
  "js/screens.js",
  "js/store.js",
  "js/util.js",
  "js/views.js",
  "icons/apple-touch-icon.png",
  "icons/icon-192.png",
  "icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL)));
  // The new version waits until the app asks it to take over ("Update now"), so a page is never half old, half new.
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n.startsWith("msac-shell-") && n !== SHELL_CACHE).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  const msg = event.data || {};
  if (msg.type === "skip-waiting") self.skipWaiting();
  if (msg.type === "prune" && Array.isArray(msg.keep)) event.waitUntil(pruneData(new Set(msg.keep)));
});

async function pruneData(keep) {
  const cache = await caches.open(DATA_CACHE);
  const requests = await cache.keys();
  await Promise.all(requests.filter((r) => !keep.has(r.url)).map((r) => cache.delete(r)));
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}

/** A cached copy, marked so the app can show "Offline: showing data as of …". */
async function markedOffline(response) {
  const headers = new Headers(response.headers);
  headers.set("x-msac-offline", "1");
  return new Response(await response.blob(), { status: response.status, statusText: response.statusText, headers });
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;            // external links are not ours to cache
  const scope = new URL(self.registration.scope);
  const path = url.pathname.slice(scope.pathname.length);

  // App pages: always the cached app shell (routes live after the "#").
  if (req.mode === "navigate") {
    event.respondWith((async () => {
      const cached = await caches.match("index.html", { cacheName: SHELL_CACHE });
      if (cached) return cached;
      return fetch(req);
    })());
    return;
  }

  // meta.json: network first (fresh "data as of"), cached copy when offline or slow.
  if (path === "data/meta.json") {
    event.respondWith((async () => {
      const cache = await caches.open(DATA_CACHE);
      try {
        const fresh = await withTimeout(fetch(req, { cache: "no-store" }), META_TIMEOUT_MS);
        if (fresh.ok) await cache.put(url.href, fresh.clone());
        return fresh;
      } catch {
        const cached = await cache.match(url.href);
        if (cached) return markedOffline(cached);
        return new Response(JSON.stringify({ error: "offline" }), { status: 503, headers: { "Content-Type": "application/json" } });
      }
    })());
    return;
  }

  // Other data files carry ?v=<hash>: cache first, they never change under the same address.
  if (path.startsWith("data/")) {
    event.respondWith((async () => {
      const cache = await caches.open(DATA_CACHE);
      const cached = await cache.match(url.href);
      if (cached) return cached;
      const fresh = await fetch(req);
      if (fresh.ok && url.searchParams.has("v")) await cache.put(url.href, fresh.clone());
      return fresh;
    })());
    return;
  }

  // App shell files: cache first, network as fallback.
  event.respondWith((async () => {
    const cached = await caches.match(req, { cacheName: SHELL_CACHE, ignoreSearch: true });
    return cached || fetch(req);
  })());
});

// ---------------------------------------------------------------- push notifications
// Payload contract: {"title": str, "body": str, "url": "#/trial/NCT…", "tag": str}
self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch { payload = { body: event.data ? event.data.text() : "" }; }
  const title = String(payload.title || "MSA-C Navigator").slice(0, 80);
  const options = {
    body: String(payload.body || "There's an update about MSA-C trials.").slice(0, 300),
    icon: "icons/icon-192.png",
    badge: "icons/icon-192.png",
    tag: payload.tag ? String(payload.tag) : undefined,
    data: { url: typeof payload.url === "string" && payload.url.startsWith("#/") ? payload.url : "#/" },
  };
  // iOS requires every push to show a notification, so this always shows one.
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data && event.notification.data.url ? event.notification.data.url : "#/", self.registration.scope).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if (client.url.startsWith(self.registration.scope)) {
        client.postMessage({ type: "open", url: target });
        return client.focus();
      }
    }
    return self.clients.openWindow(target);
  })());
});
