---
name: discover-events
description: Discover finance/investment conferences, summits, and forums matching a set of event-specification preferences, then emit a structured JSON event list ready for the Event Outbound crawl pipeline. Use when asked to find/discover events, conferences, or institutional-investor gatherings for outreach.
---

# Discover Events

You are finding real-world finance, investment, and institutional-investor
**events** (conferences, summits, forums, symposia) that match a set of
preferences, and writing them out as structured JSON for the Event Outbound
pipeline to crawl for speakers.

## Input

You will be given a **research brief** assembled from `discovery_config.yaml`:
event types, sectors, regions, a date window, target audience, include/exclude
keywords, a target event count, and an output file path. Treat the brief as the
source of truth — do not invent constraints it does not state.

## Procedure

1. **Search exhaustively.** Run many `WebSearch` queries, varying angle:
   by sector × region ("private equity summit Europe 2027"), by known organizer
   (Markets Group, SuperReturn/Informa, IPEM, PEI, Opal, iiSearches), by
   audience ("LP GP conference"), and by month within the date window. One query
   is never enough — aim for broad coverage before you stop.
2. **Verify each candidate.** Prefer events with an official website. Use
   `WebFetch` on the event site to confirm the name, exact dates, city/venue,
   and that it actually falls inside the requested date window and sector. Drop
   anything you can't stand behind.
3. **Apply the filters.** Honor `regions`, `date_from`/`date_to`,
   `exclude_keywords`, and `require_website`. Drop virtual-only events if
   excluded. De-duplicate the same event found via different queries.
4. **Stop** when you have roughly `target_event_count` solid matches or have
   genuinely exhausted good candidates — say which.

## Output — this is the important part

Write a single JSON file to the output path given in the brief. **Do not** print
the array into chat as the deliverable; the file is the deliverable. Use exactly
this shape (same one the dashboard and `crawl_event.py` read):

```json
{
  "prompt": "<the research brief, one line>",
  "discovered_at": "<ISO 8601 timestamp>",
  "events": [
    {
      "name": "6th Private Equity Chicago Forum",
      "date": "July 2027",
      "location": "University Club of Chicago",
      "city": "Chicago, IL",
      "description": "1-2 sentence summary of the event and its focus.",
      "website_url": "https://www.marketsgroup.org/forums/private-equity-chicago-forum",
      "organizer": "Markets Group",
      "topics": ["Private Equity", "Fundraising", "Deal Sourcing"],
      "sector": "Private Equity",
      "audience": "Investors & Allocators"
    }
  ]
}
```

Field rules:
- `website_url` must be a full `https://...` official URL. If `require_website`
  is true, every event must have one.
- `date` is human-readable; keep it precise if the site gives exact days.
- `topics` is a short tag list; `sector` is the single best-fit sector from the
  brief; `audience` is who the event targets.

After writing the file, report a one-line summary: how many events, the output
path, and anything notable (e.g. thin coverage for a region, or where you had to
stop). Keep the chat response short — the JSON file carries the data.
