"""Mechanism taxonomy and text classifiers.

The taxonomy is a curated, editable list of biological mechanisms implicated in
MSA. Each entry has regex patterns for the concept plus names of investigational
agents that act through it, so trials and papers about a named drug are linked to
its mechanism even when the mechanism words are absent.

Classification is deliberately transparent (regex hits are stored and shown in
the UI) so users can see *why* an item was tagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SYN = r"(?:alpha|α|a)[- ]?syn(?:uclein)?"


@dataclass(frozen=True)
class Mechanism:
    id: str
    name: str
    kind: str  # "mechanism" | "biomarker" | "symptomatic"
    description: str
    patterns: tuple[str, ...]
    agents: tuple[str, ...] = field(default=())

    def compiled(self) -> list[tuple[str, re.Pattern]]:
        out = [(p, re.compile(p, re.I)) for p in self.patterns]
        out += [(a, re.compile(rf"\b{re.escape(a)}\b", re.I)) for a in self.agents]
        return out


TAXONOMY: tuple[Mechanism, ...] = (
    Mechanism(
        "asyn_aggregation", "α-Synuclein aggregation & spread", "mechanism",
        "Misfolded α-synuclein forms glial cytoplasmic inclusions (GCIs) in oligodendrocytes and "
        "spreads cell-to-cell in a prion-like way. Small molecules aim to block aggregation.",
        (rf"{SYN}\W+(?:\w+\W+){{0,3}}(?:aggregat|fibril|oligomer|misfold|seed|propagat|spread)",
         r"glial cytoplasmic inclusion", r"\bGCIs?\b", r"prion-?like", r"synuclein strain",
         r"aggregation inhibitor"),
        ("anle138b", "emrusolmin", "TEV-56286", "EGCG", "epigallocatechin", "rifampicin",
         "minzasolmin", "UCB0599", "NPT200-11"),
    ),
    Mechanism(
        "asyn_immunotherapy", "α-Synuclein immunotherapy", "mechanism",
        "Antibodies (passive) or vaccines (active) that bind extracellular α-synuclein to "
        "limit spread.",
        (rf"{SYN}\W+(?:\w+\W+){{0,3}}(?:antibod|immunotherap|vaccin|immuniz)",
         r"anti-?\W?(?:alpha|α|a)[- ]?synuclein", r"active immuni[sz]ation", r"passive immuni[sz]ation"),
        ("prasinezumab", "PRX002", "Lu AF82422", "amlenetug", "cinpanemab", "BIIB054",
         "AFFITOPE", "PD01A", "PD03A", "ACI-7104", "UB-312"),
    ),
    Mechanism(
        "asyn_lowering", "α-Synuclein lowering (ASO / gene silencing)", "mechanism",
        "Antisense oligonucleotides or RNA interference that reduce SNCA expression.",
        (r"antisense oligonucleotide", r"\bASOs?\b", r"\bsiRNA\b", r"RNA interference",
         r"SNCA\W+(?:\w+\W+){0,2}(?:knock-?down|silenc|lower)", r"gene silencing"),
        ("ION464", "BIIB101"),
    ),
    Mechanism(
        "oligodendroglia", "Oligodendroglial dysfunction & myelin", "mechanism",
        "MSA is primarily an oligodendrogliopathy: TPPP/p25α relocalisation, myelin loss and "
        "failed trophic support of axons precede neuronal loss.",
        (r"oligodendr\w*", r"\bmyelin\w*", r"demyelinat\w*", r"\bTPPP\b", r"p25(?:α|alpha)",
         r"white matter (?:degeneration|loss|damage|injury)", r"\bS1P5\b"),
        ("ONO-2808",),  # S1P5 receptor agonist acting on oligodendrocytes
    ),
    Mechanism(
        "neuroinflammation", "Neuroinflammation & immune modulation", "mechanism",
        "Microglial and astroglial activation, TLR4 / NLRP3 inflammasome signalling and "
        "myeloperoxidase-driven oxidative damage.",
        (r"neuroinflammat\w*", r"microgli\w*", r"astrogli\w*|astrocyt\w*", r"\bTLR-?4\b",
         r"toll-like receptor", r"\bNLRP3\b", r"inflammasome", r"myeloperoxidase", r"\bTSPO\b",
         r"intravenous immunoglobulin", r"\bIVIG\b"),
        ("verdiperstat", "BHV-3241", "minocycline"),
    ),
    Mechanism(
        "mitochondria_coq10", "Mitochondrial dysfunction & CoQ10", "mechanism",
        "COQ2 variants and reduced coenzyme Q10 in MSA brain implicate impaired "
        "mitochondrial respiration; ubiquinol supplementation is under trial.",
        (r"mitochondri\w*", r"\bCOQ2\b", r"coenzyme Q10", r"\bCoQ10\b", r"respiratory chain",
         r"oxidative phosphorylation", r"\bNAD\+?(?=\W)", r"nicotinamide riboside"),
        ("ubiquinol",),
    ),
    Mechanism(
        "oxidative_iron", "Oxidative stress & iron", "mechanism",
        "Iron accumulation and reactive oxygen species promote α-synuclein aggregation and "
        "cell death (ferroptosis).",
        (r"oxidative stress", r"reactive oxygen species", r"\biron\b", r"ferroptos\w*",
         r"lipid peroxidation", r"chelat\w*"),
        ("ATH434", "PBT434", "deferiprone"),
    ),
    Mechanism(
        "proteostasis", "Autophagy, lysosome & proteostasis", "mechanism",
        "Failure to clear misfolded proteins via autophagy, the lysosome (incl. GBA) or the "
        "ubiquitin-proteasome system.",
        (r"autophag\w*", r"lysosom\w*", r"proteasom\w*", r"\bchaperone", r"heat shock protein",
         r"\bGBA1?\b", r"glucocerebrosidase", r"\bmTOR\b", r"proteostasis"),
        ("sirolimus", "rapamycin", "lithium", "ambroxol"),
    ),
    Mechanism(
        "neurotrophic", "Neurotrophic & regenerative therapy", "mechanism",
        "Restoring trophic support (GDNF, BDNF) or replacing/supporting cells with "
        "mesenchymal stem cells.",
        (r"\bGDNF\b", r"\bBDNF\b", r"neurotrophic factor", r"mesenchymal stem", r"stem cell",
         r"cell therapy", r"\bMSCs?\b", r"progenitor cells?", r"nerve graft\w*"),
        ("AB-1005", "AAV2-GDNF", "NouvNeu001"),
    ),
    Mechanism(
        "cerebellar_degeneration", "Cerebellar & olivopontocerebellar degeneration", "mechanism",
        "Purkinje cell loss and degeneration of pontine nuclei, middle cerebellar peduncles and "
        "inferior olives: the defining pathology of MSA-C.",
        (r"purkinje", r"olivopontocerebellar", r"pontine nucle\w*", r"middle cerebellar peduncle",
         r"cerebellar (?:degeneration|atrophy|neuron)", r"hot cross bun", r"inferior oliv\w*",
         r"dentate nucleus"),
    ),
    Mechanism(
        "genetics", "Genetic risk factors", "mechanism",
        "Inherited variants (e.g. COQ2, SNCA, GBA) and genome-wide association findings.",
        (r"genome-wide association", r"\bGWAS\b", r"polymorphism", r"genetic (?:risk|variant|factor)",
         r"whole[- ]exome", r"whole[- ]genome sequencing", r"\bvariants? in\b"),
    ),
    Mechanism(
        "gut_brain", "Gut–brain axis & microbiome", "mechanism",
        "Altered gut microbiota and enteric α-synuclein as potential disease drivers.",
        (r"microbio\w*", r"gut[- ]brain", r"enteric nervous", r"intestinal permeability"),
    ),
    Mechanism(
        "biomarkers", "Biomarkers & early diagnosis", "biomarker",
        "Tools that make trials possible: α-synuclein seed amplification assays, neurofilament "
        "light, skin biopsy, imaging. Not a disease mechanism itself.",
        (r"seed amplification", r"RT-?QuIC", r"\bPMCA\b", r"neurofilament light", r"\bNfL\b",
         r"skin biops\w*", r"\bbiomarkers?\b", r"diagnostic accuracy", r"\bPET\b"),
    ),
    Mechanism(
        "autonomic_symptomatic", "Symptomatic & rehabilitative treatment", "symptomatic",
        "Treatments for symptoms (orthostatic hypotension, bladder, sleep, ataxia, gait) that do not "
        "target the underlying disease, including brain and spinal stimulation and rehabilitation.",
        (r"orthostatic hypotension", r"neurogenic", r"urinary", r"bladder", r"stridor",
         r"sleep apnea|REM sleep behavio", r"symptomatic treatment", r"rehabilitation|physiotherap",
         r"transcranial (?:direct|alternating|magnetic)", r"\btDCS\b", r"\btACS\b", r"\brTMS\b",
         r"(?:spinal cord|epidural) (?:electrical )?stimulation"),
        ("droxidopa", "midodrine", "ampreloxetine", "atomoxetine", "pyridostigmine", "fludrocortisone"),
    ),
)

BY_ID = {m.id: m for m in TAXONOMY}
_COMPILED = {m.id: m.compiled() for m in TAXONOMY}


def tag_text(text: str) -> dict[str, list[str]]:
    """Return {mechanism_id: [matched snippets]} for every mechanism mentioned in text."""
    tags: dict[str, list[str]] = {}
    for mid, patterns in _COMPILED.items():
        hits: list[str] = []
        for _src, rx in patterns:
            for m in rx.finditer(text):
                snippet = m.group(0).strip().lower()
                if snippet not in hits:
                    hits.append(snippet)
                if len(hits) >= 8:
                    break
        if hits:
            tags[mid] = hits
    return tags


# ---------------------------------------------------------------- subtype
MSA_C_RE = re.compile(
    r"\bMSA[- ]?C\b|\bcerebellar (?:sub)?type|\bcerebellar variant|olivopontocerebellar atroph"
    r"|\bMSA\s*[-–(]\s*cerebellar|cerebellar (?:form|predominant|phenotype)|cerebellar multiple system atrophy"
    r"|multiple system atrophy\s*[-–,(]\s*cerebellar|predominant cerebellar ataxia|cerebellar ataxia type",
    re.I,
)
MSA_P_RE = re.compile(
    r"\bMSA[- ]?P\b|\bparkinsonian (?:sub)?type|\bparkinsonian variant|striatonigral degeneration"
    # Require a joining hyphen/comma/bracket: "Multiple System Atrophy Parkinson Disease" in a
    # condition list is two separate conditions, not the parkinsonian subtype.
    r"|(?:multiple system atrophy|\bMSA)\s*[-–,(]\s*parkinson(?:ian|\s+(?:type|variant|subtype))",
    re.I,
)


MSA_ANY_RE = re.compile(
    r"multiple system atrophy|\bMSA(?:[- ]?[CP])?\b|olivopontocerebellar|shy[- ]drager|striatonigral degeneration", re.I)


def trial_is_msa_relevant(title_text: str, summary: str, eligibility: str) -> bool:
    """Registry synonym expansion returns some unrelated studies (e.g. Huntington's).
    Keep trials that name MSA outside the exclusion criteria."""
    inclusion, _ = split_eligibility(eligibility)
    return bool(MSA_ANY_RE.search(title_text) or MSA_ANY_RE.search(summary) or MSA_ANY_RE.search(inclusion))


def article_subtype(text: str) -> str:
    """msa_c: discusses the cerebellar subtype; msa_p: only the parkinsonian; msa_general otherwise."""
    if MSA_C_RE.search(text):
        return "msa_c"
    if MSA_P_RE.search(text):
        return "msa_p"
    return "msa_general"


def split_eligibility(text: str) -> tuple[str, str]:
    parts = re.split(r"exclusion criteria\s*:?", text or "", maxsplit=1, flags=re.I)
    return parts[0], parts[1] if len(parts) > 1 else ""


def trial_subtype_fit(title_text: str, eligibility: str) -> str:
    """Classify whether a trial is open to people with MSA-C."""
    inclusion, exclusion = split_eligibility(eligibility)
    if MSA_C_RE.search(inclusion) or MSA_C_RE.search(title_text):
        return "includes_msa_c"
    if MSA_C_RE.search(exclusion):
        return "excludes_msa_c"
    if MSA_P_RE.search(inclusion) or MSA_P_RE.search(title_text):
        return "msa_p_focused"
    return "any_msa"


SUBTYPE_FIT_LABELS = {
    "includes_msa_c": "Explicitly includes MSA-C",
    "any_msa": "MSA, subtype not restricted",
    "msa_p_focused": "Focused on MSA-P (check eligibility)",
    "excludes_msa_c": "Excludes MSA-C",
}
ARTICLE_SUBTYPE_LABELS = {"msa_c": "MSA-C", "msa_p": "MSA-P", "msa_general": "MSA (general)"}

# ---------------------------------------------------------------- evidence level
_CLINICAL_PT = re.compile(r"clinical trial|randomized controlled trial|controlled clinical trial", re.I)
_REVIEW_PT = re.compile(r"\breview\b|meta-analysis", re.I)
_CLINICAL_TX = re.compile(r"randomi[sz]ed|placebo[- ]controlled|\bphase (?:1|2|3|I|II|III)\b|open[- ]label", re.I)
_POSTMORTEM_TX = re.compile(r"post[- ]?mortem|autops\w+|brain bank|neuropatholog\w+", re.I)
_ANIMAL_TX = re.compile(r"\bmice\b|\bmouse\b|\brats?\b|transgenic|animal model|drosophila|zebrafish|\bprimates?\b|macaque", re.I)
_INVITRO_TX = re.compile(r"cell line|in vitro|cultured|\biPSCs?\b|induced pluripotent|organoid", re.I)
_HUMAN_TX = re.compile(r"\bpatients\b|\bcohort\b|participants|case report|case series|registry", re.I)

EVIDENCE_LABELS = {
    "clinical": "Clinical trial",
    "human_observational": "Human observational",
    "postmortem": "Human neuropathology",
    "animal": "Animal model",
    "in_vitro": "Cell / in vitro",
    "review": "Review",
    "other": "Other",
}
EVIDENCE_WEIGHT = {
    "clinical": 3.0, "human_observational": 2.0, "postmortem": 2.0,
    "animal": 1.5, "in_vitro": 1.0, "review": 0.5, "other": 0.75,
}


# ---------------------------------------------------------------- study outcome
# Applied only to treatment studies (clinical, animal, cell). Reads the conclusion, where authors state
# whether the intervention worked. Keyword-based, so it is approximate; counts are shown so users can check.
_TREATMENT_TX = re.compile(
    r"\b(?:treat(?:ed|ment|ing)?|therap\w*|administ\w*|placebo|dos(?:e|es|ing)|drug|compound|supplement\w*"
    r"|intervention|transplant\w*|infusion|injection|immuni[sz]\w*|vaccin\w*|stimulation|inhibitor|agonist)\b", re.I)
_NEG_TX = re.compile(
    r"did not (?:significantly |show |demonstrate |improve|slow|reduce|meet|differ|change|halt|delay|modify|alter)"
    r"|failed to|no significant (?:effect|difference|benefit|improvement|change|reduction|slowing)"
    r"|was not (?:superior|effective|associated with (?:improve|benefit|slow))|no (?:clinical |therapeutic |disease[- ]modifying )?(?:benefit|efficacy)"
    r"|lack of (?:efficacy|benefit)|futility|ineffective|did not reach|not met|no evidence (?:of|for) (?:efficacy|benefit)"
    r"|did not translate|not slow", re.I)
_POS_TX = re.compile(
    r"significantly (?:reduced|improved|slowed|decreased|attenuated|ameliorated|rescued|delayed|prolonged|preserved|protected|lowered)"
    r"|met (?:its|the) primary (?:end ?point|outcome)|slow(?:ed|s) (?:the )?(?:disease )?progression"
    r"|neuroprotecti\w+ effect|(?:rescued|ameliorated|attenuated|prevented|reversed|reduced|decreased) (?:the )?(?:\w+[- ]){0,3}"
    r"(?:synuclein|aggregat\w*|inclusion|neurodegeneration|neuronal loss|cell loss|inflammation|microglial activation|motor (?:deficits|decline|impairment))"
    r"|improved (?:motor|survival|symptoms|scores|outcomes|function)|beneficial effects?|(?:was|were|is) effective"
    r"|effective in (?:reducing|improving|slowing)|promising (?:results|effect|efficacy|candidate|therapeutic)|disease[- ]modifying effect", re.I)
_SAFE_TX = re.compile(r"well[- ]tolerated|acceptable safety|favou?rable safety|safe and tolerable|no (?:serious )?safety (?:concerns|signals)", re.I)
_CONCLUSION = re.compile(r"(?:^|\n)\s*(?:CONCLUSIONS?|INTERPRETATION|SIGNIFICANCE|DISCUSSION)\s*(?:AND RELEVANCE)?\s*:\s*(.+)", re.I | re.S)

_DIAGNOSTIC_TITLE = re.compile(
    r"\b(?:PET|SPECT|tracer|ligand|scintigraph\w*|imaging|MRI|diagnos\w*|differentiat\w*|biomarkers?|uptake|genotypes?)\b", re.I)

OUTCOME_LABELS = {"positive": "Positive result", "negative": "Negative result", "mixed": "Mixed result",
                  "safety": "Safety only", "unclear": "Result unclear", "na": "Not a treatment study"}


def _conclusion(abstract: str) -> str:
    m = _CONCLUSION.search(abstract or "")
    if m:
        return m.group(1)
    sentences = re.split(r"(?<=[.!?])\s+", (abstract or "").strip())
    return " ".join(sentences[-3:])


def classify_outcome(title: str, abstract: str, evidence: str) -> str:
    if evidence not in ("clinical", "animal", "in_vitro"):
        return "na"
    focus = f"{title}. {_conclusion(abstract)}"
    # The treatment must be what the conclusion is about; diagnostic imaging studies are excluded.
    if not _TREATMENT_TX.search(focus):
        return "na"
    if _DIAGNOSTIC_TITLE.search(title) and not _TREATMENT_TX.search(title):
        return "na"
    pos, neg = bool(_POS_TX.search(focus)), bool(_NEG_TX.search(focus))
    if pos and neg:
        return "mixed"
    if pos:
        return "positive"
    if neg:
        return "negative"
    if _SAFE_TX.search(focus):
        return "safety"
    return "unclear"


# Stops that say something about the treatment (futility, efficacy, safety, sponsor reading the results);
# stops for funding or slow recruitment are not counted against the mechanism.
_FAILED_STOP = re.compile(
    r"futil|efficac|lack of (?:benefit|effect)|did not meet|primary (?:end ?point|outcome)|interim analysis|no benefit"
    r"|ineffect|safety (?:issue|concern|reason)s?|for safety|analysis results", re.I)


def trial_stopped_for_failure(status: str, why_stopped: str) -> bool:
    return status in ("TERMINATED", "SUSPENDED") and bool(_FAILED_STOP.search(why_stopped or ""))


# Reviews not (yet) tagged as such by PubMed, e.g. new papers and preprints.
_REVIEW_TITLE = re.compile(r"systematic review|meta-analys[ie]s|narrative review|scoping review|umbrella review"
                           r"|\ba review\b|\breview of\b|literature review|state of the art|current perspectives"
                           r"|\boverview\b|: a review|\bupdate on\b", re.I)
_REVIEW_ABSTRACT = re.compile(r"\b(?:this|the present|our|current) (?:narrative |systematic |mini[- ]?|comprehensive )?review\b"
                              r"|\bwe (?:here )?review\b|\bhere,? we review|\bin this (?:mini[- ]?)?review|\bwe summari[sz]e\b"
                              r"|\bthis article reviews\b|\bwe (?:also |further |then )?(?:discuss|summari[sz]e)\b"
                              r"|\bare (?:reviewed|summari[sz]ed) here\b", re.I)
# MeSH species tags mark animal experiments reliably (PubMed adds "Animals" plus the species).
_ANIMAL_MESH = {"Mice", "Rats", "Mice, Transgenic", "Disease Models, Animal", "Drosophila", "Drosophila melanogaster",
                "Zebrafish", "Macaca", "Macaca mulatta", "Primates", "Caenorhabditis elegans", "Mice, Inbred C57BL",
                "Rats, Sprague-Dawley", "Rats, Wistar", "Swine", "Dogs", "Sheep"}
_INVITRO_TITLE = re.compile(r"cell line|in vitro|cultured|\biPSCs?\b|induced pluripotent|organoid|\bcells\b|patient-derived", re.I)
# A trial in people names how many people took part or how they were assigned.
_PEOPLE_STUDIED = re.compile(
    r"\b\d+\s+(?:patients|participants|subjects|volunteers|individuals)\b"
    r"|\b(?:patients|participants|subjects)\s+(?:were|was)\s+(?:randomi[sz]ed|enrolled|recruited|assigned|treated|included|given)"
    r"|\b(?:enrolled|recruited|randomi[sz]ed)\s+\d+\b", re.I)


# ---------------------------------------------------------------- "solid human trial"
_SOLID_PT = re.compile(r"randomized controlled trial|clinical trial, phase (?:ii|iii|iv)\b|controlled clinical trial", re.I)
_RANDOMIZED = re.compile(r"randomi[sz]ed", re.I)
_CONTROLLED = re.compile(r"placebo|sham|double[- ]blind|controlled", re.I)
_EARLY_OR_SMALL = re.compile(r"\bphase\s*(?:1|i)\b|\bpilot\b|feasibility|open[- ]label|single[- ]arm|case series"
                             r"|retrospective|uncontrolled", re.I)
_SECONDARY_TITLE = re.compile(r"\bpost[- ]hoc\b|extension|exploratory|secondary analysis|sub-?study", re.I)
_PHASE_I_PT = re.compile(r"clinical trial, phase i\b(?!i)", re.I)


def is_solid_trial(title: str, abstract: str, pub_types: list[str]) -> bool:
    """A "solid human trial": randomized AND controlled (placebo, sham or comparator), and not described as
    phase 1, pilot, feasibility, open-label, single-arm, retrospective or uncontrolled. Papers whose title says they
    are a post hoc, exploratory, secondary or extension analysis don't count either.
    PubMed's "Randomized Controlled Trial" or "Phase II+" types count as randomized and controlled."""
    pt = " | ".join(pub_types)
    if _PHASE_I_PT.search(pt) or _SECONDARY_TITLE.search(title) or _EARLY_OR_SMALL.search(title):
        return False
    # When the title itself states a randomized, controlled design (e.g. "a randomized, placebo-controlled,
    # phase 3 trial"), trust it: abstracts of such trials often mention an open-label run-in or extension.
    if _RANDOMIZED.search(title) and _CONTROLLED.search(title):
        return True
    # A full trial often mentions a later "open-label extension"; that alone doesn't make it early or small.
    text = re.sub(r"open[- ]label (?:extension|run-in|titration|phase|period)", "", f"{title} {abstract}", flags=re.I)
    if _EARLY_OR_SMALL.search(text):
        return False
    return bool(_SOLID_PT.search(pt) or (_RANDOMIZED.search(text) and _CONTROLLED.search(text)))


def classify_evidence(text: str, pub_types: list[str], title: str = "", mesh: list[str] | tuple = ()) -> str:
    """Label what kind of study a paper reports. Order matters: the most reliable signals come first.

    1. PubMed publication types (clinical trial, review)
    2. reviews recognisable from the title or abstract wording
    3. animal experiments: MeSH species tags, or an animal named in the title
    4. lab work on cells: named in the title
    5. trials in people: design words (randomized, placebo, phase, open-label) and people studied
    6. weaker text cues for the remaining types
    """
    pt = " | ".join(pub_types)
    if _CLINICAL_PT.search(pt):
        return "clinical"
    # Reviews often mention "randomized" when summarising others' trials.
    if _REVIEW_PT.search(pt) or _REVIEW_TITLE.search(title) or _REVIEW_ABSTRACT.search(text):
        return "review"
    mesh_set = set(mesh)
    if ("Animals" in mesh_set and mesh_set & _ANIMAL_MESH) or _ANIMAL_TX.search(title):
        return "animal"
    if _INVITRO_TITLE.search(title):
        return "in_vitro"
    # Animal studies often say "randomized" too; without people studied they are animal work.
    if _ANIMAL_TX.search(text) and not _PEOPLE_STUDIED.search(text):
        return "animal"
    if _CLINICAL_TX.search(text) and (_PEOPLE_STUDIED.search(text) or _HUMAN_TX.search(text)):
        return "clinical"
    if _POSTMORTEM_TX.search(text):
        return "postmortem"
    if _ANIMAL_TX.search(text):
        return "animal"
    if _INVITRO_TX.search(text):
        return "in_vitro"
    if _HUMAN_TX.search(text) or "case reports" in pt.lower():
        return "human_observational"
    return "other"
