// Unit tests for web/js/store.js (phone storage) and web/js/data.js (data loader).
import { test } from "node:test";
import assert from "node:assert/strict";

import { Store, STORAGE_KEY } from "../../../web/js/store.js";
import { DataClient, DataError } from "../../../web/js/data.js";

function fakeStorage(initial = {}) {
  const data = { ...initial };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, removeItem: (k) => { delete data[k]; } };
}

function clock(start = "2026-11-14T10:00:00Z") {
  let t = new Date(start).getTime();
  const fn = () => new Date(t);
  fn.advanceDays = (d) => { t += d * 86400000; };
  return fn;
}

// ---------------------------------------------------------------- store
test("saving, notes and seen status persist in storage", () => {
  const backend = fakeStorage();
  const s = new Store(backend, clock());
  assert.equal(s.toggleSaved("trial", "NCT1", { title: "Trial one", status: "RECRUITING" }), true);
  assert.equal(s.isSaved("trial", "NCT1"), true);
  assert.equal(s.setNote("trial", "NCT1", "  Ask Dr Lee.\r\nFriday  "), true);
  assert.equal(s.getSaved("trial", "NCT1").note, "Ask Dr Lee.\nFriday");
  assert.equal(s.setNote("trial", "NOPE", "x"), false);
  s.setNote("trial", "NCT1", "x".repeat(5000));
  assert.equal(s.getSaved("trial", "NCT1").note.length, 2000);
  s.markSeen("NCT1", "COMPLETED");
  const reloaded = new Store(backend, clock());
  assert.equal(reloaded.getSaved("trial", "NCT1").last_seen_status, "COMPLETED");
  assert.equal(reloaded.toggleSaved("trial", "NCT1"), false);
  assert.equal(new Store(backend).isSaved("trial", "NCT1"), false);
});

test("home city and push state", () => {
  const s = new Store(fakeStorage());
  s.setHomeCity({ name: "Boston", region: "Massachusetts", country: "United States", lat: "42.358", lon: -71.06 });
  assert.deepEqual(s.homeCity, { name: "Boston", region: "Massachusetts", country: "United States", lat: 42.358, lon: -71.06, source: "city" });
  s.setHomeCity(null);
  assert.equal(s.homeCity, null);
  s.setPush({ subscribed: true, code: "{}" });
  assert.equal(s.state.push.subscribed, true);
});

test("unreadable storage starts fresh instead of breaking", () => {
  const s = new Store(fakeStorage({ [STORAGE_KEY]: "{not json" }));
  assert.deepEqual(s.state.saved, {});
  assert.equal(s.state.welcome_seen, false);
  const failing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("full"); } };
  const s2 = new Store(failing);
  assert.equal(s2.toggleSaved("article", "pmid:1"), true);       // keeps working in memory
});

test("export and import backups, and the backup reminder", () => {
  const now = clock();
  const s = new Store(fakeStorage(), now);
  assert.equal(s.backupDue(), null);                              // nothing saved yet
  s.toggleSaved("article", "pmid:1", { title: "Paper" });
  now.advanceDays(31);
  assert.equal(s.backupDue(), 31);
  const backup = s.exportBackup();
  assert.equal(s.backupDue(), null);
  assert.equal(backup.saved.length, 1);
  const other = new Store(fakeStorage(), now);
  assert.deepEqual(other.importBackup(JSON.stringify(backup)), { saved: 1, home_city: false });
  assert.equal(other.isSaved("article", "pmid:1"), true);
  assert.throws(() => other.importBackup("{}"), /isn't a MSA-C Navigator backup/);
  other.clearAll();
  assert.deepEqual(other.state.saved, {});
  assert.equal(other.state.welcome_seen, true);
});

// ---------------------------------------------------------------- data loader
function fakeFetch(files, { offline = false } = {}) {
  const calls = [];
  const fn = async (url) => {
    calls.push(url);
    const name = url.replace(/^data\//, "").split("?")[0];
    if (!(name in files)) return { ok: false, status: 404, headers: { get: () => null }, json: async () => ({}) };
    return { ok: true, status: 200, headers: { get: (h) => (offline && h === "x-msac-offline" ? "1" : null) }, json: async () => files[name] };
  };
  fn.calls = calls;
  return fn;
}

const META = { schema_version: 1, build_id: "b1", generated_at: "2026-11-14T06:17:42Z",
  sources: { pubmed: { refreshed_at: "2026-11-14T06:12:00Z" }, clinicaltrials: { refreshed_at: "2026-11-13T21:40:00Z", checked_at: "2026-11-14T06:17:05Z" } },
  files: { "grades.json": { bytes: 10, sha256: "abcdef0123456789" }, "old.json": { bytes: 1, sha256: "ffff" } } };

test("data files are fetched with their content hash and cached", async () => {
  const fetch = fakeFetch({ "meta.json": META, "grades.json": { schema_version: 1, mechanisms: [] } });
  const d = new DataClient("data/", fetch);
  await d.loadMeta();
  const g1 = await d.get("grades.json");
  const g2 = await d.get("grades.json");
  assert.equal(g1, g2);
  assert.deepEqual(fetch.calls, ["data/meta.json", "data/grades.json?v=abcdef012345"]);
  assert.equal(d.dataAsOf(), "2026-11-14T06:12:00Z");
  assert.deepEqual(d.currentUrls(), ["data/grades.json?v=abcdef012345", "data/old.json?v=ffff"]);
});

test("missing files, wrong schema and offline copies are reported plainly", async () => {
  const d = new DataClient("data/", fakeFetch({ "meta.json": META, "old.json": { schema_version: 2 } }, { offline: true }));
  await d.loadMeta();
  assert.equal(d.offline, true);
  await assert.rejects(d.get("nope.json"), (e) => e instanceof DataError && e.kind === "missing");
  await assert.rejects(d.get("old.json"), (e) => e.kind === "version" && /needs updating/.test(e.message));
  const down = new DataClient("data/", async () => { throw new TypeError("network"); });
  await assert.rejects(down.loadMeta(), (e) => e.kind === "network" && /Check the internet connection/.test(e.message));
});

test("'Data as of' also reads the backend's data_as_of map", async () => {
  const meta = { ...META, sources: {}, data_as_of: { clinicaltrials: "2026-11-14T17:32:02+00:00", pubmed: "2026-11-13T20:41:52+00:00" } };
  const d = new DataClient("data/", fakeFetch({ "meta.json": meta }));
  await d.loadMeta();
  assert.equal(d.dataAsOf(), "2026-11-14T17:32:02+00:00");
});
