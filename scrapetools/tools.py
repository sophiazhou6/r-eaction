"""Kent's tools for the search-strategist subagent.

Hand these three functions straight to deepagents (their docstrings become the tool
descriptions the LLM sees):
    from tools import search_reddit, score_recall, fetch_threads

Before invoking the agent, call start_event(user_query) once in Python. It creates
the event row in Supabase and resets the /data files for this run.
"""
import json
import os
import time

from config import (COMMENTS_FILE, DATA_DIR, QUERIES_FILE, chunks, hash_user, supabase)
from reddit_client import get_client

SKIP_AUTHORS = {"AutoModerator"}
SKIP_BODIES = {"", "[deleted]", "[removed]"}
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "openai:gpt-4.1-mini")

_reddit = None


def _rd():
    global _reddit
    if _reddit is None:
        _reddit = get_client()
    return _reddit


# ---------- run state (/data/queries.json) ----------

def _load() -> dict:
    return json.loads(QUERIES_FILE.read_text())


def _save(state: dict):
    QUERIES_FILE.write_text(json.dumps(state, indent=1))


def start_event(user_query: str, description: str | None = None) -> int:
    """Not an agent tool. Call once per run before agent.invoke()."""
    row = supabase().table("events").insert(
        {"user_query": user_query, "description": description}).execute().data[0]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _save({"event_id": row["event_id"], "user_query": user_query,
           "description": description, "queries": {}, "judgments": {}})
    COMMENTS_FILE.write_text("")
    return row["event_id"]


def set_event_description(description: str) -> str:
    """Save a precise, neutral description of the event (who, what, roughly when).
    score_recall uses it to judge which posts are relevant."""
    s = _load()
    s["description"] = description
    _save(s)
    supabase().table("events").update({"description": description}).eq(
        "event_id", s["event_id"]).execute()
    return "saved"


# ---------- tool 0: prompt -> searchable plan ----------

PLAN_PROMPT = """The user wants to study Reddit discussion of a current event.
User's words: "{prompt}"
Today's date: {today}

Search only matches words in post TITLES, inside one subreddit at a time. Return JSON:
{{
  "description": "precise, neutral one-sentence description: who, what, roughly when",
  "queries": ["5-8 short title-style queries, 1-4 words each"],
  "subreddits": ["6-10 subreddit names without r/, most likely to discuss it"],
  "time_filter": "week, month or year: how far back coverage likely goes"
}}
Query rules: use the names and nouns a headline would contain (people, companies,
places, official names, abbreviations). Mix broad ("Jensen Huang") and specific
("Nvidia CEO probe"). Mostly neutral wording: avoid opinion words like "scandal" or
"disaster", which only match one side's posts.
Subreddit rules: mix big general ones (news, worldnews, politics, technology) with
topic-specific ones (e.g. nvidia, stocks, soccer). Only real, active subreddits."""


def plan_queries(prompt: str) -> str:
    """Turn the user's words into a search plan: a neutral event description,
    title-style search queries, subreddits to search, and a time window.
    Also saves the description for score_recall. Call this first."""
    from datetime import date
    from openai import OpenAI
    resp = OpenAI().chat.completions.create(
        model=JUDGE_MODEL.split(":", 1)[-1], temperature=0.3,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": PLAN_PROMPT.format(
            prompt=prompt, today=date.today().isoformat())}])
    plan = json.loads(resp.choices[0].message.content)
    plan["subreddits"] = [s.removeprefix("r/").strip() for s in plan.get("subreddits", [])]
    s = _load()
    s["plan"] = plan
    _save(s)
    if plan.get("description"):
        set_event_description(plan["description"])
    return json.dumps(plan, indent=1)


# ---------- tool 1: search ----------

def _post_row(p: dict) -> dict:
    return {"post_id": p["id"], "subreddit": p["subreddit"],
            "author_hash": hash_user(p.get("author")), "title": p.get("title") or "",
            "selftext": p.get("selftext") or "", "url": p.get("url"),
            "permalink": "https://www.reddit.com" + (p.get("permalink") or ""),
            "score": p.get("score"), "upvote_ratio": p.get("upvote_ratio"),
            "num_comments": p.get("num_comments"), "created_utc": p.get("created_utc")}


def search_reddit(query: str, limit: int = 50, time_filter: str = "month",
                  subreddit: str | None = None) -> str:
    """Search Reddit posts for one query and record the ranked results.

    Search matches post TITLES, so use short queries made of words a headline would
    contain (e.g. "Jensen Huang", "Nvidia CEO"), not full sentences.
    time_filter: hour, day, week, month, year or all (default month).
    subreddit: name without "r/". Strongly recommended: pick communities likely to
    discuss the event (e.g. "nvidia", "stocks"). Without it, a fixed list of big
    general subreddits (news, worldnews, politics, technology, ...) is searched.
    Returns one line per post: post_id | r/subreddit | comments | title.
    """
    s = _load()
    label = f"{query} [r/{subreddit}]" if subreddit else query
    try:
        posts = _rd().search(query, time_filter=time_filter, limit=limit, subreddit=subreddit)
    except Exception as e:
        return f"search failed: {e}"

    rows = [_post_row(p) for p in posts]
    sb, eid = supabase(), s["event_id"]
    q = sb.table("search_queries").upsert(
        {"event_id": eid, "query_text": label, "time_filter": time_filter,
         "n_results": len(rows)}, on_conflict="event_id,query_text").execute().data[0]
    if rows:
        sb.table("posts").upsert(rows, on_conflict="post_id").execute()
        sb.table("query_hits").upsert(
            [{"query_id": q["query_id"], "post_id": r["post_id"], "rank": i}
             for i, r in enumerate(rows, 1)], on_conflict="query_id,post_id").execute()
        sb.table("event_posts").upsert(
            [{"event_id": eid, "post_id": r["post_id"]} for r in rows],
            on_conflict="event_id,post_id", ignore_duplicates=True).execute()

    s["queries"][label] = {"time_filter": time_filter, "results": [
        {"post_id": r["post_id"], "subreddit": r["subreddit"], "title": r["title"],
         "num_comments": r["num_comments"]} for r in rows]}
    _save(s)
    if not rows:
        return f"'{label}': no results"
    lines = [f"{r['post_id']} | r/{r['subreddit']} | {r['num_comments']}c | {r['title'][:120]}"
             for r in rows]
    return f"'{label}': {len(rows)} results\n" + "\n".join(lines)


# ---------- tool 2: recall@k ----------

def _judge(description: str, posts: list[dict]) -> set[str]:
    """LLM decides which posts are about the event. Returns the relevant ids.
    Uses the plain openai package, so these tools don't need LangChain installed."""
    from openai import OpenAI
    client = OpenAI()
    model = JUDGE_MODEL.split(":", 1)[-1]          # accepts "openai:gpt-4.1-mini" or "gpt-4.1-mini"
    relevant = set()
    for batch in chunks(posts, 40):
        listing = "\n".join(f"{p['post_id']} | r/{p['subreddit']} | {p['title']}" for p in batch)
        resp = client.chat.completions.create(
            model=model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content":
                f"Event: {description}\n\nWhich of these Reddit posts are about THIS specific "
                f"event (not an older related story, not a joke that only shares a keyword)?\n"
                f'Answer as JSON: {{"relevant_ids": ["id1", "id2"]}}\n\n{listing}'}])
        try:
            ids = json.loads(resp.choices[0].message.content).get("relevant_ids", [])
        except (json.JSONDecodeError, AttributeError):
            ids = []
        relevant |= set(map(str, ids)) & {p["post_id"] for p in batch}
    return relevant


def score_recall(k: int = 10) -> str:
    """Judge which searched posts are about the event, then score every query so far.

    recall@k = relevant posts in the query's top k / all relevant posts found by any query.
    precision@k = relevant posts in the top k / k.
    Low recall means the query misses coverage: write new, different queries and search again.
    Posts already judged in earlier calls are not re-judged.
    Also returns the relevant post_ids to pass to fetch_threads.
    """
    s = _load()
    desc = s.get("description") or s["user_query"]
    pool = {r["post_id"]: r for q in s["queries"].values() for r in q["results"]}
    new = [p for pid, p in pool.items() if pid not in s["judgments"]]
    if new:
        rel = _judge(desc, new)
        for p in new:
            s["judgments"][p["post_id"]] = p["post_id"] in rel
        _save(s)
        yes = [p["post_id"] for p in new if p["post_id"] in rel]
        no = [p["post_id"] for p in new if p["post_id"] not in rel]
        for ids, val in ((yes, True), (no, False)):
            for c in chunks(ids, 200):
                supabase().table("event_posts").update({"agent_relevant": val}).eq(
                    "event_id", s["event_id"]).in_("post_id", c).execute()

    relevant = {pid for pid, v in s["judgments"].items() if v and pid in pool}
    if not relevant:
        return "No relevant posts yet. Try different queries (entity names, headline wording)."

    lines = []
    for label, q in s["queries"].items():
        top = [r["post_id"] for r in q["results"][:k]]
        hits = sum(pid in relevant for pid in top)
        lines.append((hits / len(relevant), f"recall@{k}={hits / len(relevant):.2f}  "
                      f"precision@{k}={hits / max(len(top), 1):.2f}  {label}"))
    subs = {pool[p]["subreddit"] for p in relevant}
    ranked = sorted(relevant, key=lambda p: -(pool[p]["num_comments"] or 0))
    return (f"{len(relevant)} relevant posts out of {len(pool)} found, across {len(subs)} "
            f"subreddits: {', '.join(sorted(subs))}\n"
            + "\n".join(l for _, l in sorted(lines, reverse=True))
            + f"\nrelevant post_ids (most comments first): {', '.join(ranked)}")


# ---------- tool 3: fetch threads ----------

def _comment_rows(post: dict, raw: list[dict]) -> list[dict]:
    """Clean comments and attach what each one is replying to."""
    pid = post["id"]
    post_context = (post.get("title") or "")
    if post.get("selftext"):
        post_context += "\n" + post["selftext"][:500]
    seen = {}   # id -> (body, author_hash) for every comment, kept or not
    rows = []
    for c in raw:
        body = (c.get("body") or "").strip()
        ah = hash_user(c.get("author"))
        seen[c["id"]] = (body, ah)
        if c.get("author") in SKIP_AUTHORS or body in SKIP_BODIES:
            continue
        parent = c.get("parent_id") or ""
        if parent.startswith("t1_"):
            par_id = parent[3:]
            par_body, par_author = seen.get(par_id, ("[not loaded]", None))
            ptype = "comment"
        else:
            par_id, par_body, par_author, ptype = None, post_context, hash_user(post.get("author")), "post"
        rows.append({"comment_id": c["id"], "post_id": pid, "subreddit": post["subreddit"],
                     "author_hash": ah, "body": body, "score": c.get("score"),
                     "depth": c.get("depth"), "n_replies": c.get("n_replies"),
                     "created_utc": c.get("created_utc"), "parent_id": par_id,
                     "parent_type": ptype, "parent_body": par_body,
                     "parent_author_hash": par_author})
    return rows


def fetch_threads(post_ids: list[str], max_comments_per_post: int = 300) -> str:
    """Download every comment and reply (with scores) for these posts.

    Each reply is saved together with the text it replies to. Saves to Supabase and
    appends to /data/comments.jsonl. Returns a per-post count.
    """
    s = _load()
    sb, report, total = supabase(), [], 0
    for pid in post_ids:
        try:
            post, raw = _rd().thread(pid, limit=max_comments_per_post)
        except Exception as e:
            report.append(f"{pid}: failed ({e})")
            continue
        rows = _comment_rows(post, raw)
        sb.table("posts").upsert(_post_row(post), on_conflict="post_id").execute()
        for c in chunks(rows):
            sb.table("comments").upsert(c, on_conflict="comment_id").execute()
        sb.table("event_posts").upsert(
            {"event_id": s["event_id"], "post_id": pid, "comments_fetched": True},
            on_conflict="event_id,post_id").execute()
        with COMMENTS_FILE.open("a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        total += len(rows)
        report.append(f"{pid}: {len(rows)} comments from r/{post['subreddit']}")
    return "\n".join(report) + f"\ntotal: {total} comments saved"
