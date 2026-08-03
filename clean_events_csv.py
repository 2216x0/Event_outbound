#!/usr/bin/env python3
"""
Step 3 of the Events Pipeline: clean_events_csv.py

Takes the enriched CSV from pull_events_apollo.py, drops rows without emails,
deduplicates, caps per-company, and writes an Instantly-ready CSV.

Understands the column names produced by pull_events_apollo.py:
  First Name, Last Name, Email, Title, Company, Company Domain,
  LinkedIn, City, State, Apollo ID, Event Name, Event Date, Event Location, Talk Title

Output: <stem>_clean.csv in the same directory as the input file.

Usage:
    python clean_events_csv.py outputs/companies/events_*_enriched.csv
    python clean_events_csv.py <file> --config events_config.yaml --per-company 10
"""

import re
import sys
import argparse
from pathlib import Path

import pandas as pd

# Input col  →  output col name (for launch_instantly.py + Instantly merge fields)
COL_MAP = {
    "First Name":    "first_name",
    "Last Name":     "last_name",
    "Email":         "email",
    "Email Status":  "email_status",
    "Title":         "title",
    "Company":       "company_name",
    "Company Domain":"company_website",
    "LinkedIn":      "person_linkedin_url",
    "City":          "city",
    "State":         "state",
    # Event metadata — kept for personalisation in email copy
    "Event Name":    "event_name",
    "Event Date":    "event_date",
    "Event Location":"event_location",
    "Talk Title":    "talk_title",
}


def _patterns(keywords):
    return [re.compile(r"\b" + re.escape(k.lower().strip()) + r"\b", re.IGNORECASE)
            for k in (keywords or []) if k.strip()]


def _matches_any(series, patterns):
    if not patterns:
        return series.apply(lambda _: False)
    return series.apply(lambda v: any(p.search(str(v or "")) for p in patterns))


def load_config(path):
    if not path:
        return {}
    try:
        import yaml
        return yaml.safe_load(Path(path).read_text()) or {}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("enriched_csv")
    ap.add_argument("--config", default="events_config.yaml")
    ap.add_argument("--per-company", type=int, default=None)
    args = ap.parse_args()

    src = Path(args.enriched_csv)
    if not src.exists():
        sys.exit(f"File not found: {src}")

    cfg = load_config(args.config)
    per_company = args.per_company if args.per_company is not None else int(cfg.get("contacts_per_event", 200))

    df = pd.read_csv(src)
    print(f"Loaded {len(df)} rows from {src.name}")

    # Fill missing expected columns with empty string
    for col in COL_MAP:
        if col not in df.columns:
            df[col] = ""

    # Keep contacts with any email (verified, guessed, or unverified); only drop truly blank
    df = df[df["Email"].notna() & (df["Email"].astype(str).str.strip() != "")]
    print(f"  after dropping blank emails: {len(df)} (guessed/unverified kept)")

    if df.empty:
        sys.exit("[STOP] No contacts with emails after Apollo enrichment.")

    # Exclude by title keywords
    title_pats = _patterns(cfg.get("exclude_title_keywords", []))
    if title_pats:
        before = len(df)
        df = df[~_matches_any(df["Title"], title_pats)]
        if before - len(df):
            print(f"  after title exclusions: {len(df)} (removed {before - len(df)})")

    # Deduplicate by email
    before = len(df)
    df = df.drop_duplicates(subset=["Email"], keep="first")
    print(f"  after de-duplicating emails: {len(df)} (removed {before - len(df)})")

    # Cap per event (using Event Name as the group key)
    if "Event Name" in df.columns and per_company > 0:
        before = len(df)
        df = df.groupby("Event Name", group_keys=False, sort=False).head(per_company)
        if before - len(df):
            print(f"  after capping to {per_company}/event: {len(df)} (removed {before - len(df)})")

    # Rename columns
    df = df[list(COL_MAP.keys())].rename(columns=COL_MAP)

    dest = src.parent / (src.stem + "_clean.csv")
    df.to_csv(dest, index=False)

    print(f"\nWrote {len(df)} clean contacts -> {dest.name}")
    print(f"  companies: {df['company_name'].nunique()}")
    print(f"  events:    {df['event_name'].nunique()}")


if __name__ == "__main__":
    main()
