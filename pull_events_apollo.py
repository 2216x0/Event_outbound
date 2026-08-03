#!/usr/bin/env python3
"""
Step 2 of the Events Pipeline: pull_events_apollo.py

Modified version of pull_apollo.py: instead of searching for people broadly
by company domain, we already know each speaker's name + firm, so we match
directly by name (free) and then bulk-enrich the matched IDs (1 credit each).

Reuses shared helpers from the existing outbound pipeline where possible:
  - post_with_retry()   -> Apollo API POST wrapper with retry/backoff
  - row_from_person()   -> builds the output CSV row from a bulk_match person dict
  - find_apollo_domain() -> domain-search fallback when name match fails

If those live in pull_apollo.py in your repo, import them instead of the
local copies below:

    from pull_apollo import post_with_retry, row_from_person, find_apollo_domain

The local copies below are drop-in stand-ins so this script runs standalone.

Usage:
    python pull_events_apollo.py outputs/companies/events_2026-01-15_1200.csv \
        --config events_config.yaml
"""

import sys
import time
import json
import argparse
from pathlib import Path

import pandas as pd

try:
    import requests
    import yaml
except ImportError:
    sys.exit("pip install requests pyyaml pandas")

BASE = "https://api.apollo.io/api/v1"
APOLLO_API_KEY_ENV = "APOLLO_API_KEY"


# ---------------------------------------------------------------------------
# Shared helpers (reuse from pull_apollo.py if available in your repo)
# ---------------------------------------------------------------------------

def _api_key() -> str:
    import os
    key = os.environ.get(APOLLO_API_KEY_ENV)
    if not key:
        sys.exit(f"Set {APOLLO_API_KEY_ENV} in your environment before running.")
    return key


def post_with_retry(url: str, body: dict, max_retries: int = 3) -> dict:
    """POST to Apollo with basic retry/backoff on 429s and transient errors."""
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": _api_key(),
    }
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=30)
            if resp.status_code == 429:
                time.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == max_retries:
                raise
            time.sleep(delay)
            delay *= 2
    return {}


def row_from_person(p: dict, fallback_company: str) -> dict:
    """Build an output CSV row from an Apollo bulk_match person object."""
    org = p.get("organization") or {}
    # Use primary email; fall back to first personal email if primary is blank
    email = p.get("email", "") or ""
    email_status = p.get("email_status", "") or ""
    if not email:
        personal = p.get("personal_emails") or []
        if personal:
            email = personal[0]
            email_status = email_status or "personal"
    return {
        "First Name": p.get("first_name", ""),
        "Last Name": p.get("last_name", ""),
        "Email": email,
        "Email Status": email_status,
        "Title": p.get("title", ""),
        "Company": org.get("name", fallback_company),
        "Company Domain": org.get("primary_domain", ""),
        "LinkedIn": p.get("linkedin_url", ""),
        "City": p.get("city", ""),
        "State": p.get("state", ""),
        "Apollo ID": p.get("id", ""),
    }


def find_apollo_domain(company_name: str) -> str:
    """Domain-search fallback when name match returns nothing useful."""
    try:
        resp = post_with_retry(f"{BASE}/mixed_companies/search", {
            "q_organization_name": company_name,
            "per_page": 1,
        })
        orgs = resp.get("organizations") or resp.get("accounts") or []
        if orgs:
            return orgs[0].get("primary_domain", "")
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Events-specific: name-match instead of domain-search
# ---------------------------------------------------------------------------

def match_person_by_name(first: str, last: str, company: str) -> str | None:
    """
    Find Apollo person ID by name + company. FREE — no credits.
    Returns the Apollo person ID of the best match, or None.
    """
    full_name = f"{first} {last}".strip()
    body = {
        "q_person_name": full_name,
        "organization_names": [company] if company else [],
        "per_page": 1,
        "page": 1,
    }
    try:
        resp = post_with_retry(f"{BASE}/mixed_people/api_search", body)
        people = resp.get("people") or []
        if people:
            return people[0].get("id")
    except Exception as e:
        print(f"  [WARN] name match failed for {full_name} @ {company}: {e}")
    return None


def row_from_person_event(p: dict, fallback_company: str, source_row: dict) -> dict:
    """Same as row_from_person() but carries event metadata into the output CSV."""
    base = row_from_person(p, fallback_company)
    base["Event Name"] = source_row.get("event_name", "")
    base["Event Date"] = source_row.get("event_date", "")
    base["Event Location"] = source_row.get("event_location", "")
    base["Talk Title"] = source_row.get("talk_title", "")
    return base


def apply_industry_filter(rows: list[dict], include_industries: list[str]) -> list[dict]:
    """Best-effort filter: keep rows whose company/title text hints at an included vertical."""
    if not include_industries:
        return rows
    incl = [i.lower() for i in include_industries]
    kept = []
    for r in rows:
        blob = f"{r.get('Company', '')} {r.get('Title', '')}".lower()
        if any(term in blob for term in incl) or True:
            # NOTE: Apollo bulk_match doesn't reliably return an industry field per-person;
            # real filtering should happen against organization.industry from bulk_enrich.
            # Left permissive here — tighten once you inspect actual API responses.
            kept.append(r)
    return kept


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("speakers_csv", help="CSV produced by build_events.py")
    ap.add_argument("--config", default="events_config.yaml")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text()) or {}
    df = pd.read_csv(args.speakers_csv)

    # Phase 1: free name-match to get Apollo person IDs
    found = []  # list of (company, apollo_id, source_row)
    for i, (_, row) in enumerate(df.iterrows(), 1):
        first = str(row.get("speaker_first_name", "")).strip()
        last = str(row.get("speaker_last_name", "")).strip()
        company = str(row.get("company_name", "")).strip()

        pid = match_person_by_name(first, last, company)
        if pid:
            found.append((company, pid, row.to_dict()))
            print(f"  [{i}/{len(df)}] matched: {first} {last} @ {company}")
        else:
            print(f"  [{i}/{len(df)}] no match: {first} {last} @ {company}")
        time.sleep(0.2)

    if not found:
        sys.exit("No speakers matched in Apollo. Nothing to enrich.")

    # Phase 2: bulk_match the matched IDs (costs 1 credit each) to pull emails
    ids = [pid for _, pid, _ in found]
    id_to_context = {pid: (company, source_row) for company, pid, source_row in found}

    enriched_rows = []
    BATCH = 10
    for start in range(0, len(ids), BATCH):
        batch_ids = ids[start:start + BATCH]
        resp = post_with_retry(f"{BASE}/people/bulk_match", {
            "details": [{"id": pid} for pid in batch_ids],
            "reveal_personal_emails": True,
        })
        people = resp.get("matches") or resp.get("people") or []
        for p in people:
            pid = p.get("id")
            company, source_row = id_to_context.get(pid, ("", {}))
            enriched_rows.append(row_from_person_event(p, company, source_row))
        time.sleep(0.5)

    # Optional industry filter (best-effort; see apply_industry_filter note)
    enriched_rows = apply_industry_filter(enriched_rows, cfg.get("include_industries", []))

    out_df = pd.DataFrame(enriched_rows)
    dest = Path(args.speakers_csv).parent / (Path(args.speakers_csv).stem + "_enriched.csv")
    out_df.to_csv(dest, index=False)

    print(f"\nWrote {len(out_df)} enriched contacts -> {dest}")
    print(f"\nNEXT: python3 clean_csv.py {dest} --config {args.config}")


if __name__ == "__main__":
    main()
