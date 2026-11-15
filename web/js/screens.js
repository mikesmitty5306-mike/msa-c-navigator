// One render function per screen. Each returns { title, section, back, body, after? } and never touches the
// DOM directly except in `after`, which runs once the body is on the page.

import { html, fmtDate, fmtMiles, haversineKm, plural, norm } from "./util.js";
import {
  CATEGORY_LABELS, CATEGORY_SLUGS, EVIDENCE_LABELS, FIT_LABELS, OUTCOME_GROUPS, SUBTYPE_LABELS, TRIAL_GROUP_LABELS,
  cityLabel, filterLibrary, filterTrials, rowsToObjects, savedStatusChange, statusLabel, trialCountries,
} from "./logic.js";
import { errorBox, fitBadge, gradeBadge, gradeLabel, paperRow, resultsSummary, star, statusBadge, trialRow } from "./views.js";
import { NOTE_MAX } from "./store.js";

const KIND_LABELS = [["mechanism", "Disease mechanisms"], ["symptomatic", "Symptom treatment"], ["biomarker", "Biomarkers (not graded)"]];
const OPEN_FITS = ["includes_msa_c", "any_msa"];

function select(name, id, label, options, value, any = "Any") {
  return html`<label for="${id}">${label} <select id="${id}" name="${name}">
    ${any !== null ? html`<option value="">${any}</option>` : ""}
    ${options.map(([v, l]) => html`<option value="${v}" ${String(value ?? "") === String(v) ? "selected" : ""}>${l}</option>`)}
  </select></label>`;
}

function homeLine(store) {
  const h = store.homeCity;
  return h
    ? html`<p class="home-city">Nearest sites measured from <strong>${h.source === "location" ? "your location" : cityLabel(h)}</strong>.
        <a href="#/settings">Change</a></p>`
    : html`<p class="notice">Set your home city in <a href="#/settings">Settings</a> (or use your location) to see the nearest trials first, in miles.</p>`;
}

function noteForm(saved, type, id) {
  const fid = `note-${type}-${id}`.replace(/[^a-zA-Z0-9_-]/g, "-");
  return html`<form class="note-form" data-form="note" data-type="${type}" data-id="${id}">
    <label for="${fid}">Private note (stays on this phone)</label>
    <textarea id="${fid}" name="note" rows="2" maxlength="${NOTE_MAX}" placeholder="Add a note">${saved.note || ""}</textarea>
    <div class="row-actions"><button type="submit" class="secondary">Save note</button><span class="note-saved" hidden>Saved</span></div></form>`;
}

// ---------------------------------------------------------------- Home
export async function home(ctx) {
  const [g, t] = await Promise.all([ctx.grades(), ctx.trials()]);
  const meta = ctx.data.meta;
  const asOf = fmtDate(ctx.data.dataAsOf());
  const top = g.mechanisms.filter((m) => m.kind === "mechanism").slice(0, 6);
  const homeCity = ctx.store.homeCity;
  const promising = t.trials.filter((x) => x.rank && x.open && OPEN_FITS.includes(x.fit))
    .sort((a, b) => a.rank.position - b.rank.position).slice(0, 5);
  const due = ctx.store.backupDue();
  const c = meta.counts || {};
  return {
    title: "MSA-C Navigator", section: "home",
    body: html`
      ${asOf ? html`<p class="data-asof">Data as of ${asOf}</p>` : ""}
      ${due ? html`<p class="notice">Your saved items haven't been backed up for ${plural(due, "day", "days")}.
        <a href="#/settings">Back up now</a></p>` : ""}
      <section class="stats">
        <div><strong>${(c.articles ?? 0).toLocaleString("en-US")}</strong><span>papers &amp; preprints</span></div>
        <div><strong>${(c.msa_c_articles ?? 0).toLocaleString("en-US")}</strong><span>discuss MSA-C specifically</span></div>
        <div><strong>${c.trials ?? 0}</strong><span>registered trials</span></div>
        <div><strong>${c.open_trials ?? 0}</strong><span>open to enrollment</span></div>
      </section>
      <div class="grid2">
        <section>
          <h2>Best-supported mechanisms</h2>
          <p class="meta">Graded A–D on how treatments aimed at each one have done in published research.
            <a href="#/grades/help">What the grades mean</a></p>
          <ol class="graded">${top.map((m) => html`<li>${gradeBadge(m, g.meanings)}
            <div><a href="#/grades/${m.id}">${m.name}</a><span class="grade-meaning">${g.meanings[m.grade]}</span></div></li>`)}</ol>
          <a class="more-link" href="#/grades">All grades →</a>
        </section>
        <section>
          <h2>Most promising trials you can join</h2>
          <p class="meta">Recruiting treatment trials open to MSA-C, ranked on the evidence behind them, phase and design.</p>
          <ul class="plain">${promising.map((x) => trialRow(ctx.store, x, homeCity ? nearestFor(x, homeCity) : null))}</ul>
          <a class="more-link" href="#/trials?sort=promising">All recruiting trials →</a>
        </section>
      </div>`,
  };
}

function nearestFor(trial, homeCity) {
  let best = null;
  for (const [lat, lon, , , open] of trial.sites || []) {
    if (lat === null || lon === null || (trial.open && !open)) continue;
    const d = haversineKm(homeCity.lat, homeCity.lon, lat, lon);
    if (best === null || d < best) best = d;
  }
  return best;
}

// ---------------------------------------------------------------- Grades
export async function gradesList(ctx) {
  const g = await ctx.grades();
  return {
    title: "Grades", section: "grades",
    body: html`<h1>Evidence grades</h1>
      <p class="lead">Each area of MSA biology gets a letter for how treatments aimed at it have done in published studies.
        Tap a category to see the studies behind it.</p>
      ${promisingStrip(g)}
      <p><a class="more-link" href="#/grades/help">What the grades mean →</a></p>
      ${KIND_LABELS.map(([kind, label]) => html`<h2>${label}</h2><div class="mech-list">
        ${g.mechanisms.filter((m) => m.kind === kind).map((m) => html`<article class="mech-card">
          <div class="mech-head">${gradeBadge(m, g.meanings)}<div><h3><a href="#/grades/${m.id}">${m.name}</a></h3>
            <p class="grade-meaning">${g.meanings[m.grade]}</p></div></div>${resultsSummary(m)}</article>`)}</div>`)}`,
  };
}

/** "Most promising so far": mechanisms graded A or B, best first, each with its Why line. */
function promisingStrip(g) {
  const top = g.mechanisms.filter((m) => m.grade === "A" || m.grade === "B");
  return html`<section class="promising" aria-labelledby="promising-title">
    <h2 id="promising-title">Most promising so far</h2>
    ${top.length ? html`<ul class="plain">${top.map((m) => html`<li class="promising-item">${gradeBadge(m, g.meanings, true)}
      <div><a href="#/grades/${m.id}">${m.name}</a><span class="grade-meaning">${m.reason}</span></div></li>`)}</ul>`
      : html`<p class="meta">No area has reached grade A or B yet.</p>`}
  </section>`;
}

export async function gradesHelp(ctx) {
  const g = await ctx.grades();
  const d = g.definitions || {};
  return {
    title: "What the grades mean", section: "grades", back: "#/grades",
    body: html`<h1>What the grades mean</h1>
      <p class="lead">Each area of MSA biology gets one letter, based on how treatments aimed at it have done in published research.</p>
      <dl class="grade-defs">${["A", "B", "C", "D"].map((k) => html`<div><dt><span class="grade grade-${k}" aria-hidden="true">${k}</span>
        <span class="visually-hidden">Grade ${k}</span></dt><dd>${g.meanings[k]}</dd></div>`)}</dl>
      <h2>Words used</h2>
      <ul class="defs">
        ${d.solid_trial ? html`<li><strong>Solid human trial:</strong> ${d.solid_trial}</li>` : ""}
        ${d.major_failure ? html`<li><strong>Major failure:</strong> ${d.major_failure}</li>` : ""}
        ${d.outcomes ? html`<li><strong>Positive, negative, mixed, unclear:</strong> ${d.outcomes}</li>` : ""}
        ${d.grade_modifier ? html`<li><strong>+ and −:</strong> ${d.grade_modifier}</li>` : ""}
        ${d.curated ? html`<li><strong>Checked by hand:</strong> ${d.curated}</li>` : ""}
        <li><strong>Animal models</strong> are studies in mice, rats, flies and so on. <strong>In vitro</strong> means cells or proteins in the lab.</li>
      </ul>
      <h2>Please keep in mind</h2>
      <ul class="defs">
        <li>Grades are produced automatically from published research. The software reads titles, abstracts and conclusions,
          and it can be wrong. Tap any category to see and check the studies.</li>
        <li>A grade describes the research on a whole area of biology. It does not predict whether any treatment will work
          for a particular person.</li>
        <li>Most MSA research has no proven treatment yet, so most grades are C or D. That reflects the evidence, not the effort going into it.</li>
        <li>Talk to your neurologist before making any decision about treatment or trials.</li>
      </ul>
      <p><a class="button" href="#/grades">Back to the grades</a></p>`,
  };
}

export async function mechanism(ctx, id) {
  const g = await ctx.grades();
  const m = g.byId[id];
  if (!m) return notFound();
  let detail = null;
  let detailError = null;
  try { detail = await ctx.data.get(`mechanisms/${id}.json`); } catch (e) { detailError = e; }
  const a = m.activity || {};
  return {
    title: m.name, section: "grades", back: "#/grades",
    body: html`<div class="title-row">${gradeBadge(m, g.meanings)}<h1>${m.name}</h1></div>
      <p class="grade-meaning big">Grade ${gradeLabel(m)}: ${g.meanings[m.grade]}</p>
      <p class="why"><strong>Why:</strong> ${m.reason}</p>
      <p><a class="more-link" href="#/grades/help">What the grades mean →</a></p>
      <h2>Results, by kind of study</h2>
      <p class="meta">Counts of treatment studies whose conclusion reported a benefit (positive), no benefit (negative),
        both (mixed), or no clear verdict (unclear). Tap a category to see the studies.</p>
      ${resultsSummary(m)}
      <h2>About this area</h2><p>${m.description}</p>
      ${m.agents && m.agents.length ? html`<p class="meta">Treatments tracked: ${m.agents.join(", ")}.</p>` : ""}
      <h2>MSA-C specific research</h2>
      ${detail ? html`<ul class="plain">${detail.msa_c_recent.length ? detail.msa_c_recent.map((p) => paperRow(ctx.store, p))
        : html`<li class="meta">None found.</li>`}</ul>` : errorBox(detailError)}
      <a class="more-link" href="#/library?mech=${id}">All papers on this →</a>
      <h2>Research activity</h2>
      <p class="meta">Not used in the grade. ${a.papers ?? 0} papers, ${a.msa_c_papers ?? 0} on MSA-C; ${a.recent_3y ?? 0} in the last
        3 years against ${a.prior_3y ?? 0} in the 3 before.</p>`,
  };
}

export async function results(ctx, id, slug) {
  const g = await ctx.grades();
  const m = g.byId[id];
  const cat = CATEGORY_SLUGS[slug];
  if (!m || !cat) return notFound();
  const detail = await ctx.data.get(`mechanisms/${id}.json`);
  const nav = html`<nav class="category-nav" aria-label="Kind of study">${Object.entries(CATEGORY_SLUGS).map(([s, c]) =>
    html`<a href="#/grades/${id}/${s}" ${c === cat ? html`aria-current="page"` : ""}>${CATEGORY_LABELS[c]}</a>`)}</nav>`;
  let groups;
  if (cat === "trials") {
    const t = await ctx.trials();
    groups = Object.keys(TRIAL_GROUP_LABELS).map((key) => {
      const entries = (detail.trials[key] || []).map((e) => (typeof e === "string" ? { id: e } : e));
      const rows = entries.map((e) => {
        const trial = t.byId[e.id];
        if (!trial) return "";
        const extra = key === "stopped" ? html`<li class="stop-reason"><span class="meta">Reason given: ${e.why_stopped || "none stated"}</span>
          ${e.counts_as_failure ? html`<span class="badge closed">Counts as a failure in the grade</span>`
            : html`<span class="badge">Not counted against the grade</span>`}</li>` : "";
        return html`${trialRow(ctx.store, trial)}${extra}`;
      });
      return html`<details class="result-group" ${entries.length && entries.length <= 15 ? "open" : ""}>
        <summary><span>${TRIAL_GROUP_LABELS[key]}</span><span class="count">${entries.length}</span></summary>
        <ul class="plain">${rows.length ? rows : html`<li class="meta">None.</li>`}</ul></details>`;
    });
    groups = html`<p class="meta">Treatment trials registered on ClinicalTrials.gov that test something aimed at this area.
      Observational studies are not included.</p>${groups}`;
  } else {
    const byOutcome = detail.studies[cat] || {};
    groups = html`<p class="meta">Treatment studies only, read automatically from each paper's conclusion. Labels can be wrong;
      open a paper to check. Studies marked "Checked by hand" were reviewed manually.</p>${Object.keys(OUTCOME_GROUPS).map((key) => {
      const list = byOutcome[key] || [];
      const total = m.results[cat] ? m.results[cat][key] : list.length;
      return html`<details class="result-group" ${list.length && list.length <= 15 ? "open" : ""}>
        <summary><span>${OUTCOME_GROUPS[key]}</span><span class="count">${total}</span></summary>
        <ul class="plain">${list.length ? list.map((p) => paperRow(ctx.store, p)) : html`<li class="meta">None.</li>`}</ul>
        ${total > list.length ? html`<p><a class="more-link" href="#/library?mech=${id}&ev=${cat === "human" ? "clinical" : cat}&out=${key}">
          See all ${total} in the library →</a></p>` : ""}</details>`;
    })}`;
  }
  return {
    title: `${CATEGORY_LABELS[cat]} · ${m.name}`, section: "grades", back: `#/grades/${id}`,
    body: html`<p class="crumb"><a href="#/grades/${id}">‹ ${m.name}</a></p>
      <div class="title-row">${gradeBadge(m, g.meanings)}<h1>${CATEGORY_LABELS[cat]}</h1></div>
      <p class="grade-meaning">Grade ${gradeLabel(m)}: ${g.meanings[m.grade]}</p>${nav}${groups}`,
  };
}

// ---------------------------------------------------------------- Trials
export async function trialsList(ctx) {
  const [t, g] = await Promise.all([ctx.trials(), ctx.grades()]);
  const q = ctx.query;
  const homeCity = ctx.store.homeCity;
  const filters = {
    status: q.get("status") || "open", fit: q.get("fit") || "", mechanism: q.get("mech") || "", country: q.get("country") || "",
    age: q.get("age") || "", sex: q.get("sex") || "", sort: q.get("sort") || (homeCity ? "nearest" : "match"),
  };
  const rows = filterTrials(t.trials, filters, homeCity);
  const active = ["fit", "mechanism", "country", "age", "sex"].filter((k) => filters[k]).length + (filters.status !== "open" ? 1 : 0);
  const sortOptions = [["match", "Best match for MSA-C"], ["promising", "Most promising first"]];
  if (homeCity) sortOptions.unshift(["nearest", "Nearest first"]);
  return {
    title: "Clinical trials", section: "trials",
    body: html`<h1>Clinical trials</h1>${homeLine(ctx.store)}
      <details class="filters" ${q.toString() ? "" : "open"}>
        <summary>Filters <span class="meta">${active} active</span></summary>
        <form class="fields" data-form="trial-filters">
          ${select("sort", "f-sort", "Order", sortOptions, filters.sort, null)}
          ${select("status", "f-status", "Status", [["open", "Open to enrollment"], ["active", "Open or ongoing"], ["all", "All, including completed"]], filters.status, null)}
          ${select("fit", "f-fit", "MSA-C fit", Object.entries(FIT_LABELS), filters.fit)}
          ${select("mech", "f-mech", "Mechanism", g.mechanisms.map((m) => [m.id, m.name]), filters.mechanism)}
          ${select("country", "f-country", "Country", trialCountries(t.trials).map((c) => [c, c]), filters.country)}
          <label for="f-age">Your age <input id="f-age" name="age" type="number" inputmode="numeric" min="0" max="120" value="${filters.age}"></label>
          ${select("sex", "f-sex", "Sex", [["FEMALE", "Female"], ["MALE", "Male"]], filters.sex)}
          <div class="actions"><button type="submit">Show trials</button>
            ${homeCity ? "" : html`<button type="button" class="secondary" data-action="use-location">Use my location</button>`}</div>
        </form>
      </details>
      <p class="meta">${plural(rows.length, "trial", "trials")}${filters.sort === "nearest" && homeCity ? ", nearest site first" : ""}.</p>
      <ul class="plain">${rows.length ? rows.map((r) => trialRow(ctx.store, r.trial, r.km)) : html`<li class="meta">No trials match these filters.</li>`}</ul>`,
  };
}

export async function trialDetail(ctx, id) {
  const [t, g] = await Promise.all([ctx.trials(), ctx.grades()]);
  const compact = t.byId[id];
  if (!compact) return notFound();
  const d = await ctx.data.get(`trials/${id}.json`);
  const saved = ctx.store.getSaved("trial", id);
  if (saved) ctx.store.markSeen(id, d.status);          // seeing the trial clears its "status changed" badge
  const homeCity = ctx.store.homeCity;
  const isOpen = compact.open;
  let sites = (d.sites || []).filter((s) => !isOpen || !s.status || ["RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"].includes(s.status));
  sites = sites.map((s) => ({ ...s, km: homeCity && s.lat !== null && s.lon !== null ? haversineKm(homeCity.lat, homeCity.lon, s.lat, s.lon) : null }));
  if (homeCity) sites.sort((a, b) => (a.km ?? Infinity) - (b.km ?? Infinity));
  const inq = d.inquiry || {};
  const mailto = inq.email ? `mailto:${inq.email}?subject=${encodeURIComponent(inq.subject || "")}&body=${encodeURIComponent(inq.body || "")}` : null;
  const tel = (phone, ext) => `tel:${String(phone).replace(/[^\d+]/g, "")}${ext ? `,${String(ext).replace(/\D/g, "")}` : ""}`;
  const contactCard = (c) => html`<div class="contact-card"><strong>${c.name || "Study contact"}</strong>
    <div class="actions">${c.phone ? html`<a class="button secondary" href="${tel(c.phone, c.phone_ext)}">Call</a>` : ""}
      ${c.email ? html`<a class="button secondary" href="mailto:${c.email}">Email</a>` : ""}</div>
    <span class="detail">${c.phone || ""}${c.phone_ext ? ` ext. ${c.phone_ext}` : ""}${c.phone && c.email ? " · " : ""}${c.email || ""}</span></div>`;
  return {
    title: d.title, section: "trials", back: "#/trials",
    body: html`<div class="title-row"><h1>${d.title}</h1>${star(ctx.store, "trial", id, d.title, d.registry_url, d.status)}</div>
      <p class="meta">${id} · ${d.phase} · ${d.sponsor}</p>
      <p>${statusBadge(compact)} ${fitBadge(compact.fit)}</p>
      <p>${(d.mechanisms || []).map((mid) => g.byId[mid] ? html`<a class="chip" href="#/grades/${mid}">${g.byId[mid].name}${g.byId[mid].grade !== "n/a" ? ` · grade ${g.byId[mid].grade}` : ""}</a>` : "")}</p>
      ${saved ? noteForm(saved, "trial", id) : ""}
      <section class="enroll ${isOpen ? "open" : "closed"}">
        <h2>How to take part</h2>
        ${isOpen ? html`<p>This study is <strong>${statusLabel(d.status).toLowerCase()}</strong>. Enrollment goes through the study team:
            you contact them, and they check your eligibility and arrange screening. Bring this listing to your neurologist so they can help.</p>
          <ol>
            <li>Read the official listing.<br><a class="button secondary" href="${d.registry_url}" target="_blank" rel="noopener">Open on ClinicalTrials.gov</a></li>
            <li>Check the basic criteria below. Age: ${d.eligibility.min_age || "no minimum"} – ${d.eligibility.max_age || "no maximum"}
              · Sex: ${d.eligibility.sex === "ALL" ? "All" : String(d.eligibility.sex || "All").toLowerCase()} · ${FIT_LABELS[d.fit] || ""}</li>
            <li>Contact the study team:
              ${(d.contacts || []).filter((c) => c.phone || c.email).length ? d.contacts.filter((c) => c.phone || c.email).map(contactCard)
                : html`<p>No central contact is listed, so use a site contact below or the registry page.</p>`}
              ${mailto ? html`<p><a class="button" href="${mailto}">Draft an inquiry email</a></p>` : ""}</li>
          </ol>`
          : html`<p>This study is <strong>${statusLabel(d.status).toLowerCase()}</strong> and is not accepting new participants.</p>
            ${d.why_stopped ? html`<p class="meta">Reason given: ${d.why_stopped}</p>` : ""}
            <p><a class="button secondary" href="${d.registry_url}${d.has_results ? "?tab=results" : ""}" target="_blank" rel="noopener">
              ${d.has_results ? "See posted results" : "See it on ClinicalTrials.gov"}</a></p>`}
      </section>
      ${sites.length ? html`<h2>${isOpen ? "Open sites" : "Sites"}${homeCity ? ", nearest first" : ""}</h2>
        <table class="stack"><thead><tr><th>Facility</th><th>Location</th><th>Distance</th><th>Contact</th></tr></thead><tbody>
        ${sites.slice(0, 60).map((s) => html`<tr><td class="cell-title">${s.facility}</td>
          <td data-label="Location">${[s.city, s.state, s.country].filter(Boolean).join(", ")}</td>
          <td data-label="Distance">${s.km !== null ? fmtMiles(s.km) : ""}</td>
          <td class="cell-block" data-label="Contact">${(s.contacts || []).map((c) => html`${c.name || ""}${c.phone ? html` · <a href="${tel(c.phone, c.phone_ext)}">${c.phone}</a>` : ""}${c.email ? html` · <a href="mailto:${c.email}">${c.email}</a>` : ""}<br>`)}</td></tr>`)}
        </tbody></table>
        ${sites.length > 60 ? html`<p class="meta">Showing 60 of ${sites.length} sites. The full list is on ClinicalTrials.gov.</p>` : ""}
        ${homeCity ? "" : html`<p class="meta">Set your home city in <a href="#/settings">Settings</a> to sort sites by distance.</p>`}` : ""}
      <h2>Summary</h2><p class="prewrap">${d.summary}</p>
      <h2>Interventions</h2><ul>${(d.interventions || []).map((i) => html`<li><strong>${i.name}</strong> (${String(i.type || "").toLowerCase()})${i.description ? `: ${i.description}` : ""}</li>`)}</ul>
      <div class="grid2">
        <section><h2>Inclusion criteria</h2><ul>${(d.eligibility.inclusion || []).map((c) => html`<li>${c}</li>`)}</ul></section>
        <section><h2>Exclusion criteria</h2><ul>${(d.eligibility.exclusion || []).map((c) => html`<li>${c}</li>`)}</ul></section>
      </div>
      <p class="meta">Last updated on ClinicalTrials.gov: ${fmtDate(d.last_update) || "unknown"}. Always confirm details on the registry page.</p>`,
  };
}

// ---------------------------------------------------------------- Library
export async function library(ctx) {
  const [g, papers] = await Promise.all([ctx.grades(), ctx.library()]);
  const q = ctx.query;
  const filters = { q: q.get("q") || "", mechanism: q.get("mech") || "", evidence: q.get("ev") || "", subtype: q.get("sub") || "",
    outcome: q.get("out") || "", preprints: q.get("pre") || "", year_from: q.get("from") || "" };
  const shown = Math.min(Math.max(Number(q.get("n")) || 25, 25), 1000);
  const matches = filterLibrary(papers, filters);
  const active = Object.values(filters).filter(Boolean).length;
  return {
    title: "Research library", section: "library",
    body: html`<h1>Research library</h1>
      <details class="filters" ${q.toString() ? "" : "open"}>
        <summary>Search and filters <span class="meta">${active} active</span></summary>
        <form class="fields" data-form="library">
          <label for="a-q">Search <input id="a-q" name="q" type="search" enterkeyhint="search" value="${filters.q}" placeholder="e.g. purkinje, NfL"></label>
          ${select("mech", "a-mech", "Mechanism", g.mechanisms.map((m) => [m.id, m.name]), filters.mechanism)}
          ${select("sub", "a-sub", "Subtype", Object.entries(SUBTYPE_LABELS), filters.subtype)}
          ${select("ev", "a-ev", "Evidence", Object.entries(EVIDENCE_LABELS), filters.evidence)}
          ${select("out", "a-out", "Result", [["positive", "Positive"], ["negative", "Negative"], ["mixed", "Mixed"], ["unclear", "Unclear"]], filters.outcome)}
          <label for="a-year">From year <input id="a-year" name="from" type="number" inputmode="numeric" value="${filters.year_from}"></label>
          ${select("pre", "a-pre", "Preprints", [["exclude", "Exclude"], ["only", "Only preprints"]], filters.preprints, "Include")}
          <div class="actions"><button type="submit">Search</button></div>
        </form>
      </details>
      <p class="meta">${plural(matches.length, "paper", "papers")}. Search looks in titles, journals, authors and keywords.</p>
      <ul class="plain">${matches.slice(0, shown).map((p) => paperRow(ctx.store, p, { grades: g }))}
        ${matches.length ? "" : html`<li class="meta">No papers match. Try fewer words or clear a filter.</li>`}</ul>
      ${matches.length > shown ? html`<div class="more-row"><button type="button" class="secondary" data-action="more" data-n="${shown + 25}">
        Show more (${matches.length - shown} left)</button></div>` : ""}`,
  };
}

// ---------------------------------------------------------------- Saved
export async function saved(ctx) {
  const t = await ctx.trials();
  const trials = ctx.store.listSaved("trial");
  const papers = ctx.store.listSaved("article");
  const homeCity = ctx.store.homeCity;
  const due = ctx.store.backupDue();
  return {
    title: "Saved", section: "saved",
    body: html`<h1>Saved</h1>
      <p class="meta">Tap the star on any trial or paper to save it here. Saved items and notes are stored only on this phone.</p>
      ${due ? html`<p class="notice">Not backed up for ${plural(due, "day", "days")}. <button type="button" class="link-button" data-action="export">Back up now</button></p>` : ""}
      <h2>Trials <span class="meta">${trials.length}</span></h2>
      <ul class="plain saved-list">${trials.length ? trials.map((s) => {
        const tr = t.byId[s.id];
        const change = savedStatusChange(s, tr);
        return html`<li id="saved-trial-${s.id}">
          <div class="item-head">${tr ? html`<a href="#/trial/${s.id}">${tr.title}</a>` : html`<span class="item-title">${s.title}</span>`}
            ${star(ctx.store, "trial", s.id, s.title, s.url, tr ? tr.status : null)}</div>
          ${change ? html`<p class="changed" role="status"><strong>Status changed</strong>${change.date ? ` on ${fmtDate(change.date)}` : ""}:
            ${statusLabel(change.from)} → ${statusLabel(change.to)}
            <button type="button" class="link-button" data-action="seen" data-id="${s.id}" data-status="${change.to}">Got it</button></p>` : ""}
          ${tr ? html`<div class="meta">${s.id} · ${tr.phase}${homeCity && nearestFor(tr, homeCity) !== null ? ` · nearest site ${fmtMiles(nearestFor(tr, homeCity))}` : ""}</div>
            <div>${statusBadge(tr)} ${fitBadge(tr.fit)}</div>`
            : html`<div class="meta">${s.id} · no longer in the research data. Last known status: ${statusLabel(s.last_seen_status)}.</div>`}
          ${noteForm(s, "trial", s.id)}</li>`;
      }) : html`<li class="meta empty">No saved trials yet. Tap ☆ next to a trial to save it.</li>`}</ul>
      <h2>Papers <span class="meta">${papers.length}</span></h2>
      <ul class="plain saved-list">${papers.length ? papers.map((s) => html`<li>
          <div class="item-head">${s.url ? html`<a href="${s.url}" target="_blank" rel="noopener">${s.title}</a>` : html`<span class="item-title">${s.title}</span>`}
            ${star(ctx.store, "article", s.id, s.title, s.url)}</div>
          ${noteForm(s, "article", s.id)}</li>`) : html`<li class="meta empty">No saved papers yet. Tap ☆ next to a paper to save it.</li>`}</ul>
      <section class="settings-section"><h2>Backup</h2>
        <p class="meta">Saved items and notes live only on this phone. Export a backup now and then, and keep it in Files or send it to yourself.</p>
        <div class="actions"><button type="button" data-action="export">Export backup</button>
          <label class="button secondary" for="import-saved">Import backup</label>
          <input id="import-saved" class="visually-hidden" type="file" accept="application/json,.json" data-input="import"></div></section>`,
  };
}

// ---------------------------------------------------------------- Settings
export async function settings(ctx) {
  const homeCity = ctx.store.homeCity;
  const meta = ctx.data.meta || {};
  const s = meta.sources || {};
  const st = ctx.store.state;
  const sourceLine = (label, x) => x ? html`<li>${label}: ${fmtDate(x.refreshed_at || x.checked_at) || "not yet"}${x.records ? ` (${x.records.toLocaleString("en-US")} records)` : ""}</li>` : "";
  return {
    title: "Settings", section: "settings", back: "#/",
    body: html`<h1>Settings</h1>
      <a class="settings-row" href="#/settings/install"><span><strong>Install on iPhone</strong>
        <span class="meta">Add to Home Screen, notifications, and what to do if the app won't load</span></span><span class="tile-go" aria-hidden="true">›</span></a>

      <section class="settings-section" id="home-section"><h2>Home location</h2>
        <p class="meta">Used to put the nearest trial sites first, with distances in miles. It is stored only on this phone.</p>
        <p>Current: <strong>${homeCity ? (homeCity.source === "location" ? `Your location (${homeCity.lat.toFixed(2)}, ${homeCity.lon.toFixed(2)})` : cityLabel(homeCity)) : "not set"}</strong></p>
        <form class="city-form" data-form="city" autocomplete="off">
          <label for="city">Type a city or town</label>
          <div class="combo"><input id="city" name="city" type="text" data-input="city" placeholder="e.g. Boise, or Paris, Texas"
            role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="city-list" enterkeyhint="done">
            <ul id="city-list" role="listbox" hidden></ul></div>
          <p class="meta">Add a state or country after a comma to narrow it down. 70,000 places with 5,000+ people are included.</p>
          <div class="actions"><button type="submit">Save home city</button>
            <button type="button" class="secondary" data-action="use-location">Use my location</button>
            ${homeCity ? html`<button type="button" class="secondary" data-action="clear-city">Clear</button>` : ""}</div>
          <p class="status-line" id="city-status" role="status"></p>
        </form></section>

      <section class="settings-section" id="push-section"><h2>Notifications</h2>
        <p class="meta">Get a notification when a new trial that people with MSA-C can join opens, or when a trial relevant to MSA-C
          changes status.</p>
        <div id="push-body"><p class="loading">Checking…</p></div></section>

      <section class="settings-section"><h2>Backup</h2>
        <p class="meta">Saved items, notes and your home location live only on this phone. If the app is deleted or the phone is reset,
          they are gone, so export a backup now and then (to Files, Mail or Messages).</p>
        <p class="status-line">${st.last_backup_at ? `Last backup: ${fmtDate(st.last_backup_at)}` : "No backup yet."}
          ${Object.keys(st.saved).length} saved ${Object.keys(st.saved).length === 1 ? "item" : "items"}.</p>
        <div class="actions"><button type="button" data-action="export">Export backup</button>
          <label class="button secondary" for="import-settings">Import backup</label>
          <input id="import-settings" class="visually-hidden" type="file" accept="application/json,.json" data-input="import"></div>
        <p class="status-line" id="storage-status"></p></section>

      <section class="settings-section"><h2>Data</h2>
        <ul class="defs">${sourceLine("Clinical trials", s.clinicaltrials)}${sourceLine("PubMed", s.pubmed)}${sourceLine("Europe PMC", s.europepmc)}</ul>
        <p class="meta">The data is refreshed automatically in the cloud: trials every hour when ClinicalTrials.gov changes, papers daily.
          Tap the circular arrow at the top to check for new data.</p></section>

      <section class="settings-section"><h2>Help</h2>
        <div class="actions"><a class="button secondary" href="#/grades/help">What the grades mean</a>
          <button type="button" class="secondary" data-action="show-welcome">Show the welcome again</button></div></section>

      <section class="settings-section"><h2>Erase everything on this phone</h2>
        <p class="meta">Removes saved items, notes, home location and settings from this phone. Export a backup first if you want to keep them.</p>
        <div class="actions"><button type="button" class="danger" data-action="reset">Erase app data</button></div></section>`,
  };
}

export async function install(ctx) {
  return {
    title: "Install on iPhone", section: "settings", back: "#/settings",
    body: html`<h1>Install on iPhone</h1>
      <p class="lead">Add MSA-C Navigator to your Home Screen so it opens like an app: full screen, with its own icon, and working offline.</p>
      <h2>Add it to the Home Screen</h2>
      <ol class="steps">
        <li>Open the app's link in <strong>Safari</strong>.</li>
        <li>Tap the <strong>Share</strong> button
          <svg class="inline-icon" viewBox="0 0 24 24" aria-label="Share icon"><path d="M12 3v12M8 7l4-4 4 4"/><path d="M6 11v9h12v-9"/></svg>
          (at the bottom of the screen; on iPad, at the top).</li>
        <li>Scroll down and tap <strong>Add to Home Screen</strong>. Keep the name "MSA-C Navigator". If you see "Open as Web App", leave it on.</li>
        <li>Tap <strong>Add</strong>, then open the app from its new icon. Use the icon from now on: the Home Screen app keeps its own
          saved items, separate from Safari.</li>
      </ol>
      <h2>Notifications</h2>
      <ul class="defs">
        <li>Notifications tell you when a <strong>new trial that people with MSA-C can join opens</strong>, and when a
          <strong>trial relevant to MSA-C changes status</strong> (for example starts recruiting, stops or completes).</li>
        <li>They work only in the Home Screen app, on iOS 16.4 or later. Turn them on in Settings > Notifications, then tap
          <strong>Send notification code to helper</strong> and send it to the person who set up the app.</li>
        <li>Tapping a notification opens that trial in the app.</li>
      </ul>
      <h2>Getting around</h2>
      <ul class="defs">
        <li>The arrow at the top left goes <strong>Back</strong>. The tabs at the bottom go to the main sections.</li>
        <li>The circular arrow at the top right checks for <strong>new data</strong> and refreshes the page.</li>
        <li>Links to ClinicalTrials.gov and PubMed open in a Safari window on top of the app; tap <strong>Done</strong> to come back.
          "Draft an inquiry email" opens the Mail app.</li>
      </ul>
      <h2>If the app won't load</h2>
      <ul class="defs">
        <li><strong>No internet:</strong> the app needs the internet the first time. After that it opens offline with the last data it saw
          (a banner says "Offline").</li>
        <li><strong>"This app needs updating":</strong> close the app completely (swipe it away) and open it again.</li>
        <li><strong>Saved items missing:</strong> they live only on this phone. Use Settings > Import backup to restore them.</li>
        <li><strong>Still stuck:</strong> delete the Home Screen icon and add it again from Safari, then import your backup.</li>
      </ul>
      <p><a class="button secondary" href="#/settings">Back to Settings</a></p>`,
  };
}

export function notFound() {
  return {
    title: "Not found", section: "", back: "#/",
    body: html`<section class="error-page"><h1>Page not found</h1>
      <p>The page or study you asked for isn't here. It may have been removed from the registry, or the address may be mistyped.</p>
      <p class="actions"><a class="button" href="#/">Go to Home</a> <a class="button secondary" href="#/trials">Clinical trials</a>
        <a class="button secondary" href="#/saved">Saved</a></p></section>`,
  };
}

export { norm, rowsToObjects };
