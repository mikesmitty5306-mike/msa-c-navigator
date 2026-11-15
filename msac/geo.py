"""Offline city lookup and distance helpers.

Uses the same GeoNames list and matching rules as the Beta showcase page:
type the start of a place name, optionally followed by ", region" or ", country"
(for example "Paris, Texas"). Exact name matches come first, then larger places.
"lat, lon" coordinates are accepted too.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from . import config

KM_PER_MILE = 1.609344
_COORDS = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class City:
    name: str
    region: str
    country: str
    lat: float
    lon: float

    @property
    def label(self) -> str:
        return ", ".join(x for x in (self.name, self.region, self.country) if x)

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> "City | None":
        try:
            lat, lon = float(d["lat"]), float(d["lon"])
            if not valid_coords(lat, lon):
                return None
            name = str(d["name"]).strip()[:120]
            if not name:
                return None
            return cls(name, str(d.get("region", "")).strip()[:120], str(d.get("country", "")).strip()[:120], lat, lon)
        except (KeyError, TypeError, ValueError):
            return None


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def valid_coords(lat: float, lon: float) -> bool:
    return math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180


@lru_cache(maxsize=2)
def _load(path: str) -> tuple[tuple[str, City], ...]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        p = line.split("|")
        if len(p) == 5:
            c = City(p[0], p[1], p[2], float(p[3]), float(p[4]))
            rows.append((norm(c.name), c))
    return tuple(rows)


def search(query: str, limit: int = 8, path: Path = config.CITIES_PATH) -> list[City]:
    query = (query or "")[:120]
    m = _COORDS.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return [City(f"{lat:.3f}, {lon:.3f}", "", "coordinates", lat, lon)] if valid_coords(lat, lon) else []
    name, _, qualifier = query.partition(",")
    n, q = norm(name), norm(qualifier)
    if len(n) < 2:
        return []
    exact, prefix = [], []
    for key, c in _load(str(path)):           # already ordered by population, largest first
        if not key.startswith(n):
            continue
        if q and not (norm(c.region).startswith(q) or norm(c.country).startswith(q)):
            continue
        (exact if key == n else prefix).append(c)
        if len(exact) >= limit:
            break
    return (exact + prefix)[:limit]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def km_to_miles(km: float | None) -> int | None:
    return None if km is None else round(km / KM_PER_MILE)
