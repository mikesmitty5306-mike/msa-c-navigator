// Grades screen: "Most promising so far" strip, +/− badges, and hand-checked / company-reported labels.
import { test } from "node:test";
import assert from "node:assert/strict";

import { gradesList, gradesHelp } from "../../../web/js/screens.js";
import { gradeBadge, gradeLabel, paperRow } from "../../../web/js/views.js";

const MEANINGS = { A: "a", B: "b", C: "c", D: "Not yet shown to work in people.", "n/a": "n" };
const mech = (id, grade, modifier, reason) => ({ id, name: id.toUpperCase(), kind: "mechanism", grade, modifier, reason,
  results: { human: { positive: 0, negative: 0, mixed: 0, unclear: 0 }, animal: { positive: 0, negative: 0, mixed: 0, unclear: 0 },
    in_vitro: { positive: 0, negative: 0, mixed: 0, unclear: 0 } }, trials: { recruiting: 0, completed: 0, stopped: 0, ongoing: 0 } });

function ctx(mechanisms) {
  const g = { meanings: MEANINGS, definitions: { grade_modifier: "+ means peer-reviewed; − company-reported. It never changes the letter.",
    curated: "Checked by hand." }, mechanisms, byId: Object.fromEntries(mechanisms.map((m) => [m.id, m])) };
  return { grades: async () => g };
}
const text = (body) => String(body).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

test("the Grades screen starts with the B mechanisms and their Why lines", async () => {
  const s = await gradesList(ctx([
    mech("neurotrophic", "B", "+", "Lee 2012 (Annals of Neurology): slowed UMSARS worsening."),
    mech("oxidative_iron", "B", "-", "ATH434-201 (company-reported, not yet peer-reviewed): UMSARS part I."),
    mech("gut_brain", "D", null, "Little evidence so far."),
  ]));
  const t = text(s.body);
  assert.ok(t.indexOf("Most promising so far") < t.indexOf("What the grades mean"));
  const strip = t.slice(t.indexOf("Most promising so far"), t.indexOf("What the grades mean"));
  assert.match(strip, /B\+ NEUROTROPHIC Lee 2012/);
  assert.match(strip, /B− OXIDATIVE_IRON ATH434-201 \(company-reported, not yet peer-reviewed\)/);
  assert.doesNotMatch(strip, /GUT_BRAIN/);
  assert.match(t, /Not yet shown to work in people\./);
});

test("with nothing at A or B the strip says so", async () => {
  const t = text((await gradesList(ctx([mech("gut_brain", "D", null, "x")]))).body);
  assert.match(t, /Most promising so far No area has reached grade A or B yet\./);
});

test("badges and labels show the modifier, and screen readers hear it", () => {
  const b = String(gradeBadge({ grade: "B", modifier: "-" }, MEANINGS));
  assert.match(b, />B−</);
  assert.match(b, /aria-label="Grade B minus"/);
  assert.equal(gradeLabel({ grade: "B", modifier: "+" }), "B+");
  assert.equal(gradeLabel({ grade: "C", modifier: null }), "C");
  assert.equal(gradeLabel({ grade: "n/a" }), "n/a");
});

test("the help page explains + and − and hand-checked results", async () => {
  const t = text((await gradesHelp(ctx([]))).body);
  assert.match(t, /\+ and −: \+ means peer-reviewed; − company-reported\. It never changes the letter\./);
  assert.match(t, /Checked by hand: Checked by hand\./);
});

test("study rows mark hand-checked and company-reported results", () => {
  const store = { isSaved: () => false };
  const topline = String(paperRow(store, { uid: "NCT05109091", title: "ATH434-201", year: null, evidence: "clinical",
    outcome: "positive", solid: true, curated: true, source: "company_topline", design: "randomized, placebo-controlled, Phase 2, 77 people",
    note: "50 mg showed a significant benefit on UMSARS part I; 75 mg did not.", counted: true }));
  assert.match(topline, /Company-reported, not yet peer-reviewed/);
  assert.match(topline, /Checked by hand/);
  assert.match(topline, /50 mg showed a significant benefit/);
  assert.doesNotMatch(topline, /Show abstract/);                 // not a database paper, so there's no abstract
  const small = String(paperRow(store, { uid: "NCT05864365", title: "ATH434-202", curated: true, source: "company_topline",
    outcome: "positive", counted: false, note: "Open-label." }));
  assert.match(small, /20 people or fewer: listed, but not counted in the grade\./);
  const peer = String(paperRow(store, { uid: "pmid:22829267", title: "Lee 2012", curated: true, source: "peer_reviewed", outcome: "positive" }));
  assert.doesNotMatch(peer, /Company-reported/);
  assert.match(peer, /Show abstract/);
  const plain = String(paperRow(store, { uid: "pmid:1", title: "A paper", outcome: "negative" }));
  assert.doesNotMatch(plain, /Checked by hand|Company-reported/);
});
