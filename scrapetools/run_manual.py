"""Run the tools without the deep agent.

Automatic (LLM writes the queries and picks subreddits from your prompt):
    python run_manual.py "jensen huang scandal" --auto

Manual (you give queries/subreddits):
    python run_manual.py "jensen huang" --queries "Jensen Huang" "Nvidia" --subreddits nvidia stocks news
    add --no-judge to skip the LLM relevance judge too

Defaults: 3 broad queries x 8 subreddits (100 posts each, in parallel), then the
10 most relevant threads balanced across subreddits, up to 5000 comments each.
"""
import argparse
import json
import time

import tools
from embed import embed_pending
from reddit_client import ArcticShiftClient

ap = argparse.ArgumentParser()
ap.add_argument("event", help="the user's prompt, e.g. 'jensen huang scandal'")
ap.add_argument("--auto", action="store_true", help="let the LLM plan queries + subreddits")
ap.add_argument("--queries", nargs="+")
ap.add_argument("--subreddits", nargs="+", help="search these (default: big general subs)")
ap.add_argument("--time-filter", default="month")
ap.add_argument("--max-queries", type=int, default=3, help="--auto: use the first N planned queries")
ap.add_argument("--max-subs", type=int, default=8, help="--auto: use the first N planned subreddits")
ap.add_argument("--per-search", type=int, default=100, help="posts per search (archive max 100)")
ap.add_argument("--max-posts", type=int, default=10, help="threads to download")
ap.add_argument("--max-comments", type=int, default=5000, help="comments per thread")
ap.add_argument("--no-judge", action="store_true", help="treat every result as relevant")
ap.add_argument("--no-embed", action="store_true")
a = ap.parse_args()
if not a.auto and not a.queries:
    ap.error("give --auto or --queries")

t0 = time.time()
eid = tools.start_event(a.event, description=a.event)
print(f"event_id = {eid}")

queries, subs, tf = a.queries, a.subreddits, a.time_filter
if a.auto:
    plan = json.loads(tools.plan_queries(a.event))
    print("plan:", json.dumps(plan, indent=1))
    queries = a.queries or plan["queries"][:a.max_queries]
    subs = a.subreddits or plan["subreddits"][:a.max_subs]
    tf = plan.get("time_filter") or tf

subs = subs or ArcticShiftClient.DEFAULT_SUBS
print(f"\nsearching {len(queries)} queries x {len(subs)} subreddits ...")
print(tools.search_many(queries, subs, limit=a.per_search, time_filter=tf))
print(f"[{time.time() - t0:.0f}s]")

if a.no_judge:
    s = tools._load()
    pool = {r["post_id"]: r for q in s["queries"].values() for r in q["results"]}
    ids = tools.balanced_order(list(pool.values()))
else:
    report = tools.score_recall(k=10)
    print(report)
    ids = report.rsplit(": ", 1)[-1].split(", ") if "relevant post_ids" in report else []
print(f"[{time.time() - t0:.0f}s]")

print(f"\ndownloading {min(len(ids), a.max_posts)} threads ...")
print(tools.fetch_threads(ids[:a.max_posts], max_comments_per_post=a.max_comments))
print(f"[{time.time() - t0:.0f}s]")
if not a.no_embed:
    print(embed_pending(event_id=eid))
print(f"done in {time.time() - t0:.0f}s")
