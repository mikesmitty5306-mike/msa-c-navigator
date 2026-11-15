"""Mechanism ranking, trial filtering and enrollment helpers."""

from __future__ import annotations

import math
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import mechanisms as mech
from .models import Trial
from .storage import Store

RECENCY_HALF_LIFE_YEARS = 5
MSA_C_BONUS = 1.5
UNPROVEN = 0.4                      # trial ranking: value for a treatment whose mechanism is not classified

_STATUS_WEIGHT = {
    "RECRUITING": 1.5, "NOT_YET_RECRUITING": 1.5, "ENROLLING_BY_INVITATION": 1.5,
    "ACTIVE_NOT_RECRUITING": 1.3, "COMPLETED": 1.0, "UNKNOWN": 0.6,
    "TERMINATED": 0.4, "SUSPENDED": 0.4, "WITHDRAWN": 0.2,
}

# ---------------------------------------------------------------- grades (plain-language rules)
GRADE_MEANINGS = {
    "A": "At least one solid human trial showed benefit, with no major failures.",
    "B": "Promising human results from small or early studies, supported by animal or lab work.",
    "C": "Promising results in animals or cells only, or mixed results in people.",
    "D": "Little evidence so far, or human trials that failed or were stopped.",
    "n/a": "Not graded: biomarkers are tests, not treatments.",
}
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "n/a": 4}
GRADE_VALUE = {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.25}       # used only to rank recruiting trials

RESULT_CATEGORIES = ("human", "animal", "in_vitro")
CATEGORY_LABELS = {"human": "Human", "animal": "Animal models", "in_vitro": "In vitro (cell/lab)", "trials": "Trials"}
EVIDENCE_TO_CATEGORY = {"clinical": "human", "animal": "animal", "in_vitro": "in_vitro"}
CATEGORY_EVIDENCE = {v: k for k, v in EVIDENCE_TO_CATEGORY.items()}
# "Safety only" conclusions say nothing about benefit, so they count as unclear.
OUTCOME_COUNT = {"positive": "positive", "negative": "negative", "mixed": "mixed", "unclear": "unclear", "safety": "unclear"}
OUTCOME_KEYS = ("positive", "negative", "mixed", "unclear")
TRIAL_GROUPS = ("recruiting", "completed", "stopped", "ongoing")
TRIAL_GROUP_LABELS = {"recruiting": "Recruiting", "completed": "Completed", "stopped": "Stopped early",
                      "ongoing": "Ongoing (not recruiting) or status unknown"}


def trial_group(status: str) -> str:
    if status in ("RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "AVAILABLE"):
        return "recruiting"
    if status == "COMPLETED":
        return "completed"
    if status in ("TERMINATED", "SUSPENDED", "WITHDRAWN"):
        return "stopped"
    return "ongoing"


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _empty_results() -> dict[str, dict[str, int]]:
    return {c: dict.fromkeys(OUTCOME_KEYS, 0) for c in RESULT_CATEGORIES}


@dataclass
class MechanismScore:
    mechanism: mech.Mechanism
    # research activity (shown as background; not used in grades)
    n_articles: int = 0
    n_recent: int = 0               # last 3 years
    n_prior: int = 0                # the 3 years before that
    n_msa_c: int = 0
    evidence: dict[str, int] = field(default_factory=dict)
    literature_score: float = 0.0
    trial_score: float = 0.0
    trial_ids: list[str] = field(default_factory=list)
    n_active_trials: int = 0
    max_phase: float = 0.0
    # results of treatment studies, by category
    results: dict[str, dict[str, int]] = field(default_factory=_empty_results)
    solid_positive: list[str] = field(default_factory=list)     # solid human trials that showed benefit
    solid_negative: list[str] = field(default_factory=list)     # solid human trials that showed no benefit
    trial_groups: dict[str, list[str]] = field(default_factory=lambda: {g: [] for g in TRIAL_GROUPS})
    failed_trials: list[str] = field(default_factory=list)      # stopped for futility, efficacy, safety or results

    @property
    def human(self) -> dict[str, int]:
        return self.results["human"]

    @property
    def lab_positive(self) -> int:
        return self.results["animal"]["positive"] + self.results["in_vitro"]["positive"]

    @property
    def major_failures(self) -> int:
        return len(self.solid_negative) + len(self.failed_trials)

    @property
    def grade(self) -> str:
        """Plain-language rules (see GRADE_MEANINGS and reports/stepF_grades.txt):
        A  a solid human trial showed benefit and nothing major failed
        B  human benefit only from small/early studies, backed by animal or lab results, and no human failures
        C  benefit in animals or cells only, or human results that point both ways
        D  nothing positive yet, or human evidence that is only negative (including stopped trials)
        """
        if self.mechanism.kind == "biomarker":
            return "n/a"
        h = self.human
        if self.solid_positive and not self.major_failures:
            return "A"
        if h["positive"]:
            if h["negative"] or h["mixed"] or self.major_failures:
                return "C"                       # mixed results in people
            return "B" if self.lab_positive else "C"
        if h["negative"] or self.major_failures:
            return "D"                           # human trials failed or were stopped
        if h["mixed"] or self.lab_positive:
            return "C"
        return "D"                               # little evidence so far

    @property
    def grade_meaning(self) -> str:
        return GRADE_MEANINGS[self.grade]

    @property
    def grade_reason(self) -> str:
        """One sentence explaining which rule applied, with the numbers behind it."""
        g, h, lab = self.grade, self.human, self.lab_positive
        stopped = ""
        if self.failed_trials:
            stopped = _n(len(self.failed_trials), "trial was", "trials were") + " stopped for futility, safety or poor results"
        if g == "n/a":
            return "Biomarker studies measure the disease; they don't test treatments."
        if g == "A":
            extra = ""
            if h["negative"]:
                extra = " (" + _n(h["negative"], "smaller study", "smaller studies") + " found no benefit)"
            return _n(len(self.solid_positive), "solid human trial", "solid human trials") + \
                " showed benefit and no major trial failed" + extra + "."
        if g == "B":
            return (_n(h["positive"], "small or early human study", "small or early human studies") + " reported benefit, "
                    "supported by " + _n(lab, "positive animal or lab study", "positive animal or lab studies") + ".")
        if g == "C" and h["positive"]:
            if h["negative"] or h["mixed"] or self.major_failures:
                against = []
                if h["negative"]:
                    against.append(str(h["negative"]) + " found no benefit")
                if h["mixed"]:
                    against.append(_n(h["mixed"], "was mixed", "were mixed"))
                if self.solid_negative:
                    against.append(_n(len(self.solid_negative), "solid trial", "solid trials") + " failed")
                if stopped:
                    against.append(stopped)
                return ("Mixed in people: " + _n(h["positive"], "human study", "human studies") +
                        " reported benefit, but " + "; ".join(against) + ".")
            return _n(h["positive"], "human study", "human studies") + " reported benefit, but no animal or lab study supports it yet."
        if g == "C" and h["mixed"]:
            return "Mixed in people: " + _n(h["mixed"], "human study", "human studies") + " gave mixed results."
        if g == "C":
            return _n(lab, "animal or lab study", "animal or lab studies") + " reported benefit, but no human study has yet."
        if h["negative"] or self.major_failures:
            parts = []
            if h["negative"]:
                solid = ""
                if self.solid_negative:
                    solid = " (" + ("a solid trial" if len(self.solid_negative) == 1 == h["negative"]
                                    else _n(len(self.solid_negative), "solid trial", "solid trials")) + ")"
                parts.append(_n(h["negative"], "human study", "human studies") + " found no benefit" + solid)
            if stopped:
                parts.append(stopped)
            return "Human results so far are negative: " + "; ".join(parts) + "."
        return "Little evidence so far: no treatment study has reported a benefit."

    @property
    def trial_counts(self) -> dict[str, int]:
        return {g: len(ids) for g, ids in self.trial_groups.items()}

    def sort_key(self) -> tuple:
        if self.mechanism.kind == "biomarker":
            return (1, 0, -self.score)
        h = self.human
        return (0, GRADE_ORDER[self.grade], -len(self.solid_positive), -h["positive"], -self.lab_positive,
                self.major_failures, h["negative"], -self.n_articles)

    @property
    def momentum(self) -> float | None:
        """Ratio of recent to prior output; >1 means the field is accelerating. None if no prior papers."""
        return round(self.n_recent / self.n_prior, 2) if self.n_prior else None

    @property
    def momentum_label(self) -> str:
        if self.momentum is not None:
            return f"{self.momentum}×"
        return "new" if self.n_recent else "—"

    @property
    def score(self) -> float:
        return round(10 * math.log1p(self.literature_score) + 12 * math.log1p(self.trial_score), 1)

    @property
    def max_phase_label(self) -> str:
        return {0: "—", 0.5: "Early Phase 1", 1: "Phase 1", 2: "Phase 2", 3: "Phase 3"}.get(self.max_phase, "—")


def rank_mechanisms(store: Store, today: date | None = None, kinds: tuple[str, ...] | None = None) -> list[MechanismScore]:
    """Score each mechanism from tagged literature and trials.

    literature_score = Σ recency_weight × evidence_weight × (1.5 if MSA-C specific)
    trial_score      = Σ (1 + max_phase) × status_weight   (observational trials × 0.5)
    score            = 10·ln(1+literature) + 12·ln(1+trials)

    A transparent heuristic for prioritising reading — not a measure of efficacy.
    """
    year = (today or date.today()).year
    scores = {m.id: MechanismScore(m) for m in mech.TAXONOMY if not kinds or m.kind in kinds}
    facts = {r["uid"]: r for r in store.article_facts()}

    for uid, mids in store.all_tags("article").items():
        f = facts.get(uid)
        if f is None:
            continue
        age = max(year - (f["year"] or year), 0)
        weight = 0.5 ** (age / RECENCY_HALF_LIFE_YEARS) * mech.EVIDENCE_WEIGHT.get(f["evidence"], 0.75)
        if f["subtype"] == "msa_c":
            weight *= MSA_C_BONUS
        for mid in mids:
            s = scores.get(mid)
            if s is None:
                continue
            s.n_articles += 1
            s.literature_score += weight
            outcome, category = f["outcome"] or "na", EVIDENCE_TO_CATEGORY.get(f["evidence"])
            if category and outcome in OUTCOME_COUNT:
                s.results[category][OUTCOME_COUNT[outcome]] += 1
                if category == "human" and f["solid"]:
                    if outcome == "positive":
                        s.solid_positive.append(uid)
                    elif outcome == "negative":
                        s.solid_negative.append(uid)
            s.evidence[f["evidence"]] = s.evidence.get(f["evidence"], 0) + 1
            if f["subtype"] == "msa_c":
                s.n_msa_c += 1
            if f["year"] and year - f["year"] < 3:
                s.n_recent += 1
            elif f["year"] and 3 <= year - f["year"] < 6:
                s.n_prior += 1

    trials = {t.nct_id: t for t in store.iter_trials()}
    for nct, mids in store.all_tags("trial").items():
        t = trials.get(nct)
        if t is None:
            continue
        w = (1 + t.max_phase) * _STATUS_WEIGHT.get(t.status, 0.6)
        if t.study_type == "OBSERVATIONAL":
            w *= 0.5
        for mid in mids:
            s = scores.get(mid)
            if s is None:
                continue
            s.trial_ids.append(nct)
            s.trial_score += w
            s.n_active_trials += t.is_active
            if t.study_type == "INTERVENTIONAL":          # treatment trials only; observational studies aren't results
                s.max_phase = max(s.max_phase, t.max_phase)
                s.trial_groups[trial_group(t.status)].append(nct)
                if mech.trial_stopped_for_failure(t.status, t.why_stopped):
                    s.failed_trials.append(nct)

    # Best grade first; within a grade, stronger human evidence first. Biomarkers (not graded) last.
    return sorted(scores.values(), key=MechanismScore.sort_key)


# ---------------------------------------------------------------- promising open trials
TRIAL_WEIGHTS = {"mechanism": 0.35, "phase": 0.25, "design": 0.15, "target": 0.15, "fit": 0.10}
_PHASE_VALUE = {0: 0.2, 0.5: 0.3, 1: 0.4, 2: 0.7, 3: 1.0}
_FIT_VALUE = {"includes_msa_c": 1.0, "any_msa": 0.85, "msa_p_focused": 0.3, "excludes_msa_c": 0.0}


def rank_open_trials(store: Store, scores: list[MechanismScore] | None = None,
                     include_excluded: bool = False) -> list[dict[str, Any]]:
    """Rank recruiting treatment trials by how promising they look for someone with MSA-C.

    mechanism  best grade of the mechanisms the trial targets (A 1.0, B 0.75, C 0.5, D 0.25; unclassified 0.4)
    phase      later phases mean the treatment already passed earlier testing
    design     randomised, blinded and adequately sized trials give clearer answers
    target     disease-modifying > symptomatic > diagnostic
    fit        whether people with MSA-C can join
    """
    scores = scores or rank_mechanisms(store)
    by_id = {s.mechanism.id: s for s in scores}
    tags = store.all_tags("trial")
    out = []
    for t in store.iter_trials():
        if not t.is_open or t.study_type != "INTERVENTIONAL":
            continue
        if t.subtype_fit == "excludes_msa_c" and not include_excluded:
            continue
        mids = tags.get(t.nct_id, [])
        graded = [by_id[m] for m in mids if m in by_id and by_id[m].mechanism.kind != "biomarker"]
        best = min(graded, key=MechanismScore.sort_key, default=None)
        mech_v = GRADE_VALUE[best.grade] if best else UNPROVEN
        phase_v = _PHASE_VALUE.get(t.max_phase, 0.2)
        design_v = (0.4 if t.allocation == "RANDOMIZED" else 0) \
            + {"DOUBLE": 0.4, "TRIPLE": 0.4, "QUADRUPLE": 0.4, "SINGLE": 0.2}.get(t.masking, 0) \
            + (0.2 if (t.enrollment or 0) >= 60 else 0.1 if (t.enrollment or 0) >= 20 else 0)
        kinds = {by_id[m].mechanism.kind for m in mids if m in by_id}
        if t.primary_purpose in ("DIAGNOSTIC", "BASIC_SCIENCE", "SCREENING"):
            target_v, target = 0.2, "Diagnostic or research"
        elif "mechanism" in kinds:
            target_v, target = 1.0, "Targets the disease"
        elif "symptomatic" in kinds:
            target_v, target = 0.6, "Treats symptoms"
        else:
            target_v, target = 0.7, "Treatment, mechanism not yet classified"
        fit_v = _FIT_VALUE.get(t.subtype_fit, 0.5)
        parts = {"mechanism": mech_v, "phase": phase_v, "design": design_v, "target": target_v, "fit": fit_v}
        score = round(100 * sum(TRIAL_WEIGHTS[k] * v for k, v in parts.items()), 1)
        out.append({
            "trial": t, "score": score, "parts": {k: round(v, 2) for k, v in parts.items()},
            "best_mechanism": best.mechanism.id if best else None, "best_grade": best.grade if best else None,
            "target": target,
        })
    out.sort(key=lambda r: -r["score"])
    return out


# ---------------------------------------------------------------- trials
def parse_age_years(value: str | None) -> float | None:
    if not value:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(year|month|week|day)", value, re.I)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    return n / {"year": 1, "month": 12, "week": 52, "day": 365}[unit]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def _site_is_open(trial: Trial, loc: dict[str, Any]) -> bool:
    status = loc.get("status")
    return status in ("RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION") or (not status and trial.is_open)


def sites(trial: Trial, lat: float | None = None, lon: float | None = None, open_only: bool = True) -> list[dict]:
    out = []
    for loc in trial.locations:
        if open_only and not _site_is_open(trial, loc):
            continue
        site = dict(loc)
        if lat is not None and lon is not None and loc.get("lat") is not None:
            site["distance_km"] = round(haversine_km(lat, lon, loc["lat"], loc["lon"]))
        out.append(site)
    out.sort(key=lambda s: (s.get("distance_km") is None, s.get("distance_km") or 0, s.get("country", "")))
    return out


def filter_trials(trials: list[Trial], *, status: str = "open", country: str | None = None,
                  subtype_fit: str | None = None, age: float | None = None, sex: str | None = None,
                  mechanism_ids: dict[str, list[str]] | None = None, mechanism: str | None = None,
                  lat: float | None = None, lon: float | None = None) -> list[tuple[Trial, int | None]]:
    """Return (trial, nearest open site km) pairs matching the filters."""
    out = []
    for t in trials:
        if status == "open" and not t.is_open:
            continue
        if status == "active" and not t.is_active:
            continue
        if subtype_fit and t.subtype_fit != subtype_fit:
            continue
        if mechanism and mechanism not in (mechanism_ids or {}).get(t.nct_id, []):
            continue
        if age is not None:
            lo, hi = parse_age_years(t.min_age), parse_age_years(t.max_age)
            if (lo is not None and age < lo) or (hi is not None and age > hi):
                continue
        if sex and t.sex and t.sex not in ("ALL", sex.upper()):
            continue
        open_sites = sites(t, lat, lon, open_only=status == "open")
        if country and not any(country.lower() == (s.get("country") or "").lower() for s in open_sites):
            continue
        dists = [s["distance_km"] for s in open_sites if "distance_km" in s]
        out.append((t, min(dists) if dists else None))
    out.sort(key=lambda p: (p[1] is None, p[1] or 0, -p[0].max_phase, p[0].nct_id))
    return out


def criteria_list(block: str) -> list[str]:
    items = []
    for line in block.splitlines():
        line = re.sub(r"^\s*(?:[*\-•]|\d+[.)])\s*", "", line).strip()
        if line and not re.fullmatch(r"(?:inclusion|exclusion) criteria\s*:?", line, re.I):
            items.append(line)
    return items


def enrollment_info(trial: Trial, lat: float | None = None, lon: float | None = None) -> dict[str, Any]:
    """Everything a person needs to contact a study team about joining."""
    inclusion, exclusion = mech.split_eligibility(trial.eligibility)
    open_sites = sites(trial, lat, lon)
    contacts = [c for c in trial.central_contacts if c.get("email") or c.get("phone")]
    if not contacts:
        contacts = [c for s in open_sites[:3] for c in s.get("contacts", []) if c.get("email") or c.get("phone")]
    email = next((c["email"] for c in contacts if c.get("email")), None)
    mailto = None
    if email:
        subject = f"Inquiry about participating in {trial.nct_id}"
        body = (
            f"Hello,\n\nI am interested in learning whether I may be eligible for the study "
            f"\"{trial.title}\" ({trial.nct_id}, {trial.url}).\n\n"
            "About me:\n- Diagnosis: multiple system atrophy, cerebellar type (MSA-C)\n"
            "- Year of symptom onset: \n- Age: \n- Location (city/country): \n- Treating neurologist: \n\n"
            "Could you tell me about next steps for screening?\n\nThank you,\n"
        )
        mailto = f"mailto:{email}?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"
    return {
        "registry_url": trial.url,
        "accepting": trial.is_open,
        "status": trial.status_label,
        "subtype_fit": mech.SUBTYPE_FIT_LABELS.get(trial.subtype_fit, trial.subtype_fit),
        "contacts": contacts,
        "sites": open_sites,
        "mailto": mailto,
        "age_range": f"{trial.min_age or 'no minimum'} – {trial.max_age or 'no maximum'}",
        "sex": (trial.sex or "ALL").title(),
        "inclusion": criteria_list(inclusion),
        "exclusion": criteria_list(exclusion),
    }
