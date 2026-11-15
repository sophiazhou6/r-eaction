"""Publish cached runs to Supabase Storage so the UI can load them instantly.

No SQL / dashboard needed: uses the secret key from .env to create a public bucket
and upload JSON files. Usernames in the data are already hashed (Kent's hash_user).

    uv run python -m agent.publish                       # publish every event in workspace/cache/
    uv run python -m agent.publish jensen-huang-scandal  # publish one

The UI reads (no key needed, bucket is public):
    {SUPABASE_URL}/storage/v1/object/public/narrative-runs/index.json
    {SUPABASE_URL}/storage/v1/object/public/narrative-runs/<slug>/clusters.json
    {SUPABASE_URL}/storage/v1/object/public/narrative-runs/<slug>/connectivity.json
    {SUPABASE_URL}/storage/v1/object/public/narrative-runs/<slug>/briefing.json
    {SUPABASE_URL}/storage/v1/object/public/narrative-runs/<slug>/timeline.json
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scrapetools"))
import config  # noqa: E402

BUCKET = "narrative-runs"
FILES = ("briefing.json", "timeline.json", "clusters.json", "connectivity.json", "stance_axis.json",
         "subreddit_aliases.json", "queries.json", "meta.json")


def _bucket(sb):
    names = {getattr(b, "name", None) or getattr(b, "id", None) for b in sb.storage.list_buckets()}
    if BUCKET not in names:
        sb.storage.create_bucket(BUCKET, options={"public": True})
    return sb.storage.from_(BUCKET)


def _put(store, path: str, data: bytes):
    store.upload(path, data, file_options={"content-type": "application/json",
                                           "cache-control": "60", "upsert": "true"})


def publish(slugs: list[str] | None = None) -> str:
    sb = config.supabase()
    store = _bucket(sb)
    cache = config.WORKSPACE / "cache"
    slugs = slugs or sorted(p.name for p in cache.iterdir() if p.is_dir())

    try:  # keep events published earlier (possibly from another laptop)
        index = {e["slug"]: e for e in json.loads(store.download("index.json"))}
    except Exception:
        index = {}

    for slug in slugs:
        d = cache / slug
        if not (d / "clusters.json").exists():
            print(f"skip {slug}: no clusters.json")
            continue
        for name in FILES:
            if (d / name).exists():
                _put(store, f"{slug}/{name}", (d / name).read_bytes())
        meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        brief = json.loads((d / "briefing.json").read_text()) if (d / "briefing.json").exists() else {}
        n_comments = len(json.loads((d / "clusters.json").read_text()).get("comments", []))
        index[slug] = {"slug": slug, "event": meta.get("event") or brief.get("event") or slug,
                       "event_id": meta.get("event_id"), "headline": brief.get("headline"),
                       "n_comments": n_comments, "published_at": int(time.time())}
        print(f"published {slug} ({n_comments} comments)")

    _put(store, "index.json", json.dumps(sorted(index.values(), key=lambda e: -e["published_at"]),
                                         indent=1).encode())
    base = f"{config.SUPABASE_URL}/storage/v1/object/public/{BUCKET}"
    print(f"\nIndex: {base}/index.json")
    return base


if __name__ == "__main__":
    publish([a for a in sys.argv[1:] if not a.startswith("--")] or None)
