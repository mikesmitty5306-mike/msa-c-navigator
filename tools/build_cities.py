"""Build msac/data/cities.txt, the offline city list used for "home city" lookup.

Source: GeoNames "cities5000" (places with 5,000+ people), licensed CC BY 4.0, https://www.geonames.org/
The same list powers the Beta showcase page's city search.

Output: one place per line, "name|region|country|lat|lon", largest population first.

Usage (from the "MSAC Application" folder):
    python -I tools/build_cities.py                 # downloads the three GeoNames files to a temp folder
    python -I tools/build_cities.py --source DIR    # uses cities5000.zip, admin1CodesASCII.txt (or admin1.txt)
                                                    # and countryInfo.txt already in DIR
"""

from __future__ import annotations

import argparse
import io
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://download.geonames.org/export/dump/"
FILES = {"cities5000.zip": "cities5000.zip", "admin1": "admin1CodesASCII.txt", "countries": "countryInfo.txt"}
OUT = Path(__file__).resolve().parent.parent / "msac" / "data" / "cities.txt"


def _fetch(dest: Path) -> None:
    for name in FILES.values():
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "msac-research-navigator/0.1"})
        with urllib.request.urlopen(req, timeout=120) as r:
            (dest / name).write_bytes(r.read())


def build(src: Path) -> int:
    admin1_path = src / "admin1CodesASCII.txt"
    if not admin1_path.exists():
        admin1_path = src / "admin1.txt"
    admin = {}
    for line in admin1_path.read_text(encoding="utf-8").splitlines():
        p = line.split("\t")
        if len(p) >= 2:
            admin[p[0]] = p[1]
    country = {}
    for line in (src / "countryInfo.txt").read_text(encoding="utf-8").splitlines():
        p = line.split("\t")
        if not line.startswith("#") and len(p) > 4:
            country[p[0]] = p[4]
    with zipfile.ZipFile(src / "cities5000.zip") as z:
        text = io.TextIOWrapper(z.open("cities5000.txt"), encoding="utf-8").read()
    rows = []
    for line in text.splitlines():
        p = line.split("\t")
        if len(p) < 15:
            continue
        clean = lambda s: s.replace("|", " ").strip()  # noqa: E731
        rows.append((int(p[14] or 0), clean(p[1]), clean(admin.get(f"{p[8]}.{p[10]}", "")),
                     clean(country.get(p[8], p[8])), round(float(p[4]), 3), round(float(p[5]), 3)))
    rows.sort(key=lambda r: -r[0])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(f"{n}|{r}|{c}|{la}|{lo}" for _, n, r, c, la, lo in rows) + "\n", encoding="utf-8")
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, help="folder that already holds the GeoNames files")
    args = ap.parse_args()
    if args.source:
        n = build(args.source)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            _fetch(Path(tmp))
            n = build(Path(tmp))
    print(f"wrote {OUT} ({n:,} places)")
