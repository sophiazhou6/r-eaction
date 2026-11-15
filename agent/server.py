"""Live search: lets the website run the agents on any event.

    uv run python -m agent.server                                # local only, port 8787
    uv run python -m agent.server --public https://xyz.trycloudflare.com
        (after `cloudflared tunnel --url http://localhost:8787`; writes live.json to the
         public bucket so the deployed site finds this server automatically)

Quick mode by default: LIVE_POSTS threads x LIVE_COMMENTS comments (env, default 6 x 150).
No extra deps (stdlib http.server). Each job gets its own WORKSPACE so runs never collide.
"""
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "workspace_live"
POSTS = os.environ.get("LIVE_POSTS", "5")
COMMENTS = os.environ.get("LIVE_COMMENTS", "120")
MAX_RUNNING = int(os.environ.get("LIVE_MAX_JOBS", "2"))
JOBS: dict[str, dict] = {}

STEPS = [("search-strategist", "Searching Reddit for the best threads"),
         ("clustering-analyst", "Clustering comments into narratives"),
         ("narrative-analyst", "Naming narratives + mapping communities"),
         ("timeline-analyst", "Reconstructing how it evolved"),
         ("reporter", "Writing the briefing"),
         ("Final", "Publishing")]


def slugify(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:60]


def status(slug: str) -> dict:
    j = JOBS.get(slug)
    if not j:
        return {"state": "unknown", "slug": slug}
    log = j["log"].read_text(errors="ignore") if j["log"].exists() else ""
    step, n = "Starting agents", 0
    for i, (key, label) in enumerate(STEPS):
        if key in log:
            step, n = label, i + 1
    out = {"slug": slug, "query": j["q"], "step": step, "step_n": n, "steps": len(STEPS),
           "elapsed": int(time.time() - j["t"])}
    rc = j["p"].poll()
    if rc is None:
        out["state"] = "running"
    elif "Online:" in log and "skip " + slug not in log:
        out["state"] = "done"
    elif "=== Final ===" in log:  # agent stopped on purpose (usually: nothing found on Reddit)
        out["state"] = "failed"
        out["error"] = "Not enough recent Reddit discussion found for this. Try an event from the last few weeks."
    else:
        out["state"] = "failed"
        out["error"] = log.strip().splitlines()[-1][:200] if log.strip() else f"exit {rc}"
    return out


def start(q: str) -> dict:
    slug = slugify(q)
    if not slug:
        return {"state": "failed", "error": "empty query"}
    if slug in JOBS and JOBS[slug]["p"].poll() is None:
        return status(slug)
    if sum(j["p"].poll() is None for j in JOBS.values()) >= MAX_RUNNING:
        return {"state": "busy", "slug": slug, "error": "Agents are busy with another search, try again in a few minutes."}
    ws = LIVE / slug
    ws.mkdir(parents=True, exist_ok=True)
    log = ws / "run.log"
    env = {**os.environ, "WORKSPACE": str(ws), "MAX_POSTS": POSTS, "MAX_COMMENTS": COMMENTS,
           "PYTHONUNBUFFERED": "1", "LIVE_MODE": "1",
           "ARCTIC_DELAY": os.environ.get("ARCTIC_DELAY", "0.6"), "ARCTIC_WORKERS": os.environ.get("ARCTIC_WORKERS", "4")}
    p = subprocess.Popen([sys.executable, "-u", "-m", "agent.run", q, "--publish"], cwd=ROOT, env=env,
                         stdout=log.open("w"), stderr=subprocess.STDOUT)
    if os.environ.get("LIVE_BACKEND") == "json":  # Kent's no-key reddit.com JSON client: live, ~1s/request
        env.update({"REDDIT_BACKEND": "json", "REDDIT_DELAY": os.environ.get("REDDIT_DELAY", "2.5"),
                    "ARCTIC_WORKERS": "1"})  # one request at a time so Reddit doesn't rate-limit us
    JOBS[slug] = {"q": q, "p": p, "log": log, "t": time.time()}
    print(f"[live] started '{q}' -> {slug} ({POSTS} threads x {COMMENTS} comments)")
    return status(slug)


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/status":
            return self._send(200, status((parse_qs(u.query).get("slug") or [""])[0]))
        if u.path in ("/", "/api/health"):
            return self._send(200, {"ok": True, "jobs": len(JOBS)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/run":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except Exception:
            body = {}
        self._send(200, start(str(body.get("q", "")).strip()[:120]))

    def log_message(self, *a):
        pass


def set_public(url: str):
    sys.path.insert(0, str(ROOT / "scrapetools"))
    from agent.publish import _bucket, _put
    import config
    _put(_bucket(config.supabase()), "live.json", json.dumps({"api": url.rstrip("/"), "at": int(time.time())}).encode())
    print(f"[live] site will call {url} (live.json updated)")


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8787
    if "--public" in args:
        set_public(args[args.index("--public") + 1])
    print(f"[live] listening on http://localhost:{port}  ({POSTS} threads x {COMMENTS} comments per search, "
          f"source: {'reddit.com JSON' if os.environ.get('LIVE_BACKEND') == 'json' else 'Arctic Shift'})")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
