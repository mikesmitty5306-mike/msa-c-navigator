// Pure app logic (no DOM): labels, trial filters and sorting, library search, city matching,
// saved-trial status changes and the backup file format. Unit-tested in tests/frontend/unit/.

import { haversineKm, norm } from "./util.js";

export const SCHEMA_VERSION = 1;

export const FIT_LABELS = {
  includes_msa_c: "Explicitly includes MSA-C",
  any_msa: "MSA, subtype not restricted",
  msa_p_focused: "Focused on MSA-P (check eligibility)",
  excludes_msa_c: "Excludes MSA-C",
};
export const FIT_ORDER = { includes_msa_c: 0, any_msa: 1, msa_p_focused: 2, excludes_msa_c: 3 };
export const EVIDENCE_LABELS = {
  clinical: "Clinical trial", human_observational: "Human observational", postmortem: "Human neuropathology",
  animal: "Animal model", in_vitro: "Cell / in vitro", review: "Review", other: "Other",
};
export const SUBTYPE_LABELS = { msa_c: "MSA-C", msa_p: "MSA-P", msa_general: "MSA (general)" };
export const OUTCOME_LABELS = {
  positive: "Treatment worked", negative: "Treatment did not work", mixed: "Mixed result",
  unclear: "Result unclear", safety: "Safety only",
};
export const OUTCOME_GROUPS = {
  positive: "Positive: reported a benefit", negative: "Negative: reported no benefit",
  mixed: "Mixed: benefit on some measures, not others", unclear: "Unclear: no clear verdict (including safety-only studies)",
};
export const CATEGORY_LABELS = { human: "Human", animal: "Animal models", in_vitro: "In vitro (cell/lab)", trials: "Trials" };
export const CATEGORY_SLUGS = { human: "human", animal: "animal", "in-vitro": "in_vitro", trials: "trials" };
export const TRIAL_GROUP_LABELS = {
  recruiting: "Recruiting", completed: "Completed", stopped: "Stopped early", ongoing: "Ongoing (not recruiting) or status unknown",
};
export const STATUS_LABELS = {
  RECRUITING: "Recruiting", NOT_YET_RECRUITING: "Not yet recruiting", ENROLLING_BY_INVITATION: "Enrolling by invitation",
  ACTIVE_NOT_RECRUITING: "Active, not recruiting", COMPLETED: "Completed", TERMINATED: "Terminated", WITHDRAWN: "Withdrawn",
  SUSPENDED: "Suspended", UNKNOWN: "Unknown", AVAILABLE: "Available", NO_LONGER_AVAILABLE: "No longer available",
};
export const statusLabel = (s) => STATUS_LABELS[s] || String(s || "Unknown").replace(/_/g, " ").toLowerCase();

// ---------------------------------------------------------------- trials
/** Nearest site distance in km from a {lat, lon} point. Prefers open sites when `openOnly`. */
export function nearestKm(trial, home, openOnly = true) {
  if (!home || !Array.isArray(trial.sites)) return null;
  let best = null;
  for (const [lat, lon, , , open] of trial.sites) {
    if (lat === null || lon === null || lat === undefined || lon === undefined) continue;
    if (openOnly && trial.open && !open) continue;
    const d = haversineKm(home.lat, home.lon, lat, lon);
    if (best === null || d < best) best = d;
  }
  return best;
}

export function trialCountries(trials) {
  const set = new Set();
  trials.forEach((t) => (t.sites || []).forEach((s) => { if (s[3]) set.add(s[3]); }));
  return [...set].sort((a, b) => a.localeCompare(b));
}

/**
 * Filter and sort the compact trial list.
 * filters: { status: "open"|"active"|"all", fit, mechanism, country, age, sex, sort: "nearest"|"promising"|"match" }
 * Returns [{ trial, km }].
 */
export function filterTrials(trials, filters = {}, home = null) {
  const status = filters.status || "open";
  const age = filters.age === "" || filters.age === undefined || filters.age === null ? null : Number(filters.age);
  const rows = [];
  for (const t of trials) {
    if (status === "open" && !t.open) continue;
    if (status === "active" && !t.active) continue;
    if (filters.fit && t.fit !== filters.fit) continue;
    if (filters.mechanism && !(t.mechanisms || []).includes(filters.mechanism)) continue;
    if (filters.country && !(t.sites || []).some((s) => s[3] === filters.country)) continue;
    if (age !== null && Number.isFinite(age)) {
      if (t.min_age !== null && t.min_age !== undefined && age < t.min_age) continue;
      if (t.max_age !== null && t.max_age !== undefined && age > t.max_age) continue;
    }
    if (filters.sex && t.sex && t.sex !== "ALL" && t.sex !== filters.sex) continue;
    rows.push({ trial: t, km: nearestKm(t, home, status === "open") });
  }
  const sort = filters.sort || (home ? "nearest" : "match");
  const byMatch = (a, b) => (FIT_ORDER[a.trial.fit] ?? 9) - (FIT_ORDER[b.trial.fit] ?? 9)
    || (b.trial.type === "INTERVENTIONAL") - (a.trial.type === "INTERVENTIONAL")
    || (b.trial.max_phase || 0) - (a.trial.max_phase || 0) || a.trial.id.localeCompare(b.trial.id);
  if (sort === "nearest" && home) {
    rows.sort((a, b) => (a.km ?? Infinity) - (b.km ?? Infinity) || byMatch(a, b));
  } else if (sort === "promising") {
    rows.sort((a, b) => (a.trial.rank?.position ?? Infinity) - (b.trial.rank?.position ?? Infinity) || byMatch(a, b));
  } else {
    rows.sort(byMatch);
  }
  return rows;
}

// ---------------------------------------------------------------- library
/** Turn shard rows into objects using the index's column list. */
export function rowsToObjects(columns, rows) {
  return rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]])));
}

/**
 * Filter papers. filters: { q, mechanism, evidence, subtype, outcome, preprints: ""|"exclude"|"only", year_from }.
 * Search matches every word against title, journal, first author and keywords (accent-insensitive).
 */
export function filterLibrary(papers, filters = {}) {
  const words = norm(filters.q || "").split(/\s+/).filter(Boolean);
  const yearFrom = Number(filters.year_from) || null;
  const wantOutcome = filters.outcome === "unclear" ? ["unclear", "safety"] : filters.outcome ? [filters.outcome] : null;
  const out = papers.filter((p) => {
    if (filters.mechanism && !(p.mechanisms || []).includes(filters.mechanism)) return false;
    if (filters.evidence && p.evidence !== filters.evidence) return false;
    if (filters.subtype && p.subtype !== filters.subtype) return false;
    if (wantOutcome && !wantOutcome.includes(p.outcome)) return false;
    if (filters.preprints === "only" && !p.preprint) return false;
    if (filters.preprints === "exclude" && p.preprint) return false;
    if (yearFrom && (p.year || 0) < yearFrom) return false;
    if (words.length) {
      const hay = norm(`${p.title} ${p.journal} ${p.first_author || ""} ${p.keywords || ""}`);
      if (!words.every((w) => hay.includes(w))) return false;
    }
    return true;
  });
  return out.sort((a, b) => (b.year || 0) - (a.year || 0) || String(a.uid).localeCompare(String(b.uid)));
}

// ---------------------------------------------------------------- cities
const COORDS = /^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$/;

/** City file key for a query: first letter of the accent-free name, or "other". */
export function cityKey(query) {
  const n = norm(String(query || "").split(",")[0]);
  return n && n[0] >= "a" && n[0] <= "z" ? n[0] : "other";
}

export function parseCoords(query) {
  const m = COORDS.exec(String(query || ""));
  if (!m) return null;
  const lat = Number(m[1]);
  const lon = Number(m[2]);
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { name: `${lat.toFixed(3)}, ${lon.toFixed(3)}`, region: "", country: "coordinates", lat, lon };
}

/**
 * Same rules as the Python app and the Beta page: name prefix, optional ", region/country" qualifier,
 * exact names first, then larger places (rows are ordered by population).
 */
export function searchCities(rows, query, limit = 8) {
  const coords = parseCoords(query);
  if (coords) return [coords];
  const [name, ...rest] = String(query || "").split(",");
  const n = norm(name);
  const q = norm(rest.join(","));
  if (n.length < 2) return [];
  const exact = [];
  const prefix = [];
  for (const [cname, region, country, lat, lon] of rows) {
    const key = norm(cname);
    if (!key.startsWith(n)) continue;
    if (q && !norm(region).startsWith(q) && !norm(country).startsWith(q)) continue;
    const city = { name: cname, region, country, lat, lon };
    (key === n ? exact : prefix).push(city);
    if (exact.length >= limit) break;
  }
  return [...exact, ...prefix].slice(0, limit);
}

export function cityLabel(c) {
  return [c.name, c.region, c.country].filter((x) => x && x !== "coordinates").join(", ") || c.name;
}

// ---------------------------------------------------------------- saved trials: status changes
/**
 * Has a saved trial's status changed since the person last saw it?
 * Returns { from, to, date } or null. Uses the phone's last_seen_status first; falls back to the build's
 * status_changed when the change happened after the item was saved.
 */
export function savedStatusChange(saved, trial) {
  if (!trial) return null;
  if (saved.last_seen_status && saved.last_seen_status !== trial.status) {
    const date = trial.status_changed && trial.status_changed.to === trial.status ? trial.status_changed.date : null;
    return { from: saved.last_seen_status, to: trial.status, date };
  }
  return null;
}

// ---------------------------------------------------------------- backup file
export const BACKUP_APP = "msac-navigator";
export const BACKUP_VERSION = 1;

export function makeBackup(state, now = new Date()) {
  return {
    app: BACKUP_APP, backup_version: BACKUP_VERSION, exported_at: now.toISOString().replace(/\.\d{3}Z$/, "Z"),
    home_city: state.home_city || null,
    saved: Object.values(state.saved || {}).map((s) => ({
      type: s.type, id: s.id, title: s.title || "", url: s.url || null, note: s.note || "",
      saved_at: s.saved_at || null, last_seen_status: s.last_seen_status || null,
    })),
  };
}

/** Validate an imported backup. Returns { home_city, saved } or throws Error with a plain-language message. */
export function parseBackup(text) {
  let data;
  try {
    data = typeof text === "string" ? JSON.parse(text) : text;
  } catch {
    throw new Error("That file isn't a MSA-C Navigator backup (it isn't valid JSON).");
  }
  if (!data || data.app !== BACKUP_APP) throw new Error("That file isn't a MSA-C Navigator backup.");
  if (data.backup_version !== BACKUP_VERSION) throw new Error("That backup was made by a different version of the app.");
  const saved = {};
  for (const s of Array.isArray(data.saved) ? data.saved : []) {
    if (!s || (s.type !== "trial" && s.type !== "article") || typeof s.id !== "string" || !s.id) continue;
    saved[`${s.type}:${s.id}`] = {
      type: s.type, id: s.id.slice(0, 200), title: String(s.title || "").slice(0, 500), url: s.url ? String(s.url) : null,
      note: String(s.note || "").slice(0, 2000), saved_at: s.saved_at || null, last_seen_status: s.last_seen_status || null,
    };
  }
  let home = null;
  const h = data.home_city;
  if (h && Number.isFinite(Number(h.lat)) && Number.isFinite(Number(h.lon)) && Math.abs(h.lat) <= 90 && Math.abs(h.lon) <= 180) {
    home = { name: String(h.name || "Home").slice(0, 120), region: String(h.region || "").slice(0, 120),
      country: String(h.country || "").slice(0, 120), lat: Number(h.lat), lon: Number(h.lon) };
  }
  return { home_city: home, saved };
}
