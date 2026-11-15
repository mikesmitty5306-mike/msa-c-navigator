// Unit tests for web/js/util.js and web/js/logic.js.  Run: node --test tests/frontend/unit
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { abstractShard, buildHash, esc, fmtDate, haversineKm, html, miles, norm, parseHash, urlBase64ToUint8Array } from "../../../web/js/util.js";
import {
  cityKey, filterLibrary, filterTrials, makeBackup, nearestKm, parseBackup, rowsToObjects, savedStatusChange, searchCities,
} from "../../../web/js/logic.js";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = (name) => JSON.parse(readFileSync(join(here, "..", "fixtures", "data", name), "utf8"));
const BOSTON = { lat: 42.358, lon: -71.06 };

// ---------------------------------------------------------------- util
test("html escapes data but keeps nested safe html", () => {
  const evil = '<img src=x onerror="alert(1)">';
  const out = String(html`<p title="${evil}">${evil}${html`<b>${"&"}</b>`}${["<i>", html`<u>ok</u>`]}</p>`);
  assert.equal(out, '<p title="&lt;img src=x onerror=&quot;alert(1)&quot;&gt;">&lt;img src=x onerror=&quot;alert(1)&quot;&gt;<b>&amp;</b>&lt;i&gt;<u>ok</u></p>');
  assert.equal(esc(null), "");
  assert.equal(String(html`${null}${undefined}${false}${0}`), "0");
});

test("norm, distances, miles and dates", () => {
  assert.equal(norm("  Montréal "), "montreal");
  const km = haversineKm(42.358, -71.06, 40.713, -74.006);
  assert.ok(km > 300 && km < 310, `Boston-NYC ${km}`);
  assert.equal(miles(km), 190);
  assert.equal(miles(null), null);
  assert.equal(fmtDate("2026-11-13T21:40:00Z"), "13 November 2026");
  assert.equal(fmtDate("2026-01-02"), "2 January 2026");
  assert.equal(fmtDate("not a date"), "");
  assert.equal(fmtDate(null), "");
});

test("hash routes parse and build", () => {
  const { parts, query } = parseHash("#/trial/NCT01?fit=any_msa&age=60");
  assert.deepEqual(parts, ["trial", "NCT01"]);
  assert.equal(query.get("fit"), "any_msa");
  assert.equal(buildHash(["trials"], { fit: "any_msa", age: "", sex: null }), "#/trials?fit=any_msa");
  assert.equal(buildHash(["grades", "a b"]), "#/grades/a%20b");
  assert.deepEqual(parseHash("").parts, []);
});

test("VAPID key conversion and abstract shard names match the build", async () => {
  const key = urlBase64ToUint8Array("BAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0-P0A");
  assert.equal(key.length, 65);
  assert.equal(key[0], 4);
  assert.equal(key[64], 64);
  assert.equal(await abstractShard("pmid:111"), "17");           // same as Python hashlib.sha1(...).hexdigest()[:2]
  assert.equal(await abstractShard("doi:10.1101/623538"), "d4");
});

// ---------------------------------------------------------------- trials
test("trial filters on the fixture data", () => {
  const { trials } = fixture("trials.json");
  const meta = fixture("meta.json");
  assert.equal(filterTrials(trials, { status: "open" }).length, meta.counts.open_trials);
  assert.equal(filterTrials(trials, { status: "all" }).length, trials.length);
  const named = filterTrials(trials, { status: "open", fit: "includes_msa_c" });
  assert.ok(named.length > 0 && named.every((r) => r.trial.fit === "includes_msa_c"));
  const young = filterTrials(trials, { status: "all", age: "20" });
  assert.ok(young.every((r) => r.trial.min_age === null || r.trial.min_age <= 20));
  const japan = filterTrials(trials, { status: "all", country: "Japan" });
  assert.ok(japan.length > 0 && japan.every((r) => r.trial.sites.some((s) => s[3] === "Japan")));
});

test("nearest first from a home city, and most promising first", () => {
  const { trials } = fixture("trials.json");
  const rows = filterTrials(trials, { status: "open", sort: "nearest" }, BOSTON);
  const kms = rows.map((r) => r.km).filter((k) => k !== null);
  assert.deepEqual(kms, [...kms].sort((a, b) => a - b));
  assert.ok(rows[0].km < 5, "a Boston site should come first");
  const promising = filterTrials(trials, { status: "open", sort: "promising" });
  const ranked = promising.filter((r) => r.trial.rank).map((r) => r.trial.rank.position);
  assert.deepEqual(ranked, [...ranked].sort((a, b) => a - b));
  assert.equal(nearestKm({ sites: [[null, null, "", "", true]], open: true }, BOSTON), null);
  assert.equal(nearestKm(trials[0], null), null);
});

// ---------------------------------------------------------------- library
test("library search and filters", () => {
  const index = fixture("library/index.json");
  const papers = index.shards.flatMap((s) => rowsToObjects(index.columns, fixture(`library/${s.name}.json`).rows));
  assert.ok(papers.length > 100);
  const all = filterLibrary(papers, {});
  assert.deepEqual(all.map((p) => p.year || 0), [...all.map((p) => p.year || 0)].sort((a, b) => b - a));
  const hits = filterLibrary(papers, { q: "SYNUCLEIN multiple" });
  assert.ok(hits.length > 0 && hits.every((p) => norm(`${p.title} ${p.journal} ${p.first_author} ${p.keywords}`).includes("synuclein")));
  const unclear = filterLibrary(papers, { outcome: "unclear" });
  assert.ok(unclear.every((p) => p.outcome === "unclear" || p.outcome === "safety"));
  assert.ok(filterLibrary(papers, { preprints: "only" }).every((p) => p.preprint));
  assert.ok(filterLibrary(papers, { year_from: "2026" }).every((p) => p.year >= 2026));
  assert.equal(filterLibrary(papers, { q: "zzqqxxnotaword" }).length, 0);
});

// ---------------------------------------------------------------- cities
test("city lookup follows the same rules as the Python app", () => {
  const b = fixture("cities/b.json").rows;
  const p = fixture("cities/p.json").rows;
  assert.deepEqual(searchCities(b, "Boston")[0], { name: "Boston", region: "Massachusetts", country: "United States", lat: 42.358, lon: -71.06 });
  assert.equal(searchCities(p, "Paris")[0].country, "France");
  // the fixture keeps only the 400 largest places per letter, so test the ", region" rule on a small list
  const paris = [["Paris", "Île-de-France", "France", 48.853, 2.349], ["Paris", "Texas", "United States", 33.661, -95.556]];
  assert.equal(searchCities(paris, "paris, tex")[0].region, "Texas");
  assert.equal(searchCities(paris, "Paris, united")[0].region, "Texas");
  assert.equal(searchCities(b, "42.36, -71.06")[0].country, "coordinates");
  assert.deepEqual(searchCities(b, "b"), []);
  assert.deepEqual(searchCities(b, "99, 500"), []);
  assert.equal(searchCities(b, "Ba", 8).length, 8);
  assert.equal(cityKey("Évry"), "e");
  assert.equal(cityKey("1st town"), "other");
});

// ---------------------------------------------------------------- saved and backup
test("status change badge for a saved trial", () => {
  const trial = { status: "ACTIVE_NOT_RECRUITING", status_changed: { from: "RECRUITING", to: "ACTIVE_NOT_RECRUITING", date: "2026-11-14" } };
  assert.deepEqual(savedStatusChange({ last_seen_status: "RECRUITING" }, trial), { from: "RECRUITING", to: "ACTIVE_NOT_RECRUITING", date: "2026-11-14" });
  assert.equal(savedStatusChange({ last_seen_status: "ACTIVE_NOT_RECRUITING" }, trial), null);
  assert.equal(savedStatusChange({ last_seen_status: "RECRUITING" }, null), null);
});

test("backup file round trip and validation", () => {
  const state = { home_city: { name: "Boston", region: "Massachusetts", country: "United States", lat: 42.358, lon: -71.06 },
    saved: { "trial:NCT1": { type: "trial", id: "NCT1", title: "T", url: null, note: "n", saved_at: "2026-11-14T00:00:00Z", last_seen_status: "RECRUITING" } } };
  const backup = makeBackup(state, new Date("2026-11-14T10:00:00.123Z"));
  assert.equal(backup.exported_at, "2026-11-14T10:00:00Z");
  assert.equal(backup.app, "msac-navigator");
  const back = parseBackup(JSON.stringify(backup));
  assert.deepEqual(back.saved["trial:NCT1"].note, "n");
  assert.equal(back.home_city.name, "Boston");
  assert.throws(() => parseBackup("not json"), /isn't a MSA-C Navigator backup/);
  assert.throws(() => parseBackup('{"app":"other"}'), /isn't a MSA-C Navigator backup/);
  assert.throws(() => parseBackup('{"app":"msac-navigator","backup_version":9}'), /different version/);
  const messy = parseBackup({ app: "msac-navigator", backup_version: 1, home_city: { lat: 999, lon: 0 },
    saved: [{ type: "bogus", id: "x" }, { type: "article", id: "", title: "x" }, { type: "article", id: "pmid:1", note: "x".repeat(3000) }] });
  assert.equal(messy.home_city, null);
  assert.deepEqual(Object.keys(messy.saved), ["article:pmid:1"]);
  assert.equal(messy.saved["article:pmid:1"].note.length, 2000);
});
