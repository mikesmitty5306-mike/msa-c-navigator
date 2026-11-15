// Everything personal lives here, on the phone only: home city, saved items and notes, and app settings.
// Stored in localStorage under one versioned key. Nothing here is ever sent anywhere.

import { makeBackup, parseBackup } from "./logic.js";

export const STORAGE_KEY = "msac.v1";
export const NOTE_MAX = 2000;

function emptyState() {
  return {
    version: 1,
    home_city: null,          // { name, region, country, lat, lon, source: "city"|"location" }
    saved: {},                // "trial:NCT…" / "article:pmid:…" -> { type, id, title, url, note, saved_at, last_seen_status }
    welcome_seen: false,
    last_backup_at: null,     // ISO time of the last Export
    last_change_at: null,     // ISO time of the last change to saved items or notes
    push: { subscribed: false, code: null, subscribed_at: null },
  };
}

export class Store {
  /** backend: anything with getItem/setItem/removeItem (window.localStorage in the app, a fake in tests). */
  constructor(backend, now = () => new Date()) {
    this.backend = backend;
    this.now = now;
    this.listeners = new Set();
    this.state = this.load();
  }

  load() {
    try {
      const raw = this.backend.getItem(STORAGE_KEY);
      if (!raw) return emptyState();
      const parsed = JSON.parse(raw);
      return { ...emptyState(), ...parsed, push: { ...emptyState().push, ...(parsed.push || {}) } };
    } catch {
      return emptyState();     // unreadable storage: start fresh rather than break the app
    }
  }

  save() {
    try {
      this.backend.setItem(STORAGE_KEY, JSON.stringify(this.state));
    } catch {
      // Storage full or blocked (for example private browsing): keep working in memory.
    }
    this.listeners.forEach((fn) => fn(this.state));
  }

  onChange(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  stamp() { return this.now().toISOString(); }

  // ---- home city
  get homeCity() { return this.state.home_city; }
  setHomeCity(city) {
    this.state.home_city = city ? { name: city.name, region: city.region || "", country: city.country || "",
      lat: Number(city.lat), lon: Number(city.lon), source: city.source || "city" } : null;
    this.save();
  }

  // ---- saved items
  key(type, id) { return `${type}:${id}`; }
  isSaved(type, id) { return Boolean(this.state.saved[this.key(type, id)]); }
  getSaved(type, id) { return this.state.saved[this.key(type, id)] || null; }
  listSaved(type) {
    return Object.values(this.state.saved).filter((s) => s.type === type)
      .sort((a, b) => String(b.saved_at).localeCompare(String(a.saved_at)));
  }

  /** Toggle; returns the new saved state. `item` = { title, url, status } for a new save. */
  toggleSaved(type, id, item = {}) {
    const k = this.key(type, id);
    if (this.state.saved[k]) {
      delete this.state.saved[k];
    } else {
      this.state.saved[k] = { type, id, title: String(item.title || id).slice(0, 500), url: item.url || null, note: "",
        saved_at: this.stamp(), last_seen_status: item.status || null };
    }
    this.state.last_change_at = this.stamp();
    this.save();
    return Boolean(this.state.saved[k]);
  }

  setNote(type, id, note) {
    const s = this.getSaved(type, id);
    if (!s) return false;
    s.note = String(note || "").replace(/\r\n/g, "\n").trim().slice(0, NOTE_MAX);
    this.state.last_change_at = this.stamp();
    this.save();
    return true;
  }

  /** The person has seen a saved trial's current status (clears its "status changed" badge). */
  markSeen(id, status) {
    const s = this.getSaved("trial", id);
    if (!s || s.last_seen_status === status) return;
    s.last_seen_status = status;
    this.save();
  }

  // ---- welcome, push
  setWelcomeSeen() { this.state.welcome_seen = true; this.save(); }
  resetWelcome() { this.state.welcome_seen = false; this.save(); }
  setPush(push) { this.state.push = { ...this.state.push, ...push }; this.save(); }

  // ---- backup
  exportBackup() {
    const backup = makeBackup(this.state, this.now());
    this.state.last_backup_at = backup.exported_at;
    this.save();
    return backup;
  }

  /** Replace saved items (and the home city, if the backup has one) with a backup's contents. */
  importBackup(text) {
    const parsed = parseBackup(text);
    this.state.saved = parsed.saved;
    if (parsed.home_city) this.state.home_city = { ...parsed.home_city, source: "city" };
    this.state.last_change_at = this.stamp();
    this.save();
    return { saved: Object.keys(parsed.saved).length, home_city: Boolean(parsed.home_city) };
  }

  /** Days since the last backup, if there are unsaved changes worth backing up; otherwise null. */
  backupDue(days = 30) {
    const changed = this.state.last_change_at;
    if (!changed || !Object.keys(this.state.saved).length) return null;
    const last = this.state.last_backup_at;
    if (last && last >= changed) return null;
    const since = last ? (this.now() - new Date(last)) / 86400000 : (this.now() - new Date(changed)) / 86400000;
    return since >= days ? Math.floor(since) : null;
  }

  clearAll() {
    this.state = emptyState();
    this.state.welcome_seen = true;
    this.save();
  }
}
