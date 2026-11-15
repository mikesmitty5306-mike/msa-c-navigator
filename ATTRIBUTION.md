# Data sources and attribution

## GeoNames (city data)

The city search and "distance to trial site" features use place names and coordinates from
**GeoNames** (<https://www.geonames.org/>), dataset `cities5000` with `admin1CodesASCII.txt` and
`countryInfo.txt`, licensed under the
[Creative Commons Attribution 4.0 International licence (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

Changes made: records were reduced to name, region, country and coordinates, ordered by
population, and split into files by the first letter of the accent-normalized name
(`public-data/cities/`, built by `tools/build_cities.py`). GeoNames provides the data "as is",
without warranty.

## Research and trial records

- **PubMed / NCBI E-utilities**, U.S. National Library of Medicine
  (<https://pubmed.ncbi.nlm.nih.gov/>). Citations and abstracts remain subject to their publishers'
  copyright; see the NLM's terms and conditions for PubMed data.
- **Europe PMC** (<https://europepmc.org/>), including preprints. Use follows the Europe PMC
  terms of use; abstracts remain subject to their publishers' copyright.
- **ClinicalTrials.gov** API v2, U.S. National Library of Medicine
  (<https://clinicaltrials.gov/>). Trial records, including study-team contacts, are as published
  by the registry. Always check the official registry page before contacting a study team.

The app links back to each source record. Grades, rankings and labels are this project's own
automated summaries, are not endorsed by any of these sources, and are not medical advice.
