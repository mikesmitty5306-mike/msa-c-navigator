// Shared building blocks for screens. All output goes through the html`` tag, which escapes data.

import { html, fmtMiles, plural } from "./util.js";
import { CATEGORY_LABELS, EVIDENCE_LABELS, FIT_LABELS, OUTCOME_LABELS } from "./logic.js";

const MODIFIER_TEXT = { "+": "+", "-": "−" };
const MODIFIER_WORDS = { "+": " plus", "-": " minus" };

/** "B+", "B−" or "C": the letter plus its peer-review modifier, when it has one. */
export function gradeLabel(m) {
  const g = m.grade || "n/a";
  return g + (MODIFIER_TEXT[m.modifier] || "");
}

export function gradeBadge(m, meanings, small = false) {
  const g = m.grade || "n/a";
  const cls = g === "n/a" ? "na" : g;
  const label = gradeLabel(m);
  return html`<span class="grade grade-${cls}${small ? " sm" : ""}${label.length > 1 && g !== "n/a" ? " mod" : ""}"
    title="Grade ${label}: ${meanings[g] || ""}" aria-label="Grade ${g}${MODIFIER_WORDS[m.modifier] || ""}">${label}</span>`;
}

export function fitBadge(fit) {
  return html`<span class="badge fit-${fit}">${FIT_LABELS[fit] || fit}</span>`;
}

export function statusBadge(t) {
  const cls = t.open ? "open" : t.active ? "active" : "closed";
  return html`<span class="badge ${cls}">${t.status_label}</span>`;
}

/** Star button: saves or unsaves in place (handled by app.js through data-action="star"). */
export function star(store, type, id, title, url, status) {
  const on = store.isSaved(type, id);
  return html`<button type="button" class="star" data-action="star" data-type="${type}" data-id="${id}"
    data-title="${title || ""}" data-url="${url || ""}" data-status="${status || ""}" aria-pressed="${on ? "true" : "false"}"
    aria-label="${on ? "Remove from saved" : "Save"}: ${title || id}" title="${on ? "Saved" : "Save"}">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3.2 2.7 5.5 6.1.9-4.4 4.3 1 6.1L12 17.1 6.6 20l1-6.1-4.4-4.3 6.1-.9z"/></svg></button>`;
}

export function resultsSummary(m) {
  const tile = (cat, slug) => {
    const r = m.results[cat];
    return html`<a class="result-tile" href="#/grades/${m.id}/${slug}"
      aria-label="${CATEGORY_LABELS[cat]}: ${r.positive} positive, ${r.negative} negative, ${r.mixed} mixed, ${r.unclear} unclear. See the studies.">
      <span class="tile-label">${CATEGORY_LABELS[cat]} <span class="tile-go" aria-hidden="true">›</span></span>
      <span class="tile-counts"><span class="pos"><b>${r.positive}</b> positive</span><span class="neg"><b>${r.negative}</b> negative</span>
        <span><b>${r.mixed}</b> mixed</span><span><b>${r.unclear}</b> unclear</span></span></a>`;
  };
  const t = m.trials;
  return html`<div class="results">${tile("human", "human")}${tile("animal", "animal")}${tile("in_vitro", "in-vitro")}
    <a class="result-tile" href="#/grades/${m.id}/trials"
       aria-label="Trials: ${t.recruiting} recruiting, ${t.completed} completed, ${t.stopped} stopped. See the trials.">
      <span class="tile-label">Trials <span class="tile-go" aria-hidden="true">›</span></span>
      <span class="tile-counts"><span><b>${t.recruiting}</b> recruiting</span><span><b>${t.completed}</b> completed</span>
        <span class="neg"><b>${t.stopped}</b> stopped</span>${t.ongoing ? html`<span><b>${t.ongoing}</b> ongoing</span>` : ""}</span></a></div>`;
}

export function trialRow(store, t, km = null, extra = "") {
  return html`<li class="trial" id="trial-${t.id}">
    <div class="item-head"><a href="#/trial/${t.id}">${t.title}</a>${star(store, "trial", t.id, t.title, null, t.status)}</div>
    <div class="meta">${t.id} · ${t.phase} · ${t.sponsor}${km !== null && km !== undefined ? html` · nearest site <strong>${fmtMiles(km)}</strong>` : ""}</div>
    <div>${statusBadge(t)} ${fitBadge(t.fit)}</div>${extra}</li>`;
}

/** A paper row (study object from the contract, or a library row). External link opens outside the app. */
export function paperRow(store, p, { grades = null, tags = null } = {}) {
  const outcome = ["positive", "negative", "mixed"].includes(p.outcome)
    ? html`<span class="badge outcome-${p.outcome}">${OUTCOME_LABELS[p.outcome]}</span>` : "";
  const mechs = tags || p.mechanisms || [];
  const chips = grades && mechs.length ? html`<div>${mechs.map((id) => {
    const m = grades.byId[id];
    return m ? html`<a class="chip" href="#/grades/${id}">${m.name}${m.grade !== "n/a" ? ` · ${gradeLabel(m)}` : ""}</a>` : "";
  })}</div>` : "";
  const title = p.title || "(untitled)";
  const link = p.url ? html`<a href="${p.url}" target="_blank" rel="noopener">${title}</a>` : html`<span class="item-title">${title}</span>`;
  return html`<li class="article">
    <div class="item-head">${link}${star(store, "article", p.uid, title, p.url)}</div>
    <div class="meta">${p.year || "n.d."} · ${p.journal || ""}${p.first_author ? ` · ${p.first_author}` : ""}
      ${p.preprint ? html`<span class="badge warn">Preprint, not peer reviewed</span>` : ""}
      <span class="badge">${EVIDENCE_LABELS[p.evidence] || p.evidence || ""}</span>
      ${p.subtype === "msa_c" ? html`<span class="badge fit-includes_msa_c">MSA-C</span>` : ""}
      ${p.solid ? html`<span class="badge solid">Solid trial</span>` : ""}${outcome}
      ${p.curated ? html`<span class="badge curated">Checked by hand</span>` : ""}
      ${p.source === "company_topline" ? html`<span class="badge warn">Company-reported, not yet peer-reviewed</span>` : ""}</div>
    ${p.curated ? html`<p class="curated-note">${p.design ? html`<span class="meta">${p.design}.</span> ` : ""}${p.note || ""}
      ${p.counted === false ? html`<span class="meta">20 people or fewer: listed, but not counted in the grade.</span>` : ""}</p>` : ""}
    ${chips}
    ${p.curated && !String(p.uid).includes(":") ? "" : html`<button type="button" class="secondary abstract-btn" data-action="abstract"
      data-uid="${p.uid}" aria-expanded="false">Show abstract</button>
    <div class="abstract" data-abstract-for="${p.uid}" hidden></div>`}</li>`;
}

export function errorBox(err, retryHash = null) {
  return html`<div class="error-box" role="alert"><h2>Couldn't load this</h2><p>${err && err.message ? err.message : String(err)}</p>
    <p class="row-actions"><button type="button" data-action="refresh">Try again</button>
    ${retryHash ? html`<a class="button secondary" href="${retryHash}">Go back</a>` : ""}</p></div>`;
}

export function countLine(n, one, many) { return plural(n, one, many); }
