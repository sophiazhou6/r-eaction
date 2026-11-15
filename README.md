# r/eaction 

**type in a news event. see the narratives Reddit is telling about it, which communities tell them, and how those communities connect.**

hackprinceton · Arlequin AI Track 2: Social Media Analysis (content + connectivity)

A LangChain deep agent searches Reddit for an event, pulls the comment threads, and turns thousands of
comments into a handful of **narratives**,
scores each comment's stance on the event's central question, and maps the **communities**: which
subreddits tell the same story, which share commenters, and which narratives argue with each other.

## How it works

```
user query
  → search-strategist     plans queries, searches Reddit, scores recall@k, fetches threads   (scrapetools/)
  → clustering-analyst    picks the event's stance question, rewrites each comment as a neutral
                          claim + stance (0-1), embeds the claims, UMAP → HDBSCAN             (narrative/)
  → narrative-analyst     labels and merges narratives, builds community + connectivity tables (narrative/)
  → UI                    reads clusters.json + connectivity.json
```

Python tools do the work; agents only make the judgment calls (which queries, what the stance axis is,
whether the clustering is usable, what each narrative means).

**What makes the analysis work**
- **Claims, not raw text.** Each comment is rewritten as the position it takes before embedding, so
  clusters group by *what people argue*, not by topic or thread. On the Jensen Huang event this raised
  cluster coherence from 1.4 to 2.5 / 5 and cut noise from 64% to 22% vs. raw-text embeddings.
- **Event-specific stance.** The agent defines what 0 and 1 mean for each event; comments that don't
  address it get `null` (off-axis), not "neutral". The axis must cover >40% of comments in >40% of clusters.
- **No amplification.** Upvotes are percentile-ranked within each thread; agree-only replies ("this")
  add capped weight to their parent instead of counting as new voices; one thread can supply at most
  20% of a community's narrative mix. Weights affect sizes only, never clustering.
- **Content × connectivity.** Communities are placed by narrative distance (√Jensen-Shannon, metric
  MDS) and linked by shared commenters (Jaccard). Pairs are labeled *independent convergence* (same
  story, different people), *same people, different framing*, *shared ecosystem*, or *separate worlds*.
- **Contested comments.** `controversy = log(1 + replies) × (1 − score percentile)` surfaces the
  comments people argue over.

## Repo layout

| Path | What | Owner |
|---|---|---|
| `scrapetools/` | Reddit search, recall@k, thread fetching, embeddings → Supabase | Kent |
| `narrative/` | stance + claims, clustering, narratives, connectivity; the two subagents | Sophia |
| `narrative/agents.py` | `clustering_analyst`, `narrative_analyst` for `create_deep_agent(subagents=[...])` | Sophia |
| `docs/ui_visualization_spec.md` | output contract + interaction spec for the UI | |
| `docs/project_overview.md` | design notes, evaluation plan (expert-consensus recall, ablations) | |

## Run it

```bash
# .env in the repo root: SUPABASE_URL, SUPABASE_SECRET_KEY, OPENAI_API_KEY, USERNAME_SALT
python scrapetools/run_manual.py "jensen huang scandal" --auto      # 1. fetch an event into Supabase
./run_pipeline.command                 # 2. cluster the latest event (creates .venv on first run)
./run_pipeline.command --cheap         #    gpt-4.1-nano for the claim pass (~$0.10 per 2k comments)
./run_pipeline.command --free          #    no LLM: stored embeddings, no stance
./compare.command                      #    claims vs raw-text clustering on the same comments
./run_pipeline.command --cleanup       #    save outputs to Supabase `narrative_runs`, delete raw comments
```

Outputs land in `workspace/data/`: `clusters.json` (every comment with x/y, narrative, stance,
weight, controversy, plus the narratives) and `connectivity.json` (community layout, narrative mix,
stance by community, shared commenters, pair insights, reply matrix, echo scores).

See `scrapetools/README.md` and `narrative/README.md` for details.
