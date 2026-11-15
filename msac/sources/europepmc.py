"""Europe PMC REST API: journal articles plus preprints (bioRxiv, medRxiv, Research Square...)."""

from __future__ import annotations

import html
import re
from datetime import date
from typing import Any, Iterator

from .. import config
from ..http import get_json
from ..models import Article

SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _clean(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def search(query: str = config.EUROPEPMC_QUERY, *, since: date | None = None,
           max_results: int | None = None, page_size: int = 1000) -> Iterator[Article]:
    if since:
        query = f"({query}) AND FIRST_IDATE:[{since.isoformat()} TO {date.today().isoformat()}]"
    cursor, produced = "*", 0
    while True:
        data = get_json(SEARCH, {
            "query": query, "format": "json", "resultType": "core",
            "pageSize": page_size, "cursorMark": cursor,
        })
        results = data.get("resultList", {}).get("result", [])
        for raw in results:
            yield parse_result(raw)
            produced += 1
            if max_results is not None and produced >= max_results:
                return
        nxt = data.get("nextCursorMark")
        if not results or not nxt or nxt == cursor:
            return
        cursor = nxt


def parse_result(r: dict[str, Any]) -> Article:
    source, rid = r.get("source", ""), r.get("id", "")
    pmid = r.get("pmid") or None
    doi = r.get("doi") or None
    year = r.get("pubYear")
    journal = ((r.get("journalInfo") or {}).get("journal") or {}).get("title", "")
    is_preprint = source == "PPR"
    if is_preprint and not journal:
        journal = (r.get("bookOrReportDetails") or {}).get("publisher", "") or "Preprint"
    mesh = [m.get("descriptorName", "") for m in (r.get("meshHeadingList") or {}).get("meshHeading", [])]
    return Article(
        uid=Article.make_uid(pmid, doi, f"epmc:{source}:{rid}"),
        title=_clean(r.get("title")),
        abstract=_clean(r.get("abstractText")),
        pmid=pmid,
        pmcid=r.get("pmcid") or None,
        doi=doi,
        journal=journal,
        year=int(year) if str(year or "").isdigit() else None,
        pub_date=r.get("firstPublicationDate"),
        authors=[a.strip() for a in (r.get("authorString") or "").rstrip(".").split(",") if a.strip()],
        pub_types=[p for p in (r.get("pubTypeList") or {}).get("pubType", []) if isinstance(p, str)],
        mesh_terms=[m for m in mesh if m],
        keywords=[k for k in (r.get("keywordList") or {}).get("keyword", []) if isinstance(k, str) and k],
        sources=["europepmc"],
        is_preprint=is_preprint,
        cited_by=r.get("citedByCount"),
        alt_url=f"https://europepmc.org/article/{source}/{rid}" if source and rid else None,
    )
