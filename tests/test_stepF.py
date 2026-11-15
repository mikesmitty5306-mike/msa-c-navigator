"""Step F: plain-language letter grades, results by category, grades help page, evidence-label fixes."""

import copy
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from msac import analysis, mechanisms, pipeline  # noqa: E402
from msac.models import Article  # noqa: E402
from msac.sources import clinicaltrials  # noqa: E402
from msac.storage import Store  # noqa: E402
from test_core import NO_AUTH, STUDY  # noqa: E402
from test_stepB import _fill  # noqa: E402

MEANINGS = {
    "A": "Benefit on UMSARS or brain imaging in at least two controlled human trials of more than 20 people, "
         "with no major failures.",
    "B": "Benefit on UMSARS or brain imaging in one controlled human trial of more than 20 people, not yet repeated, "
         "supported by animal or lab work.",
    "C": "Promising results in animals or cells only, or mixed results in people.",
    "D": "Not yet shown to work in people.",
}


def key_result(i=0, source="peer_reviewed", n=40, randomized=True, placebo=True, umsars="improved", imaging=None,
               outcome="positive", mechanism="neuroinflammation"):
    return analysis.CuratedResult(ids=(f"NCT9{i:07d}",), label=f"Trial {i}", title=f"Trial {i}", mechanism=mechanism,
                                  outcome=outcome, randomized=randomized, placebo=placebo, phase="Phase 2", n=n,
                                  source=source, umsars=umsars, imaging=imaging, subtype="msa_general", year=2024,
                                  journal=None, url=None, note=f"Trial {i} slowed UMSARS worsening.")


def score(kind="neuroinflammation", human=None, animal=None, in_vitro=None, solid_pos=0, solid_neg=0, failed=0, keys=()):
    results = analysis._empty_results()
    for cat, counts in (("human", human), ("animal", animal), ("in_vitro", in_vitro)):
        results[cat].update(counts or {})
    keys = list(keys)
    results["human"]["positive"] += len(keys)          # key results are counted human results, as in rank_mechanisms
    return analysis.MechanismScore(
        mechanisms.BY_ID[kind], results=results, solid_positive=[f"p{i}" for i in range(solid_pos)],
        solid_negative=[f"n{i}" for i in range(solid_neg)], failed_trials=[f"NCT{i}" for i in range(failed)],
        curated=keys, qualifying=keys)


# ---------------------------------------------------------------- the four rules
@pytest.mark.parametrize("kwargs, grade, reason_part", [
    # A: benefit repeated in two key results (controlled, >20 people, UMSARS/imaging better), nothing major failed
    (dict(keys=[key_result(0), key_result(1)]), "A", "Benefit was repeated in 2 controlled human trials"),
    (dict(keys=[key_result(0), key_result(1)], human={"negative": 1}), "A", "smaller study found no benefit"),
    # two key results plus a major failure is no longer an A: results point both ways
    (dict(keys=[key_result(0), key_result(1)], human={"negative": 1}, solid_neg=1), "C", "Mixed in people"),
    (dict(keys=[key_result(0), key_result(1)], failed=1), "C", "stopped for futility"),
    # B: one key result, backed by animal or lab work, nothing against it in people. The Why line names the trial.
    (dict(keys=[key_result(0)], animal={"positive": 3}), "B", "Trial 0 (randomized, placebo-controlled, Phase 2, 40 people)"),
    (dict(keys=[key_result(0)], in_vitro={"positive": 1}), "B", "Not yet repeated in a second controlled trial"),
    # a keyword "solid" success alone is no longer enough: the key result must be hand-checked
    (dict(human={"positive": 1}, solid_pos=1, animal={"positive": 2}), "C", "none is yet a controlled trial of more than 20 people"),
    (dict(human={"positive": 2}, animal={"positive": 3}), "C", "none is yet a controlled trial"),
    # C: human benefit without any lab support; mixed in people; animals or cells only
    (dict(keys=[key_result(0)]), "C", "no animal or lab study supports it"),
    (dict(human={"positive": 1, "negative": 2}, animal={"positive": 5}), "C", "Mixed in people"),
    (dict(human={"mixed": 1}), "C", "gave mixed results"),
    (dict(animal={"positive": 4}, in_vitro={"positive": 1}), "C", "5 animal or lab studies reported benefit"),
    (dict(animal={"positive": 4}, human={"unclear": 6}), "C", "no human study has yet"),
    # D: little evidence, or human trials that failed or were stopped
    (dict(), "D", "Little evidence so far"),
    (dict(human={"unclear": 9}, animal={"unclear": 30, "negative": 2}), "D", "Little evidence so far"),
    (dict(human={"negative": 1}, animal={"positive": 10}, solid_neg=1), "D", "found no benefit (a solid trial)"),
    (dict(failed=2, animal={"positive": 3}), "D", "2 trials were stopped"),
])
def test_grade_rules(kwargs, grade, reason_part):
    s = score(**kwargs)
    assert s.grade == grade, s.grade_reason
    assert reason_part in s.grade_reason
    assert s.grade_meaning == MEANINGS[grade]


def test_biomarkers_not_graded():
    s = score(kind="biomarkers", human={"positive": 5}, solid_pos=2)
    assert s.grade == "n/a" and "not treatments" in s.grade_meaning


def test_meanings_are_the_agreed_wording():
    for g, text in MEANINGS.items():
        assert analysis.GRADE_MEANINGS[g] == text


def test_order_within_grades():
    better = score(human={"positive": 3}, animal={"positive": 2})
    worse = score(human={"positive": 1}, animal={"positive": 1})
    d = score()
    assert sorted([d, worse, better], key=analysis.MechanismScore.sort_key) == [better, worse, d]


# ---------------------------------------------------------------- "solid human trial"
@pytest.mark.parametrize("title, abstract, pub_types, solid", [
    ("Rasagiline in MSA-P: a randomised, double-blind, placebo-controlled trial", "", [], True),
    ("Drug X for nOH: a randomized, placebo-controlled, phase 3 trial",
     "After open-label titration, patients were randomized.", [], True),       # title wins over open-label run-in
    ("Drug Y in MSA", "", ["Randomized Controlled Trial"], True),
    ("Drug Y in MSA", "", ["Clinical Trial, Phase II"], True),
    ("Drug Z in MSA", "Patients were randomized to drug or placebo.", [], True),
    ("Safety of vaccine V: a randomised, single-blinded, phase 1 trial", "", ["Clinical Trial, Phase I", "Randomized Controlled Trial"], False),
    ("Octreotide in MSA: pilot trial", "Five patients received octreotide in an open trial.", ["Clinical Trial"], False),
    ("Open-label study of drug W", "Randomized to two doses with a placebo arm.", [], False),
    ("Post hoc analysis of a randomized placebo-controlled trial", "", ["Randomized Controlled Trial"], False),
    ("Drug Q in MSA", "Twelve patients received drug Q.", [], False),                # not randomized or controlled
])
def test_solid_trial_definition(title, abstract, pub_types, solid):
    assert mechanisms.is_solid_trial(title, abstract, pub_types) is solid


# ---------------------------------------------------------------- evidence label fixes found in the spot-check
@pytest.mark.parametrize("title, abstract, pub_types, mesh, expected", [
    # 1. reviews without PubMed's review type (new papers, preprints)
    ("Effects of rTMS on ataxia in MSA: A Systematic Review and Meta-Analysis", "We searched randomized trials.", ["Journal Article"], [], "review"),
    ("Antisense therapies in neurological diseases", "Therapies are presented. We also discuss preclinical work.", ["Journal Article"], [], "review"),
    ("The many faces of parkinsonism", "In this review we compare MSA and PD in vitro and in patients.", ["Preprint"], [], "review"),
    # 2. animal studies that mention patients and "randomized" in passing
    ("GV-971 attenuates synuclein aggregation", "A clinical unmet need exists for patients. Mice were randomized to GV-971.",
     ["Journal Article"], ["Animals", "Humans", "Mice"], "animal"),
    ("Melatonin in the zitter rat", "Patients with PD benefit. We treated rats.", ["Journal Article"], [], "animal"),
    # 3. cell work named in the title
    ("Generation of an induced pluripotent stem cell line from an MSA patient", "A 60-year-old patient donated cells.", [], ["Humans"], "in_vitro"),
    # 4. genuine human trials stay human, even when earlier mouse work is mentioned
    ("Drug X in MSA", "Drug X protected mice in earlier work. Here 24 patients were randomized to drug X or placebo.", [], ["Humans"], "clinical"),
    ("Drug Y in MSA: a randomized controlled trial", "", ["Randomized Controlled Trial"], ["Humans"], "clinical"),
])
def test_evidence_labels(title, abstract, pub_types, mesh, expected):
    text = " ".join([title, abstract, *mesh])
    assert mechanisms.classify_evidence(text, pub_types, title, mesh) == expected


# ---------------------------------------------------------------- counting into the four categories
def _article(uid, title, abstract, pub_types=(), mesh=()):
    return Article(uid=uid, title=title, abstract=abstract, pub_types=list(pub_types), mesh_terms=list(mesh), year=2024,
                   sources=["pubmed"])


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "r.sqlite3"
    _fill(db)
    s = Store(db)
    papers = [
        _article("pmid:9001", "Ubiquinol in multiple system atrophy: a randomized, placebo-controlled trial",
                 "CONCLUSIONS: Ubiquinol significantly slowed disease progression in patients.", ["Randomized Controlled Trial"], ["Humans"]),
        _article("pmid:9002", "Coenzyme Q10 in MSA mice", "CONCLUSION: CoQ10 treatment reduced alpha-synuclein aggregation in mice.",
                 [], ["Animals", "Mice"]),
        _article("pmid:9003", "Coenzyme Q10 open-label safety study", "CONCLUSION: Coenzyme Q10 treatment was well tolerated in 10 patients.", ["Clinical Trial"], ["Humans"]),
        _article("pmid:9004", "Mitochondrial rescue in cultured oligodendroglial cells",
                 "CONCLUSION: Treatment with the compound did not reduce mitochondrial damage.", [], []),
    ]
    for a in papers:
        pipeline.save_article(s, a)
    stopped = copy.deepcopy(STUDY)
    ps = stopped["protocolSection"]
    ps["identificationModule"] = {"nctId": "NCT00000002", "briefTitle": "Ubiquinol high dose in MSA"}
    ps["statusModule"] = {"overallStatus": "TERMINATED", "whyStopped": "Interim analysis showed futility"}
    pipeline.save_trial(s, clinicaltrials.parse_study(stopped))
    s.commit()
    yield s
    s.close()


def test_results_by_category(store):
    m = {r.mechanism.id: r for r in analysis.rank_mechanisms(store)}["mitochondria_coq10"]
    assert m.results["human"] == {"positive": 1, "negative": 0, "mixed": 0, "unclear": 1}   # safety-only counts as unclear
    assert m.results["animal"]["positive"] == 1
    assert m.results["in_vitro"]["negative"] == 1
    assert m.solid_positive == ["pmid:9001"]
    assert m.trial_counts == {"recruiting": 1, "completed": 0, "stopped": 1, "ongoing": 0}
    assert m.failed_trials == ["NCT00000002"]
    assert m.grade == "C" and "Mixed in people" in m.grade_reason                   # solid success, but a trial stopped for futility


# ---------------------------------------------------------------- pages
NO_SCORE = re.compile(r"of 100|success score|How the grade is built|weighted|\b50%|\b25%", re.I)


