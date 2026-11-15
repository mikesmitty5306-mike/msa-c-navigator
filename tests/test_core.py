"""Offline tests: parsers, classifiers, scoring, storage and web smoke test."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from msac import analysis, mechanisms, pipeline  # noqa: E402
from msac.models import Article, Trial  # noqa: E402
from msac.sources import clinicaltrials, europepmc, pubmed  # noqa: E402
from msac.storage import Store  # noqa: E402

# Earlier tests exercise pages, not security: skip the passcode and CSRF here (tested in test_stepC.py).
NO_AUTH = {"LOGIN_DISABLED": True, "WTF_CSRF_ENABLED": False}

PUBMED_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>111</PMID>
<Article><Journal><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue><Title>Mov Disord</Title></Journal>
<ArticleTitle>Purkinje cell loss in <i>MSA-C</i> patients</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Glial cytoplasmic inclusions and microglial activation.</AbstractText></Abstract>
<AuthorList><Author><LastName>Doe</LastName><Initials>J</Initials></Author></AuthorList>
<PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList></Article>
<MeshHeadingList><MeshHeading><DescriptorName>Multiple System Atrophy</DescriptorName></MeshHeading></MeshHeadingList>
</MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/ABC</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle></PubmedArticleSet>"""

STUDY = {"protocolSection": {
    "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Ubiquinol in MSA"},
    "statusModule": {"overallStatus": "RECRUITING", "lastUpdatePostDateStruct": {"date": "2026-01-01"}},
    "designModule": {"phases": ["PHASE2"], "studyType": "INTERVENTIONAL"},
    "conditionsModule": {"conditions": ["Multiple System Atrophy"]},
    "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "Ubiquinol"}]},
    "eligibilityModule": {"eligibilityCriteria": "Inclusion Criteria:\n* MSA-P or MSA-C\n* Age 30-80\n\nExclusion Criteria:\n* Dementia",
                          "minimumAge": "30 Years", "maximumAge": "80 Years", "sex": "ALL"},
    "contactsLocationsModule": {
        "centralContacts": [{"name": "Study Coordinator", "email": "study@example.org", "phone": "555-0100"}],
        "locations": [
            {"facility": "Far Clinic", "city": "Tokyo", "country": "Japan", "status": "RECRUITING",
             "geoPoint": {"lat": 35.68, "lon": 139.69}},
            {"facility": "Near Clinic", "city": "Boston", "country": "United States", "status": "RECRUITING",
             "geoPoint": {"lat": 42.36, "lon": -71.06}},
        ]},
}}


def test_pubmed_parse():
    (a,) = list(pubmed.parse_efetch(PUBMED_XML))
    assert a.uid == "pmid:111" and a.year == 2024 and a.doi == "10.1/ABC"
    assert "MSA-C" in a.title and a.abstract.startswith("BACKGROUND:")
    assert a.authors == ["Doe J"]


def test_europepmc_preprint_parse():
    a = europepmc.parse_result({"id": "PPR1", "source": "PPR", "title": "<i>x</i> MSA", "doi": "10.2/X",
                                "pubYear": "2025", "authorString": "A B, C D."})
    assert a.is_preprint and a.uid == "doi:10.2/x" and a.title == "x MSA" and a.authors == ["A B", "C D"]


def test_merge_keeps_richest():
    a = Article(uid="pmid:1", title="T", sources=["pubmed"], pub_types=["Journal Article"])
    b = Article(uid="pmid:1", title="T", abstract="long abstract", sources=["europepmc"], cited_by=7)
    m = a.merge(b)
    assert m.abstract == "long abstract" and m.cited_by == 7 and m.sources == ["pubmed", "europepmc"]


def test_tagging_and_classifiers():
    tags = mechanisms.tag_text("Anle138b reduced alpha-synuclein aggregation and microglial activation")
    assert {"asyn_aggregation", "neuroinflammation"} <= set(tags)
    assert mechanisms.article_subtype("cerebellar type of multiple system atrophy") == "msa_c"
    assert mechanisms.article_subtype("MSA-P cohort") == "msa_p"
    assert mechanisms.classify_evidence("transgenic mice", []) == "animal"
    assert mechanisms.classify_evidence("x", ["Randomized Controlled Trial"]) == "clinical"


@pytest.mark.parametrize("elig,title,expected", [
    ("Inclusion Criteria:\n* MSA-P or MSA-C\nExclusion Criteria:\n* none", "", "includes_msa_c"),
    ("Inclusion Criteria:\n* MSA-P\nExclusion Criteria:\n* MSA-C", "", "excludes_msa_c"),
    ("Inclusion Criteria:\n* probable MSA-P", "", "msa_p_focused"),
    ("Inclusion Criteria:\n* probable MSA", "", "any_msa"),
    # Regression: adjacent condition names must not read as the MSA-P subtype
    ("", "CPAP study; Multiple System Atrophy; Parkinson Disease", "any_msa"),
    ("Inclusion Criteria:\n* Lewy Body Disease, Multiple System Atrophy, Parkinson's Disease (PD)", "", "any_msa"),
    ("", "Follow-up; Multiple System Atrophy, Parkinson Variant", "msa_p_focused"),
    ("", "Trial; Multiple System Atrophy - Parkinsonian Subtype (MSA-P)", "msa_p_focused"),
    ("", "Registry; Multiple System Atrophy, Parkinsonian Type; Multiple System Atrophy, Cerebellar Type", "includes_msa_c"),
])
def test_trial_subtype_fit(elig, title, expected):
    assert mechanisms.trial_subtype_fit(title, elig) == expected


@pytest.mark.parametrize("title,abstract,evidence,expected", [
    ("Rasagiline in MSA-P: a randomised placebo-controlled trial", "CONCLUSIONS: Treatment with rasagiline did not show a significant benefit.", "clinical", "negative"),
    ("Drug X in PLP-SYN mice", "Results... In conclusion, drug X significantly reduced alpha-synuclein aggregation.", "animal", "positive"),
    ("Drug Y open-label study", "CONCLUSION: Drug Y was well tolerated.", "clinical", "safety"),
    ("PET tracer uptake in MSA", "Uptake did not differ after dosing of the tracer.", "clinical", "na"),
    ("Natural history cohort", "Patients progressed rapidly.", "human_observational", "na"),
])
def test_outcome_classifier(title, abstract, evidence, expected):
    assert mechanisms.classify_outcome(title, abstract, evidence) == expected


def test_failed_trial_detection():
    assert mechanisms.trial_stopped_for_failure("TERMINATED", "Interim analysis showed futility")
    assert not mechanisms.trial_stopped_for_failure("TERMINATED", "Lost funding")
    assert not mechanisms.trial_stopped_for_failure("COMPLETED", "futility")


def test_grade_rewards_results_not_volume():
    """Grades come from study results; a pile of papers with no results earns nothing."""
    m = mechanisms.BY_ID["neuroinflammation"]
    many_papers = analysis.MechanismScore(m, n_articles=900, literature_score=400.0)
    assert many_papers.grade == "D" and "Little evidence" in many_papers.grade_reason
    results = analysis._empty_results()
    results["human"]["positive"] = 2
    results["animal"]["positive"] = 3
    unchecked = analysis.MechanismScore(m, n_articles=20, results=results)
    assert unchecked.grade == "C"          # B needs a key result: a controlled trial of >20 people improving UMSARS/imaging
    key = analysis.CuratedResult(ids=("NCT00000009",), label="Trial X", title="Trial X", mechanism=m.id, outcome="positive",
                                 randomized=True, placebo=True, phase="Phase 2", n=40, source="peer_reviewed", umsars="improved",
                                 imaging=None, subtype="msa_general", year=2024, journal=None, url=None, note="Slowed UMSARS.")
    promising = analysis.MechanismScore(m, n_articles=20, results=results, curated=[key], qualifying=[key])
    assert promising.grade == "B"


def test_unrelated_trials_dropped():
    s = Store(":memory:")
    hd = Trial(nct_id="NCT1", title="Tominersen in Huntington Disease", conditions=["Huntington Disease"],
               eligibility="Inclusion Criteria:\n* HD\nExclusion Criteria:\n* multiple system atrophy")
    assert pipeline.save_trial(s, hd) is False and s.get_trial("NCT1") is None
    assert pipeline.save_trial(s, clinicaltrials.parse_study(STUDY)) is True


def test_trial_parse_and_enrollment():
    t = clinicaltrials.parse_study(STUDY)
    pipeline.annotate_trial(t)
    assert t.is_open and t.max_phase == 2 and t.subtype_fit == "includes_msa_c"
    info = analysis.enrollment_info(t, lat=40.71, lon=-74.01)  # New York
    assert info["sites"][0]["facility"] == "Near Clinic"
    assert info["mailto"].startswith("mailto:study@example.org?subject=")
    assert info["inclusion"] == ["MSA-P or MSA-C", "Age 30-80"] and info["exclusion"] == ["Dementia"]
    assert analysis.filter_trials([t], age=25) == []
    assert len(analysis.filter_trials([t], age=60, country="Japan")) == 1


@pytest.fixture
def store():
    s = Store(":memory:")
    for a in pubmed.parse_efetch(PUBMED_XML):
        pipeline.save_article(s, a)
    pipeline.save_trial(s, clinicaltrials.parse_study(STUDY))
    s.commit()
    return s


def test_ranking(store):
    ranked = {r.mechanism.id: r for r in analysis.rank_mechanisms(store, today=date(2026, 6, 1))}
    assert ranked["cerebellar_degeneration"].n_msa_c == 1
    assert ranked["mitochondria_coq10"].trial_ids == ["NCT00000001"]
    assert ranked["mitochondria_coq10"].max_phase_label == "Phase 2"
    assert ranked["mitochondria_coq10"].trial_counts["recruiting"] == 1
    top = analysis.rank_open_trials(store)
    assert [r["trial"].nct_id for r in top] == ["NCT00000001"] and 0 < top[0]["score"] <= 100
    items, total = store.search_articles(q="purkinje", mechanism="cerebellar_degeneration")
    assert total == 1 and items[0].subtype == "msa_c"


