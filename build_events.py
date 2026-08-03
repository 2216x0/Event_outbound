#!/usr/bin/env python3
"""
Step 1 of the Events Pipeline: build_events.py

Scrapes speaker/agenda data from finance conferences and writes a speaker CSV
that pull_events_apollo.py reads for enrichment. No Apollo credits spent here.

Usage:
    python build_events.py                         # uses events_config.yaml
    python build_events.py events_config.yaml --no-prompt
"""

import sys
import re
import time
import datetime
import argparse
from pathlib import Path

import pandas as pd

try:
    import yaml, requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install pyyaml requests beautifulsoup4 pandas openpyxl lxml")


def p(msg: str):
    """Print with immediate flush so the dashboard sees it in real time."""
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Scraping strategies
# ---------------------------------------------------------------------------

def scrape_html_speakers(url: str, event_name: str) -> list[dict]:
    """
    Generic HTML scraper for conference speaker pages.

    Pattern A) Speaker cards: <div class="speaker-card"> etc.
    Pattern B) Agenda-style: speaker names inside session rows.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    }

    # ── Fetch ────────────────────────────────────────────────────────────────
    p(f"SCRAPE:FETCH:{event_name}:{url}")
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        p(f"SCRAPE:HTTP:{event_name}:{r.status_code}:{len(r.content)}")
    except Exception as e:
        p(f"SCRAPE:FAIL:{event_name}:{e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    speakers = []

    # ── Pattern A: speaker cards ─────────────────────────────────────────────
    CARD_SELECTORS = [
        "div.speaker-card", "div.speaker", "article.speaker",
        "div.speaker-item", "li.speaker", "div[class*='speaker']",
    ]
    p(f"SCRAPE:TRY_A:{event_name}")
    for sel in CARD_SELECTORS:
        p(f"SCRAPE:SEL:{event_name}:{sel}")
        cards = soup.select(sel)
        if not cards:
            continue

        p(f"SCRAPE:HIT_A:{event_name}:{len(cards)}:{sel}")
        for i, card in enumerate(cards):
            name_el = card.select_one("h2, h3, h4, .name, .speaker-name, strong")
            title_el = card.select_one(".title, .role, .position, .job-title, p")
            company_el = card.select_one(".company, .organization, .firm, .employer")
            linkedin_el = card.select_one("a[href*='linkedin.com/in/']")

            if not name_el:
                continue
            full_name = name_el.get_text(strip=True)
            parts = full_name.split(None, 1)
            first = parts[0] if parts else ""
            last = parts[1] if len(parts) > 1 else ""

            speakers.append({
                "speaker_first_name": first,
                "speaker_last_name": last,
                "speaker_title": title_el.get_text(strip=True) if title_el else "",
                "company_name": company_el.get_text(strip=True) if company_el else "",
                "speaker_linkedin": linkedin_el["href"] if linkedin_el else "",
                "talk_title": "",
            })

            # Live parse progress every 5 cards and on the last one
            if speakers and ((i + 1) % 5 == 0 or (i + 1) == len(cards)):
                p(f"SCRAPE:PARSE:{event_name}:{len(speakers)}:{len(cards)}")

        break  # found a working selector

    # ── Pattern B: agenda / session rows ─────────────────────────────────────
    if not speakers:
        p(f"SCRAPE:NOHIT:{event_name}")
        SESSION_SELECTORS = [
            "div.session", "article.session", "div.agenda-item",
            "tr.session", "div[class*='agenda']",
        ]
        p(f"SCRAPE:TRY_B:{event_name}")
        for sel in SESSION_SELECTORS:
            p(f"SCRAPE:SEL:{event_name}:{sel}")
            sessions = soup.select(sel)
            if not sessions:
                continue

            p(f"SCRAPE:HIT_B:{event_name}:{len(sessions)}:{sel}")
            for session in sessions:
                session_title = (
                    session.select_one("h3, h4, .session-title") or
                    session.select_one("strong")
                )
                speaker_els = session.select(
                    "a[href*='linkedin.com/in/'], .speaker-name, .panelist"
                )
                for sp_el in speaker_els:
                    linkedin_url = sp_el.get("href", "") if sp_el.name == "a" else ""
                    full_name = sp_el.get_text(strip=True)
                    parts = full_name.split(None, 1)
                    speakers.append({
                        "speaker_first_name": parts[0] if parts else "",
                        "speaker_last_name": parts[1] if len(parts) > 1 else "",
                        "speaker_title": "",
                        "company_name": "",
                        "speaker_linkedin": linkedin_url,
                        "talk_title": session_title.get_text(strip=True) if session_title else "",
                    })

            if speakers:
                p(f"SCRAPE:PARSE:{event_name}:{len(speakers)}:{len(speakers)}")
            break

    # ── Result ───────────────────────────────────────────────────────────────
    if not speakers:
        p(f"SCRAPE:ZERO:{event_name}")
    else:
        p(f"SCRAPE:DONE:{event_name}:{len(speakers)}")

    # Keep the original summary line (existing dashboard parser picks this up too)
    print(f"  {event_name}: scraped {len(speakers)} speakers from {url}", flush=True)
    return speakers


def load_manual_csv(path: str, event_name: str = "") -> list[dict]:
    """Load manually-curated speaker data from a CSV."""
    p(f"SCRAPE:CSV:{event_name}:{path}")
    try:
        df = pd.read_csv(path)
    except Exception as e:
        p(f"SCRAPE:FAIL:{event_name}:Could not read {path}: {e}")
        return []
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")
    col_map = {
        "company": "company_name", "first_name": "speaker_first_name",
        "last_name": "speaker_last_name", "title": "speaker_title",
        "linkedin_url": "speaker_linkedin",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    p(f"SCRAPE:DONE:{event_name}:{len(df)}")
    return df.to_dict("records")


def guess_website(company_name: str) -> str:
    name = re.sub(
        r"\b(LLC|LP|Inc\.?|Corp\.?|Group|Capital|Partners|Management|"
        r"Advisors?|Investments?|Fund|Asset|Holdings?)\b",
        "", company_name, flags=re.I,
    )
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    return f"https://www.{slug}.com" if slug else ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def make_run_slug(cfg: dict) -> str:
    prefix = cfg.get("output_prefix", "events")
    now = datetime.datetime.now()
    return f"{prefix}_{now.strftime('%Y-%m-%d_%H%M')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default="events_config.yaml")
    ap.add_argument("--no-prompt", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text()) or {}
    events = cfg.get("events", [])
    if not events:
        sys.exit("No events defined in config. Add at least one entry under 'events:'")

    all_speakers = []
    total = len(events)

    for idx, ev in enumerate(events, 1):
        name = ev.get("name", "Unknown Event")
        method = ev.get("scrape_method", "html")
        date = ev.get("event_date", "")
        location = ev.get("event_location", "")

        p(f"EVENT_START:{idx}:{total}:{name}")

        if method == "html":
            url = ev.get("speakers_url", "")
            if not url:
                p(f"SCRAPE:FAIL:{name}:No speakers_url configured")
                continue
            raw = scrape_html_speakers(url, name)
        elif method == "manual_csv":
            raw = load_manual_csv(ev.get("manual_csv_path", "manual_speakers.csv"), name)
        else:
            p(f"SCRAPE:FAIL:{name}:Unknown scrape_method '{method}'")
            continue

        for sp in raw:
            sp["event_name"] = name
            sp["event_date"] = date
            sp["event_location"] = location
            sp["talk_title"] = sp.get("talk_title", "")
            if not sp.get("website"):
                sp["website"] = guess_website(sp.get("company_name", ""))
            sp["city"] = ""
            sp["state"] = ""
        all_speakers.extend(raw)
        time.sleep(1.5)   # polite crawl delay

    if not all_speakers:
        sys.exit("No speakers scraped. Check config URLs and selectors.")

    df = pd.DataFrame(all_speakers)

    # Drop rows with no company name
    before_drop = len(df)
    df = df[df["company_name"].astype(str).str.strip().replace("", pd.NA).notna()]
    if len(df) < before_drop:
        p(f"FILTER:NO_COMPANY:{before_drop - len(df)}")

    # Title exclusions
    excl = [kw.lower().strip() for kw in cfg.get("exclude_title_keywords", [])]
    if excl:
        before_excl = len(df)
        mask = df["speaker_title"].str.lower().apply(
            lambda t: any(re.search(r"\b" + re.escape(kw) + r"\b", str(t)) for kw in excl)
        )
        df = df[~mask]
        removed = before_excl - len(df)
        if removed:
            p(f"FILTER:EXCL_TITLE:{removed}:{','.join(excl[:3])}")

    # Cap per event
    cap = int(cfg.get("contacts_per_event", 0))
    if cap > 0:
        before_cap = len(df)
        df = df.groupby("event_name", group_keys=False).head(cap)
        if len(df) < before_cap:
            p(f"FILTER:CAP:{before_cap - len(df)}:{cap}")

    # Deduplicate
    before_dedup = len(df)
    df = df.drop_duplicates(subset=["speaker_first_name", "speaker_last_name", "company_name"])
    if len(df) < before_dedup:
        p(f"FILTER:DEDUP:{before_dedup - len(df)}")

    slug = make_run_slug(cfg)
    out_dir = Path("outputs/companies")
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{slug}.csv"
    df.to_csv(dest, index=False)

    print(f"\nWrote {len(df)} speakers -> {dest}", flush=True)
    print(f"  events covered: {df['event_name'].nunique()}", flush=True)
    print(f"  companies: {df['company_name'].nunique()}", flush=True)
    print(f"\nNEXT: python3 pull_events_apollo.py {dest} --config events_config.yaml", flush=True)


if __name__ == "__main__":
    main()
