// Small shared helpers. No DOM access here, so they can be unit-tested in Node.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** Marker for HTML that is already safe (built by the html`` tag). */
export class SafeHtml {
  constructor(text) { this.text = text; }
  toString() { return this.text; }
}

function render(value) {
  if (value === null || value === undefined || value === false) return "";
  if (value instanceof SafeHtml) return value.text;
  if (Array.isArray(value)) return value.map(render).join("");
  return esc(value);
}

/** Tagged template: every interpolated value is escaped unless it is itself SafeHtml. */
export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((v, i) => { out += render(v) + strings[i + 1]; });
  return new SafeHtml(out);
}

/** Lower-case, accent-free text for matching ("Montréal" -> "montreal"). */
export function norm(text) {
  return String(text ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
}

export const KM_PER_MILE = 1.609344;

export function haversineKm(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180;
  const dp = (lat2 - lat1) * r;
  const dl = (lon2 - lon1) * r;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dl / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(a));
}

export function miles(km) {
  return km === null || km === undefined ? null : Math.round(km / KM_PER_MILE);
}

export function fmtMiles(km) {
  const mi = miles(km);
  return mi === null ? "" : `${mi.toLocaleString("en-US")} mi`;
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];

/** "2026-11-13T21:40:00Z" -> "13 November 2026" (UTC date). Returns "" for missing or invalid input. */
export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(String(iso).length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

/** Parse "#/trials/NCT1?fit=x" into { parts: ["trials", "NCT1"], query: URLSearchParams }. */
export function parseHash(hash) {
  const h = String(hash || "").replace(/^#/, "");
  const [path, qs = ""] = h.split("?");
  const parts = path.split("/").filter(Boolean).map((p) => decodeURIComponent(p));
  return { parts, query: new URLSearchParams(qs) };
}

export function buildHash(parts, params) {
  const path = "#/" + parts.map((p) => encodeURIComponent(p)).join("/");
  const q = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => { if (v !== null && v !== undefined && v !== "") q.set(k, v); });
  const qs = q.toString();
  return qs ? `${path}?${qs}` : path;
}

/** Convert a base64url VAPID key into the Uint8Array that pushManager.subscribe expects. */
export function urlBase64ToUint8Array(b64) {
  const padded = (b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(padded);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

/** First two hex characters of sha1(uid): the abstracts shard name (same rule as the build). */
export async function abstractShard(uid) {
  const bytes = new TextEncoder().encode(uid);
  const digest = await crypto.subtle.digest("SHA-1", bytes);
  return Array.from(new Uint8Array(digest).slice(0, 1), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}
