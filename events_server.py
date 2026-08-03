#!/usr/bin/env python3
"""
Dashboard server for the events outbound pipeline.

    python3 events_server.py           # http://localhost:5556
    python3 events_server.py --port 8080
"""

import sys
import os
import json
import glob
import queue
import signal
import threading
import subprocess
import argparse
from pathlib import Path

try:
    from flask import Flask, jsonify, request, Response
    import yaml
except ImportError:
    sys.exit("Missing deps. Run:  pip install flask pyyaml")

# Auto-load .env if present
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

app = Flask(__name__)
BASE_DIR = Path(__file__).parent
PY = sys.executable

_proc = None
_q: queue.Queue = queue.Queue()
_lock = threading.Lock()


def _stream_proc(proc):
    for line in proc.stdout:
        _q.put(line)
    proc.wait()
    _q.put("[DONE]" if proc.returncode == 0 else "[ERROR]")


def _clear_queue():
    while not _q.empty():
        try:
            _q.get_nowait()
        except queue.Empty:
            break


def _launch(cmd):
    """Start a subprocess and begin streaming its output."""
    global _proc
    _clear_queue()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    _proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(BASE_DIR),
        start_new_session=True,
        env=env,
    )
    threading.Thread(target=_stream_proc, args=(_proc,), daemon=True).start()


@app.route("/")
def index():
    return (BASE_DIR / "events_dashboard.html").read_text()


@app.route("/api/config", methods=["GET"])
def get_config():
    cfg_path = request.args.get("file", "events_config.yaml")
    try:
        cfg = yaml.safe_load((BASE_DIR / cfg_path).read_text()) or {}
        return jsonify(cfg)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config", methods=["POST"])
def save_config():
    cfg_path = request.args.get("file", "events_config.yaml")
    try:
        cfg = request.get_json(force=True)
        with open(BASE_DIR / cfg_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def run_pipeline():
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            return jsonify({"error": "already running"}), 409

        opts = request.get_json(force=True) or {}
        cfg_file = opts.get("config_file", "events_config.yaml")

        cmd = [PY, "run_events_pipeline.py", cfg_file]
        if opts.get("no_prompt"):     cmd.append("--no-prompt")
        if opts.get("dry_run"):       cmd.append("--dry-run")
        if opts.get("auto_yes"):      cmd.append("--yes")
        if opts.get("skip_verify"):   cmd.append("--skip-verify")
        if opts.get("campaign_name"): cmd += ["--campaign-name", opts["campaign_name"]]
        sf = int(opts.get("start_from", 1))
        if sf > 1: cmd += ["--from", str(sf)]
        sa = int(opts.get("stop_after", 5))
        if sa < 5: cmd += ["--stop-after", str(sa)]

        _launch(cmd)

    return jsonify({"ok": True})


@app.route("/api/stream")
def stream():
    def generate():
        while True:
            try:
                item = _q.get(timeout=20)
            except queue.Empty:
                yield "data: [PING]\n\n"
                continue
            if item in ("[DONE]", "[ERROR]"):
                yield f"data: {item}\n\n"
                return
            yield f"data: {json.dumps(item)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/stop", methods=["POST"])
def stop_pipeline():
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            try:
                os.killpg(os.getpgid(_proc.pid), signal.SIGTERM)
            except Exception:
                _proc.terminate()
    return jsonify({"ok": True})


@app.route("/api/status")
def status():
    running = _proc is not None and _proc.poll() is None
    return jsonify({"running": running})


@app.route("/api/preview")
def preview():
    import csv as csv_mod
    pattern = str(BASE_DIR / "outputs" / "companies" / "*.csv")
    files = [
        f for f in __import__("glob").glob(pattern)
        if "_enriched" not in f and "_clean" not in f
    ]
    if not files:
        return jsonify({"rows": [], "total": 0, "file": None})
    latest = max(files, key=lambda f: Path(f).stat().st_mtime)
    rows = []
    with open(latest, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv_mod.DictReader(f)):
            if i >= 500:
                break
            rows.append(dict(row))
    return jsonify({"file": Path(latest).name, "rows": rows, "total": len(rows)})


@app.route("/api/configs")
def list_configs():
    files = sorted(BASE_DIR.glob("*_config.yaml"))
    return jsonify([f.name for f in files])


@app.route("/api/events", methods=["POST"])
def post_events():
    """Claude Code calls this to push a discovered event list into the dashboard."""
    data = request.get_json(force=True) or {}
    events = data.get("events", [])
    prompt = data.get("prompt", "")
    if not events:
        return jsonify({"error": "no events"}), 400

    disc_dir = BASE_DIR / "outputs" / "discovered"
    disc_dir.mkdir(parents=True, exist_ok=True)

    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    out = disc_dir / f"events_{ts}.json"
    mode = data.get("mode", "events")
    out.write_text(json.dumps({"prompt": prompt, "mode": mode, "discovered_at": _dt.datetime.now().isoformat(), "events": events}, indent=2))

    # Clear any previous selection
    sel = disc_dir / "selected.json"
    if sel.exists():
        sel.unlink()

    return jsonify({"ok": True, "count": len(events), "file": out.name})


@app.route("/api/discovered", methods=["GET"])
def get_discovered():
    files = (
        glob.glob(str(BASE_DIR / "outputs" / "discovered" / "events_*.json")) +
        glob.glob(str(BASE_DIR / "outputs" / "discovered" / "assoc_*.json"))
    )
    if not files:
        return jsonify({"events": [], "prompt": "", "file": None, "selected_indices": []})
    latest = max(files, key=lambda f: Path(f).stat().st_mtime)
    try:
        data = json.loads(Path(latest).read_text())
        data["file"] = Path(latest).name
        # Attach current selection if any
        sel_path = BASE_DIR / "outputs" / "discovered" / "selected.json"
        data["selected_indices"] = json.loads(sel_path.read_text()) if sel_path.exists() else []
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/select", methods=["POST"])
def select_events():
    """Claude Code calls this with the 1-based event numbers the user chose."""
    data = request.get_json(force=True) or {}
    indices = data.get("indices", [])  # 1-based
    zero_based = [i - 1 for i in indices if isinstance(i, int) and i >= 1]

    disc_dir = BASE_DIR / "outputs" / "discovered"
    disc_dir.mkdir(parents=True, exist_ok=True)
    (disc_dir / "selected.json").write_text(json.dumps(zero_based))
    return jsonify({"ok": True, "selected": zero_based})


@app.route("/api/crawl", methods=["POST"])
def crawl():
    """Start the crawl using currently selected events (or events POSTed in body)."""
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            return jsonify({"error": "already running"}), 409

        opts = request.get_json(force=True) or {}
        events = opts.get("events")

        if not events:
            # Load from selection file
            pattern = str(BASE_DIR / "outputs" / "discovered" / "events_*.json")
            files = glob.glob(pattern)
            if not files:
                return jsonify({"error": "no events file found"}), 400
            latest = max(files, key=lambda f: Path(f).stat().st_mtime)
            all_events = json.loads(Path(latest).read_text()).get("events", [])

            sel_path = BASE_DIR / "outputs" / "discovered" / "selected.json"
            if sel_path.exists():
                indices = json.loads(sel_path.read_text())
                events = [all_events[i] for i in indices if 0 <= i < len(all_events)]
            else:
                events = all_events

        if not events:
            return jsonify({"error": "no events selected"}), 400

        sel_dir = BASE_DIR / "outputs" / "discovered"
        sel_dir.mkdir(parents=True, exist_ok=True)
        sel_path = sel_dir / "selected.json"
        # Write selected event objects for crawl_event.py
        crawl_path = sel_dir / "crawl_input.json"
        crawl_path.write_text(json.dumps(events, indent=2))

        _launch([PY, "crawl_event.py", "--events-json", str(crawl_path)])
    return jsonify({"ok": True})


@app.route("/api/discover_associations", methods=["POST"])
def discover_associations_route():
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            return jsonify({"error": "already running"}), 409
        opts = request.get_json(force=True) or {}
        prompt = opts.get("prompt", "finance associations Hong Kong")
        _launch([PY, "discover_associations.py", prompt])
    return jsonify({"ok": True})


@app.route("/api/download_csv")
def download_csv():
    from flask import send_file
    pattern = str(BASE_DIR / "outputs" / "companies" / "*.csv")
    files = [
        f for f in glob.glob(pattern)
        if "_enriched" not in f and "_clean" not in f
    ]
    if not files:
        return jsonify({"error": "no CSV found"}), 404
    latest = max(files, key=lambda f: Path(f).stat().st_mtime)
    return send_file(latest, as_attachment=True, download_name=Path(latest).name)


@app.route("/api/discover", methods=["POST"])
def discover():
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            return jsonify({"error": "already running"}), 409
        opts = request.get_json(force=True) or {}
        prompt = opts.get("prompt", "finance conferences 2026")
        _launch([PY, "discover_events.py", prompt])
    return jsonify({"ok": True})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5556)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"\n  Events Pipeline Dashboard")
    print(f"  Open → http://{a.host}:{a.port}\n")
    app.run(host=a.host, port=a.port, debug=False, threaded=True)
