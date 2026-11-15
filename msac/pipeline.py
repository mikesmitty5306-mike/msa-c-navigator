"""Ingest orchestration: fetch -> merge -> annotate -> store."""

from __future__ import annotations

from datetime import date
from typing import Callable, Iterable

from . import mechanisms
from .http import FetchError
from .models import Article, Trial
from .sources import clinicaltrials, europepmc, pubmed
from .storage import Store

ALL_SOURCES = ("clinicaltrials", "pubmed", "europepmc")
COMMIT_EVERY = 250


def annotate_article(a: Article) -> dict[str, list[str]]:
    text = a.text
    a.subtype = mechanisms.article_subtype(text)
    a.evidence = mechanisms.classify_evidence(text, a.pub_types, a.title, a.mesh_terms)
    a.outcome = mechanisms.classify_outcome(a.title, a.abstract, a.evidence)
    a.solid_trial = a.evidence == "clinical" and mechanisms.is_solid_trial(a.title, a.abstract, a.pub_types)
    return mechanisms.tag_text(text)


def annotate_trial(t: Trial) -> dict[str, list[str]]:
    t.subtype_fit = mechanisms.trial_subtype_fit(_trial_title_text(t), t.eligibility)
    return mechanisms.tag_text(t.text)


def save_article(store: Store, a: Article) -> None:
    existing = store.get_article(a.uid)
    merged = existing.merge(a) if existing else a
    store.put_article(merged, annotate_article(merged))


def _trial_title_text(t: Trial) -> str:
    # "; " keeps adjacent condition names from reading as one phrase.
    return "; ".join([t.title, t.official_title, *t.conditions])


def save_trial(store: Store, t: Trial) -> bool:
    """Store an MSA-relevant trial; drop (and purge) unrelated ones. Returns True if kept."""
    if not mechanisms.trial_is_msa_relevant(_trial_title_text(t), t.summary, t.eligibility):
        store.delete_trial(t.nct_id)
        return False
    store.put_trial(t, annotate_trial(t))
    return True


def ingest(store: Store, sources: Iterable[str] = ALL_SOURCES, *, since: date | None = None,
           max_results: int | None = None, log: Callable[[str], None] = print) -> dict[str, int]:
    summary: dict[str, int] = {}
    for source in sources:
        run_id = store.start_run(source)
        n = 0
        log(f"[{source}] fetching...")
        try:
            if source == "pubmed":
                items, saver = pubmed.search(since=since, max_results=max_results), save_article
            elif source == "europepmc":
                items, saver = europepmc.search(since=since, max_results=max_results), save_article
            elif source == "clinicaltrials":
                # Trials are few; always refresh all so status changes are captured.
                items, saver = clinicaltrials.search(max_results=max_results), save_trial
            else:
                raise ValueError(f"Unknown source: {source}")
            for item in items:
                if saver(store, item) is False:
                    continue
                n += 1
                if n % COMMIT_EVERY == 0:
                    store.commit()
                    log(f"[{source}] {n} records")
            store.commit()
            store.finish_run(run_id, n, "ok")
            log(f"[{source}] done: {n} records")
        except (FetchError, ValueError) as exc:
            store.commit()  # keep partial progress
            store.finish_run(run_id, n, "error", str(exc))
            log(f"[{source}] stopped after {n} records: {exc}")
        except BaseException as exc:
            store.commit()
            store.finish_run(run_id, n, "error", f"{type(exc).__name__}: {exc}")
            raise
        summary[source] = n
    return summary


def retag(store: Store) -> tuple[int, int]:
    """Re-run classifiers on stored records (use after editing the taxonomy)."""
    articles = list(store.iter_articles())
    for a in articles:
        store.put_article(a, annotate_article(a))
    kept = sum(save_trial(store, t) for t in list(store.iter_trials()))
    store.commit()
    return len(articles), kept
