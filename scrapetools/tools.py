"""Kent's tools for the search-strategist subagent.

Hand these functions straight to deepagents (their docstrings become the tool
descriptions the LLM sees):
    from tools import plan_queries, search_many, search_reddit, score_recall, fetch_threads

Before invoking the agent, call start_event(user_query) once in Python. It creates
the event row in Supabase and resets the /data files for this run.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (COMMENTS_FILE, DATA_DIR, QUERIES_FILE, chunks, hash_user, supabase)
from reddit_client import get_client

SKIP_AUTHORS = {"AutoModerator"}
SKIP_BODIES = {"", "[deleted]", "[removed]"}
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "openai:gpt-4.1-mini")
WORKERS = int(os.environ.get("ARCTIC_WORKERS", "3"))

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
  "queries": ["5-8 short title-style queries, 1-4 words each, BROADEST FIRST"],
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

_post_cache: dict[str, dict] = {}     # raw posts seen in searches, so fetch_threads can skip re-fetching


def _post_row(p: dict) -> dict:
    return {"post_id": p["id"], "subreddit": p["subreddit"],
            "author_hash": hash_user(p.get("author")), "title": p.get("title") or "",
            "selftext": p.get("selftext") or "", "url": p.get("url"),
            "permalink": "https://www.reddit.com" + (p.get("permalink") or ""),
            "score": p.get("score"), "upvote_ratio": p.get("upvote_ratio"),
            "num_comments": p.get("num_comments"), "created_utc": p.get("created_utc")}


def _record_search(s: dict, label: str, time_filter: str, posts: list[dict]) -> list[dict]:
    """Write one search's ranked results to Supabase and the run state. Main thread only."""
    for p in posts:
        _post_cache[p["id"]] = p
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
    return rows


def search_reddit(query: str, limit: int = 100, time_filter: str = "month",
                  subreddit: str | None = None) -> str:
    """Search Reddit posts for one query in one subreddit and record the ranked results.
    Prefer search_many to run several queries x subreddits at once.

    Search matches post TITLES, so use short queries made of words a headline would
    contain (e.g. "Jensen Huang", "Nvidia CEO"), not full sentences.
    time_filter: hour, day, week, month, year or all (default month).
    subreddit: name without "r/". Strongly recommended: pick communities likely to
    discuss the event (e.g. "nvidia", "stocks"). Without it, a fixed list of big
    general subreddits (news, worldnews, politics, technology, ...) is searched.
    Returns the top 30 results, one per line: post_id | r/subreddit | comments | title.
    """
    s = _load()
    label = f"{query} [r/{subreddit}]" if subreddit else query
    try:
        posts = _rd().search(query, time_filter=time_filter, limit=limit, subreddit=subreddit)
    except Exception as e:
        return f"search failed: {e}"
    rows = _record_search(s, label, time_filter, posts)
    _save(s)
    if not rows:
        return f"'{label}': no results"
    lines = [f"{r['post_id']} | r/{r['subreddit']} | {r['num_comments']}c | {r['title'][:120]}"
             for r in rows[:30]]
    return f"'{label}': {len(rows)} results (top 30 shown)\n" + "\n".join(lines)


def search_many(queries: list[str], subreddits: list[str], limit: int = 100,
                time_filter: str = "month") -> str:
    """Run every query in every subreddit, in parallel, and record all ranked results.

    Use 2-4 broad title-style queries (e.g. ["Jensen Huang", "Nvidia"]) and 5-10
    subreddits. Broad is fine: score_recall filters out off-topic posts afterwards.
    Returns a count per search plus the total number of unique posts found.
    """
    s = _load()
    grid = [(q, sub) for q in queries for sub in subreddits]
    lines, found = [], set()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_rd().search, q, time_filter, limit, sub): (q, sub) for q, sub in grid}
        for f in as_completed(futs):
            q, sub = futs[f]
            label = f"{q} [r/{sub}]"
            try:
                rows = _record_search(s, label, time_filter, f.result())
            except Exception as e:
                lines.append(f"'{label}': failed ({str(e)[:80]})")
                continue
            found |= {r["post_id"] for r in rows}
            lines.append(f"'{label}': {len(rows)} results")
    _save(s)
    return "\n".join(sorted(lines)) + f"\n{len(found)} unique posts found. Next: score_recall."


# ---------- tool 2: recall@k ----------

def _judge_batch(client, model: str, description: str, batch: list[dict]) -> set[str]:
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
    return set(map(str, ids)) & {p["post_id"] for p in batch}


def _judge(description: str, posts: list[dict]) -> set[str]:
    """LLM decides which posts are about the event. Batches of 40 titles, 6 in parallel.
    Uses the plain openai package, so these tools don't need LangChain installed."""
    from openai import OpenAI
    client = OpenAI()
    model = JUDGE_MODEL.split(":", 1)[-1]          # accepts "openai:gpt-4.1-mini" or "gpt-4.1-mini"
    relevant = set()
    with ThreadPoolExecutor(max_workers=6) as ex:
        for ids in ex.map(lambda b: _judge_batch(client, model, description, b),
                          list(chunks(posts, 40))):
            relevant |= ids
    return relevant


def score_recall(k: int = 10) -> str:
    """Judge which searched posts are about the event, then score every query so far.

    recall@k = relevant posts in the query's top k / all relevant posts found by any query.
    precision@k = relevant posts in the top k / k.
    Low recall means the query misses coverage: write new, different queries and search again.
    Posts already judged in earlier calls are not re-judged.
    Also returns the relevant post_ids to pass to fetch_threads (balanced across subreddits).
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
    lines.sort(reverse=True)
    subs = {pool[p]["subreddit"] for p in relevant}
    ranked = balanced_order([pool[p] for p in relevant])
    shown = lines[:15] + ([(0, f"... {len(lines) - 15} more queries")] if len(lines) > 15 else [])
    return (f"{len(relevant)} relevant posts out of {len(pool)} found, across {len(subs)} "
            f"subreddits: {', '.join(sorted(subs))}\n"
            + "\n".join(l for _, l in shown)
            + f"\nrelevant post_ids (balanced across subreddits, biggest first): {', '.join(ranked)}")


def balanced_order(posts: list[dict]) -> list[str]:
    """Order post ids round-robin across subreddits, biggest thread first in each, so
    taking the first N gives a spread of communities instead of one big subreddit."""
    by_sub: dict[str, list[dict]] = {}
    for p in sorted(posts, key=lambda p: -(p.get("num_comments") or 0)):
        by_sub.setdefault(p["subreddit"], []).append(p)
    queues = sorted(by_sub.values(), key=lambda q: -(q[0].get("num_comments") or 0))
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0)["post_id"])
    return out


# ---------- tool 3: fetch threads ----------

def _comment_rows(post: dict, raw: list[dict]) -> list[dict]:
    """Clean comments and attach what each one is replying to (order-independent)."""
    pid = post["id"]
    post_context = (post.get("title") or "")
    if post.get("selftext"):
        post_context += "\n" + post["selftext"][:500]
    everyone = {c["id"]: ((c.get("body") or "").strip(), hash_user(c.get("author"))) for c in raw}
    rows = []
    for c in raw:
        body, ah = everyone[c["id"]]
        if c.get("author") in SKIP_AUTHORS or body in SKIP_BODIES:
            continue
        parent = c.get("parent_id") or ""
        if parent.startswith("t1_"):
            par_id = parent[3:]
            par_body, par_author = everyone.get(par_id, ("[not loaded]", None))
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


def _download(pid: str, limit: int):
    return _rd().thread(pid, limit=limit, post=_post_cache.get(pid))


def fetch_threads(post_ids: list[str], max_comments_per_post: int = 5000) -> str:
    """Download every comment and reply (with scores) for these posts, several at once.

    Aim for 25-50 posts spread across subreddits (score_recall lists them in a balanced
    order, so take the first N). Each reply is saved with the text it replies to.
    Saves to Supabase and appends to /data/comments.jsonl. Returns counts.
    """
    s = _load()
    sb, report, total = supabase(), [], 0
    per_sub: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_download, pid, max_comments_per_post): pid for pid in post_ids}
        for f in as_completed(futs):                 # DB writes stay on this thread
            pid = futs[f]
            try:
                post, raw = f.result()
            except Exception as e:
                report.append(f"{pid}: failed ({str(e)[:80]})")
                continue
            rows = _comment_rows(post, raw)
            if pid not in _post_cache:
                sb.table("posts").upsert(_post_row(post), on_conflict="post_id").execute()
            for c in chunks(rows):
                sb.table("comments").upsert(c, on_conflict="comment_id").execute()
            sb.table("event_posts").upsert(
                {"event_id": s["event_id"], "post_id": pid, "comments_fetched": True},
                on_conflict="event_id,post_id").execute()
            with COMMENTS_FILE.open("a") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            total += len(rows)
            per_sub[post["subreddit"]] = per_sub.get(post["subreddit"], 0) + len(rows)
            print(f"  {pid}: {len(rows)} comments from r/{post['subreddit']} (total {total})",
                  flush=True)
    by_sub = ", ".join(f"r/{k} {v}" for k, v in sorted(per_sub.items(), key=lambda x: -x[1]))
    fails = [r for r in report if "failed" in r]
    return (f"saved {total} comments from {len(post_ids) - len(fails)} threads\n"
            f"by subreddit: {by_sub}" + ("\n" + "\n".join(fails) if fails else ""))
