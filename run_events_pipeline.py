#!/usr/bin/env python3
"""
Orchestration wrapper for the Events Pipeline.

Usage:
    python run_events_pipeline.py [events_config.yaml]
    python run_events_pipeline.py --dry-run          # skips paid steps
    python run_events_pipeline.py --from 2           # resume from step 2
"""

import sys
import glob
import argparse
import subprocess
from pathlib import Path

py = sys.executable


def find_script(name: str) -> str:
    """Find a script in current dir, falling back to ../outbound/ for shared scripts."""
    local = Path(name)
    if local.exists():
        return str(local)
    sibling = Path(__file__).parent.parent / "outbound" / name
    if sibling.exists():
        return str(sibling)
    sys.exit(f"[STOP] Cannot find {name}. Expected at ./{name} or ../outbound/{name}")


def latest_file(pattern: str, exclude_suffix: str = "") -> str | None:
    files = glob.glob(pattern)
    if exclude_suffix:
        files = [f for f in files if not f.endswith(exclude_suffix)]
    if not files:
        return None
    return max(files, key=lambda f: Path(f).stat().st_mtime)


def run_step(cmd: list[str], label: str, num: int):
    print(f"\nSTEP {num}/5: {label}", flush=True)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"PIPELINE STOPPED at step {num}", flush=True)
        sys.exit(f"[STOP] Step {num} failed (exit {result.returncode}).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default="events_config.yaml")
    ap.add_argument("--no-prompt", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--skip-verify", action="store_true")
    ap.add_argument("--from", dest="start_from", type=int, default=1, metavar="N")
    ap.add_argument("--stop-after", dest="stop_after", type=int, default=5, metavar="N")
    ap.add_argument("--campaign-name", default="")
    args = ap.parse_args()

    start = args.start_from

    # ── Step 1: Build Events ──────────────────────────────────────────────────
    companies_csv = None
    if start <= 1:
        before = set(glob.glob("outputs/companies/*.csv"))
        cmd = [py, find_script("build_events.py"), args.config, "--no-prompt"]
        run_step(cmd, "build_events.py", 1)
        after = set(glob.glob("outputs/companies/*.csv"))
        new_files = after - before
        companies_csv = (
            max(new_files, key=lambda f: Path(f).stat().st_mtime)
            if new_files else latest_file("outputs/companies/*.csv")
        )
    else:
        companies_csv = latest_file("outputs/companies/*.csv")

    if not companies_csv:
        sys.exit("[STOP] No speaker CSV found after Step 1.")
    print(f"  using: {companies_csv}", flush=True)

    if args.stop_after <= 1:
        print("\nPIPELINE PAUSED after step 1 — review results then continue from step 2", flush=True)
        print("PIPELINE COMPLETE", flush=True)
        return

    # ── Step 2: Pull Apollo ───────────────────────────────────────────────────
    enriched_csv = str(
        Path(companies_csv).parent / (Path(companies_csv).stem + "_enriched.csv")
    )
    if start <= 2:
        if args.dry_run:
            print(f"\nSTEP 2/5: pull_events_apollo.py [DRY RUN — skipped, no credits spent]",
                  flush=True)
        else:
            cmd = [py, find_script("pull_events_apollo.py"), companies_csv,
                   "--config", args.config]
            run_step(cmd, "pull_events_apollo.py", 2)

    if not args.dry_run and not Path(enriched_csv).exists():
        sys.exit(f"[STOP] Expected enriched CSV not found: {enriched_csv}")

    # ── Step 3: Clean CSV ─────────────────────────────────────────────────────
    cleaned_csv = str(
        Path(enriched_csv).parent / (Path(enriched_csv).stem + "_clean.csv")
    )
    if start <= 3:
        if args.dry_run:
            print(f"\nSTEP 3/5: clean_csv.py [DRY RUN — skipped]", flush=True)
        else:
            cmd = [py, find_script("clean_events_csv.py"), enriched_csv, "--config", args.config]
            run_step(cmd, "clean_events_csv.py", 3)

    # ── Step 4: Launch Instantly ──────────────────────────────────────────────
    if start <= 4:
        if args.dry_run:
            print(f"\nSTEP 4/5: launch_instantly.py [DRY RUN — skipped]", flush=True)
        else:
            cmd = [py, find_script("launch_instantly.py"), cleaned_csv,
                   "--config", args.config]
            if args.campaign_name:
                cmd += ["--name", args.campaign_name]
            run_step(cmd, "launch_instantly.py", 4)

    # ── Step 5: Verify ────────────────────────────────────────────────────────
    if start <= 5 and not args.skip_verify and not args.dry_run:
        cmd = [py, find_script("verify_instantly.py"), "--config", args.config]
        run_step(cmd, "verify_instantly.py", 5)

    if args.dry_run:
        print("\nPIPELINE COMPLETE (dry run — no credits or emails sent)", flush=True)
    else:
        print("\nPIPELINE COMPLETE", flush=True)


if __name__ == "__main__":
    main()
