"""Build frontend fixtures that follow the data contract in reports/static_plan.txt.

Frontend-only stand-in until Codex's exporter (B1) and contract/sample/ exist. It READS the
local research database and backend modules; it never writes outside tests/frontend/fixtures/.

    ..\\.venv\\Scripts\\python.exe tests/frontend/fixtures/make_fixtures.py
    ..\\.venv\\Scripts\\python.exe tests/frontend/fixtures/make_fixtures.py --full --out <folder> [--db <copy>]
        A complete, uncapped export of the research database in the contract format, for
        integration checks while the backend exporter doesn't exist. Refuses to write to public-data/.

Output: tests/frontend/fixtures/data/ (committed, so tests don't need the database).
Scope (smaller than a real export, same shapes):
  - grades.json: all mechanisms;  mechanisms/<id>.json: every mechanism, lists capped at 15
  - trials.json and trials/<id>.json: every trial
  - library: papers from 2024 onwards plus every paper referenced elsewhere; abstracts for those
  - cities: letters b, l, n, p, s (largest 400 places each)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import unicodedata
import urllib.parse
from pathlib import Path

APP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APP))

from msac import analysis, config  # noqa: E402
from msac import mechanisms as mech  # noqa: E402
from msac.storage import Store  # noqa: E402

OUT = Path(__file__).resolve().parent / "data"
SCHEMA = 1
CAP = 15
CITY_LETTERS = "blnps"
CITY_ROWS = 400
LIBRARY_COLUMNS = ["uid", "title", "year", "journal", "first_author", "evidence", "outcome", "subtype",
                   "preprint", "solid", "mechanisms", "url", "keywords"]


def write(rel: str, obj) -> None:
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def study(a) -> dict:
    return {"uid": a.uid, "title": a.title, "year": a.year, "journal": a.journal or "",
            "first_author": a.authors[0] if a.authors else None, "url": a.url, "evidence": a.evidence,
            "outcome": a.outcome if a.outcome in analysis.OUTCOME_KEYS or a.outcome == "safety" else None,
            "solid": bool(a.solid_trial), "preprint": bool(a.is_preprint), "subtype": a.subtype}


def library_bucket(year) -> str:
    if not year or year < 1990:
        return "before-1990"
    if year >= 2015:
        return str(year)
    start = year - (year % 5)
    return f"{start}-{start + 4}"


def shard_of(uid: str) -> str:
    return hashlib.sha1(uid.encode("utf-8")).hexdigest()[:2]


def city_key(name: str) -> str:
    n = "".join(c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c)).lower().strip()
    return n[0] if n and "a" <= n[0] <= "z" else "other"


FULL = False


def main() -> None:
    global OUT, CAP, CITY_LETTERS, CITY_ROWS, FULL
    ap = argparse.ArgumentParser(description="Build contract-format data for the frontend")
    ap.add_argument("--full", action="store_true", help="every paper, uncapped study lists, the whole city list")
    ap.add_argument("--out", type=Path, help="output folder (default: tests/frontend/fixtures/data)")
    ap.add_argument("--db", type=Path, default=config.DB_PATH, help="research database to read")
    args = ap.parse_args()
    if args.out:
        if args.out.resolve() == (APP / "public-data").resolve():
            raise SystemExit("public-data/ belongs to the backend build; choose another folder.")
        OUT = args.out
    if args.full:
        FULL, CAP, CITY_LETTERS, CITY_ROWS = True, 100000, "abcdefghijklmnopqrstuvwxyz", 10 ** 9
    store = Store(args.db)
    if OUT.exists():
        shutil.rmtree(OUT)
    ranked = analysis.rank_mechanisms(store)
    tags = store.all_tags("article")
    trial_tags = store.all_tags("trial")
    referenced: set[str] = set()

    # ---- grades.json
    write("grades.json", {
        "schema_version": SCHEMA,
        "meanings": analysis.GRADE_MEANINGS,
        "definitions": {
            "solid_trial": "A trial in people that was randomized and controlled (placebo, sham or comparison treatment), "
                           "past the first safety stage, and not a pilot, open-label or single-group study.",
            "major_failure": "A solid human trial that found no benefit, or a trial stopped for futility, lack of effect, "
                             "safety or its results. Trials stopped for funding or slow recruitment don't count.",
            "outcomes": "Positive, negative, mixed and unclear are what each treatment study's own conclusion says. "
                        "Unclear includes studies that only reported safety.",
        },
        "mechanisms": [{
            "id": s.mechanism.id, "name": s.mechanism.name, "kind": s.mechanism.kind,
            "description": s.mechanism.description, "agents": list(s.mechanism.agents),
            "grade": s.grade, "reason": s.grade_reason, "results": s.results, "trials": s.trial_counts,
            "activity": {"papers": s.n_articles, "msa_c_papers": s.n_msa_c, "recent_3y": s.n_recent, "prior_3y": s.n_prior},
        } for s in ranked],
    })

    # ---- mechanisms/<id>.json
    for s in ranked:
        mid = s.mechanism.id
        studies = {}
        for cat in analysis.RESULT_CATEGORIES:
            studies[cat] = {}
            for key in analysis.OUTCOME_KEYS:
                outs = [k for k, v in analysis.OUTCOME_COUNT.items() if v == key]
                items, _ = store.search_articles(mechanism=mid, evidence_in=[analysis.CATEGORY_EVIDENCE[cat]],
                                                 outcome_in=outs, limit=CAP)
                studies[cat][key] = [study(a) for a in items]
                referenced.update(a.uid for a in items)
        trials = {g: list(s.trial_groups[g]) for g in ("recruiting", "completed", "ongoing")}
        trials["stopped"] = []
        for nct in s.trial_groups["stopped"]:
            t = store.get_trial(nct)
            trials["stopped"].append({"id": nct, "why_stopped": (t.why_stopped or None) if t else None,
                                      "counts_as_failure": nct in s.failed_trials})
        msa_c, _ = store.search_articles(mechanism=mid, subtype="msa_c", limit=8)
        referenced.update(a.uid for a in msa_c)
        write(f"mechanisms/{mid}.json", {"schema_version": SCHEMA, "id": mid, "studies": studies, "trials": trials,
                                         "msa_c_recent": [study(a) for a in msa_c]})

    # ---- trials.json and trials/<id>.json
    rank = {r["trial"].nct_id: (i + 1, r) for i, r in enumerate(analysis.rank_open_trials(store, ranked))}
    compact = []
    for t in sorted(store.iter_trials(), key=lambda x: x.nct_id):
        sites = analysis.sites(t, open_only=False)
        compact.append({
            "id": t.nct_id, "title": t.title, "status": t.status, "status_label": t.status_label,
            "open": t.is_open, "active": t.is_active, "phase": t.phase_label, "max_phase": t.max_phase,
            "type": t.study_type, "sponsor": t.sponsor, "fit": t.subtype_fit, "mechanisms": sorted(trial_tags.get(t.nct_id, [])),
            "min_age": analysis.parse_age_years(t.min_age), "max_age": analysis.parse_age_years(t.max_age),
            "sex": t.sex or "ALL",
            "sites": [[s.get("lat"), s.get("lon"), s.get("city") or "", s.get("country") or "",
                       analysis._site_is_open(t, s)] for s in sites],
            "last_update": t.last_update, "status_changed": None,
            "rank": ({"position": rank[t.nct_id][0], "score": rank[t.nct_id][1]["score"],
                      "target": rank[t.nct_id][1]["target"]} if t.nct_id in rank else None),
        })
        info = analysis.enrollment_info(t)
        inquiry = {"email": None, "subject": f"Inquiry about participating in {t.nct_id}", "body": ""}
        if info["mailto"]:
            parsed = urllib.parse.urlsplit(info["mailto"])
            q = urllib.parse.parse_qs(parsed.query)
            inquiry = {"email": parsed.path, "subject": q.get("subject", [""])[0], "body": q.get("body", [""])[0]}
        inc, exc = info["inclusion"], info["exclusion"]
        write(f"trials/{t.nct_id}.json", {
            "schema_version": SCHEMA, "id": t.nct_id, "title": t.title, "official_title": t.official_title,
            "summary": t.summary, "status": t.status, "status_label": t.status_label,
            "why_stopped": t.why_stopped or None, "has_results": t.has_results, "phase": t.phase_label,
            "type": t.study_type, "sponsor": t.sponsor, "enrollment": t.enrollment,
            "design": {"allocation": t.allocation or None, "masking": t.masking or None,
                       "primary_purpose": t.primary_purpose or None},
            "start_date": t.start_date, "completion_date": t.completion_date, "last_update": t.last_update,
            "registry_url": t.url, "conditions": t.conditions, "mechanisms": sorted(trial_tags.get(t.nct_id, [])),
            "fit": t.subtype_fit,
            "interventions": [{"type": i.get("type", ""), "name": i.get("name", ""), "description": i.get("description", "")}
                              for i in t.interventions],
            "eligibility": {"min_age": t.min_age, "max_age": t.max_age, "sex": t.sex or "ALL",
                            "healthy_volunteers": t.healthy_volunteers, "inclusion": inc, "exclusion": exc},
            "contacts": [{"name": c.get("name"), "role": c.get("role"), "phone": c.get("phone"),
                          "phone_ext": c.get("phoneExt"), "email": c.get("email")} for c in t.central_contacts],
            "sites": [{"facility": s.get("facility", ""), "city": s.get("city", ""), "state": s.get("state", ""),
                       "country": s.get("country", ""), "status": s.get("status") or None, "lat": s.get("lat"),
                       "lon": s.get("lon"),
                       "contacts": [{"name": c.get("name"), "role": c.get("role"), "phone": c.get("phone"),
                                     "phone_ext": c.get("phoneExt"), "email": c.get("email")} for c in s.get("contacts", [])]}
                      for s in t.locations],
            "inquiry": inquiry,
        })
    write("trials.json", {"schema_version": SCHEMA, "trials": compact})

    # ---- library (2024 onwards + referenced) and abstracts
    rows_by_shard: dict[str, list] = {}
    abstracts: dict[str, dict[str, str]] = {}
    for a in store.iter_articles():
        if not (FULL or (a.year or 0) >= 2024 or a.uid in referenced):
            continue
        row = [a.uid, a.title, a.year, a.journal or "", a.authors[0] if a.authors else None, a.evidence,
               a.outcome if a.outcome != "na" else None, a.subtype, bool(a.is_preprint), bool(a.solid_trial),
               sorted(tags.get(a.uid, [])), a.url, " ".join(a.keywords)[:300]]
        rows_by_shard.setdefault(library_bucket(a.year), []).append(row)
        if a.abstract:
            abstracts.setdefault(shard_of(a.uid), {})[a.uid] = a.abstract
    order = sorted(rows_by_shard, key=lambda n: (n == "before-1990", -int(n.split("-")[0]) if n[0].isdigit() else 0))
    shards = []
    for name in order:
        rows = sorted(rows_by_shard[name], key=lambda r: (-(r[2] or 0), r[0]))
        write(f"library/{name}.json", {"schema_version": SCHEMA, "rows": rows})
        shards.append({"name": name, "rows": len(rows), "sha256": hashlib.sha256((OUT / f"library/{name}.json").read_bytes()).hexdigest()})
    write("library/index.json", {"schema_version": SCHEMA, "columns": LIBRARY_COLUMNS, "shards": shards})
    for shard, items in abstracts.items():
        write(f"abstracts/{shard}.json", {"schema_version": SCHEMA, "abstracts": items})

    # ---- cities
    by_key: dict[str, list] = {}
    for line in config.CITIES_PATH.read_text(encoding="utf-8").splitlines():
        p = line.split("|")
        if len(p) != 5:
            continue
        k = city_key(p[0])
        if (k in CITY_LETTERS or (FULL and k == "other")) and len(by_key.setdefault(k, [])) < CITY_ROWS:
            by_key[k].append([p[0], p[1], p[2], float(p[3]), float(p[4])])
    for k, rows in by_key.items():
        write(f"cities/{k}.json", {"schema_version": SCHEMA, "columns": ["name", "region", "country", "lat", "lon"], "rows": rows})

    # ---- meta.json (last, so it can list every file with its size and hash)
    runs = {r["source"]: r for r in reversed(store.recent_runs(50)) if r["status"] == "ok"}
    files = {}
    for path in sorted(OUT.rglob("*.json")):
        rel = path.relative_to(OUT).as_posix()
        data = path.read_bytes()
        files[rel] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    stamp = lambda r: (r["finished_at"] or "").replace("+00:00", "Z") or None  # noqa: E731
    stats = store.stats()
    write("meta.json", {
        "schema_version": SCHEMA, "build_id": "fixture-2026-11-14T06:17:42Z", "generated_at": "2026-11-14T06:17:42Z",
        "sources": {
            "clinicaltrials": {"data_timestamp": None, "checked_at": stamp(runs["clinicaltrials"]) if "clinicaltrials" in runs else None,
                               "refreshed_at": stamp(runs["clinicaltrials"]) if "clinicaltrials" in runs else None,
                               "records": stats["trials"]},
            "pubmed": {"refreshed_at": stamp(runs["pubmed"]) if "pubmed" in runs else None, "records": runs["pubmed"]["n_items"] if "pubmed" in runs else None},
            "europepmc": {"refreshed_at": stamp(runs["europepmc"]) if "europepmc" in runs else None, "records": runs["europepmc"]["n_items"] if "europepmc" in runs else None},
        },
        "counts": {"articles": stats["articles"], "msa_c_articles": stats["msa_c_articles"], "preprints": stats["preprints"],
                   "trials": stats["trials"], "open_trials": stats["open_trials"]},
        "files": files,
        "disclaimer": config.DISCLAIMER,
        "push_public_key": None,
    })
    total = sum(f.stat().st_size for f in OUT.rglob("*.json"))
    print(f"fixtures written to {OUT}: {len(files) + 1} files, {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
