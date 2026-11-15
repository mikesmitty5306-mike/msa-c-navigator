"""ClinicalTrials.gov API v2."""

from __future__ import annotations

from typing import Any, Iterator

from .. import config
from ..http import get_json
from ..models import Trial

STUDIES = "https://clinicaltrials.gov/api/v2/studies"


def search(condition: str = config.CTGOV_CONDITION, *, max_results: int | None = None,
           page_size: int = 100) -> Iterator[Trial]:
    token, produced = None, 0
    while True:
        params = {"query.cond": condition, "pageSize": page_size, "format": "json"}
        if token:
            params["pageToken"] = token
        data = get_json(STUDIES, params)
        for study in data.get("studies", []):
            yield parse_study(study)
            produced += 1
            if max_results is not None and produced >= max_results:
                return
        token = data.get("nextPageToken")
        if not token:
            return


def fetch(nct_id: str) -> Trial:
    return parse_study(get_json(f"{STUDIES}/{nct_id}", {"format": "json"}))


def _date(module: dict, key: str) -> str | None:
    return (module.get(key) or {}).get("date")


def _contact(c: dict[str, Any]) -> dict[str, str]:
    return {k: c[k] for k in ("name", "role", "phone", "phoneExt", "email") if c.get(k)}


def parse_study(study: dict[str, Any]) -> Trial:
    p = study.get("protocolSection", {})
    ident = p.get("identificationModule", {})
    status = p.get("statusModule", {})
    design = p.get("designModule", {})
    elig = p.get("eligibilityModule", {})
    cl = p.get("contactsLocationsModule", {})
    locations = []
    for loc in cl.get("locations", []):
        geo = loc.get("geoPoint") or {}
        locations.append({
            "facility": loc.get("facility", ""),
            "city": loc.get("city", ""),
            "state": loc.get("state", ""),
            "zip": loc.get("zip", ""),
            "country": loc.get("country", ""),
            "status": loc.get("status", ""),
            "lat": geo.get("lat"),
            "lon": geo.get("lon"),
            "contacts": [_contact(c) for c in loc.get("contacts", [])],
        })
    return Trial(
        nct_id=ident.get("nctId", ""),
        title=ident.get("briefTitle", ""),
        official_title=ident.get("officialTitle", ""),
        status=status.get("overallStatus", "UNKNOWN"),
        phases=design.get("phases", []) or [],
        study_type=design.get("studyType", ""),
        conditions=p.get("conditionsModule", {}).get("conditions", []),
        interventions=[
            {"type": i.get("type", ""), "name": i.get("name", ""), "description": i.get("description", "")}
            for i in p.get("armsInterventionsModule", {}).get("interventions", [])
        ],
        summary=p.get("descriptionModule", {}).get("briefSummary", ""),
        eligibility=elig.get("eligibilityCriteria", ""),
        min_age=elig.get("minimumAge"),
        max_age=elig.get("maximumAge"),
        sex=elig.get("sex"),
        healthy_volunteers=elig.get("healthyVolunteers"),
        start_date=_date(status, "startDateStruct"),
        completion_date=_date(status, "completionDateStruct") or _date(status, "primaryCompletionDateStruct"),
        last_update=_date(status, "lastUpdatePostDateStruct"),
        sponsor=(p.get("sponsorCollaboratorsModule", {}).get("leadSponsor") or {}).get("name", ""),
        enrollment=(design.get("enrollmentInfo") or {}).get("count"),
        central_contacts=[_contact(c) for c in cl.get("centralContacts", [])],
        locations=locations,
        why_stopped=status.get("whyStopped", ""),
        has_results=bool(study.get("hasResults")),
        allocation=(design.get("designInfo") or {}).get("allocation", ""),
        masking=((design.get("designInfo") or {}).get("maskingInfo") or {}).get("masking", ""),
        primary_purpose=(design.get("designInfo") or {}).get("primaryPurpose", ""),
    )
