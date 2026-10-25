# Public macro data feed

A small scheduled job that refreshes `macro_data.json` once a day from official, free,
public data sources. This repository contains the refresh script, its tests, the
scheduler workflow, and the data file with official published economic figures and
their source credits. It contains no private application code, no personal
information, no frameworks, and no keys.

## What it does

- Runs daily at 07:30 (Asia/Singapore) via GitHub Actions (`.github/workflows/macro-update.yml`).
- Fetches only public macro series from official sources.
- Writes `macro_data.json` and commits it only when something changed.
- Each series keeps its own source, access date and last-successful update date.
- If a source fails, the previous good dated values stay in place and the failure is
  listed under `failures`. Nothing is estimated or substituted.

## Sources (all free and official)

| Series | Source | Terms |
| --- | --- | --- |
| US CPI inflation, US unemployment | US Bureau of Labor Statistics public API | Public domain (bls.gov/opub/copyright-information.htm) |
| Same two series (fallback only) | FRED (Federal Reserve Bank of St. Louis) key-free CSV of the same BLS series (CPIAUCNS, UNRATE) | Citation required; included in the file when used (fred.stlouisfed.org/legal) |
| SG CPI inflation, SG real GDP growth | SingStat Table Builder API | Singapore Open Data Licence v1.0 - attribution with access date included in the file (data.gov.sg/open-data-licence) |
| US Treasury 2-year and 10-year yields | US Treasury daily par yield curve CSV | US government public data |

When the US Bureau of Labor Statistics API is unreachable, the two US series fall back
to the identical BLS series republished by FRED (CPIAUCNS / UNRATE - same definitions,
no estimation). The file records which path each series used (`retrieved_via`) and the
matching attribution.

## Cost

GitHub Actions usage is free for public repositories on standard GitHub-hosted runners
(docs.github.com/en/billing/concepts/product-billing/github-actions). One run takes
about 1-2 minutes.

## Reading the data

`macro_data.json` is served read-only from this repository's raw URL, so any consumer
reads it without a token. Each series carries its source, dates, last-successful update
date and attribution, so a reader can show honest dated figures.

## Setup (about 5 minutes, no command line)

1. On github.com/new, create an empty PUBLIC repository (no README/license/.gitignore).
2. Upload the three normal files: on the empty repo page choose "uploading an existing
   file", then drag in `README.md`, `macro_update.py` and `tests/test_macro_update.py`
   (create the `tests/` path by uploading `test_macro_update.py` into a `tests` folder,
   or create it via Add file > Create new file with path `tests/test_macro_update.py`),
   and commit.
3. Add the workflow separately so the hidden `.github` folder is created exactly:
   Add file > Create new file, type the path `.github/workflows/macro-update.yml`
   (GitHub creates the nested folders from the path), paste the file's contents, commit.
4. No settings change is needed. The workflow file requests exactly the permission it
   uses (contents: write, to commit the refreshed data file). GitHub starts a new
   repository's job token at read-only, then applies what the workflow file asks for -
   "The permissions ... are initially set to the default setting ... The permissions are
   then adjusted based on any configuration within the workflow file"
   (docs.github.com/en/actions/reference/workflow-syntax-for-github-actions#permissions).
5. First data: either open the Actions tab and run "Daily macro data refresh" manually
   (Run workflow), or wait for the next 07:30 SGT schedule. If a source is unreachable
   that day, that series is simply absent from `macro_data.json` with the reason listed
   under `failures` - it appears after its first successful fetch.

## Tests

`python3 -m unittest discover -s tests` - 14 fixture tests, no network: refresh success,
per-source failure with last-good fallback, FRED fallback path and recovery back to the
primary source, first-run failure leaves series absent, malformed payloads, unparseable
source key formats, date preservation, CPI YoY math, and a no-secrets/no-personal-data
scan of the output file.
