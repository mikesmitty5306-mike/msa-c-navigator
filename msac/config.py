"""Central configuration. Override paths/keys with environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent

# Secrets (session key, passcode hash) live in "MSAC Application/.env", which git ignores.
# See .env.example. Values already set in the environment take precedence.
ENV_PATH = Path(os.environ.get("MSAC_ENV_FILE", APP_DIR / ".env"))
load_dotenv(ENV_PATH, override=False)


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Read the .env file fresh (used by the server at start-up and by set-passcode)."""
    return {k: v for k, v in dotenv_values(path).items() if v} if path.exists() else {}


SESSION_DAYS = 30                 # how long the phone stays logged in
DEFAULT_PORT = 5000
DATA_DIR = Path(os.environ.get("MSAC_DATA_DIR", APP_DIR / "data"))
DB_PATH = Path(os.environ.get("MSAC_DB_PATH", DATA_DIR / "msac.sqlite3"))
# Personal data (saved items, notes, home city) lives in its own file so that re-running
# "msac ingest", or deleting and rebuilding the research database, never touches it.
USER_DB_PATH = Path(os.environ.get("MSAC_USER_DB_PATH", DB_PATH.parent / "user.sqlite3"))
# Offline city list built from GeoNames (CC BY 4.0) by tools/build_cities.py
CITIES_PATH = Path(__file__).resolve().parent / "data" / "cities.txt"

# Optional NCBI credentials raise the PubMed rate limit from 3 to 10 req/s.
NCBI_API_KEY = os.environ.get("NCBI_API_KEY") or None
NCBI_EMAIL = os.environ.get("NCBI_EMAIL") or None
NCBI_TOOL = "msac-research-navigator"

# We collect MSA research broadly (MSA-C shares its core pathology with MSA-P),
# then flag subtype-specific work during annotation.
PUBMED_QUERY = (
    '"multiple system atrophy"[MeSH Terms] OR "multiple system atrophy"[tiab] '
    'OR olivopontocerebellar[tiab] OR "MSA-C"[tiab] OR "Shy-Drager"[tiab]'
)

EUROPEPMC_QUERY = (
    '(TITLE:"multiple system atrophy" OR ABSTRACT:"multiple system atrophy" '
    'OR TITLE:"olivopontocerebellar" OR ABSTRACT:"olivopontocerebellar" '
    'OR ABSTRACT:"MSA-C")'
)

CTGOV_CONDITION = "multiple system atrophy OR olivopontocerebellar atrophy"

DISCLAIMER = (
    "This tool summarizes publicly available research for information only. "
    "It is not medical advice. Rankings are automated heuristics, not clinical "
    "judgments. Discuss any trial with your neurologist before contacting a study team."
)
