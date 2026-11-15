"""Hand-checked key results (contract/curated_results.json) and the grade rules that use them:
A = benefit repeated in two key results; B = one key result plus animal/lab support; + peer-reviewed, - company-reported.
A key result is a controlled human trial of more than 20 people that improved UMSARS or brain imaging."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from msac import analysis, mechanisms  # noqa: E402
from msac.export import documents  # noqa: E402
from msac.models import Article  # noqa: E402
from msac.storage import Store  # noqa: E402
from test_stepF import key_result, score  # noqa: E402

CURATED = analysis.load_curated()
BY_LABEL = {c.label: c for c in CURATED}


# ---------------------------------------------------------------- the curated file
def test_curated_file_seeds_are_valid():
    assert {"ATH434-201", "ATH434-202", "Lee 2012, Annals of Neurology", "PROMESA (EGCG)"} <= set(BY_LABEL)
    ath201, ath202 = BY_LABEL["ATH434-201"], BY_LABEL["ATH434-202"]
    lee, promesa = BY_LABEL["Lee 2012, Annals of Neurology"], BY_LABEL["PROMESA (EGCG)"]
    assert ath201.mechanism == ath202.mechanism == "oxidative_iron" and lee.mechanism == "neurotrophic"
    assert promesa.mechanism == "asyn_aggregation"                       # EGCG: not oligodendroglia
    assert ath201.qualifies and not ath201.peer_reviewed and ath201.n == 77
    assert not ath202.qualifies and not ath202.counts                     # open-label, 10 people: listed, not counted
    assert lee.qualifies and lee.peer_reviewed and "doi:10.1002/ana.23612" in lee.ids and lee.subtype == "msa_c"
    assert promesa.outcome == "negative" and promesa.solid
    for c in CURATED:
        assert c.mechanism in mechanisms.BY_ID and c.note


def test_invalid_entries_are_refused(tmp_path):
    bad = json.loads((ROOT / "contract" / "curated_results.json").read_text(encoding="utf-8"))
    bad["results"][0]["mechanism"] = "not_a_mechanism"
    path = tmp_path / "curated.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid curated result"):
        analysis.load_curated(path)


@pytest.mark.parametrize("change, qualifies", [
    (dict(), True),
    (dict(n=20), False),                                  # more than 20 people needed
    (dict(n=21), True),
    (dict(placebo=False), False),                         # must be controlled
    (dict(randomized=False, placebo=False), False),
    (dict(umsars=None, imaging="improved"), True),        # brain imaging counts as well as UMSARS
    (dict(umsars="no_benefit"), False),
    (dict(outcome="mixed"), False),
])
def test_what_counts_as_a_key_result(change, qualifies):
    assert key_result(**change).qualifies is qualifies


def test_modifier_follows_peer_review_and_never_changes_the_letter():
    peer = score(keys=[key_result(0)], animal={"positive": 2})
    topline = score(keys=[key_result(0, source="company_topline")], animal={"positive": 2})
    assert (peer.grade, peer.grade_modifier, peer.grade_label) == ("B", "+", "B+")
    assert (topline.grade, topline.grade_modifier, topline.grade_label) == ("B", "-", "B−")
    assert "company-reported, not yet peer-reviewed" in topline.grade_reason
    mixed_a = score(keys=[key_result(0, source="company_topline"), key_result(1)])
    assert (mixed_a.grade, mixed_a.grade_modifier) == ("A", "+")
    assert score(animal={"positive": 3}).grade_modifier is None           # C and D have no modifier
    assert analysis.MechanismScore.sort_key(peer) < analysis.MechanismScore.sort_key(topline)


# ---------------------------------------------------------------- the real seeds, through rank_mechanisms and the export
def _put(store, uid, title, evidence, outcome, tags, solid=False, subtype="msa_general", year=2020):
    a = Article(uid=uid, title=title, abstract="", year=year, sources=["pubmed"], evidence=evidence, outcome=outcome,
                solid_trial=solid, subtype=subtype)
    store.put_article(a, {t: ["test"] for t in tags})


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "r.sqlite3")
    for i in range(3):
        _put(s, f"pmid:81{i}", f"Iron chelation protects MSA mice {i}", "animal", "positive", ["oxidative_iron"])
        _put(s, f"pmid:82{i}", f"Stem cells protect MSA mice {i}", "animal", "positive", ["neurotrophic"])
    _put(s, "pmid:830", "Oligodendroglial protection in MSA mice", "animal", "positive", ["oligodendroglia"])
    # Lee 2012 as the keyword reader saw it: a solid trial whose verdict was "unclear".
    _put(s, "pmid:22829267", "A randomized trial of mesenchymal stem cells in multiple system atrophy.", "clinical",
         "unclear", ["neurotrophic"], solid=True, subtype="msa_c", year=2012)
    # PROMESA as the keyword reader saw it: negative and counted for two mechanisms.
    _put(s, "pmid:31278067", "Safety and efficacy of epigallocatechin gallate in multiple system atrophy (PROMESA)",
         "clinical", "negative", ["asyn_aggregation", "oligodendroglia"], solid=True, year=2019)
    s.commit()
    yield s
    s.close()


def test_both_mechanisms_grade_b_and_the_why_line_names_the_trial(store):
    m = {s.mechanism.id: s for s in analysis.rank_mechanisms(store, curated=CURATED)}
    iron, neuro = m["oxidative_iron"], m["neurotrophic"]
    assert iron.grade == "B" and iron.grade_label == "B−"
    assert iron.grade_reason.startswith("ATH434-201 (randomized, placebo-controlled, Phase 2, 77 people; "
                                        "company-reported, not yet peer-reviewed)")
    assert "UMSARS part I" in iron.grade_reason
    assert iron.results["human"]["positive"] == 1                       # ATH434-202 (10 people) is listed, not counted
    assert neuro.grade == "B" and neuro.grade_label == "B+"
    assert neuro.grade_reason.startswith("Lee 2012, Annals of Neurology")
    assert neuro.results["human"] == {"positive": 1, "negative": 0, "mixed": 0, "unclear": 0}   # curated replaces "unclear"
    # PROMESA counts toward α-synuclein aggregation only
    assert m["asyn_aggregation"].solid_negative == ["pmid:31278067"]
    assert m["oligodendroglia"].results["human"]["negative"] == 0 and not m["oligodendroglia"].solid_negative
    # sorted best first: the B+ comes before the B−
    order = [s.mechanism.id for s in analysis.rank_mechanisms(store, curated=CURATED)]
    assert order.index("neurotrophic") < order.index("oxidative_iron")


def test_export_marks_curated_and_company_reported_results(store):
    files, _meta = documents(store, {}, build_time="2026-11-15T00:00:00Z")
    grades = {g["id"]: g for g in files["grades.json"]["mechanisms"]}
    assert (grades["oxidative_iron"]["grade"], grades["oxidative_iron"]["modifier"]) == ("B", "-")
    assert (grades["neurotrophic"]["grade"], grades["neurotrophic"]["modifier"]) == ("B", "+")
    assert grades["asyn_aggregation"]["modifier"] is None
    assert files["grades.json"]["meanings"]["D"] == "Not yet shown to work in people."
    assert "never changes the letter" in files["grades.json"]["definitions"]["grade_modifier"]
    iron = files["mechanisms/oxidative_iron.json"]["studies"]["human"]["positive"]
    by_uid = {s["uid"]: s for s in iron}
    assert by_uid["NCT05109091"]["source"] == "company_topline" and by_uid["NCT05109091"]["counted"] is True
    assert by_uid["NCT05864365"]["counted"] is False
    assert [s["uid"] for s in iron[:2]] == ["NCT05109091", "NCT05864365"]      # counted key result listed first
    neuro = files["mechanisms/neurotrophic.json"]["studies"]["human"]
    assert [s["uid"] for s in neuro["positive"]] == ["pmid:22829267"] and neuro["unclear"] == []
    assert neuro["positive"][0]["source"] == "peer_reviewed" and neuro["positive"][0]["curated"] is True
    oligo = files["mechanisms/oligodendroglia.json"]["studies"]["human"]["negative"]
    assert oligo == []                                                    # PROMESA isn't listed under oligodendroglia
    rows = [r for name, f in files.items() if name.startswith("library/") and name != "library/index.json" for r in f["rows"]]
    promesa = next(r for r in rows if r[0] == "pmid:31278067")
    assert promesa[10] == ["asyn_aggregation"]                           # library tags follow the curated mechanism


def test_published_export_grades_both_b_when_present():
    """Runs against the regenerated public-data/ export (skipped if it isn't there)."""
    path = ROOT / "public-data" / "grades.json"
    if not path.exists():
        pytest.skip("no public-data export")
    g = {m["id"]: m for m in json.loads(path.read_text(encoding="utf-8"))["mechanisms"]}
    if "modifier" not in g["oxidative_iron"]:
        pytest.skip("public-data was exported before curated results existed")
    assert (g["oxidative_iron"]["grade"], g["oxidative_iron"]["modifier"]) == ("B", "-")
    assert (g["neurotrophic"]["grade"], g["neurotrophic"]["modifier"]) == ("B", "+")
    assert "ATH434-201" in g["oxidative_iron"]["reason"] and "Lee 2012" in g["neurotrophic"]["reason"]
