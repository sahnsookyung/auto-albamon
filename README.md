# Auto Albamon

Independent Albamon job discovery, detail verification, deduplication,
commute screening, ranking, and Korean application-message reporting. This
repository is intentionally separate from JobScout and the Raspberry Pi Pico
reaction-testing project.

The implementation uses Python's standard library. Run the test suite from the
repository root with:

```bash
python3 -m unittest discover -s tests -p 'test_albamon*.py' -v
```

Generated reports, API caches, authenticated application history, and personal
application dispositions remain in the local working tree but are ignored by
Git to avoid committing bulky or private runtime data.

For a fresh clone, copy the two `*.example.json` files in `tools/` to their
matching non-example names before recording application history or personal
defer/do-not-apply choices. Missing application history is treated as an empty,
explicitly unchecked snapshot; it is never fabricated from the public list.
The historical application-message regression class uses a local ignored report
fixture and is skipped when that fixture is not present.

Run the complete daily workflow with one command:

```bash
export KAKAO_REST_API_KEY="your Kakao REST API key"
python3 tools/albamon_refresh.py
```

This produces `latest-combined.*`, refreshes/reuses exact public-transit routes,
and writes `latest-unified-report.*`. Use `--combined-only` when a Kakao key is
not available; the tool deliberately does not produce an actionable report
without the required commute verification.

## Unified ranked application report

After generating the combined report, create one Korean message per eligible
job with:

```bash
python3 tools/albamon_application_messages.py \
  --input artifacts/albamon/latest-combined.json \
  --output-prefix artifacts/albamon/latest-unified-report
```

The unified report places each job's utility score, positive ranking reasons,
cautions, schedule, pay, duration, hard requirements, and tailored Korean
application message together. The message generator first classifies the actual
role and extracts the employer's duties and priorities from the detailed posting.
It then permits only directly relevant applicant facts: for example, cleaning
copy may use restaurant hygiene experience but not coding, logistics copy uses
accuracy and follow-through rather than unrelated office experience, and English
appears only when the posting signals a language or foreign-customer need. It also
records which applicant facts were used, the relevance rationale, and a separate
`verify_before_sending` checklist. Unknown certificates, café history,
physical attributes, language requirements, or cold-storage capability are
never stated as fact. An optional sentence is supplied for those cases and must
only be added after the applicant confirms every claim in it. The profile-fact
set intentionally excludes contact details, exact address, birth date, and
resume identifiers. Current jobs have hand-tailored copy; new job IDs receive a
clearly marked rule-based first draft so a daily run still completes, with a
mandatory detailed-posting review note.

Persistent per-job choices live in
`tools/albamon_application_dispositions.json`. A `maybe_later` or
`do_not_apply` entry removes that job from the actionable headings and places it
under `부적합·나중에 지원 검토`; if the job is later found in application
history, the applied status takes precedence.

## Daily combined report

`tools/albamon_combined_jobs.py` is the repeatable workflow for one deduplicated
report covering 맞춤알바, 여름알바, and 급구알바. It reads the live saved condition
(`suitNo` `35138482`), applies those same filters to the two special-list APIs,
then verifies the detail content rather than trusting only Albamon's broad tags.

The collection currently encodes:

- Area: all 25 Seoul districts (the saved-search area selection is overridden at
  API collection time)
- Period buckets: one day, one week or less, one week to one month, and one to
  three months
- Weekend (Sat/Sun), including negotiated schedules
- Male age 26, including unrestricted listings
- Education unrestricted

Run it with:

```bash
python3 tools/albamon_combined_jobs.py \
  --suit-no 35138482 \
  --output-prefix artifacts/albamon/latest-combined
```

The default refresh is incremental and checkpoints to
`artifacts/albamon/cache/details.json`. New jobs and jobs whose list-level
fingerprint changed always receive a full detail fetch. Every currently
eligible or already-applied job is also rechecked on every run. Unchanged jobs
that were previously excluded reuse their full cached detail for seven days,
then are refreshed on a rolling basis. A missing ID never survives into the
live report merely because it remains in the cache. Use `--no-detail-cache` for
a cold audit, or adjust `--cache-ttl-days`, `--cache-retention-days`, and
`--checkpoint-every` when diagnosing cache behavior.

Detail fetching uses Albamon's compact `/v1/recruit/view` and
`/v1/recruit/view/detail` responses in parallel. Both responses are required;
their merged posting text is what drives weekend, duration, licence, and hard-
requirement screening. The much larger public HTML page is retained as a
per-job fallback if the compact API changes or fails. Ambiguous list metadata
is never used to skip detail verification. Safe list-level reductions are
limited to server-side adult/bar exclusion, conclusive non-Seoul coordinates
and addresses, explicit clinical/driving titles, and exact operational
duplicate signatures.

Each JSON report contains a `performance` object with list/detail timings,
request and byte counts, retries, cache hits, network jobs, checkpoints,
fallbacks, early exclusions, and duplicate counts. This makes a slow or
degraded run diagnosable without logging response bodies or credentials.

### Reference benchmark

Measured on 2026-08-05 against 4,180 unique IDs after the server-side bar
filter (network conditions will vary):

| Run | Full details downloaded | Wall time |
| --- | ---: | ---: |
| Cold incremental-cache bootstrap | 1,626 | 14.66 s |
| Warm daily refresh | 163 | 6.94 s |
| Local regroup/rerank from cached report | 0 | 0.18 s |
| Warm application-message report | 0 | 0.07 s |

The cold and warm reports both produced the same 139 eligible IDs and the same
schedule, duration, requirement, pay, and application metadata as the previous
exhaustive HTML-based report. These figures are evidence from one run, not a
latency guarantee.

The run also reads `tools/albamon_applied_jobs.json`, a privacy-minimized
snapshot from the signed-in Albamon application-history pages. Matching jobs
are excluded from the recommended/actionable headings and placed under
`Already applied — reference only`; their messages remain available for audit
but should not be submitted again. Refresh this snapshot from all three history
tabs (online/SMS/email, other applications, and hidden history) before a daily
application run.

To update only the application grouping on an existing report without refetching
hundreds of listings, run:

```bash
python3 tools/albamon_combined_jobs.py \
  --input-report artifacts/albamon/latest-combined.json \
  --applied-history tools/albamon_applied_jobs.json \
  --output-prefix artifacts/albamon/latest-combined
```

The combined report excludes work terms ending on the run date or earlier,
driver/licence roles, verified
hard commitments of one month or more, demographic mismatches, out-of-area
branches, and negotiated ads that do not actually document a Sat/Sun option.
An employer-written hard request for long-term availability with no numeric
minimum is held out in its own review section instead of being silently accepted
or assigned an invented duration.
It combines repeated campaigns by stable/semantic identity, separates Coupang,
B-Mart, Kurly, and other warehouse work, and annotates cold-storage conditions.
B-Mart's shared campaign image has been reviewed and explicitly covers
refrigerated/frozen handling; Coupang's reviewed sorting ad does not advertise
cold storage. Generic clinical-study campaign pages and adult-only bar ads are
held out for separate review because their exact schedule or requirements cannot
be proven at the API level.

The output JSON retains source membership, alternate recruit IDs/URLs,
application methods, external application links, requirements, certificates,
duration evidence, and pre-application questions for a later confirmed
application workflow. CSV contains the compact eligible set; Markdown is the
readable report.

Eligible jobs are sorted strictly by reliable comparable pay descending, then by
verified public-transit minutes from 잠실새내역 ascending when pay is equal.
Before ranking, run the public-transit pass:

```bash
export KAKAO_REST_API_KEY="your Kakao REST API key"
python3 tools/albamon_transit.py \
  --input artifacts/albamon/latest-combined.json \
  --output tools/albamon_transit_cache.json
python3 tools/albamon_application_messages.py \
  --input artifacts/albamon/latest-combined.json \
  --transit-cache tools/albamon_transit_cache.json \
  --output-prefix artifacts/albamon/latest-unified-report
```

`albamon_transit.py` calls Kakao Maps' public-transit endpoint and selects the
fastest bus/train route to the verified Albamon worksite coordinates. The
rounded-up displayed duration must be strictly below 50 minutes: 49 minutes is
eligible, while 50 minutes is excluded.
Listings whose actual worksite cannot be verified (notably pickup-only Coupang
campaigns) are conservatively placed in `거리 제외` as well. The API key is
read from the environment and is never saved. Transit is time-sensitive, so the
cache should be refreshed on each daily job refresh, and borderline routes should
be checked again before departure.

Transit results are keyed by exact worksite address (or verified coordinates),
not recruit number, so reposts and same-site jobs share a lookup. Results less
than 24 hours old are reused by default; set `--route-cache-hours` to change
that window. A broad district label without an exact address is never shared
between jobs.

When only the cutoff changes, reuse the already-fetched route durations without
another API pass:

```bash
python3 tools/albamon_transit.py \
  --output tools/albamon_transit_cache.json \
  --reclassify-cache
```

The unified application ranking is strict pay-first and exact-commute-second.
Mixed salary units are converted to a comparable hourly amount using the
displayed paid shift hours. A lower-paying job cannot move above a higher-paying
job because of commute or fit; commute is the first tie-break only when reliable
comparable pay is equal. Schedule certainty, short duration, hard requirements,
and application friction remain visible as diagnostic fit scores and cautions.
Where the structured salary is only a headline maximum, an employer-detail
review can override it with the rate applicable to the weekend role; the report
shows the conversion basis and caution rather than silently comparing unlike
amounts. District proximity remains only in the source report and no longer
drives the unified rank once exact transit time is available.
Shift end time—including before/after 17:00 and
overnight work—is retained as report metadata but is neutral in ranking. The
`--end-before` argument controls only the informational schedule annotation.
B-Mart cold work receives a summer-usefulness bonus; Coupang's pickup-area
campaign is penalized because the actual center is not verified. JSON and CSV
include the target district, proximity penalty, rank, tier, score, positive
reasons, and cautions so the weighting can be changed
later without losing the underlying facts.

Job-specific application-message analysis is cached by the posting fields that
affect the message. Unchanged postings reuse the prior analysis, while new or
changed postings are regenerated. `MESSAGE_RULESET_VERSION` invalidates the
cache when the message/profile rules change. The command prints cache hit and
miss counts after every run.

If preferences change, edit the Albamon saved condition and rerun. The special
pages accept the same condition payload even though their web UI exposes looser
filters, so no code edit is normally needed.

## Saved-search-only report

`tools/albamon_jobs.py` exports application-ready metadata from a saved
Albamon 맞춤알바 condition. It uses read-only public endpoints and does not need
browser cookies or account credentials.

Current saved condition:

- Title: `맞춤조건 1`
- `suitNo`: `35138482`

Run the current Saturday-and-Sunday-only check:

```bash
python3 tools/albamon_jobs.py \
  --suit-no 35138482 \
  --strict-weekdays \
  --sat-sun-only \
  --exclude-stale \
  --exclude-driving-required \
  --exclude-month-or-longer \
  --end-before 17:00 \
  --output-prefix artifacts/albamon/latest
```

The command writes:

- `latest.json`: the saved condition, detailed records, hard requirements,
  duration evidence, application methods, stable IDs/links, and any detail-page
  errors.
- `latest.csv`: compact application/filtering fields for spreadsheets.
- `latest.md`: a human-readable list and a separate section for early shifts.

To change the areas, duration, age, gender, or other Albamon-supported filters,
edit the saved condition on Albamon and rerun. The script fetches the live saved
condition by `suitNo`; no code change is needed. Use a different `--suit-no` for
another saved condition.

`--strict-weekdays` changes Albamon's `excludeNegoWorkWeek` request field to
exclude listings whose selected weekdays are negotiable. `--sat-sun-only` then
checks each public detail page and keeps only records whose structured weekdays
are exactly `sat,sun`. This second check removes listings that were tagged as
weekend work but actually specify Friday-only, Saturday-only, or other days.

`--exclude-stale` removes listings whose explicit work-term end date is before
the run date. `--exclude-driving-required` removes explicit driver-licence
requirements and roles whose work itself is driving or valet service; a generic
"able to drive preferred" field does not exclude an unrelated job.
`--exclude-month-or-longer` uses the employer-written posting content, not the
broad Albamon duration label. It recognizes explicit minimums such as
`최소 2개월`, `6개월 이상`, fixed month-long date ranges, and non-negotiable
month ranges. A range marked `협의가능` is reported as negotiable instead of
being treated as a hard minimum. If the detail page's text does not support its
structured `1개월~3개월` label, the report says so explicitly.

Mandatory requirements are extracted separately from `우대` preferences. The
report includes a dedicated certificate/licence field, so a café job that wants
experience but does not require a barista certificate will say exactly that.
Descriptions rendered only as images can be audited in
`tools/albamon_manual_reviews.json`; the default run merges those dated reviews
and records their evidence source. Use `--manual-reviews PATH` for another
review file.

The exporter intentionally performs no application actions. Applying, uploading
a resume, or sending a custom message is an external side effect and should be a
separate confirmed workflow. The exported stable job link, job ID, company,
schedule, requirements, and application-channel metadata are retained to support
that later workflow. Because the fast exporter is intentionally unauthenticated,
its `application_status_from_public_read` field is not reliable evidence that the
signed-in account has or has not already applied; recheck that field in the
authenticated browser before a bulk-application run.

Albamon's BFF and Next.js page shapes are not documented public APIs and may
change. A failed detail page is reported and omitted from verified results rather
than being treated as a match.
