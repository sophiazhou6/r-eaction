# Kent's tools: Reddit search → recall@k → threads → embeddings (Supabase)

## One-time setup
1. Supabase dashboard: **restore the project** if it says paused / not active.
2. SQL Editor → paste `schema.sql` → Run. Creates the tables, pgvector, the `reply_edges`
   view, the `set_comment_embeddings` function, and read-only access for the frontend.
3. `cp .env.example .env` and fill it in (the secret key only ever lives in `.env`).
4. `pip install -r requirements.txt` (the first `embed.py` run downloads a ~90 MB model).

## Test without the agent first
```bash
python run_manual.py "jensen huang" --queries "Jensen Huang" "Nvidia CEO" --max-posts 3 --no-judge
python run_manual.py "jensen huang" --queries "Jensen Huang" "Nvidia CEO" --max-posts 5   # with LLM judge
python run_manual.py "jensen huang scandal" --auto --max-posts 5   # LLM plans queries + subreddits
```

## For Poshitha (orchestrator)
```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
import config, tools
from embed import embed_pending

search_strategist = {
    "name": "search-strategist",
    "description": "Turns the event into Reddit queries, scores them by recall@k, "
                   "loops until coverage is good, then fetches the threads.",
    "system_prompt": "...",   # loop: search_reddit x5-8 -> score_recall -> if low recall, new queries -> fetch_threads
    "tools": [tools.plan_queries, tools.search_reddit,
              tools.score_recall, tools.fetch_threads],
}

agent = create_deep_agent(
    model="openai:gpt-4.1-mini",
    system_prompt="...",
    subagents=[search_strategist, ...],
    backend=FilesystemBackend(root_dir=config.WORKSPACE, virtual_mode=True),  # agents see /data/*
)

event_id = tools.start_event(user_query)      # call once per run, before invoke
agent.invoke({"messages": [{"role": "user", "content": user_query}]})
embed_pending(event_id=event_id)              # or give it to an agent as a tool
```

## What each tool does
| tool | writes | returns to the agent |
|---|---|---|
| `start_event(q)` (Python, not a tool) | `events` row, resets `/data/` | event_id |
| `plan_queries(prompt)` | `events.description`, plan in `/data/queries.json` | description, title-style queries, subreddits, time window |
| `set_event_description(d)` | `events.description` | "saved" |
| `search_reddit(query, limit, time_filter, subreddit)` | `posts`, `search_queries`, `query_hits`, `event_posts`, `/data/queries.json` | one line per post |
| `score_recall(k)` | `event_posts.agent_relevant` | recall@k + precision@k per query, relevant post_ids |
| `fetch_threads(post_ids)` | `comments` (with `parent_body`), `/data/comments.jsonl` | comment count per post |
| `embed.py` / `embed_pending()` | `comments.embedding` (384-dim) | count |

recall@k uses pooled relevance: the relevant set is every post the LLM judged relevant across
all queries so far. A new query can lower everyone else's recall by finding posts they missed.

## Reading the data (Sophia / frontend)
```python
sb = config.supabase()
rows = sb.table("comments").select("comment_id,subreddit,body,parent_body,score,embedding") \
         .in_("post_id", post_ids).execute().data          # embedding comes back as a string: json.loads it
edges = sb.table("reply_edges").select("*").execute().data  # who replied to whom
```
The frontend should use the **publishable** key (read-only via the policies in schema.sql).
