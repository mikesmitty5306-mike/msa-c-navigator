# GitHub Pages setup

Put this application at the **root** of a fresh public repository, with `main` as
the default branch. Include `web/`, `msac/`, `public-data/`, `contract/`, `tools/`,
the requirements files and `.github/workflows/refresh.yml`.

The first push to `main` deploys the checked-in, validated research export. It
does not wait for PubMed, Europe PMC or ClinicalTrials.gov, and needs no research
or push secrets. Hourly runs refresh research separately. A failed or timed-out
refresh fails visibly and keeps the last published website.

## 1. Enable GitHub Actions and Pages

1. Create the public repository. Keep its default branch named `main`.
2. Open **Settings → Actions → General** and allow GitHub Actions. Under
   **Workflow permissions**, keep **Read repository contents and packages
   permissions**, the restricted/read-only default. No repository-wide write
   default is needed: the workflow declares the permissions each job requires.
3. Open **Settings → Pages → Build and deployment → Source** and select
   **GitHub Actions**. This is required; do not choose deployment from a branch.
4. Push the application to `main`. In **Actions → Refresh data**, open the run
   and wait for **build** and **deploy** to succeed. The deployment URL appears
   in the `github-pages` environment and on the Pages settings screen.

If Pages settings are unavailable until the repository has its first commit,
push first, select **GitHub Actions** as the source, then use **Actions → Refresh
data → Run workflow** on `main`. Leave both checkboxes off for that initial retry.
If branch rules restrict bot commits or environment deployments, allow this
workflow to update `public-data/` on `main` and deploy to `github-pages`.

The workflow-level default grants no permissions. The `build` job alone requests
`contents: write`, because it commits changed `public-data/`; `deploy` alone gets
`pages: write` and `id-token: write`; `test-push` gets only `contents: read`.
GitHub permits workflows to adjust the restricted defaults using explicit
[job permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions).

## 2. Generate VAPID keys locally, if you want notifications

Install the build dependencies in a local virtual environment, then run:

```text
python -m pip install -r tools/requirements-static.txt
python tools/make_vapid_keys.py
```

The helper prints `VAPID_PRIVATE_KEY` and `VAPID_PUBLIC_KEY` as JSON. It creates
no file. Copy each value without its surrounding quotes into GitHub secrets.
Run it locally, not in Actions. Do not redirect its output to a repository file.
Keep the private key in a password manager if you need a recovery copy. Keep the
same key pair once phones have subscribed; changing it requires resubscribing.
`python -m msac generate-vapid` also prints keys without creating a file.

## 3. Add secrets in this order

Open **Settings → Secrets and variables → Actions → Secrets → New repository
secret** for each value. No secrets are needed just to publish the site.

| Order | Exact secret name | Needed for | Value and where to get it |
| --- | --- | --- | --- |
| 1 | `VAPID_PRIVATE_KEY` | Push notifications | The private value printed by the local key helper; base64url DER. Never commit it. |
| 2 | `VAPID_PUBLIC_KEY` | Phone subscription and push | The public value from the **same** key pair. The workflow writes this public value into deployed `data/meta.json`. |
| 3 | `VAPID_SUBJECT` | Push sender identity | Recommended: your site's `https://<user>.github.io/<repo>` URL. A `mailto:` contact also works; no personal email is needed for push. |
| 4 | `NCBI_API_KEY` | Optional faster PubMed refresh | Sign in to NCBI, open account settings and API Key Management, and create/copy an API key. Skip this if you do not have one. |
| 5 | `NCBI_EMAIL` | Optional NCBI contact | Your contact email, without `mailto:`. You supply it; no special key generation is needed. |
| 6 | `PUSH_SUBSCRIPTION` | Delivery to your phone | One complete JSON object shared by the installed phone app, including `endpoint` and `keys`. Obtain it after the next steps. Do not use a JSON list. |

Use **repository** secrets rather than environment secrets, so the refresh and
test-push jobs can both read them. Existing variables for `VAPID_PUBLIC_KEY`,
`VAPID_SUBJECT` and `NCBI_EMAIL` still work, but the secret names above are the
standard setup. The old plural subscription name is only a compatibility fallback;
new setups use `PUSH_SUBSCRIPTION`.

`GITHUB_TOKEN` is supplied automatically by GitHub; **do not create a PAT or
manually add a token secret**. No Pages deployment key or password is required.

## 4. Connect the phone and send one test

1. After adding the VAPID keys and contact, open **Actions → Refresh data → Run
   workflow**, select `main`, and leave both checkboxes off. This publishes the
   public key without contacting the research APIs.
2. On the iPhone, open the published HTTPS URL in Safari, then **Share → Add to
   Home Screen**. Open the app from that icon.
3. In the app, open **Settings → Turn on notifications** and grant permission.
   Use **Send notification code to helper** to share/copy its subscription JSON.
4. Add that entire JSON object as the repository secret `PUSH_SUBSCRIPTION`.
5. In **Actions → Refresh data → Run workflow** on `main`, check
   **send_test_push**. Leave **refresh_data** off. This deploys the validated
   snapshot, then sends **one** test notification to that single subscription.
6. Check the **test-push** job and its summary. If it reports HTTP 404/410, turn
   notifications off/on in the phone app, share a new code, and replace the secret.

The test-push input defaults to false. Ordinary pushes and hourly runs never send
a test notification. Normal event notifications remain part of successful hourly
refreshes. The test is intentionally not sent if deployment fails.

## Ongoing refreshes and failures

Each hourly run at `:17` follows this sequence (GitHub can delay scheduled runs):

1. Restore the cached working research SQLite database. A cache miss causes a
   full rebuild during this refresh, while the existing site remains available.
   This rebuild is a **silent notification baseline**: it records current trials
   and public status history without sending new-trial or status-change alerts.
   The baseline remains silent across failed/interrupted rebuild retries until
   the complete refresh and validated export succeed. Subsequent genuine changes
   can notify normally; an unchanged follow-up does not replay baseline alerts.
2. Read ClinicalTrials.gov API v2 `/version` and record the check. If its
   `dataTimestamp` changed, download all MSA trials again, update their status
   history, and queue eligible trial alerts. An unchanged timestamp avoids the
   trial download; the database still records that the check occurred.
3. Once daily, on the first run at/after 06:00 UTC, incrementally refresh PubMed
   and Europe PMC using their checkpoints plus a seven-day overlap, then back
   up. Missed runs catch up. The first rebuild fetches the full literature.
4. When research was refreshed, or a rebuild/public-key update/monthly heartbeat
   is due, recompute annotations and rankings and validate/export the public
   JSON. Then try queued event alerts: new open trials or status changes for
   current `includes_msa_c`/`any_msa` fits, capped at five messages including any
   summary. Missing push secrets skip sending. Pending alerts may be sent on an
   otherwise unchanged hour; hourly runs never send the optional test push.
5. Checkpoint/cache SQLite, including its private event ledger. If publication
   is due, validate the public snapshot, run focused checks, assemble the site,
   and commit **only changed `public-data/` files** as the Actions bot. Upload the
   Pages artifact and deploy it in the same workflow run. The monthly heartbeat
   updates metadata when necessary to keep repository activity from going stale.

An ordinary unchanged hour has no public-data commit or Pages deployment. The
check history and delivery state are still cached. A source error or timeout
fails the run visibly and skips export/commit/upload/deploy, keeping the last
published data and website. All runs share one concurrency group.

The bot's commit uses the automatic `GITHUB_TOKEN`, **so its push to `main` does
not trigger another `push` workflow run**. The existing run deploys explicitly;
it does not rely on the bot commit triggering a second run. This follows
[GitHub's token event rules](https://docs.github.com/en/actions/concepts/security/github_token).

- To refresh now, choose **Run workflow → refresh_data**. Without this checkbox,
  a manual run only validates/deploys the committed snapshot and public key.
- If a refresh run is red, open its failed refresh step in **Actions**. Fix the
  reported source/configuration issue, then run it again with **refresh_data**.
- Private keys and subscriptions are never exported. Research cache is rebuildable;
  phone saves and notes remain on the phone and use its Export backup feature.

GitHub references: [custom Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
and [automatic token behavior](https://docs.github.com/en/actions/concepts/security/github_token).
VAPID accepts HTTPS and mailto sender identities under
[RFC 8292, section 2.1](https://www.rfc-editor.org/rfc/rfc8292.html#section-2.1).
