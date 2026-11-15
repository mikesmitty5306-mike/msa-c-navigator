"""Step B: bad input, error pages, saved items and notes, surviving re-ingest, home city lookup."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from msac import geo, pipeline  # noqa: E402
from msac.sources import clinicaltrials, pubmed  # noqa: E402
from msac.storage import Store  # noqa: E402
from msac.userdata import NOTE_MAX, UserStore  # noqa: E402
from test_core import NO_AUTH, PUBMED_XML, STUDY  # noqa: E402

TRIAL = "NCT00000001"
PAPER = "pmid:111"


def _fill(db: Path, status: str = "RECRUITING") -> None:
    s = Store(db)
    for a in pubmed.parse_efetch(PUBMED_XML):
        pipeline.save_article(s, a)
    study = copy.deepcopy(STUDY)
    study["protocolSection"]["statusModule"]["overallStatus"] = status
    pipeline.save_trial(s, clinicaltrials.parse_study(study))
    s.commit()
    s.close()


# ---------------------------------------------------------------- 1. bad input and error pages
# ---------------------------------------------------------------- 3. saving, unsaving, notes
# ---------------------------------------------------------------- 3. surviving re-ingest, status-change badge
def test_cli_ingest_syncs_saved_statuses(tmp_path, monkeypatch, capsys):
    db = tmp_path / "research.sqlite3"
    _fill(db)
    UserStore(tmp_path / "user.sqlite3").save("trial", TRIAL, "Ubiquinol in MSA", None, "RECRUITING")
    study = copy.deepcopy(STUDY)
    study["protocolSection"]["statusModule"]["overallStatus"] = "COMPLETED"
    monkeypatch.setattr(clinicaltrials, "search", lambda **kw: iter([clinicaltrials.parse_study(study)]))
    from msac import cli
    cli.main(["--db", str(db), "ingest", "--sources", "clinicaltrials"])
    out = capsys.readouterr().out
    assert f"Saved trial {TRIAL} changed status: RECRUITING -> COMPLETED" in out
    assert UserStore(tmp_path / "user.sqlite3").get("trial", TRIAL)["last_status"] == "COMPLETED"


# ---------------------------------------------------------------- 4. home city lookup and distances
def test_city_lookup():
    first = geo.search("Boston")[0]
    assert (first.name, first.region, first.country) == ("Boston", "Massachusetts", "United States")
    assert geo.search("Paris")[0].country == "France"
    assert geo.search("Paris, Texas")[0].region == "Texas"
    assert geo.search("paris, tex")[0].region == "Texas"                      # case and partial qualifier
    assert geo.search("Montreal")[0].name in ("Montréal", "Montreal")          # accents ignored
    assert geo.search("Bath")[0].name == "Bath"                                # exact name before longer names
    assert geo.search("42.36, -71.06")[0].country == "coordinates"
    assert geo.search("b") == [] and geo.search("") == [] and geo.search("zzqqxx") == []
    assert geo.search("99, 500") == []
    assert len(geo.search("San", limit=8)) == 8


# ---------------------------------------------------------------- 2. grades shown throughout
