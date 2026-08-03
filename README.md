# Event Outbound Pipeline

Finds finance/investment conferences and their speakers, enriches speakers with
verified emails, and stages an Instantly campaign — so outreach can target people
who are *speaking at* (or sponsoring) a relevant event instead of a cold list.

Sister project to [`outbound`](https://github.com/2216x0/outbound): this repo
handles event/speaker sourcing, then hands off to that repo's
`launch_instantly.py` and `verify_instantly.py` for the send side. **Clone both
repos as siblings** (`outbound/` and `Event_outbound/` in the same parent folder)
— `run_events_pipeline.py` looks in `../outbound/` for any script it can't find
locally.

## The flow

```
  [discover events]                     discover_events.py / discover_associations.py
          |                              (Claude API + web search, writes outputs/discovered/*.json)
          v
  [pick which events matter]             events_dashboard.html (or Claude Code via /api/select)
          |
          v
  [crawl for speakers]                   crawl_event.py  -> outputs/companies/events_<date>.csv
          |
          v
  1. build_events.py      -> outputs/companies/events_<date>.csv    (speaker/agenda scrape, no credits)
          |
          v
  2. pull_events_apollo.py -> ..._enriched.csv                       (name-match + bulk enrich, Apollo credits)
          |
          v
  3. clean_events_csv.py   -> ..._clean.csv                          (dedupe, drop no-email rows, cap/company)
          |
          v
  4. launch_instantly.py   -> PAUSED campaign in Instantly            (from ../outbound/)
          |
          v
  5. verify_instantly.py   -> read-only API health check              (from ../outbound/)
          |
          v
  [YOU: paste email copy referencing the event/talk, review, LAUNCH in Instantly]
```

`run_events_pipeline.py` runs steps 1–5 for you. `events_server.py` wraps
discovery, crawling, and the pipeline in a browser dashboard with live progress.

## One-time setup

```bash
pip3 install pandas pyyaml requests beautifulsoup4 lxml flask anthropic
# optional, for JS-heavy event sites:
pip3 install crawl4ai
```

```bash
export ANTHROPIC_API_KEY="..."     # event/association discovery (discover_events.py, discover_associations.py)
export APOLLO_API_KEY="..."        # speaker enrichment (pull_events_apollo.py)
export INSTANTLY_API_KEY="..."     # campaign creation (launch_instantly.py, verify_instantly.py)
```
`events_server.py` also auto-loads a `.env` file in this folder if present, so you
can drop the three keys there instead of exporting them each session.

## Per-trip run

### Option A — Dashboard (recommended)
```bash
python3 events_server.py        # http://localhost:5556
```
Discover events or associations from the UI, pick which ones to pursue, crawl
them for speakers, preview the resulting CSV, then run the enrich → clean →
launch → verify steps with live progress.

### Option B — CLI, step by step
```bash
# 1. Find candidate events (writes outputs/discovered/events_<ts>.json)
python3 discover_events.py "PE and VC summits US Q1 2027"

# (optional) Find trade associations / industry bodies instead of events
python3 discover_associations.py "Hong Kong private equity"

# 2. Crawl selected events for speakers -> outputs/companies/events_<date>.csv
python3 crawl_event.py --events-json outputs/discovered/crawl_input.json

# 3. Enrich speakers with Apollo (name-match, then bulk enrich — spends credits)
python3 pull_events_apollo.py outputs/companies/events_<date>.csv --config events_config.yaml

# 4. Clean + dedupe + cap per company
python3 clean_events_csv.py outputs/companies/events_<date>_enriched.csv --config events_config.yaml

# 5. Create the paused Instantly campaign (shared script, lives in ../outbound/)
python3 ../outbound/launch_instantly.py outputs/companies/events_<date>_enriched_clean.csv \
    --config events_config.yaml --name "AVCJ Japan 2026"

# 6. Health check
python3 ../outbound/verify_instantly.py --config events_config.yaml
```

### Option C — CLI, fully orchestrated
```bash
python3 run_events_pipeline.py                        # uses events_config.yaml
python3 run_events_pipeline.py --dry-run               # preview, skips paid steps
python3 run_events_pipeline.py --from 2                # resume from step 2
python3 run_events_pipeline.py --stop-after 1           # build the speaker list, then pause for review
python3 run_events_pipeline.py --campaign-name "AVCJ Japan 2026"
```

**Finish in Instantly (manual):** open the paused campaign, paste copy that
references the event and/or the person's talk, review, launch. Merge fields
include `{{first_name}}`, `{{company_name}}`, `{{event_name}}`, `{{event_date}}`,
`{{event_location}}`, `{{talk_title}}`, plus the standard ones.

## Config

`events_config.yaml` controls the run:

```yaml
contact_type: Event Speaker — Investment Management
events: []                     # filled in by discovery/crawl, or list events by hand
contacts_per_event: 200
apollo_seniorities: []
exclude_title_keywords:
- moderator
- emcee
- host
include_industries:
- financial services
- investment management
- venture capital
- private equity
- capital markets
- asset management
- hedge fund
- wealth management
output_prefix: events
```

## Repo layout

- `discover_events.py` / `discover_associations.py` — Claude + web search to find
  candidate events or trade associations matching a prompt; results go to
  `outputs/discovered/`.
- `crawl_event.py` — deep-crawls selected event sites for speakers/agenda
  (uses `crawl4ai` if installed, else falls back to `requests` + BeautifulSoup).
- `build_events.py` — step 1, generic HTML speaker/agenda scraper for a configured
  list of events.
- `pull_events_apollo.py` — step 2, matches speakers to Apollo by name (free) then
  bulk-enriches matched IDs (1 credit each); falls back to domain search.
- `clean_events_csv.py` — step 3, drops rows with no email, dedupes, caps
  contacts per company, renames columns to what `launch_instantly.py` expects.
- `run_events_pipeline.py` — orchestrates steps 1–5, resolving shared scripts
  (`launch_instantly.py`, `verify_instantly.py`) from `../outbound/` if not found
  locally.
- `events_server.py` + `events_dashboard.html` — browser dashboard: discovery,
  event selection, crawling, CSV preview/download, and pipeline run/stream/stop.
- `outputs/discovered/` — raw discovery results and your event/association
  selection (`selected.json`).
- `outputs/companies/` — speaker CSVs at each pipeline stage (`events_<date>.csv`
  → `..._enriched.csv` → `..._enriched_clean.csv`).
- `outputs/ready/`, `instantly_campaigns/` — per-event exports staged for or sent
  to Instantly.
- `AVCJ_Japan_2026/`, `Private_Equity_Chicago_Forum_Speakers.csv`,
  `manual_speakers.csv` — example/manual speaker lists for specific events.

## Notes

- Apollo credits are only spent in step 2 (`pull_events_apollo.py`), on the bulk
  enrich call after the free name-match search.
- `launch_instantly.py` (shared with `outbound`) sets `skip_if_in_workspace` /
  `skip_if_in_campaign`, so speakers already in Instantly from a past trip or the
  main pipeline won't be re-added.
- `events_server.py` has no authentication and can trigger discovery, crawling,
  and Apollo/Instantly calls — don't expose it on a public network without adding
  auth in front of it.
- Speaker/contact data pulled here (names, emails, LinkedIn URLs) is personal
  data — treat `outputs/`, exported CSVs, and any deployment of this dashboard
  accordingly.
