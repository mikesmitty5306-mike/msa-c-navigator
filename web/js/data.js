// Loads the published JSON data (the contract in reports/static_plan.txt).
// meta.json is always fetched fresh (the service worker falls back to its copy when offline);
// every other file is fetched as <name>?v=<hash> so it can be cached until it changes.

import { SCHEMA_VERSION } from "./logic.js";

export class DataError extends Error {
  constructor(message, kind = "network") { super(message); this.kind = kind; }
}

export class DataClient {
  /** base: URL prefix of the data folder ("data/"); fetchFn: window.fetch (or a fake in tests). */
  constructor(base = "data/", fetchFn = (...a) => fetch(...a)) {
    this.base = base;
    this.fetch = fetchFn;
    this.meta = null;
    this.offline = false;
    this.cache = new Map();
  }

  async getJson(url) {
    let resp;
    try {
      resp = await this.fetch(url, { cache: "no-cache" });
    } catch {
      throw new DataError("Can't reach the data. Check the internet connection, then tap Refresh.");
    }
    if (resp.status === 404) throw new DataError("That information isn't available.", "missing");
    if (!resp.ok) throw new DataError(`The data couldn't be loaded (error ${resp.status}). Try Refresh.`);
    if (resp.headers && typeof resp.headers.get === "function" && resp.headers.get("x-msac-offline") === "1") this.offline = true;
    let data;
    try {
      data = await resp.json();
    } catch {
      throw new DataError("The data file is damaged. Try Refresh.", "format");
    }
    if (!data || data.schema_version !== SCHEMA_VERSION) {
      throw new DataError("This app needs updating to read the latest data. Close it and open it again.", "version");
    }
    return data;
  }

  /** Load meta.json (always revalidated). `force` also clears cached files so they are re-read. */
  async loadMeta(force = false) {
    this.offline = false;
    const meta = await this.getJson(`${this.base}meta.json`);
    if (force || !this.meta || this.meta.build_id !== meta.build_id) this.cache.clear();
    this.meta = meta;
    return meta;
  }

  versionOf(name) {
    const f = this.meta && this.meta.files && this.meta.files[name];
    return f && f.sha256 ? f.sha256.slice(0, 12) : null;
  }

  has(name) { return Boolean(this.meta && this.meta.files && this.meta.files[name]); }

  urlFor(name) {
    const v = this.versionOf(name);
    return `${this.base}${name}${v ? `?v=${v}` : ""}`;
  }

  /** Fetch a data file by its contract name, e.g. "grades.json" or "trials/NCT01.json". */
  async get(name) {
    if (!this.meta) await this.loadMeta();
    if (this.meta.files && !this.meta.files[name]) throw new DataError("That information isn't available.", "missing");
    const url = this.urlFor(name);
    if (!this.cache.has(url)) {
      const p = this.getJson(url).catch((e) => { this.cache.delete(url); throw e; });
      this.cache.set(url, p);
    }
    return this.cache.get(url);
  }

  /** The current list of hashed data URLs (sent to the service worker so it can drop old copies). */
  currentUrls() {
    if (!this.meta || !this.meta.files) return [];
    return Object.keys(this.meta.files).map((n) => this.urlFor(n));
  }

  /** "13 November 2026" for the newest refresh across sources (or null). */
  dataAsOf() {
    const s = (this.meta && this.meta.sources) || {};
    const times = Object.values(s).map((x) => x && (x.refreshed_at || x.checked_at)).filter(Boolean);
    const backend = (this.meta && this.meta.data_as_of) || {};          // written by msac.maintenance.write_meta
    Object.values(backend).forEach((t) => { if (typeof t === "string") times.push(t); });
    times.sort((a, b) => new Date(a) - new Date(b));
    return times.length ? times[times.length - 1] : (this.meta && this.meta.generated_at) || null;
  }
}
