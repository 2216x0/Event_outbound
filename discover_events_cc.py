#!/usr/bin/env python3
"""
Phase 1a (automated): Event discovery driven by Claude Code.

Instead of a single Messages-API call (see discover_events.py), this runner
turns discovery_config.yaml into a research brief and hands it to Claude Code in
headless mode (`claude -p`). Claude Code uses the `discover-events` skill to
search the web agentically and write the event JSON itself.

Usage:
    python3 discover_events_cc.py
    python3 discover_events_cc.py --config discovery_config.yaml
    python3 discover_events_cc.py --prompt "extra freeform focus to add"
    python3 discover_events_cc.py --dry-run      # print the brief + command, don't call Claude Code
"""

import sys
import json
import shutil
import argparse
import datetime
import subprocess
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pip install pyyaml")

BASE_DIR = Path(__file__).resolve().parent


def p(msg: str):
    print(msg, flush=True)


def _fmt_list(items):
    return ", ".join(str(i) for i in items) if items else "(none specified)"


def build_brief(cfg: dict, out_file: Path, extra: str = "") -> str:
    cc = cfg.get("claude_code", {})
    date_from = cfg.get("date_from") or "any"
    date_to = cfg.get("date_to") or "open-ended"

    lines = [
        "Invoke the `discover-events` skill and follow it exactly for this task.",
        "",
        "Discover real finance / investment / institutional-investor events.",
        "",
        f"Event types: {_fmt_list(cfg.get('event_types'))}",
        f"Sectors / themes: {_fmt_list(cfg.get('sectors'))}",
        f"Regions: {_fmt_list(cfg.get('regions'))}",
        f"Date window: {date_from} to {date_to} (events must take place in this window)",
        f"Target audience: {_fmt_list(cfg.get('target_audience'))}",
        f"Bias toward keywords: {_fmt_list(cfg.get('include_keywords'))}",
        f"Exclude events matching: {_fmt_list(cfg.get('exclude_keywords'))}",
        f"Require an official website URL: {bool(cfg.get('require_website', True))}",
        f"Aim for about {cfg.get('target_event_count', 40)} high-quality, de-duplicated matches.",
        "",
        f"Write the results as JSON to this exact path: {out_file}",
        "Use the `discover-events` skill for the output shape and procedure.",
    ]
    if extra:
        lines += ["", f"Additional focus from the operator: {extra}"]
    return "\n".join(lines)


def run(cfg: dict, brief: str, out_file: Path) -> None:
    cc = cfg.get("claude_code", {})
    binary = cc.get("binary", "claude")
    resolved = shutil.which(binary)
    if not resolved:
        sys.exit(f"[STOP] Claude Code CLI '{binary}' not found on PATH. Install it or set claude_code.binary.")

    cmd = [
        resolved, "-p", brief,
        "--output-format", "json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", "WebSearch,WebFetch,Write,Read,Skill(discover-events)",
        "--max-turns", str(cc.get("max_turns", 40)),
    ]
    if cc.get("model"):
        cmd += ["--model", cc["model"]]

    p(f"DISCOVER:START:{out_file.name}")
    p(f"DISCOVER:CMD:{' '.join(cmd[:2])} ... (--max-turns {cc.get('max_turns', 40)})")

    try:
        proc = subprocess.run(
            cmd, cwd=str(BASE_DIR),
            capture_output=True, text=True,
            timeout=cc.get("timeout_seconds", 900),
        )
    except subprocess.TimeoutExpired:
        sys.exit("[STOP] Claude Code timed out. Raise claude_code.timeout_seconds or narrow the brief.")

    if proc.returncode != 0:
        p(proc.stdout[-2000:])
        p(proc.stderr[-2000:])
        sys.exit(f"[STOP] Claude Code exited {proc.returncode}.")

    # The -p --output-format json wrapper prints a session summary; the real
    # deliverable is the file the agent wrote. Surface the agent's final text.
    try:
        summary = json.loads(proc.stdout)
        if isinstance(summary, dict) and summary.get("result"):
            p(f"DISCOVER:AGENT:{summary['result'][:500]}")
    except json.JSONDecodeError:
        p(proc.stdout[-1000:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="discovery_config.yaml")
    ap.add_argument("--prompt", default="", help="extra freeform focus appended to the brief")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg_path = BASE_DIR / args.config
    if not cfg_path.exists():
        sys.exit(f"[STOP] Config not found: {cfg_path}")
    cfg = yaml.safe_load(cfg_path.read_text()) or {}

    out_dir = BASE_DIR / cfg.get("output_dir", "outputs/discovered")
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now()
    out_file = out_dir / f"events_{now.strftime('%Y-%m-%d_%H%M')}.json"

    brief = build_brief(cfg, out_file, args.prompt.strip())

    if args.dry_run:
        p("=== BRIEF ===")
        p(brief)
        p("\n=== (dry run — Claude Code not invoked) ===")
        return

    run(cfg, brief, out_file)

    if out_file.exists():
        try:
            data = json.loads(out_file.read_text())
            n = len(data.get("events", [])) if isinstance(data, dict) else 0
            p(f"\nDiscovered {n} events -> {out_file}")
            p("DISCOVERY COMPLETE")
        except json.JSONDecodeError:
            p(f"[WARN] {out_file} exists but is not valid JSON — check the agent output.")
    else:
        p(f"[WARN] Claude Code finished but {out_file} was not written. Check the agent transcript above.")


if __name__ == "__main__":
    main()
