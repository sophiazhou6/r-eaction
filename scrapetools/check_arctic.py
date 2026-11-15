"""Quick check that the Reddit archive works and parses. Paste the output to Claude if it fails.

    python check_arctic.py
    python check_arctic.py --sub nvidia --q "Jensen Huang"
"""
import argparse
import json

from reddit_client import ArcticShiftClient

ap = argparse.ArgumentParser()
ap.add_argument("--sub", default="technology")
ap.add_argument("--q", default="Nvidia")
a = ap.parse_args()

c = ArcticShiftClient()
raw = c._get("/api/posts/search", {"subreddit": a.sub, "title": a.q, "limit": 3, "sort": "desc"})
print("RAW search (first item, truncated):")
print(json.dumps(raw[0] if raw else raw, indent=1)[:1200])

posts = c.search(a.q, time_filter="year", limit=5, subreddit=a.sub)
print(f"\nparsed {len(posts)} posts:")
for p in posts:
    print(" ", p["id"], p["num_comments"], p["title"][:80])

if posts:
    pid = posts[0]["id"]
    tree = c._get("/api/comments/tree", {"link_id": f"t3_{pid}", "limit": 5})
    print(f"\nRAW comment tree for {pid} (truncated):")
    print(json.dumps(tree, indent=1)[:1500])
    post, comments = c.thread(pid, limit=100)
    print(f"\nparsed {len(comments)} comments; first 3:")
    for cm in comments[:3]:
        print(" ", cm["depth"], cm["parent_id"], (cm["body"] or "")[:80])
