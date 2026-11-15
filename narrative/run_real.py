"""Run Sophia's pipeline on a real event, end to end, without the agents.

    python narrative/run_real.py                       # latest event that has comments, from Supabase (.env)
    python narrative/run_real.py --event-id 2
    python narrative/run_real.py --csv comments.csv    # a Supabase Table Editor export instead of the API

Writes workspace/data/{comments.jsonl, clusters.json, connectivity.json} and prints a summary.
Uses the embeddings already stored in Supabase (all-MiniLM-L6-v2), so no OpenAI key is needed.
With OPENAI_API_KEY set and --llm, it also scores stance + rewrites claims (then embeds claims).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scrapetools"))
csv.field_size_limit(10**9)

from narrative import sophia_tools as st  # noqa: E402

COLS = ["comment_id", "post_id", "subreddit", "author_hash", "body", "score", "depth", "n_replies",
        "created_utc", "parent_id", "parent_type", "parent_body", "parent_author_hash", "embedding"]


def client():
    import os
    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv(ROOT / ".env")
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])


def from_supabase(event_id):
    # own client (works on the Mac's default Python 3.9, where scrapetools/config.py can't import)
    import os
    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv(ROOT / ".env")
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    if event_id is None:
        ev = sb.table("event_posts").select("event_id").eq("comments_fetched", True) \
               .order("event_id", desc=True).limit(1).execute().data
        if not ev:
            sys.exit("no event has fetched comments yet")
        event_id = ev[0]["event_id"]
    pids = [r["post_id"] for r in sb.table("event_posts").select("post_id")
            .eq("event_id", event_id).eq("comments_fetched", True).execute().data]
    rows = []
    for i in range(0, len(pids), 20):
        start = 0
        while True:
            page = sb.table("comments").select(",".join(COLS)).in_("post_id", pids[i:i + 20]) \
                     .range(start, start + 999).execute().data
            rows += page
            if len(page) < 1000:
                break
            start += 1000
    return event_id, rows


def from_csv(path):
    with open(path, newline="") as f:
        rows = [{k: (v if v != "" else None) for k, v in r.items() if k in COLS} for r in csv.DictReader(f)]
    for r in rows:
        for k in ("score", "depth", "n_replies"):
            r[k] = int(float(r[k])) if r.get(k) not in (None, "") else None
    return None, rows


ap = argparse.ArgumentParser()
ap.add_argument("--event-id", type=int)
ap.add_argument("--csv")
ap.add_argument("--llm", action="store_true", help="score stance + claims with OpenAI (needs OPENAI_API_KEY)")
ap.add_argument("--question", default="Did Jensen Huang do something wrong?")
ap.add_argument("--pole0", default="condemns him")
ap.add_argument("--pole1", default="defends him")
ap.add_argument("--k", type=int, help="force k clusters instead of HDBSCAN")
ap.add_argument("--cleanup", action="store_true",
                help="after saving outputs to Supabase narrative_runs, DELETE this event's raw comments")
a = ap.parse_args()

event_id, rows = from_csv(a.csv) if a.csv else from_supabase(a.event_id)
path = st._resolve(st.DEFAULT_COMMENTS)
path.parent.mkdir(parents=True, exist_ok=True)
for f in path.parent.glob("clusters.*"):
    f.unlink()                                   # stale caches from another event
with path.open("w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
print(f"event {event_id}: {len(rows)} comments -> {path}")

print(json.dumps(st.community_summary(), indent=1))
print(json.dumps(st.score_stance(st.DEFAULT_COMMENTS, {"question": a.question, "0": a.pole0, "1": a.pole1},
                                 fast_mode=not a.llm), indent=1)[:1500])
out = st.cluster_narratives(k=a.k)
print(f"\nembeddings: {out['embed_backend']} | method: {out['method']} | "
      f"min_cluster_size: {out.get('min_cluster_size')} | clustered: {out['n_clustered']}")
for s_ in out["sweep"]:
    print("  sweep", s_)
for c in out["clusters"]:
    print(f"\n### cluster {c['cluster']}  size={c['size']}  weighted={c['weighted_size']}  "
          f"subs={c['subreddit_counts']}  localized={c['localized']}")
    print("  keywords:", ", ".join(c["keywords"][:8]))
    for t in c["top_comments"][:4]:
        print(f"  [{t['score']:>5}] {t['text']}")
    for t in c["most_contested"][:2]:
        print(f"  contested ({t['replies']} replies, {t['score']} pts): {t['text']}")
st.set_narratives([{"cluster": c["cluster"], "label": " / ".join(c["keywords"][:3]), "summary": "",
                    "merge_into": None} for c in out["clusters"]])
print("\n", json.dumps(st.build_connectivity(min_comments=20), indent=1))
print(f"\noutputs in {path.parent}")

if a.cleanup:
    if event_id is None:
        sys.exit("--cleanup needs a Supabase event (not --csv)")
    print("\ncleanup:", json.dumps(st.archive_and_cleanup(event_id, sb=client()), indent=1))
