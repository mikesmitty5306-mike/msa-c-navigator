Version 1 is defined by reports/static_plan.txt. Schemas are JSON Schema 2020-12.
Run python -m pytest -q tests/test_static_contract.py to validate the sample and,
when present, public-data/. Install tools/requirements-static.txt first.

sample/ contains 3 mechanisms, 5 trials, 50 papers, all 256 SHA-1 abstract shards,
and 2 city letters. It is a small subset of public research fixtures, not personal
data. Empty abstract shards remain present so every UID has a predictable URL.
The sample grade explanations are recomputed for its own subset; sample grades
are for frontend development and do not represent the complete research corpus.

schemas/ covers meta, grades, mechanism details, compact trials, trial details,
library index/rows, abstract shards, city shards and phone backups. common.json
contains shared definitions. The phone backup intentionally uses backup_version,
not schema_version, following its separate contract; it is never build output.

Missing strings are null. Safety-only outcomes become unclear; non-treatment
outcomes become null. Registry dates without a known day become null rather than
inventing a day. All exported timestamps use UTC Z; data_timestamp preserves the
upstream ClinicalTrials.gov version string exactly for reliable comparisons.

The exporter adds the requested data_as_of map to meta. Existing plan
fields, including sources, files and push_public_key, retain their contract shape.
File hashes are SHA-256 over exact UTF-8 bytes, including the trailing newline.
Library shard hashes match their entries in meta.files. No self-hash for meta.

Trial status_changed persists the most recent status transition and its UTC date.
Refreshes compare against the preceding public export, including after a cache
miss. The cache stores research SQLite and its event ledger; subscriptions and
private keys are never exported. Push trial links use #/trials/<NCT ID>.

Push setup: use the GitHub secret PUSH_SUBSCRIPTION with one subscription JSON
object. The legacy plural/list configuration is accepted silently only when the
standard secret is absent. Notification eligibility is restricted to current fit
includes_msa_c or any_msa, and visible status text uses plain labels.

Internal diagnostics live in data/maintenance-internal.json and are never part of
the public contract. The --meta maintenance option must never point into a public
export or site directory. Exported meta contains source timestamps and hashes,
not internal run errors. The sample includes asyn_aggregation, proteostasis and
biomarkers, plus trial NCT04165486 and the proteostasis failure trials used by the
frontend smoke screens. Sample regeneration: python tools/build_contract.py
--sample-from public-data (keep both parts of that command on one line).
