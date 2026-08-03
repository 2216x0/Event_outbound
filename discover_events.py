#!/usr/bin/env python3
"""
Phase 1a: Event Discovery using Claude API + web search.

Usage:
    python3 discover_events.py "finance conferences August 2026 US"
    python3 discover_events.py "PE summits Europe 2026" --output-dir outputs/discovered
"""

import sys
import json
import argparse
import datetime
from pathlib import Path

try:
    import anthropic
except ImportError:
    sys.exit("pip install anthropic")


def p(msg: str):
    print(msg, flush=True)


def discover_events(prompt: str) -> list:
    client = anthropic.Anthropic()

    p(f"DISCOVER:START:{prompt}")

    system = (
        "You are a research assistant that finds finance, investment, and professional industry "
        "conferences, summits, forums, and events worldwide. Search exhaustively using multiple "
        "queries. Your final response must be ONLY a valid JSON array — no prose before or after."
    )

    user_msg = f"""Search the web comprehensively to find ALL events matching:

"{prompt}"

Search multiple times with varied queries to be exhaustive. Include conferences, summits, forums, symposia, workshops, and seminars.

For each event return a JSON object with these exact fields:
  "name"        — full official event name
  "date"        — date range (e.g. "August 12-14, 2026")
  "location"    — "City, Country"
  "website_url" — official website URL (full https://... URL)
  "description" — 1-2 sentence summary
  "categories"  — list of relevant tags (e.g. ["private equity", "venture capital"])

Return ONLY the JSON array. No intro text, no markdown fences, no explanation after the array."""

    full_text = ""
    current_block_type = None
    current_block_json = ""

    try:
        with client.messages.stream(
            model="claude-opus-4-8",
            max_tokens=8000,
            thinking={"type": "adaptive"},
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        ) as stream:
            for event in stream:
                etype = getattr(event, "type", None)

                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    current_block_type = getattr(block, "type", None) if block else None
                    current_block_json = ""

                elif etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if not delta:
                        continue
                    dtype = getattr(delta, "type", None)

                    if current_block_type == "server_tool_use" and dtype == "input_json_delta":
                        current_block_json += getattr(delta, "partial_json", "")

                    elif dtype == "text_delta":
                        full_text += getattr(delta, "text", "")

                elif etype == "content_block_stop":
                    if current_block_type == "server_tool_use" and current_block_json:
                        try:
                            inp = json.loads(current_block_json)
                            query = inp.get("query", "")
                            if query:
                                p(f"DISCOVER:SEARCH:{query}")
                        except Exception:
                            pass
                    current_block_type = None
                    current_block_json = ""

            final = stream.get_final_message()

        text_content = ""
        for block in final.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                text_content += getattr(block, "text", "")

        if not text_content.strip():
            text_content = full_text

    except Exception as e:
        p(f"DISCOVER:ERROR:{e}")
        return []

    text_content = text_content.strip()
    start = text_content.find("[")
    end = text_content.rfind("]")

    if start < 0 or end <= start:
        p("DISCOVER:PARSE_ERROR:No JSON array found in response")
        if text_content:
            p(f"DISCOVER:RAW_SNIPPET:{text_content[:300]}")
        return []

    try:
        events = json.loads(text_content[start:end + 1])
        if not isinstance(events, list):
            p("DISCOVER:PARSE_ERROR:Response is not a JSON array")
            return []
    except json.JSONDecodeError as e:
        p(f"DISCOVER:PARSE_ERROR:{e}")
        return []

    p(f"DISCOVER:FOUND:{len(events)}")
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default="finance conferences 2026")
    ap.add_argument("--output-dir", default="outputs/discovered")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    events = discover_events(args.prompt)

    if not events:
        sys.exit("[STOP] No events discovered. Check ANTHROPIC_API_KEY and prompt.")

    now = datetime.datetime.now()
    out_file = out_dir / f"events_{now.strftime('%Y-%m-%d_%H%M')}.json"

    with open(out_file, "w") as f:
        json.dump({"prompt": args.prompt, "discovered_at": now.isoformat(), "events": events}, f, indent=2)

    print(f"\nDiscovered {len(events)} events -> {out_file}", flush=True)
    print("DISCOVERY COMPLETE", flush=True)


if __name__ == "__main__":
    main()
