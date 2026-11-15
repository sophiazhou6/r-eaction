# HackPrinceton F26: Reddit Narrative Agent

Submission due **Sun Nov 15, 8am ET**. Team of 4.

## The idea
Online, *what* people say and *who* says it are two halves of one story. A user types any news event in their own words ("jensen huang scandal", "anthropic IPO"). A deep agent investigates how Reddit talks about it and answers:
- **Which communities** are discussing it, and how much
- **Where each community stands** on the question that actually divides people for *this* event
- **What narratives exist**, and which ones **cross community lines** (the same story showing up in r/nvidia, r/stocks and r/technology)
- **What the median story is**: what most people think happened

The interesting output is the *combination*: a narrative alone is a topic model, a community alone is a subreddit list. A narrative spreading across communities, or two communities telling opposite stories about the same facts, is the insight.

## Tracks we are targeting (judges care about these)
**Arlequin Track 2: Social Media Analysis (content + connectivity)**
- Must use BOTH layers (semantic content + connectivity between actors/communities) and show insight from combining them
- Pipeline must be sound; **recall@k is explicitly a plus** if we use retrieval
- Insight should be non-obvious
- Responsible compute (scales, efficient) and responsible data (public only, privacy)
- Clear, explorable visualization/dashboard

**LangChain: Best use of deepagents**
- Is the use case complex?
- Did we use Deep Agents effectively (subagents, filesystem, planning)?
- Demo quality
- The agent must visibly *decide and adapt* (loops), not just run a fixed pipeline. Show write_todos live and LangSmith traces.

## Team
- **Poshitha**: the deep agent (agent/), prompts, wiring, Supabase push
- **Kent**: data collection tools (tools/kent_tools.py)
- **Sophia**: analysis tools (tools/sophia_tools.py)
- 4th teammate: UI / visualization

## Full pipeline (original team plan)
1. **Query**: user types any event. Agent generates search terms/keywords, grades them by **recall@k**, refines. (Extensions: suggest searches from latest news in the UI; cache popular searches with a graph view, Flaneur-style.)
2. **Data** (Reddit):
   - All comments relevant to the event: opinions, reports, etc.
   - **Replies too**, with parent text attached so meaning comes through in context
   - **Upvotes/scores** so we can weight how much a comment matters
   - Subreddit each comment was made in
   - Usernames (and maybe which other communities they post in, for connectivity)
   - Extension: X/Twitter or temporal patterns if Reddit misses part of the narrative range
3. **Community step (first)**: group by subreddit, keep top-k communities, drop noise (meme subs, tiny subs), flag under-sampled important ones
4. **Sentiment/stance step (second)**: 0-1 score on an axis the agent picks per event, plus an emotion tag
5. **Narrative step (third)**: pool ALL kept comments (+ parent context), embed (OpenAI embeddings), unsupervised clustering, LLM names each cluster, then regroup by community for the visualization
6. **Connectivity**: narrative x community matrix, narratives crossing communities, bridge users posting in multiple communities, where a narrative started
7. **Visualization**: graph of communities and narratives, stance per community, cached searches
8. **Validation (improvement)**: compare against ground truth like Pew Research or consensus of trusted news coverage

## Decisions made (and why)
- **Subreddit step first, but cluster narratives ONCE on all comments pooled.** If you cluster per subreddit, "narrative 1" in r/nvidia and r/stocks are unrelated and you can't show a narrative spreading. Subreddit is a label, not a partition.
- **Stance axis is chosen per event by the agent**, not a fixed happy/sad scale. Happy/sad is meaningless for most events (nobody is "sad" about an IPO). Examples: scandal -> condemns vs defends; IPO -> bearish vs bullish; FAA closure -> blames vs trusts government; FIFA election -> rigged vs legitimate. If scores cluster at 0.5 everywhere, the axis was the wrong question: pick a new one. Tradeoff accepted: scores are not comparable across events.
- **Emotion is a separate tag** (angry / frustrated / mocking / worried / excited / neutral), not a point on the 0-1 line. Same LLM call as stance.
- **Upvotes are weights, not part of the embedding.** Putting them into the embedding distorts meaning. Use them after: Sophia uses upvote percentile within each thread (removes subreddit-size bias), ~1 vote per author per thread, max 20% of a cell from one thread.
- **Python tools do the heavy work; agents make judgment calls.** Clustering and scoring are deterministic code. The LLM never reads thousands of comments: tools write files and return small summaries.
- **Clustering-analyst (Sophia) makes the judgment calls the old community-scout plan had:** picks the stance axis per event and decides whether clustering is usable. Orchestrator decides whether to loop back for more data.
- **Embed claims, not raw comments** (Sophia): an LLM rewrites each comment into a neutral claim so clusters group by position, not topic. Parent text only interprets replies; agree-only replies add weight to the parent instead of counting as new voices.
- **Kent chose one agent for search** (search + scrape together).
- **Supabase** for the UI side (final briefing + cached searches). Agents talk to each other through files.
- **Dev on Haiku, demo on Sonnet** (cost: about $0.15 per run on Haiku, about $1 on Sonnet).

## Architecture (INTEGRATED: real code from Kent + Sophia)
```
Orchestrator (write_todos, delegates with task)            agent/agent.py, agent/prompts.py
├── search-strategist   Kent's tools: plan_queries, search_reddit, score_recall, fetch_threads
│                       -> /data/queries.json, /data/comments.jsonl (+ Supabase)
├── clustering-analyst  Sophia: community_summary, preview_comments, score_stance(axis), cluster_narratives
│                       -> /data/stance_axis.json, stance/claim/emotion added to comments, /data/clusters.json
├── narrative-analyst   glue: cluster_summaries_tool + Sophia: set_narratives, connectivity, set_group_labels
│                       -> labels in clusters.json, /data/connectivity.json
└── reporter            glue: report_context_tool -> /data/briefing.json (text layer for the UI)
```
- Kent's code: scrapetools/ (README there). Data via Arctic Shift (no Reddit keys), stored in Supabase. Usernames hashed.
- Sophia's code: narrative/sophia_tools.py (README in narrative/, UI contract in docs/ui_visualization_spec.md).
  Claims (neutral one-line rewrite per comment) are embedded, not raw text. UMAP + HDBSCAN. Connectivity = sqrt-JS
  narrative distance + MDS layout, shared commenters (Jaccard), community groups, narrative reply matrix + echo score.
- agent/glue.py: two small read-only tools so no agent ever read_files clusters.json (it holds every comment).
  report_context also computes content-vs-connectivity contrasts (the key Arlequin insight).
- Agent filesystem: FilesystemBackend(root_dir=config.WORKSPACE) -> "/data/x" == workspace/data/x on disk.
- run.py wipes workspace/data per event (Sophia caches embeddings/UMAP there), calls kent.start_event() first,
  and tries to save briefing.json to the Supabase events row (needs a jsonb `briefing` column).
- UI reads workspace/data/clusters.json + connectivity.json (+ briefing.json for text).
- Run from repo root: `uv run python -m agent.run "event"`. Dev MODEL default anthropic:claude-haiku-5-5.

Loops (keep visible for the deepagents judges):
- search: < 5 relevant posts or recall@10 < 0.5 -> new queries/subreddits (max 2 extra rounds)
- orchestrator: thin coverage / mostly off-axis / unusable clustering -> loop back once
- clustering-analyst: rerun with k if < 3 or > 15 clusters (max 1)
- narrative-analyst: merges duplicate clusters

Keys needed in .env (repo root): ANTHROPIC_API_KEY (agents), OPENAI_API_KEY (Kent's planner/judge,
Sophia's claims + embeddings), SUPABASE_SECRET_KEY + USERNAME_SALT (Kent), LANGSMITH_*.
Do not rename Kent's or Sophia's functions; adapt in agent/ instead.

## Rules
- Tools return SMALL summaries, never raw comment lists (Haiku pricing jumps 5x past 100K-token prompts)
- Agent paths like "/data/comments.jsonl" resolve to workspace/data/ (config.WORKSPACE; Sophia's _resolve())
- Traces go to LangSmith project "hackprinceton"
- **NEVER commit or push. Poshitha pushes code herself.**
- Never print or commit .env
- Never read_file /data/clusters.json or /data/comments.jsonl from an agent (huge); use the glue tools

## Status
- Branch `integrate` = Kent's main + Sophia's branch + agent/. Offline test passes (synthetic data in
  Kent's format through Sophia's real pipeline + glue + agent build). First real run pending keys.

## Next steps
1. First real run: `uv run python -m agent.run "jensen huang scandal"`; fix what breaks
2. Pre-run and cache 5 demo events: jensen huang scandal, anthropic IPO, FAA closure, new FIFA election, You Can See Everything
3. UI (4th teammate) reads clusters.json + connectivity.json per docs/ui_visualization_spec.md, briefing.json for text
4. Evaluation from docs/project_overview.md: expert-consensus narrative recall, coherence, stability
5. Optional: HITL approval of the stance axis (interrupt_on) as a demo moment

## Open questions
- Can we steer the embedding toward narrative rather than topic (e.g. embed an LLM-written "what story does this tell" summary instead of raw text)?
- How to define "median narrative" concretely (largest weighted cluster? centroid of all comments?)
- Best connectivity signal with Reddit data: shared users across subs, reply chains, or narrative overlap
- Ground-truth check against Pew / trusted news consensus
