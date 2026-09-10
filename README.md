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

## Configuration and secrets

Public, documented defaults live in [`config/jobscout.toml`](config/jobscout.toml).
Every supported source, duration bucket, date window, pay threshold, commute
rule, cache lifetime, and dashboard limit is listed there with its allowed
values. Its saved-search ID and date windows deliberately fail closed, so a
fresh public clone cannot start somebody else's search accidentally. Do not edit
Python constants to change search preferences.

For personal settings, copy the override templates and edit the ignored copies:

```bash
cp config/jobscout.local.example.toml config/jobscout.local.toml
cp config/applicant_profile.example.toml config/applicant_profile.local.toml
cp .env.example .env
```

Set the Albamon saved-search ID, personal commute defaults, search windows, and
optional verified résumé facts only in those ignored files. A different TOML
override can be selected with `JOBSCOUT_CONFIG=/absolute/path/settings.toml`.
Nested override tables are merged with the public defaults; unknown keys,
invalid enum values, malformed ranges, and secret-like configuration fail
before network activity. Existing CLI options remain one-run overrides.

Common changes all belong in the ignored override:

- Change Albamon scope with `enabled_sources`, `work_periods`,
  `target_districts`, and `availability_end_date` under `[albamon]`.
- Change evening eligibility with the hours, districts, stations, and walking
  model under `[albamon.evening]`.
- Change origin limits and optional long-commute pay extensions under
  `[commute]`; the dashboard-confirmed origin remains in its ignored JSON file.
- Change agency dates, weekend-only behavior, providers, and the actual/base
  pay floor under `[weekend_agencies]`.
- Change result weighting without changing eligibility under `[ranking]`.
- Change quotas, cache lifetimes, and exploration bounds under `[kakao]`,
  `[cache]`, and `[dashboard]`.

Credentials are intentionally excluded from TOML and JSON. Put only
`KAKAO_REST_API_KEY`, `DISCORD_WEBHOOK_URL`, and
`DISCORD_WEBHOOK_URL_HOTELIER` in `.env` or same-named process variables.
`.env*` is ignored except for the empty `.env.example`; application artifacts,
the selected commute origin, and the applicant profile are ignored as well.
Before making the repository public, scan both the current tree and Git history
with a credential scanner such as `gitleaks dir . --redact` and
`gitleaks git . --redact`. The scanner will still see an ignored local `.env`
in a whole-directory scan; never stage that file, and inspect only redacted
output.

For a fresh clone, copy the two `*.example.json` files in `tools/` to their
matching non-example names before recording application history or personal
defer/do-not-apply choices. Missing application history is treated as an empty,
explicitly unchecked snapshot; it is never fabricated from the public list.
The historical application-message regression class uses a local ignored report
fixture and is skipped when that fixture is not present.

## Separate weekend staffing-agency report

WithYou, Huplux, Eumplus, 어울림, and 루틴 are collected by an isolated adapter
package; they do not enter Albamon's combined report, application
dispositions, or Discord notification state. The monitored date range,
weekend-only switch, enabled providers, and minimum actual/base wage are set in
`[weekend_agencies]`. A 주휴-inclusive headline never rescues an offer below the
configured base-pay floor.

The JSON and Markdown reports also carry a seven-agency reference catalog for
데이에이전시, 후플럭스 (HUPLUX), 이음플러스, 위드유, 호텔서비스
(HTService), 어울림, and 루틴. It records their principal hotels or specialties
without a subjective star rating. Existing collector IDs
(`huplux`, `eumplus`, `withyou`, `eoullim`, and `rutine`) resolve to those canonical
entries. 데이에이전시 remains catalog-only because its exact dated shifts are
behind an authenticated application flow; static hotel pages are not fabricated
into live jobs. HTService is also catalog-only: its observed target jobs fell
below the actual/base pay threshold and its public source uses unencrypted HTTP.

```bash
python3 tools/weekend_agency_refresh.py \
  --env-file .env \
  --notify
```

The command atomically writes `artifacts/weekend-agencies/latest.json`, `.csv`,
and `.md`. Results are ranked by actual/base pay descending and verified
commute ascending. The report displays 주휴-inclusive values separately and
never uses them for the minimum-pay filter or rank. Materially equivalent
venue/date/shift/role/department offers are grouped across agencies, while all
source links and pay variants remain visible and the highest-paying agency is
named.

The source HTTP client uses fixed-host allowlisting, bounded responses,
idempotent retry, a five-second Huplux request cadence, and a one-second 어울림
cadence. 어울림's legacy CP949 list is decoded explicitly and its image-based
open/closed state is preserved; its login-gated detail page is never polled.
루틴's anonymous public table is collected in one bounded request and keeps
every exact shift while separating base and 주휴-inclusive pay.
Raw rows, page-boundary duplicates, unique listing IDs, open IDs, and target-date
offers are reported separately so an empty target window is observable. A transient source
failure may reuse only a visibly marked snapshot within the configured cache lifetime;
otherwise the previous complete report is preserved. Successful source
snapshots are still checkpointed when another source fails, including valid
empty results. Venue routes share Albamon's Kakao free-tier ledger and request
pacer. The workflow exits without fetching after the configured search end.

Hotel-agency Discord delivery is also isolated from Albamon. It reads only
`DISCORD_WEBHOOK_URL_HOTELIER` from the selected workspace `.env` (or the same
named process variable), never falls back to `DISCORD_WEBHOOK_URL`, and stores
semantic delivery state in
`artifacts/weekend-agencies/cache/discord_notifications.json`. The first
successful notification run sends the complete result set grouped into one
overview row per hotel plus bounded hotel-detail pages, then records the hotel
summaries as its baseline. Each summary shows overall and daytime maximum actual
pay, conditional 주휴 pay, commute, dates, every distinct shift, roles, provider
price comparisons, and representative provider links. Later runs resend only a
new or materially changed hotel's complete block, checkpointing each successful
page independently and resuming an interrupted full digest from its last page.
Actual/base pay and the conditional 주휴-inclusive value remain separate in
every message. The webhook value is validated, never logged, and never written
to an artifact.

After placing the REST key in the ignored workspace `.env`, run the complete
workflow without putting the credential in the command line or shell history:

```bash
python3 tools/albamon_refresh.py
```

For the scheduled end-to-end workflow, refresh and notify in one locked
transaction:

```bash
python3 tools/albamon_refresh.py --notify \
  --webhook-env-file .env
```

Only one refresh process and one Discord state writer may run at a time.
Overlapping four-hour/manual executions exit cleanly instead of racing cache or
notification state. JSON, CSV, and Markdown outputs are atomically replaced,
and every combined/unified pair carries a matching run ID. Transit IDs, report
IDs, counts, and ended-term exclusions are validated before Discord delivery.

This produces `latest-combined.*`, refreshes/reuses public-transit routes, and
writes `latest-unified-report.*`. Kakao public-transit calls are guarded by an
atomic Korea-date ledger at
`artifacts/albamon/cache/kakao_public_transit_quota.json`. The public default
ceiling and pacing are documented under `[kakao]`; the shipped values retain a
100-call margin below Kakao's published 1,000/day free quota. Cache hits do not
consume the budget. Kakao request starts are shared across all workers and use
the configured global pace. Because Kakao error `-10` can mean either a
per-second limit or quota exhaustion, the first occurrence applies one shared
configured cooldown and one paced retry; a repeated `-10`, local ceiling,
Kakao HTTP 429, or API access error stops further calls and marks affected
worksites for a Kakao Maps WebUI fallback. Use `--combined-only` only when
route/report generation is not needed.

Kakao currently grants the free Kakao Map quota only to the first Map-enabled
app per developer account. Confirm the `카카오맵 무료 쿼터` badge in the Kakao
Developers app console, keep paid API usage disabled, and enable Kakao Map API
for the app. The local guard cannot inspect or override account billing settings.

## Local decision dashboard

The reporting pipeline remains the source of truth. To explore its latest
output in a linked ranked table and worksite map, run:

```bash
python3 tools/albamon_dashboard.py
```

The Hotel agencies tab can run a separate, non-notifying exploration for any
future date range up to the configured exploration limit. The selected range is saved in browser local
storage, writes immutable results only to `artifacts/weekend-agencies/explore/`,
and never changes the monitored Discord date window. A normal search
reuses an identical result for the configured exploration-cache duration; `Refresh sources` explicitly
bypasses that cache. Network searches and origin recalculations run as bounded
background operations with visible progress and cooperative cancellation, so
the local HTTP server remains responsive. Rows whose work date is before the
current Korea date are filtered from both report refreshes and dashboard
projections.

Hotel results can be narrowed by available date, an overnight-aware shift-time
window, verified commute, actual/base or conditional 주휴 pay, role, provider,
and hotel/provider text. The same exact-shift subset drives the table, map, and
recomputed Pareto frontier. Filter sets persist locally, can be saved under a
name, and can be copied into the dashboard URL. Each hotel exposes estimated
shift earnings, an hourly rate after round-trip commute time, late-return risk,
and pay-derivation warnings. Overlapping chart or map targets open a chooser
instead of hiding one another, and source-health details distinguish live,
cached, empty, disabled, and login-gated feeds. Report downloads appear once in
the shared header.

The dashboard shows the active commute origin above both data views. A fresh
clone starts from the non-personal public landmark configured in
`config/jobscout.toml`; a user's chosen
origin remains only in the ignored `tools/commute_origin.json`. An address can
be resolved with the shared Kakao free-tier ledger, or a coordinate can be
selected directly from either map without an API call. A confirmed change first
recalculates Albamon and hotel routes against the candidate, then publishes the
origin, caches, and reports together. A failed recalculation restores the prior
cache state and leaves the active origin unchanged. Kakao and Naver browser
links are generated locally and do not consume an API quota.

Then open <http://127.0.0.1:8765>. The server binds only to the local machine
and has no additional Python dependencies. It reads the current combined,
unified, transit, and Discord-state files each time the page reloads; it does
not scrape Albamon or replace the four-hour refresh.

The default view shows actionable jobs ranked by the report's strict pay-first,
exact-transit-second order. Filters cover text, status, exact versus negotiated
weekend evidence, policy-adjusted or fixed commute limits, comparable hourly
pay, district, priority zone, recent discovery, hard requirements, and easy
online application, with a quick closing-within-72-hours constraint. Dedicated
schedule and zone filters expose safe remote work and the station-corridor
evening lane. The trade-off view exposes the pay/commute Pareto frontier: it
keeps actionable jobs for which no comparable job pays at least as much with
an equal-or-shorter verified commute. Remote and on-site jobs use separate
frontiers so remote work does not hide useful physical worksites. The zone sort
puts remote first, then nearby corridors, the configured premium-commute
districts, and the rest of Seoul. Selecting a row or map marker opens the full decision context,
verified route, tailored application message, and the pre-send checklist.
Markdown, CSV, and JSON reports remain downloadable from the header.

Select `Add to compare` on up to three rows for a side-by-side pay, commute,
schedule, duration, requirements, and report-rank view. Filter and sort choices
are remembered locally; `Reset filters` clears them. The compact shortlist is
loaded before external map assets, while full messages and ranking evidence are
requested only when a job is opened. Generated reports remain complete and are
not reduced by these dashboard performance optimizations. Search text, dates,
numeric sort keys, schedule lanes, and application flags are normalized once
per payload rather than on every filter change; table rows use delegated events
instead of allocating listeners per row. Pareto marking runs in O(n log n).

“Maybe later,” “Skip job,” and “Restore actionable” update
`tools/albamon_application_dispositions.json` and immediately rebuild all three
unified report formats. Applied status is intentionally read-only in the app;
the authenticated Albamon application-history snapshot remains authoritative.

The dashboard separates three decision queues. `New in 24h` is the rolling
24-hour subset of actionable jobs based on first-seen notification state;
`Actionable matches` passed the automated date, schedule, pay, commute,
worksite, and exclusion rules; and `Manual review queue` contains either an
unverified posting condition or a job deliberately marked `Maybe later`.
Opening a manual-review row shows its exact evidence checklist. Marking a
posting-condition check complete records only that evidence fingerprint in the
local, ignored `tools/albamon_review_resolutions.json`; it does not apply or
contact the employer, and changed posting evidence automatically reopens the
review. Deferred jobs leave the queue when restored or skipped, while applied
jobs leave when authenticated application history confirms them.

## Four-hour Discord notifications

After a successful unified refresh, notify Discord only about newly actionable
jobs with:

```bash
python3 tools/albamon_discord.py \
  --combined artifacts/albamon/latest-combined.json \
  --report artifacts/albamon/latest-unified-report.json
```

`DISCORD_WEBHOOK_URL` is read from the explicitly selected workspace `.env`
file first, then from the process environment only when the file has no value.
Do not point scheduled runs at another repository's env file. The URL is
validated as an official HTTPS Discord webhook and is never
copied into an artifact or printed. Messages include the employer, job link,
pay and comparable hourly pay, schedule, commute and transfers, duration, hard
requirements, application methods, tailored Korean application message, and
the checks required before sending it.

Notification state is stored in the ignored
`artifacts/albamon/cache/discord_notifications.json` file. Jobs are keyed by a
hash of their semantic identity, so an unchanged Albamon repost does not create
another notification. Successful deliveries are checkpointed one job at a time,
and state entries not seen for 90 days are pruned. The longer notification
retention intentionally prevents an old repost from being announced again; it
contains only hashed semantic identities and timestamps.

On initial setup, establish a quiet baseline so all current matches are not sent
as new jobs:

```bash
python3 tools/albamon_discord.py --bootstrap --announce-bootstrap
```

The bootstrap sends one monitoring-enabled confirmation but does not send the
existing jobs. Later runs remain silent when there are no new actionable jobs.
Use `--dry-run` to preview pending messages without contacting Discord or
changing notification state.

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
resume identifiers. Every job receives a role-aware, evidence-grounded draft
so a daily run still completes, with a mandatory detailed-posting review note.
Job-specific pay corrections are limited to reviewed public-posting evidence
and never contain applicant data.

Persistent per-job choices live in
`tools/albamon_application_dispositions.json`. A `maybe_later` or
`do_not_apply` entry removes that job from the actionable headings and places it
under `부적합·나중에 지원 검토`; if the job is later found in application
history, the applied status takes precedence.

## Daily combined report

`tools/albamon_combined_jobs.py` is the repeatable workflow for one deduplicated
report covering 맞춤알바, a one-day multi-slot safety-net query, a configured
evening query, an exact configured-station safety net, 재택알바, 여름알바, and
급구알바. It reads the live saved condition selected by
`albamon.saved_search_id`, applies those
same filters to the special-list APIs, then verifies the detail content rather
than trusting only Albamon's broad tags. The safety-net query removes Albamon's
server-side weekday constraint only for one-day jobs; this catches postings that
store one representative weekday while offering selectable Sat/Sun slots in the
actual description.

Weekend availability means any verified Saturday or Sunday shift; it does not
require both days. Required weekday attendance, weekday training, and bundled
multi-day schedules still fail closed unless the posting exposes an independent
weekend option.

The station-corridor evening query removes weekday constraints and uses the
districts, work-period buckets, deadline, hours, stations, and walking model in
`[albamon]` and `[albamon.evening]`. Detail screening admits only work that fits
those settings. The default station catalog
covers 역삼·강남·송파·잠실·잠실새내·삼성; the configurable walking model is
applied separately from the verified origin-to-work commute rule.
A second bounded station-tab query uses the configured Albamon station codes.
Before opening details, it retains only rows with a list-level weekend,
configured evening-window, or independently selectable-day signal; the detail
worksite must still pass the configured station check.

The remote lane ignores the posting's nominal city because no commute is
required, but is deliberately stricter elsewhere: only stated hourly wages at
or above `albamon.remote_minimum_hourly_won`, short commitments, a
weekend/evening/fully flexible
schedule, readable details, and currently applicable posts can pass. Signup,
fees, account sharing, promotional work, surveys, review/comment work, and
personal photo/video collection signals are rejected. Remote rows receive a
zero-request `not_required` transit result.
A separate browser-
verified route cache in `tools/albamon_late_night_route_cache.json` attaches
each listing's exact advertised start/end times to Naver Map future-time transit
results and retains Kakao's measured bicycle road-priority route as fallback.
Naver offers future dates and 10-minute departure slots. Outbound checks use the
latest slot whose resulting arrival is no later than the advertised start; the
stored elapsed time includes timetable waiting, rather than copying Naver's
smaller displayed walking/riding duration. Return checks depart at the exact
advertised end time. The representative service date is stored with both legs
and must be checked again for the eventual work date.

The shipped configuration currently specifies:

- Area: all 25 Seoul districts (the saved-search area selection is overridden at
  API collection time)
- Period buckets, availability deadline, and enabled collection lanes come from
  `[albamon]`. The public defaults omit the one-to-three-month bucket.
- Weekend (Saturday or Sunday), including negotiated schedules with a verified
  weekend option
- Neutral demographic metadata by default; personal overrides may narrow it
- Education requirements are recorded from each posting rather than inferred

Run it with:

```bash
python3 tools/albamon_combined_jobs.py \
  --output-prefix artifacts/albamon/latest-combined
```

The default refresh is incremental and checkpoints to
`artifacts/albamon/cache/details.json`. New jobs and jobs whose list-level
fingerprint changed always receive a full detail fetch. Unchanged posting
refresh intervals, excluded-entry TTL, and retention are controlled by
`[cache]`. Inactive excluded and authoritatively closed entries are removed
immediately. A missing ID never survives into the live
report merely because it remains in the cache. Semantic deduplication includes
material schedule and guaranteed-pay fields, so operationally different shifts
or pay offers are not collapsed. Use `--no-detail-cache` for
a cold audit, or adjust `--closing-refresh-hours`, `--eligible-refresh-hours`,
`--applied-refresh-hours`, `--cache-ttl-days`, `--cache-retention-days`, and
`--checkpoint-every` when diagnosing cache behavior.

The main saved search, bounded one-day multi-slot query, and station-corridor
evening query run every refresh. Summer, urgent, remote, and exact-station
source-page membership is cached for four hours in
`artifacts/albamon/cache/special_sources.json`; the cache is invalidated when
the saved Albamon condition changes. This avoids repeating source-list reads
and the report records per-source exclusive IDs/counts plus overlap totals so a
tab that silently stops contributing is visible. Use `--no-special-source-cache`
for an exhaustive source-membership audit. Search pages request up to 5,000
rows by default, while still honoring a smaller page size returned by Albamon.

Detail fetching uses Albamon's compact `/v1/recruit/view` and
`/v1/recruit/view/detail` responses in parallel. Both responses are required;
their merged posting text is what drives weekend, duration, licence, and hard-
requirement screening. The much larger public HTML page is retained as a
per-job fallback if the compact API changes or fails. Ambiguous list metadata
is never used to skip detail verification. Safe list-level reductions are
limited to server-side adult/bar exclusion, conclusive non-Seoul coordinates
and addresses, explicit clinical/driving titles, remote safety/pay checks,
bounded station availability signals, and exact operational duplicate
signatures.

Each JSON report contains a `performance` object with list/detail timings,
request and byte counts, retries, cache hits, network jobs, checkpoints,
fallbacks, early exclusions, and duplicate counts. This makes a slow or
degraded run diagnosable without logging response bodies or credentials. The
orchestrated refresh also retains the latest 50 successful run summaries in
`artifacts/albamon/cache/performance_history.json`, including stage timings,
request/cache metrics, route activity, and output counts. The history is
bounded and contains no response bodies or credentials.

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

The combined report excludes work terms ending before the run date. A same-day
job stays available until its last exact advertised shift starts; when no exact
start is available it is retained until the next date rather than disappearing
prematurely. It also excludes fixed terms extending past the configured
availability end, minimum commitments that no longer fit between the run date
and that deadline, driver/licence roles, demographic mismatches, out-of-area
branches, and negotiated ads that do not actually document a Saturday or Sunday
option.
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
verified public-transit minutes from the configured commute origin ascending when
pay is equal.
Before ranking, run the Kakao Maps web-UI pass from the configured origin and
then regenerate the application report:

```bash
python3 tools/albamon_application_messages.py \
  --input artifacts/albamon/latest-combined.json \
  --transit-cache tools/albamon_transit_cache.json \
  --output-prefix artifacts/albamon/latest-unified-report
```

Ordinary jobs prefer Kakao's official public-transit REST endpoint from
the configured commute origin to the exact Albamon worksite. The endpoint has no
future-departure parameter, so its result is recorded as a current route pattern
and is not presented as an exact 10:00 timetable. Browser verification remains
the fallback when the local free-call budget is unavailable, Kakao rejects the
request, or a borderline time needs a specific service-time check. Songpa
late-night jobs continue to use Naver Map future-time transit for each advertised
shift and Kakao's reverse-direction bicycle road-priority route as fallback.
When a Naver future result exists, the unified distance filter uses its full
departure-to-arrival elapsed time, including scheduled wait. If an exact shift
has not yet received a Naver pass, the Kakao route pattern remains visibly
provisional. The unified report uses `[commute].base_limit_minutes` as its
inclusive standard cutoff. Optional pay-dependent extensions—including their
districts, thresholds, slopes, and hard cap—are defined under
`[commute.premium_extension]`. Unknown routes remain excluded.
Listings whose actual worksite cannot be verified (notably pickup-only Coupang
campaigns) are conservatively placed in `거리 제외` as well. Transit is
time-sensitive, so routes are refreshed after the configured local refresh hour and borderline routes
should be checked again at the intended departure time.

Transit results are keyed by exact worksite address (or verified coordinates),
not recruit number, so reposts and same-site jobs share a lookup. New worksites
are checked immediately. Existing route refresh hour and maximum cache age come
from `[commute]` and can be overridden once with `--route-refresh-hour` or
`--route-cache-hours`. A broad
district label without an exact address is never shared between jobs. Changing
the configured origin automatically invalidates all prior routes; a cutoff-only
reclassification is refused until the routes have been recomputed from the new
origin. Scheduled refreshes may temporarily reuse a stale verified route with a
visible warning so one expired entry cannot block every Discord notification.
Any new, unverified worksite remains distance-excluded until its API or WebUI
pass.

When only the cutoff changes, reuse the already-fetched route durations without
another API pass:

```bash
python3 tools/albamon_transit.py \
  --output tools/albamon_transit_cache.json \
  --reclassify-cache
```

The unified application ranking is strict pay-first and exact-commute-second.
Mixed salary units are converted to a reliable **work-hour equivalent** only
when the posting supplies enough shift evidence. Daily pay is divided by the
displayed shift span minus an explicitly stated break; monthly pay requires a
stated weekly schedule. Unknown daily, weekly, or monthly hours are never filled
with an assumed 8-, 40-, or 209-hour value: those jobs retain their advertised
pay, are labelled unverified, and are excluded from pay ranking and Pareto
membership. A lower-paying job cannot move above a higher-paying job because of
commute or fit; commute is the first tie-break only when reliable comparable pay
is equal. Schedule certainty, short duration, hard requirements, and application
friction remain visible as diagnostic fit scores and cautions.

The dashboard also shows a separate **door-to-door hourly rate** when both fixed
shift earnings and the full scheduled span are known:

`fixed shift earnings / (scheduled shift span + 2 x verified one-way commute)`

This is a personal time-efficiency metric, not the advertised wage. It is not
used as the Pareto pay axis because that chart already uses commute as its other
axis; using it there would count the same commute penalty twice. Hourly offers
without a reliable paid duration do not receive an invented shift total or
door-to-door rate.
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

Set `albamon.saved_search_id` in the ignored local configuration before using
the saved-search source. The public default is zero and fails closed with a
setup message rather than exposing or using somebody else's saved search.

Run the current Saturday-and-Sunday-only check:

```bash
python3 tools/albamon_jobs.py \
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
