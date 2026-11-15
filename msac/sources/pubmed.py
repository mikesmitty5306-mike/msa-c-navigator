"""PubMed via NCBI E-utilities (esearch + efetch XML)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Iterator

from .. import config
from ..http import get, get_json
from ..models import Article

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
# E-utilities will not page past 10,000 records per query; split date ranges below this.
_MAX_WINDOW = 9000


def _auth() -> dict[str, str]:
    params = {"tool": config.NCBI_TOOL}
    if config.NCBI_API_KEY:
        params["api_key"] = config.NCBI_API_KEY
    if config.NCBI_EMAIL:
        params["email"] = config.NCBI_EMAIL
    return params


def _esearch(query: str, mindate: date, maxdate: date) -> dict:
    params = {
        "db": "pubmed", "term": query, "retmode": "json", "retmax": 0, "usehistory": "y",
        "datetype": "edat", "mindate": mindate.strftime("%Y/%m/%d"), "maxdate": maxdate.strftime("%Y/%m/%d"),
        **_auth(),
    }
    return get_json(ESEARCH, params)["esearchresult"]


def _windows(query: str, mindate: date, maxdate: date) -> Iterator[dict]:
    """Yield esearch results for date windows each holding < _MAX_WINDOW records."""
    res = _esearch(query, mindate, maxdate)
    if int(res["count"]) <= _MAX_WINDOW or (maxdate - mindate).days < 2:
        yield res
        return
    mid = mindate + (maxdate - mindate) / 2
    yield from _windows(query, mindate, mid)
    yield from _windows(query, mid + timedelta(days=1), maxdate)


def search(query: str = config.PUBMED_QUERY, *, since: date | None = None,
           max_results: int | None = None, batch: int = 200) -> Iterator[Article]:
    mindate = since or date(1900, 1, 1)
    produced = 0
    for res in _windows(query, mindate, date.today()):
        count = int(res["count"])
        for start in range(0, count, batch):
            size = min(batch, count - start)
            if max_results is not None:
                size = min(size, max_results - produced)
                if size <= 0:
                    return
            xml = get(EFETCH, {
                "db": "pubmed", "WebEnv": res["webenv"], "query_key": res["querykey"],
                "retstart": start, "retmax": size, "retmode": "xml", **_auth(),
            })
            for article in parse_efetch(xml):
                yield article
                produced += 1


def _text(el: ET.Element | None) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def _year(article: ET.Element) -> int | None:
    for path in ("Journal/JournalIssue/PubDate/Year", "ArticleDate/Year"):
        value = article.findtext(path)
        if value and value.isdigit():
            return int(value)
    match = re.search(r"(19|20)\d{2}", article.findtext("Journal/JournalIssue/PubDate/MedlineDate") or "")
    return int(match.group()) if match else None


def parse_efetch(xml_bytes: bytes) -> Iterator[Article]:
    root = ET.fromstring(xml_bytes)
    for pa in root.iter("PubmedArticle"):
        mc = pa.find("MedlineCitation")
        art = mc.find("Article") if mc is not None else None
        if art is None:
            continue
        pmid = mc.findtext("PMID")
        abstract_parts = []
        for ab in art.findall("Abstract/AbstractText"):
            label, body = ab.get("Label"), _text(ab)
            abstract_parts.append(f"{label}: {body}" if label else body)
        ids = {i.get("IdType"): (i.text or "").strip() for i in pa.findall("PubmedData/ArticleIdList/ArticleId")}
        authors = []
        for au in art.findall("AuthorList/Author"):
            name = " ".join(filter(None, [au.findtext("LastName"), au.findtext("Initials")])) or au.findtext("CollectiveName")
            if name:
                authors.append(name)
        ad = art.find("ArticleDate")
        pub_date = None
        if ad is not None and ad.findtext("Year"):
            pub_date = "-".join(x for x in (ad.findtext("Year"), ad.findtext("Month"), ad.findtext("Day")) if x)
        doi = ids.get("doi") or None
        yield Article(
            uid=Article.make_uid(pmid, doi, f"pubmed:{pmid}"),
            title=_text(art.find("ArticleTitle")),
            abstract="\n".join(abstract_parts),
            pmid=pmid,
            pmcid=ids.get("pmc") or None,
            doi=doi,
            journal=art.findtext("Journal/Title") or "",
            year=_year(art),
            pub_date=pub_date,
            authors=authors,
            pub_types=[_text(p) for p in art.findall("PublicationTypeList/PublicationType")],
            mesh_terms=[_text(m) for m in mc.findall("MeshHeadingList/MeshHeading/DescriptorName")],
            keywords=[_text(k) for k in mc.findall("KeywordList/Keyword")],
            sources=["pubmed"],
        )
