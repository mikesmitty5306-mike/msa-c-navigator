# MSA-C Navigator

A free, installable web app (PWA) for iPhone that gathers public research and clinical trials on
**multiple system atrophy, cerebellar subtype (MSA-C)**. It grades disease mechanisms A–D by how
treatments aimed at them have done in published research, and helps find and contact recruiting
trials.

> **Not medical advice.** This tool summarizes publicly available research for information only.
> Grades and rankings are automated heuristics, not clinical judgments. Discuss any trial with a
> neurologist before contacting a study team.

Live site: `https://<username>.github.io/<repo>/`

## How it works

- **No server.** The app is static files on GitHub Pages. GitHub Actions refreshes the data:
  ClinicalTrials.gov is checked hourly, and PubMed and Europe PMC are refreshed daily at about
  06:17 UTC.
- **Personal data stays on the phone.** Saved items, notes, home city and location (optional, only
  when "Use my location" is tapped) are kept in the phone's browser storage only. Nothing is sent
  anywhere. Settings > Export backup makes a copy.
- **Optional notifications** (iOS 16.4+, Home Screen app only): new MSA-C trials opening, and status
  changes in relevant trials.

```
.github/workflows/refresh.yml   hourly/manual: refresh -> export -> test -> commit public-data/ -> deploy Pages
msac/                           research pipeline: sources, classification, grades, export, push
public-data/                    the published JSON data (written by the workflow)
contract/                       JSON Schemas for the published data, plus a small sample
web/                            the app itself (HTML, CSS, JavaScript, service worker)
tools/                          site assembly and build helpers
tests/                          backend, contract and frontend tests
```

## Setting up on GitHub

Step-by-step instructions are in **[SETUP.md](SETUP.md)**. In short:

1. Settings > Actions > General: allow actions, with **Read and write** workflow permissions.
2. Settings > Pages > Source: **GitHub Actions**.
3. Push to `main`. The first push deploys the committed, validated data export. It needs no
   secrets and doesn't wait for the research APIs. Hourly runs refresh the data after that.
4. Optional: add the secrets listed in SETUP.md for faster PubMed access and for notifications
   (`VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT`, `PUSH_SUBSCRIPTION`).
   Notifications stay off until they are set.

## Running locally

Python 3.12 and Node.js (for the JavaScript unit tests). Headless Edge or Chrome runs the screen tests.

```
python -m pip install -r tools/requirements-static.txt
python -m pytest -q                                              # all tests
python tools/assemble_site.py --data contract/sample --out _sample_site
python -m http.server 8000 --directory _sample_site              # open http://127.0.0.1:8000/
```

With a research database (`python -m msac refresh-scheduled --force` builds one), `python -m msac
export-static --out public-data` followed by `python tools/assemble_site.py` builds the real site.

## Data sources and credits

See [ATTRIBUTION.md](ATTRIBUTION.md). Research data: PubMed (NCBI), Europe PMC, ClinicalTrials.gov.
City data © GeoNames, CC BY 4.0.
