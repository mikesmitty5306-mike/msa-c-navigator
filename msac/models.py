"""Core data records shared by sources, storage, analysis and UI."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

OPEN_STATUSES = {"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "AVAILABLE"}
ACTIVE_STATUSES = OPEN_STATUSES | {"ACTIVE_NOT_RECRUITING"}

PHASE_RANK = {"EARLY_PHASE1": 0.5, "PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 3}


def _from_dict(cls, data: dict[str, Any]):
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Article:
    uid: str
    title: str = ""
    abstract: str = ""
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    journal: str = ""
    year: int | None = None
    pub_date: str | None = None
    authors: list[str] = field(default_factory=list)
    pub_types: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    is_preprint: bool = False
    cited_by: int | None = None
    alt_url: str | None = None
    # Derived during annotation
    subtype: str = "msa_general"
    evidence: str = "other"
    outcome: str = "na"  # positive | negative | mixed | safety | unclear | na (not a treatment study)
    solid_trial: bool = False  # randomized, controlled human trial past phase 1 (see mechanisms.is_solid_trial)

    @staticmethod
    def make_uid(pmid: str | None, doi: str | None, fallback: str) -> str:
        if pmid:
            return f"pmid:{pmid}"
        if doi:
            return f"doi:{doi.lower()}"
        return fallback

    @property
    def url(self) -> str | None:
        if self.pmid:
            return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"
        if self.doi:
            return f"https://doi.org/{self.doi}"
        if self.pmcid:
            return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{self.pmcid}/"
        return self.alt_url

    @property
    def text(self) -> str:
        return " ".join(filter(None, [self.title, self.abstract, *self.keywords, *self.mesh_terms]))

    def merge(self, other: "Article") -> "Article":
        """Combine two records of the same paper, preferring the richer value."""
        def pick(a, b):
            return a if a else b

        def longer(a: str, b: str) -> str:
            return a if len(a or "") >= len(b or "") else b

        def union(a: list[str], b: list[str]) -> list[str]:
            return list(dict.fromkeys([*a, *b]))

        return Article(
            uid=self.uid,
            title=longer(self.title, other.title),
            abstract=longer(self.abstract, other.abstract),
            pmid=pick(self.pmid, other.pmid),
            pmcid=pick(self.pmcid, other.pmcid),
            doi=pick(self.doi, other.doi),
            journal=pick(self.journal, other.journal),
            year=pick(self.year, other.year),
            pub_date=pick(self.pub_date, other.pub_date),
            authors=self.authors if len(self.authors) >= len(other.authors) else other.authors,
            pub_types=union(self.pub_types, other.pub_types),
            mesh_terms=union(self.mesh_terms, other.mesh_terms),
            keywords=union(self.keywords, other.keywords),
            sources=union(self.sources, other.sources),
            is_preprint=self.is_preprint and other.is_preprint,
            cited_by=max(filter(None, [self.cited_by, other.cited_by]), default=None),
            alt_url=pick(self.alt_url, other.alt_url),
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Article":
        return _from_dict(cls, data)


@dataclass
class Trial:
    nct_id: str
    title: str = ""
    official_title: str = ""
    status: str = "UNKNOWN"
    phases: list[str] = field(default_factory=list)
    study_type: str = ""
    conditions: list[str] = field(default_factory=list)
    interventions: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    eligibility: str = ""
    min_age: str | None = None
    max_age: str | None = None
    sex: str | None = None
    healthy_volunteers: bool | None = None
    start_date: str | None = None
    completion_date: str | None = None
    last_update: str | None = None
    sponsor: str = ""
    enrollment: int | None = None
    central_contacts: list[dict[str, str]] = field(default_factory=list)
    locations: list[dict[str, Any]] = field(default_factory=list)
    why_stopped: str = ""
    has_results: bool = False
    allocation: str = ""        # RANDOMIZED | NON_RANDOMIZED | NA
    masking: str = ""           # NONE | SINGLE | DOUBLE | TRIPLE | QUADRUPLE
    primary_purpose: str = ""   # TREATMENT | DIAGNOSTIC | PREVENTION | SUPPORTIVE_CARE | BASIC_SCIENCE ...
    # Derived
    subtype_fit: str = "any_msa"

    @property
    def url(self) -> str:
        return f"https://clinicaltrials.gov/study/{self.nct_id}"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def max_phase(self) -> float:
        return max((PHASE_RANK.get(p, 0) for p in self.phases), default=0)

    @property
    def phase_label(self) -> str:
        labels = [p.replace("EARLY_PHASE1", "Early Phase 1").replace("PHASE", "Phase ") for p in self.phases if p != "NA"]
        return " / ".join(labels) or ("Observational" if self.study_type == "OBSERVATIONAL" else "N/A")

    @property
    def status_label(self) -> str:
        return self.status.replace("_", " ").title()

    @property
    def text(self) -> str:
        parts = [self.title, self.official_title, self.summary, " ".join(self.conditions)]
        parts += [f"{i.get('name', '')} {i.get('description', '')}" for i in self.interventions]
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trial":
        return _from_dict(cls, data)
