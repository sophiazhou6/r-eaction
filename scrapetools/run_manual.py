"""Run the tools without the deep agent.

Automatic (LLM writes the queries and picks subreddits from your prompt):
    python run_manual.py "jensen huang scandal" --auto --max-posts 5

Manual (you give queries/subreddits):
    python run_manual.py "jensen huang" --queries "Jensen Huang" "Nvidia" --subreddits nvidia stocks
    add --no-judge to skip the LLM relevance judge too
"""
import argparse
import json

import tools
from embed import embed_pending

ap = argparse.ArgumentParser()
ap.add_argument("event", help="the user's prompt, e.g. 'jensen huang scandal'")
ap.add_argument("--auto", action="store_true", help="let the LLM plan queries + subreddits")
ap.add_argument("--queries", nargs="+")
ap.add_argument("--subreddits", nargs="+", help="search these (default: big general subs)")
ap.add_argument("--time-filter", default="month")
ap.add_argument("--max-queries", type=int, default=4, help="--auto: use the first N planned queries")
ap.add_argument("--max-subs", type=int, default=6, help="--auto: use the first N planned subreddits")
ap.add_argument("--max-posts", type=int, default=5)
ap.add_argument("--max-comments", type=int, default=300)
ap.add_argument("--no-judge", action="store_true", help="treat every result as relevant")
ap.add_argument("--no-embed", action="store_true")
a = ap.parse_args()
if not a.auto and not a.queries:
    ap.error("give --auto or --queries")

eid = tools.start_event(a.event, description=a.event)
print(f"event_id = {eid}")

queries, subs, tf = a.queries, a.subreddits, a.time_filter
if a.auto:
    plan = json.loads(tools.plan_queries(a.event))
    print("plan:", json.dumps(plan, indent=1))
    # every query runs in every subreddit, so cap the grid (4 x 6 = 24 searches, ~2-3 min)
    queries = a.queries or plan["queries"][:a.max_queries]
    subs = a.subreddits or plan["subreddits"][:a.max_subs]
    tf = plan.get("time_filter") or tf

for q in queries:
    for sub in (subs or [None]):
        print(tools.search_reddit(q, limit=25, time_filter=tf, subreddit=sub).splitlines()[0])

if a.no_judge:
    s = tools._load()
    pool = {r["post_id"]: r for q in s["queries"].values() for r in q["results"]}
    ids = sorted(pool, key=lambda p: -(pool[p]["num_comments"] or 0))
else:
    report = tools.score_recall(k=10)
    print(report)
    ids = report.rsplit(": ", 1)[-1].split(", ") if "relevant post_ids" in report else []

print(tools.fetch_threads(ids[:a.max_posts], max_comments_per_post=a.max_comments))
if not a.no_embed:
    print(embed_pending(event_id=eid))
