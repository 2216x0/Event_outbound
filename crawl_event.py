#!/usr/bin/env python3
"""
Phase 1c: Deep-crawl selected event websites and extract contacts.

Uses crawl4ai (headless browser) when available, falls back to requests + BeautifulSoup.
Extraction uses HTML structural analysis + JSON-LD + regex heuristics — no external API needed.

Usage:
    python3 crawl_event.py --events-json outputs/discovered/crawl_input.json
"""

import sys
import re
import json
import asyncio
import argparse
import datetime
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    sys.exit("pip install pandas")

try:
    import requests
    from bs4 import BeautifulSoup, Tag
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from crawl4ai import AsyncWebCrawler
    HAS_CRAWL4AI = True
except ImportError:
    HAS_CRAWL4AI = False


def p(msg: str):
    print(msg, flush=True)


SUB_PATHS = [
    ("/speakers",           "speaker"),
    ("/speaker",            "speaker"),
    ("/speakers-2026",      "speaker"),
    ("/speakers-2025",      "speaker"),
    ("/keynotes",           "speaker"),
    ("/agenda",             "speaker"),
    ("/program",            "speaker"),
    ("/schedule",           "speaker"),
    ("/panelists",          "speaker"),
    ("/faculty",            "speaker"),
    ("/sponsors",           "sponsor"),
    ("/sponsor",            "sponsor"),
    ("/exhibitors",         "sponsor"),
    ("/partners",           "sponsor"),
    ("/committee",          "committee"),
    ("/organizing-committee","committee"),
    ("/advisory-board",     "committee"),
    ("/board",              "committee"),
    ("/attendees",          "attendee"),
    ("/who-attends",        "attendee"),
    ("/attendee-profile",   "attendee"),
]

# CSS selectors to try — ordered from most to least specific
SPEAKER_SELECTORS = [
    '[class*="speaker"]',   '[class*="Speaker"]',
    '[class*="people"]',    '[class*="People"]',
    '[class*="person"]',    '[class*="Person"]',
    '[class*="panelist"]',  '[class*="Panelist"]',
    '[class*="faculty"]',   '[class*="presenter"]',
    '[class*="expert"]',    '[class*="profile"]',
    '[class*="committee"]', '[class*="advisory"]',
    '[class*="board-member"]',
    '[class*="sponsor-card"]', '[class*="exhibitor"]',
    '[class*="card"]',      '[class*="Card"]',
    '[class*="member"]',    '[class*="team"]',
]

TITLE_KEYWORDS = {
    "ceo", "cfo", "coo", "cto", "cio", "cmo", "cso", "cpo",
    "president", "vice president", "vp", "evp", "svp", "avp",
    "director", "managing director", "md", "executive director",
    "partner", "general partner", "managing partner", "senior partner",
    "founder", "co-founder", "co founder",
    "head", "chief", "principal",
    "manager", "senior manager", "associate",
    "analyst", "associate director",
    "chairman", "chairwoman", "board member", "trustee",
    "professor", "dr", "doctor",
}


# ── Fetchers ──────────────────────────────────────────────────────────────────

async def fetch_with_crawl4ai(url: str):
    """Returns (markdown, raw_html, success)."""
    try:
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)
            success = getattr(result, "success", True)
            if success:
                md   = getattr(result, "markdown", "") or ""
                html = getattr(result, "html", "") or ""
                return md, html, bool(md.strip())
        return "", "", False
    except Exception:
        return "", "", False


def fetch_with_requests(url: str):
    """Returns (text, raw_html, success)."""
    if not HAS_BS4:
        return "", "", False
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        raw_html = r.text
        soup = BeautifulSoup(raw_html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "iframe", "noscript"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True), raw_html, True
    except Exception:
        return "", "", False


async def fetch_page(url: str):
    """Returns (text_content, raw_html, success)."""
    if HAS_CRAWL4AI:
        content, html, ok = await fetch_with_crawl4ai(url)
        if ok:
            return content, html, True
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_with_requests, url)


# ── Name validation ───────────────────────────────────────────────────────────

_BAD_PHRASES = {
    "view more", "read more", "learn more", "see more", "click here",
    "sign up", "register now", "book now", "get tickets", "join now",
    "find out", "contact us", "our team", "meet our", "about us",
    "privacy policy", "terms of", "cookie policy", "all rights",
}

def _is_valid_name(name: str) -> bool:
    if not name:
        return False
    name = name.strip()
    words = name.split()
    if len(words) < 2 or len(words) > 6:
        return False
    if any(c.isdigit() for c in name):
        return False
    # Must start with a capital letter
    if not words[0][0].isupper():
        return False
    if name.lower() in _BAD_PHRASES:
        return False
    # Reject if any word is all-caps and > 4 chars (likely an acronym/heading)
    if any(w.isupper() and len(w) > 4 for w in words):
        return False
    # At least first word looks like a real name (not a number or symbol)
    if not re.match(r"^[A-Za-zÀ-ÿ''\-\.]+$", words[0]):
        return False
    return True


def _parse_title_company(lines: list) -> tuple:
    """Parse (title, company) from a list of text lines."""
    title, company = "", ""
    for raw in lines[:5]:
        line = re.sub(r"[\*_\[\]\(\)]+", "", raw).strip()
        if not line or len(line) < 3 or len(line) > 200:
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|", 1)]
            title = title or parts[0]
            company = company or (parts[1] if len(parts) > 1 else "")
        elif "," in line and not title:
            parts = line.split(",", 1)
            # heuristic: if first part looks like a title keyword, split that way
            first = parts[0].strip().lower()
            if any(kw in first for kw in TITLE_KEYWORDS):
                title, company = parts[0].strip(), parts[1].strip()
            else:
                # Could be "Company, Inc" — put whole thing as company
                company = company or line
        elif re.search(r"\s+at\s+", line, re.IGNORECASE) and not title:
            parts = re.split(r"\s+at\s+", line, 1, re.IGNORECASE)
            title = parts[0].strip()
            company = parts[1].strip() if len(parts) > 1 else ""
        elif not title and any(kw in line.lower() for kw in TITLE_KEYWORDS):
            title = line
        elif not company:
            company = line
    return title[:150], company[:150]


def _make_person(name, title, company, li_url, category, event_name, source_url):
    words = name.strip().split()
    return {
        "first_name":  words[0],
        "last_name":   " ".join(words[1:]),
        "title":       title,
        "company":     company,
        "linkedin_url": li_url,
        "category":    category or "speaker",
        "event_name":  event_name,
        "source_url":  source_url,
    }


# ── Extractor 1: JSON-LD (schema.org/Person) ─────────────────────────────────

def extract_jsonld(html: str, event_name: str, source_url: str, category_hint: str) -> list:
    if not html:
        return []
    people, seen = [], set()
    for script in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                              html, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(script)
        except Exception:
            continue
        # Normalise to list
        items = data if isinstance(data, list) else [data]
        for item in items:
            # Unwrap @graph
            if "@graph" in item:
                items.extend(item["@graph"] if isinstance(item["@graph"], list) else [item["@graph"]])
            t = item.get("@type", "")
            if "Person" not in (t if isinstance(t, list) else [t]):
                continue
            name = item.get("name", "").strip()
            if not _is_valid_name(name):
                continue
            title   = item.get("jobTitle", "") or ""
            org     = item.get("worksFor", {})
            company = (org.get("name", "") if isinstance(org, dict) else "") or ""
            li_url  = ""
            for sp in (item.get("sameAs") or []):
                if "linkedin.com/in/" in str(sp):
                    li_url = sp; break
            key = name.lower()
            if key not in seen:
                seen.add(key)
                people.append(_make_person(name, title, company, li_url, category_hint, event_name, source_url))
    return people


# ── Extractor 2: HTML structural (BeautifulSoup) ──────────────────────────────

def extract_html(html: str, event_name: str, source_url: str, category_hint: str) -> list:
    if not HAS_BS4 or not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    people, seen = [], set()

    # Try each selector; stop at first one that yields >= 3 plausible cards
    cards = []
    for sel in SPEAKER_SELECTORS:
        try:
            found = soup.select(sel)
            # Filter: not too long (containers), not too short (empty)
            found = [c for c in found
                     if isinstance(c, Tag) and 15 < len(c.get_text(strip=True)) < 800]
            if len(found) >= 3:
                cards = found
                break
        except Exception:
            continue

    for card in cards:
        # Name: prefer heading, then strong/b, then first non-empty text
        name_el = (card.find(["h1","h2","h3","h4","h5"])
                   or card.find("strong")
                   or card.find("b"))
        if not name_el:
            continue
        name = re.sub(r"\s+", " ", name_el.get_text()).strip()
        if not _is_valid_name(name):
            continue

        # Lines for title/company: text of card minus the name
        card_text = card.get_text(separator="\n")
        lines = [l.strip() for l in card_text.split("\n")
                 if l.strip() and l.strip() != name and len(l.strip()) < 200]
        title, company = _parse_title_company(lines)

        # LinkedIn
        li_url = ""
        li_a = card.find("a", href=re.compile(r"linkedin\.com/in/", re.I))
        if li_a:
            li_url = li_a.get("href", "")

        key = name.lower()
        if key not in seen:
            seen.add(key)
            people.append(_make_person(name, title, company, li_url, category_hint, event_name, source_url))

    return people


# ── Extractor 3: Regex on text/markdown ──────────────────────────────────────

def extract_regex(content: str, event_name: str, source_url: str, category_hint: str) -> list:
    if not content:
        return []
    people, seen = [], set()

    # Pattern A: **Bold Name** (markdown bold)
    bold_re = re.compile(
        r"\*\*([A-ZÀ-Ü][a-zà-ü\-\']+(?:\s+[A-ZÀ-Ü][a-zà-ü\-\']+)+)\*\*"
        r"[^\n]*\n((?:[^\n#*\[<]{5,150}\n?){0,4})",
        re.MULTILINE
    )
    # Pattern B: ## / ### Heading Name
    heading_re = re.compile(
        r"^#{1,4}\s+([A-ZÀ-Ü][a-zà-ü\-\']+(?:\s+[A-ZÀ-Ü][a-zà-ü\-\']+)+)\s*\n"
        r"((?:[^\n#*\[<]{5,150}\n?){0,4})",
        re.MULTILINE
    )
    # Pattern C: "Name, Title, Company" or "Name | Title | Company" on one line
    inline_re = re.compile(
        r"([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Ü][a-zà-ü]+)+)"
        r"\s*[,|]\s*"
        r"([A-Za-z][^,|\n]{3,80})"
        r"(?:\s*[,|]\s*([A-Za-z][^,|\n]{2,80}))?"
    )

    def add(name, detail_text="", title="", company=""):
        if not _is_valid_name(name):
            return
        if not title and not company:
            lines = [l.strip() for l in detail_text.split("\n") if l.strip()]
            title, company = _parse_title_company(lines)
        key = name.lower()
        if key not in seen:
            seen.add(key)
            people.append(_make_person(name, title, company, "", category_hint, event_name, source_url))

    for m in bold_re.finditer(content):
        add(m.group(1).strip(), m.group(2) or "")
    for m in heading_re.finditer(content):
        add(m.group(1).strip(), m.group(2) or "")
    # Inline only if bold/heading found nothing (avoid too many false positives)
    if not people:
        for m in inline_re.finditer(content):
            name = m.group(1).strip()
            t = (m.group(2) or "").strip()
            c = (m.group(3) or "").strip()
            # Validate: title segment should contain a known keyword
            if any(kw in t.lower() for kw in TITLE_KEYWORDS):
                add(name, title=t, company=c)

    return people


# ── Extractor 4: Table rows ───────────────────────────────────────────────────

def extract_tables(html: str, event_name: str, source_url: str, category_hint: str) -> list:
    """Extract people from HTML <table> rows."""
    if not HAS_BS4 or not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    people, seen = [], set()

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        # Try to find name/title/company columns from header
        headers = []
        header_row = rows[0]
        for th in header_row.find_all(["th", "td"]):
            headers.append(th.get_text(strip=True).lower())

        name_col = next((i for i, h in enumerate(headers) if "name" in h), None)
        title_col = next((i for i, h in enumerate(headers) if "title" in h or "role" in h), None)
        company_col = next((i for i, h in enumerate(headers) if "company" in h or "org" in h or "firm" in h), None)

        if name_col is None:
            continue

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= name_col:
                continue
            name = cells[name_col].get_text(strip=True)
            if not _is_valid_name(name):
                continue
            title   = cells[title_col].get_text(strip=True) if title_col and len(cells) > title_col else ""
            company = cells[company_col].get_text(strip=True) if company_col and len(cells) > company_col else ""
            key = name.lower()
            if key not in seen:
                seen.add(key)
                people.append(_make_person(name, title, company, "", category_hint, event_name, source_url))

    return people


# ── Master extraction ─────────────────────────────────────────────────────────

def extract_people(content: str, html: str, event_name: str, source_url: str, category_hint: str) -> list:
    """Try all extractors in order; return first that yields results."""
    for fn in [extract_jsonld, extract_tables, extract_html, extract_regex]:
        try:
            arg = html if fn in (extract_jsonld, extract_tables, extract_html) else content
            result = fn(arg, event_name, source_url, category_hint)
            if result:
                return result
        except Exception:
            continue
    return []


# ── Crawl one event ───────────────────────────────────────────────────────────

async def crawl_one_event(event: dict) -> list:
    name     = event.get("name", "Unknown Event")
    base_url = (event.get("website_url") or "").rstrip("/")
    date     = event.get("date", "")
    location = event.get("location", "")

    if not base_url:
        p(f"CRAWL:SKIP:{name}:no URL")
        return []

    p(f"CRAWL:START:{name}:{base_url}")

    all_people = []
    seen_urls  = set()
    urls_to_try = [(base_url, "")] + [(base_url + path, cat) for path, cat in SUB_PATHS]

    for url, category in urls_to_try:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            p(f"CRAWL:FETCH:{name}:{url}")
            content, html, ok = await fetch_page(url)
            if not ok or not content.strip():
                continue
            p(f"CRAWL:EXTRACT:{name}:{url}")
            loop = asyncio.get_event_loop()
            people = await loop.run_in_executor(
                None, extract_people, content, html, name, url, category
            )
            if people:
                p(f"CRAWL:FOUND:{name}:{len(people)}:{url}")
                for person in people:
                    person["event_date"]     = date
                    person["event_location"] = location
                all_people.extend(people)
        except Exception as e:
            p(f"CRAWL:ERROR:{name}:{url}:{type(e).__name__}: {e}")

    # Global dedup by (first, last, company)
    seen, unique = set(), []
    for person in all_people:
        key = (
            person.get("first_name", "").lower().strip(),
            person.get("last_name",  "").lower().strip(),
            person.get("company",    "").lower().strip(),
        )
        if key not in seen and any(key):
            seen.add(key)
            unique.append(person)

    p(f"CRAWL:DONE:{name}:{len(unique)}")
    return unique


# ── Main ──────────────────────────────────────────────────────────────────────

async def main_async(events: list, out_dir: Path):
    p(f"CRAWL:TOTAL:{len(events)}")
    all_people = []

    for i, event in enumerate(events, 1):
        p(f"EVENT_START:{i}:{len(events)}:{event.get('name','Unknown')}")
        people = await crawl_one_event(event)
        all_people.extend(people)

    if not all_people:
        sys.exit("[STOP] No contacts found from any event.")

    rows = []
    for person in all_people:
        rows.append({
            "speaker_first_name": person.get("first_name", ""),
            "speaker_last_name":  person.get("last_name",  ""),
            "speaker_title":      person.get("title",      ""),
            "company_name":       person.get("company",    ""),
            "speaker_linkedin":   person.get("linkedin_url",""),
            "talk_title":         "",
            "event_name":         person.get("event_name", ""),
            "event_date":         person.get("event_date", ""),
            "event_location":     person.get("event_location", ""),
            "website":            "",
            "city":               "",
            "state":              "",
            "category":           person.get("category",  "speaker"),
            "source_url":         person.get("source_url", ""),
        })

    df = pd.DataFrame(rows)
    df = df[
        df["speaker_first_name"].astype(str).str.strip().ne("")
        | df["speaker_last_name"].astype(str).str.strip().ne("")
    ]

    now      = datetime.datetime.now()
    out_file = out_dir / f"events_{now.strftime('%Y-%m-%d_%H%M')}.csv"
    df.to_csv(out_file, index=False)

    print(f"\nWrote {len(df)} contacts -> {out_file}", flush=True)
    print(f"  events covered: {df['event_name'].nunique()}", flush=True)
    print(f"  companies: {df['company_name'].nunique()}", flush=True)
    print(f"\nNEXT: python3 pull_events_apollo.py {out_file} --config events_config.yaml", flush=True)
    print("CRAWL COMPLETE", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events-json", required=True,
                    help="Path to JSON file or inline JSON of selected events")
    ap.add_argument("--output-dir", default="outputs/companies")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    src = Path(args.events_json)
    if src.exists():
        events = json.loads(src.read_text())
    else:
        try:
            events = json.loads(args.events_json)
        except json.JSONDecodeError as e:
            sys.exit(f"[STOP] Could not parse events JSON: {e}")

    if not events:
        sys.exit("[STOP] No events to crawl.")

    asyncio.run(main_async(events, out_dir))


if __name__ == "__main__":
    main()
